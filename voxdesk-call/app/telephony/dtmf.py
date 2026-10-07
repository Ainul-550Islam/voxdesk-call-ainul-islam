"""
app/telephony/dtmf.py
DTMF digit validation, buffer accumulation, IVR route matching, and barge-in handling.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.telephony_models import TelephonyCallSession
from app.telephony.call_session import append_runtime_event, append_transcript_turn
from app.telephony.enums import (
    TERMINAL_CALL_STATES,
    InternalTelephonyEventType,
    TelephonyCallState,
)
from app.telephony.exceptions import CallStateTransitionError, DtmfError
from app.telephony.media_gateway import media_gateway_manager
from app.telephony.schemas import CallDtmfResponse

_VALID_DTMF_RE = re.compile(r"^[0-9*#A-Da-d]+$")

DEFAULT_IVR_ROUTES: dict[str, dict[str, Any]] = {
    "1": {"action": "route_department", "department": "sales", "label": "Sales Queue"},
    "2": {"action": "route_department", "department": "support", "label": "Support Queue"},
    "3": {"action": "route_department", "department": "billing", "label": "Billing Queue"},
    "0": {"action": "transfer_operator", "department": "operator", "label": "Human Operator"},
    "9": {"action": "repeat_menu", "department": "ivr", "label": "Repeat Menu"},
}


def validate_dtmf_digits(digits: str) -> str:
    cleaned = (digits or "").strip().upper()
    if not cleaned:
        raise DtmfError("DTMF digits cannot be empty.", detail={"digits": digits})
    if len(cleaned) > 32:
        raise DtmfError(
            "DTMF sequence exceeds maximum length of 32 characters.",
            detail={"digits": digits, "length": len(cleaned)},
        )
    if not _VALID_DTMF_RE.match(cleaned):
        raise DtmfError(
            f"Invalid DTMF characters in {digits!r}. Allowed: 0-9, *, #, A-D.",
            detail={"digits": digits},
        )
    return cleaned


def match_ivr_route(
    digits: str,
    *,
    custom_routes: dict[str, Any] | None = None,
    current_buffer: str = "",
) -> dict[str, Any] | None:
    routes = dict(DEFAULT_IVR_ROUTES)
    if custom_routes and isinstance(custom_routes, dict):
        for k, v in custom_routes.items():
            if isinstance(v, dict):
                routes[str(k).strip().upper()] = v
            else:
                routes[str(k).strip().upper()] = {"action": "custom", "target": str(v)}

    stripped_key = digits.rstrip("#")
    if digits in routes:
        return {"matched_key": digits, **routes[digits]}
    if stripped_key and stripped_key in routes:
        return {"matched_key": stripped_key, **routes[stripped_key]}
    buf_key = current_buffer.rstrip("#")
    if buf_key and buf_key in routes:
        return {"matched_key": buf_key, **routes[buf_key]}
    return None


async def process_call_dtmf(
    session: AsyncSession,
    *,
    call_session: TelephonyCallSession,
    digits: str,
    source: str = "caller",
) -> CallDtmfResponse:
    """Validate and apply DTMF input to an active call session."""
    state_enum = TelephonyCallState(call_session.status)
    if state_enum in TERMINAL_CALL_STATES:
        raise CallStateTransitionError(
            f"Cannot process DTMF on call in terminal state {state_enum.value}.",
            detail={"call_id": str(call_session.id), "status": state_enum.value},
        )

    normalized_digits = validate_dtmf_digits(digits)
    now = datetime.utcnow()

    # Trigger barge-in if media session is currently speaking
    barge_in_triggered = False
    media_sess = media_gateway_manager.get_session(call_session.id)
    if media_sess is not None:
        barge_res = media_gateway_manager.trigger_barge_in(
            call_session.id, reason=f"dtmf_{normalized_digits}"
        )
        barge_in_triggered = bool(barge_res.get("barge_in_triggered"))

    new_buffer = ((call_session.dtmf_buffer or "") + normalized_digits)[-64:]
    call_session.dtmf_buffer = new_buffer

    custom_ivr = (call_session.metadata_json or {}).get("ivr_routes")
    matched = match_ivr_route(
        normalized_digits,
        custom_routes=custom_ivr if isinstance(custom_ivr, dict) else None,
        current_buffer=new_buffer,
    )

    dtmf_event = {
        "digits": normalized_digits,
        "source": source,
        "buffer_after": new_buffer,
        "matched_route": matched,
        "barge_in_triggered": barge_in_triggered,
        "timestamp": now.isoformat(),
    }
    dtmf_list = list(call_session.dtmf_events or [])
    dtmf_list.append(dtmf_event)
    call_session.dtmf_events = dtmf_list

    append_runtime_event(
        call_session,
        event_type=InternalTelephonyEventType.CALL_DTMF,
        detail=dtmf_event,
        occurred_at=now,
    )
    append_transcript_turn(
        call_session,
        role="dtmf",
        content=f"[DTMF: {normalized_digits}]",
        metadata={"matched_route": matched, "source": source},
        occurred_at=now,
    )
    call_session.updated_at = now
    await session.flush()

    return CallDtmfResponse(
        call_id=call_session.id,
        accepted_digits=normalized_digits,
        dtmf_buffer=new_buffer,
        matched_route=matched,
        barge_in_triggered=barge_in_triggered,
        status=call_session.status,
    )
