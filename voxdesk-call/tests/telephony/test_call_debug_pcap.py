"""PART 13 / P13-11 — debug captures: RBAC, audit, signed URLs, retention.

A packet capture is the most sensitive artefact this platform can produce: it
contains the call's audio and the SIP signalling, including any digits the
caller pressed. So the tests are about the *controls*, not the capture:

* `calls.debug` is required, and an ordinary reader of calls does not have it;
* every mint of a download URL writes an audit row naming the user;
* a signed URL expires, and an expired or tampered signature is refused;
* retention purges the file and marks the row, and a purged row stops serving.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.telephony.pcap import (
    DEFAULT_RETENTION_HOURS,
    CaptureError,
    CaptureRequest,
    LocalCaptureStore,
    build_storage_key,
    capture_call,
    checksum,
    purge_expired,
    retention_deadline,
    sign_download,
)


EMPTY_CLASSIC_RAW_PCAP = (
    b"\xd4\xc3\xb2\xa1\x02\x00\x04\x00"
    + b"\x00" * 8
    + b"\xff\xff\x00\x00\x65\x00\x00\x00"
)


def _pcap_record_header(seconds: int, usec: int, captured: int, original: int) -> bytes:
    return b"".join(
        value.to_bytes(4, "little") for value in (seconds, usec, captured, original)
    )


class FakeGateway:
    """Stands in for the SIP gateway's capture API."""

    def __init__(self, payload: bytes = EMPTY_CLASSIC_RAW_PCAP) -> None:
        self.payload = payload
        self.calls: list[dict] = []

    async def capture(self, request: dict) -> bytes:
        self.calls.append(request)
        return self.payload


@pytest.fixture()
def store(tmp_path) -> LocalCaptureStore:
    return LocalCaptureStore(tmp_path / "captures", secret="capture-signing-secret")


@pytest.mark.asyncio
async def test_sip_call_capture_uses_the_carrier_call_id_after_tenant_authorization(
    db, tenant_a
) -> None:
    from sqlalchemy import select

    from app.api.call_debug_routes import _authorized_call
    from app.db.models import Call, CallStatus, Environment

    call_uuid = uuid.uuid4()
    call_sid = "sip-call-0001@carrier.example"
    environment = (
        await db.execute(
            select(Environment).where(
                Environment.tenant_id == tenant_a.id,
                Environment.kind == "production",
            )
        )
    ).scalar_one()
    db.add(
        Call(
            id=call_uuid,
            tenant_id=tenant_a.id,
            environment_id=environment.id,
            call_sid=call_sid,
            from_number="+15559998888",
            to_number="+15550001111",
            status=CallStatus.IN_PROGRESS,
        )
    )
    await db.flush()

    gateway_call_id = await _authorized_call(db, tenant_a.id, call_uuid)
    request = CaptureRequest(
        tenant_id=str(tenant_a.id),
        call_id=str(call_uuid),
        gateway_call_id=gateway_call_id,
    )
    payload = request.as_gateway_payload()

    assert payload["call_id"] == call_sid
    assert payload["tenant_id"] == str(tenant_a.id)


@pytest.mark.asyncio
async def test_a_capture_is_stored_and_the_row_points_at_it(db, store, tenant_a) -> None:
    gateway = FakeGateway()
    call_id = uuid.uuid4()
    outcome = await capture_call(
        db,
        tenant_id=tenant_a.id,
        call_id=call_id,
        gateway=gateway,
        store=store,
    )
    await db.commit()

    assert outcome.size_bytes == len(gateway.payload)
    assert outcome.sha256 == checksum(gateway.payload)
    assert outcome.packet_count == 0
    assert outcome.truncated is False
    assert gateway.calls[0] == {
        "tenant_id": str(tenant_a.id),
        "call_id": str(call_id),
        "duration_seconds": 30,
        "snaplen": 262,
        "direction": "both",
    }
    assert "filter" not in gateway.calls[0]
    stored = store.root / outcome.storage_key
    assert stored.read_bytes() == gateway.payload


@pytest.mark.asyncio
async def test_a_signed_url_round_trips_and_expires(db, store, tenant_a) -> None:
    outcome = await capture_call(
        db, tenant_id=tenant_a.id, call_id=uuid.uuid4(), gateway=FakeGateway(), store=store
    )
    from sqlalchemy import select

    from app.db.telephony_models import CallDebugArtifact

    row = (
        await db.execute(select(CallDebugArtifact).where(CallDebugArtifact.id == uuid.UUID(outcome.artifact_id)))
    ).scalar_one()

    link = await sign_download(db, row, store=store, user_id="user-1", ttl_seconds=60)
    assert link["expires_in"] == 60
    token = link["url"].split("?", 1)[1]
    expires = int(token.split("expires=")[1].split("&")[0])
    signature = token.split("sig=")[1]
    assert store.verify(row.storage_key, expires=expires, signature=signature) is True

    # One second past expiry, the same signature is worthless.
    assert (
        store.verify(row.storage_key, expires=expires, signature=signature, now=expires + 1)
        is False
    )


