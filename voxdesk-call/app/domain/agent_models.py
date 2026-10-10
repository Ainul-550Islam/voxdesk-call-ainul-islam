"""Domain models for agent configuration and lifecycle.

Why this exists
---------------
``Tenant`` carries the live voice-runtime configuration directly on the row
(``agent_name``, ``greeting``, ``system_prompt``, ``language``, ``voice_id``,
``speech_speed``, ``llm_provider``, ``llm_model``, ``llm_temperature``,
``escalation_number``, ``business_hours_*``, ``record_calls``, ...). That
layout is fast for the real-time voice loop, which cannot afford extra joins on
every turn, and is preserved as-is.

First-class ``Agent`` and immutable ``AgentVersion`` rows in PostgreSQL back the
enterprise lifecycle: draft vs published configurations, version history with
rollback, structured tool/knowledge bindings, safety policy, and multi-agent
per-tenant definitions. This module gives those concepts a strict, validated,
pure-Python domain representation that maps cleanly onto both the ``Agent`` /
``AgentVersion`` tables and the ``Tenant`` runtime projection.

No SQLAlchemy imports live here so the domain rules can be unit-tested in
isolation and reused by worker processes.
"""

from __future__ import annotations

from app.core.value_types import dictionary_value

import enum
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Enumerations & constants
# ---------------------------------------------------------------------------


class AgentStatus(str, enum.Enum):
    """Lifecycle states for an agent configuration.

    Transitions::

        DRAFT ----publish----> PUBLISHED ----retire----> RETIRED
          ^                       |   |
          +-------rollback--------+   +----archive---> ARCHIVED
    """

    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"
    RETIRED = "retired"


_ALLOWED_TRANSITIONS: dict[AgentStatus, frozenset[AgentStatus]] = {
    AgentStatus.DRAFT: frozenset(
        {AgentStatus.DRAFT, AgentStatus.PUBLISHED, AgentStatus.ARCHIVED, AgentStatus.RETIRED}
    ),
    AgentStatus.PUBLISHED: frozenset(
        {AgentStatus.PUBLISHED, AgentStatus.DRAFT, AgentStatus.ARCHIVED, AgentStatus.RETIRED}
    ),
    AgentStatus.ARCHIVED: frozenset(
        {AgentStatus.ARCHIVED, AgentStatus.DRAFT, AgentStatus.RETIRED}
    ),
    AgentStatus.RETIRED: frozenset({AgentStatus.RETIRED}),
}


def can_transition(current: AgentStatus, target: AgentStatus) -> bool:
    """Return True iff moving from ``current`` to ``target`` is permitted."""
    return target in _ALLOWED_TRANSITIONS.get(current, frozenset())


class HandoffMode(str, enum.Enum):
    NONE = "none"
    WARM = "warm"
    COLD = "cold"
    QUEUE = "queue"
    NUMBER = "number"


ALLOWED_LLM_PROVIDERS: frozenset[str] = frozenset(
    {"anthropic", "openai", "google", "groq", "azure_openai", "bedrock", "custom"}
)

ALLOWED_VOICE_PROVIDERS: frozenset[str] = frozenset(
    {"elevenlabs", "openai", "deepgram", "cartesia", "playht", "azure", "polly", "google"}
)

ALLOWED_AGENT_MODES: frozenset[str] = frozenset({"single_prompt", "flow"})

ALLOWED_TOOLS: frozenset[str] = frozenset(
    {
        "book_appointment",
        "cancel_appointment",
        "reschedule_appointment",
        "check_availability",
        "lookup_customer",
        "create_lead",
        "update_lead",
        "transfer_to_human",
        "warm_transfer",
        "end_call",
        "send_dtmf",
        "navigate_ivr",
        "leave_voicemail",
        "send_sms_confirmation",
        "search_knowledge_base",
        "capture_callback",
        "verify_caller",
    }
)

MAX_PROMPT_CHARS = 16_000
MAX_GREETING_CHARS = 500
_E164_RE = re.compile(r"^\+[1-9]\d{6,14}$")
_BCP47_RE = re.compile(r"^[a-zA-Z]{2,3}(-[a-zA-Z0-9]{2,8})*$")

