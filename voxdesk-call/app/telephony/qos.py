"""Call quality metrics.

Missing metrics stay missing. Negative values are rejected. A quality index
is computed only when packet loss, jitter and RTT are all present. It is not
a MOS score. The formula is:

    index_v1 = clamp(0, 100, 100 - loss*2 - jitter_ms*0.5 - max(0, rtt_ms-150)*0.1)

No other score is invented.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, String, select
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base
from app.telephony.provider_errors import ProviderValidationError
from app.tenancy.isolation import NotFound

FORMULA = "index_v1 = clamp(0, 100, 100 - loss*2 - jitter_ms*0.5 - max(0, rtt_ms-150)*0.1)"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class QosSample(Base):
    __tablename__ = "call_qos_samples"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    call_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("calls.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    packet_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    jitter_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    rtt_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    disconnect_reason: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    provider_error: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    media_error: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    quality_index: Mapped[float | None] = mapped_column(Float, nullable=True)
    quality_formula: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )

    def as_dict(self) -> dict:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "call_id": str(self.call_id),
            "provider": self.provider,
            "packet_loss": self.packet_loss,
            "jitter_ms": self.jitter_ms,
            "rtt_ms": self.rtt_ms,
            "latency_ms": self.latency_ms,
            "duration_seconds": self.duration_seconds,
            "disconnect_reason": self.disconnect_reason,
            "provider_error": self.provider_error,
            "media_error": self.media_error,
            "quality_index": self.quality_index,
            "quality_formula": self.quality_formula or None,
            "mos": None,
        }


def normalize(raw: dict, *, provider: str) -> dict:
    """Validate and convert units. Absent keys stay absent."""
    if not isinstance(raw, dict):
        raise ProviderValidationError("QoS payload must be an object", provider=provider)
    if "mos" in raw:
        raise ProviderValidationError(
            "MOS is not accepted from a provider payload", provider=provider
        )
    packet_loss = _optional(
        raw, "packet_loss", provider, unit=raw.get("packet_loss_unit"), kind="percent"
    )
    jitter = _optional(raw, "jitter_ms", provider, unit=raw.get("jitter_unit"), kind="ms")
    rtt = _optional(raw, "rtt_ms", provider, unit=raw.get("rtt_unit"), kind="ms")
    latency = _optional(raw, "latency_ms", provider, unit=raw.get("latency_unit"), kind="ms")
    duration = _optional(raw, "duration_seconds", provider, unit=None, kind="seconds")
    index, formula = _index(packet_loss, jitter, rtt)
    return {
        "provider": provider,
        "packet_loss": packet_loss,
        "jitter_ms": jitter,
        "rtt_ms": rtt,
        "latency_ms": latency,
        "duration_seconds": duration,
        "disconnect_reason": str(raw.get("disconnect_reason") or "")[:80],
        "provider_error": str(raw.get("provider_error") or "")[:80],
        "media_error": str(raw.get("media_error") or "")[:80],
        "quality_index": index,
        "quality_formula": formula,
        "mos": None,
    }


async def record(
    session, *, tenant_id: uuid.UUID, call_id: uuid.UUID, provider: str, raw: dict
) -> QosSample:
    cleaned = normalize(raw, provider=provider)
    row = QosSample(
        tenant_id=tenant_id,
        call_id=call_id,
        provider=provider,
        packet_loss=cleaned["packet_loss"],
        jitter_ms=cleaned["jitter_ms"],
        rtt_ms=cleaned["rtt_ms"],
        latency_ms=cleaned["latency_ms"],
        duration_seconds=cleaned["duration_seconds"],
        disconnect_reason=cleaned["disconnect_reason"],
        provider_error=cleaned["provider_error"],
        media_error=cleaned["media_error"],
        quality_index=cleaned["quality_index"],
        quality_formula=cleaned["quality_formula"],
    )
    session.add(row)
    await session.flush()
    return row


async def aggregate(session, tenant_id: uuid.UUID) -> dict:
    rows = (
        (await session.execute(select(QosSample).where(QosSample.tenant_id == tenant_id)))
        .scalars()
        .all()
    )
    return _aggregate(rows, tenant_id)


def _aggregate(rows, tenant_id: uuid.UUID) -> dict:
    def avg(values):
        present = [value for value in values if value is not None]
        if not present:
            return None
        return round(sum(present) / len(present), 3)

    return {
        "tenant_id": str(tenant_id),
        "samples": len(rows),
        "packet_loss": avg([row.packet_loss for row in rows]),
        "jitter_ms": avg([row.jitter_ms for row in rows]),
        "rtt_ms": avg([row.rtt_ms for row in rows]),
        "latency_ms": avg([row.latency_ms for row in rows]),
        "quality_index": avg([row.quality_index for row in rows]),
        "mos": None,
    }


async def get_owned(session, tenant_id: uuid.UUID, sample_id: uuid.UUID) -> QosSample:
    row = await session.get(QosSample, sample_id)
    if row is None or row.tenant_id != tenant_id:
        raise NotFound()
    return row


def _optional(raw: dict, key: str, provider: str, *, unit, kind: str) -> float | None:
    if key not in raw or raw[key] is None:
        return None
    try:
        value = float(raw[key])
    except (TypeError, ValueError) as exc:
        raise ProviderValidationError(f"{key} is not a number", provider=provider) from exc
    if value < 0:
        raise ProviderValidationError(f"{key} cannot be negative", provider=provider)
    if kind == "percent" and value > 100:
        raise ProviderValidationError("packet loss cannot exceed 100 percent", provider=provider)
    if unit == "s" and kind == "ms":
        value = value * 1000
    return value


def _index(loss, jitter, rtt) -> tuple[float | None, str]:
    if loss is None or jitter is None or rtt is None:
        return None, ""
    raw = 100 - (loss * 2) - (jitter * 0.5) - (max(0.0, rtt - 150) * 0.1)
    return round(min(100.0, max(0.0, raw)), 3), FORMULA