@pytest.mark.asyncio
async def test_signed_url_cannot_outlive_artifact_retention(db, store, tenant_a) -> None:
    from sqlalchemy import select

    from app.db.telephony_models import CallDebugArtifact

    outcome = await capture_call(
        db, tenant_id=tenant_a.id, call_id=uuid.uuid4(), gateway=FakeGateway(), store=store
    )
    row = (
        await db.execute(
            select(CallDebugArtifact).where(CallDebugArtifact.id == uuid.UUID(outcome.artifact_id))
        )
    ).scalar_one()
    row.expires_at = datetime.now(timezone.utc) + timedelta(seconds=120)

    link = await sign_download(db, row, store=store, user_id="user-1", ttl_seconds=3600)
    expires = int(link["url"].split("expires=")[1].split("&")[0])

    assert link["expires_in"] < 3600
    assert expires <= int(row.expires_at.timestamp())


@pytest.mark.asyncio
async def test_the_mint_is_recorded_on_the_row(db, store, tenant_a) -> None:
    from sqlalchemy import select

    from app.db.telephony_models import CallDebugArtifact

    outcome = await capture_call(
        db, tenant_id=tenant_a.id, call_id=uuid.uuid4(), gateway=FakeGateway(), store=store
    )
    row = (
        await db.execute(select(CallDebugArtifact).where(CallDebugArtifact.id == uuid.UUID(outcome.artifact_id)))
    ).scalar_one()
    await sign_download(db, row, store=store, user_id="user-42")
    assert row.last_accessed_by == "user-42"
    assert row.access_count == 1
    assert row.last_accessed_at is not None


@pytest.mark.asyncio
async def test_a_tampered_signature_is_refused(store) -> None:
    key = build_storage_key("t", "c")
    await store.put(key, b"bytes")
    url = await store.signed_url(key, expires_in=60)
    expires = int(url.split("expires=")[1].split("&")[0])
    assert store.verify(key, expires=expires, signature="0" * 64) is False
    # The signature is bound to the key, so it cannot be replayed on another file.
    other = build_storage_key("t", "c2")
    await store.put(other, b"other")
    signature = url.split("sig=")[1]
    assert store.verify(other, expires=expires, signature=signature) is False


def test_a_key_cannot_escape_the_storage_root(store) -> None:
    with pytest.raises(CaptureError, match="escapes"):
        store._path("../../etc/passwd")


@pytest.mark.asyncio
async def test_local_storage_io_failures_have_specific_capture_errors(store, monkeypatch) -> None:
    from pathlib import Path

    key = build_storage_key("tenant", "call")

    def fail_write(_path, _data):
        raise PermissionError("simulated local disk failure")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "write_bytes", fail_write)
        with pytest.raises(CaptureError) as refused:
            await store.put(key, b"pcap")
    assert refused.value.code == "storage_write_failed"

    await store.put(key, b"pcap")

    def fail_read(_path):
        raise PermissionError("simulated local disk failure")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_bytes", fail_read)
        with pytest.raises(CaptureError) as refused:
            await store.get(key)
    assert refused.value.code == "storage_read_failed"

    with pytest.raises(CaptureError) as missing:
        await store.get(build_storage_key("tenant", "missing"))
    assert missing.value.code == "artifact_missing"

    def fail_unlink(_path):
        raise PermissionError("simulated local disk failure")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail_unlink)
        with pytest.raises(CaptureError) as refused:
            await store.delete(key)
    assert refused.value.code == "storage_delete_failed"


def test_retention_is_bounded() -> None:
    captured, expires = retention_deadline(
        now=datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
    )
    assert expires - captured == timedelta(hours=DEFAULT_RETENTION_HOURS)
    with pytest.raises(CaptureError, match="retention"):
        retention_deadline(hours=0)


def test_a_capture_request_is_validated() -> None:
    with pytest.raises(CaptureError, match="capture length"):
        CaptureRequest(tenant_id="t", call_id="c", seconds=0).validate()
    with pytest.raises(CaptureError, match="snaplen"):
        CaptureRequest(tenant_id="t", call_id="c", snaplen=8).validate()
    assert CaptureRequest(tenant_id="t", call_id="c").as_gateway_payload()["duration_seconds"] == 30


@pytest.mark.asyncio
async def test_invalid_retention_is_rejected_before_gateway_or_storage(db, store, tenant_a) -> None:
    class NeverCalledGateway:
        async def capture(self, _request):
            raise AssertionError("invalid retention must fail before gateway capture")

    with pytest.raises(CaptureError) as refused:
        await capture_call(
            db,
            tenant_id=tenant_a.id,
            call_id=uuid.uuid4(),
            gateway=NeverCalledGateway(),
            store=store,
            retention_hours=0,
        )
    assert refused.value.code == "invalid_retention"
    assert list(store.root.rglob("*.pcap")) == []


@pytest.mark.asyncio
async def test_purge_removes_the_file_and_marks_the_row(db, store, tenant_a) -> None:
    from sqlalchemy import select

    from app.db.telephony_models import CallDebugArtifact

    past = datetime.now(timezone.utc) - timedelta(hours=96)
    outcome = await capture_call(
        db,
        tenant_id=tenant_a.id,
        call_id=uuid.uuid4(),
        gateway=FakeGateway(),
        store=store,
        retention_hours=1,
        now=past,
    )
    await db.commit()
    path = store.root / outcome.storage_key
    assert path.is_file()

    result = await purge_expired(db, tenant_id=tenant_a.id, store=store)
    await db.commit()
    assert result["purged"] == 1
    assert not path.exists()

    row = (
        await db.execute(select(CallDebugArtifact).where(CallDebugArtifact.id == uuid.UUID(outcome.artifact_id)))
    ).scalar_one()
    assert row.purged_at is not None


