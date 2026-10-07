"""
app/telephony/idempotency.py
Durable idempotency, replay-window validation, and concurrency locks for
telephony webhooks, outbound call creation, transfers, and usage finalization.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.telephony_models import TelephonyProviderEvent
from app.telephony.exceptions import ProviderWebhookVerificationError

_EVENT_LOCKS: dict[str, asyncio.Lock] = {}
_DEFAULT_REPLAY_TOLERANCE_SECONDS = 300


def get_idempotency_lock(scope_key: str) -> asyncio.Lock:
    lock = _EVENT_LOCKS.get(scope_key)
    if lock is None:
        if len(_EVENT_LOCKS) > 4096:
            _EVENT_LOCKS.clear()
        lock = asyncio.Lock()
        _EVENT_LOCKS[scope_key] = lock
    return lock


def compute_payload_hash(payload: dict[str, Any] | bytes | str) -> str:
    if isinstance(payload, bytes):
        raw = payload
    elif isinstance(payload, str):
        raw = payload.encode("utf-8")
    else:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        raw = canonical.encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def derive_idempotency_key(
    *,
    provider: str,
    event_type: str,
    provider_call_id: str,
    explicit_event_id: str | None = None,
    extra_discriminator: str | None = None,
) -> str:
    if explicit_event_id and explicit_event_id.strip():
        return explicit_event_id.strip()[:128]
    material = "|".join(
        [
            provider.upper().strip(),
            event_type.lower().strip(),
            provider_call_id.strip(),
            (extra_discriminator or "").strip(),
        ]
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
    return f"evt_{provider.lower()}_{digest}"


def verify_replay_timestamp(
    timestamp_value: str | int | float | None,
    *,
    tolerance_seconds: int = _DEFAULT_REPLAY_TOLERANCE_SECONDS,
    now_epoch: float | None = None,
) -> None:
    """Validate that a webhook timestamp falls within the replay window."""
    if timestamp_value is None or timestamp_value == "":
        return
    now = now_epoch if now_epoch is not None else time.time()
    try:
        if isinstance(timestamp_value, (int, float)):
            ts = float(timestamp_value)
        else:
            raw = str(timestamp_value).strip()
            if raw.isdigit():
                ts = float(raw)
            else:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                ts = parsed.timestamp()
        # Convert milliseconds if needed
        if ts > 1e12:
            ts = ts / 1000.0
    except Exception as exc:
        raise ProviderWebhookVerificationError(
            f"Malformed webhook timestamp: {timestamp_value!r}",
            detail={"timestamp": str(timestamp_value)},
        ) from exc

    drift = abs(now - ts)
    if drift > tolerance_seconds:
        raise ProviderWebhookVerificationError(
            f"Webhook timestamp outside replay tolerance ({int(drift)}s > {tolerance_seconds}s)",
            detail={"drift_seconds": int(drift), "tolerance_seconds": tolerance_seconds},
        )


@dataclass
class IdempotentEventClaim:
    event_row: TelephonyProviderEvent
    is_duplicate: bool


async def claim_provider_event(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    environment_id: UUID | None,
    provider: str,
    provider_event_id: str,
    event_type: str,
    provider_call_id: str | None,
    normalized_payload: dict[str, Any],
    call_id: UUID | None = None,
) -> IdempotentEventClaim:
    """Atomically claim a provider webhook event for a tenant, detecting duplicates."""
    provider_upper = provider.upper().strip()
    event_key = provider_event_id.strip()[:128]
    lock_key = f"{tenant_id}:{provider_upper}:{event_key}"
    payload_hash = compute_payload_hash(normalized_payload)

    async with get_idempotency_lock(lock_key):
        existing_stmt = select(TelephonyProviderEvent).where(
            TelephonyProviderEvent.tenant_id == tenant_id,
            TelephonyProviderEvent.provider == provider_upper,
            TelephonyProviderEvent.provider_event_id == event_key,
        )
        existing = (await session.execute(existing_stmt)).scalar_one_or_none()
        if existing is not None:
            existing.duplicate_count = int(existing.duplicate_count or 0) + 1
            await session.flush()
            return IdempotentEventClaim(event_row=existing, is_duplicate=True)

        row = TelephonyProviderEvent(
            tenant_id=tenant_id,
            environment_id=environment_id,
            provider=provider_upper,
            provider_event_id=event_key,
            event_type=event_type,
            call_id=call_id,
            provider_call_id=provider_call_id,
            payload_hash=payload_hash,
            received_at=datetime.utcnow(),
            processing_status="processing",
            duplicate_count=0,
            normalized_payload=normalized_payload,
        )
        try:
            async with session.begin_nested():
                session.add(row)
                await session.flush()
            return IdempotentEventClaim(event_row=row, is_duplicate=False)
        except IntegrityError:
            existing = (await session.execute(existing_stmt)).scalar_one()
            existing.duplicate_count = int(existing.duplicate_count or 0) + 1
            await session.flush()
            return IdempotentEventClaim(event_row=existing, is_duplicate=True)
