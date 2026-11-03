#!/usr/bin/env python3
"""Local Splunk HEC + Elastic _bulk + Datadog logs intake for live sink tests."""
from __future__ import annotations

import gzip
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ACKS: dict[int, bool] = {}
NEXT_ACK = 1


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        return

    def _read(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if self.headers.get("Content-Encoding") == "gzip":
            return gzip.decompress(raw)
        return raw

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in {"/services/collector/health", "/health"}:
            self._json(200, {"status": "ok"})
            return
        self._json(404, {"error": path})

    def do_POST(self) -> None:  # noqa: N802
        global NEXT_ACK
        path = self.path.split("?", 1)[0]
        body = self._read()

        if path.endswith("/services/collector/event") or path == "/services/collector/event":
            ack_id = NEXT_ACK
            NEXT_ACK += 1
            ACKS[ack_id] = True
            self._json(200, {"code": 0, "ackId": ack_id, "text": "Success"})
            return

        if path.endswith("/services/collector/ack") or path == "/services/collector/ack":
            try:
                asked = json.loads(body.decode() or "{}").get("acks") or []
            except ValueError:
                asked = []
            self._json(200, {"acks": {str(i): bool(ACKS.get(int(i), False)) for i in asked}})
            return

        if path.endswith("/_bulk") or path == "/_bulk":
            lines = [ln for ln in body.decode().splitlines() if ln.strip()]
            items = []
            for i in range(0, len(lines), 2):
                try:
                    action = json.loads(lines[i])
                    meta = next(iter(action.values()))
                    doc_id = meta.get("_id", str(i))
                except Exception:
                    doc_id = str(i)
                items.append({"index": {"_id": doc_id, "status": 201, "result": "created"}})
            self._json(200, {"took": 1, "errors": False, "items": items})
            return

        if path.endswith("/api/v2/logs") or path == "/api/v2/logs":
            if not self.headers.get("DD-API-KEY"):
                self._json(403, {"error": "missing key"})
                return
            self._json(202, {"status": "ok"})
            return

        self._json(404, {"error": path})


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", 18088), Handler)
    print("siem receivers on :18088", flush=True)
    server.serve_forever()