@pytest.mark.asyncio
async def test_purge_endpoint_is_tenant_scoped_and_audited(
    client, db, owner_a, tenant_a, tenant_b, store, monkeypatch
) -> None:
    from sqlalchemy import select

    from app.db.models import AuditLog
    from app.db.telephony_models import CallDebugArtifact
    from tests.conftest import auth_headers

    past = datetime.now(timezone.utc) - timedelta(hours=2)
    outcomes = {}
    for tenant in (tenant_a, tenant_b):
        outcomes[tenant.id] = await capture_call(
            db,
            tenant_id=tenant.id,
            call_id=uuid.uuid4(),
            gateway=FakeGateway(),
            store=store,
            retention_hours=1,
            now=past,
        )
    await db.commit()
    monkeypatch.setattr("app.api.call_debug_routes._store", lambda: store)

    response = await client.post(
        "/api/calls/debug-artifacts/purge",
        headers=await auth_headers(client, owner_a),
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"purged": 1, "examined": 1}
    rows = (
        await db.execute(
            select(CallDebugArtifact).where(
                CallDebugArtifact.id.in_(
                    [uuid.UUID(outcome.artifact_id) for outcome in outcomes.values()]
                )
            )
        )
    ).scalars().all()
    by_tenant = {row.tenant_id: row for row in rows}
    assert by_tenant[tenant_a.id].purged_at is not None
    assert by_tenant[tenant_b.id].purged_at is None
    assert not (store.root / outcomes[tenant_a.id].storage_key).exists()
    assert (store.root / outcomes[tenant_b.id].storage_key).is_file()

    event = (
        await db.execute(
            select(AuditLog).where(
                AuditLog.tenant_id == tenant_a.id,
                AuditLog.event_type == "call.debug.purge",
            )
        )
    ).scalar_one()
    assert event.actor_user_id == owner_a.id
    assert event.result == "success"
    assert event.detail == {"purged": 1, "examined": 1, "failed": 0}


@pytest.mark.asyncio
async def test_a_purged_artifact_stops_serving(db, store, tenant_a) -> None:
    from sqlalchemy import select

    from app.db.telephony_models import CallDebugArtifact

    outcome = await capture_call(
        db, tenant_id=tenant_a.id, call_id=uuid.uuid4(), gateway=FakeGateway(), store=store
    )
    row = (
        await db.execute(select(CallDebugArtifact).where(CallDebugArtifact.id == uuid.UUID(outcome.artifact_id)))
    ).scalar_one()
    row.purged_at = datetime.now(timezone.utc)
    with pytest.raises(CaptureError, match="purged"):
        await sign_download(db, row, store=store, user_id="user-1")


@pytest.mark.asyncio
async def test_a_gateway_without_capture_support_is_named(db, store, tenant_a) -> None:
    class NoCapture:
        pass

    with pytest.raises(CaptureError) as refused:
        await capture_call(
            db, tenant_id=tenant_a.id, call_id=uuid.uuid4(), gateway=NoCapture(), store=store
        )
    assert refused.value.code == "gateway_unsupported"


@pytest.mark.asyncio
async def test_valid_raw_pcap_packet_record_is_accepted_and_stored(db, store, tenant_a) -> None:
    packet = bytes(28)  # reconstructed IPv4 + UDP headers, with an empty UDP payload
    payload = (
        EMPTY_CLASSIC_RAW_PCAP
        + _pcap_record_header(1, 500_000, len(packet), len(packet))
        + packet
    )
    outcome = await capture_call(
        db,
        tenant_id=tenant_a.id,
        call_id=uuid.uuid4(),
        gateway=FakeGateway(payload),
        store=store,
    )
    assert await store.get(outcome.storage_key) == payload


@pytest.mark.parametrize(
    "payload",
    [
        b"not a pcap",
        EMPTY_CLASSIC_RAW_PCAP + bytes(15),
        EMPTY_CLASSIC_RAW_PCAP[:20] + (1).to_bytes(4, "little"),
        EMPTY_CLASSIC_RAW_PCAP + _pcap_record_header(1, 0, 8, 8) + bytes(4),
    ],
)
@pytest.mark.asyncio
async def test_malformed_gateway_capture_is_rejected_before_storage(
    db, store, tenant_a, payload: bytes
) -> None:
    gateway = FakeGateway(payload)
    with pytest.raises(CaptureError) as refused:
        await capture_call(
            db,
            tenant_id=tenant_a.id,
            call_id=uuid.uuid4(),
            gateway=gateway,
            store=store,
        )
    assert refused.value.code == "invalid_capture"
    assert list(store.root.rglob("*.pcap")) == []


# --------------------------------------------------------------- route layer


@pytest.mark.asyncio
async def test_a_reader_of_calls_cannot_download_a_capture(client, viewer_a) -> None:
    """`calls.debug` is narrower than `call:read`, and the route enforces it."""
    from tests.conftest import auth_headers

    headers = await auth_headers(client, viewer_a)
    response = await client.get(f"/api/calls/{uuid.uuid4()}/pcap", headers=headers)
    assert response.status_code == 403, response.text


