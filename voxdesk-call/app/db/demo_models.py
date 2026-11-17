"""Demo-call tables (PART 18, P18-03).

Two tables, both keyed by nothing the visitor controls:

``demo_destination_verifications`` — proof that a human controls a number. The
code is stored hashed, is single-use, expires in 15 minutes and is attempt
limited. A demo will not dial a number that is not in this table.
``demo_call_requests`` — one row per attempted demo call, including the consent
text that was on screen when it was made. Rows are also the counter behind the
rate limits and the daily cost cap, so they are the audit trail and the throttle
at the same time.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _now() -> datetime:
    return datetime.now(timezone.utc)


class DemoDestinationVerification(Base):
    __tablename__ = "demo_destination_verifications"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    #: E.164, unique: verifying a number twice is a no-op, not a second identity.
    number: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )


class DemoCallRequest(Base):
    __tablename__ = "demo_call_requests"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    number: Mapped[str] = mapped_column(String(20), nullable=False)
    #: Salted hashes, so the rate limiter can count without a cleartext index.
    number_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    ip_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: The wording actually agreed to, stored verbatim per request.
    consent_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    consent_version: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    max_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    provider_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(400), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False, index=True
    )

    def as_dict(self) -> dict[str, object]:
        from app.services.public_demo_service import mask_destination

        return {
            "id": str(self.id),
            "destination": mask_destination(self.number),
            "status": self.status,
            "max_seconds": int(self.max_seconds),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
