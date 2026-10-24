"""Durable records for live PSTN proof runs.

One row per attempt to prove the agent works on the real telephone network. The
row is the operational truth: it exists *before* the call is placed (status
``running``) and is updated when the run ends, so a crashed process leaves a
visible ``running`` row instead of a gap. Evidence files live on disk next to it
and are identified by their SHA-256, which is what makes the artifact tamper
evident.

Tenant scoped like every other business table: ``tenant_id`` is mandatory and is
part of every index, so a run can never be read across tenants.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base

#: Run lifecycle. ``unproven`` means the call happened but at least one provider
#: stage could not be proven real, so the row must not be read as evidence.
RUN_STATUSES = ("queued", "running", "succeeded", "unproven", "failed", "aborted")


class LiveProofRun(Base):
    """One real PSTN call placed to prove the live agent end to end."""

    __tablename__ = "live_proof_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','succeeded','unproven','failed','aborted')",
            name="ck_live_proof_run_status",
        ),
        CheckConstraint("cost_usd >= 0", name="ck_live_proof_run_cost"),
        CheckConstraint("turns >= 0", name="ck_live_proof_run_turns"),
        Index("ix_live_proof_runs_tenant_created", "tenant_id", "created_at"),
        Index("ix_live_proof_runs_tenant_status", "tenant_id", "status"),
        Index("ix_live_proof_run_call", "call_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    provider_set: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    requested_to_number: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    call_sid: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    call_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    turns: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    e2e_p50_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    e2e_p95_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    e2e_p99_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    stt_ttfb_p50_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    llm_ttfb_p50_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    tts_ttfb_p50_ms: Mapped[float | None] = mapped_column(Float, nullable=True)

    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    provider_fakes: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    evidence_path: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    evidence_sha256: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    recording_sha256: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    requested_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    error: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    detail: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def is_evidence(self) -> bool:
        """True only when the row can be used as proof of a real live call."""
        return (
            self.status == "succeeded"
            and not self.provider_fakes
            and bool(self.evidence_sha256)
        )

    @property
    def duration_seconds(self) -> float | None:
        if self.started_at is None or self.finished_at is None:
            return None
        return round((self.finished_at - self.started_at).total_seconds(), 3)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "status": self.status,
            "provider_set": [part for part in (self.provider_set or "").split(",") if part],
            "requested_to_number": self.requested_to_number,
            "call_sid": self.call_sid,
            "call_id": str(self.call_id) if self.call_id else None,
            "turns": int(self.turns or 0),
            "e2e_p50_ms": self.e2e_p50_ms,
            "e2e_p95_ms": self.e2e_p95_ms,
            "e2e_p99_ms": self.e2e_p99_ms,
            "stt_ttfb_p50_ms": self.stt_ttfb_p50_ms,
            "llm_ttfb_p50_ms": self.llm_ttfb_p50_ms,
            "tts_ttfb_p50_ms": self.tts_ttfb_p50_ms,
            "cost_usd": round(float(self.cost_usd or 0.0), 6),
            "provider_fakes": bool(self.provider_fakes),
            "is_evidence": self.is_evidence,
            "evidence_path": self.evidence_path,
            "evidence_sha256": self.evidence_sha256,
            "recording_sha256": self.recording_sha256,
            "requested_by": str(self.requested_by) if self.requested_by else None,
            "error": self.error,
            "detail": self.detail or {},
            "duration_seconds": self.duration_seconds,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<LiveProofRun {self.id} status={self.status} fakes={self.provider_fakes}>"
