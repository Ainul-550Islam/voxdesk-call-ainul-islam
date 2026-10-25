import base64
import hashlib
import hmac
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext
from app.contact_center_v2.errors import Conflict, NotConfigured, PermissionDenied
from app.contact_center_v2.occupancy import reserve_agent
from app.contact_center_v2.repository import utcnow
from app.contact_center_v2.settings import ContactCenterV2Settings
from app.contact_center_v2.softphone import (
    call_payload,
    ice_servers,
    record_agent_leg,
    revoke_session,
    set_recording_pause,
    transfer_call,
)
from app.db.contact_center_v2_models import SoftphoneCall, SoftphoneLeg, SoftphoneSession
from app.db.models import Call, CallStatus, Lead, User
from app.telephony import phone
from tests.contact_center_v2.conftest import Cc2Scope


async def make_call(
    db: AsyncSession,
    scope: Cc2Scope,
    agent: User,
    registration: SoftphoneSession,
) -> SoftphoneCall:
    row = SoftphoneCall(
        tenant_id=scope.tenant.id,
        environment_id=scope.environment.id,
        session_id=registration.id,
        agent_user_id=agent.id,
        origin="agent_outbound",
        status="dialing",
        conference_name=f"voxdesk-{uuid.uuid4()}",
        idempotency_key=f"test-{uuid.uuid4().hex}",
    )
    db.add(row)
    await db.flush()
    await reserve_agent(
        db,
        tenant_id=scope.tenant.id,
        environment_id=scope.environment.id,
        user_id=agent.id,
        kind="softphone",
        resource_id=row.id,
    )
    await db.commit()
    return row


async def make_connected_call(
    db: AsyncSession,
    scope: Cc2Scope,
    agent: User,
    registration: SoftphoneSession,
    lead: Lead | None = None,
) -> tuple[SoftphoneCall, Call]:
    row = await make_call(db, scope, agent, registration)
    customer_sid = f"CA-CUSTOMER-{uuid.uuid4().hex[:16]}"
    canonical = Call(
        tenant_id=scope.tenant.id,
        environment_id=scope.environment.id,
        call_sid=customer_sid,
        from_number=scope.tenant.twilio_number,
        to_number=lead.phone if lead else "+15550002000",
        lead_id=lead.id if lead else None,
        status=CallStatus.IN_PROGRESS,
    )
    db.add(canonical)
    await db.flush()
    row.call_id = canonical.id
    row.status = "in_progress"
    db.add_all([
        SoftphoneLeg(
            tenant_id=row.tenant_id, environment_id=row.environment_id,
            softphone_call_id=row.id, call_id=canonical.id, kind="customer",
            provider_call_id=customer_sid, idempotency_key=f"customer:{row.id}",
            status="in_progress", answered_at=utcnow(),
        ),
        SoftphoneLeg(
            tenant_id=row.tenant_id, environment_id=row.environment_id,
            softphone_call_id=row.id, kind="agent",
            provider_call_id=f"CA-AGENT-{uuid.uuid4().hex[:16]}",
            idempotency_key=f"agent:{row.id}", status="in_progress", answered_at=utcnow(),
        ),
    ])
    await db.commit()
    return row, canonical


def test_turn_rest_credentials_are_short_lived_and_authenticated():
    user_id = uuid.uuid4()
    config = ContactCenterV2Settings(
        stun_url="stun:stun.example.test:3478",
        turn_udp_url="turn:turn.example.test:3478?transport=udp",
        turns_url="turns:turn.example.test:5349?transport=tcp",
        turn_static_secret="test-only-turn-secret",
        turn_ttl_seconds=300,
    )
    servers = ice_servers(user_id, config)
    assert servers[0] == {"urls": ["stun:stun.example.test:3478"]}
    turn = servers[1]
    username = turn["username"]
    assert username.endswith(f":{user_id}")
    expected = base64.b64encode(
        hmac.new(b"test-only-turn-secret", username.encode(), hashlib.sha1).digest()
    ).decode()
    assert turn["credential"] == expected
    assert int(username.split(":", 1)[0]) > 0


def test_turn_urls_without_a_secret_fail_closed():
    config = ContactCenterV2Settings(
        stun_url="",
        turn_udp_url="turn:turn.example.test:3478",
        turn_tcp_url="",
        turns_url="",
        turn_static_secret="",
    )
    with pytest.raises(NotConfigured):
        ice_servers(uuid.uuid4(), config)


async def test_agent_connection_comes_only_from_matching_signed_leg_data(
    db: AsyncSession,
    cc2_scope: Cc2Scope,
    agent_a: User,
    softphone_registration: SoftphoneSession,
    available_presence,
):
    call = await make_call(db, cc2_scope, agent_a, softphone_registration)
    assert (await call_payload(db, call))["agent_joined_at"] is None
    with pytest.raises(PermissionDenied):
        await record_agent_leg(
            db,
            call_id=call.id,
            softphone_session_id=softphone_registration.id,
            provider_call_id="CA-WRONG-IDENTITY",
            from_uri="client:another-agent",
        )
    leg = await record_agent_leg(
        db,
        call_id=call.id,
        softphone_session_id=softphone_registration.id,
        provider_call_id="CA-AGENT-1",
        from_uri=f"client:{softphone_registration.client_identity}",
    )
    replay = await record_agent_leg(
        db,
        call_id=call.id,
        softphone_session_id=softphone_registration.id,
        provider_call_id="CA-AGENT-1",
        from_uri=f"client:{softphone_registration.client_identity}",
    )
    await db.commit()
    assert replay.id == leg.id
    assert call.status == "in_progress"
    assert (await call_payload(db, call))["agent_joined_at"] == leg.answered_at.isoformat()
    assert available_presence.state == "busy"


