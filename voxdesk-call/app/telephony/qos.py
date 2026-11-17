"""Call quality metrics.

Missing metrics stay missing. Negative values are rejected. A quality index
is computed only when packet loss, jitter and RTT are all present. It is not
a MOS score. The formula is:

    index_v1 = clamp(0, 100, 100 - loss*2 - jitter_ms*0.5 - max(0, rtt_ms-150)*0.1)

No other score is invented.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, String, select
from sqlalchemy import Uuid as UUID
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


# ---------------------------------------------------------------------------
# PART 13 / P13-12 — disconnection reasons
#
# Free-text `failure_reason` is fine for one engineer reading a log and useless
# for a customer asking "how many calls failed because the carrier blocked us
# last month?". These are the names that answer that question. Six of them are
# new in PART 13; the four above them already existed as ad-hoc strings and are
# kept so historical rows stay readable.
# ---------------------------------------------------------------------------


class DisconnectionReason(str, enum.Enum):
    NORMAL = "normal"
    CALLER_HANGUP = "caller_hangup"
    AGENT_HANGUP = "agent_hangup"
    NO_ANSWER = "no_answer"
    USER_BUSY = "user_busy"
    # PART 13 additions
    INVALID_DESTINATION = "invalid_destination"
    TELEPHONY_PROVIDER_PERMISSION_DENIED = "telephony_provider_permission_denied"
    TELEPHONY_PROVIDER_UNAVAILABLE = "telephony_provider_unavailable"
    SIP_ROUTING_ERROR = "sip_routing_error"
    MARKED_AS_SPAM = "marked_as_spam"
    USER_DECLINED = "user_declined"


#: The six additions, as data, so the migration and the API can list them
#: without importing the enum (a migration must not import app code).
PART13_REASONS: tuple[str, ...] = (
    "invalid_destination",
    "telephony_provider_permission_denied",
    "telephony_provider_unavailable",
    "sip_routing_error",
    "marked_as_spam",
    "user_declined",
)

#: SIP final responses, as the gateway reports them. A 4xx here is a *call*
#: failure (we reached the registrar and it said no), which is exactly why it is
#: mapped and not retried blindly.
SIP_STATUS_REASONS: dict[int, DisconnectionReason] = {
    200: DisconnectionReason.NORMAL,
    403: DisconnectionReason.TELEPHONY_PROVIDER_PERMISSION_DENIED,
    401: DisconnectionReason.TELEPHONY_PROVIDER_PERMISSION_DENIED,
    402: DisconnectionReason.TELEPHONY_PROVIDER_PERMISSION_DENIED,
    404: DisconnectionReason.INVALID_DESTINATION,
    484: DisconnectionReason.INVALID_DESTINATION,
    485: DisconnectionReason.SIP_ROUTING_ERROR,
    486: DisconnectionReason.USER_BUSY,
    487: DisconnectionReason.NORMAL,
    488: DisconnectionReason.SIP_ROUTING_ERROR,
    500: DisconnectionReason.TELEPHONY_PROVIDER_UNAVAILABLE,
    502: DisconnectionReason.SIP_ROUTING_ERROR,
    503: DisconnectionReason.TELEPHONY_PROVIDER_UNAVAILABLE,
    504: DisconnectionReason.TELEPHONY_PROVIDER_UNAVAILABLE,
    600: DisconnectionReason.USER_BUSY,
    603: DisconnectionReason.USER_DECLINED,
    604: DisconnectionReason.INVALID_DESTINATION,
    606: DisconnectionReason.MARKED_AS_SPAM,
}

#: Provider error codes that are really the same six stories. Kept small on
#: purpose: an unmapped code becomes TELEPHONY_PROVIDER_UNAVAILABLE, which is
#: honest ("something at the carrier failed") rather than invented.
PROVIDER_ERROR_REASONS: dict[str, DisconnectionReason] = {
    "21211": DisconnectionReason.INVALID_DESTINATION,          # Twilio invalid 'To'
    "13224": DisconnectionReason.MARKED_AS_SPAM,               # Twilio spam block
    "30006": DisconnectionReason.TELEPHONY_PROVIDER_UNAVAILABLE,  # landline/unreachable
    "30007": DisconnectionReason.MARKED_AS_SPAM,
    "unverified_number": DisconnectionReason.TELEPHONY_PROVIDER_PERMISSION_DENIED,
    "403": DisconnectionReason.TELEPHONY_PROVIDER_PERMISSION_DENIED,
    "invalid_destination": DisconnectionReason.INVALID_DESTINATION,
    "declined": DisconnectionReason.USER_DECLINED,
}

#: What the traffic report says. One sentence, no provider names.
REASON_LABELS: dict[DisconnectionReason, str] = {
    DisconnectionReason.NORMAL: "Call ended normally",
    DisconnectionReason.CALLER_HANGUP: "The caller hung up",
    DisconnectionReason.AGENT_HANGUP: "Our side ended the call",
    DisconnectionReason.NO_ANSWER: "Nobody answered",
    DisconnectionReason.USER_BUSY: "The line was busy",
    DisconnectionReason.INVALID_DESTINATION: "The destination number does not exist",
    DisconnectionReason.TELEPHONY_PROVIDER_PERMISSION_DENIED: (
        "The carrier refused the call: permission or verification problem"
    ),
    DisconnectionReason.TELEPHONY_PROVIDER_UNAVAILABLE: (
        "The carrier was unavailable, the call never connected"
    ),
    DisconnectionReason.SIP_ROUTING_ERROR: "SIP routing failed before the call could connect",
    DisconnectionReason.MARKED_AS_SPAM: "The carrier marked this call as spam",
    DisconnectionReason.USER_DECLINED: "The callee declined the call",
}


def reason_from_sip_status(status: int) -> DisconnectionReason:
    """Map a SIP final response to a reason. Unknown codes are unavailable."""
    return SIP_STATUS_REASONS.get(int(status), DisconnectionReason.TELEPHONY_PROVIDER_UNAVAILABLE)


def reason_from_provider_error(code: str | int, *, default: DisconnectionReason | None = None) -> DisconnectionReason:
    key = str(code).strip().lower()
    if key in PROVIDER_ERROR_REASONS:
        return PROVIDER_ERROR_REASONS[key]
    if key.isdigit() and int(key) in SIP_STATUS_REASONS:
        return SIP_STATUS_REASONS[int(key)]
    return default or DisconnectionReason.TELEPHONY_PROVIDER_UNAVAILABLE


def explain_reason(reason: DisconnectionReason | str) -> str:
    try:
        return REASON_LABELS[DisconnectionReason(reason)]
    except (ValueError, KeyError):
        return "Call ended"


async def record_failed_inbound(
    session,
    tenant_id,
    *,
    call_sid: str,
    from_number: str,
    to_number: str,
    reason: DisconnectionReason | str,
    environment_id=None,
    provider: str = "",
    occurred_at: datetime | None = None,
    agent_id=None,
):
    """Create the history row for an inbound call that never got answered.

    A failed inbound call is still a call: the phone rang, the customer has a
    number in their log, and the tenant paid for the number. Without this row
    the dashboard shows a quiet morning that never happened.
    """
    from app.db.models import Agent, Call, CallDirection, CallStatus, Environment

    code = reason.value if isinstance(reason, DisconnectionReason) else str(reason)
    moment = occurred_at or _now()
    if environment_id is None:
        environment_id = (
            await session.execute(
                select(Environment.id)
                .where(Environment.tenant_id == tenant_id)
                .order_by(Environment.is_default.desc(), Environment.created_at.asc())
                .limit(1)
            )
        ).scalar_one_or_none()
    if environment_id is None:
        raise ProviderValidationError(
            "Cannot record failed inbound call without a tenant environment.",
            provider=provider or "telephony",
        )

    if agent_id is not None:
        try:
            agent_uuid = uuid.UUID(str(agent_id))
        except (TypeError, ValueError):
            agent_uuid = None
        if agent_uuid is not None:
            agent_id = (
                await session.execute(
                    select(Agent.id).where(
                        Agent.id == agent_uuid,
                        Agent.tenant_id == tenant_id,
                    )
                )
            ).scalar_one_or_none()
        else:
            agent_id = None
    row = (
        await session.execute(
            select(Call).where(Call.tenant_id == tenant_id, Call.call_sid == call_sid)
        )
    ).scalar_one_or_none()
    if row is None:
        row = Call(
            tenant_id=tenant_id,
            environment_id=environment_id,
            call_sid=call_sid,
            from_number=from_number,
            to_number=to_number,
            status=CallStatus.FAILED,
            direction=CallDirection.INBOUND,
            started_at=moment,
            ended_at=moment,
            duration_seconds=0.0,
            failure_reason=code,
            summary=explain_reason(code),
            agent_id=agent_id,
        )
        session.add(row)
    else:
        # Provider and app-level rejection paths can race to record the same
        # call SID. Update the one history row instead of creating a duplicate.
        row.status = CallStatus.FAILED
        row.direction = CallDirection.INBOUND
        row.failure_reason = code
        row.summary = explain_reason(code)
        row.ended_at = row.ended_at or moment
        row.duration_seconds = 0.0
        if environment_id is not None:
            row.environment_id = environment_id
        if agent_id is not None:
            row.agent_id = agent_id
    await session.flush()
    return row


async def reason_breakdown(session, tenant_id, *, since: datetime | None = None) -> dict[str, int]:
    """Count failed calls by reason for the traffic report."""
    from app.db.models import Call

    stmt = select(Call.failure_reason).where(
        Call.tenant_id == tenant_id, Call.failure_reason.is_not(None)
    )
    if since is not None:
        stmt = stmt.where(Call.started_at >= since)
    counts: dict[str, int] = {}
    for (reason,) in (await session.execute(stmt)).all():
        counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items()))