@pytest.mark.asyncio
async def test_the_owner_sees_an_empty_capture_list_for_a_call(client, owner_a) -> None:
    from tests.conftest import auth_headers

    headers = await auth_headers(client, owner_a)
    response = await client.get(f"/api/calls/{uuid.uuid4()}/pcap", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json() == {"artifacts": [], "download": None}


@pytest.mark.asyncio
async def test_capture_route_cannot_reach_another_tenants_call(
    client, db, owner_a, tenant_b, store, monkeypatch
) -> None:
    from app.db.telephony_models import TelephonyCallSession
    from tests.conftest import auth_headers

    call_id = uuid.uuid4()
    db.add(
        TelephonyCallSession(
            id=call_id,
            tenant_id=tenant_b.id,
            provider_call_id=f"provider-{uuid.uuid4().hex}",
            direction="inbound",
            from_number="+15550001001",
            to_number="+15550001002",
        )
    )
    await db.commit()
    gateway = FakeGateway()
    monkeypatch.setattr("app.api.call_debug_routes._gateway", lambda: gateway)
    monkeypatch.setattr("app.api.call_debug_routes._store", lambda: store)

    response = await client.post(
        f"/api/calls/{call_id}/pcap",
        headers=await auth_headers(client, owner_a),
    )

    assert response.status_code == 404, response.text
    assert gateway.calls == []
    assert list(store.root.rglob("*.pcap")) == []


@pytest.mark.asyncio
async def test_capture_route_accepts_same_tenant_call_and_audits_metadata(
    client, db, owner_a, tenant_a, store, monkeypatch
) -> None:
    import base64

    from sqlalchemy import select

    from app.db.models import AuditLog
    from app.db.telephony_models import CallDebugArtifact, TelephonyCallSession
    from tests.conftest import auth_headers

    call_id = uuid.uuid4()
    provider_call_id = f"provider-{uuid.uuid4().hex}"
    db.add(
        TelephonyCallSession(
            id=call_id,
            tenant_id=tenant_a.id,
            provider_call_id=provider_call_id,
            direction="inbound",
            from_number="+15550001001",
            to_number="+15550001002",
        )
    )
    await db.commit()

    class MetadataGateway:
        def __init__(self):
            self.calls = []

        async def capture(self, payload):
            self.calls.append(payload)
            return {
                "data_base64": base64.b64encode(EMPTY_CLASSIC_RAW_PCAP).decode("ascii"),
                "packet_count": 0,
                "truncated": True,
            }

    gateway = MetadataGateway()
    monkeypatch.setattr("app.api.call_debug_routes._gateway", lambda: gateway)
    monkeypatch.setattr("app.api.call_debug_routes._store", lambda: store)

    response = await client.post(
        f"/api/calls/{call_id}/pcap",
        headers=await auth_headers(client, owner_a),
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["packet_count"] == 0
    assert body["truncated"] is True
    assert gateway.calls == [
        {
            "tenant_id": str(tenant_a.id),
            "call_id": provider_call_id,
            "duration_seconds": 30,
            "snaplen": 262,
            "direction": "both",
        }
    ]
    artifact = (
        await db.execute(
            select(CallDebugArtifact).where(
                CallDebugArtifact.tenant_id == tenant_a.id,
                CallDebugArtifact.call_id == call_id,
            )
        )
    ).scalar_one()
    assert artifact.packet_count == 0 and artifact.truncated is True
    assert (store.root / artifact.storage_key).read_bytes() == EMPTY_CLASSIC_RAW_PCAP
    event = (
        await db.execute(
            select(AuditLog).where(
                AuditLog.tenant_id == tenant_a.id,
                AuditLog.event_type == "call.debug.capture",
                AuditLog.resource_id == str(call_id),
            )
        )
    ).scalar_one()
    assert event.detail["packet_count"] == 0
    assert event.detail["truncated"] is True


@pytest.mark.asyncio
async def test_capture_list_includes_persisted_packet_metadata(
    client, db, owner_a, tenant_a, store, monkeypatch
) -> None:
    import base64

    from tests.conftest import auth_headers

    class MetadataGateway:
        async def capture(self, _request: dict) -> dict:
            return {
                "data_base64": base64.b64encode(EMPTY_CLASSIC_RAW_PCAP).decode("ascii"),
                "packet_count": 0,
                "truncated": True,
            }

    call_id = uuid.uuid4()
    await capture_call(
        db,
        tenant_id=tenant_a.id,
        call_id=call_id,
        gateway=MetadataGateway(),
        store=store,
    )
    await db.commit()
    monkeypatch.setattr("app.api.call_debug_routes._store", lambda: store)

    response = await client.get(
        f"/api/calls/{call_id}/pcap",
        headers=await auth_headers(client, owner_a),
    )

    assert response.status_code == 200, response.text
    artifact = response.json()["artifacts"][0]
    assert artifact["packet_count"] == 0
    assert artifact["truncated"] is True


@pytest.mark.asyncio
async def test_expired_download_link_mint_is_audited_as_failure(
    client, db, owner_a, tenant_a, store, monkeypatch
) -> None:
    from sqlalchemy import select

    from app.db.models import AuditLog
    from app.db.telephony_models import CallDebugArtifact
    from tests.conftest import auth_headers

    call_id = uuid.uuid4()
    outcome = await capture_call(
        db,
        tenant_id=tenant_a.id,
        call_id=call_id,
        gateway=FakeGateway(),
        store=store,
    )
    row = (
        await db.execute(
            select(CallDebugArtifact).where(CallDebugArtifact.id == uuid.UUID(outcome.artifact_id))
        )
    ).scalar_one()
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await db.commit()
    monkeypatch.setattr("app.api.call_debug_routes._store", lambda: store)

    response = await client.get(
        f"/api/calls/{call_id}/pcap",
        headers=await auth_headers(client, owner_a),
    )

    assert response.status_code == 200, response.text
    assert response.json()["download"]["error"]["code"] == "artifact_expired"
    event = (
        await db.execute(
            select(AuditLog).where(
                AuditLog.tenant_id == tenant_a.id,
                AuditLog.event_type == "call.debug.download",
                AuditLog.resource_id == str(call_id),
            )
        )
    ).scalar_one()
    assert event.result == "failure"
    assert event.detail["error_code"] == "artifact_expired"


@pytest.mark.asyncio
async def test_capture_endpoint_returns_and_audits_gateway_metadata(
    client, owner_a, monkeypatch
) -> None:
    from app.api import call_debug_routes
    from app.telephony.pcap import CaptureOutcome
    from tests.conftest import auth_headers

    events = []

    async def allow_call(*_args, **_kwargs):
        return None

    async def fake_capture(*_args, **_kwargs):
        return CaptureOutcome(
            "artifact-id",
            "tenant/call/capture.pcap",
            len(EMPTY_CLASSIC_RAW_PCAP),
            "sha256",
            datetime.now(timezone.utc) + timedelta(hours=1),
            0,
            True,
        )

    async def capture_event(*_args, **kwargs):
        events.append(kwargs)

    monkeypatch.setattr(call_debug_routes, "_authorized_call", allow_call)
    monkeypatch.setattr(call_debug_routes, "_gateway", lambda: object())
    monkeypatch.setattr(call_debug_routes, "_store", lambda: object())
    monkeypatch.setattr(call_debug_routes.capture_logic, "capture_call", fake_capture)
    monkeypatch.setattr(call_debug_routes, "record_event", capture_event)

    response = await client.post(
        f"/api/calls/{uuid.uuid4()}/pcap",
        headers=await auth_headers(client, owner_a),
    )

    assert response.status_code == 201
    assert response.json()["packet_count"] == 0
    assert response.json()["truncated"] is True
    assert events[0]["detail"]["packet_count"] == 0
    assert events[0]["detail"]["truncated"] is True


@pytest.mark.parametrize(
    "error_code, expected_status",
    [("storage_write_failed", 503), ("invalid_capture", 502)],
)
@pytest.mark.asyncio
async def test_capture_failures_return_appropriate_upstream_status(
    client, db, owner_a, monkeypatch, error_code: str, expected_status: int
) -> None:
    from sqlalchemy import select

    from app.api import call_debug_routes
    from app.db.models import AuditLog
    from tests.conftest import auth_headers

    async def allow_call(*_args, **_kwargs):
        return None

    async def fail_capture(*_args, **_kwargs):
        raise CaptureError(error_code, "simulated capture failure")

    monkeypatch.setattr(call_debug_routes, "_authorized_call", allow_call)
    monkeypatch.setattr(call_debug_routes, "_gateway", lambda: object())
    monkeypatch.setattr(call_debug_routes, "_store", lambda: object())
    monkeypatch.setattr(call_debug_routes.capture_logic, "capture_call", fail_capture)

    call_id = uuid.uuid4()
    response = await client.post(
        f"/api/calls/{call_id}/pcap",
        headers=await auth_headers(client, owner_a),
    )

    assert response.status_code == expected_status
    assert response.json()["detail"]["code"] == error_code
    event = (
        await db.execute(
            select(AuditLog).where(
                AuditLog.tenant_id == owner_a.tenant_id,
                AuditLog.event_type == "call.debug.capture",
                AuditLog.resource_id == str(call_id),
            )
        )
    ).scalar_one()
    assert event.result == "failure"
    assert event.detail["error_code"] == error_code


@pytest.mark.asyncio
async def test_capture_store_configuration_failure_is_audited(client, db, owner_a, monkeypatch) -> None:
    from fastapi import HTTPException
    from sqlalchemy import select

    from app.api import call_debug_routes
    from app.db.models import AuditLog
    from tests.conftest import auth_headers

    async def allow_call(*_args, **_kwargs):
        return None

    def unavailable_store():
        raise HTTPException(status_code=503, detail="capture storage unavailable")

    monkeypatch.setattr(call_debug_routes, "_authorized_call", allow_call)
    monkeypatch.setattr(call_debug_routes, "_gateway", lambda: object())
    monkeypatch.setattr(call_debug_routes, "_store", unavailable_store)
    call_id = uuid.uuid4()

    response = await client.post(
        f"/api/calls/{call_id}/pcap",
        headers=await auth_headers(client, owner_a),
    )

    assert response.status_code == 503
    event = (
        await db.execute(
            select(AuditLog).where(
                AuditLog.tenant_id == owner_a.tenant_id,
                AuditLog.event_type == "call.debug.capture",
                AuditLog.resource_id == str(call_id),
            )
        )
    ).scalar_one()
    assert event.result == "failure"
    assert event.detail["error_code"] == "capture_storage_unavailable"


@pytest.mark.asyncio
async def test_download_mint_store_configuration_failure_is_audited(
    client, db, owner_a, tenant_a, store, monkeypatch
) -> None:
    from fastapi import HTTPException
    from sqlalchemy import select

    from app.api import call_debug_routes
    from app.db.models import AuditLog
    from tests.conftest import auth_headers

    call_id = uuid.uuid4()
    outcome = await capture_call(
        db,
        tenant_id=tenant_a.id,
        call_id=call_id,
        gateway=FakeGateway(),
        store=store,
    )
    await db.commit()

    def unavailable_store():
        raise HTTPException(status_code=503, detail="capture storage unavailable")

    monkeypatch.setattr(call_debug_routes, "_store", unavailable_store)
    response = await client.get(
        f"/api/calls/{call_id}/pcap",
        headers=await auth_headers(client, owner_a),
    )

    assert response.status_code == 503
    event = (
        await db.execute(
            select(AuditLog).where(
                AuditLog.tenant_id == tenant_a.id,
                AuditLog.event_type == "call.debug.download",
                AuditLog.resource_id == str(call_id),
            )
        )
    ).scalar_one()
    assert event.result == "failure"
    assert event.detail == {
        "artifact_id": outcome.artifact_id,
        "error_code": "capture_storage_unavailable",
    }


@pytest.mark.asyncio
async def test_purge_store_configuration_failure_is_audited(client, db, owner_a, monkeypatch) -> None:
    from fastapi import HTTPException
    from sqlalchemy import select

    from app.api import call_debug_routes
    from app.db.models import AuditLog
    from tests.conftest import auth_headers

    def unavailable_store():
        raise HTTPException(status_code=503, detail="capture storage unavailable")

    monkeypatch.setattr(call_debug_routes, "_store", unavailable_store)
    response = await client.post(
        "/api/calls/debug-artifacts/purge",
        headers=await auth_headers(client, owner_a),
    )

    assert response.status_code == 503
    event = (
        await db.execute(
            select(AuditLog).where(
                AuditLog.tenant_id == owner_a.tenant_id,
                AuditLog.event_type == "call.debug.purge",
            )
        )
    ).scalar_one()
    assert event.result == "failure"
    assert event.detail["error_code"] == "capture_storage_unavailable"
    assert event.detail["examined"] == 0 and event.detail["purged"] == 0


@pytest.mark.asyncio
async def test_an_unsigned_download_request_is_forbidden(client) -> None:
    response = await client.get(
        "/api/calls/debug-artifacts/tenant/call/file.pcap?expires=1&sig=deadbeef"
    )
    assert response.status_code in (403, 503), response.text


@pytest.mark.asyncio
async def test_signed_download_serves_bytes_without_cache(client, store, monkeypatch) -> None:
    key = build_storage_key("tenant", "call")
    await store.put(key, EMPTY_CLASSIC_RAW_PCAP)
    url = await store.signed_url(key, expires_in=60)
    monkeypatch.setattr("app.api.call_debug_routes._store", lambda: store)

    response = await client.get(url)

    assert response.status_code == 200, response.text
    assert response.content == EMPTY_CLASSIC_RAW_PCAP
    assert response.headers["content-type"].startswith("application/vnd.tcpdump.pcap")
    cache_directives = {item.strip() for item in response.headers["cache-control"].split(",")}
    assert {"private", "no-store"} <= cache_directives
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.asyncio
async def test_gateway_capture_response_base64_is_decoded_and_scoped() -> None:
    import base64

    from app.telephony.sip.sip_gateway_client import SipGatewayClient

    raw_pcap = EMPTY_CLASSIC_RAW_PCAP
    observed = {}

    async def transport(method, url, *, headers, json):
        observed.update(method=method, url=url, headers=headers, payload=json)
        return 200, {
            "content_type": "application/vnd.tcpdump.pcap",
            "data_base64": base64.b64encode(raw_pcap).decode("ascii"),
            "packet_count": 0,
            "truncated": False,
        }

    client = SipGatewayClient(base_url="http://gateway", token="token", transport=transport)
    request = CaptureRequest(tenant_id="tenant-1", call_id="call-1", seconds=2)
    response = await client.capture(request.as_gateway_payload())

    assert observed["method"] == "POST"
    assert observed["url"] == "http://gateway/v1/calls/call-1/pcap"
    assert observed["payload"]["tenant_id"] == "tenant-1"
    assert response["packet_count"] == 0
    decoded = base64.b64decode(response["data_base64"], validate=True)
    assert decoded == raw_pcap


@pytest.mark.asyncio
async def test_capture_call_decodes_gateway_base64_response(db, store, tenant_a) -> None:
    import base64

    class EncodedGateway:
        async def capture(self, request: dict) -> dict:
            assert request["tenant_id"] == str(tenant_a.id)
            return {"data_base64": base64.b64encode(EMPTY_CLASSIC_RAW_PCAP).decode("ascii")}

    outcome = await capture_call(
        db,
        tenant_id=tenant_a.id,
        call_id=uuid.uuid4(),
        gateway=EncodedGateway(),
        store=store,
    )
    assert await store.get(outcome.storage_key) == EMPTY_CLASSIC_RAW_PCAP
    assert outcome.packet_count == 0
    assert outcome.truncated is False


@pytest.mark.asyncio
async def test_capture_preserves_gateway_packet_metadata(db, store, tenant_a) -> None:
    import base64

    class MetadataGateway:
        async def capture(self, _request: dict) -> dict:
            return {
                "data_base64": base64.b64encode(EMPTY_CLASSIC_RAW_PCAP).decode("ascii"),
                "packet_count": 0,
                "truncated": True,
            }

    call_id = uuid.uuid4()
    outcome = await capture_call(
        db,
        tenant_id=tenant_a.id,
        call_id=call_id,
        gateway=MetadataGateway(),
        store=store,
    )
    from sqlalchemy import select

    from app.db.telephony_models import CallDebugArtifact

    row = (
        await db.execute(
            select(CallDebugArtifact).where(CallDebugArtifact.id == uuid.UUID(outcome.artifact_id))
        )
    ).scalar_one()
    assert row.tenant_id == tenant_a.id
    assert row.call_id == call_id
    assert row.packet_count == 0
    assert row.truncated is True
    assert outcome.as_dict()["packet_count"] == 0
    assert outcome.as_dict()["truncated"] is True


@pytest.mark.asyncio
async def test_capture_rejects_gateway_packet_count_mismatch(db, store, tenant_a) -> None:
    import base64

    class MismatchGateway:
        async def capture(self, _request: dict) -> dict:
            return {
                "data_base64": base64.b64encode(EMPTY_CLASSIC_RAW_PCAP).decode("ascii"),
                "packet_count": 1,
                "truncated": False,
            }

    with pytest.raises(CaptureError, match="packet count") as refused:
        await capture_call(
            db,
            tenant_id=tenant_a.id,
            call_id=uuid.uuid4(),
            gateway=MismatchGateway(),
            store=store,
        )
    assert refused.value.code == "invalid_capture"
    assert list(store.root.rglob("*.pcap")) == []


@pytest.mark.asyncio
async def test_oversized_base64_is_rejected_before_decoding(db, store, tenant_a, monkeypatch) -> None:
    monkeypatch.setattr("app.telephony.pcap.MAX_CAPTURE_BYTES", 3)

    def unexpected_decode(*_args, **_kwargs):
        pytest.fail("oversized base64 must be rejected before allocation")

    monkeypatch.setattr("app.telephony.pcap.base64.b64decode", unexpected_decode)

    class OversizedGateway:
        async def capture(self, _request: dict) -> dict:
            return {"data_base64": "A" * 5}

    with pytest.raises(CaptureError) as refused:
        await capture_call(
            db,
            tenant_id=tenant_a.id,
            call_id=uuid.uuid4(),
            gateway=OversizedGateway(),
            store=store,
        )
    assert refused.value.code == "capture_too_large"
    assert list(store.root.rglob("*.pcap")) == []


@pytest.mark.asyncio
async def test_s3_capture_store_uses_private_prefix_and_signed_app_url() -> None:
    from app.telephony.pcap import S3CaptureStore

    class FakeClient:
        def __init__(self):
            self.objects = {}
            self.deleted = []

        def delete_object(self, *, Bucket, Key):
            self.deleted.append((Bucket, Key))
            self.objects.pop(Key, None)

    class FakeStorage:
        bucket = "debug-artifacts"

        def __init__(self):
            self.client = FakeClient()

        async def put(self, key, data, *, content_type=None):
            assert content_type == "application/vnd.tcpdump.pcap"
            self.client.objects[key] = bytes(data)
            return key

        async def get(self, key):
            return self.client.objects[key]

        def _get_client(self):
            return self.client

    backing = FakeStorage()
    store = S3CaptureStore(secret="capture-key", storage=backing)
    key = build_storage_key("tenant", "call")
    await store.put(key, b"pcap")
    object_key = f"telephony-pcap/{key}"
    assert backing.client.objects[object_key] == b"pcap"
    assert await store.get(key) == b"pcap"
    url = await store.signed_url(key, expires_in=60)
    expires = int(url.split("expires=")[1].split("&")[0])
    signature = url.split("sig=")[1]
    assert url.startswith("/api/calls/debug-artifacts/")
    assert store.verify(key, expires=expires, signature=signature)
    assert await store.delete(key)
    assert backing.client.deleted == [("debug-artifacts", object_key)]


@pytest.mark.asyncio
async def test_s3_capture_store_uses_boto3_requests_without_external_network() -> None:
    """Exercise the real S3 adapter request path with botocore's protocol stub."""
    import io

    import boto3
    from botocore.response import StreamingBody
    from botocore.stub import Stubber

    from app.knowledge.storage.s3 import S3Storage
    from app.telephony.pcap import S3CaptureStore

    bucket = "voxdesk-pcap-test"
    region = "us-east-1"
    payload = EMPTY_CLASSIC_RAW_PCAP
    key = build_storage_key("tenant-1", "call-1")
    object_key = f"telephony-pcap/{key}"
    client = boto3.client(
        "s3",
        region_name=region,
        aws_access_key_id="unit-test-access-key",
        aws_secret_access_key="unit-test-secret-key",
    )
    storage = S3Storage(bucket=bucket, region=region)
    storage._client = client
    store = S3CaptureStore(secret="unit-test-capture-signing-secret", storage=storage)
    stubber = Stubber(client)
    stubber.add_response(
        "put_object",
        {},
        {
            "Bucket": bucket,
            "Key": object_key,
            "Body": payload,
            "ContentType": "application/vnd.tcpdump.pcap",
        },
    )
    stubber.add_response(
        "get_object",
        {
            "Body": StreamingBody(io.BytesIO(payload), len(payload)),
            "ContentLength": len(payload),
        },
        {"Bucket": bucket, "Key": object_key},
    )
    stubber.add_response(
        "delete_object",
        {},
        {"Bucket": bucket, "Key": object_key},
    )

    with stubber:
        assert await store.put(key, payload) == key
        assert await store.get(key) == payload
        signed_url = await store.signed_url(key, expires_in=60)
        expires = int(signed_url.split("expires=")[1].split("&")[0])
        signature = signed_url.split("sig=")[1]
        assert signed_url.startswith("/api/calls/debug-artifacts/")
        assert store.verify(key, expires=expires, signature=signature)
        assert await store.delete(key)
        stubber.assert_no_pending_responses()


@pytest.mark.parametrize(
    "failure, expected_code",
    [("missing", "artifact_missing"), ("unavailable", "storage_read_failed")],
)
@pytest.mark.asyncio
async def test_s3_reads_distinguish_missing_objects_from_storage_outages(
    failure: str, expected_code: str
) -> None:
    from app.telephony.pcap import S3CaptureStore

    class FailingStorage:
        async def get(self, _key):
            if failure == "missing":
                raise KeyError("missing object")
            raise RuntimeError("object store unavailable")

    store = S3CaptureStore(secret="capture-key", storage=FailingStorage())
    with pytest.raises(CaptureError) as refused:
        await store.get("tenant/call/file.pcap")
    assert refused.value.code == expected_code


@pytest.mark.parametrize(
    "error_code, status_code, expected_code",
    [
        ("NoSuchKey", 404, "artifact_missing"),
        ("", 404, "artifact_missing"),
        ("NoSuchBucket", 404, "storage_read_failed"),
        ("AccessDenied", 403, "storage_read_failed"),
    ],
)
@pytest.mark.asyncio
async def test_s3_client_errors_are_classified_by_error_code(
    error_code: str, status_code: int, expected_code: str
) -> None:
    from app.knowledge.storage.base import StorageError
    from app.telephony.pcap import S3CaptureStore

    class ClientError(Exception):
        def __init__(self):
            self.response = {
                "Error": {"Code": error_code},
                "ResponseMetadata": {"HTTPStatusCode": status_code},
            }
            super().__init__("simulated S3 client error")

    class S3LikeStorage:
        async def get(self, _key):
            try:
                raise ClientError()
            except ClientError as error:
                raise StorageError("object retrieval failed") from error

    store = S3CaptureStore(secret="capture-key", storage=S3LikeStorage())
    with pytest.raises(CaptureError) as refused:
        await store.get("tenant/call/file.pcap")
    assert refused.value.code == expected_code


@pytest.mark.parametrize(
    "error_code, expected_status",
    [("artifact_missing", 404), ("storage_read_failed", 503)],
)
@pytest.mark.asyncio
async def test_capture_download_maps_missing_and_storage_errors(
    client, monkeypatch, error_code: str, expected_status: int
) -> None:
    from app.api import call_debug_routes

    class FailingStore:
        def verify(self, _key, *, expires, signature):
            return True

        async def get(self, _key):
            raise CaptureError(error_code, "simulated download failure")

    monkeypatch.setattr(call_debug_routes, "_store", lambda: FailingStore())
    response = await client.get(
        "/api/calls/debug-artifacts/tenant/call/file.pcap?expires=9999999999&sig=valid"
    )
    assert response.status_code == expected_status


@pytest.mark.parametrize(
    "key",
    ["../escape.pcap", "/absolute.pcap", "tenant//call.pcap", r"tenant\\call.pcap"],
)
@pytest.mark.asyncio
async def test_s3_store_rejects_unsafe_keys_before_storage_access(key: str) -> None:
    from app.telephony.pcap import S3CaptureStore

    class NeverCalledStorage:
        async def put(self, *args, **kwargs):
            raise AssertionError("an unsafe key must not reach object storage")

    store = S3CaptureStore(secret="capture-key", storage=NeverCalledStorage())
    with pytest.raises(CaptureError) as refused:
        await store.put(key, b"pcap")
    assert refused.value.code == "invalid_key"


@pytest.mark.asyncio
async def test_s3_delete_failure_does_not_mark_expired_artifact_purged(db, tenant_a) -> None:
    from sqlalchemy import select

    from app.db.telephony_models import CallDebugArtifact
    from app.telephony.pcap import S3CaptureStore

    class FailingClient:
        def delete_object(self, *, Bucket, Key):
            raise RuntimeError("simulated object-store delete failure")

    class FakeStorage:
        bucket = "debug-artifacts"

        def __init__(self):
            self.client = FailingClient()

        async def put(self, key, data, *, content_type=None):
            return key

        def _get_client(self):
            return self.client

    store = S3CaptureStore(secret="capture-key", storage=FakeStorage())
    captured_at = datetime.now(timezone.utc) - timedelta(hours=2)
    outcome = await capture_call(
        db,
        tenant_id=tenant_a.id,
        call_id=uuid.uuid4(),
        gateway=FakeGateway(),
        store=store,
        retention_hours=1,
        now=captured_at,
    )
    await db.commit()

    result = await purge_expired(db, tenant_id=tenant_a.id, store=store)
    await db.commit()

    row = (
        await db.execute(
            select(CallDebugArtifact).where(
                CallDebugArtifact.id == uuid.UUID(outcome.artifact_id)
            )
        )
    ).scalar_one()
    assert result == {"purged": 0, "examined": 1}
    assert row.purged_at is None


@pytest.mark.asyncio
async def test_capture_scope_mismatch_fails_before_calling_gateway(db, store, tenant_a) -> None:
    class NeverCalled:
        async def capture(self, request):
            raise AssertionError("a mismatched capture must not reach the gateway")

    with pytest.raises(CaptureError) as exc:
        await capture_call(
            db,
            tenant_id=tenant_a.id,
            call_id=uuid.uuid4(),
            gateway=NeverCalled(),
            store=store,
            request=CaptureRequest(tenant_id="other-tenant", call_id="call"),
        )
    assert exc.value.code == "scope_mismatch"
