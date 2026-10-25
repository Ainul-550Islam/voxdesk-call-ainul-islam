import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center_v2.errors import PermissionDenied, Validation
from app.contact_center_v2.softphone.signaling import (
    SignalingHub,
    SignalingPeer,
    handle_signaling_message,
)
from app.db.contact_center_v2_models import SoftphoneSession
from app.db.models import User
from tests.contact_center_v2.test_softphone import make_call


def peer(registration: SoftphoneSession, sent: list, *, tenant_id=None) -> SignalingPeer:
    return SignalingPeer(
        tenant_id=tenant_id or registration.tenant_id,
        environment_id=registration.environment_id,
        user_id=registration.user_id,
        session_id=registration.id,
        send=sent.append_async if hasattr(sent, "append_async") else _append(sent),
    )


def _append(sent: list):
    async def send(event):
        sent.append(event)
    return send


async def test_offer_answer_ice_and_dtmf_relay_only_between_attached_call_peers(
    db: AsyncSession,
    cc2_scope,
    agent_a: User,
    softphone_registration: SoftphoneSession,
    available_presence,
):
    call = await make_call(db, cc2_scope, agent_a, softphone_registration)
    hub = SignalingHub()
    first_events, second_events = [], []
    first = peer(softphone_registration, first_events)
    second = peer(softphone_registration, second_events)
    for item in (first, second):
        attached = await handle_signaling_message(
            db, hub, item, softphone_registration,
            {"type": "call.attach", "call_id": str(call.id)},
        )
        assert attached["type"] == "call.attached"
    offer = {
        "type": "webrtc.offer", "call_id": str(call.id),
        "payload": {"description": {"type": "offer", "sdp": "v=0\r\na=setup:actpass\r\n"}},
    }
    acknowledgement = await handle_signaling_message(
        db, hub, first, softphone_registration, offer
    )
    assert acknowledgement["payload"]["delivered"] == 1
    assert second_events[-1]["type"] == "webrtc.offer"
    assert second_events[-1]["payload"]["description"]["type"] == "offer"

    answer = await handle_signaling_message(
        db, hub, second, softphone_registration,
        {"type": "webrtc.answer", "call_id": str(call.id),
         "payload": {"description": {"type": "answer", "sdp": "v=0\r\na=setup:active\r\n"}}},
    )
    assert answer["payload"]["delivered"] == 1
    ice = await handle_signaling_message(
        db, hub, first, softphone_registration,
        {"type": "webrtc.ice", "call_id": str(call.id),
         "payload": {"candidate": {"candidate": "candidate:1 1 UDP 1 192.0.2.1 5000 typ host", "sdpMLineIndex": 0}}},
    )
    assert ice["payload"]["delivered"] == 1
    dtmf = await handle_signaling_message(
        db, hub, first, softphone_registration,
        {"type": "call.dtmf", "call_id": str(call.id), "payload": {"digits": "12#"}},
    )
    assert dtmf["type"] == "signaling.ack"
    assert second_events[-1]["payload"]["digits"] == "12#"
    assert "12#" not in str(dtmf)

    await handle_signaling_message(
        db, hub, second, softphone_registration,
        {"type": "call.detach", "call_id": str(call.id)},
    )
    with pytest.raises(PermissionDenied):
        await handle_signaling_message(db, hub, second, softphone_registration, offer)


async def test_signaling_rejects_wrong_tenant_malformed_sdp_and_unattached_controls(
    db: AsyncSession,
    cc2_scope,
    tenant_b,
    agent_a: User,
    softphone_registration: SoftphoneSession,
    available_presence,
):
    call = await make_call(db, cc2_scope, agent_a, softphone_registration)
    hub = SignalingHub()
    events = []
    foreign = peer(softphone_registration, events, tenant_id=tenant_b.id)
    with pytest.raises(PermissionDenied):
        await handle_signaling_message(
            db, hub, foreign, softphone_registration,
            {"type": "call.attach", "call_id": str(call.id)},
        )
    local = peer(softphone_registration, events)
    with pytest.raises(PermissionDenied):
        await handle_signaling_message(
            db, hub, local, softphone_registration,
            {"type": "call.hold", "call_id": str(call.id), "payload": {}},
        )
    await handle_signaling_message(
        db, hub, local, softphone_registration,
        {"type": "call.attach", "call_id": str(call.id)},
    )
    with pytest.raises(Validation):
        await handle_signaling_message(
            db, hub, local, softphone_registration,
            {"type": "webrtc.answer", "call_id": str(call.id),
             "payload": {"description": {"type": "offer", "sdp": "v=0"}}},
        )
