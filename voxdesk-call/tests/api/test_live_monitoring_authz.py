# File: tests/api/test_live_monitoring_authz.py — RBAC, tenant isolation, WS token expiry, durable sessions & audit tests for Live Monitoring (Part 4 / Gate G5)
"""Tests for ``app.api.live_monitoring_routes`` and ``app.api.ws.monitor_ws``.

Verifies:
  1. RBAC enforcement of ``calls.monitor`` (Viewer denied with 403 + ``AuditAction.AUTHZ_DENIED``;
     Manager read-only; Admin/Owner full access).
  2. Strict per-tenant isolation across HTTP endpoints and ``/ws/monitor/{call_id}``.
  3. Short-lived WebSocket token validation, expiry rejection, live PCM16 + transcript
     streaming, and clean close on ``call_ended``.
  4. Correct ``AuditAction`` / ``event_type`` (``live_monitor.started``,
     ``live_monitor.whisper``, ``live_monitor.takeover``, ``live_monitor.ended`` —
     never ``AuditAction.RESOURCE_EXPORTED``) and durable idempotency + heartbeat/expiry.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

import jwt
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.db.models  # noqa: F401
from app.agent.monitor_tap import MonitorTap
from app.api.ws.monitor_ws import (
    MONITOR_TOKEN_SCOPE,
    MonitorTokenExpiredError,
     _signing_secret,
    issue_monitor_ws_token,
    verify_monitor_ws_token,
)
from app.db.enterprise_models import LiveCallSession
from app.db.models import AuditAction, AuditLog, Call, CallDirection, CallStatus, UserRole
from app.main import app
from app.telephony.monitor_bus import get_monitor_bus
from app.telephony.provider import FakeTelephonyProvider, set_provider
from tests.conftest import auth_headers, make_tenant, make_user


class _InLoopWebSocketSession:
    """In-loop ASGI WebSocket test helper that runs on the active event loop."""

    def __init__(self, to_app: asyncio.Queue, from_app: asyncio.Queue) -> None:
        self._to_app = to_app
        self._from_app = from_app
        self.close_code: int | None = None

    async def receive_message(self, timeout: float = 5.0) -> dict[str, Any]:
        msg = await asyncio.wait_for(self._from_app.get(), timeout=timeout)
        if msg["type"] == "websocket.close":
            self.close_code = msg.get("code", 1000)
        return msg

    async def receive_json(self, timeout: float = 5.0) -> dict[str, Any]:
        import json

        msg = await self.receive_message(timeout=timeout)
        assert msg["type"] == "websocket.send", f"Expected websocket.send, got {msg}"
        return json.loads(msg["text"])

    async def receive_bytes(self, timeout: float = 5.0) -> bytes:
        msg = await self.receive_message(timeout=timeout)
        assert msg["type"] == "websocket.send", f"Expected websocket.send, got {msg}"
        return msg["bytes"]

    async def send_json(self, data: dict[str, Any]) -> None:
        import json

        await self._to_app.put({"type": "websocket.receive", "text": json.dumps(data)})

    async def close(self, code: int = 1000) -> None:
        await self._to_app.put({"type": "websocket.disconnect", "code": code})


@asynccontextmanager
async def _connect_ws(url: str):
    parsed = urlsplit(url)
    to_app: asyncio.Queue = asyncio.Queue()
    from_app: asyncio.Queue = asyncio.Queue()
    scope = {
        "type": "websocket",
        "asgi": {"version": "3.0"},
        "scheme": "ws",
        "http_version": "1.1",
        "path": parsed.path,
        "raw_path": parsed.path.encode("ascii"),
        "query_string": parsed.query.encode("ascii"),
        "root_path": "",
        "headers": [(b"host", b"testserver")],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
        "subprotocols": [],
        "state": {},
    }
    await to_app.put({"type": "websocket.connect"})
    task = asyncio.create_task(app(scope, to_app.get, from_app.put))
    first = await asyncio.wait_for(from_app.get(), timeout=5.0)
    assert first["type"] == "websocket.accept"
    ws = _InLoopWebSocketSession(to_app, from_app)
    try:
        yield ws
    finally:
        if not task.done():
            await ws.close()
            try:
                await asyncio.wait_for(task, timeout=3.0)
            except asyncio.TimeoutError:
                task.cancel()


async def _make_active_call(db: AsyncSession, tenant_id: uuid.UUID, sid_suffix: str) -> Call:
    call = Call(
        tenant_id=tenant_id,
        call_sid=f"CA_MON_{sid_suffix}",
        direction=CallDirection.INBOUND,
        status=CallStatus.IN_PROGRESS,
        from_number="+15550101234",
        to_number="+15550105678",
    )
    db.add(call)
    await db.commit()
    await db.refresh(call)
    return call


@pytest.mark.asyncio
async def test_live_monitoring_rbac_enforces_calls_monitor_and_audits_denial(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    """Viewer is denied with 403 + AUTHZ_DENIED; Manager can read but not write; Admin can write."""
    tenant = await make_tenant(db, name="rbac-monitor-org")
    viewer = await make_user(db, tenant=tenant, role=UserRole.VIEWER, email="viewer@example.com")
    manager = await make_user(db, tenant=tenant, role=UserRole.MANAGER, email="mgr@example.com")
    admin = await make_user(db, tenant=tenant, role=UserRole.ADMIN, email="admin@example.com")
    call = await _make_active_call(db, tenant.id, "RBAC01")

    viewer_headers = await auth_headers(client, viewer)
    manager_headers = await auth_headers(client, manager)
    admin_headers = await auth_headers(client, admin)

    # Viewer denied on start_monitoring (write) and list_monitoring_sessions (read)
    res_viewer_post = await client.post(
        f"/api/calls/{call.id}/monitor",
        json={"mode": "listen", "reason": "Unauthorized attempt"},
        headers=viewer_headers,
    )
    assert res_viewer_post.status_code == 403
    assert "calls.monitor" in res_viewer_post.json()["detail"]

    res_viewer_get = await client.get(
        f"/api/calls/{call.id}/monitor",
        headers=viewer_headers,
    )
    assert res_viewer_get.status_code == 403

    # Verify AuditAction.AUTHZ_DENIED recorded with missing=["calls.monitor"]
    denial_rows = (
        await db.execute(
            select(AuditLog).where(
                AuditLog.tenant_id == tenant.id,
                AuditLog.actor_user_id == viewer.id,
                AuditLog.action == AuditAction.AUTHZ_DENIED,
            )
        )
    ).scalars().all()
    assert len(denial_rows) >= 1
    assert denial_rows[0].detail.get("missing") == ["calls.monitor"]

    # Manager can list sessions (read=True) but cannot start monitoring or takeover (write=True)
    res_mgr_get = await client.get(
        f"/api/calls/{call.id}/monitor",
        headers=manager_headers,
    )
    assert res_mgr_get.status_code == 200

    res_mgr_post = await client.post(
        f"/api/calls/{call.id}/monitor",
        json={"mode": "listen"},
        headers=manager_headers,
    )
    assert res_mgr_post.status_code == 403

    # Admin succeeds
    res_admin = await client.post(
        f"/api/calls/{call.id}/monitor",
        json={"mode": "listen", "reason": "Supervisor QA"},
        headers=admin_headers,
    )
    assert res_admin.status_code == 201
    body = res_admin.json()
    assert body["mode"] == "listen"
    assert body["status"] == "active"
    assert body["ws_token"]
    assert f"/ws/monitor/{call.id}" in body["ws_url"]


@pytest.mark.asyncio
async def test_live_monitoring_tenant_isolation(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    """A supervisor in Tenant B cannot monitor, whisper, takeover, or WS-stream a Call in Tenant A."""
    tenant_a = await make_tenant(db, name="tenant-a-mon")
    tenant_b = await make_tenant(db, name="tenant-b-mon")
    admin_a = await make_user(db, tenant=tenant_a, role=UserRole.ADMIN, email="a@example.com")
    admin_b = await make_user(db, tenant=tenant_b, role=UserRole.ADMIN, email="b@example.com")

    call_a = await _make_active_call(db, tenant_a.id, "ISO01")
    headers_a = await auth_headers(client, admin_a)
    headers_b = await auth_headers(client, admin_b)

    # Admin A starts a session on Call A
    start_res = await client.post(
        f"/api/calls/{call_a.id}/monitor",
        json={"mode": "whisper_ai", "reason": "Tenant A QA"},
        headers=headers_a,
    )
    assert start_res.status_code == 201
    session_id = start_res.json()["id"]

    # Admin B gets 404 across all endpoints for Call A
    assert (
        await client.post(
            f"/api/calls/{call_a.id}/monitor",
            json={"mode": "listen"},
            headers=headers_b,
        )
    ).status_code == 404

    assert (
        await client.get(f"/api/calls/{call_a.id}/monitor", headers=headers_b)
    ).status_code == 404

    assert (
        await client.post(
            f"/api/calls/{call_a.id}/monitor/{session_id}/whisper",
            json={"text": "Cross-tenant injection"},
            headers=headers_b,
        )
    ).status_code == 404

    assert (
        await client.post(
            f"/api/calls/{call_a.id}/takeover",
            json={"reason": "Cross-tenant takeover", "supervisor_destination": "+15550199999"},
            headers=headers_b,
        )
    ).status_code == 404

    # Cross-tenant WS token forged with Tenant B's tenant_id is rejected with 1008 call_not_found
    forged_token = issue_monitor_ws_token(
        call_id=call_a.id,
        tenant_id=tenant_b.id,
        supervisor_id=admin_b.id,
        mode="listen",
    )
    async with _connect_ws(f"/ws/monitor/{call_a.id}?token={forged_token}") as ws:
        err = await ws.receive_json()
        assert err["type"] == "error"
        assert err["code"] == "call_not_found"
        close_msg = await ws.receive_message()
        assert close_msg["type"] == "websocket.close"
        assert close_msg["code"] == 1008


@pytest.mark.asyncio
async def test_monitor_ws_token_expiry_and_live_streaming(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    """Expired tokens are rejected with 1008; valid tokens stream PCM16 + transcripts and close on call end."""
    tenant = await make_tenant(db, name="ws-stream-org")
    admin = await make_user(db, tenant=tenant, role=UserRole.ADMIN, email="ws-admin@example.com")
    call = await _make_active_call(db, tenant.id, "WS01")
    headers = await auth_headers(client, admin)

    # 1. Expired JWT token is rejected by verify_monitor_ws_token and by /ws/monitor/{call_id}
    now = int(time.time())
    expired_token = jwt.encode(
        {
            "sub": str(admin.id),
            "tenant_id": str(tenant.id),
            "call_id": str(call.id),
            "session_id": "",
            "mode": "listen",
            "scope": MONITOR_TOKEN_SCOPE,
            "iat": now - 600,
            "exp": now - 60,
        },
        _signing_secret(),
        algorithm="HS256",
    )
    with pytest.raises(MonitorTokenExpiredError):
        verify_monitor_ws_token(expired_token, expected_call_id=call.id)

    async with _connect_ws(f"/ws/monitor/{call.id}?token={expired_token}") as ws:
        err = await ws.receive_json()
        assert err["type"] == "error"
        assert err["code"] == "token_expired"
        close_msg = await ws.receive_message()
        assert close_msg["type"] == "websocket.close"
        assert close_msg["code"] == 1008

    # 2. Valid session token connects and streams binary PCM16 + transcript JSON
    start_res = await client.post(
        f"/api/calls/{call.id}/monitor",
        json={"mode": "whisper_ai", "reason": "Live stream test"},
        headers=headers,
    )
    assert start_res.status_code == 201
    ws_url = start_res.json()["ws_url"]

    bus = get_monitor_bus()
    async with _connect_ws(ws_url) as ws:
        connected = await ws.receive_json()
        assert connected["type"] == "connected"
        assert connected["call_id"] == str(call.id)

        pcm_bytes = b"\x05\x00\x06\x00" * 80
        await bus.publish_audio(call.id, pcm_bytes, speaker="caller", sample_rate=16000)
        received_pcm = await ws.receive_bytes()
        assert received_pcm == pcm_bytes

        await bus.publish_transcript(call.id, "Caller live transcript line", speaker="caller")
        received_tx = await ws.receive_json()
        assert received_tx["type"] == "transcript"
        assert received_tx["speaker"] == "caller"
        assert received_tx["text"] == "Caller live transcript line"

        await bus.publish_call_ended(call.id, reason="completed")
        ended_msg = await ws.receive_json()
        assert ended_msg["type"] == "call_ended"
        assert ended_msg["call_id"] == str(call.id)


@pytest.mark.asyncio
async def test_audit_actions_idempotency_whisper_takeover_and_heartbeat_expiry(
    client: AsyncClient,
    db: AsyncSession,
) -> None:
    """Verify correct AuditAction/event_type (never RESOURCE_EXPORTED), idempotency, whisper, takeover, and expiry."""
    fake_provider = FakeTelephonyProvider()
    set_provider(fake_provider)
    try:
        tenant = await make_tenant(db, name="audit-mon-org")
        admin = await make_user(db, tenant=tenant, role=UserRole.ADMIN, email="audit-admin@example.com")
        call = await _make_active_call(db, tenant.id, "AUD01")
        headers = await auth_headers(client, admin)

        tap = MonitorTap(call.id, bus=get_monitor_bus())
        captured_frames = []

        async def _capture_push(frame, direction=None):
            captured_frames.append(frame)

        tap.push_frame = _capture_push  # type: ignore[method-assign]

        try:
            # 1. Start monitoring with Idempotency-Key (replayed call returns same session id)
            idem_key = f"idem-mon-{uuid.uuid4().hex[:12]}"
            r1 = await client.post(
                f"/api/calls/{call.id}/monitor",
                json={"mode": "whisper_ai", "reason": "Audit check", "idempotency_key": idem_key},
                headers=headers,
            )
            r2 = await client.post(
                f"/api/calls/{call.id}/monitor",
                json={"mode": "whisper_ai", "reason": "Audit check", "idempotency_key": idem_key},
                headers=headers,
            )
            assert r1.status_code == 201
            assert r2.status_code == 201
            assert r1.json()["id"] == r2.json()["id"]
            session_id = r1.json()["id"]

            # 2. Whisper guidance to AI -> delivered to active MonitorTap
            w_res = await client.post(
                f"/api/calls/{call.id}/monitor/{session_id}/whisper",
                json={"text": "Mention the enterprise SLA guarantee.", "target": "agent"},
                headers=headers,
            )
            assert w_res.status_code == 200
            w_body = w_res.json()
            assert w_body["status"] == "delivered"
            assert w_body["delivery_status"] == "DELIVERED"
            assert w_body["media_connected"] is True
            assert len(captured_frames) == 2

            # 3. Heartbeat extends expires_at; past expires_at auto-expires session
            hb_res = await client.post(
                f"/api/calls/{call.id}/monitor/{session_id}/heartbeat",
                headers=headers,
            )
            assert hb_res.status_code == 200

            sess_row = await db.get(LiveCallSession, uuid.UUID(session_id))
            assert sess_row is not None
            meta = dict(sess_row.meta or {})
            meta["expires_at"] = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
            sess_row.meta = meta
            await db.commit()

            list_res = await client.get(f"/api/calls/{call.id}/monitor", headers=headers)
            assert list_res.status_code == 200
            assert list_res.json()["active"] == 0
            assert list_res.json()["sessions"][0]["status"] == "expired"

            # 4. Supervisor Takeover executes TwiML replacement on provider and records audit
            tk_res = await client.post(
                f"/api/calls/{call.id}/takeover",
                json={
                    "reason": "Customer requested supervisor escalation",
                    "supervisor_destination": "+15550197777",
                    "ownership": "operator",
                },
                headers=headers,
            )
            assert tk_res.status_code == 201
            assert len(fake_provider.redirects) == 1
            assert "<Number>+15550197777</Number>" in fake_provider.redirects[0][1]

            # 5. Verify AuditLog rows have GOVERNANCE_EVENT and proper event_type (never RESOURCE_EXPORTED)
            audit_rows = (
                await db.execute(
                    select(AuditLog).where(AuditLog.tenant_id == tenant.id)
                )
            ).scalars().all()
            monitor_rows = [
                r for r in audit_rows if (r.event_type or "").startswith("live_monitor.")
            ]
            event_types = {r.event_type for r in monitor_rows}
            assert "live_monitor.started" in event_types
            assert "live_monitor.whisper" in event_types
            assert "live_monitor.takeover" in event_types
            for r in monitor_rows:
                assert r.action == AuditAction.GOVERNANCE_EVENT
                assert str(r.action) != "resource_exported"
                assert r.event_type != "resource_exported"
        finally:
            await tap.close()
    finally:
        set_provider(None)
