import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center_v2.errors import PermissionDenied, Validation
from app.contact_center_v2.softphone.signaling import (
    consume_websocket_ticket,
    encode_event,
    event_envelope,
    issue_websocket_ticket,
    ticket_matches,
)
from app.db.contact_center_v2_models import SoftphoneSession
from tests.contact_center_v2.conftest import Cc2Scope


async def test_signaling_ticket_is_secret_free_single_use_and_tenant_bound(
    db: AsyncSession, cc2_scope: Cc2Scope, softphone_registration: SoftphoneSession,
):
    ticket = await issue_websocket_ticket(db, softphone_registration)
    assert ticket_matches(softphone_registration, ticket)
    assert softphone_registration.ws_ticket_hash != ticket

    consumed = await consume_websocket_ticket(
        db, ticket, tenant_id=cc2_scope.tenant.id
    )
    assert consumed.id == softphone_registration.id
    assert consumed.ws_ticket_hash == ""
    with pytest.raises(PermissionDenied):
        await consume_websocket_ticket(db, ticket, tenant_id=cc2_scope.tenant.id)


def test_signaling_envelope_strips_credentials_and_rejects_unknown_events():
    event = event_envelope(
        event_type="call.updated",
        payload={"call_id": "allowed", "access_token": "secret", "raw_transcript": "private"},
    )
    assert event["payload"] == {"call_id": "allowed"}
    assert "secret" not in encode_event(event)
    with pytest.raises(Validation, match="Unsupported"):
        event_envelope(event_type="provider.debug", payload={})
