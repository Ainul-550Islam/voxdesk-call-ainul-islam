from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.escalation.transfer import Escalation


@pytest.mark.asyncio
async def test_escalation_requires_persisted_scoped_transfer_context():
    with pytest.raises(ValueError, match="persisted call, tenant, and database session"):
        await Escalation().transfer("call-1", "caller requested a human")


@pytest.mark.asyncio
async def test_escalation_delegates_to_authoritative_transfer_service(monkeypatch):
    import app.escalation.transfer as facade

    session = object()
    tenant = SimpleNamespace(id="tenant-1")
    call = SimpleNamespace(id="call-1")
    result = SimpleNamespace(
        as_tool_result=lambda: {
            "ok": False,
            "outcome": "TRANSFER_FAILED",
            "message": "I can take a message and have someone call you back.",
        }
    )
    request_transfer = AsyncMock(return_value=result)
    monkeypatch.setattr(facade, "request_transfer", request_transfer)

    response = await Escalation().transfer(
        "call-1", "caller requested a human", session=session, tenant=tenant, call=call
    )

    request_transfer.assert_awaited_once_with(
        session, tenant, call, reason="caller requested a human"
    )
    assert response["ok"] is False
    assert response["outcome"] == "TRANSFER_FAILED"


@pytest.mark.asyncio
async def test_escalation_rejects_call_id_mismatch_before_provider_action():
    call = SimpleNamespace(id="another-call")
    with pytest.raises(ValueError, match="does not match"):
        await Escalation().transfer(
            "call-1", "caller requested a human", session=object(), tenant=object(), call=call
        )
