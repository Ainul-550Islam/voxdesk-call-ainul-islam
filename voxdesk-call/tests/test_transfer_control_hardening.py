"""Regression coverage for the hardened legacy transfer-control surface."""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.transfer_control_routes import (
    TransferRequest,
    _redact_credential_material,
    _validate_context_data,
)
from app.db.models import CallStatus, TransferState
from app.environments.resource_scope import resolve_scope
from app.telephony.transfer import transfer_twiml
from app.telephony.transfer_service import _safe_whisper_reason
from tests.conftest import auth_headers, make_call


def test_transfer_request_accepts_only_valid_e164_destinations():
    request = TransferRequest(destination="+1 (415) 555-2671")
    assert request.destination == "+14155552671"

    with pytest.raises(ValueError, match="valid E.164"):
        TransferRequest(destination="sip:agent@example.com")


def test_warm_context_rejects_secret_named_fields():
    with pytest.raises(HTTPException) as caught:
        _validate_context_data({"account": {"refresh_token": "not-for-context"}})
    assert caught.value.status_code == 422


def test_warm_context_rejects_credential_material_in_text_values():
    with pytest.raises(HTTPException) as caught:
        _validate_context_data({"notes": "authorization: Bearer abcdefghijklmnop"})
    assert caught.value.status_code == 422


def test_existing_transfer_text_is_redacted_before_response():
    assert _redact_credential_material("Authorization: Bearer abcdefghijklmnop") == (
        "[CREDENTIAL REDACTED]"
    )


def test_whisper_sanitizer_removes_credential_assignments_and_phone_digits():
    sanitized = _safe_whisper_reason(
        "Password=hunter2; call +1 415 555 2671 about <urgent>"
    )
    assert "hunter2" not in sanitized
    assert "+1 415 555 2671" not in sanitized
    assert "<urgent>" not in sanitized
    assert "CREDENTIAL REDACTED" in sanitized


def test_transfer_twiml_uses_requested_timeout_and_bridge_behavior():
    twiml = transfer_twiml(
        "+14155552671",
        timeout=60,
        answer_on_bridge=False,
    )
    assert 'timeout="60"' in twiml
    assert 'answerOnBridge="false"' in twiml


# corrected: endpoint implemented in gap-fill — send_whisper and bulk_transfer are real
# CallControlService operations while complete_transfer / fail_transfer refuse client-authored
# outcomes with 403 and are omitted from OpenAPI.
@pytest.mark.asyncio
async def test_client_authored_transfer_outcomes_and_unverified_bulk_fail_closed(
    client, db, tenant_a, owner_a
):
    scope = await resolve_scope(db, tenant_id=tenant_a.id, user_id=owner_a.id)
    call = await make_call(
        db,
        tenant_a,
        environment_id=scope.id,
        status=CallStatus.IN_PROGRESS,
        transfer_state=TransferState.DIALING,
    )
    call_id = call.id

    headers = await auth_headers(client, owner_a)

    comp = await client.post(f"/api/calls/{call_id}/transfer/complete", headers=headers)
    assert comp.status_code == 403
    assert comp.json()["detail"]["code"] == "client_transfer_completion_forbidden"

    fail = await client.post(
        f"/api/calls/{call_id}/transfer/fail",
        params={"reason": "busy"},
        headers=headers,
    )
    assert fail.status_code == 403
    assert fail.json()["detail"]["code"] == "client_transfer_failure_forbidden"

    whisper = await client.post(
        f"/api/calls/{call_id}/transfer/whisper",
        json={"message": "Customer is asking about billing"},
        headers=headers,
    )
    assert whisper.status_code != 501

    bulk = await client.post(
        "/api/calls/bulk-transfer",
        json={"items": [{"call_id": str(call_id), "destination": "+14155552671"}]},
        headers=headers,
    )
    assert bulk.status_code != 501