async def test_active_call_prevents_registration_revoke(
    db: AsyncSession,
    cc2_scope: Cc2Scope,
    agent_a: User,
    softphone_registration: SoftphoneSession,
    available_presence,
):
    call = await make_call(db, cc2_scope, agent_a, softphone_registration)
    context = TenantContext(user=agent_a, tenant=cc2_scope.tenant)
    with pytest.raises(Conflict):
        await revoke_session(db, context, softphone_registration.id)
    call.status = "cancelled"
    await db.commit()
    revoked = await revoke_session(db, context, softphone_registration.id)
    assert revoked.status == "revoked"


async def test_briefing_and_customer_context_are_bounded_and_redacted(
    db: AsyncSession,
    cc2_scope: Cc2Scope,
    agent_a: User,
    softphone_registration: SoftphoneSession,
    available_presence,
    lead_factory,
):
    lead = await lead_factory()
    call, canonical = await make_connected_call(db, cc2_scope, agent_a, softphone_registration, lead)
    canonical.transfer_context = {
        "warm_transfer": {
            "briefing_text": f"Customer context includes {lead.phone}",
            "status": "briefing_played", "human_briefed": True,
            "target_number": "+15550009999", "target_twiml": "<Dial>secret</Dial>",
        },
    }
    prior = Call(
        tenant_id=cc2_scope.tenant.id, environment_id=cc2_scope.environment.id,
        call_sid=f"CA-PRIOR-{uuid.uuid4().hex[:16]}", from_number=cc2_scope.tenant.twilio_number,
        to_number=lead.phone, lead_id=lead.id, status=CallStatus.COMPLETED,
        summary=f"Prior conversation with {lead.phone}", duration_seconds=18,
    )
    db.add(prior)
    await db.commit()
    payload = await call_payload(db, call)
    briefing = payload["warm_transfer_briefing"]
    assert briefing["status"] == "briefing_played"
    assert lead.phone not in briefing["text_redacted"]
    assert "target_number" not in briefing and "target_twiml" not in briefing
    context = payload["customer_context"]
    assert context["lead"]["id"] == str(lead.id)
    assert len(context["prior_calls"]) == 1
    assert lead.phone not in context["prior_calls"][0]["summary_redacted"]


async def test_recording_pause_resume_is_provider_backed_and_idempotent(
    db: AsyncSession,
    cc2_scope: Cc2Scope,
    agent_a: User,
    softphone_registration: SoftphoneSession,
    available_presence,
):
    call, canonical = await make_connected_call(db, cc2_scope, agent_a, softphone_registration)

    class Recording:
        status = "in-progress"
        def update(self, *, status):
            self.status = status
            return self

    class Recordings:
        def __init__(self, recording): self.recording = recording
        def list(self, *, limit):
            assert limit == 20
            return [self.recording]

    class Calls:
        def __init__(self, recording): self.recordings = Recordings(recording)
        def __call__(self, provider_id): return self

    class Provider:
        def __init__(self, recording): self.calls = Calls(recording)

    class Adapter:
        def __init__(self):
            self.recording = Recording()
            self.provider = Provider(self.recording)
        def require_configured(self): return None
        def _client(self): return self.provider

    adapter = Adapter()
    context = TenantContext(user=agent_a, tenant=cc2_scope.tenant)
    await set_recording_pause(db, context, call_id=call.id, paused=True, adapter=adapter)
    assert adapter.recording.status == "paused"
    assert canonical.transfer_context["cc2_recording"]["paused"] is True
    await set_recording_pause(db, context, call_id=call.id, paused=True, adapter=adapter)
    await set_recording_pause(db, context, call_id=call.id, paused=False, adapter=adapter)
    assert adapter.recording.status == "in-progress"
    assert (await call_payload(db, call))["recording_paused"] is False


async def test_same_target_consult_and_blind_transfer_are_idempotent(
    db: AsyncSession,
    cc2_scope: Cc2Scope,
    agent_a: User,
    softphone_registration: SoftphoneSession,
    available_presence,
):
    call, canonical = await make_connected_call(db, cc2_scope, agent_a, softphone_registration)
    target = "+15550003000"
    consult = SoftphoneLeg(
        tenant_id=call.tenant_id, environment_id=call.environment_id,
        softphone_call_id=call.id, kind="consult", status="in_progress",
        provider_call_id=f"CA-CONSULT-{uuid.uuid4().hex[:16]}",
        idempotency_key=f"consult:{call.id}", destination_redacted=phone.redact(target),
    )
    db.add(consult)
    await db.commit()
    context = TenantContext(user=agent_a, tenant=cc2_scope.tenant)
    returned, existing = await transfer_call(db, context, call_id=call.id, destination=target, mode="warm", adapter=object())
    assert returned.id == call.id and existing is not None and existing.id == consult.id
    call.status = "transferred"
    canonical.transfer_destination = target
    await db.commit()
    returned, absent = await transfer_call(db, context, call_id=call.id, destination=target, mode="blind", adapter=object())
    assert returned.id == call.id and absent is None
