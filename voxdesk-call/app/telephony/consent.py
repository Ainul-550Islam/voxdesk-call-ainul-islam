"""Recording-consent decision.

Categories are configuration. ``all_party`` requires a recorded grant.
``one_party`` does not. ``unspecified`` does not allow recording, because an
unknown rule is not permission. This module does not certify compliance with
any jurisdiction's telephone-recording law.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base
from app.tenancy.isolation import NotFound, ValidationFailed

CATEGORIES = {
    "all_party": True,
    "one_party": False,
    "unspecified": None,
}
STATES = frozenset({"unknown", "granted", "denied", "not_required"})


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RecordingConsent(Base):
    __tablename__ = "recording_consents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    call_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("calls.id", ondelete="CASCADE"), nullable=False, index=True
    )
    jurisdiction: Mapped[str] = mapped_column(String(32), nullable=False)
    requirement: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    source: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )

    def as_dict(self) -> dict:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "call_id": str(self.call_id),
            "jurisdiction": self.jurisdiction,
            "requirement": self.requirement,
            "state": self.state,
            "source": self.source,
            "recorded_at": self.recorded_at.isoformat() if self.recorded_at else None,
            "legal_certification": False,
        }


@dataclass(frozen=True)
class ConsentDecision:
    allowed: bool
    reason: str
    required: bool | None

    def as_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "required": self.required,
            "legal_certification": False,
        }


def evaluate(category: str, state: str) -> ConsentDecision:
    key = (category or "").strip().lower()
    if key not in CATEGORIES:
        raise ValidationFailed("Unknown consent category")
    current = (state or "unknown").strip().lower()
    if current not in STATES:
        raise ValidationFailed("Unknown consent state")
    required = CATEGORIES[key]
    if required is None:
        return ConsentDecision(False, "jurisdiction_unspecified", None)
    if not required:
        return ConsentDecision(True, "consent_not_required", False)
    if current == "granted":
        return ConsentDecision(True, "granted", True)
    return ConsentDecision(False, "consent_missing", True)


async def record(
    session,
    *,
    tenant_id: uuid.UUID,
    call_id: uuid.UUID,
    jurisdiction: str,
    state: str,
    source: str,
) -> RecordingConsent:
    decision = evaluate(jurisdiction, state)
    requirement = (
        "unspecified"
        if decision.required is None
        else ("required" if decision.required else "not_required")
    )
    row = RecordingConsent(
        tenant_id=tenant_id,
        call_id=call_id,
        jurisdiction=jurisdiction.strip().lower()[:32],
        requirement=requirement,
        state=state.strip().lower()[:16],
        source=(source or "")[:64],
    )
    session.add(row)
    await session.flush()
    return row


async def get_owned(session, tenant_id: uuid.UUID, consent_id: uuid.UUID) -> RecordingConsent:
    row = await session.get(RecordingConsent, consent_id)
    if row is None or row.tenant_id != tenant_id:
        raise NotFound()
    return row
