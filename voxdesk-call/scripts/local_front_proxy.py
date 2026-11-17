#!/usr/bin/env python3
"""A stand-in for Caddy, for local verification of the shipped topology.

The production front door is Caddy: documents go to the Next standalone server,
API prefixes go to FastAPI. This script does the same two-upstream job with the
standard library so the shipped bundle can be exercised on a machine that has no
Docker — which is the case in the sandbox where PART 17A was verified.

    python3 scripts/local_front_proxy.py --web 127.0.0.1:3100 --api 127.0.0.1:8000 --port 8080

It is a verification tool, not a deployment component: no TLS, no caching, no
compression, single-threaded. The prefix list is copied from the `Caddyfile`, and
`--check` fails if the two drift apart, so this file cannot quietly route
something the real front door does not.
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CADDYFILE = ROOT / "Caddyfile"

#: Kept identical to the `@api path ...` matcher in the Caddyfile.
API_PREFIXES = (
    "/api",
    "/auth",
    "/channels",
    "/health",
    "/messaging",
    "/public",
    "/scim",
    "/telephony",
    "/webhooks",
    "/widget",
    "/ws",
    "/.well-known",
    "/metrics",
    "/openapi.json",
    "/redoc",
    "/robots.txt",
    "/sitemap.xml",
    "/security.txt",
)
#: Paths the Caddyfile serves from the frontend even though they look like API.
FRONTEND_OWNED = ("/docs",)

#: Never copied from the upstream response: this shim sets its own framing.
#: `content-length` must be in this set — copying it produced two of them and
#: strict HTTP clients rejected the message ("Duplicate Content-Length").
NOT_COPIED_FROM_UPSTREAM = {"content-length", "transfer-encoding", "connection"}

HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


def is_api(path: str) -> bool:
    if any(path == owned or path.startswith(owned + "/") for owned in FRONTEND_OWNED):
        return False
    return any(path == prefix or path.startswith(prefix + "/") for prefix in API_PREFIXES)


def check_against_caddyfile() -> list[str]:
    if not CADDYFILE.is_file():
        return [f"{CADDYFILE} is missing"]
    text = CADDYFILE.read_text(encoding="utf-8")
    match = re.search(r"@api path (.*?)\n\s*handle @api", text, re.S)
    if not match:
        return ["the Caddyfile has no `@api path` matcher"]
    raw = match.group(1).replace("\\\n", " ")
    declared = {token.split("*")[0].rstrip("/") or "/" for token in raw.split()}
    problems = []
    for prefix in sorted(declared - set(API_PREFIXES)):
        problems.append(f"Caddyfile routes {prefix} to the API; this proxy does not")
    for prefix in sorted(set(API_PREFIXES) - declared):
        problems.append(f"this proxy routes {prefix} to the API; the Caddyfile does not")
    return problems


class FrontProxy(BaseHTTPRequestHandler):
    web = "127.0.0.1:3100"
    api = "127.0.0.1:8000"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:  # quieter than the default
        sys.stderr.write("proxy " + (fmt % args) + "\n")

    def _forward(self) -> None:
        upstream = self.api if is_api(self.path.split("?")[0]) else self.web
        body = None
        length = self.headers.get("Content-Length")
        if length:
            body = self.rfile.read(int(length))
        url = f"http://{upstream}{self.path}"
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_BY_HOP}
        headers["Host"] = upstream
        request = urllib.request.Request(url, data=body, headers=headers, method=self.command)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = response.read()
                self.send_response(response.status)
                for key, value in response.headers.items():
                    if key.lower() in HOP_BY_HOP or key.lower() in NOT_COPIED_FROM_UPSTREAM:
                        continue
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
        except urllib.error.HTTPError as error:
            payload = error.read()
            self.send_response(error.code)
            for key, value in error.headers.items():
                if key.lower() in HOP_BY_HOP or key.lower() in NOT_COPIED_FROM_UPSTREAM:
                    continue
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except Exception as error:  # upstream down: say so instead of hanging
            message = f"upstream {upstream} failed: {error}".encode()
            self.send_response(502)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(message)))
            self.end_headers()
            self.wfile.write(message)

    do_GET = _forward
    do_HEAD = _forward
    do_POST = _forward
    do_PUT = _forward
    do_PATCH = _forward
    do_DELETE = _forward
    do_OPTIONS = _forward


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--web", default="127.0.0.1:3100")
    parser.add_argument("--api", default="127.0.0.1:8000")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--check", action="store_true", help="compare the prefix list with the Caddyfile")
    args = parser.parse_args(argv)

    problems = check_against_caddyfile()
    if args.check:
        for problem in problems:
            print(problem)
        print("prefix list matches the Caddyfile" if not problems else "prefix list drifted")
        return 1 if problems else 0
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1

    FrontProxy.web = args.web
    FrontProxy.api = args.api
    server = ThreadingHTTPServer(("127.0.0.1", args.port), FrontProxy)
    print(f"front door on http://127.0.0.1:{args.port}  (web {args.web}, api {args.api})")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
