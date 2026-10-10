#!/usr/bin/env python3
"""Boot-probe ``app.main:app`` via a real ``uvicorn`` subprocess and verify health/OpenAPI.

Spawns ``uvicorn app.main:app`` on an ephemeral localhost port, polls ``/health``
until HTTP 200 (within ``--timeout`` seconds), probes ``/health/ready``,
``/health/dependencies``, and ``/openapi.json``, records child process RSS via
``psutil.Process(pid).memory_info().rss``, sends ``SIGTERM``, and verifies clean
shutdown. Emits a JSON summary to ``stdout`` and ``reports/check/boot_probe.json``.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx
import psutil

ROOT = Path(__file__).resolve().parents[1]


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def run_probe(timeout_s: float) -> dict[str, Any]:
    t0 = time.perf_counter()
    port = _pick_free_port()
    base_url = f"http://127.0.0.1:{port}"

    env = os.environ.copy()
    tmp_db_file: str | None = None
    if not env.get("DATABASE_URL"):
        fd, tmp_db_file = tempfile.mkstemp(prefix="voxdesk_boot_", suffix=".db")
        os.close(fd)
        env["DATABASE_URL"] = f"sqlite+aiosqlite:///{tmp_db_file}"
    env.setdefault("JWT_SECRET", "boot-probe-ephemeral-secret-key-0123456789abcdef")

    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--log-level",
        "warning",
    ]
    proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        deadline = time.perf_counter() + timeout_s
        health_resp: httpx.Response | None = None
        with httpx.Client(base_url=base_url, timeout=5.0) as client:
            while time.perf_counter() < deadline:
                if proc.poll() is not None:
                    out, err = proc.communicate(timeout=5)
                    raise RuntimeError(
                        f"uvicorn exited early with code {proc.returncode}: {err[-500:] or out[-500:]}"
                    )
                try:
                    resp = client.get("/health")
                    if resp.status_code == 200:
                        health_resp = resp
                        break
                except httpx.HTTPError:
                    time.sleep(0.15)

            if health_resp is None:
                raise TimeoutError(f"uvicorn did not respond 200 on /health within {timeout_s}s")

            boot_ready_s = round(time.perf_counter() - t0, 3)

            ready_resp = client.get("/health/ready")
            deps_resp = client.get("/health/dependencies")

            t_oa = time.perf_counter()
            openapi_resp = client.get("/openapi.json")
            openapi_elapsed_s = round(time.perf_counter() - t_oa, 3)

        ps_proc = psutil.Process(proc.pid)
        rss_bytes = ps_proc.memory_info().rss
        for child in ps_proc.children(recursive=True):
            try:
                rss_bytes += child.memory_info().rss
            except psutil.Error:
                pass
        rss_mb = round(rss_bytes / (1024 * 1024), 2)

        openapi_body = openapi_resp.json() if openapi_resp.status_code == 200 else {}
        paths = openapi_body.get("paths", {})
        operation_count = sum(
            len([m for m in item.keys() if m.lower() in {"get", "post", "put", "patch", "delete", "options", "head"}])
            for item in paths.values()
            if isinstance(item, dict)
        )

        # Clean shutdown via SIGTERM
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=10.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5.0)

        total_elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        result = {
            "status": "ok" if (health_resp.status_code == 200 and openapi_resp.status_code == 200) else "failed",
            "uvicorn_pid": proc.pid,
            "uvicorn_exit_code": proc.returncode,
            "port": port,
            "boot_ready_seconds": boot_ready_s,
            "boot_duration_ms": total_elapsed_ms,
            "rss_bytes": rss_bytes,
            "rss_mb": rss_mb,
            "health": {
                "status_code": health_resp.status_code,
                "body": health_resp.json(),
            },
            "health_ready": {
                "status_code": ready_resp.status_code,
                "body": ready_resp.json() if ready_resp.headers.get("content-type", "").startswith("application/json") else ready_resp.text,
            },
            "health_dependencies": {
                "status_code": deps_resp.status_code,
                "body": deps_resp.json() if deps_resp.headers.get("content-type", "").startswith("application/json") else deps_resp.text,
            },
            "openapi": {
                "status_code": openapi_resp.status_code,
                "bytes": len(openapi_resp.content),
                "seconds": openapi_elapsed_s,
                "openapi_version": openapi_body.get("openapi"),
                "title": openapi_body.get("info", {}).get("title"),
                "version": openapi_body.get("info", {}).get("version"),
                "path_count": len(paths),
                "operation_count": operation_count,
            },
        }
        return result
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5.0)
        if tmp_db_file and os.path.exists(tmp_db_file):
            try:
                os.remove(tmp_db_file)
            except OSError:
                pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe FastAPI boot, health, and OpenAPI via uvicorn.")
    parser.add_argument("--timeout", type=float, default=30.0, help="Timeout in seconds (default: 30).")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "reports" / "check" / "boot_probe.json",
        help="Optional output JSON path.",
    )
    args = parser.parse_args()

    result = run_probe(args.timeout)
    payload = json.dumps(result, indent=2, sort_keys=False)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
