import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center_v2.softphone.media_bridge import (
    conference_media,
    customer_context,
    media_handoff,
)
from app.contact_center_v2.softphone.service import record_agent_leg
from app.db.contact_center_v2_models import SoftphoneSession
from app.db.models import User
from tests.contact_center_v2.test_softphone import make_call, make_connected_call


async def test_agent_media_bridge_requires_matching_signed_client_identity(
    db: AsyncSession,
    cc2_scope,
    agent_a: User,
    softphone_registration: SoftphoneSession,
    available_presence,
):
    call = await make_call(db, cc2_scope, agent_a, softphone_registration)
    leg = await record_agent_leg(
        db,
        call_id=call.id,
        softphone_session_id=softphone_registration.id,
        provider_call_id=f"CA-AGENT-{uuid.uuid4().hex[:16]}",
        from_uri=f"client:{softphone_registration.client_identity}",
    )
    replay = await record_agent_leg(
        db,
        call_id=call.id,
        softphone_session_id=softphone_registration.id,
        provider_call_id=leg.provider_call_id,
        from_uri=f"client:{softphone_registration.client_identity}",
    )
    assert replay.id == leg.id
    assert call.status == "in_progress"
    xml = conference_media(call, customer=False)
    assert call.conference_name in xml
    assert "<Conference" in xml
    assert 'record="do-not-record"' in xml
    call.status = "completed"
    assert "This call cannot be connected." in conference_media(call, customer=False)


async def test_media_handoff_redacts_briefing_and_bounds_customer_history(
    db: AsyncSession,
    cc2_scope,
    agent_a: User,
    softphone_registration: SoftphoneSession,
    available_presence,
    lead_factory,
):
    lead = await lead_factory()
    call, canonical = await make_connected_call(
        db, cc2_scope, agent_a, softphone_registration, lead
    )
    canonical.transfer_context = {
        "warm_transfer": {
            "briefing_text": f"Customer {lead.phone} needs a refund; email person@example.com",
            "status": "briefing_played",
            "human_briefed": True,
            "ai_detached": True,
            "target_twiml": "<Dial>secret</Dial>",
        },
        "crm_context": {"account_id": "account-1", "raw_transcript": "do not expose"},
    }
    await db.commit()
    handoff = media_handoff(call, customer=True, canonical_call=canonical)
    briefing = handoff["warm_transfer_briefing"]
    assert briefing["human_briefed"] is True
    assert briefing["ai_detached"] is True
    assert lead.phone not in briefing["text_redacted"]
    assert "person@example.com" not in briefing["text_redacted"]
    assert "target_twiml" not in briefing
    assert call.conference_name in handoff["twiml"]
    context = await customer_context(db, call, canonical)
    assert context["lead"]["id"] == str(lead.id)
    assert context["crm"] == {"account_id": "account-1"}
    assert len(context["prior_calls"]) <= 5