# Keys that must never appear inside arbitrary configuration dicts. Prevents
# callers from smuggling provider credentials through configuration payloads
# that later get returned by ``GET`` endpoints or written to audit logs.
SECRET_KEY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"api[_-]?key", re.IGNORECASE),
    re.compile(r"secret", re.IGNORECASE),
    re.compile(r"password", re.IGNORECASE),
    re.compile(r"private[_-]?key", re.IGNORECASE),
    re.compile(r"access[_-]?token", re.IGNORECASE),
    re.compile(r"auth[_-]?token", re.IGNORECASE),
    re.compile(r"client[_-]?secret", re.IGNORECASE),
)

SECRET_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^sk-[A-Za-z0-9_\-]{12,}$"),
    re.compile(r"^rk_[A-Za-z0-9_\-]{12,}$"),
    re.compile(r"^whsec_[A-Za-z0-9_\-]{12,}$"),
    re.compile(r"^xox[baprs]-[A-Za-z0-9\-]{10,}$"),
    re.compile(r"^gh[pousr]_[A-Za-z0-9]{20,}$"),
    re.compile(r"^AIza[0-9A-Za-z\-_]{20,}$"),
)


def find_secret_like_keys(mapping: dict[str, Any], prefix: str = "") -> list[str]:
    """Return dotted paths of any keys that look like credentials or secret values."""
    offenders: list[str] = []
    if not isinstance(mapping, dict):
        return offenders
    for key, value in mapping.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if any(pattern.search(str(key)) for pattern in SECRET_KEY_PATTERNS):
            # Allow explicit boolean flags or non-secret reference IDs like secret_ref
            if str(key) not in {"secret_ref", "redact_pii"}:
                offenders.append(path)
        if isinstance(value, str) and any(
            pattern.match(value.strip()) for pattern in SECRET_VALUE_PATTERNS
        ):
            if path not in offenders:
                offenders.append(path)
        elif isinstance(value, dict):
            offenders.extend(find_secret_like_keys(value, path))
        elif isinstance(value, (list, tuple)):
            for idx, item in enumerate(value):
                if isinstance(item, dict):
                    offenders.extend(find_secret_like_keys(item, f"{path}[{idx}]"))
                elif isinstance(item, str) and any(
                    pattern.match(item.strip()) for pattern in SECRET_VALUE_PATTERNS
                ):
                    offenders.append(f"{path}[{idx}]")
    return offenders


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VoiceConfig:
    voice_id: str = ""
    speech_speed: float = 1.0
    stability: float = 0.75
    similarity_boost: float = 0.75
    barge_in_enabled: bool = True
    silence_timeout_ms: int = 1200
    provider: str = "elevenlabs"

    def validate(self) -> list[str]:
        problems: list[str] = []
        if not (0.5 <= self.speech_speed <= 2.0):
            problems.append("voice.speech_speed must be between 0.5 and 2.0")
        if not (0.0 <= self.stability <= 1.0):
            problems.append("voice.stability must be between 0.0 and 1.0")
        if not (0.0 <= self.similarity_boost <= 1.0):
            problems.append("voice.similarity_boost must be between 0.0 and 1.0")
        if not (200 <= self.silence_timeout_ms <= 10_000):
            problems.append("voice.silence_timeout_ms must be between 200 and 10000")
        if len(self.voice_id) > 80:
            problems.append("voice.voice_id exceeds 80 characters")
        return problems


@dataclass(frozen=True)
class LanguageConfig:
    primary: str = "en-US"
    fallbacks: tuple[str, ...] = ()
    auto_detect: bool = False

    def validate(self) -> list[str]:
        problems: list[str] = []
        if not _BCP47_RE.match(self.primary):
            problems.append(f"language.primary {self.primary!r} is not a valid BCP-47 tag")
        for tag in self.fallbacks:
            if not _BCP47_RE.match(tag):
                problems.append(f"language.fallbacks entry {tag!r} is not a valid BCP-47 tag")
        if len(self.fallbacks) > 10:
            problems.append("language.fallbacks may contain at most 10 entries")
        return problems


@dataclass(frozen=True)
class ModelConfig:
    provider: str = "anthropic"
    model: str = "claude-haiku-4-5-20251001"
    temperature: float = 0.3
    max_tokens: int = 300
    fallback_provider: str = "openai"

    def validate(self) -> list[str]:
        problems: list[str] = []
        if self.provider not in ALLOWED_LLM_PROVIDERS:
            problems.append(
                f"model.provider must be one of {sorted(ALLOWED_LLM_PROVIDERS)}"
            )
        if self.fallback_provider and self.fallback_provider not in ALLOWED_LLM_PROVIDERS:
            problems.append(
                f"model.fallback_provider must be one of {sorted(ALLOWED_LLM_PROVIDERS)}"
            )
        if not self.model or len(self.model) > 80:
            problems.append("model.model must be 1..80 characters")
        if not (0.0 <= self.temperature <= 1.5):
            problems.append("model.temperature must be between 0.0 and 1.5")
        if not (32 <= self.max_tokens <= 4096):
            problems.append("model.max_tokens must be between 32 and 4096")
        return problems


