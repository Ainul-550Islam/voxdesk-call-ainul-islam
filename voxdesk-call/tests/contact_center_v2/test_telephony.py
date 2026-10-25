import uuid

import pytest
from starlette.requests import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center_v2.dialer import execute_contact
from app.contact_center_v2.policy import prepare_contacts
from app.contact_center_v2.telephony import handle_outbound_answer, handle_voice
from app.db.contact_center_v2_models import DialerAttempt, DialerContact, SoftphoneCall, SoftphoneSession
from app.db.models import Call, User
from tests.contact_center_v2.conftest import Cc2Scope
from tests.contact_center_v2.test_softphone import make_call


def form_request(query: str, form: str) -> Request:
    delivered = False
    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": form.encode(), "more_body": False}
        return {"type": "http.disconnect"}
    return Request({
        "type": "http",
        "method": "POST",
        "path": "/telephony/voice",
        "query_string": query.encode(),
        "headers": [(b"content-type", b"application/x-www-form-urlencoded")],
    }, receive)


async def test_twiml_app_agent_connect_uses_signed_form_parameters(
    db: AsyncSession,
    cc2_scope: Cc2Scope,
    agent_a: User,
    softphone_registration: SoftphoneSession,
    available_presence,
):
    call = await make_call(db, cc2_scope, agent_a, softphone_registration)
    request = form_request(
        "cc2=softphone-agent",
        f"call_id={call.id}&session_id={softphone_registration.id}&To=client%3Aconference",
    )
    response = await handle_voice(
        request,
        db,
        call_sid="CA-AGENT-FORM",
        from_uri=f"client:{softphone_registration.client_identity}",
    )
    assert response is not None
    assert response.status_code == 200
    assert b"<Conference" in response.body
    assert call.status == "in_progress"


async def test_non_cc2_voice_remains_on_canonical_inbound_path(db: AsyncSession):
    request = form_request("", "")
    assert await handle_voice(request, db, call_sid="CA-INBOUND", from_uri="+15550000000") is None


@pytest.mark.parametrize("answered_by", ["human", "machine_start"])
async def test_ai_blast_answer_reconciles_cc2_then_uses_canonical_ai_path(
    answered_by: str,
    db: AsyncSession,
    cc2_scope: Cc2Scope,
    policy_factory,
    lead_factory,
    monkeypatch: pytest.MonkeyPatch,
):
    await lead_factory()
    policy = await policy_factory(mode="ai_agent_blast")
    policy.status = "running"
    await prepare_contacts(db, policy)
    contact = (await db.execute(select(DialerContact))).scalar_one()
    provider_sid = f"CA{uuid.uuid4().hex[:30]}"

    async def acknowledged(session, tenant, campaign, dialed_lead, **kwargs):
        call = Call(
            tenant_id=tenant.id, environment_id=campaign.environment_id,
            call_sid=provider_sid, from_number=kwargs["caller_id_number"],
            to_number=dialed_lead.phone, lead_id=dialed_lead.id,
        )
        session.add(call)
        await session.commit()
        return {"ok": True, "call_sid": provider_sid, "call_id": str(call.id)}

    monkeypatch.setattr("app.telephony.outbound.place_call", acknowledged)
    attempt = await execute_contact(db, policy=policy, contact=contact)
    assert attempt is not None
    response = await handle_outbound_answer(
        form_request("", ""), db, campaign_id=str(cc2_scope.campaign.id),
        call_sid=provider_sid, answered_by=answered_by,
    )
    assert response is None
    await db.refresh(attempt)
    assert attempt.answered_by == answered_by
    assert attempt.answered_at is not None
    if answered_by == "human":
        assert attempt.outcome == "answered"
        assert attempt.agent_connected_at is not None
    else:
        assert attempt.outcome == "machine"
        assert attempt.agent_connected_at is None
    assert not (await db.execute(select(SoftphoneCall))).scalars().all()
    assert (await db.execute(select(DialerAttempt))).scalar_one().id == attempt.id
