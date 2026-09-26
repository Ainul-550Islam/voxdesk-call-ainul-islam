"""Cross-tenant recording, transcript, number, callback and credential isolation."""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.telephony.call_events import TelephonyCallbackEvent
from app.telephony.media_storage import issue, verify
from app.telephony.number_provisioning import assign, provision, release
from app.telephony.providers.factory import public_config
from app.telephony.providers.webhook_verifier import verify as verify_webhook
from app.telephony.recording import authorize_read, open_grant, request_recording
from app.telephony.recording_policy import get_owned, save
from app.telephony.replay import replay
from app.telephony.transcription import enqueue, get_owned as get_transcript
from app.tenancy.isolation import Forbidden, NotFound
from tests.conftest import auth_headers, make_call, subscribe

pytestmark = pytest.mark.asyncio


class _Adapter:
    async def provision_number(self, e164, *, country=""):
        from app.telephony.capabilities import CapabilitySet
        from app.telephony.providers.base import NumberResult

        return NumberResult(
            "twilio", e164, "PN-SEC", CapabilitySet(voice=True, source="provider_api")
        )

    async def release_number(self, external_id):
        from app.telephony.providers.base import NumberResult

        return NumberResult("twilio", "", external_id)


async def test_tenant_a_cannot_touch_tenant_b_media(
    db, client, tenant_a, tenant_b, owner_a, owner_b, viewer_a, monkeypatch
):
    monkeypatch.setattr(settings, "telnyx_api_key", "TELNYX-ISOLATION-SECRET", raising=False)
    monkeypatch.setattr(settings, "twilio_skip_webhook_verify", False, raising=False)
    tenant_b.record_calls = True
    await db.commit()
    await subscribe(db, tenant_b)
    number = await provision(
        db, tenant_b, e164="+15558880001", provider="twilio", adapter=_Adapter()
    )
    call = await make_call(db, tenant_b)
    recording = await request_recording(
        db, call, provider="twilio", consent_category="one_party", consent_state="granted"
    )
    job = await enqueue(db, call, language="en-US")
    saved = await save(db, tenant_b, enabled=False, retention_days=10)
    event = TelephonyCallbackEvent(
        tenant_id=tenant_b.id,
        provider="twilio",
        event_type="call.status",
        external_id=call.call_sid,
        event_id="evt-sec",
        idempotency_key="a" * 64,
        status="dead_letter",
        safe_metadata={},
    )
    db.add(event)
    await db.commit()

    with pytest.raises(NotFound):
        await authorize_read(
            db,
            tenant_id=tenant_a.id,
            recording_id=recording.id,
            role=owner_a.role,
            actor_user_id=owner_a.id,
        )
    with pytest.raises(NotFound):
        await get_transcript(db, tenant_a.id, job.id, owner_a.role)
    with pytest.raises(NotFound):
        await assign(db, tenant_a.id, number.id, use="voice")
    with pytest.raises(NotFound):
        await release(db, tenant_a.id, number.id, adapter=_Adapter())
    with pytest.raises(NotFound):
        await replay(
            db,
            tenant_id=tenant_a.id,
            event_id=event.id,
            role=owner_a.role,
            actor_user_id=owner_a.id,
        )
    with pytest.raises(NotFound):
        await get_owned(db, tenant_a.id, saved.id)

    rendered = str(public_config())
    assert "TELNYX-ISOLATION-SECRET" not in rendered
    owner = await auth_headers(client, owner_a)
    listed = await client.get("/api/phone-numbers", headers=owner)
    assert listed.status_code == 200
    assert "TELNYX-ISOLATION-SECRET" not in listed.text

    from starlette.requests import Request

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": "/telephony/status",
        "raw_path": b"/telephony/status",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 1),
        "server": ("example.com", 443),
    }

    async def receive():
        return {"type": "http.request", "body": b"CallSid=CA-forged", "more_body": False}

    forged = await verify_webhook("twilio", Request(scope, receive))
    assert forged.valid is False
    assert forged.reason != "twilio_signature"

    grant = issue(recording.tenant_id, recording.id, now=1_700_000_000, ttl_seconds=30)
    expired = verify(
        grant.token,
        tenant_id=recording.tenant_id,
        recording_id=recording.id,
        now=grant.expires_at + 1,
    )
    assert expired.allowed is False
    assert expired.reason == "expired"
    with pytest.raises(Forbidden):
        open_grant(recording, grant.token, role=viewer_a.role, now=1_700_000_000)
    with pytest.raises(Forbidden):
        await authorize_read(
            db,
            tenant_id=tenant_b.id,
            recording_id=recording.id,
            role=viewer_a.role,
            actor_user_id=viewer_a.id,
        )