@dataclass(frozen=True)
class OperatingHours:
    timezone: str = "America/New_York"
    start: time = time(9, 0)
    end: time = time(17, 0)
    days: tuple[int, ...] = (0, 1, 2, 3, 4)  # 0=Mon .. 6=Sun
    after_hours_greeting: str = ""

    def validate(self) -> list[str]:
        problems: list[str] = []
        if not self.timezone or len(self.timezone) > 64:
            problems.append("operating_hours.timezone must be 1..64 characters")
        if self.start >= self.end:
            problems.append("operating_hours.start must be earlier than operating_hours.end")
        if any(d < 0 or d > 6 for d in self.days):
            problems.append("operating_hours.days entries must be 0..6 (Mon..Sun)")
        if len(self.after_hours_greeting) > MAX_GREETING_CHARS:
            problems.append(
                f"operating_hours.after_hours_greeting exceeds {MAX_GREETING_CHARS} characters"
            )
        return problems


@dataclass(frozen=True)
class HandoffConfig:
    mode: HandoffMode = HandoffMode.NONE
    destination: str = ""  # E.164 number or queue slug depending on mode
    triggers: tuple[str, ...] = ("caller_requested_human", "repeated_misunderstanding")
    max_failed_turns: int = 3
    ring_timeout_seconds: int = 25

    def validate(self) -> list[str]:
        problems: list[str] = []
        if self.mode in {HandoffMode.WARM, HandoffMode.COLD, HandoffMode.NUMBER}:
            if not self.destination or not _E164_RE.match(self.destination):
                problems.append(
                    "handoff.destination must be an E.164 phone number when mode is warm/cold/number"
                )
        elif self.mode is HandoffMode.QUEUE:
            if not self.destination or len(self.destination) > 64:
                problems.append("handoff.destination must name a target queue (1..64 chars)")
        if not (1 <= self.max_failed_turns <= 20):
            problems.append("handoff.max_failed_turns must be between 1 and 20")
        if not (5 <= self.ring_timeout_seconds <= 120):
            problems.append("handoff.ring_timeout_seconds must be between 5 and 120")
        return problems


@dataclass(frozen=True)
class SafetyPolicy:
    pii_redaction: bool = True
    profanity_filter: bool = True
    record_calls: bool = False
    disallowed_topics: tuple[str, ...] = ()
    require_consent_disclosure: bool = True

    def validate(self) -> list[str]:
        problems: list[str] = []
        if len(self.disallowed_topics) > 50:
            problems.append("safety.disallowed_topics may contain at most 50 entries")
        for topic in self.disallowed_topics:
            if not topic or len(topic) > 80:
                problems.append("safety.disallowed_topics entries must be 1..80 characters")
        return problems


@dataclass(frozen=True)
class TurnTakingConfig:
    """Turn-taking, backchannel, idle reminder, and keyword boosting settings (Sub-Phase 2B)."""

    responsiveness: float = 0.7
    interruption_sensitivity: float = 0.7
    enable_smart_turn: bool = True
    enable_backchannel: bool = False
    backchannel_frequency: float = 0.5
    backchannel_words: tuple[str, ...] = ("yeah", "uh-huh", "got it", "mm-hmm", "okay")
    reminder_trigger_ms: int = 10000
    reminder_max_count: int = 2
    boosted_keywords: tuple[tuple[str, float], ...] = ()

    def validate(self) -> list[str]:
        problems: list[str] = []
        if not (0.0 <= self.responsiveness <= 1.0):
            problems.append("turn_taking.responsiveness must be between 0.0 and 1.0")
        if not (0.0 <= self.interruption_sensitivity <= 1.0):
            problems.append("turn_taking.interruption_sensitivity must be between 0.0 and 1.0")
        if not (0.0 <= self.backchannel_frequency <= 1.0):
            problems.append("turn_taking.backchannel_frequency must be between 0.0 and 1.0")
        if len(self.backchannel_words) > 25:
            problems.append("turn_taking.backchannel_words may contain at most 25 entries")
        if not (1000 <= self.reminder_trigger_ms <= 120_000):
            problems.append("turn_taking.reminder_trigger_ms must be between 1000 and 120000")
        if not (0 <= self.reminder_max_count <= 10):
            problems.append("turn_taking.reminder_max_count must be between 0 and 10")
        if len(self.boosted_keywords) > 100:
            problems.append("turn_taking.boosted_keywords may contain at most 100 entries")
        return problems


