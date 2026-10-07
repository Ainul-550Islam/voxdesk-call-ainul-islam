"""Durable idempotency receipts for synchronous API side effects.

The client-supplied key is never stored. A unique database constraint
arbitrates concurrent requests across workers. Callers should commit the
``in_progress`` receipt before performing a non-transactional external side
effect, then update the receipt and business row in one transaction. An
ambiguous crash leaves an in-progress receipt that rejects retries rather than
blindly repeating a potentially completed side effect.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Environment, RequestIdempotencyReceipt

_OPERATION_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,63}$")
MAX_KEY_LENGTH = 256


class IdempotencyError(RuntimeError):
    """Base class for stable idempotency policy outcomes."""

    code = "idempotency_error"


class IdempotencyConflict(IdempotencyError):
    """The same key was reused for a different canonical request."""

    code = "idempotency_conflict"


class IdempotencyInProgress(IdempotencyError):
    """A prior request owns the key and has not reached a terminal state."""

    code = "idempotency_in_progress"


class IdempotencyPreviousFailure(IdempotencyError):
    """The original request failed; a new key is required for a new attempt."""

    code = "idempotency_previous_failure"


class IdempotencyScopeError(IdempotencyError):
    """The requested tenant/environment scope is invalid."""

    code = "idempotency_scope_invalid"


@dataclass(frozen=True)
class RequestClaim:
    receipt: RequestIdempotencyReceipt
    created: bool

    @property
    def replayed(self) -> bool:
        return not self.created and self.receipt.status == "succeeded"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _json_default(item: Any) -> str:
    if isinstance(item, (uuid.UUID, datetime)):
        return str(item)
    raise TypeError(f"unsupported idempotency value type: {type(item).__name__}")


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
            default=_json_default,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ValueError("idempotency request data is not canonical JSON") from exc


def request_digest(request_data: Any) -> str:
    """SHA-256 of canonical request data, excluding credentials and raw keys."""
    return hashlib.sha256(_canonical(request_data)).hexdigest()


def _key_digest(key: str) -> str:
    if not isinstance(key, str):
        raise ValueError("Idempotency-Key must be a string")
    normalized = key.strip()
    if not normalized or len(normalized) > MAX_KEY_LENGTH or any(ord(char) < 33 for char in normalized):
        raise ValueError("Idempotency-Key must be printable and at most 256 characters")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


async def claim_request(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    operation: str,
    key: str,
    request_data: Any,
    environment_id: uuid.UUID | None = None,
) -> RequestClaim:
    """Atomically reserve an idempotency key within a tenant/environment.

    A successful replay returns the stored receipt with ``created=False``.
    Pending and failed duplicates raise stable typed exceptions. The caller
    still owns the transaction and must commit a newly-created receipt before
    external side effects begin.
    """
    if not _OPERATION_RE.fullmatch(operation or ""):
        raise ValueError("operation must be a bounded lowercase identifier")
    tenant_uuid = tenant_id if isinstance(tenant_id, uuid.UUID) else uuid.UUID(str(tenant_id))
    environment_uuid = (
        environment_id
        if isinstance(environment_id, uuid.UUID)
        else uuid.UUID(str(environment_id)) if environment_id is not None else None
    )
    if environment_uuid is not None:
        exists = await session.scalar(
            select(Environment.id).where(
                Environment.id == environment_uuid,
                Environment.tenant_id == tenant_uuid,
            )
        )
        if exists is None:
            raise IdempotencyScopeError("environment does not belong to tenant")

    scope = str(environment_uuid) if environment_uuid else "tenant"
    key_hash = _key_digest(key)
    body_hash = request_digest(request_data)
    row = RequestIdempotencyReceipt(
        id=uuid.uuid4(),
        tenant_id=tenant_uuid,
        environment_id=environment_uuid,
        environment_scope=scope,
        operation=operation,
        key_digest=key_hash,
        request_digest=body_hash,
        status="in_progress",
    )
    # SQLite's legacy transaction mode does not BEGIN for SELECT. Releasing
    # its first SAVEPOINT would otherwise commit a receipt outside the caller's
    # business transaction. PostgreSQL already begins on the first statement.
    connection = await session.connection()
    if connection.dialect.name == "sqlite":
        raw = await connection.get_raw_connection()
        if not raw.driver_connection.in_transaction:
            await connection.exec_driver_sql("BEGIN")
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
        return RequestClaim(row, True)
    except IntegrityError:
        existing = await session.scalar(
            select(RequestIdempotencyReceipt).where(
                RequestIdempotencyReceipt.tenant_id == tenant_uuid,
                RequestIdempotencyReceipt.environment_scope == scope,
                RequestIdempotencyReceipt.operation == operation,
                RequestIdempotencyReceipt.key_digest == key_hash,
            )
        )
        if existing is None:
            raise
        if not hmac.compare_digest(existing.request_digest, body_hash):
            raise IdempotencyConflict("Idempotency-Key was already used for a different request") from None
        if existing.status == "succeeded":
            return RequestClaim(existing, False)
        if existing.status == "failed":
            raise IdempotencyPreviousFailure("The original idempotent request failed") from None
        raise IdempotencyInProgress("The original idempotent request is still in progress") from None


async def complete_request(
    session: AsyncSession,
    receipt: RequestIdempotencyReceipt,
    *,
    resource_type: str,
    resource_id: str | uuid.UUID,
) -> RequestIdempotencyReceipt:
    """Mark the receipt successful in the same transaction as the resource."""
    if receipt.status != "in_progress":
        if receipt.status == "succeeded":
            return receipt
        raise IdempotencyPreviousFailure("A failed idempotency receipt cannot be completed")
    if not resource_type or len(resource_type) > 64:
        raise ValueError("resource_type must be between 1 and 64 characters")
    identifier = str(resource_id)
    if not identifier or len(identifier) > 160 or any(ord(char) < 32 for char in identifier):
        raise ValueError("resource_id must be a printable identifier of at most 160 characters")
    receipt.resource_type = resource_type
    receipt.resource_id = identifier
    receipt.status = "succeeded"
    receipt.error_category = ""
    receipt.completed_at = _now()
    await session.flush()
    return receipt


async def fail_request(
    session: AsyncSession,
    receipt: RequestIdempotencyReceipt,
    *,
    category: str,
) -> RequestIdempotencyReceipt:
    """Persist a non-retryable/ambiguous failure without storing raw exception text."""
    if receipt.status == "succeeded":
        return receipt
    receipt.status = "failed"
    receipt.resource_type = ""
    receipt.resource_id = ""
    receipt.error_category = re.sub(r"[^a-z0-9_.-]", "_", category.lower())[:64]
    receipt.completed_at = _now()
    await session.flush()
    return receipt


__all__ = [
    "IdempotencyConflict",
    "IdempotencyError",
    "IdempotencyInProgress",
    "IdempotencyPreviousFailure",
    "IdempotencyScopeError",
    "RequestClaim",
    "claim_request",
    "complete_request",
    "fail_request",
    "request_digest",
]
