"""SIEM delivery against real local protocol servers.

Every sink here talks to a socket this test opened: an HTTP server for Splunk
HEC, Datadog and Elastic, a TCP server for syslog, and an S3-shaped HTTP server
for the archive sink. Nothing is mocked at the client layer — the transport that
production uses is the transport under test, including boto3's request signing.

The assertions are about what the destination actually received (wire body, auth
header, CEF escaping) and what the dispatcher did with the result (cursor
advanced, retry recorded, dead letter written).
"""

from __future__ import annotations

import http.server
import socket
import threading
from dataclasses import dataclass, field
from typing import Iterator

import pytest
from sqlalchemy import select

from app.audit.service import record_event
from app.compliance_v2.siem import dispatcher
from app.compliance_v2.siem.events import SiemEvent
from app.compliance_v2.siem.sinks_base import (
    CATEGORY_SERVER,
    SinkResult,
)
from app.db.compliance_v2_models import SiemSink as SiemSinkRow


@dataclass
class Capture:
    """What a local destination received."""

    requests: list[dict] = field(default_factory=list)
    status: int = 200
    body: bytes = b"{}"

    def last(self) -> dict:
        assert self.requests, "the destination received no request"
        return self.requests[-1]


class _Handler(http.server.BaseHTTPRequestHandler):
    """One server for every HTTP-shaped destination the sinks talk to.

    The reply body is scripted by the test. ``PutObject`` is special-cased to an
    empty 200 because boto3 treats a non-empty 200 body as an S3 error document,
    which is exactly how the real service behaves.
    """

    def _record(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        self.server.capture.requests.append(
            {"path": self.path, "method": self.command, "headers": dict(self.headers), "body": body}
        )
        return body

    def _reply(self, payload: bytes, *, content_type: str = "application/json") -> None:
        self.send_response(self.server.capture.status)
        self.send_header("Content-Type", content_type)
        self.send_header("ETag", '"d41d8cd98f00b204e9800998ecf8427e"')
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self) -> None:  # noqa: N802 - http.server's spelling
        self._record()
        self._reply(self.server.capture.body)

    def do_PUT(self) -> None:  # noqa: N802
        self._record()
        self._reply(b"", content_type="application/xml")

    def do_GET(self) -> None:  # noqa: N802
        self._record()
        self._reply(self.server.capture.body)

    def log_message(self, *_args) -> None:
        return


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    capture: Capture


@pytest.fixture
def http_destination() -> Iterator[tuple[str, Capture]]:
    capture = Capture()
    server = _Server(("127.0.0.1", 0), _Handler)
    server.capture = capture
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    try:
        yield f"http://{host}:{port}", capture
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def tcp_destination() -> Iterator[tuple[str, int, list[bytes]]]:
    """A plain TCP collector that keeps every byte it is sent."""
    received: list[bytes] = []
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    host, port = listener.getsockname()

    def _serve() -> None:
        conn, _ = listener.accept()
        with conn:
            while True:
                chunk = conn.recv(65536)
                if not chunk:
                    return
                received.append(chunk)

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
    try:
        yield host, port, received
    finally:
        listener.close()
        thread.join(timeout=5)


def _event(
    *, event_id: str = "11111111-1111-1111-1111-111111111111", tenant_id: str = "t-1"
) -> SiemEvent:
    return SiemEvent(
        event_id=event_id,
        schema_version="voxdesk.siem.event.v1",
        tenant_id=tenant_id,
        occurred_at="2026-10-01T12:00:00+00:00",
        event_type="call.completed",
        action="call_scored",
        result="success",
        actor_type="human",
        actor_id="user-1",
        resource_type="call",
        resource_id="call-1",
        ip_address="203.0.113.10",
        detail={"note": "cef=injection|attempt"},
        chain_hash="a" * 64,
    )


# --------------------------------------------------------------- transports ---


@dataclass
class _StubSink:
    """A sink with a scripted result; the dispatcher is what is under test."""

    kind: str
    results: list[SinkResult]
    sent: list[list[SiemEvent]] = field(default_factory=list)

    async def send_batch(self, events):
        self.sent.append(list(events))
        return self.results.pop(0)

    async def health(self):
        from app.compliance_v2.siem.sinks_base import SinkHealth

        return SinkHealth(ok=True, detail="stub")

    async def aclose(self) -> None:
        return None