# ---------------------------------------------------------------------------
# Aggregate root & immutable version record
# ---------------------------------------------------------------------------


def stable_id(tenant_id: str, *parts: str) -> str:
    """Deterministic short identifier for a tenant-scoped (name, ...) tuple.

    Historically this took exactly ``(tenant_id, name)``. Several domain
    modules already call it with more parts — campaign identity folds in
    ``goal`` and ``channel``, automation-run idempotency folds in the trigger
    event and the business event id, and inbox threads fold in the channel and
    the provider's source reference. With a fixed two-argument signature every
    one of those calls raised ``TypeError`` at runtime, turning an ordinary
    request into a 500.

    The extra parts are therefore accepted and folded into the digest in order.

    Backwards compatibility is exact: for the original ``(tenant_id, name)``
    call the digest input is byte-for-byte what it always was
    (``f"{tenant_id}:{name.strip().lower()}"``), so identifiers already
    persisted in the database are unchanged. Only ``parts`` are normalised
    (stripped and lower-cased); ``tenant_id`` is inserted verbatim, as before.
    """
    if not parts:
        raise ValueError("stable_id requires at least one name part in addition to tenant_id")
    folded = ":".join(str(part).strip().lower() for part in parts)
    digest = hashlib.sha256(f"{tenant_id}:{folded}".encode("utf-8")).hexdigest()
    return f"agt_{digest[:16]}"


def compute_config_etag(payload: dict[str, Any]) -> str:
    """Deterministic SHA-256 ETag for an arbitrary configuration payload."""
    canonical = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"W/\"{digest[:24]}\""


