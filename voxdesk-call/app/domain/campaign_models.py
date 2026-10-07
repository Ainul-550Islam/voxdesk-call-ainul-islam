"""Campaign-domain models (Batch 01 enterprise expansion).

These objects sit *above* the existing ``Campaign``/``Lead``/``Tenant`` tables
and the outbound safety machinery in ``app/telephony/outbound.py``. The domain
layer never dials anything; it expresses the *rules* a campaign must obey —
DNC checks, call-window checks, daily limits, attempt limits — and the service
layer turns those rules into eligibility decisions and execution intents.

The state machine is the important part. A campaign moves
``draft -> scheduled -> running -> (paused <-> running) -> completed/cancelled``.
Every transition is explicit and validated, and the service layer refuses to
resume a campaign whose window has closed or whose daily limit is exhausted.

Campaign execution MUST respect the existing call-window/DNC/message safety
controls. Nothing in this module can weaken them; ``ComplianceGate`` only ever
makes requirements *stricter* (all flags default to the safe value).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, time as _time, timezone

from app.domain.agent_models import stable_id
from app.domain.automation_models import FilterRule

# ------------------------------------------------------------------ enums ---

class CampaignState(str, enum.Enum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class CampaignChannel(str, enum.Enum):
    VOICE = "voice"
    SMS = "sms"
    WHATSAPP = "whatsapp"


class CampaignGoal(str, enum.Enum):
    QUALIFY = "qualify"
    REMIND = "remind"
    FOLLOWUP = "followup"
    SURVEY = "survey"


# ------------------------------------------------------------------- parts ---

@dataclass(frozen=True)
class Segment:
    """A named audience segment expressed as filter rules (reused evaluator).

    ``environment_id`` records the environment the segment was defined in.
    An empty value means "legacy, tenant-wide"; the campaign service refuses
    to target a segment whose environment is set and differs from the
    campaign's own environment.
    """

    id: str
    tenant_id: str
    name: str
    rules: tuple[FilterRule, ...] = ()
    environment_id: str = ""

    def validate(self) -> list[str]:
        problems: list[str] = []
        if not self.tenant_id or not self.id or not self.name.strip():
            problems.append("segment requires tenant_id, id and name")
        if len(self.name) > 200:
            problems.append("segment name must be at most 200 characters")
        for rule in self.rules:
            problems += rule.validate()
        return problems


@dataclass(frozen=True)
class Audience:
    """The population a campaign targets. Bounded, tenant- and environment-scoped.

    ``environment_id`` is stamped by the campaign service from the campaign's
    own environment before persistence: leads and segments are only ever
    resolved inside that one environment.
    """

    tenant_id: str
    segment_ids: tuple[str, ...] = ()
    lead_ids: tuple[str, ...] = ()
    environment_id: str = ""

    def validate(self) -> list[str]:
        problems: list[str] = []
        if not self.tenant_id:
            problems.append("audience requires tenant_id")
        if len(self.lead_ids) > 100_000:
            problems.append("audience lead_ids must be at most 100000")
        if len(self.segment_ids) > 100:
            problems.append("audience segment_ids must be at most 100")
        return problems


@dataclass(frozen=True)
class CampaignSchedule:
    start_at: str = ""
    end_at: str = ""
    daily_start: _time = _time(9, 0)
    daily_end: _time = _time(20, 0)
    days_of_week: tuple[int, ...] = (0, 1, 2, 3, 4, 5, 6)   # 0=Monday
    timezone: str = "UTC"

    def validate(self) -> list[str]:
        problems: list[str] = []
        if self.daily_start >= self.daily_end:
            problems.append("daily_start must be earlier than daily_end")
        bad_days = sorted(set(self.days_of_week) - set(range(7)))
        if bad_days:
            problems.append(f"days_of_week contains invalid days: {bad_days}")
        if not self.days_of_week:
            problems.append("days_of_week must not be empty")
        return problems


@dataclass(frozen=True)
class Throttle:
    calls_per_minute: int = 2
    daily_limit: int = 200
    max_attempts_per_lead: int = 3

    def validate(self) -> list[str]:
        problems: list[str] = []
        if not 1 <= self.calls_per_minute <= 60:
            problems.append("calls_per_minute must be between 1 and 60")
        if not 1 <= self.daily_limit <= 100_000:
            problems.append("daily_limit must be between 1 and 100000")
        if not 1 <= self.max_attempts_per_lead <= 10:
            problems.append("max_attempts_per_lead must be between 1 and 10")
        return problems


@dataclass(frozen=True)
class ComplianceGate:
    """The safety requirements a campaign must honour. Defaults are strict."""

    require_dnc_check: bool = True
    require_call_window: bool = True
    require_attempt_limit: bool = True
    require_daily_limit: bool = True
    require_a2p_registration: bool = False   # only for SMS/WhatsApp channels

    def validate(self) -> list[str]:
        # These may not be disabled: the domain offers no way to weaken the
        # legal/regulatory controls, only to tighten them.
        problems: list[str] = []
        if not self.require_dnc_check:
            problems.append("require_dnc_check may not be disabled")
        if not self.require_call_window:
            problems.append("require_call_window may not be disabled")
        return problems


@dataclass(frozen=True)
class CampaignDefinition:
    """The full domain view of a campaign.

    ``environment_id`` is part of the campaign's *identity*, not a cosmetic
    response field: since migration ``0026_campaign_environment_scope`` the
    persistence contract stores it NOT NULL beside ``tenant_id``, and the
    service layer resolves and stamps it before any validation that leads to
    a write. A campaign with an empty ``environment_id`` can still be built
    and reasoned about in-memory (legacy callers), but it can never be
    persisted — see :meth:`validate_binding`.
    """

    id: str
    tenant_id: str
    name: str
    environment_id: str = ""
    goal: CampaignGoal = CampaignGoal.QUALIFY
    channel: CampaignChannel = CampaignChannel.VOICE
    script_prompt: str = ""
    opening_line: str = ""
    audience: Audience = field(default_factory=lambda: Audience(tenant_id=""))
    schedule: CampaignSchedule = field(default_factory=CampaignSchedule)
    throttle: Throttle = field(default_factory=Throttle)
    compliance: ComplianceGate = field(default_factory=ComplianceGate)
    state: CampaignState = CampaignState.DRAFT

    def validate(self) -> list[str]:
        problems: list[str] = []
        if not self.tenant_id:
            problems.append("campaign requires tenant_id")
        if not self.id:
            problems.append("campaign requires id")
        if not self.name.strip() or len(self.name) > 200:
            problems.append("campaign name must be 1–200 characters")
        if len(self.script_prompt) > 20_000:
            problems.append("script_prompt must be at most 20000 characters")
        if len(self.opening_line) > 2_000:
            problems.append("opening_line must be at most 2000 characters")
        if self.environment_id and self.audience.environment_id and (
            self.environment_id != self.audience.environment_id
        ):
            problems.append("audience environment must match the campaign environment")
        problems += self.audience.validate()
        problems += self.schedule.validate()
        problems += self.throttle.validate()
        problems += self.compliance.validate()
        if self.channel is not CampaignChannel.VOICE and not self.compliance.require_a2p_registration:
            problems.append("non-voice campaigns must require A2P registration")
        return problems

    def validate_binding(self) -> list[str]:
        """The persistence contract: identity is tenant + environment + campaign.

        Called by the service at every write boundary. ``validate()`` stays
        lenient about ``environment_id`` so in-memory/legacy domain use keeps
        working; a *persisted* campaign must always carry all three.
        """
        problems = self.validate()
        if not self.environment_id:
            problems.append("campaign requires environment_id")
        if self.audience.environment_id and self.audience.environment_id != self.environment_id:
            problems.append("audience environment must match the campaign environment")
        return problems

    def is_valid(self) -> bool:
        return not self.validate()

    def identity(self) -> str:
        return stable_id(self.tenant_id, self.name, self.goal.value, self.channel.value)


@dataclass(frozen=True)
class CampaignExecutionIntent:
    """A single, safe unit of work the outbound layer MAY pick up later.

    Creating an intent is **not** dialing. It only records "this lead is
    eligible and due"; the existing outbound mechanisms decide when and how to
    act. ``idempotency_key`` stops the same lead being queued twice.

    Intent identity is ``tenant + environment + campaign + lead``. The
    ``campaign_id`` is a database primary key bound to exactly one
    tenant/environment pair (migration 0026), so the deterministic
    ``intent_idempotency_key(campaign_id, lead_id)`` already implies the
    environment; ``environment_id`` is carried explicitly so consumers can
    enforce the boundary without a database round-trip.
    """

    id: str
    campaign_id: str
    tenant_id: str
    lead_id: str
    channel: CampaignChannel
    window_at: str = ""
    idempotency_key: str = ""
    reason_skipped: str = ""
    environment_id: str = ""

    @property
    def skipped(self) -> bool:
        return bool(self.reason_skipped)

    def validate(self) -> list[str]:
        problems: list[str] = []
        for field_name in ("id", "campaign_id", "tenant_id", "lead_id"):
            if not getattr(self, field_name):
                problems.append(f"execution intent requires {field_name}")
        if not self.environment_id and not self.skipped:
            problems.append("execution intent requires environment_id")
        return problems


@dataclass(frozen=True)
class CampaignMetrics:
    """Aggregate-safe outcome counters. No phone numbers, no PII."""

    total_leads: int = 0
    attempted: int = 0
    answered: int = 0
    completed: int = 0
    booked: int = 0
    failed: int = 0
    dnc_skipped: int = 0
    window_skipped: int = 0
    attempt_limit_skipped: int = 0
    daily_limit_skipped: int = 0
    conversions: int = 0

    def merge(self, other: "CampaignMetrics") -> "CampaignMetrics":
        return CampaignMetrics(**{
            name: getattr(self, name) + getattr(other, name)
            for name in self.__dataclass_fields__
        })


def intent_idempotency_key(campaign_id: str, lead_id: str) -> str:
    """One campaign + one lead ⇒ one key, so re-planning never double-queues.

    The persisted identity contract binds ``campaign_id`` (a primary key) to
    exactly one tenant + environment pair, so the pair ``(campaign_id,
    lead_id)`` already implies the environment; folding the environment
    string in as well would change every key for the same logical work unit
    without making it more unique.
    """
    return stable_id(campaign_id, lead_id)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
