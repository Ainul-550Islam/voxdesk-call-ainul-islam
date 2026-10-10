#!/usr/bin/env python3
"""In-stack smoke driver for VoxDesk Docker run check (`SELL CHECK 3 of 4`).

Subcommands:
  http      — HTTP probes (/health, /health/ready, /metrics, /api/v1/health/deep,
              /, /login, /dashboard, /openapi.json) + owner login -> GET /auth/me
              -> GET /api/tenants -> POST/GET /api/v1/agents
  realtime  — Go gateway (/healthz, /readyz, /metrics, signed + unsigned
              POST /ingest/v1/publish) + Rust media-engine (/v1/health, /healthz,
              /metrics)
  twilio    — Twilio POST /telephony/voice with valid X-Twilio-Signature (200 TwiML
              with <Stream>) vs invalid X-Twilio-Signature (403)
  call-ws   — WebSocket /telephony/ws?token=... handshake (connected -> start ->
              media -> stop), bad-token 1008 rejection, and PostgreSQL call_sid
              verification via GET /api/tenants/{tenant_id}/calls
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
import websockets  # noqa: E402

from scripts.twilio_signature import compute_twilio_signature  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_env_file(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        data[k.strip()] = v.strip()
    return data


def _ensure_owner_session(
    client: httpx.Client,
    env_map: dict[str, str],
) -> tuple[str, dict[str, Any]]:
    """Log in as the check owner (or sign up if not yet bootstrapped)."""
    email = env_map.get("DEMO_OWNER_EMAIL") or os.environ.get("DEMO_OWNER_EMAIL") or "owner@check.local"
    password = (
        env_map.get("DEMO_OWNER_PASSWORD")
        or os.environ.get("DEMO_OWNER_PASSWORD")
        or env_map.get("VOXDESK_OWNER_PASSWORD")
        or os.environ.get("VOXDESK_OWNER_PASSWORD")
        or "VoxCheck!DefaultPass9A"
    )
    candidates = [email]
    if email.endswith(".local"):
        candidates.append(email[:-6] + ".example.com")
    resp = None
    for candidate_email in candidates:
        resp = client.post("/auth/login", json={"email": candidate_email, "password": password})
        if resp.status_code == 200:
            body = resp.json()
            return body["access_token"], body
    if email.endswith(".local"):
        email = email[:-6] + ".example.com"

    signup_resp = client.post(
        "/auth/signup",
        json={
            "full_name": "Check Owner",
            "work_email": email,
            "company_name": "Check Tenant",
            "password": password,
            "plan_tier": "growth",
        },
    )
    if signup_resp.status_code in (200, 201):
        body = signup_resp.json()
        return body["access_token"], body

    raise RuntimeError(
        f"Owner authentication failed: login={resp.status_code} ({resp.text[:200]}), "
        f"signup={signup_resp.status_code} ({signup_resp.text[:200]})"
    )


def run_http_smoke(base_url: str, env_file: Path) -> dict[str, Any]:
    base = base_url.rstrip("/")
    env_map = load_env_file(env_file)
    checks: list[dict[str, Any]] = []

    def record(name: str, ok: bool, detail: str) -> None:
        status = "PASS" if ok else "FAIL"
        checks.append({"check": name, "status": status, "detail": detail})
        print(f"  [{status}] {name} — {detail}")
        if not ok:
            raise AssertionError(f"{name} failed: {detail}")

    with httpx.Client(base_url=base, timeout=15.0) as client:
        r_health = client.get("/health")
        record(
            "GET /health",
            r_health.status_code == 200 and r_health.json().get("status") == "ok",
            f"status={r_health.status_code} body={r_health.text.strip()}",
        )

        r_ready = client.get("/health/ready")
        record(
            "GET /health/ready",
            r_ready.status_code == 200 and r_ready.json().get("status") == "ready",
            f"status={r_ready.status_code} body={r_ready.text.strip()[:160]}",
        )

        r_metrics = client.get("/metrics")
        record(
            "GET /metrics",
            r_metrics.status_code == 200 and "voxdesk_" in r_metrics.text,
            f"status={r_metrics.status_code} bytes={len(r_metrics.text)}",
        )

        for spa_path in ("/", "/login", "/dashboard"):
            r_spa = client.get(spa_path)
            record(
                f"GET {spa_path}",
                r_spa.status_code == 200 and "<html" in r_spa.text.lower(),
                f"status={r_spa.status_code} bytes={len(r_spa.text)}",
            )

        r_openapi = client.get("/openapi.json")
        openapi_paths = len(r_openapi.json().get("paths", {})) if r_openapi.status_code == 200 else 0
        record(
            "GET /openapi.json",
            r_openapi.status_code == 200 and openapi_paths >= 900,
            f"status={r_openapi.status_code} paths={openapi_paths}",
        )

        token, login_body = _ensure_owner_session(client, env_map)
        headers = {"Authorization": f"Bearer {token}"}
        record(
            "POST /auth/login",
            bool(token),
            f"authenticated user={login_body.get('user', {}).get('email')}",
        )

        r_deep = client.get("/api/v1/health/deep", headers=headers)
        if r_deep.status_code == 404:
            r_deep = client.get("/health/dependencies", headers=headers)
            deep_label = "GET /health/dependencies"
        else:
            deep_label = "GET /api/v1/health/deep"
        deep_json = r_deep.json() if r_deep.status_code in (200, 503) else {}
        deep_checks = deep_json.get("checks", {})
        db_ok = deep_checks.get("database", {}).get("state") == "healthy" or deep_checks.get("database", {}).get("ok") is True
        cache_ok = deep_checks.get("cache", {}).get("state") == "healthy" or deep_checks.get("redis", {}).get("ok") is True
        record(
            deep_label,
            r_deep.status_code in (200, 503) and db_ok and cache_ok,
            f"status={r_deep.status_code} db_healthy={db_ok} cache_healthy={cache_ok} overall={deep_json.get('status')}",
        )

        r_me = client.get("/auth/me", headers=headers)
        me_data = r_me.json() if r_me.status_code == 200 else {}
        tenant_id = str(me_data.get("tenant", {}).get("id") or me_data.get("user", {}).get("tenant_id") or "")
        record(
            "GET /auth/me",
            r_me.status_code == 200 and bool(tenant_id),
            f"status={r_me.status_code} tenant_id={tenant_id} permissions={len(me_data.get('permissions', []))}",
        )

        r_tenants = client.get("/api/tenants", headers=headers)
        tenants_list = r_tenants.json() if r_tenants.status_code == 200 else []
        record(
            "GET /api/tenants",
            r_tenants.status_code == 200 and isinstance(tenants_list, list) and len(tenants_list) >= 1,
            f"status={r_tenants.status_code} count={len(tenants_list)}",
        )

        agent_name = f"Docker Smoke Agent {uuid.uuid4().hex[:6]}"
        r_create_agent = client.post(
            "/api/v1/agents",
            headers=headers,
            json={
                "name": agent_name,
                "description": "Created by scripts/stack_smoke.py http",
                "agent_type": "voice",
            },
        )
        created_agent = r_create_agent.json() if r_create_agent.status_code in (200, 201) else {}
        created_agent_id = created_agent.get("agent_id") or created_agent.get("id")
        record(
            "POST /api/v1/agents",
            r_create_agent.status_code in (200, 201) and bool(created_agent_id),
            f"status={r_create_agent.status_code} agent_id={created_agent_id}",
        )

        r_list_agents = client.get("/api/v1/agents", headers=headers)
        list_body = r_list_agents.json() if r_list_agents.status_code == 200 else {}
        items = list_body.get("items", list_body) if isinstance(list_body, dict) else list_body
        found = any(
            str(item.get("agent_id") or item.get("id")) == str(created_agent_id)
            for item in (items if isinstance(items, list) else [])
        )
        record(
            "GET /api/v1/agents",
            r_list_agents.status_code == 200 and found,
            f"status={r_list_agents.status_code} found_created={found}",
        )

    return {"mode": "http", "status": "PASS", "checks": checks}


def run_realtime_smoke(gateway_url: str, media_url: str, env_file: Path) -> dict[str, Any]:
    gw = gateway_url.rstrip("/")
    me = media_url.rstrip("/")
    env_map = load_env_file(env_file)
    ingest_secret = (
        env_map.get("REALTIME_GATEWAY_INGEST_SECRET")
        or os.environ.get("REALTIME_GATEWAY_INGEST_SECRET")
        or ""
    )
    metrics_token = env_map.get("METRICS_TOKEN") or os.environ.get("METRICS_TOKEN") or ""
    checks: list[dict[str, Any]] = []

    def record(name: str, ok: bool, detail: str) -> None:
        status = "PASS" if ok else "FAIL"
        checks.append({"check": name, "status": status, "detail": detail})
        print(f"  [{status}] {name} — {detail}")
        if not ok:
            raise AssertionError(f"{name} failed: {detail}")

    with httpx.Client(timeout=10.0) as client:
        r_gw_health = client.get(f"{gw}/healthz")
        record(
            "gateway GET /healthz",
            r_gw_health.status_code == 200,
            f"status={r_gw_health.status_code} body={r_gw_health.text.strip()}",
        )

        r_gw_ready = client.get(f"{gw}/readyz")
        ready_json = r_gw_ready.json() if r_gw_ready.status_code == 200 else {}
        record(
            "gateway GET /readyz",
            r_gw_ready.status_code == 200 and (ready_json.get("status") == "ok" or ready_json.get("ready") is True) and ready_json.get("checks", {}).get("engine", {}).get("ok") is True,
            f"status={r_gw_ready.status_code} body={r_gw_ready.text.strip()}",
        )

        gw_metrics_headers = {"Authorization": f"Bearer {metrics_token}"} if metrics_token else {}
        r_gw_metrics = client.get(f"{gw}/metrics", headers=gw_metrics_headers)
        record(
            "gateway GET /metrics",
            r_gw_metrics.status_code == 200 and "voxdesk_gateway_" in r_gw_metrics.text,
            f"status={r_gw_metrics.status_code} bytes={len(r_gw_metrics.text)}",
        )

        tenant_id = str(uuid.uuid4())
        event_id = str(uuid.uuid4())
        publish_payload = {
            "tenant_id": tenant_id,
            "room": "calls",
            "kind": "call.updated",
            "event_id": event_id,
            "payload": {"call_id": str(uuid.uuid4()), "status": "in_progress"},
        }
        r_pub_unauth = client.post(
            f"{gw}/ingest/v1/publish",
            json=publish_payload,
            headers={"Authorization": "Bearer wrong-secret-token"},
        )
        record(
            "gateway POST /ingest/v1/publish (bad secret -> 401)",
            r_pub_unauth.status_code == 401,
            f"status={r_pub_unauth.status_code}",
        )

        r_pub_ok = client.post(
            f"{gw}/ingest/v1/publish",
            json=publish_payload,
            headers={"Authorization": f"Bearer {ingest_secret}"},
        )
        pub_json = r_pub_ok.json() if r_pub_ok.status_code == 200 else {}
        record(
            "gateway POST /ingest/v1/publish (valid secret -> 200)",
            r_pub_ok.status_code == 200 and pub_json.get("duplicate") is False,
            f"status={r_pub_ok.status_code} body={r_pub_ok.text.strip()}",
        )

        r_pub_dup = client.post(
            f"{gw}/ingest/v1/publish",
            json=publish_payload,
            headers={"Authorization": f"Bearer {ingest_secret}"},
        )
        dup_json = r_pub_dup.json() if r_pub_dup.status_code == 200 else {}
        record(
            "gateway POST /ingest/v1/publish (replay dedupe -> duplicate=true)",
            r_pub_dup.status_code == 200 and dup_json.get("duplicate") is True,
            f"status={r_pub_dup.status_code} body={r_pub_dup.text.strip()}",
        )

        r_me_health = client.get(f"{me}/v1/health")
        me_json = r_me_health.json() if r_me_health.status_code == 200 else {}
        record(
            "media-engine GET /v1/health",
            r_me_health.status_code == 200 and me_json.get("ready") is True,
            f"status={r_me_health.status_code} body={r_me_health.text.strip()}",
        )

        r_me_metrics = client.get(f"{me}/metrics")
        record(
            "media-engine GET /metrics",
            r_me_metrics.status_code == 200 and "voxdesk_media_udp_frames_total" in r_me_metrics.text,
            f"status={r_me_metrics.status_code} bytes={len(r_me_metrics.text)}",
        )

    return {"mode": "realtime", "status": "PASS", "checks": checks}


def _resolve_callable_number(client: httpx.Client, env_map: dict[str, str]) -> tuple[str, str, str]:
    """Return (access_token, tenant_id, twilio_number) for the check owner."""
    token, _ = _ensure_owner_session(client, env_map)
    headers = {"Authorization": f"Bearer {token}"}
    r_tenants = client.get("/api/tenants", headers=headers)
    r_tenants.raise_for_status()
    tenants = r_tenants.json()
    if not tenants:
        raise RuntimeError("No tenant returned by GET /api/tenants")
    tenant = tenants[0]
    return token, str(tenant["id"]), str(tenant["twilio_number"])


def run_twilio_smoke(base_url: str, env_file: Path) -> dict[str, Any]:
    base = base_url.rstrip("/")
    env_map = load_env_file(env_file)
    twilio_token = env_map.get("TWILIO_AUTH_TOKEN") or os.environ.get("TWILIO_AUTH_TOKEN") or ""
    checks: list[dict[str, Any]] = []

    def record(name: str, ok: bool, detail: str) -> None:
        status = "PASS" if ok else "FAIL"
        checks.append({"check": name, "status": status, "detail": detail})
        print(f"  [{status}] {name} — {detail}")
        if not ok:
            raise AssertionError(f"{name} failed: {detail}")

    with httpx.Client(base_url=base, timeout=15.0) as client:
        _, _, to_number = _resolve_callable_number(client, env_map)
        voice_url = f"{base}/telephony/voice"
        call_sid = f"CA{uuid.uuid4().hex[:32]}"
        form_data = {
            "CallSid": call_sid,
            "From": "+15550009999",
            "To": to_number,
        }

        r_bad = client.post(
            "/telephony/voice",
            data=form_data,
            headers={"X-Twilio-Signature": "invalid-signature-value"},
        )
        record(
            "POST /telephony/voice (bad signature -> 403)",
            r_bad.status_code == 403,
            f"status={r_bad.status_code} body={r_bad.text.strip()[:80]}",
        )

        valid_sig = compute_twilio_signature(voice_url, form_data, twilio_token)
        r_good = client.post(
            "/telephony/voice",
            data=form_data,
            headers={"X-Twilio-Signature": valid_sig},
        )
        has_stream = "<Stream" in r_good.text and "/telephony/ws?token=" in r_good.text
        record(
            "POST /telephony/voice (valid signature -> 200 TwiML <Stream>)",
            r_good.status_code == 200 and has_stream,
            f"status={r_good.status_code} call_sid={call_sid} has_stream={has_stream}",
        )

    return {"mode": "twilio", "status": "PASS", "call_sid": call_sid, "checks": checks}


async def _run_call_ws_async(ws_base_url: str, env_file: Path) -> dict[str, Any]:
    env_map = load_env_file(env_file)
    twilio_token = env_map.get("TWILIO_AUTH_TOKEN") or os.environ.get("TWILIO_AUTH_TOKEN") or ""
    parsed_ws = urlparse(ws_base_url.rstrip("/"))
    http_scheme = "https" if parsed_ws.scheme == "wss" else "http"
    http_base = urlunparse((http_scheme, parsed_ws.netloc, "", "", "", "")).rstrip("/")
    checks: list[dict[str, Any]] = []

    def record(name: str, ok: bool, detail: str) -> None:
        status = "PASS" if ok else "FAIL"
        checks.append({"check": name, "status": status, "detail": detail})
        print(f"  [{status}] {name} — {detail}")
        if not ok:
            raise AssertionError(f"{name} failed: {detail}")

    with httpx.Client(base_url=http_base, timeout=15.0) as client:
        owner_jwt, tenant_id, to_number = _resolve_callable_number(client, env_map)
        call_sid = f"CA{uuid.uuid4().hex[:32]}"
        stream_sid = f"MZ{uuid.uuid4().hex[:32]}"
        voice_url = f"{http_base}/telephony/voice"
        form_data = {
            "CallSid": call_sid,
            "From": "+15550008888",
            "To": to_number,
        }
        sig = compute_twilio_signature(voice_url, form_data, twilio_token)
        r_voice = client.post(
            "/telephony/voice",
            data=form_data,
            headers={"X-Twilio-Signature": sig},
        )
        m = re.search(r'<Stream\s+url="([^"]+)"', r_voice.text)
        record(
            "POST /telephony/voice (mint signed stream URL)",
            r_voice.status_code == 200 and m is not None,
            f"status={r_voice.status_code} call_sid={call_sid}",
        )
        assert m is not None
        twiml_stream_url = m.group(1).replace("&amp;", "&")
        parsed_stream = urlparse(twiml_stream_url)
        ws_target = urlunparse(
            (parsed_ws.scheme, parsed_ws.netloc, parsed_stream.path, "", parsed_stream.query, "")
        )
        bad_ws_target = urlunparse(
            (parsed_ws.scheme, parsed_ws.netloc, parsed_stream.path, "", "token=forged.token", "")
        )

    # 1. Verify forged stream token is rejected with policy violation 1008.
    bad_code: int | None = None
    try:
        async with websockets.connect(bad_ws_target, open_timeout=10, close_timeout=5) as ws_bad:
            await ws_bad.send(json.dumps({"event": "connected", "protocol": "Call", "version": "1.0.0"}))
            await ws_bad.send(
                json.dumps(
                    {
                        "event": "start",
                        "sequenceNumber": "1",
                        "start": {"streamSid": stream_sid, "callSid": call_sid},
                    }
                )
            )
            try:
                await asyncio.wait_for(ws_bad.recv(), timeout=5.0)
            except websockets.ConnectionClosed as exc:
                bad_code = getattr(getattr(exc, "rcvd", None), "code", None)
            else:
                bad_code = ws_bad.close_code
    except websockets.ConnectionClosed as exc:
        bad_code = getattr(getattr(exc, "rcvd", None), "code", None)
    record(
        "WS /telephony/ws (bad stream token -> close 1008)",
        bad_code == 1008,
        f"close_code={bad_code}",
    )

    # 2. Valid signed stream token: connected -> start -> media -> stop.
    frames_sent = 0
    try:
        async with websockets.connect(ws_target, open_timeout=10, close_timeout=5) as ws:
            for event_payload in (
                {"event": "connected", "protocol": "Call", "version": "1.0.0"},
                {
                    "event": "start",
                    "sequenceNumber": "1",
                    "start": {"streamSid": stream_sid, "callSid": call_sid},
                    "streamSid": stream_sid,
                },
                {
                    "event": "media",
                    "sequenceNumber": "2",
                    "media": {
                        "track": "inbound",
                        "chunk": "1",
                        "timestamp": "20",
                        "payload": "//7+/v7+/v7+/v7+/v7+/v7+",
                    },
                    "streamSid": stream_sid,
                },
                {
                    "event": "stop",
                    "sequenceNumber": "3",
                    "stop": {"callSid": call_sid, "streamSid": stream_sid},
                    "streamSid": stream_sid,
                },
            ):
                try:
                    await ws.send(json.dumps(event_payload))
                    frames_sent += 1
                    await asyncio.sleep(0.05)
                except websockets.ConnectionClosed:
                    break
    except websockets.ConnectionClosed:
        pass
    record(
        "WS /telephony/ws (connected -> start -> media -> stop)",
        frames_sent >= 2,
        f"frames_sent={frames_sent} call_sid={call_sid}",
    )

    # 3. Verify the Call row via authenticated REST API.
    with httpx.Client(base_url=http_base, timeout=15.0) as client:
        headers = {"Authorization": f"Bearer {owner_jwt}"}
        r_calls = client.get(f"/api/tenants/{tenant_id}/calls", headers=headers)
        calls_body = r_calls.json() if r_calls.status_code == 200 else {}
        calls_list = calls_body.get("calls", []) if isinstance(calls_body, dict) else calls_body
        matched_detail: dict[str, Any] | None = None
        for item in calls_list:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            r_det = client.get(f"/api/calls/{item['id']}", headers=headers)
            if r_det.status_code == 200 and r_det.json().get("call_sid") == call_sid:
                matched_detail = r_det.json()
                break
        record(
            "GET /api/tenants/{tenant_id}/calls + /api/calls/{id} (verify call_sid persisted)",
            r_calls.status_code == 200 and matched_detail is not None,
            f"status={r_calls.status_code} call_sid={call_sid} call_status={matched_detail.get('status') if matched_detail else None}",
        )

    return {"mode": "call-ws", "status": "PASS", "call_sid": call_sid, "checks": checks}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="VoxDesk in-stack Docker smoke runner.")
    sub = parser.add_subparsers(dest="subcommand", required=True)

    p_http = sub.add_parser("http", help="Run HTTP + authenticated API smoke checks.")
    p_http.add_argument("--base", default="http://127.0.0.1:8000")
    p_http.add_argument("--env-file", default=".env.check")

    p_rt = sub.add_parser("realtime", help="Run Go realtime-gateway + Rust media-engine smoke checks.")
    p_rt.add_argument("--gateway", default="http://127.0.0.1:8790")
    p_rt.add_argument("--media", default="http://127.0.0.1:9001")
    p_rt.add_argument("--env-file", default=".env.check")

    p_tw = sub.add_parser("twilio", help="Run Twilio webhook signature verification smoke checks.")
    p_tw.add_argument("--base", default="http://127.0.0.1:8000")
    p_tw.add_argument("--env-file", default=".env.check")

    p_ws = sub.add_parser("call-ws", help="Run Twilio Media Stream WebSocket smoke checks.")
    p_ws.add_argument("--base", default="ws://127.0.0.1:8000")
    p_ws.add_argument("--env-file", default=".env.check")

    args = parser.parse_args(argv)
    env_path = Path(args.env_file)
    if not env_path.is_absolute():
        env_path = REPO_ROOT / env_path

    if args.subcommand == "http":
        run_http_smoke(args.base, env_path)
    elif args.subcommand == "realtime":
        run_realtime_smoke(args.gateway, args.media, env_path)
    elif args.subcommand == "twilio":
        run_twilio_smoke(args.base, env_path)
    elif args.subcommand == "call-ws":
        asyncio.run(_run_call_ws_async(args.base, env_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
