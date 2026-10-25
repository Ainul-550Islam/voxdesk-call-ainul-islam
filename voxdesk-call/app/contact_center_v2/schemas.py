"""Strict HTTP schemas. Tenant identity is never accepted from a request body."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CallingWindow(Schema):
    days: list[int] = Field(min_length=1, max_length=7)
    opens_at: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    closes_at: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")

    @field_validator("days")
    @classmethod
    def valid_days(cls, value: list[int]) -> list[int]:
        if any(day < 0 or day > 6 for day in value) or len(set(value)) != len(value):
            raise ValueError("days must contain unique ISO weekdays from 0 through 6")
        return sorted(value)


class DialerPolicyBody(Schema):
    campaign_id: uuid.UUID
    mode: Literal["predictive", "progressive", "preview", "ai_agent_blast"]
    caller_id_number: str = Field(min_length=8, max_length=32)
    pacing_ratio: Decimal = Field(default=Decimal("1.0"), gt=0)
    abandonment_cap: Decimal = Field(default=Decimal("0.03"), gt=0, le=1)
    max_inflight: int = Field(default=10, ge=1, le=1000)
    calling_windows: list[CallingWindow] = Field(min_length=1, max_length=21)
    holiday_dates: list[str] = Field(default_factory=list, max_length=366)
    safe_harbor_message: str = Field(min_length=10, max_length=1200)
    consent_required: bool = True
    jurisdiction: str = Field(default="US", min_length=2, max_length=32)

    @field_validator("holiday_dates")
    @classmethod
    def valid_holidays(cls, value: list[str]) -> list[str]:
        parsed = []
        for item in value:
            try:
                parsed.append(datetime.strptime(item, "%Y-%m-%d").date().isoformat())
            except ValueError as exc:
                raise ValueError("holiday_dates must use YYYY-MM-DD") from exc
        return sorted(set(parsed))


class DialerPolicyPatch(Schema):
    mode: Literal["predictive", "progressive", "preview", "ai_agent_blast"] | None = None
    caller_id_number: str | None = Field(default=None, min_length=8, max_length=32)
    pacing_ratio: Decimal | None = Field(default=None, gt=0)
    abandonment_cap: Decimal | None = Field(default=None, gt=0, le=1)
    max_inflight: int | None = Field(default=None, ge=1, le=1000)
    calling_windows: list[CallingWindow] | None = Field(default=None, min_length=1, max_length=21)
    holiday_dates: list[str] | None = None
    safe_harbor_message: str | None = Field(default=None, min_length=10, max_length=1200)
    consent_required: bool | None = None
    jurisdiction: str | None = Field(default=None, min_length=2, max_length=32)


class CampaignAction(Schema):
    reason: str = Field(default="", max_length=300)


class JurisdictionOverrideBody(Schema):
    jurisdiction: str = Field(min_length=2, max_length=32)
    abandonment_cap: Decimal = Field(gt=Decimal("0.03"), le=Decimal("0.10"))
    legal_reference: str = Field(min_length=5, max_length=500)
    reason: str = Field(min_length=10, max_length=1000)
    expires_at: datetime


class PreviewSkipBody(Schema):
    reason: str = Field(min_length=1, max_length=300)


class SoftphoneRegisterBody(Schema):
    environment_id: uuid.UUID
    device_label: str = Field(min_length=1, max_length=100)
    device_fingerprint: str | None = Field(default=None, min_length=8, max_length=256)
    device_kind: Literal["browser", "desk_phone"] = "browser"


class SoftphoneCallBody(Schema):
    session_id: uuid.UUID
    destination: str = Field(min_length=8, max_length=32)
    caller_id_number: str | None = Field(default=None, min_length=8, max_length=32)


class SoftphoneJoinBody(Schema):
    session_id: uuid.UUID


class SoftphoneControlBody(Schema):
    enabled: bool


class DtmfBody(Schema):
    digits: str = Field(min_length=1, max_length=32)

    @field_validator("digits")
    @classmethod
    def valid_digits(cls, value: str) -> str:
        if any(character not in "0123456789*#ABCDabcd" for character in value):
            raise ValueError("invalid DTMF sequence")
        return value


class TransferBody(Schema):
    destination: str = Field(min_length=3, max_length=256)
    mode: Literal["blind", "warm"] = "warm"


class AssistStartBody(Schema):
    agent_user_id: uuid.UUID | None = None


class AssistTranscriptBody(Schema):
    seq: int = Field(ge=1)
    speaker: Literal["caller", "agent", "system", "supervisor"] = "caller"
    text: str = Field(min_length=1, max_length=12000)
    is_final: bool = True


class AssistFeedbackBody(Schema):
    feedback: Literal["accepted", "dismissed", "helpful", "not_helpful"]
    note: str = Field(default="", max_length=500)


class ComplianceScriptBody(Schema):
    environment_id: uuid.UUID
    name: str = Field(min_length=1, max_length=100)
    script_text: str = Field(min_length=1, max_length=20000)
    required_phrases: list[str] = Field(default_factory=list, max_length=50)
    forbidden_phrases: list[str] = Field(default_factory=list, max_length=50)
    jurisdiction: str = Field(default="US", min_length=2, max_length=32)
    version: int | None = Field(default=None, ge=1)


class ComplianceScriptUpdateBody(Schema):
    script_text: str | None = Field(default=None, min_length=1, max_length=20000)
    required_phrases: list[str] | None = Field(default=None, max_length=50)
    forbidden_phrases: list[str] | None = Field(default=None, max_length=50)
    jurisdiction: str | None = Field(default=None, min_length=2, max_length=32)
    active: bool = True


class AcdPolicyBody(Schema):
    strategy: Literal["skill_first", "priority_first", "least_loaded", "round_robin", "longest_idle"]
    required_skills: list[str] = Field(default_factory=list, max_length=20)
    priority: int = Field(default=0, ge=0, le=10000)
    max_concurrency: int = Field(default=10, ge=1, le=10000)
    overflow_kind: Literal["none", "callback", "escalate", "voicemail", "queue"] = "none"
    overflow_after_seconds: int = Field(default=0, ge=0, le=86400)
    overflow_waiting: int = Field(default=0, ge=0, le=100000)
    overflow_queue_id: uuid.UUID | None = None
    escalation_number: str = Field(default="", max_length=32)
    callback_enabled: bool = False
    max_wait_seconds: int = Field(default=300, ge=1, le=86400)
    fallback_kind: Literal["ai_agent", "voicemail", "callback", "queue"] = "callback"
    overflow_chain: list[dict] = Field(default_factory=list, max_length=10)
    ewt_config: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def overflow_target(self):
        if self.overflow_kind == "queue" and self.overflow_queue_id is None:
            raise ValueError("overflow_queue_id is required for queue overflow")
        if self.overflow_kind == "escalate" and not self.escalation_number:
            raise ValueError("escalation_number is required for escalation")
        return self


class PriorityBody(Schema):
    priority: int = Field(default=0, ge=0, le=10000)
    strategy: Literal["skill_first", "priority_first", "least_loaded", "round_robin", "longest_idle"] = "longest_idle"
    max_wait_seconds: int = Field(default=300, ge=1, le=86400)
    fallback_kind: Literal["ai_agent", "voicemail", "callback", "queue"] = "callback"
    overflow_chain: list[dict] = Field(default_factory=list, max_length=10)
    ewt_config: dict = Field(default_factory=dict)


class RouteNextBody(Schema):
    call_id: uuid.UUID | None = None
    priority: int = Field(default=0, ge=0, le=10000)


class SkillBody(Schema):
    name: str = Field(min_length=1, max_length=64)
    enabled: bool = True


class AgentSkillBody(Schema):
    skill_id: uuid.UUID
    proficiency: int = Field(ge=1, le=5)
    weight: int = Field(default=0, ge=-1000, le=1000)
    enabled: bool = True


class QueueSkillRequirementBody(Schema):
    skill_id: uuid.UUID
    min_proficiency: int = Field(default=1, ge=1, le=5)
    required: bool = True
    weight: int = Field(default=0, ge=-1000, le=1000)


class QueueSkillsBody(Schema):
    requirements: list[QueueSkillRequirementBody] = Field(default_factory=list, max_length=50)


class WrapupBody(Schema):
    call_id: uuid.UUID
    disposition: str = Field(min_length=1, max_length=80)
    summary_redacted: str = Field(default="", max_length=8000)
    tags: list[str] = Field(default_factory=list, max_length=20)


class AgentStateBody(Schema):
    state: Literal["offline", "available", "ringing", "busy", "wrap_up", "away", "paused", "disabled"]
    capacity: int | None = Field(default=None, ge=1, le=100)
    reason_code: Literal[
        "manual", "break", "lunch", "training", "technical_issue",
        "after_call_work", "shift_end", "supervisor_change",
    ] | None = None


class CallbackBody(Schema):
    queue_entry_id: uuid.UUID
    scheduled_at: datetime
    timezone: str = Field(min_length=1, max_length=64)
    note: str = Field(default="", max_length=500)


class CallbackAction(Schema):
    reason: str = Field(min_length=1, max_length=300)


class CallbackRescheduleBody(Schema):
    scheduled_at: datetime
    timezone: str = Field(min_length=1, max_length=64)


class IdempotentResponse(Schema):
    id: uuid.UUID
    replayed: bool = False
