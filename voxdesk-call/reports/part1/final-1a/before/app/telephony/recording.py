"""Recording lifecycle.

States move forward only. A duplicate provider callback does not create a
second row and does not regress ``ready`` to ``recording``. Access requires
the tenant and ``recording:read`` on every call. The provider URL on ``Call``
is not returned from here.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    select,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column

from app.auth.permissions import Permission
from app.auth.rbac import has_permission
from app.db.models import AuditAction, Base, Call, Tenant, User, UserRole
from app.telephony.media_storage import delete_bytes, issue, safe_reference, verify
from app.tenancy.isolation import Forbidden, NotFound

STATES = frozenset(
    {"requested", "recording", "processing", "ready", "failed", "deletion_pending", "deleted"}
)
_TRANSITIONS = {
    "requested": frozenset({"recording", "failed"}),
    "recording": frozenset({"processing", "failed", "ready"}),
    "processing": frozenset({"ready", "failed"}),
    "ready": frozenset({"deletion_pending"}),
    "failed": frozenset({"deletion_pending"}),
    "deletion_pending": frozenset({"deleted"}),
    "deleted": frozenset(),
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CallRecording(Base):
    __tablename__ = "call_recordings"
    __table_args__ = (
        UniqueConstraint("provider", "external_guard", name="uq_call_recordings_external"),
        CheckConstraint(
            "state IN ('requested', 'recording', 'processing', 'ready', 'failed', 'deletion_pending', 'deleted')",
            name="ck_call_recordings_state",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    call_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("calls.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    external_recording_id: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    external_guard: Mapped[str | None] = mapped_column(String(96), nullable=True)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="requested")
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    content_type: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    checksum: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    storage_key: Mapped[str] = mapped_column(String(240), default="", nullable=False)
    error_class: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retention_deadline: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def as_dict(self) -> dict:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "call_id": str(self.call_id),
            "provider": self.provider,
            "external_recording_id": self.external_recording_id,
            "state": self.state,
            "duration_seconds": self.duration_seconds,
            "content_type": self.content_type,
            "size_bytes": self.size_bytes,
            "checksum": self.checksum or None,
            "has_storage": bool(self.storage_key),
            "error_class": self.error_class,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "deleted_at": self.deleted_at.isoformat() if self.deleted_at else None,
            "retention_deadline": self.retention_deadline.isoformat()
            if self.retention_deadline
            else None,
            "provider_url": None,
        }


def transition(row: CallRecording, target: str, *, observed_at: datetime | None = None) -> str:
    """Move the row or leave it. Same state is a duplicate, not an error."""
    if target not in STATES:
        return "unknown_state"
    if row.state == target:
        return "duplicate"
    if row.state == "deleted":
        return "terminal"
    if (
        observed_at is not None
        and row.updated_at is not None
        and _as_utc(observed_at) < _as_utc(row.updated_at)
    ):
        if target in _TRANSITIONS.get(row.state, frozenset()):
            return "stale_ignored"
    if target not in _TRANSITIONS.get(row.state, frozenset()):
        return "illegal"
    row.state = target
    row.updated_at = _now()
    if target == "ready":
        row.completed_at = row.completed_at or row.updated_at
    if target == "failed":
        row.error_class = row.error_class or "provider_failed"
    if target == "deleted":
        row.deleted_at = row.updated_at
        if row.storage_key:
            delete_bytes(row.storage_key)
            row.storage_key = ""
    return "applied"


async def request_recording(
    session,
    call: Call,
    *,
    provider: str,
    consent_category: str = "unspecified",
    consent_state: str = "unknown",
) -> CallRecording:
    from app.telephony.consent import evaluate
    from app.telephony.recording_policy import deadline, effective

    tenant = await session.get(Tenant, call.tenant_id)
    policy = await effective(session, tenant)
    if not policy["enabled"]:
        raise Forbidden("Recording is disabled for this tenant")
    decision = evaluate(consent_category, consent_state)
    if not decision.allowed:
        raise Forbidden("Recording consent does not allow capture")
    row = CallRecording(
        tenant_id=call.tenant_id,
        call_id=call.id,
        provider=provider,
        state="requested",
        retention_deadline=deadline(policy["retention_days"]),
    )
    session.add(row)
    await session.flush()
    return row


async def apply_provider_event(
    session,
    *,
    tenant_id: uuid.UUID,
    provider: str,
    external_id: str,
    target_state: str,
    call_id: uuid.UUID | None = None,
    observed_at: datetime | None = None,
    duration_seconds: float | None = None,
    content_type: str = "",
    size_bytes: int | None = None,
    checksum: str = "",
) -> tuple[CallRecording | None, str]:
    row = await _by_external(session, provider, external_id) if external_id else None
    if row is None and call_id is not None:
        row = await _latest_for_call(session, tenant_id, call_id)
    if row is None:
        return None, "missing"
    if row.tenant_id != tenant_id:
        return None, "missing"
    outcome = transition(row, target_state, observed_at=observed_at)
    if outcome == "applied":
        if duration_seconds is not None and duration_seconds >= 0:
            row.duration_seconds = duration_seconds
        if content_type:
            row.content_type = content_type[:80]
        if size_bytes is not None and size_bytes >= 0:
            row.size_bytes = size_bytes
        if checksum:
            row.checksum = checksum[:64]
        if external_id and not row.external_recording_id:
            row.external_recording_id = external_id[:80]
            row.external_guard = f"{provider}:{external_id}"[:96]
            try:
                async with session.begin_nested():
                    await session.flush()
            except IntegrityError:
                row.external_guard = None
                return row, "duplicate_external"
    await session.flush()
    return row, outcome


async def authorize_read(
    session,
    *,
    tenant_id: uuid.UUID,
    recording_id: uuid.UUID,
    role: UserRole | None,
    actor_user_id: uuid.UUID | None = None,
) -> CallRecording:
    if actor_user_id is not None:
        actor_tenant_id = await session.scalar(
            select(User.tenant_id).where(User.id == actor_user_id)
        )
        if actor_tenant_id is None:
            raise NotFound()
        if actor_tenant_id != tenant_id:
            # Attribute the denied cross-tenant attempt to the caller's own
            # tenant. Never attach a foreign actor to the target tenant's audit
            # record, and do not query the target recording after this mismatch.
            await _audit(
                session,
                actor_tenant_id,
                actor_user_id,
                "recording_access_denied",
                str(recording_id),
                attempted_tenant_id=tenant_id,
            )
            raise NotFound()

    row = await session.get(CallRecording, recording_id)
    if row is None or row.tenant_id != tenant_id or row.state == "deleted":
        raise NotFound()
    if role is None or not has_permission(role, Permission.RECORDING_READ):
        await _audit(
            session, tenant_id, actor_user_id, "recording_access_denied", str(recording_id)
        )
        raise Forbidden()
    await _audit(session, tenant_id, actor_user_id, "recording_access", str(recording_id))
    return row


def grant_for(row: CallRecording, *, role: UserRole | None, now: int | None = None):
    if role is None or not has_permission(role, Permission.RECORDING_READ):
        raise Forbidden()
    if row.state in {"deleted", "deletion_pending"}:
        raise NotFound()
    return issue(row.tenant_id, row.id, now=now)


def open_grant(
    row: CallRecording, token: str, *, role: UserRole | None, now: int | None = None
) -> dict:
    if role is None or not has_permission(role, Permission.RECORDING_READ):
        raise Forbidden()
    verdict = verify(token, tenant_id=row.tenant_id, recording_id=row.id, now=now)
    if not verdict.allowed:
        return {"allowed": False, "reason": verdict.reason, "url": None}
    reference = safe_reference(row.storage_key)
    return {
        "allowed": True,
        "reason": "valid" if reference else "not_stored",
        "url": None,
        "storage_key": reference,
        "provider_url": None,
    }


async def mark_deletion(session, row: CallRecording, *, held: bool) -> str:
    if held:
        return "legal_hold"
    if row.state == "deleted":
        return "duplicate"
    if row.state != "deletion_pending":
        outcome = transition(row, "deletion_pending")
        if outcome not in {"applied", "duplicate"}:
            return outcome
    return transition(row, "deleted")


async def purge_for_calls(session, call_ids: list[uuid.UUID]) -> dict:
    if not call_ids:
        return {"purged_recordings": 0, "held_calls": []}
    from app.telephony.recording_policy import RecordingPolicy

    rows = (
        (await session.execute(select(CallRecording).where(CallRecording.call_id.in_(call_ids))))
        .scalars()
        .all()
    )
    held_tenants = {
        row.tenant_id
        for row in (
            await session.execute(
                select(RecordingPolicy).where(
                    RecordingPolicy.legal_hold.is_(True),
                    RecordingPolicy.tenant_id.in_(
                        {item.tenant_id for item in rows} or {uuid.uuid4()}
                    ),
                )
            )
        )
        .scalars()
        .all()
    }
    purged = 0
    held_calls = []
    for row in rows:
        if row.tenant_id in held_tenants:
            held_calls.append(row.call_id)
            continue
        if row.state != "deleted":
            row.state = "deletion_pending"
            transition(row, "deleted")
            purged += 1
    await session.flush()
    return {"purged_recordings": purged, "held_calls": held_calls}


async def _by_external(session, provider: str, external_id: str) -> CallRecording | None:
    guard = f"{provider}:{external_id}"
    return (
        await session.execute(
            select(CallRecording).where(
                CallRecording.provider == provider,
                CallRecording.external_guard == guard,
            )
        )
    ).scalar_one_or_none()


async def _latest_for_call(session, tenant_id, call_id) -> CallRecording | None:
    return (
        (
            await session.execute(
                select(CallRecording)
                .where(CallRecording.tenant_id == tenant_id, CallRecording.call_id == call_id)
                .order_by(CallRecording.created_at.desc())
            )
        )
        .scalars()
        .first()
    )


async def _audit(
    session,
    tenant_id,
    actor_user_id,
    operation: str,
    recording_id: str,
    *,
    attempted_tenant_id: uuid.UUID | None = None,
) -> None:
    from app.auth.identity.events import emit

    detail = {"operation": operation, "recording_id": recording_id}
    if attempted_tenant_id is not None:
        detail["attempted_tenant_id"] = str(attempted_tenant_id)
    await emit(
        session,
        AuditAction.RESOURCE_EXPORTED,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        detail=detail,
        commit=False,
    )
    await session.flush()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