def compute_config_hash(payload: dict[str, Any]) -> str:
    """Deterministic SHA-256 hex digest for an arbitrary configuration snapshot."""
    canonical = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AgentConfig:
    """Complete, validated specification of an AI agent."""

    tenant_id: str
    name: str
    greeting: str = "Thanks for calling. How can I help you today?"
    system_prompt: str = ""
    persona: str = "professional, concise, empathetic"
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    language: LanguageConfig = field(default_factory=LanguageConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    operating_hours: OperatingHours = field(default_factory=OperatingHours)
    handoff: HandoffConfig = field(default_factory=HandoffConfig)
    safety: SafetyPolicy = field(default_factory=SafetyPolicy)
    turn_taking: TurnTakingConfig = field(default_factory=TurnTakingConfig)
    enabled_tools: tuple[str, ...] = ("book_appointment", "transfer_to_human")
    knowledge_source_ids: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    agent_id: str = ""
    mode: str = "single_prompt"
    flow: dict[str, Any] | None = None

    @property
    def id(self) -> str:
        if self.agent_id:
            return self.agent_id
        return stable_id(self.tenant_id, self.name)

    def validate(self) -> list[str]:
        problems: list[str] = []
        if not self.tenant_id:
            problems.append("tenant_id is required")
        cleaned_name = self.name.strip() if self.name else ""
        if not cleaned_name or len(cleaned_name) > 80:
            problems.append("name must be 1..80 non-whitespace characters")
        if len(self.greeting) > MAX_GREETING_CHARS:
            problems.append(f"greeting exceeds {MAX_GREETING_CHARS} characters")
        if len(self.system_prompt) > MAX_PROMPT_CHARS:
            problems.append(f"system_prompt exceeds {MAX_PROMPT_CHARS} characters")
        if len(self.persona) > 240:
            problems.append("persona exceeds 240 characters")
        problems.extend(self.voice.validate())
        problems.extend(self.language.validate())
        problems.extend(self.model.validate())
        problems.extend(self.operating_hours.validate())
        problems.extend(self.handoff.validate())
        problems.extend(self.safety.validate())
        problems.extend(self.turn_taking.validate())
        unknown_tools = [t for t in self.enabled_tools if t not in ALLOWED_TOOLS]
        if unknown_tools:
            problems.append(f"enabled_tools contains unsupported tools: {sorted(unknown_tools)}")
        if len(self.knowledge_source_ids) > 50:
            problems.append("knowledge_source_ids may contain at most 50 entries")
        secret_keys = find_secret_like_keys(self.metadata, "metadata")
        if secret_keys:
            problems.append(
                f"metadata must not contain credential-like keys: {sorted(secret_keys)}"
            )
        norm_mode = (self.mode or "single_prompt").strip().lower()
        if norm_mode not in ALLOWED_AGENT_MODES:
            problems.append(
                f"mode must be one of {sorted(ALLOWED_AGENT_MODES)}"
            )
        elif norm_mode == "flow":
            if not self.flow or not isinstance(self.flow, dict):
                problems.append("flow is required when mode is 'flow'")
            else:
                from app.builder.flow_validation import validate_flow

                flow_res = validate_flow(self.flow)
                for issue in flow_res.errors:
                    node_ref = f" (node '{issue.node_id}')" if issue.node_id else ""
                    problems.append(f"flow.{issue.code}{node_ref}: {issue.message}")
        elif self.flow and isinstance(self.flow, dict) and self.flow.get("nodes"):
            from app.builder.flow_validation import validate_flow

            flow_res = validate_flow(self.flow)
            for issue in flow_res.errors:
                node_ref = f" (node '{issue.node_id}')" if issue.node_id else ""
                problems.append(f"flow.{issue.code}{node_ref}: {issue.message}")
        return problems

    def canonical_hash(self) -> str:
        """Content hash over every behavioural field — used to detect no-op publishes."""
        payload: dict[str, Any] = {
            "name": self.name.strip(),
            "greeting": self.greeting,
            "system_prompt": self.system_prompt,
            "persona": self.persona,
            "voice": self.voice.__dict__,
            "language": {
                "primary": self.language.primary,
                "fallbacks": list(self.language.fallbacks),
                "auto_detect": self.language.auto_detect,
            },
            "model": self.model.__dict__,
            "operating_hours": {
                "timezone": self.operating_hours.timezone,
                "start": self.operating_hours.start.isoformat(),
                "end": self.operating_hours.end.isoformat(),
                "days": list(self.operating_hours.days),
                "after_hours_greeting": self.operating_hours.after_hours_greeting,
            },
            "handoff": {
                "mode": self.handoff.mode.value,
                "destination": self.handoff.destination,
                "triggers": list(self.handoff.triggers),
                "max_failed_turns": self.handoff.max_failed_turns,
                "ring_timeout_seconds": self.handoff.ring_timeout_seconds,
            },
            "safety": {
                "pii_redaction": self.safety.pii_redaction,
                "profanity_filter": self.safety.profanity_filter,
                "record_calls": self.safety.record_calls,
                "disallowed_topics": list(self.safety.disallowed_topics),
                "require_consent_disclosure": self.safety.require_consent_disclosure,
            },
            "enabled_tools": sorted(self.enabled_tools),
            "knowledge_source_ids": sorted(self.knowledge_source_ids),
        }
        if self.mode != "single_prompt" or self.flow is not None:
            payload["mode"] = self.mode
            payload["flow"] = self.flow
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def to_snapshot_dict(self) -> dict[str, Any]:
        """Serialize this AgentConfig into a JSON-safe dictionary for DB storage."""
        builder_extra = {}
        if isinstance(self.metadata, dict):
            builder_extra = {
                k: v for k, v in self.metadata.items() if not any(p.search(str(k)) for p in SECRET_KEY_PATTERNS)
            }
        snap: dict[str, Any] = {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "name": self.name,
            "mode": self.mode,
            "greeting": self.greeting,
            "system_prompt": self.system_prompt,
            "persona": self.persona,
            "voice": {
                "voice_id": self.voice.voice_id,
                "speech_speed": self.voice.speech_speed,
                "stability": self.voice.stability,
                "similarity_boost": self.voice.similarity_boost,
                "barge_in_enabled": self.voice.barge_in_enabled,
                "silence_timeout_ms": self.voice.silence_timeout_ms,
                "provider": self.voice.provider,
            },
            "language": {
                "primary": self.language.primary,
                "fallbacks": list(self.language.fallbacks),
                "auto_detect": self.language.auto_detect,
            },
            "model": {
                "provider": self.model.provider,
                "model": self.model.model,
                "temperature": self.model.temperature,
                "max_tokens": self.model.max_tokens,
                "fallback_provider": self.model.fallback_provider,
            },
            "operating_hours": {
                "timezone": self.operating_hours.timezone,
                "start": self.operating_hours.start.strftime("%H:%M"),
                "end": self.operating_hours.end.strftime("%H:%M"),
                "days": list(self.operating_hours.days),
                "after_hours_greeting": self.operating_hours.after_hours_greeting,
            },
            "handoff": {
                "mode": self.handoff.mode.value,
                "destination": self.handoff.destination,
                "triggers": list(self.handoff.triggers),
                "max_failed_turns": self.handoff.max_failed_turns,
                "ring_timeout_seconds": self.handoff.ring_timeout_seconds,
            },
            "safety": {
                "pii_redaction": self.safety.pii_redaction,
                "profanity_filter": self.safety.profanity_filter,
                "record_calls": self.safety.record_calls,
                "disallowed_topics": list(self.safety.disallowed_topics),
                "require_consent_disclosure": self.safety.require_consent_disclosure,
            },
            "turn_taking": {
                "responsiveness": self.turn_taking.responsiveness,
                "interruption_sensitivity": self.turn_taking.interruption_sensitivity,
                "enable_smart_turn": self.turn_taking.enable_smart_turn,
                "enable_backchannel": self.turn_taking.enable_backchannel,
                "backchannel_frequency": self.turn_taking.backchannel_frequency,
                "backchannel_words": list(self.turn_taking.backchannel_words),
                "reminder_trigger_ms": self.turn_taking.reminder_trigger_ms,
                "reminder_max_count": self.turn_taking.reminder_max_count,
                "boosted_keywords": [
                    [str(w), float(b)] for w, b in self.turn_taking.boosted_keywords
                ],
            },
            "enabled_tools": list(self.enabled_tools),
            "knowledge_source_ids": list(self.knowledge_source_ids),
            "metadata": builder_extra,
        }
        if self.flow is not None:
            snap["flow"] = dict(self.flow)
        return snap

    @classmethod
    def from_snapshot_dict(
        cls,
        data: dict[str, Any],
        *,
        tenant_id: str | None = None,
        fallback_name: str = "Agent",
        agent_id: str = "",
    ) -> "AgentConfig":
        """Reconstruct an AgentConfig from a stored JSON snapshot dictionary."""
        if not isinstance(data, dict):
            data = {}
        resolved_tenant = str(tenant_id or data.get("tenant_id") or "")
        def block(key: str) -> dict[str, Any]:
            value = data.get(key)
            return value if isinstance(value, dict) else {}

        identity_block = block("identity")
        voice_block = block("voice")
        lang_block = block("language")
        model_block = block("model")
        hours_block = block("operating_hours")
        handoff_block = block("handoff")
        call_handling_block = block("call_handling")
        safety_block = block("safety")
        security_block = block("security")

        name = str(
            data.get("name")
            or identity_block.get("name")
            or fallback_name
        ).strip() or fallback_name

        greeting = str(
            data.get(
                "greeting",
                identity_block.get(
                    "greeting", "Thanks for calling. How can I help you today?"
                ),
            )
        )
        system_prompt = str(
            data.get("system_prompt", model_block.get("system_prompt", ""))
        )
        persona = str(
            data.get(
                "persona",
                identity_block.get("persona", "professional, concise, empathetic"),
            )
        )

        speech_speed = float(
            voice_block.get("speech_speed", voice_block.get("speed", 1.0))
        )
        voice_cfg = VoiceConfig(
            voice_id=str(voice_block.get("voice_id", "")),
            speech_speed=speech_speed,
            stability=float(voice_block.get("stability", 0.75)),
            similarity_boost=float(voice_block.get("similarity_boost", 0.75)),
            barge_in_enabled=bool(voice_block.get("barge_in_enabled", True)),
            silence_timeout_ms=int(
                voice_block.get(
                    "silence_timeout_ms",
                    call_handling_block.get("silence_timeout_seconds", 1.2) * 1000
                    if "silence_timeout_seconds" in call_handling_block
                    else 1200,
                )
            ),
            provider=str(voice_block.get("provider", "elevenlabs")),
        )

        primary_lang = str(
            lang_block.get("primary")
            or voice_block.get("language")
            or data.get("language_primary")
            or "en-US"
        )
        lang_cfg = LanguageConfig(
            primary=primary_lang,
            fallbacks=tuple(str(x) for x in lang_block.get("fallbacks", ()) if x),
            auto_detect=bool(lang_block.get("auto_detect", False)),
        )

        provider = str(model_block.get("provider", "anthropic"))
        if provider not in ALLOWED_LLM_PROVIDERS:
            provider = "anthropic"
        fallback_provider = str(model_block.get("fallback_provider", "openai"))
        if fallback_provider and fallback_provider not in ALLOWED_LLM_PROVIDERS:
            fallback_provider = "openai"

        model_cfg = ModelConfig(
            provider=provider,
            model=str(
                model_block.get("model")
                or model_block.get("model_name")
                or "claude-haiku-4-5-20251001"
            ),
            temperature=float(model_block.get("temperature", 0.3)),
            max_tokens=int(model_block.get("max_tokens", 300)),
            fallback_provider=fallback_provider,
        )

        def _parse_time(raw: Any, default_val: time) -> time:
            if isinstance(raw, time):
                return raw
            if isinstance(raw, str) and ":" in raw:
                parts = raw.strip().split(":")
                try:
                    return time(int(parts[0]), int(parts[1]))
                except (ValueError, IndexError):
                    return default_val
            return default_val

        hours_cfg = OperatingHours(
            timezone=str(hours_block.get("timezone", "America/New_York")),
            start=_parse_time(hours_block.get("start"), time(9, 0)),
            end=_parse_time(hours_block.get("end"), time(17, 0)),
            days=tuple(int(d) for d in hours_block.get("days", (0, 1, 2, 3, 4))),
            after_hours_greeting=str(hours_block.get("after_hours_greeting", "")),
        )

        raw_mode = str(handoff_block.get("mode", HandoffMode.NONE.value))
        if raw_mode == HandoffMode.NONE.value and call_handling_block.get("transfer_phone_number"):
            raw_mode = HandoffMode.NUMBER.value
        try:
            handoff_mode = HandoffMode(raw_mode)
        except ValueError:
            handoff_mode = HandoffMode.NONE

        destination = str(
            handoff_block.get("destination")
            or call_handling_block.get("transfer_phone_number")
            or ""
        )
        handoff_cfg = HandoffConfig(
            mode=handoff_mode,
            destination=destination,
            triggers=tuple(
                str(t)
                for t in handoff_block.get(
                    "triggers", ("caller_requested_human", "repeated_misunderstanding")
                )
            ),
            max_failed_turns=int(handoff_block.get("max_failed_turns", 3)),
            ring_timeout_seconds=int(handoff_block.get("ring_timeout_seconds", 25)),
        )

        safety_cfg = SafetyPolicy(
            pii_redaction=bool(
                safety_block.get("pii_redaction", security_block.get("redact_pii", True))
            ),
            profanity_filter=bool(safety_block.get("profanity_filter", True)),
            record_calls=bool(safety_block.get("record_calls", False)),
            disallowed_topics=tuple(
                str(t) for t in safety_block.get("disallowed_topics", ()) if t
            ),
            require_consent_disclosure=bool(
                safety_block.get("require_consent_disclosure", True)
            ),
        )

        raw_tools = data.get("enabled_tools")
        if raw_tools is None:
            raw_tools = ("book_appointment", "transfer_to_human")
        enabled_tools = tuple(
            str(t) for t in raw_tools if str(t) in ALLOWED_TOOLS
        )

        raw_kb = data.get("knowledge_source_ids")
        if raw_kb is None and isinstance(data.get("knowledge_bases"), list):
            raw_kb = [
                str(kb.get("kb_id"))
                for kb in data["knowledge_bases"]
                if isinstance(kb, dict) and kb.get("kb_id")
            ]
        knowledge_source_ids = tuple(str(k) for k in (raw_kb or ()) if k)

        raw_meta = dictionary_value(data.get("metadata"))
        turn_block = block("turn_taking")
        raw_keywords = data.get("boosted_keywords", turn_block.get("boosted_keywords", ()))
        parsed_keywords: list[tuple[str, float]] = []
        if isinstance(raw_keywords, (list, tuple)):
            for item in raw_keywords:
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    parsed_keywords.append((str(item[0]), float(item[1])))
                elif isinstance(item, dict) and item.get("word"):
                    parsed_keywords.append((str(item["word"]), float(item.get("boost", 2.0))))
                elif isinstance(item, str) and item.strip():
                    parsed_keywords.append((item.strip(), 2.0))

        raw_bc_words = data.get(
            "backchannel_words",
            turn_block.get(
                "backchannel_words", ("yeah", "uh-huh", "got it", "mm-hmm", "okay")
            ),
        )
        turn_cfg = TurnTakingConfig(
            responsiveness=float(
                data.get("responsiveness", turn_block.get("responsiveness", 0.7))
            ),
            interruption_sensitivity=float(
                data.get(
                    "interruption_sensitivity",
                    turn_block.get("interruption_sensitivity", 0.7),
                )
            ),
            enable_smart_turn=bool(
                data.get("enable_smart_turn", turn_block.get("enable_smart_turn", True))
            ),
            enable_backchannel=bool(
                data.get(
                    "enable_backchannel", turn_block.get("enable_backchannel", False)
                )
            ),
            backchannel_frequency=float(
                data.get(
                    "backchannel_frequency",
                    turn_block.get("backchannel_frequency", 0.5),
                )
            ),
            backchannel_words=tuple(str(w) for w in (raw_bc_words or ()) if str(w).strip()),
            reminder_trigger_ms=int(
                data.get(
                    "reminder_trigger_ms",
                    turn_block.get("reminder_trigger_ms", 10000),
                )
            ),
            reminder_max_count=int(
                data.get(
                    "reminder_max_count",
                    turn_block.get("reminder_max_count", 2),
                )
            ),
            boosted_keywords=tuple(parsed_keywords),
        )

        raw_flow = data.get("flow")
        flow_dict = dict(raw_flow) if isinstance(raw_flow, dict) else None
        raw_mode = str(data.get("mode") or "single_prompt").strip().lower()
        if raw_mode not in ALLOWED_AGENT_MODES:
            raw_mode = "single_prompt"

        return cls(
            tenant_id=resolved_tenant,
            name=name,
            greeting=greeting,
            system_prompt=system_prompt,
            persona=persona,
            voice=voice_cfg,
            language=lang_cfg,
            model=model_cfg,
            operating_hours=hours_cfg,
            handoff=handoff_cfg,
            safety=safety_cfg,
            turn_taking=turn_cfg,
            enabled_tools=enabled_tools,
            knowledge_source_ids=knowledge_source_ids,
            metadata=dict(raw_meta),
            agent_id=str(agent_id or data.get("id") or ""),
            mode=raw_mode,
            flow=flow_dict,
        )


@dataclass(frozen=True)
class AgentVersion:
    """Immutable snapshot produced every time an ``AgentConfig`` is published.

    ``status`` is the persisted immutable-version status (for example
    ``published``, ``superseded`` or ``rolled_back``), distinct from the
    mutable lifecycle status of the parent agent.
    """

    agent_id: str
    tenant_id: str
    version: int
    status: str
    config: AgentConfig
    config_hash: str
    published_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    published_by: str = "system"
    changelog: str = ""
    id: str = ""
    version_label: str = ""
    published_environment_id: str | None = None
    published_environment: str = "production"
    source_version_id: str | None = None
    is_rollback: bool = False
    is_active: bool = True
    config_snapshot: dict[str, Any] = field(default_factory=dict)

    @property
    def version_number(self) -> int:
        return self.version


@dataclass(frozen=True)
class AgentProjection:
    """Read-model returned by the service & API layers (never exposes secrets)."""

    id: str
    tenant_id: str
    name: str
    status: AgentStatus
    active_version: int
    greeting: str
    persona: str
    primary_language: str
    fallback_languages: tuple[str, ...]
    voice_id: str
    speech_speed: float
    llm_provider: str
    llm_model: str
    llm_temperature: float
    handoff_mode: HandoffMode
    handoff_destination: str
    record_calls: bool
    pii_redaction: bool
    enabled_tools: tuple[str, ...]
    knowledge_source_ids: tuple[str, ...]
    operating_timezone: str
    operating_window: str
    updated_at: str
    external_key: str = ""
    description: str = ""
    agent_type: str = "voice"
    environment_id: str | None = None
    published_version_id: str | None = None
    published_version_number: int | None = None
    draft_etag: str = ""
    lock_version: int = 1
    validation_status: str = "unvalidated"
    validation_errors: tuple[dict[str, Any], ...] = ()
    archived_at: str | None = None
    created_at: str = ""
    mode: str = "single_prompt"
    has_flow: bool = False
