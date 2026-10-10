"""Enterprise agent configuration & lifecycle service (PostgreSQL-backed).

Responsibilities
----------------
1. Manage durable ``Agent`` and immutable ``AgentVersion`` records in PostgreSQL
   via SQLAlchemy ``AsyncSession`` with strict tenant and environment scoping.
2. Enforce optimistic concurrency control via ``draft_etag`` (``If-Match``) on
   draft saves and ``SELECT ... FOR UPDATE`` row-level locking on version
   number allocation during publish and rollback.
3. Project published agent configurations onto ``Tenant`` voice/LLM runtime
   columns via ``_apply_to_tenant`` so the low-latency voice loop continues to
   read ``Tenant`` directly without extra joins.
4. Provide deterministic configuration validation, builder schema validation,
   tool & knowledge base binding helpers, version diffing, environment
   promotion, lifecycle management (archive/restore/delete/clone), and
   structured audit logging.
"""

from __future__ import annotations

from app.core.value_types import dictionary_value, list_value

import asyncio
import copy
import re
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.identity.events import scrub
from app.auth.service import record_audit
from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.core.logging import log
from app.db.models import (
    Agent,
    AgentLifecycleStatus,
    AgentValidationStatus,
    AgentVersion as AgentVersionRow,
    AgentVersionStatus,
    AuditAction,
    Tenant,
)
from app.domain.agent_models import (
    ALLOWED_LLM_PROVIDERS,
    ALLOWED_TOOLS,
    AgentConfig,
    AgentProjection,
    AgentStatus,
    AgentVersion,
    HandoffConfig,
    HandoffMode,
    LanguageConfig,
    ModelConfig,
    OperatingHours,
    SafetyPolicy,
    VoiceConfig,
    can_transition,
    compute_config_etag,
    compute_config_hash,
    find_secret_like_keys,
    stable_id,
)

_PUBLISH_LOCKS: dict[str, asyncio.Lock] = {}


def _get_publish_lock(key: str) -> asyncio.Lock:
    lock = _PUBLISH_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _PUBLISH_LOCKS[key] = lock
    return lock


_E164_RE = re.compile(r"^\+[1-9]\d{6,14}$")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _tenant_uuid(tenant_or_id: Tenant | str | uuid.UUID) -> uuid.UUID:
    raw = tenant_or_id.id if isinstance(tenant_or_id, Tenant) else tenant_or_id
    if isinstance(raw, uuid.UUID):
        return raw
    try:
        return uuid.UUID(str(raw))
    except (TypeError, ValueError) as exc:
        raise BadRequestError(f"Invalid tenant UUID: {raw!r}") from exc


def _parse_optional_uuid(value: str | uuid.UUID | None) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


