"""Deterministic dialing pacing and recipient-local calling windows.

No function in this module places a call. Carrier results arrive only through
``DialerAttempt`` transitions in ``dialer.py`` and ``telephony.py``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_FLOOR
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.contact_center_v2.errors import ContactCenterV2Error

HARD_ABANDONMENT_CAP = Decimal("0.03")


@dataclass(frozen=True)
class AbandonmentMeasurement:
    window_start: datetime
    human_answered: int
    abandoned: int
    cap: Decimal
    minimum_sample: int

    @property
    def rate(self) -> Decimal:
        if self.human_answered <= 0:
            return Decimal("0")
        return Decimal(self.abandoned) / Decimal(self.human_answered)

    @property
    def sample_sufficient(self) -> bool:
        return self.human_answered >= self.minimum_sample

    @property
    def breached(self) -> bool:
        return self.sample_sufficient and self.rate >= self.cap

    @property
    def remaining(self) -> int:
        """Whole additional abandonments permitted before the cap is reached."""
        allowed = (self.cap * self.human_answered).to_integral_value(rounding=ROUND_FLOOR)
        return max(0, int(allowed) - self.abandoned)

    def as_dict(self) -> dict:
        return {
            "window_start": self.window_start.isoformat(),
            "human_answered": self.human_answered,
            "abandoned": self.abandoned,
            "rate": str(self.rate),
            "rate_percent": str(self.rate * 100),
            "cap": str(self.cap),
            "cap_percent": str(self.cap * 100),
            "sample_sufficient": self.sample_sufficient,
            "breached": self.breached,
            "remaining": self.remaining,
        }


@dataclass(frozen=True)
class PacingDecision:
    allowed: int
    reason: str
    target_inflight: int
    reserved_agents: int

    def as_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "target_inflight": self.target_inflight,
            "reserved_agents": self.reserved_agents,
        }


def effective_cap(configured: Decimal, override: Decimal | None = None) -> Decimal:
    value = Decimal(configured)
    if value <= 0:
        raise ContactCenterV2Error("invalid_abandonment_cap", "Abandonment cap must be positive", status_code=422)
    if value <= HARD_ABANDONMENT_CAP:
        return value
    if override is None or Decimal(override) < value:
        raise ContactCenterV2Error(
            "jurisdiction_override_required",
            "An abandonment cap above 3% requires an active audited jurisdiction override",
            status_code=409,
        )
    return value


def pacing_decision(
    *,
    mode: str,
    available_agents: int,
    inflight: int,
    max_inflight: int,
    ratio: Decimal,
    measurement: AbandonmentMeasurement,
) -> PacingDecision:
    available = max(0, int(available_agents))
    active = max(0, int(inflight))
    ceiling = max(1, int(max_inflight))
    if measurement.breached:
        return PacingDecision(0, "abandonment_cap", active, 0)
    if mode == "preview":
        return PacingDecision(0, "agent_click_required", active, 0)
    if mode == "ai_agent_blast":
        allowed = min(ceiling - active, max(0, int(ratio)))
        reason = "max_inflight" if active >= ceiling else "ai_agent_capacity"
        return PacingDecision(max(0, allowed), reason, active + max(0, allowed), 0)
    if available == 0:
        return PacingDecision(0, "no_available_agent", active, 0)
    if active >= ceiling:
        return PacingDecision(0, "max_inflight", active, 0)
    if mode == "progressive":
        allowed = min(available, ceiling - active)
        return PacingDecision(max(0, allowed), "progressive_agent_reserved", active, allowed)
    if mode != "predictive":
        return PacingDecision(0, "unknown_mode", active, 0)

    target = min(ceiling, int((Decimal(available) * Decimal(ratio)).to_integral_value(rounding=ROUND_FLOOR)))
    target = max(1, target)
    room = max(0, target - active)
    # Base capacity never depends on an abandonment forecast. Only the excess
    # above one ringing attempt per available agent consumes measured headroom.
    base_room = min(room, max(0, available - active))
    excess_room = max(0, room - base_room)
    if not measurement.sample_sufficient:
        excess_room = 0
    else:
        excess_room = min(excess_room, measurement.remaining)
    allowed = base_room + excess_room
    reason = "predictive_paced" if allowed else "abandonment_headroom"
    return PacingDecision(allowed, reason, target, 0)


def timezone_for_contact(raw: str) -> ZoneInfo:
    try:
        return ZoneInfo(raw.strip())
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ContactCenterV2Error(
            "recipient_timezone_required",
            "Every recipient must have a valid IANA timezone",
            status_code=409,
        ) from exc


def _minutes(value: str) -> int:
    try:
        hour, minute = (int(part) for part in value.split(":", 1))
    except (ValueError, TypeError) as exc:
        raise ContactCenterV2Error("invalid_calling_window", "Calling window time must be HH:MM", status_code=422) from exc
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ContactCenterV2Error("invalid_calling_window", "Calling window time must be HH:MM", status_code=422)
    return hour * 60 + minute


def _inside_window(local_day: date, local_minutes: int, windows: list[dict], holidays: set[str]) -> bool:
    if local_day.isoformat() in holidays:
        return False
    weekday = local_day.weekday()
    previous_day = local_day - timedelta(days=1)
    for item in windows:
        try:
            days = {int(day) for day in item.get("days", [])}
            opens = _minutes(str(item["opens_at"]))
            closes = _minutes(str(item["closes_at"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ContactCenterV2Error("invalid_calling_window", "Calling window is malformed", status_code=422) from exc
        if opens == closes:
            continue
        if opens < closes:
            if weekday in days and opens <= local_minutes < closes:
                return True
        else:
            # Overnight windows are evaluated in the recipient's own day. A
            # window starting yesterday can end after midnight today.
            if weekday in days and local_minutes >= opens:
                return True
            if previous_day.weekday() in days and previous_day.isoformat() not in holidays and local_minutes < closes:
                return True
    return False


def calling_window_open(
    *,
    recipient_timezone: str,
    windows: list[dict],
    holiday_dates: list[str],
    now: datetime,
) -> bool:
    zone = timezone_for_contact(recipient_timezone)
    aware = now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now.astimezone(timezone.utc)
    local = aware.astimezone(zone)
    return _inside_window(local.date(), local.hour * 60 + local.minute, windows, set(holiday_dates))


def next_calling_window(
    *,
    recipient_timezone: str,
    windows: list[dict],
    holiday_dates: list[str],
    now: datetime,
) -> datetime:
    zone = timezone_for_contact(recipient_timezone)
    aware = now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now.astimezone(timezone.utc)
    local = aware.astimezone(zone)
    if calling_window_open(
        recipient_timezone=recipient_timezone, windows=windows, holiday_dates=holiday_dates, now=aware
    ):
        return aware
    holidays = set(holiday_dates)
    for day_offset in range(0, 370):
        candidate_day = (local + timedelta(days=day_offset)).date()
        for item in sorted(windows, key=lambda row: str(row.get("opens_at", ""))):
            days = {int(day) for day in item.get("days", [])}
            if candidate_day.weekday() not in days or candidate_day.isoformat() in holidays:
                continue
            opens = _minutes(str(item["opens_at"]))
            candidate = datetime.combine(candidate_day, time(hour=opens // 60, minute=opens % 60), tzinfo=zone)
            if candidate <= local:
                continue
            if calling_window_open(
                recipient_timezone=recipient_timezone,
                windows=windows,
                holiday_dates=holiday_dates,
                now=candidate.astimezone(timezone.utc),
            ):
                return candidate.astimezone(timezone.utc)
    raise ContactCenterV2Error("no_calling_window", "No recipient calling window opens in the next year")
