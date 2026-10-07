"""Regression coverage for the hardened legacy transfer-control surface."""
from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from app.api.transfer_control_routes import (
    TransferRequest,
    _redact_credential_material,
    _validate_context_data,
    bulk_transfer,
    complete_transfer,
    fail_transfer,
    send_whisper,
)
from app.telephony.transfer import transfer_twiml
from app.telephony.transfer_service import _safe_whisper_reason


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


@pytest.mark.asyncio
async def test_client_authored_transfer_outcomes_and_unverified_bulk_fail_closed():
    call_id = uuid.uuid4()
    principal = object()
    cases = (
        send_whisper(call_id, "hello", principal),
        bulk_transfer([call_id], "+14155552671", None, principal),
        complete_transfer(call_id, principal),
        fail_transfer(call_id, "busy", principal),
    )
    for operation in cases:
        with pytest.raises(HTTPException) as caught:
            await operation
        assert caught.value.status_code == 501
