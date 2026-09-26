"""Provider state versus local state.

A newer local terminal state is not overwritten by an older provider snapshot.
Call transitions go through the existing call-state machine. Recording and
number transitions go through their own machines. The repair is idempotent.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.db.models import Call
from app.telephony import call_state
from app.telephony.call_events import accept
from app.telephony.number_provisioning import PhoneNumber
from app.telephony.recording import CallRecording, apply_provider_event, transition


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _older(observed: datetime | None, local: datetime | None) -> bool:
    left = _as_utc(observed)
    right = _as_utc(local)
    return left is not None and right is not None and left < right


async def reconcile_call(session, call: Call, snapshot: dict) -> dict:
    observed = snapshot.get("observed_at")
    if call_state.is_terminal(call.status) and _older(observed, call.ended_at):
        return {"outcome": "stale_ignored", "status": call.status.value}
    result = call_state.apply_provider_status(
        call,
        snapshot.get("status"),
        duration_seconds=snapshot.get("duration_seconds"),
        source="reconcile",
    )
    return {
        "outcome": "applied" if result.applied else result.reason,
        "status": call.status.value,
    }


async def reconcile_recording(session, row: CallRecording, snapshot: dict) -> dict:
    observed = snapshot.get("observed_at")
    if row.state in {"ready", "deleted"} and _older(observed, row.completed_at or row.updated_at):
        return {"outcome": "stale_ignored", "state": row.state}
    outcome = transition(row, str(snapshot.get("state") or ""), observed_at=observed)
    await session.flush()
    return {"outcome": outcome, "state": row.state}


async def reconcile_number(session, row: PhoneNumber, snapshot: dict) -> dict:
    """Never move a number to another tenant. A stale assigned snapshot does not revive a release."""
    if snapshot.get("tenant_id") and str(snapshot.get("tenant_id")) != str(row.tenant_id):
        return {"outcome": "ownership_ignored", "status": row.status}
    observed = snapshot.get("observed_at")
    remote = str(snapshot.get("status") or "")
    if (
        row.status == "released"
        and remote in {"assigned", "provisioned", "reserved"}
        and _older(observed, row.released_at or row.updated_at)
    ):
        return {"outcome": "stale_ignored", "status": row.status}
    if remote == row.status:
        return {"outcome": "duplicate", "status": row.status}
    if remote == "released" and row.status != "released" and not _older(observed, row.updated_at):
        row.status = "released"
        row.active_key = None
        row.released_at = _now()
        row.updated_at = row.released_at
        await session.flush()
        return {"outcome": "applied", "status": row.status}
    return {"outcome": "unchanged", "status": row.status}


async def ingest_callback(session, envelope: dict) -> dict:
    return await accept(session, envelope, _apply)


async def _apply(session, event, envelope: dict) -> tuple[str, bool]:
    kind = event.event_type
    if kind == "call.status":
        call = await _call(session, event)
        if call is None:
            return "missing", False
        result = await reconcile_call(session, call, envelope)
        return result["outcome"], result["outcome"] == "applied"
    if kind == "recording.status":
        if event.tenant_id is None:
            return "missing", False
        row, outcome = await apply_provider_event(
            session,
            tenant_id=event.tenant_id,
            provider=event.provider,
            external_id=str(envelope.get("external_recording_id") or event.external_id),
            target_state=str(envelope.get("state") or ""),
            call_id=envelope.get("call_id"),
            observed_at=envelope.get("observed_at"),
        )
        return outcome, outcome == "applied" and row is not None
    if kind == "number.status":
        row = await _number(session, event)
        if row is None:
            return "missing", False
        result = await reconcile_number(session, row, envelope)
        return result["outcome"], result["outcome"] == "applied"
    return "ignored", False


async def _call(session, event):
    if not event.external_id:
        return None
    filters = [Call.call_sid == event.external_id]
    if event.tenant_id is not None:
        filters.append(Call.tenant_id == event.tenant_id)
    return (await session.execute(select(Call).where(*filters))).scalar_one_or_none()


async def _number(session, event):
    if event.tenant_id is None or not event.external_id:
        return None
    return (
        await session.execute(
            select(PhoneNumber).where(
                PhoneNumber.tenant_id == event.tenant_id,
                PhoneNumber.external_id == event.external_id,
            )
        )
    ).scalar_one_or_none()


def _now() -> datetime:
    return datetime.now(timezone.utc)