async def _emit_audit(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    action: str,
    agent_id: str,
    actor: str | uuid.UUID | None = None,
    environment_id: uuid.UUID | None = None,
    reason: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    actor_uuid = _parse_optional_uuid(actor)
    payload = scrub(
        {
            "event": action,
            "operation": action,
            "resource_type": "agent",
            "resource_id": str(agent_id),
            "environment_id": str(environment_id) if environment_id else None,
            "reason": reason,
            "actor_label": str(actor) if (actor and actor_uuid is None) else None,
            **(details or {}),
        }
    )
    try:
        await record_audit(
            session,
            action=AuditAction.GOVERNANCE_EVENT,
            tenant_id=tenant_id,
            actor_user_id=actor_uuid,
            actor_email=str(actor or "")[:320] if actor_uuid is None else "",
            detail=payload,
            commit=False,
        )
    except Exception as exc:
        log.warning("agent.audit.record_failed", action=action, error=str(exc))
    log.info(
        action,
        tenant_id=str(tenant_id),
        agent_id=str(agent_id),
        actor=str(actor or "system"),
        **(details or {}),
    )


# ---------------------------------------------------------------------------
# Pure translation helpers between Tenant <-> AgentConfig
# ---------------------------------------------------------------------------


def from_tenant(tenant: Tenant) -> AgentConfig:
    """Build an ``AgentConfig`` from the live columns on a ``Tenant`` row."""
    raw_days = getattr(tenant, "business_hours_days", "") or ""
    days = tuple(
        int(part)
        for part in raw_days.split(",")
        if part.strip().isdigit()
    ) or (0, 1, 2, 3, 4)
    escalation = getattr(tenant, "escalation_number", None)
    handoff_mode = HandoffMode.NUMBER if escalation else HandoffMode.NONE
    provider = (getattr(tenant, "llm_provider", None) or "anthropic").lower()
    if provider not in ALLOWED_LLM_PROVIDERS:
        provider = "anthropic"
    temp_val = getattr(tenant, "llm_temperature", None)
    if temp_val is None:
        temp_val = getattr(tenant, "temperature", 0.3)
    sys_prompt = getattr(tenant, "system_prompt", None)
    if sys_prompt is None:
        sys_prompt = getattr(tenant, "system_prompt_extra", "") or ""
    start_time = (
        getattr(tenant, "business_hours_start", None)
        or getattr(tenant, "business_open", None)
        or OperatingHours().start
    )
    end_time = (
        getattr(tenant, "business_hours_end", None)
        or getattr(tenant, "business_close", None)
        or OperatingHours().end
    )
    return AgentConfig(
        tenant_id=str(tenant.id),
        name=(getattr(tenant, "agent_name", None) or getattr(tenant, "name", None) or "Alex").strip()[:80],
        greeting=getattr(tenant, "greeting", None) or "Thanks for calling. How can I help you today?",
        system_prompt=sys_prompt or "",
        voice=VoiceConfig(
            voice_id=getattr(tenant, "voice_id", None) or "",
            speech_speed=float(getattr(tenant, "speech_speed", None) or 1.0),
        ),
        language=LanguageConfig(primary=getattr(tenant, "language", None) or "en-US"),
        model=ModelConfig(
            provider=provider,
            model=getattr(tenant, "llm_model", None) or "claude-haiku-4-5-20251001",
            temperature=float(temp_val if temp_val is not None else 0.3),
        ),
        operating_hours=OperatingHours(
            timezone=getattr(tenant, "timezone", None) or "America/New_York",
            start=start_time,
            end=end_time,
            days=days,
            after_hours_greeting=getattr(tenant, "after_hours_greeting", None) or "",
        ),
        handoff=HandoffConfig(
            mode=handoff_mode,
            destination=escalation or "",
        ),
        safety=SafetyPolicy(record_calls=bool(getattr(tenant, "record_calls", False))),
    )


def _apply_to_tenant(tenant: Tenant, config: AgentConfig) -> None:
    """Write the voice-runtime subset of ``config`` onto ``tenant``."""
    tenant.agent_name = config.name.strip()
    tenant.greeting = config.greeting
    tenant.system_prompt = config.system_prompt
    if hasattr(tenant, "system_prompt_extra"):
        tenant.system_prompt_extra = config.system_prompt
    tenant.language = config.language.primary
    tenant.voice_id = config.voice.voice_id or None
    tenant.speech_speed = config.voice.speech_speed
    tenant.llm_provider = config.model.provider
    tenant.llm_model = config.model.model
    tenant.llm_temperature = config.model.temperature
    if hasattr(tenant, "temperature"):
        tenant.temperature = config.model.temperature
    tenant.timezone = config.operating_hours.timezone
    tenant.business_hours_start = config.operating_hours.start
    tenant.business_hours_end = config.operating_hours.end
    if hasattr(tenant, "business_open"):
        tenant.business_open = config.operating_hours.start
    if hasattr(tenant, "business_close"):
        tenant.business_close = config.operating_hours.end
    tenant.business_hours_days = ",".join(str(d) for d in config.operating_hours.days)
    tenant.after_hours_greeting = config.operating_hours.after_hours_greeting or None
    tenant.escalation_number = (
        config.handoff.destination
        if config.handoff.mode in {HandoffMode.WARM, HandoffMode.COLD, HandoffMode.NUMBER}
        else None
    )
    tenant.record_calls = config.safety.record_calls


def default_builder_config(
    *,
    name: str = "New Agent",
    description: str = "",
    greeting: str = "Hello, how can I help you today?",
    system_prompt: str = "You are a helpful AI assistant.",
    persona: str = "professional, concise, empathetic",
    voice_provider: str = "elevenlabs",
    voice_id: str = "rachel",
    language: str = "en-US",
    speech_speed: float = 1.0,
    stability: float = 0.75,
    similarity_boost: float = 0.75,
    llm_provider: str = "anthropic",
    llm_model: str = "claude-haiku-4-5-20251001",
    temperature: float = 0.3,
    max_tokens: int = 300,
    transfer_phone_number: str | None = None,
    record_calls: bool = False,
    redact_pii: bool = True,
    enabled_tools: tuple[str, ...] | list[str] = ("book_appointment", "transfer_to_human"),
    knowledge_bases: list[dict[str, Any]] | None = None,
    tools: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Construct a unified snapshot dictionary compatible with both AgentConfig and Builder UI."""
    kb_list = knowledge_bases or []
    tool_list = tools or []
    return {
        "name": name,
        "greeting": greeting,
        "system_prompt": system_prompt,
        "persona": persona,
        "identity": {
            "name": name,
            "description": description,
            "persona": persona,
            "greeting": greeting,
            "end_call_message": "Thank you for calling. Goodbye!",
            "fallback_message": "I'm sorry, I didn't catch that. Could you repeat?",
        },
        "voice": {
            "provider": voice_provider,
            "voice_id": voice_id,
            "language": language,
            "speed": speech_speed,
            "speech_speed": speech_speed,
            "pitch": 1.0,
            "stability": stability,
            "similarity_boost": similarity_boost,
            "barge_in_enabled": True,
            "silence_timeout_ms": 1200,
            "fallback_voice_id": None,
        },
        "language": {
            "primary": language,
            "fallbacks": [],
            "auto_detect": False,
        },
        "model": {
            "provider": llm_provider,
            "model": llm_model,
            "model_name": llm_model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "system_prompt": system_prompt,
            "fallback_provider": "openai",
            "context_window_turns": 20,
            "response_style": "conversational",
        },
        "operating_hours": {
            "timezone": "America/New_York",
            "start": "09:00",
            "end": "17:00",
            "days": [0, 1, 2, 3, 4],
            "after_hours_greeting": "",
        },
        "handoff": {
            "mode": "number" if transfer_phone_number else "none",
            "destination": transfer_phone_number or "",
            "triggers": ["caller_requested_human", "repeated_misunderstanding"],
            "max_failed_turns": 3,
            "ring_timeout_seconds": 25,
        },
        "safety": {
            "pii_redaction": redact_pii,
            "profanity_filter": True,
            "record_calls": record_calls,
            "disallowed_topics": [],
            "require_consent_disclosure": True,
        },
        "knowledge_bases": kb_list,
        "knowledge_source_ids": [
            str(kb.get("kb_id")) for kb in kb_list if isinstance(kb, dict) and kb.get("kb_id")
        ],
        "tools": tool_list,
        "enabled_tools": list(enabled_tools),
        "call_handling": {
            "silence_timeout_seconds": 5,
            "max_call_duration_seconds": 1800,
            "interruption_sensitivity": 0.5,
            "voicemail_detection": True,
            "dtmf_enabled": True,
            "transfer_phone_number": transfer_phone_number,
        },
        "security": {
            "redact_pii": redact_pii,
            "retention_days": 90,
            "allowed_domains": [],
            "webhook_signing_enabled": True,
            "custom_headers": {},
        },
        "metadata": {},
    }


def merge_config_and_builder_snapshot(
    config: AgentConfig,
    existing_snapshot: dict[str, Any] | None = None,
    description: str = "",
) -> dict[str, Any]:
    """Merge an AgentConfig into a unified snapshot preserving builder-specific blocks."""
    base = copy.deepcopy(existing_snapshot) if isinstance(existing_snapshot, dict) else {}
    cfg_dict = config.to_snapshot_dict()
    base.update(cfg_dict)

    identity = dict(base.get("identity") or {})
    identity.update(
        {
            "name": config.name,
            "description": description or identity.get("description", ""),
            "persona": config.persona,
            "greeting": config.greeting,
            "end_call_message": identity.get(
                "end_call_message", "Thank you for calling. Goodbye!"
            ),
            "fallback_message": identity.get(
                "fallback_message", "I'm sorry, I didn't catch that. Could you repeat?"
            ),
        }
    )
    base["identity"] = identity

    voice = dict(base.get("voice") or {})
    voice.update(
        {
            "provider": config.voice.provider or voice.get("provider", "elevenlabs"),
            "voice_id": config.voice.voice_id or voice.get("voice_id") or "rachel_en_us",
            "language": config.language.primary,
            "speed": config.voice.speech_speed,
            "speech_speed": config.voice.speech_speed,
            "pitch": voice.get("pitch", 1.0),
            "stability": config.voice.stability,
            "similarity_boost": config.voice.similarity_boost,
            "barge_in_enabled": config.voice.barge_in_enabled,
            "silence_timeout_ms": config.voice.silence_timeout_ms,
            "fallback_voice_id": voice.get("fallback_voice_id"),
        }
    )
    base["voice"] = voice

    model = dict(base.get("model") or {})
    model.update(
        {
            "provider": config.model.provider,
            "model": config.model.model,
            "model_name": config.model.model,
            "temperature": config.model.temperature,
            "max_tokens": config.model.max_tokens,
            "system_prompt": (
                config.system_prompt
                or model.get("system_prompt")
                or f"You are {config.name}, a helpful and concise AI voice assistant."
            ),
            "fallback_provider": config.model.fallback_provider,
            "context_window_turns": model.get("context_window_turns", 20),
            "response_style": model.get("response_style", "conversational"),
        }
    )
    base["model"] = model

    call_handling = dict(base.get("call_handling") or {})
    transfer_num = (
        config.handoff.destination
        if config.handoff.mode in {HandoffMode.WARM, HandoffMode.COLD, HandoffMode.NUMBER}
        and config.handoff.destination
        else call_handling.get("transfer_phone_number")
    )
    call_handling.update(
        {
            "silence_timeout_seconds": call_handling.get("silence_timeout_seconds", 5),
            "max_call_duration_seconds": call_handling.get("max_call_duration_seconds", 1800),
            "interruption_sensitivity": call_handling.get("interruption_sensitivity", 0.5),
            "voicemail_detection": call_handling.get("voicemail_detection", True),
            "dtmf_enabled": call_handling.get("dtmf_enabled", True),
            "transfer_phone_number": transfer_num,
        }
    )
    base["call_handling"] = call_handling

    security = dict(base.get("security") or {})
    security.update(
        {
            "redact_pii": config.safety.pii_redaction,
            "retention_days": security.get("retention_days", 90),
            "allowed_domains": security.get("allowed_domains", []),
            "webhook_signing_enabled": security.get("webhook_signing_enabled", True),
            "custom_headers": security.get("custom_headers", {}),
        }
    )
    base["security"] = security

    if "knowledge_bases" not in base or not isinstance(base["knowledge_bases"], list):
        base["knowledge_bases"] = [
            {
                "kb_id": kb_id,
                "name": f"Knowledge Source {kb_id}",
                "priority": idx + 1,
                "top_k": 3,
                "similarity_threshold": 0.7,
            }
            for idx, kb_id in enumerate(config.knowledge_source_ids)
        ]
    if "tools" not in base or not isinstance(base["tools"], list):
        base["tools"] = []

    return base


def validate_snapshot_dict(
    snapshot: dict[str, Any],
    *,
    tenant_id: str,
    agent_name: str = "",
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Run full structured validation over a draft snapshot dictionary.

    Returns ``(errors, warnings)`` where each item has ``field``, ``code``, ``message``.
    """
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    identity = dictionary_value(snapshot.get("identity"))
    voice = dictionary_value(snapshot.get("voice"))
    model = dictionary_value(snapshot.get("model"))
    call_handling = (
        dictionary_value(snapshot.get("call_handling"))
    )
    handoff = dictionary_value(snapshot.get("handoff"))

    resolved_name = (
        str(identity.get("name") or snapshot.get("name") or agent_name or "").strip()
    )
    if not resolved_name:
        errors.append(
            {
                "field": "identity.name",
                "code": "REQUIRED",
                "message": "Agent name is required.",
            }
        )
    elif len(resolved_name) > 80:
        errors.append(
            {
                "field": "identity.name",
                "code": "TOO_LONG",
                "message": "Agent name must be 1..80 characters.",
            }
        )

    sys_prompt = str(
        model.get("system_prompt")
        if "system_prompt" in model
        else snapshot.get("system_prompt", "You are a helpful AI assistant.")
    )
    if "system_prompt" in model and not sys_prompt.strip():
        errors.append(
            {
                "field": "model.system_prompt",
                "code": "EMPTY_PROMPT",
                "message": "System prompt cannot be empty.",
            }
        )

    voice_id_val = voice.get("voice_id")
    if "voice_id" in voice and voice_id_val is not None and not str(voice_id_val).strip():
        errors.append(
            {
                "field": "voice.voice_id",
                "code": "REQUIRED",
                "message": "Voice ID is required.",
            }
        )

    speed_val = voice.get("speed", voice.get("speech_speed", 1.0))
    try:
        speed_f = float(speed_val)
        if not (0.5 <= speed_f <= 2.0):
            errors.append(
                {
                    "field": "voice.speed",
                    "code": "OUT_OF_RANGE",
                    "message": "voice.speed must be between 0.5 and 2.0.",
                }
            )
    except (TypeError, ValueError):
        errors.append(
            {
                "field": "voice.speed",
                "code": "INVALID_TYPE",
                "message": "voice.speed must be a number.",
            }
        )

    temp_val = model.get("temperature", 0.3)
    try:
        temp_f = float(temp_val)
        if not (0.0 <= temp_f <= 2.0):
            errors.append(
                {
                    "field": "model.temperature",
                    "code": "OUT_OF_RANGE",
                    "message": "model.temperature must be between 0.0 and 2.0.",
                }
            )
        elif temp_f > 1.2:
            warnings.append(
                {
                    "field": "model.temperature",
                    "code": "HIGH_TEMPERATURE",
                    "message": "High temperature (>1.2) may cause unpredictable responses.",
                }
            )
    except (TypeError, ValueError):
        errors.append(
            {
                "field": "model.temperature",
                "code": "INVALID_TYPE",
                "message": "model.temperature must be a number.",
            }
        )

    transfer_num = call_handling.get("transfer_phone_number") or (
        handoff.get("destination")
        if handoff.get("mode") in {"warm", "cold", "number"}
        else None
    )
    if transfer_num:
        if not _E164_RE.match(str(transfer_num)):
            errors.append(
                {
                    "field": "call_handling.transfer_phone_number",
                    "code": "INVALID_PHONE",
                    "message": "Transfer phone number must be in E.164 format (e.g. +14155552671).",
                }
            )

    secret_paths = find_secret_like_keys(snapshot)
    for path in secret_paths:
        errors.append(
            {
                "field": path,
                "code": "RAW_SECRET_FORBIDDEN",
                "message": f"Field '{path}' contains a forbidden credential key or raw secret value.",
            }
        )

    try:
        cfg = AgentConfig.from_snapshot_dict(
            snapshot, tenant_id=tenant_id, fallback_name=resolved_name or "Agent"
        )
        for problem in cfg.validate():
            field_name = problem.split()[0] if problem else "config"
            if not any(e["message"] == problem for e in errors):
                errors.append(
                    {
                        "field": field_name,
                        "code": "DOMAIN_VALIDATION_ERROR",
                        "message": problem,
                    }
                )
    except Exception as exc:
        __import__("logging").getLogger(__name__).debug("suppressed_exception", exc_info=True)
        errors.append(
            {
                "field": "config",
                "code": "INVALID_CONFIG",
                "message": str(exc),
            }
        )

    greeting_val = str(
        identity.get("greeting") if "greeting" in identity else snapshot.get("greeting", "")
    )
    if not greeting_val.strip():
        warnings.append(
            {
                "field": "identity.greeting",
                "code": "EMPTY_GREETING",
                "message": "No greeting message set; agent will wait for user to speak first.",
            }
        )

    tools_list = list_value(snapshot.get("tools"))
    for idx, tool in enumerate(tools_list):
        if isinstance(tool, dict):
            endpoint = str(tool.get("endpoint_url") or "")
            if endpoint and not endpoint.startswith("https://"):
                errors.append(
                    {
                        "field": f"tools[{idx}].endpoint_url",
                        "code": "INSECURE_URL",
                        "message": f"Tool '{tool.get('name', idx)}' endpoint must use HTTPS.",
                    }
                )

    return errors, warnings


# ---------------------------------------------------------------------------
# Row <-> Domain conversion helpers
# ---------------------------------------------------------------------------


def _row_to_agent_config(row: Agent) -> AgentConfig:
    snapshot = row.current_draft_config if isinstance(row.current_draft_config, dict) else {}
    return AgentConfig.from_snapshot_dict(
        snapshot,
        tenant_id=str(row.tenant_id),
        fallback_name=row.name,
        agent_id=row.external_key or str(row.id),
    )


def _version_row_to_domain(row: AgentVersionRow, external_key: str | None = None) -> AgentVersion:
    snapshot = row.config_snapshot if isinstance(row.config_snapshot, dict) else {}
    fallback_name = str(
        snapshot.get("name")
        or (snapshot.get("identity") or {}).get("name")
        or "Agent"
    )
    cfg = AgentConfig.from_snapshot_dict(
        snapshot,
        tenant_id=str(row.tenant_id),
        fallback_name=fallback_name,
        agent_id=external_key or str(row.agent_id),
    )
    return AgentVersion(
        id=str(row.id),
        agent_id=external_key or str(row.agent_id),
        tenant_id=str(row.tenant_id),
        version=row.version_number,
        version_label=row.version_label or f"v{row.version_number}",
        status=str(row.status),
        config=cfg,
        config_hash=row.config_hash,
        published_at=row.published_at,
        published_by=str(row.published_by_user_id) if row.published_by_user_id else str((row.meta or {}).get("published_by", "system")),
        changelog=row.changelog or "",
        published_environment_id=(
            str(row.published_environment_id) if row.published_environment_id else None
        ),
        published_environment=row.published_environment or "production",
        source_version_id=str(row.source_version_id) if row.source_version_id else None,
        is_rollback=bool(row.is_rollback),
        is_active=bool(row.is_active),
        config_snapshot=copy.deepcopy(snapshot),
    )


def project(
    config: AgentConfig,
    *,
    status: AgentStatus,
    active_version: int,
    updated_at: datetime | None = None,
    agent_row: Agent | None = None,
) -> AgentProjection:
    """Build a safe, secret-free ``AgentProjection`` for API consumers."""
    window = (
        f"{config.operating_hours.start.strftime('%H:%M')}-"
        f"{config.operating_hours.end.strftime('%H:%M')}"
    )
    ts = (updated_at or (agent_row.updated_at if agent_row else None) or _utcnow()).isoformat()
    if agent_row is not None:
        return AgentProjection(
            id=str(agent_row.id),
            external_key=agent_row.external_key,
            tenant_id=str(agent_row.tenant_id),
            name=agent_row.name,
            description=agent_row.description or "",
            agent_type=agent_row.agent_type or "voice",
            status=status,
            active_version=active_version,
            greeting=config.greeting,
            persona=config.persona,
            primary_language=config.language.primary,
            fallback_languages=config.language.fallbacks,
            voice_id=config.voice.voice_id,
            speech_speed=config.voice.speech_speed,
            llm_provider=config.model.provider,
            llm_model=config.model.model,
            llm_temperature=config.model.temperature,
            handoff_mode=config.handoff.mode,
            handoff_destination=config.handoff.destination,
            record_calls=config.safety.record_calls,
            pii_redaction=config.safety.pii_redaction,
            enabled_tools=config.enabled_tools,
            knowledge_source_ids=config.knowledge_source_ids,
            operating_timezone=config.operating_hours.timezone,
            operating_window=window,
            updated_at=ts,
            environment_id=(
                str(agent_row.environment_id) if agent_row.environment_id else None
            ),
            published_version_id=(
                str(agent_row.published_version_id) if agent_row.published_version_id else None
            ),
            published_version_number=agent_row.published_version_number,
            draft_etag=agent_row.draft_etag or "",
            lock_version=agent_row.lock_version or 1,
            validation_status=agent_row.validation_status or "unvalidated",
            validation_errors=tuple(agent_row.validation_errors or ()),
            archived_at=(
                agent_row.archived_at.isoformat() if agent_row.archived_at else None
            ),
            created_at=(
                agent_row.created_at.isoformat() if agent_row.created_at else ts
            ),
        )
    return AgentProjection(
        id=config.id,
        external_key=config.id,
        tenant_id=config.tenant_id,
        name=config.name,
        status=status,
        active_version=active_version,
        greeting=config.greeting,
        persona=config.persona,
        primary_language=config.language.primary,
        fallback_languages=config.language.fallbacks,
        voice_id=config.voice.voice_id,
        speech_speed=config.voice.speech_speed,
        llm_provider=config.model.provider,
        llm_model=config.model.model,
        llm_temperature=config.model.temperature,
        handoff_mode=config.handoff.mode,
        handoff_destination=config.handoff.destination,
        record_calls=config.safety.record_calls,
        pii_redaction=config.safety.pii_redaction,
        enabled_tools=config.enabled_tools,
        knowledge_source_ids=config.knowledge_source_ids,
        operating_timezone=config.operating_hours.timezone,
        operating_window=window,
        updated_at=ts,
    )


def project_row(row: Agent) -> AgentProjection:
    """Convert an ``Agent`` ORM row directly into an ``AgentProjection``."""
    cfg = _row_to_agent_config(row)
    try:
        st = AgentStatus(row.status)
    except ValueError:
        st = AgentStatus.DRAFT
    active_ver = int(row.published_version_number or 0)
    return project(cfg, status=st, active_version=active_ver, updated_at=row.updated_at, agent_row=row)


# ---------------------------------------------------------------------------
# Database lookup helpers (scoped by tenant_id, matches UUID id or external_key)
# ---------------------------------------------------------------------------


async def get_agent_row(
    session: AsyncSession,
    tenant_or_id: Tenant | str | uuid.UUID,
    agent_identifier: str | uuid.UUID,
    *,
    for_update: bool = False,
    include_deleted: bool = False,
) -> Agent | None:
    """Load an ``Agent`` row scoped to ``tenant_id`` by UUID ``id`` or ``external_key``."""
    t_uuid = _tenant_uuid(tenant_or_id)
    ident_str = str(agent_identifier).strip()
    ident_uuid = _parse_optional_uuid(ident_str)

    conditions = [Agent.tenant_id == t_uuid]
    if not include_deleted:
        conditions.append(Agent.deleted_at.is_(None))

    if ident_uuid is not None:
        conditions.append(or_(Agent.id == ident_uuid, Agent.external_key == ident_str))
    else:
        conditions.append(Agent.external_key == ident_str)

    stmt = select(Agent).where(*conditions)
    if for_update:
        stmt = stmt.with_for_update()
    res = await session.execute(stmt)
    return res.scalars().first()


# ---------------------------------------------------------------------------
# Validation & offline testing
# ---------------------------------------------------------------------------


def validate_config(config: AgentConfig) -> list[str]:
    """Return a list of human-readable validation errors (empty when valid)."""
    return config.validate()


def test_configuration(
    tenant: Tenant,
    config: AgentConfig,
    *,
    sample_utterance: str = "I'd like to book an appointment tomorrow morning.",
) -> dict[str, Any]:
    """Run an offline readiness check against ``config`` without calling providers."""
    if config.tenant_id != str(tenant.id):
        raise ValueError("AgentConfig.tenant_id does not match Tenant.id")
    problems = config.validate()
    checks = {
        "schema_valid": len(problems) == 0,
        "greeting_present": bool(config.greeting.strip()),
        "voice_configured": bool(config.voice.voice_id or tenant.voice_id),
        "handoff_reachable": (
            config.handoff.mode is HandoffMode.NONE or bool(config.handoff.destination)
        ),
        "tools_valid": all(t in ALLOWED_TOOLS for t in config.enabled_tools),
        "operating_hours_valid": config.operating_hours.start < config.operating_hours.end,
        "sample_utterance_chars": len(sample_utterance),
    }
    return {
        "ok": all(
            v if isinstance(v, bool) else True
            for k, v in checks.items()
            if k != "voice_configured"
        ),
        "agent_id": config.id,
        "config_hash": config.canonical_hash(),
        "problems": problems,
        "checks": checks,
    }


# ---------------------------------------------------------------------------
# Durable CRUD, Draft Save (ETag / 409), Validate, Publish (FOR UPDATE), Rollback
# ---------------------------------------------------------------------------


async def create_draft_async(
    session: AsyncSession,
    tenant: Tenant,
    config: AgentConfig,
    *,
    description: str = "",
    agent_type: str = "voice",
    environment_id: uuid.UUID | str | None = None,
    actor: str | uuid.UUID | None = "system",
) -> Agent:
    """Create or update a durable draft ``Agent`` row in PostgreSQL."""
    if config.tenant_id != str(tenant.id):
        raise ValueError("AgentConfig.tenant_id does not match Tenant.id")
    problems = config.validate()
    if problems:
        raise ValueError("Invalid AgentConfig: " + "; ".join(problems))

    t_uuid = _tenant_uuid(tenant)
    env_uuid = _parse_optional_uuid(environment_id)
    ext_key = config.id or stable_id(str(t_uuid), config.name)

    existing = await get_agent_row(
        session, t_uuid, ext_key, for_update=True, include_deleted=False
    )
    now = _utcnow()
    snapshot = merge_config_and_builder_snapshot(
        config,
        existing.current_draft_config if existing else None,
        description=description,
    )
    etag = compute_config_etag(snapshot)

    if existing is not None:
        if existing.status in {
            AgentLifecycleStatus.ARCHIVED.value,
            AgentLifecycleStatus.RETIRED.value,
        }:
            raise ValueError(f"Cannot modify draft for {existing.status} agent {ext_key}")
        existing.name = config.name.strip()
        if description:
            existing.description = description
        existing.current_draft_config = snapshot
        existing.draft_etag = etag
        existing.validation_status = AgentValidationStatus.VALID.value
        existing.validation_errors = []
        existing.lock_version = (existing.lock_version or 1) + 1
        existing.updated_by_user_id = _parse_optional_uuid(actor)
        existing.updated_at = now
        await session.flush()
        await _emit_audit(
            session,
            tenant_id=t_uuid,
            action="agent.draft.updated",
            agent_id=str(existing.id),
            actor=actor,
            environment_id=existing.environment_id,
            details={"external_key": ext_key, "draft_etag": etag},
        )
        return existing

    agent_row = Agent(
        id=uuid.uuid4(),
        tenant_id=t_uuid,
        environment_id=env_uuid,
        external_key=ext_key,
        name=config.name.strip(),
        description=description,
        agent_type=agent_type,
        status=AgentLifecycleStatus.DRAFT.value,
        current_draft_config=snapshot,
        draft_etag=etag,
        published_version_id=None,
        published_version_number=None,
        validation_status=AgentValidationStatus.VALID.value,
        validation_errors=[],
        lock_version=1,
        created_by_user_id=_parse_optional_uuid(actor),
        updated_by_user_id=_parse_optional_uuid(actor),
        created_at=now,
        updated_at=now,
        meta={},
    )
    session.add(agent_row)
    await session.flush()
    await _emit_audit(
        session,
        tenant_id=t_uuid,
        action="agent.created",
        agent_id=str(agent_row.id),
        actor=actor,
        environment_id=env_uuid,
        details={"external_key": ext_key, "name": agent_row.name, "draft_etag": etag},
    )
    return agent_row


async def create_agent_from_builder_async(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    *,
    name: str,
    description: str = "",
    agent_type: str = "voice",
    environment_id: uuid.UUID | str | None = None,
    initial_config: dict[str, Any] | None = None,
    actor: str | uuid.UUID | None = None,
) -> Agent:
    """Create a durable ``Agent`` row initialized with builder configuration."""
    t_uuid = _tenant_uuid(tenant_id)
    cleaned_name = (name or "").strip()
    if not cleaned_name or len(cleaned_name) > 80:
        raise BadRequestError("Agent name must be 1..80 non-whitespace characters")

    ext_key = stable_id(str(t_uuid), cleaned_name)
    # Check if external_key already exists for this tenant; if so, append short uuid suffix
    existing = await get_agent_row(session, t_uuid, ext_key, include_deleted=True)
    if existing is not None:
        ext_key = f"{ext_key}_{uuid.uuid4().hex[:6]}"

    snapshot = default_builder_config(name=cleaned_name, description=description)
    if isinstance(initial_config, dict):
        for k, v in initial_config.items():
            if isinstance(v, dict) and isinstance(snapshot.get(k), dict):
                snapshot[k].update(v)
            else:
                snapshot[k] = v
        snapshot["name"] = cleaned_name
        if isinstance(snapshot.get("identity"), dict):
            snapshot["identity"]["name"] = cleaned_name
            snapshot["identity"]["description"] = description

    errors, _warnings = validate_snapshot_dict(
        snapshot, tenant_id=str(t_uuid), agent_name=cleaned_name
    )
    raw_secret_errors = [e for e in errors if e["code"] == "RAW_SECRET_FORBIDDEN"]
    if raw_secret_errors:
        raise BadRequestError(raw_secret_errors[0]["message"])

    now = _utcnow()
    etag = compute_config_etag(snapshot)
    env_uuid = _parse_optional_uuid(environment_id)
    actor_uuid = _parse_optional_uuid(actor)

    agent_row = Agent(
        id=uuid.uuid4(),
        tenant_id=t_uuid,
        environment_id=env_uuid,
        external_key=ext_key,
        name=cleaned_name,
        description=description,
        agent_type=agent_type or "voice",
        status=AgentLifecycleStatus.DRAFT.value,
        current_draft_config=snapshot,
        draft_etag=etag,
        published_version_id=None,
        published_version_number=None,
        validation_status=(
            AgentValidationStatus.VALID.value
            if not errors
            else AgentValidationStatus.INVALID.value
        ),
        validation_errors=errors,
        lock_version=1,
        created_by_user_id=actor_uuid,
        updated_by_user_id=actor_uuid,
        created_at=now,
        updated_at=now,
        meta={},
    )
    session.add(agent_row)
    await session.flush()
    await _emit_audit(
        session,
        tenant_id=t_uuid,
        action="agent.created",
        agent_id=str(agent_row.id),
        actor=actor,
        environment_id=env_uuid,
        details={"external_key": ext_key, "name": cleaned_name, "draft_etag": etag},
    )
    return agent_row


async def update_draft_async(
    session: AsyncSession,
    tenant: Tenant,
    config: AgentConfig,
    *,
    agent_identifier: str | uuid.UUID | None = None,
    expected_etag: str | None = None,
    description: str | None = None,
    actor: str | uuid.UUID | None = "system",
) -> Agent:
    """Update an existing durable draft ``Agent`` or create one if none exists yet."""
    if config.tenant_id != str(tenant.id):
        raise ValueError("AgentConfig.tenant_id does not match Tenant.id")
    problems = config.validate()
    if problems:
        raise ValueError("Invalid AgentConfig: " + "; ".join(problems))

    t_uuid = _tenant_uuid(tenant)
    lookup_key = str(agent_identifier or config.id)
    row = await get_agent_row(session, t_uuid, lookup_key, for_update=True)
    if row is None:
        return await create_draft_async(
            session,
            tenant,
            config,
            description=description or "",
            actor=actor,
        )

    if row.status in {
        AgentLifecycleStatus.ARCHIVED.value,
        AgentLifecycleStatus.RETIRED.value,
    }:
        raise ValueError(f"Cannot update draft for {row.status} agent {lookup_key}")

    if expected_etag is not None and expected_etag.strip():
        if row.draft_etag and expected_etag.strip() != row.draft_etag:
            raise ConflictError(
                f"Draft ETag mismatch: expected {expected_etag!r}, current is {row.draft_etag!r}"
            )

    now = _utcnow()
    snapshot = merge_config_and_builder_snapshot(
        config,
        row.current_draft_config,
        description=description if description is not None else row.description,
    )
    etag = compute_config_etag(snapshot)
    row.name = config.name.strip()
    if description is not None:
        row.description = description
    row.current_draft_config = snapshot
    row.draft_etag = etag
    row.validation_status = AgentValidationStatus.VALID.value
    row.validation_errors = []
    row.lock_version = (row.lock_version or 1) + 1
    row.updated_by_user_id = _parse_optional_uuid(actor)
    row.updated_at = now
    await session.flush()

    await _emit_audit(
        session,
        tenant_id=t_uuid,
        action="agent.draft.updated",
        agent_id=str(row.id),
        actor=actor,
        environment_id=row.environment_id,
        details={"external_key": row.external_key, "draft_etag": etag, "lock_version": row.lock_version},
    )
    return row


async def save_builder_draft_async(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    agent_identifier: str | uuid.UUID,
    patch_updates: dict[str, Any],
    *,
    expected_etag: str | None = None,
    actor: str | uuid.UUID | None = None,
) -> Agent:
    """Apply partial builder configuration updates to a durable ``Agent`` row with ETag concurrency control."""
    t_uuid = _tenant_uuid(tenant_id)
    row = await get_agent_row(session, t_uuid, agent_identifier, for_update=True)
    if row is None:
        raise NotFoundError("Agent not found")

    if row.status in {
        AgentLifecycleStatus.ARCHIVED.value,
        AgentLifecycleStatus.RETIRED.value,
    }:
        raise BadRequestError(f"Cannot edit draft of {row.status} agent")

    if expected_etag is not None and expected_etag.strip():
        if row.draft_etag and expected_etag.strip() != row.draft_etag:
            raise ConflictError(
                "Conflict: Draft was modified by another session. Reload the latest configuration before saving."
            )

    secret_offenders = find_secret_like_keys(patch_updates)
    if secret_offenders:
        raise BadRequestError(
            f"Configuration must not contain raw secret or credential fields: {sorted(secret_offenders)}"
        )

    snapshot = copy.deepcopy(
        row.current_draft_config
        if isinstance(row.current_draft_config, dict) and row.current_draft_config
        else default_builder_config(name=row.name, description=row.description)
    )

    for section, val in patch_updates.items():
        if val is None:
            continue
        if section in {"identity", "voice", "model", "call_handling", "security", "language", "operating_hours", "handoff", "safety"} and isinstance(val, dict):
            existing_sec = dict(snapshot.get(section) or {})
            existing_sec.update(val)
            snapshot[section] = existing_sec
        else:
            snapshot[section] = val

    # Keep top-level and nested builder fields synchronized
    identity = dictionary_value(snapshot.get("identity"))
    if identity.get("name"):
        row.name = str(identity["name"]).strip()
        snapshot["name"] = row.name
    elif snapshot.get("name"):
        row.name = str(snapshot["name"]).strip()
        if isinstance(snapshot.get("identity"), dict):
            snapshot["identity"]["name"] = row.name

    if "description" in identity and identity["description"] is not None:
        row.description = str(identity["description"])
    if "greeting" in identity:
        snapshot["greeting"] = identity["greeting"]
    if "persona" in identity:
        snapshot["persona"] = identity["persona"]

    voice = dictionary_value(snapshot.get("voice"))
    if "speed" in voice:
        voice["speech_speed"] = voice["speed"]
    elif "speech_speed" in voice:
        voice["speed"] = voice["speech_speed"]
    if "language" in voice:
        lang_block = dict(snapshot.get("language") or {})
        lang_block["primary"] = voice["language"]
        snapshot["language"] = lang_block

    model = dictionary_value(snapshot.get("model"))
    if "model_name" in model:
        model["model"] = model["model_name"]
    elif "model" in model:
        model["model_name"] = model["model"]
    if "system_prompt" in model:
        snapshot["system_prompt"] = model["system_prompt"]

    if isinstance(snapshot.get("knowledge_bases"), list):
        snapshot["knowledge_source_ids"] = [
            str(kb.get("kb_id"))
            for kb in snapshot["knowledge_bases"]
            if isinstance(kb, dict) and kb.get("kb_id")
        ]

    errors, _warnings = validate_snapshot_dict(
        snapshot, tenant_id=str(t_uuid), agent_name=row.name
    )
    now = _utcnow()
    new_etag = compute_config_etag(snapshot)
    row.current_draft_config = snapshot
    row.draft_etag = new_etag
    row.validation_status = (
        AgentValidationStatus.VALID.value
        if not errors
        else AgentValidationStatus.INVALID.value
    )
    row.validation_errors = errors
    row.lock_version = (row.lock_version or 1) + 1
    row.updated_by_user_id = _parse_optional_uuid(actor)
    row.updated_at = now
    await session.flush()

    await _emit_audit(
        session,
        tenant_id=t_uuid,
        action="agent.draft.updated",
        agent_id=str(row.id),
        actor=actor,
        environment_id=row.environment_id,
        details={
            "external_key": row.external_key,
            "draft_etag": new_etag,
            "lock_version": row.lock_version,
            "validation_status": row.validation_status,
        },
    )
    return row


async def validate_agent_async(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    agent_identifier: str | uuid.UUID,
    *,
    actor: str | uuid.UUID | None = None,
) -> dict[str, Any]:
    """Validate the durable draft configuration of an ``Agent`` and persist validation status."""
    t_uuid = _tenant_uuid(tenant_id)
    row = await get_agent_row(session, t_uuid, agent_identifier, for_update=True)
    if row is None:
        raise NotFoundError("Agent not found")

    snapshot = (
        row.current_draft_config
        if isinstance(row.current_draft_config, dict) and row.current_draft_config
        else default_builder_config(name=row.name, description=row.description)
    )
    errors, warnings = validate_snapshot_dict(
        snapshot, tenant_id=str(t_uuid), agent_name=row.name
    )
    is_valid = len(errors) == 0
    now = _utcnow()
    row.validation_status = (
        AgentValidationStatus.VALID.value
        if is_valid
        else AgentValidationStatus.INVALID.value
    )
    row.validation_errors = errors
    row.updated_at = now
    await session.flush()

    await _emit_audit(
        session,
        tenant_id=t_uuid,
        action="agent.validated",
        agent_id=str(row.id),
        actor=actor,
        environment_id=row.environment_id,
        details={"valid": is_valid, "error_count": len(errors), "warning_count": len(warnings)},
    )
    return {
        "valid": is_valid,
        "errors": errors,
        "warnings": warnings,
        "checked_at": now.isoformat(),
    }


async def get_draft_async(
    session: AsyncSession,
    tenant: Tenant | str | uuid.UUID,
    agent_id: str | uuid.UUID,
) -> AgentConfig | None:
    """Return the durable draft ``AgentConfig`` for ``(tenant, agent_id)`` or ``None``."""
    row = await get_agent_row(session, tenant, agent_id)
    if row is None:
        return None
    return _row_to_agent_config(row)


async def list_agents_async(
    session: AsyncSession,
    tenant: Tenant,
    *,
    include_archived: bool = False,
    ensure_default: bool = True,
    environment_id: uuid.UUID | str | None = None,
) -> list[AgentProjection]:
    """Return projections for every durable agent belonging to ``tenant``.

    If the tenant has not created any explicit ``Agent`` rows yet, returns a
    single projection synthesized from the ``Tenant`` row so the UI is never
    blank on a brand-new workspace.
    """
    t_uuid = _tenant_uuid(tenant)
    conditions = [Agent.tenant_id == t_uuid, Agent.deleted_at.is_(None)]
    if not include_archived:
        conditions.append(Agent.status != AgentLifecycleStatus.ARCHIVED.value)
    env_uuid = _parse_optional_uuid(environment_id)
    if env_uuid is not None:
        conditions.append(Agent.environment_id == env_uuid)

    stmt = select(Agent).where(*conditions).order_by(Agent.updated_at.desc(), Agent.created_at.desc())
    res = await session.execute(stmt)
    rows = res.scalars().all()
    if rows:
        return [project_row(r) for r in rows]
    if not ensure_default:
        return []

    default_cfg = from_tenant(tenant)
    return [project(default_cfg, status=AgentStatus.PUBLISHED, active_version=1)]


async def version_history_async(
    session: AsyncSession,
    tenant: Tenant | str | uuid.UUID,
    agent_id: str | uuid.UUID,
) -> list[AgentVersion]:
    """Return published ``AgentVersion`` records newest-first (empty if none)."""
    row = await get_agent_row(session, tenant, agent_id, include_deleted=True)
    if row is None:
        return []
    t_uuid = _tenant_uuid(tenant)
    stmt = (
        select(AgentVersionRow)
        .where(
            AgentVersionRow.tenant_id == t_uuid,
            AgentVersionRow.agent_id == row.id,
        )
        .order_by(AgentVersionRow.version_number.desc())
    )
    res = await session.execute(stmt)
    ver_rows = res.scalars().all()
    return [_version_row_to_domain(v, external_key=row.external_key) for v in ver_rows]


async def get_version_async(
    session: AsyncSession,
    tenant: Tenant | str | uuid.UUID,
    agent_id: str | uuid.UUID,
    version_number: int,
) -> AgentVersion | None:
    """Return a specific immutable ``AgentVersion`` by version number or ``None``."""
    row = await get_agent_row(session, tenant, agent_id, include_deleted=True)
    if row is None:
        return None
    t_uuid = _tenant_uuid(tenant)
    stmt = select(AgentVersionRow).where(
        AgentVersionRow.tenant_id == t_uuid,
        AgentVersionRow.agent_id == row.id,
        AgentVersionRow.version_number == int(version_number),
    )
    res = await session.execute(stmt)
    ver_row = res.scalars().first()
    if ver_row is None:
        return None
    return _version_row_to_domain(ver_row, external_key=row.external_key)


async def get_version_config_async(
    session: AsyncSession,
    tenant: Tenant | str | uuid.UUID,
    agent_id: str | uuid.UUID,
    version_number: int,
) -> dict[str, Any] | None:
    """Return a deep-copied configuration snapshot for ``(tenant, agent_id, version_number)``."""
    ver = await get_version_async(session, tenant, agent_id, version_number)
    if ver is None:
        return None
    if ver.config_snapshot:
        return copy.deepcopy(ver.config_snapshot)
    return ver.config.to_snapshot_dict()


async def publish_async(
    session: AsyncSession,
    tenant: Tenant,
    config: AgentConfig | None = None,
    *,
    agent_identifier: str | uuid.UUID | None = None,
    agent_id: str | uuid.UUID | None = None,
    actor: str | uuid.UUID | None = "system",
    changelog: str = "",
    environment: str = "production",
    environment_id: uuid.UUID | str | None = None,
) -> AgentVersion:
    """Validate and publish an ``Agent``, minting an immutable ``AgentVersion`` row in PostgreSQL.

    Acquires a ``SELECT ... FOR UPDATE`` lock on the ``Agent`` row to serialize
    concurrent publishes for the same agent, computes ``MAX(version_number) + 1``,
    marks prior active versions for the target environment as ``superseded``,
    writes the new ``AgentVersion`` row, updates ``Agent``, projects runtime
    columns onto ``Tenant``, and records an audit event.
    """
    t_uuid = _tenant_uuid(tenant)
    lookup_key = str(agent_identifier or agent_id or (config.id if config else ""))
    if not lookup_key:
        raise ValueError("agent_identifier or config is required to publish")

    agent_row = await get_agent_row(session, t_uuid, lookup_key, for_update=True)
    if agent_row is None:
        if config is None:
            raise LookupError(f"Agent {lookup_key!r} not found for tenant {tenant.id}")
        agent_row = await create_draft_async(
            session,
            tenant,
            config,
            environment_id=environment_id,
            actor=actor,
        )

    if agent_row.deleted_at is not None or agent_row.status in {
        AgentLifecycleStatus.ARCHIVED.value,
        AgentLifecycleStatus.RETIRED.value,
    }:
        raise ValueError(f"Agent {lookup_key} is {agent_row.status} and cannot be published")

    try:
        current_status = AgentStatus(agent_row.status)
    except ValueError:
        current_status = AgentStatus.DRAFT

    if not can_transition(current_status, AgentStatus.PUBLISHED):
        raise ValueError(f"Agent {lookup_key} is {current_status.value} and cannot be published")

    if config is not None:
        if config.tenant_id != str(tenant.id):
            raise ValueError("AgentConfig.tenant_id does not match Tenant.id")
        problems = config.validate()
        if problems:
            raise ValueError("Cannot publish invalid AgentConfig: " + "; ".join(problems))
        snapshot = merge_config_and_builder_snapshot(
            config,
            agent_row.current_draft_config,
            description=agent_row.description,
        )
        effective_config = config
    else:
        snapshot = (
            copy.deepcopy(agent_row.current_draft_config)
            if isinstance(agent_row.current_draft_config, dict) and agent_row.current_draft_config
            else default_builder_config(name=agent_row.name, description=agent_row.description)
        )
        errors, _warnings = validate_snapshot_dict(
            snapshot, tenant_id=str(t_uuid), agent_name=agent_row.name
        )
        if errors:
            msg = "; ".join(f"{e['field']}: {e['message']}" for e in errors)
            raise ValueError(f"Cannot publish invalid AgentConfig: {msg}")
        effective_config = AgentConfig.from_snapshot_dict(
            snapshot,
            tenant_id=str(t_uuid),
            fallback_name=agent_row.name,
            agent_id=agent_row.external_key,
        )
        problems = effective_config.validate()
        if problems:
            raise ValueError("Cannot publish invalid AgentConfig: " + "; ".join(problems))

    # Compute next monotonic version_number under the Agent row lock + async mutex
    async with _get_publish_lock(f"{t_uuid}:{agent_row.id}"):
        max_stmt = select(func.coalesce(func.max(AgentVersionRow.version_number), 0)).where(
            AgentVersionRow.agent_id == agent_row.id
        )
        max_res = await session.execute(max_stmt)
        next_version = int(max_res.scalar_one() or 0) + 1

        env_name = (environment or "production").strip().lower()
        env_uuid = _parse_optional_uuid(environment_id) or agent_row.environment_id

        # Mark previously active versions for this (agent_id, published_environment) as superseded
        prev_stmt = select(AgentVersionRow).where(
            AgentVersionRow.agent_id == agent_row.id,
            AgentVersionRow.published_environment == env_name,
            AgentVersionRow.is_active.is_(True),
        )
        prev_res = await session.execute(prev_stmt)
        for prev_ver in prev_res.scalars().all():
            prev_ver.is_active = False
            if prev_ver.status == AgentVersionStatus.PUBLISHED.value:
                prev_ver.status = AgentVersionStatus.SUPERSEDED.value

        now = _utcnow()
        cfg_hash = effective_config.canonical_hash()
        actor_uuid = _parse_optional_uuid(actor)

        ver_row = AgentVersionRow(
            id=uuid.uuid4(),
            tenant_id=t_uuid,
            agent_id=agent_row.id,
            version_number=next_version,
            version_label=f"v{next_version}",
            status=AgentVersionStatus.PUBLISHED.value,
            config_snapshot=copy.deepcopy(snapshot),
            config_hash=cfg_hash,
            changelog=(changelog or "")[:500],
            published_environment_id=env_uuid,
            published_environment=env_name,
            source_version_id=None,
            is_rollback=False,
            is_active=True,
            published_by_user_id=actor_uuid,
            published_at=now,
            created_at=now,
            meta={"published_by": str(actor or "system")},
        )
        session.add(ver_row)
        await session.flush()

        new_etag = compute_config_etag(snapshot)
        agent_row.name = effective_config.name.strip()
        agent_row.status = AgentLifecycleStatus.PUBLISHED.value
        agent_row.current_draft_config = snapshot
        agent_row.draft_etag = new_etag
        agent_row.published_version_id = ver_row.id
        agent_row.published_version_number = next_version
        agent_row.validation_status = AgentValidationStatus.VALID.value
        agent_row.validation_errors = []
        agent_row.lock_version = (agent_row.lock_version or 1) + 1
        agent_row.updated_by_user_id = actor_uuid
        agent_row.updated_at = now

        _apply_to_tenant(tenant, effective_config)
        await session.flush()

    await _emit_audit(
        session,
        tenant_id=t_uuid,
        action="agent.published",
        agent_id=str(agent_row.id),
        actor=actor,
        environment_id=env_uuid,
        reason=changelog or None,
        details={
            "external_key": agent_row.external_key,
            "version": next_version,
            "version_id": str(ver_row.id),
            "config_hash": cfg_hash,
            "environment": env_name,
        },
    )
    return _version_row_to_domain(ver_row, external_key=agent_row.external_key)


async def rollback_async(
    session: AsyncSession,
    tenant: Tenant,
    agent_id: str | uuid.UUID,
    target_version: int,
    *,
    actor: str | uuid.UUID | None = "system",
    reason: str = "",
) -> AgentVersion:
    """Roll ``agent_id`` back to ``target_version`` by minting a new immutable version.

    History stays append-only: rolling back from v3 to v1 creates v4 whose
    ``config_snapshot`` equals v1's configuration and sets ``is_rollback=True``
    and ``source_version_id=v1.id``.
    """
    t_uuid = _tenant_uuid(tenant)
    agent_row = await get_agent_row(session, t_uuid, agent_id, for_update=True)
    if agent_row is None:
        raise LookupError(f"No version history for agent {agent_id!r}")

    if agent_row.deleted_at is not None or agent_row.status in {
        AgentLifecycleStatus.ARCHIVED.value,
        AgentLifecycleStatus.RETIRED.value,
    }:
        raise ValueError(f"Cannot rollback {agent_row.status} agent {agent_id}")

    target_stmt = select(AgentVersionRow).where(
        AgentVersionRow.tenant_id == t_uuid,
        AgentVersionRow.agent_id == agent_row.id,
        AgentVersionRow.version_number == int(target_version),
    )
    target_res = await session.execute(target_stmt)
    target_row = target_res.scalars().first()
    if target_row is None:
        raise LookupError(f"Version {target_version} not found for agent {agent_id!r}")

    max_stmt = select(func.coalesce(func.max(AgentVersionRow.version_number), 0)).where(
        AgentVersionRow.agent_id == agent_row.id
    )
    max_res = await session.execute(max_stmt)
    next_version = int(max_res.scalar_one() or 0) + 1

    env_name = target_row.published_environment or "production"
    prev_stmt = select(AgentVersionRow).where(
        AgentVersionRow.agent_id == agent_row.id,
        AgentVersionRow.published_environment == env_name,
        AgentVersionRow.is_active.is_(True),
    )
    prev_res = await session.execute(prev_stmt)
    for prev_ver in prev_res.scalars().all():
        prev_ver.is_active = False
        prev_ver.status = AgentVersionStatus.ROLLED_BACK.value

    snapshot = copy.deepcopy(target_row.config_snapshot or {})
    restored_config = AgentConfig.from_snapshot_dict(
        snapshot,
        tenant_id=str(t_uuid),
        fallback_name=agent_row.name,
        agent_id=agent_row.external_key,
    )
    now = _utcnow()
    actor_uuid = _parse_optional_uuid(actor)
    changelog_text = f"rollback to v{target_version}" + (f": {reason}" if reason else "")

    new_ver_row = AgentVersionRow(
        id=uuid.uuid4(),
        tenant_id=t_uuid,
        agent_id=agent_row.id,
        version_number=next_version,
        version_label=f"v{next_version}",
        status=AgentVersionStatus.PUBLISHED.value,
        config_snapshot=snapshot,
        config_hash=target_row.config_hash or restored_config.canonical_hash(),
        changelog=changelog_text[:500],
        published_environment_id=target_row.published_environment_id or agent_row.environment_id,
        published_environment=env_name,
        source_version_id=target_row.id,
        is_rollback=True,
        is_active=True,
        published_by_user_id=actor_uuid,
        published_at=now,
        created_at=now,
        meta={
            "published_by": str(actor or "system"),
            "rolled_back_from_version": int(target_version),
        },
    )
    session.add(new_ver_row)
    await session.flush()

    new_etag = compute_config_etag(snapshot)
    agent_row.name = restored_config.name.strip()
    agent_row.status = AgentLifecycleStatus.PUBLISHED.value
    agent_row.current_draft_config = snapshot
    agent_row.draft_etag = new_etag
    agent_row.published_version_id = new_ver_row.id
    agent_row.published_version_number = next_version
    agent_row.validation_status = AgentValidationStatus.VALID.value
    agent_row.validation_errors = []
    agent_row.lock_version = (agent_row.lock_version or 1) + 1
    agent_row.updated_by_user_id = actor_uuid
    agent_row.updated_at = now

    _apply_to_tenant(tenant, restored_config)
    await session.flush()

    await _emit_audit(
        session,
        tenant_id=t_uuid,
        action="agent.rolled_back",
        agent_id=str(agent_row.id),
        actor=actor,
        environment_id=agent_row.environment_id,
        reason=changelog_text,
        details={
            "external_key": agent_row.external_key,
            "target_version": int(target_version),
            "new_version": next_version,
            "source_version_id": str(target_row.id),
        },
    )
    return _version_row_to_domain(new_ver_row, external_key=agent_row.external_key)


async def configure_tools_async(
    session: AsyncSession,
    tenant: Tenant,
    agent_id: str | uuid.UUID,
    enabled: tuple[str, ...] | list[str],
    *,
    actor: str | uuid.UUID | None = "system",
) -> AgentConfig:
    """Replace the enabled tool set on a durable agent draft after allow-list validation."""
    unknown = [t for t in enabled if t not in ALLOWED_TOOLS]
    if unknown:
        raise ValueError(f"Unsupported tools: {sorted(unknown)}")
    row = await get_agent_row(session, tenant, agent_id, for_update=True)
    if row is None:
        raise LookupError(f"Agent {agent_id!r} not found")
    current_cfg = _row_to_agent_config(row)
    updated_cfg = replace(current_cfg, enabled_tools=tuple(dict.fromkeys(enabled)))
    await update_draft_async(
        session, tenant, updated_cfg, agent_identifier=row.id, actor=actor
    )
    return updated_cfg


async def attach_knowledge_sources_async(
    session: AsyncSession,
    tenant: Tenant,
    agent_id: str | uuid.UUID,
    source_ids: tuple[str, ...] | list[str],
    *,
    actor: str | uuid.UUID | None = "system",
) -> AgentConfig:
    """Bind knowledge base source IDs to a durable agent draft."""
    cleaned = tuple(dict.fromkeys(s.strip() for s in source_ids if s and s.strip()))
    if len(cleaned) > 50:
        raise ValueError("At most 50 knowledge sources may be attached to an agent")
    row = await get_agent_row(session, tenant, agent_id, for_update=True)
    if row is None:
        raise LookupError(f"Agent {agent_id!r} not found")
    current_cfg = _row_to_agent_config(row)
    updated_cfg = replace(current_cfg, knowledge_source_ids=cleaned)
    await update_draft_async(
        session, tenant, updated_cfg, agent_identifier=row.id, actor=actor
    )
    return updated_cfg


async def retire_async(
    session: AsyncSession,
    tenant: Tenant,
    agent_id: str | uuid.UUID,
    *,
    actor: str | uuid.UUID | None = "system",
    reason: str = "",
) -> None:
    """Mark an agent as retired in PostgreSQL (terminal state — cannot be republished)."""
    t_uuid = _tenant_uuid(tenant)
    row = await get_agent_row(session, t_uuid, agent_id, for_update=True)
    if row is None:
        raise LookupError(f"Agent {agent_id!r} not found")
    try:
        current = AgentStatus(row.status)
    except ValueError:
        current = AgentStatus.DRAFT
    if not can_transition(current, AgentStatus.RETIRED):
        raise ValueError(f"Cannot transition agent from {current.value} to retired")

    now = _utcnow()
    row.status = AgentLifecycleStatus.RETIRED.value
    row.archived_at = now
    row.archived_reason = (reason or "retired")[:500]
    row.lock_version = (row.lock_version or 1) + 1
    row.updated_by_user_id = _parse_optional_uuid(actor)
    row.updated_at = now
    await session.flush()

    await _emit_audit(
        session,
        tenant_id=t_uuid,
        action="agent.retired",
        agent_id=str(row.id),
        actor=actor,
        environment_id=row.environment_id,
        reason=reason or "retired",
        details={"external_key": row.external_key},
    )


async def archive_agent_async(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    agent_id: str | uuid.UUID,
    *,
    reason: str = "",
    actor: str | uuid.UUID | None = None,
) -> Agent:
    """Archive an agent in PostgreSQL."""
    t_uuid = _tenant_uuid(tenant_id)
    row = await get_agent_row(session, t_uuid, agent_id, for_update=True)
    if row is None:
        raise NotFoundError("Agent not found")
    if row.status == AgentLifecycleStatus.ARCHIVED.value:
        raise ConflictError("Agent is already archived")

    now = _utcnow()
    row.status = AgentLifecycleStatus.ARCHIVED.value
    row.archived_at = now
    row.archived_reason = (reason or "")[:500]
    row.lock_version = (row.lock_version or 1) + 1
    row.updated_by_user_id = _parse_optional_uuid(actor)
    row.updated_at = now
    await session.flush()

    await _emit_audit(
        session,
        tenant_id=t_uuid,
        action="agent.archived",
        agent_id=str(row.id),
        actor=actor,
        environment_id=row.environment_id,
        reason=reason or None,
        details={"external_key": row.external_key},
    )
    return row


async def restore_agent_async(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    agent_id: str | uuid.UUID,
    *,
    actor: str | uuid.UUID | None = None,
) -> Agent:
    """Restore an archived agent back to draft or published state in PostgreSQL."""
    t_uuid = _tenant_uuid(tenant_id)
    row = await get_agent_row(session, t_uuid, agent_id, for_update=True)
    if row is None:
        raise NotFoundError("Agent not found")
    if row.status != AgentLifecycleStatus.ARCHIVED.value:
        raise BadRequestError("Agent is not archived")

    now = _utcnow()
    row.status = (
        AgentLifecycleStatus.PUBLISHED.value
        if row.published_version_number
        else AgentLifecycleStatus.DRAFT.value
    )
    row.archived_at = None
    row.archived_reason = ""
    row.lock_version = (row.lock_version or 1) + 1
    row.updated_by_user_id = _parse_optional_uuid(actor)
    row.updated_at = now
    await session.flush()

    await _emit_audit(
        session,
        tenant_id=t_uuid,
        action="agent.restored",
        agent_id=str(row.id),
        actor=actor,
        environment_id=row.environment_id,
        details={"external_key": row.external_key, "status": row.status},
    )
    return row


async def delete_agent_async(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    agent_id: str | uuid.UUID,
    *,
    actor: str | uuid.UUID | None = None,
) -> Agent:
    """Soft-delete an agent in PostgreSQL by setting ``deleted_at``."""
    t_uuid = _tenant_uuid(tenant_id)
    row = await get_agent_row(session, t_uuid, agent_id, for_update=True)
    if row is None:
        raise NotFoundError("Agent not found")

    now = _utcnow()
    row.deleted_at = now
    row.status = AgentLifecycleStatus.ARCHIVED.value
    row.lock_version = (row.lock_version or 1) + 1
    row.updated_by_user_id = _parse_optional_uuid(actor)
    row.updated_at = now
    await session.flush()

    await _emit_audit(
        session,
        tenant_id=t_uuid,
        action="agent.deleted",
        agent_id=str(row.id),
        actor=actor,
        environment_id=row.environment_id,
        details={"external_key": row.external_key},
    )
    return row


async def clone_agent_async(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    source_agent_id: str | uuid.UUID,
    *,
    new_name: str,
    include_knowledge_bases: bool = True,
    include_tools: bool = True,
    actor: str | uuid.UUID | None = None,
) -> Agent:
    """Clone an existing durable ``Agent`` into a new draft ``Agent`` row."""
    t_uuid = _tenant_uuid(tenant_id)
    source_row = await get_agent_row(session, t_uuid, source_agent_id)
    snapshot = (
        copy.deepcopy(source_row.current_draft_config)
        if source_row and isinstance(source_row.current_draft_config, dict)
        else default_builder_config(name=new_name)
    )
    if not include_knowledge_bases:
        snapshot["knowledge_bases"] = []
        snapshot["knowledge_source_ids"] = []
    if not include_tools:
        snapshot["tools"] = []
        snapshot["enabled_tools"] = []

    cloned = await create_agent_from_builder_async(
        session,
        t_uuid,
        name=new_name,
        description=source_row.description if source_row else "",
        agent_type=source_row.agent_type if source_row else "voice",
        environment_id=source_row.environment_id if source_row else None,
        initial_config=snapshot,
        actor=actor,
    )
    if source_row is not None:
        meta = dict(cloned.meta or {})
        meta["cloned_from_agent_id"] = str(source_row.id)
        cloned.meta = meta
        await session.flush()
    return cloned


async def resolve_pinned_agent_version_async(
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    agent_id: str | uuid.UUID,
    version_number: int,
    *,
    agent_kind: str = "voice",
) -> dict[str, Any]:
    """Resolve an exact, immutable Agent Version snapshot for testing/simulation.

    Never floats to "latest" or draft configuration. Raises ``NotFoundError``
    if the agent or the pinned ``version_number`` does not exist for ``tenant_id``.
    Supports both Voice agents (``agents`` / ``agent_versions``) and Chat agents
    (``chat_agents`` / ``chat_agent_versions``).
    """
    from app.db.retell_models import ChatAgent, ChatAgentVersion

    t_uuid = _tenant_uuid(tenant_id)
    v_num = int(version_number)
    if v_num < 1:
        raise BadRequestError(f"Pinned version_number must be >= 1 (got {version_number}).")

    kind_norm = (agent_kind or "voice").strip().lower()

    # Try Voice Agent first unless explicitly chat
    if kind_norm != "chat":
        agent_row = await get_agent_row(session, t_uuid, agent_id)
        if agent_row is not None:
            ver_stmt = select(AgentVersionRow).where(
                AgentVersionRow.tenant_id == t_uuid,
                AgentVersionRow.agent_id == agent_row.id,
                AgentVersionRow.version_number == v_num,
            )
            ver_row = (await session.execute(ver_stmt)).scalars().first()
            if ver_row is None:
                raise NotFoundError(
                    f"Pinned AgentVersion v{v_num} not found for voice agent {agent_id!r}."
                )
            snapshot = copy.deepcopy(ver_row.config_snapshot or {})
            cfg_hash = ver_row.config_hash or compute_config_hash(snapshot)
            model_cfg = dictionary_value(snapshot.get("model"))
            provider = str(
                snapshot.get("llm_provider")
                or model_cfg.get("provider")
                or "openai"
            )
            model_name = str(
                snapshot.get("llm_model")
                or model_cfg.get("model")
                or "gpt-4o-mini"
            )
            return {
                "agent_id": str(agent_row.id),
                "external_key": agent_row.external_key,
                "agent_kind": "voice",
                "name": agent_row.name,
                "environment_id": agent_row.environment_id,
                "version_id": ver_row.id,
                "version_number": int(ver_row.version_number),
                "config_hash": cfg_hash,
                "config_snapshot": snapshot,
                "provider": provider,
                "model": model_name,
            }

    # Try ChatAgent (either explicitly requested or fallback when UUID matches a ChatAgent)
    parsed_uuid = _parse_optional_uuid(agent_id)
    chat_stmt = select(ChatAgent).where(ChatAgent.tenant_id == t_uuid)
    if parsed_uuid is not None:
        chat_stmt = chat_stmt.where(ChatAgent.id == parsed_uuid)
    else:
        chat_stmt = chat_stmt.where(ChatAgent.name == str(agent_id))
    chat_res = await session.execute(chat_stmt)
    chat_row = chat_res.scalars().first()
    if chat_row is not None:
        cver_stmt = select(ChatAgentVersion).where(
            ChatAgentVersion.tenant_id == t_uuid,
            ChatAgentVersion.chat_agent_id == chat_row.id,
            ChatAgentVersion.version == v_num,
        )
        cver_res = await session.execute(cver_stmt)
        cver_row = cver_res.scalars().first()
        if cver_row is None:
            raise NotFoundError(
                f"Pinned ChatAgentVersion v{v_num} not found for chat agent {agent_id!r}."
            )
        snapshot = copy.deepcopy(cver_row.config or {})
        cfg_hash = compute_config_hash(snapshot)
        provider = str(snapshot.get("llm_provider") or "openai")
        model_name = str(snapshot.get("llm_model") or "gpt-4o-mini")
        return {
            "agent_id": str(chat_row.id),
            "external_key": str(chat_row.id),
            "agent_kind": "chat",
            "name": chat_row.name,
            "environment_id": None,
            "version_id": cver_row.id,
            "version_number": int(cver_row.version),
            "config_hash": cfg_hash,
            "config_snapshot": snapshot,
            "provider": provider,
            "model": model_name,
        }

    raise NotFoundError(f"Agent {agent_id!r} (version v{v_num}) not found for tenant.")


def reset_ephemeral_state() -> None:
    """No-op compatibility hook for legacy test fixtures; state lives in PostgreSQL/SQLite."""
    return None