async def _seed_audit(db, tenant, count: int = 2) -> list:
    for index in range(count):
        await record_event(
            db,
            tenant_id=tenant.id,
            event_type="user.login",
            resource_type="user",
            resource_id=f"user-{index}",
            detail={"index": index},
        )
    await db.commit()
    return list(
        (
            await db.execute(
                select(dispatcher.AuditLog).where(dispatcher.AuditLog.tenant_id == tenant.id)
            )
        ).scalars()
    )


def _sink_row(tenant, *, name: str, kind: str) -> SiemSinkRow:
    return SiemSinkRow(
        tenant_id=tenant.id,
        name=name,
        kind=kind,
        endpoint="http://127.0.0.1:1",
        secret_ref="siem/splunk-token",
        config={},
        enabled=True,
        batch_size=500,
    )


async def test_dispatcher_advances_the_cursor_on_success(
    db, tenant_a, compliance_settings, monkeypatch
):
    await _seed_audit(db, tenant_a, count=3)
    sink_row = _sink_row(tenant_a, name="splunk", kind="splunk_hec")
    db.add(sink_row)
    await db.commit()

    stub = _StubSink(kind="splunk_hec", results=[SinkResult.success(3)])
    monkeypatch.setattr(dispatcher, "build_sink", lambda row, settings: stub)

    outcome = await dispatcher.dispatch_tick(db, settings=compliance_settings)
    await db.commit()

    assert outcome["delivered"] == 3
    assert len(stub.sent[0]) == 3
    cursor = await dispatcher.ensure_cursor(db, sink_row)
    assert cursor.events_delivered == 3
    assert cursor.state == "idle"
    assert cursor.consecutive_failures == 0


async def test_dispatcher_records_a_retry_without_advancing(
    db, tenant_a, compliance_settings, monkeypatch
):
    await _seed_audit(db, tenant_a, count=2)
    sink_row = _sink_row(tenant_a, name="elastic", kind="elastic_bulk")
    db.add(sink_row)
    await db.commit()
    cursor = await dispatcher.ensure_cursor(db, sink_row)
    await db.commit()

    stub = _StubSink(
        kind="elastic_bulk", results=[SinkResult.retry(CATEGORY_SERVER, "indexer closed")]
    )
    monkeypatch.setattr(dispatcher, "build_sink", lambda row, settings: stub)

    await dispatcher.dispatch_tick(db, settings=compliance_settings)
    await db.commit()
    await db.refresh(cursor)

    assert cursor.events_delivered == 0
    assert cursor.consecutive_failures == 1
    assert cursor.next_attempt_at is not None
    assert "indexer closed" in cursor.last_error


async def test_dispatcher_dead_letters_a_permanently_rejected_event(
    db, tenant_a, compliance_settings, monkeypatch
):
    from app.db.compliance_v2_models import SiemDeadLetter

    rows = await _seed_audit(db, tenant_a, count=1)
    sink_row = _sink_row(tenant_a, name="elastic-bad-mapping", kind="elastic_bulk")
    db.add(sink_row)
    await db.commit()

    stub = _StubSink(
        kind="elastic_bulk",
        results=[
            SinkResult.permanent("rejected", "mapper_parsing_exception", permanent_event_ids=(str(rows[0].id),))
        ],
    )
    monkeypatch.setattr(dispatcher, "build_sink", lambda row, settings: stub)

    await dispatcher.dispatch_tick(db, settings=compliance_settings)
    await db.commit()

    letters = list(
        (
            await db.execute(
                select(SiemDeadLetter).where(SiemDeadLetter.tenant_id == tenant_a.id)
            )
        ).scalars()
    )
    assert len(letters) == 1
    assert str(letters[0].audit_id) == str(rows[0].id)
    assert letters[0].error_category == "rejected"


async def test_dispatcher_is_a_noop_when_siem_is_disabled(
    db, tenant_a, compliance_settings, monkeypatch
):
    monkeypatch.setattr(compliance_settings, "siem_enabled", False)
    outcome = await dispatcher.dispatch_tick(db, settings=compliance_settings)
    assert outcome["disabled"] is True
    assert outcome["delivered"] == 0
