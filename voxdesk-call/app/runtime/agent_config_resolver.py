"""Call-time agent & runtime configuration resolver (Sub-Phase 2E).

Resolves a single immutable :class:`RuntimeConfig` for every inbound or outbound
call using the strict precedence hierarchy:

1. **Active Experiment Variant** (Sub-Phase 1F: ``assign_call_to_experiment`` or
   existing ``call.experiment_id`` / ``call.variant_id`` -> loads the variant's
   ``AgentVersion`` and merges ``variant.config_override`` on top of
   ``config_snapshot``).
2. **PhoneNumber Binding** (``phone_numbers`` / ``telephony_phone_numbers``:
   ``inbound_agent_id`` + optional pinned ``inbound_agent_version`` for inbound
   calls; ``outbound_agent_id`` for outbound calls).
3. **Explicit Call Binding** (``call.agent_id`` / ``call.agent_version_id``
   pre-populated by outbound dialer / test suite).
4. **Tenant Default Published Agent** (most recently updated active published
   ``Agent`` belonging to ``call.tenant_id``).
5. **Legacy Tenant Fields Fallback** (``tenant.system_prompt``, ``tenant.greeting``,
   ``tenant.voice_id``, ``tenant.llm_provider``, ``tenant.llm_model``,
   ``tenant.language``, and ``tenant.voice_*`` controls).

Also resolves the bound agent's enabled tools (``AgentTool``, ``ApiTool``,
``McpServer``/``McpTool``) and scoped ``KnowledgeCollection`` document IDs.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import log
from app.db.enterprise_models import (
    AgentTool,
    ExperimentVariant,
    KnowledgeCollection,
    KnowledgeCollectionSource,
)
from app.db.models import (
    Agent,
    AgentVersion,
    ApiTool,
    Call,
    CallDirection,
    McpServer,
    McpTool,
    Tenant,
)
from app.db.telephony_models import TelephonyPhoneNumber
from app.services.experiment_service import assign_call_to_experiment
from app.telephony.number_provisioning import PhoneNumber, PhoneNumberStatus


def _parse_uuid(value: Any) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value).strip())
    except (ValueError, TypeError, AttributeError):
        return None


def _coerce_float(val: Any, default: float) -> float:
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def _coerce_int(val: Any, default: int) -> int:
    try:
        return int(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def _parse_boosted_keywords(raw: Any) -> list[tuple[str, float]]:
    if not raw or not isinstance(raw, list):
        return []
    out: list[tuple[str, float]] = []
    for item in raw:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            word = str(item[0]).strip()
            boost = _coerce_float(item[1], 2.0)
            if word:
                out.append((word, boost))
        elif isinstance(item, dict):
            word = str(item.get("word") or item.get("keyword") or "").strip()
            boost = _coerce_float(item.get("boost") or item.get("intensifier"), 2.0)
            if word:
                out.append((word, boost))
        elif isinstance(item, str) and item.strip():
            raw_str = item.strip()
            if ":" in raw_str:
                w, b = raw_str.rsplit(":", 1)
                out.append((w.strip(), _coerce_float(b, 2.0)))
            else:
                out.append((raw_str, 2.0))
    return out


@dataclass
class RuntimeConfig:
    """Resolved per-call voice runtime configuration."""

    tenant_id: uuid.UUID
    agent_id: uuid.UUID | None = None
    agent_version_id: uuid.UUID | None = None
    agent_version_number: int | None = None
    experiment_id: uuid.UUID | None = None
    variant_id: uuid.UUID | None = None
    source: str = "tenant_fallback"

    # Prompt & Persona
    system_prompt: str = "You are a helpful AI receptionist."
    greeting: str = "Hello, how can I help you today?"
    language: str = "en"
    auto_detect_language: bool = False

    # LLM settings
    llm_provider: str = "openai"
    llm_model: str = "gpt-4o-mini"
    llm_fallback_providers: list[str] = field(default_factory=list)
    temperature: float = 0.6
    max_tokens: int = 300

    # STT settings
    stt_provider: str = "deepgram"
    stt_model: str = "nova-3"
    stt_fallback_providers: list[str] = field(default_factory=list)

    # TTS & Voice settings
    tts_provider: str = "elevenlabs"
    tts_model: str = "eleven_flash_v2_5"
    tts_fallback_providers: list[str] = field(default_factory=list)
    voice_id: str = ""
    voice_settings: dict[str, Any] = field(default_factory=dict)

    # S2S (Speech-to-Speech) settings (2C)
    s2s_enabled: bool = False
    s2s_provider: str | None = None
    s2s_model: str | None = None

    # Turn-Taking, Backchannel & Idle Reminders (2B)
    responsiveness: float = 0.7
    interruption_sensitivity: float = 0.7
    enable_smart_turn: bool = True
    enable_backchannel: bool = False
    backchannel_enabled: bool = False
    backchannel_frequency: float = 0.5
    backchannel_words: list[str] = field(
        default_factory=lambda: ["yeah", "uh-huh", "got it", "mm-hmm", "okay"]
    )
    reminder_trigger_ms: int = 10000
    reminder_timeout_ms: int = 10000
    reminder_max_count: int = 2
    boosted_keywords: list[tuple[str, float]] = field(default_factory=list)
    filler_trigger_ms: int = 0

    # Ambient Audio & Denoise (2G)
    ambient_sound: str | None = None
    ambient_volume: float = 0.12
    denoise_mode: str = "off"

    # Voicemail, IVR & Transfer settings (2D)
    voicemail_detection_enabled: bool = False
    voicemail_action: str = "hangup"
    voicemail_message: str | None = None
    ivr_navigation_enabled: bool = False
    ivr_goal: str | None = None
    transfer_targets: dict[str, str] = field(default_factory=dict)
    warm_transfer_enabled: bool = False

    # Scoped Tools & Knowledge Base (2D / 2E)
    tools: list[dict[str, Any]] = field(default_factory=list)
    knowledge_collection_ids: list[uuid.UUID] = field(default_factory=list)
    knowledge_document_ids: list[uuid.UUID] | None = None
    environment_id: uuid.UUID | None = None

    raw_config: dict[str, Any] = field(default_factory=dict)

    @property
    def denoise_enabled(self) -> bool:
        return str(self.denoise_mode or "").strip().lower() not in {
            "off",
            "none",
            "disabled",
            "false",
            "0",
            "",
        }

    @property
    def custom_tools(self) -> list[dict[str, Any]]:
        return [
            t
            for t in self.tools
            if str(t.get("kind") or "agent_tool") in {"agent_tool", "http", "api_tool"}
        ]

    @property
    def mcp_tools(self) -> list[dict[str, Any]]:
        return [t for t in self.tools if str(t.get("kind") or "") == "mcp"]

    @property
    def mode(self) -> str:
        return "s2s" if self.s2s_enabled else "pipeline"

    def __post_init__(self) -> None:
        if self.enable_backchannel or self.backchannel_enabled:
            self.enable_backchannel = True
            self.backchannel_enabled = True
        if self.reminder_trigger_ms != 10000 and self.reminder_timeout_ms == 10000:
            self.reminder_timeout_ms = self.reminder_trigger_ms
        elif self.reminder_timeout_ms != 10000 and self.reminder_trigger_ms == 10000:
            self.reminder_trigger_ms = self.reminder_timeout_ms


async def _find_phone_binding(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    e164: str,
) -> tuple[uuid.UUID | None, int | None, uuid.UUID | None]:
    """Return ``(inbound_agent_id, inbound_agent_version, outbound_agent_id)`` for ``e164``."""
    if not e164:
        return None, None, None
    pn = (
        await session.execute(
            select(PhoneNumber).where(
                PhoneNumber.tenant_id == tenant_id,
                PhoneNumber.e164 == e164,
                PhoneNumber.status != PhoneNumberStatus.RELEASED.value,
            )
        )
    ).scalars().first()
    if pn is not None and (pn.inbound_agent_id or pn.outbound_agent_id):
        return pn.inbound_agent_id, pn.inbound_agent_version, pn.outbound_agent_id

    tpn = (
        await session.execute(
            select(TelephonyPhoneNumber).where(
                TelephonyPhoneNumber.tenant_id == tenant_id,
                TelephonyPhoneNumber.e164_number == e164,
                TelephonyPhoneNumber.status != "RELEASED",
            )
        )
    ).scalars().first()
    if tpn is not None:
        return (
            _parse_uuid(tpn.inbound_agent_id),
            tpn.inbound_agent_version,
            _parse_uuid(tpn.outbound_agent_id),
        )
    return None, None, None


async def _resolve_agent_and_version(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
    pinned_version_number: int | None = None,
    explicit_version_id: uuid.UUID | None = None,
) -> tuple[Agent | None, AgentVersion | None]:
    agent = (
        await session.execute(
            select(Agent).where(
                Agent.id == agent_id,
                Agent.tenant_id == tenant_id,
                Agent.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if agent is None:
        return None, None

    if explicit_version_id is not None:
        ver = (
            await session.execute(
                select(AgentVersion).where(
                    AgentVersion.id == explicit_version_id,
                    AgentVersion.tenant_id == tenant_id,
                    AgentVersion.agent_id == agent.id,
                )
            )
        ).scalar_one_or_none()
        if ver is not None:
            return agent, ver

    if pinned_version_number is not None:
        ver = (
            await session.execute(
                select(AgentVersion).where(
                    AgentVersion.tenant_id == tenant_id,
                    AgentVersion.agent_id == agent.id,
                    AgentVersion.version_number == int(pinned_version_number),
                )
            )
        ).scalar_one_or_none()
        if ver is not None:
            return agent, ver

    if agent.published_version_id is not None:
        ver = (
            await session.execute(
                select(AgentVersion).where(
                    AgentVersion.id == agent.published_version_id,
                    AgentVersion.tenant_id == tenant_id,
                )
            )
        ).scalar_one_or_none()
        if ver is not None:
            return agent, ver

    ver = (
        await session.execute(
            select(AgentVersion)
            .where(
                AgentVersion.tenant_id == tenant_id,
                AgentVersion.agent_id == agent.id,
            )
            .order_by(AgentVersion.is_active.desc(), AgentVersion.version_number.desc())
        )
    ).scalars().first()
    return agent, ver


async def _resolve_scoped_tools(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID | None,
    snapshot: dict[str, Any],
) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    seen_names: set[str] = set()

    # 1. Inline tools in snapshot["tools"]
    raw_tools = snapshot.get("tools")
    if isinstance(raw_tools, list):
        for item in raw_tools:
            if isinstance(item, dict) and item.get("name"):
                name = str(item["name"]).strip()
                if name and name not in seen_names:
                    seen_names.add(name)
                    tools.append(dict(item))
            elif isinstance(item, str) and item.strip():
                name = item.strip()
                if name not in seen_names:
                    seen_names.add(name)
                    tools.append({"name": name, "kind": "builtin"})

    # 2. AgentTool rows bound to this agent
    if agent_id is not None:
        agent_tool_rows = (
            await session.execute(
                select(AgentTool).where(
                    AgentTool.tenant_id == tenant_id,
                    AgentTool.agent_id == str(agent_id),
                    AgentTool.is_enabled.is_(True),
                )
            )
        ).scalars().all()
        for row in agent_tool_rows:
            if row.name not in seen_names:
                seen_names.add(row.name)
                tools.append(
                    {
                        "id": str(row.id),
                        "name": row.name,
                        "description": row.description,
                        "schema": dict(row.schema or {}),
                        "auth_binding": dict(row.auth_binding or {}),
                        "kind": (row.schema or {}).get("kind", "agent_tool"),
                    }
                )

    # 3. ApiTool rows enabled for tenant
    api_tool_rows = (
        await session.execute(
            select(ApiTool).where(
                ApiTool.tenant_id == tenant_id,
                ApiTool.enabled.is_(True),
            )
        )
    ).scalars().all()
    allowed_api_ids = {
        str(x) for x in (snapshot.get("api_tool_ids") or []) if x is not None
    }
    for row in api_tool_rows:
        if allowed_api_ids and str(row.id) not in allowed_api_ids:
            continue
        if row.name not in seen_names:
            seen_names.add(row.name)
            tools.append(
                {
                    "id": str(row.id),
                    "name": row.name,
                    "description": row.description,
                    "kind": "http",
                    "api_tool_id": str(row.id),
                }
            )

    # 4. MCP tools enabled for tenant
    mcp_tool_rows = (
        await session.execute(
            select(McpTool, McpServer)
            .join(McpServer, McpTool.server_id == McpServer.id)
            .where(
                McpTool.tenant_id == tenant_id,
                McpTool.enabled.is_(True),
                McpServer.status != "disabled",
            )
        )
    ).all()
    for mcp_tool, mcp_server in mcp_tool_rows:
        qname = getattr(mcp_tool, "qualified_name", None) or mcp_tool.name
        rname = getattr(mcp_tool, "remote_name", None) or mcp_tool.name
        if qname not in seen_names:
            seen_names.add(qname)
            tools.append(
                {
                    "id": str(mcp_tool.id),
                    "name": qname,
                    "remote_name": rname,
                    "description": mcp_tool.description,
                    "input_schema": dict(mcp_tool.input_schema or {}),
                    "kind": "mcp",
                    "mcp_server_id": str(mcp_server.id),
                    "mcp_tool_id": str(mcp_tool.id),
                }
            )

    return tools


async def _resolve_knowledge_scope(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID | None,
    snapshot: dict[str, Any],
) -> tuple[list[uuid.UUID], list[uuid.UUID] | None]:
    """Return ``(collection_ids, document_ids)`` for the bound agent."""
    explicit_collection_ids: list[uuid.UUID] = []
    for raw_cid in snapshot.get("knowledge_collection_ids") or snapshot.get("collection_ids") or []:
        parsed = _parse_uuid(raw_cid)
        if parsed is not None and parsed not in explicit_collection_ids:
            explicit_collection_ids.append(parsed)

    collections = (
        await session.execute(
            select(KnowledgeCollection).where(
                KnowledgeCollection.tenant_id == tenant_id,
                KnowledgeCollection.is_active.is_(True),
            )
        )
    ).scalars().all()

    matched_collection_ids: list[uuid.UUID] = list(explicit_collection_ids)
    if agent_id is not None:
        agent_id_str = str(agent_id)
        for col in collections:
            bound_agents = [str(a) for a in (col.agent_ids or [])]
            if agent_id_str in bound_agents and col.id not in matched_collection_ids:
                matched_collection_ids.append(col.id)

    if not matched_collection_ids:
        return [], None

    source_rows = (
        await session.execute(
            select(KnowledgeCollectionSource).where(
                KnowledgeCollectionSource.tenant_id == tenant_id,
                KnowledgeCollectionSource.collection_id.in_(matched_collection_ids),
                KnowledgeCollectionSource.source_type == "document",
            )
        )
    ).scalars().all()

    doc_ids: list[uuid.UUID] = []
    for src in source_rows:
        parsed_doc = _parse_uuid(src.source_id)
        if parsed_doc is not None and parsed_doc not in doc_ids:
            doc_ids.append(parsed_doc)

    return matched_collection_ids, doc_ids


async def resolve_runtime_config(
    session: AsyncSession,
    call: Call | None = None,
    tenant: Tenant | None = None,
    *,
    agent_id: uuid.UUID | None = None,
    environment: str | uuid.UUID | None = None,
) -> RuntimeConfig:
    """Resolve the authoritative :class:`RuntimeConfig` for ``call`` and persist
    ``call.agent_id`` / ``call.agent_version_id`` (and experiment assignment).
    """
    if tenant is None and call is not None:
        tenant = (
            await session.execute(select(Tenant).where(Tenant.id == call.tenant_id))
        ).scalar_one()
    if tenant is None:
        raise ValueError("Cannot resolve RuntimeConfig without a Tenant")

    tenant_id = tenant.id
    direction_val = (
        call.direction.value
        if call is not None and isinstance(call.direction, CallDirection)
        else str(getattr(call, "direction", "inbound") or "inbound")
    ).lower()

    # Determine candidate agent_id & pinned version from PhoneNumber binding or Call
    inbound_aid, inbound_ver, outbound_aid = await _find_phone_binding(
        session,
        tenant_id=tenant_id,
        e164=(
            (call.to_number if direction_val == "inbound" else call.from_number)
            if call is not None
            else ""
        ),
    )

    candidate_agent_id: uuid.UUID | None = None
    pinned_version_number: int | None = None
    explicit_version_id: uuid.UUID | None = getattr(call, "agent_version_id", None) if call is not None else None
    source = "tenant_fallback"

    explicit_aid = (getattr(call, "agent_id", None) if call is not None else None) or agent_id
    if direction_val == "inbound" and inbound_aid is not None:
        candidate_agent_id = inbound_aid
        pinned_version_number = inbound_ver
        source = "phone_number"
    elif direction_val == "outbound" and explicit_aid is not None:
        candidate_agent_id = explicit_aid
        source = "call"
    elif direction_val == "outbound" and outbound_aid is not None:
        candidate_agent_id = outbound_aid
        source = "phone_number"
    elif explicit_aid is not None:
        candidate_agent_id = explicit_aid
        source = "call"
    else:
        # Check if tenant has a default published Agent
        default_agent = (
            await session.execute(
                select(Agent)
                .where(
                    Agent.tenant_id == tenant_id,
                    Agent.deleted_at.is_(None),
                )
                .order_by(
                    (Agent.status == "published").desc(),
                    Agent.updated_at.desc(),
                )
            )
        ).scalars().first()
        if default_agent is not None:
            candidate_agent_id = default_agent.id
            source = "tenant_default_agent"

    agent: Agent | None = None
    agent_version: AgentVersion | None = None
    variant_override: dict[str, Any] = {}
    experiment_id: uuid.UUID | None = getattr(call, "experiment_id", None) if call is not None else None
    variant_id: uuid.UUID | None = getattr(call, "variant_id", None) if call is not None else None

    if candidate_agent_id is not None:
        agent, agent_version = await _resolve_agent_and_version(
            session,
            tenant_id=tenant_id,
            agent_id=candidate_agent_id,
            pinned_version_number=pinned_version_number,
            explicit_version_id=explicit_version_id,
        )
        if agent is not None and environment is not None and getattr(agent, "environment_id", None) is not None:
            from app.db.models import Environment

            req_env_uuid = _parse_uuid(environment)
            if req_env_uuid is not None:
                if agent.environment_id != req_env_uuid:
                    raise ValueError(
                        f"Environment mismatch: agent {agent.id} belongs to environment {agent.environment_id}, not {req_env_uuid}"
                    )
            else:
                env_row = (
                    await session.execute(
                        select(Environment).where(
                            Environment.tenant_id == tenant_id,
                            Environment.name == str(environment).strip(),
                        )
                    )
                ).scalars().first()
                if env_row is not None and agent.environment_id != env_row.id:
                    raise ValueError(
                        f"Environment mismatch: agent {agent.id} belongs to environment {agent.environment_id}, not '{environment}'"
                    )

        # Precedence 1: Active Experiment Variant (1F) overrides phone/default version
        if agent is not None:
            if experiment_id is not None and variant_id is not None:
                v_row = (
                    await session.execute(
                        select(ExperimentVariant).where(
                            ExperimentVariant.id == variant_id,
                            ExperimentVariant.experiment_id == experiment_id,
                        )
                    )
                ).scalar_one_or_none()
                if v_row is not None:
                    variant_override = dict(v_row.config or {})
                    if v_row.prompt:
                        variant_override.setdefault("system_prompt", v_row.prompt)
                    exp_ver_id = _parse_uuid(variant_override.get("agent_version_id"))
                    exp_ver_num = variant_override.get("agent_version_number")
                    if exp_ver_id is not None or exp_ver_num is not None:
                        _, exp_ver = await _resolve_agent_and_version(
                            session,
                            tenant_id=tenant_id,
                            agent_id=agent.id,
                            pinned_version_number=_coerce_int(exp_ver_num, 0) or None,
                            explicit_version_id=exp_ver_id,
                        )
                        if exp_ver is not None:
                            agent_version = exp_ver
                    source = "experiment"
            elif call is not None:
                assigned = await assign_call_to_experiment(
                    session,
                    tenant_id=tenant_id,
                    agent_id=str(agent.id),
                    call_sid=call.call_sid or str(call.id),
                    call_id=call.id,
                )
                if assigned is not None:
                    experiment_id = _parse_uuid(assigned.get("experiment_id"))
                    variant_id = _parse_uuid(assigned.get("variant_id"))
                    call.experiment_id = experiment_id
                    call.variant_id = variant_id
                    variant_override = dict(assigned.get("config") or {})
                    if assigned.get("prompt"):
                        variant_override.setdefault("system_prompt", assigned["prompt"])
                    exp_ver_id = _parse_uuid(variant_override.get("agent_version_id"))
                    exp_ver_num = variant_override.get("agent_version_number")
                    if exp_ver_id is not None or exp_ver_num is not None:
                        _, exp_ver = await _resolve_agent_and_version(
                            session,
                            tenant_id=tenant_id,
                            agent_id=agent.id,
                            pinned_version_number=_coerce_int(exp_ver_num, 0) or None,
                            explicit_version_id=exp_ver_id,
                        )
                        if exp_ver is not None:
                            agent_version = exp_ver
                    source = "experiment"

    # Merge config snapshot: Agent.current_draft_config -> AgentVersion.config_snapshot -> variant_override
    merged_snapshot: dict[str, Any] = {}
    if agent is not None and isinstance(agent.current_draft_config, dict):
        merged_snapshot.update(agent.current_draft_config)
    if agent_version is not None and isinstance(agent_version.config_snapshot, dict):
        merged_snapshot.update(agent_version.config_snapshot)
    if variant_override:
        merged_snapshot.update(variant_override)

    # Persist resolved agent_id and agent_version_id onto the Call row
    if call is not None:
        if agent is not None:
            call.agent_id = agent.id
        if agent_version is not None:
            call.agent_version_id = agent_version.id
        await session.flush()

    # Extract nested blocks if present in config_snapshot
    voice_block = merged_snapshot.get("voice") if isinstance(merged_snapshot.get("voice"), dict) else {}
    model_block = merged_snapshot.get("model") if isinstance(merged_snapshot.get("model"), dict) else {}
    stt_block = merged_snapshot.get("stt") if isinstance(merged_snapshot.get("stt"), dict) else {}
    turn_block = (
        merged_snapshot.get("turn_taking")
        if isinstance(merged_snapshot.get("turn_taking"), dict)
        else {}
    )
    audio_block = (
        merged_snapshot.get("audio")
        if isinstance(merged_snapshot.get("audio"), dict)
        else {}
    )

    system_prompt = str(
        merged_snapshot.get("system_prompt")
        or merged_snapshot.get("prompt")
        or getattr(tenant, "system_prompt", None)
        or getattr(tenant, "system_prompt_extra", None)
        or "You are a helpful AI receptionist."
    )
    greeting = str(
        merged_snapshot.get("greeting")
        or merged_snapshot.get("first_message")
        or getattr(tenant, "greeting", None)
        or "Hello, how can I help you today?"
    )
    language = str(
        merged_snapshot.get("language")
        or getattr(tenant, "language", None)
        or "en"
    )
    auto_detect_language = (
        language.lower() == "auto"
        or bool(merged_snapshot.get("auto_detect_language", False))
    )

    llm_provider = str(
        merged_snapshot.get("llm_provider")
        or model_block.get("provider")
        or getattr(tenant, "llm_provider", None)
        or "openai"
    ).lower()
    llm_model = str(
        merged_snapshot.get("llm_model")
        or model_block.get("model")
        or getattr(tenant, "llm_model", None)
        or "gpt-4o-mini"
    )
    llm_fallbacks = [
        str(p).lower()
        for p in (
            merged_snapshot.get("llm_fallback_providers")
            or model_block.get("fallback_providers")
            or []
        )
        if p
    ]
    temperature = _coerce_float(
        merged_snapshot.get("temperature", model_block.get("temperature")),
        0.6,
    )
    max_tokens = _coerce_int(
        merged_snapshot.get("max_tokens", model_block.get("max_tokens")),
        300,
    )

    stt_provider = str(
        merged_snapshot.get("stt_provider")
        or stt_block.get("provider")
        or "deepgram"
    ).lower()
    stt_model = str(
        merged_snapshot.get("stt_model")
        or stt_block.get("model")
        or "nova-3"
    )
    stt_fallbacks = [
        str(p).lower()
        for p in (
            merged_snapshot.get("stt_fallback_providers")
            or stt_block.get("fallback_providers")
            or []
        )
        if p
    ]

    tts_provider = str(
        merged_snapshot.get("tts_provider")
        or voice_block.get("provider")
        or "elevenlabs"
    ).lower()
    tts_model = str(
        merged_snapshot.get("tts_model")
        or voice_block.get("model")
        or "eleven_flash_v2_5"
    )
    tts_fallbacks = [
        str(p).lower()
        for p in (
            merged_snapshot.get("tts_fallback_providers")
            or voice_block.get("fallback_providers")
            or []
        )
        if p
    ]
    voice_id = str(
        merged_snapshot.get("voice_id")
        or voice_block.get("voice_id")
        or getattr(tenant, "voice_id", None)
        or ""
    )

    voice_settings = {
        "stability": _coerce_float(
            merged_snapshot.get(
                "voice_stability",
                voice_block.get("stability", getattr(tenant, "voice_stability", 0.5)),
            ),
            0.5,
        ),
        "similarity_boost": _coerce_float(
            merged_snapshot.get(
                "voice_similarity",
                voice_block.get(
                    "similarity_boost", getattr(tenant, "voice_similarity", 0.75)
                ),
            ),
            0.75,
        ),
        "style": _coerce_float(
            merged_snapshot.get(
                "voice_style",
                voice_block.get("style", getattr(tenant, "voice_style", 0.0)),
            ),
            0.0,
        ),
        "speed": _coerce_float(
            merged_snapshot.get(
                "voice_speed",
                voice_block.get("speed", getattr(tenant, "voice_speed", 1.0)),
            ),
            1.0,
        ),
        "use_speaker_boost": bool(
            merged_snapshot.get(
                "voice_expressive",
                voice_block.get(
                    "use_speaker_boost", getattr(tenant, "voice_expressive", True)
                ),
            )
        ),
    }

    s2s_provider_raw = merged_snapshot.get("s2s_provider") or model_block.get("s2s_provider")
    s2s_enabled = bool(
        merged_snapshot.get("s2s_enabled")
        or s2s_provider_raw
        or llm_provider in {"openai_realtime", "gemini_live"}
    )
    s2s_provider = (
        str(s2s_provider_raw or llm_provider).lower()
        if s2s_enabled
        else None
    )
    s2s_model = (
        str(
            merged_snapshot.get("s2s_model")
            or model_block.get("s2s_model")
            or llm_model
        )
        if s2s_enabled
        else None
    )

    responsiveness = _coerce_float(
        merged_snapshot.get("responsiveness", turn_block.get("responsiveness")),
        0.7,
    )
    interruption_sensitivity = _coerce_float(
        merged_snapshot.get(
            "interruption_sensitivity",
            turn_block.get("interruption_sensitivity"),
        ),
        0.7,
    )
    enable_smart_turn = bool(
        merged_snapshot.get(
            "enable_smart_turn",
            turn_block.get("enable_smart_turn", True),
        )
    )
    backchannel_enabled = bool(
        merged_snapshot.get(
            "enable_backchannel",
            merged_snapshot.get(
                "backchannel_enabled",
                turn_block.get(
                    "enable_backchannel",
                    turn_block.get(
                        "backchannel_enabled",
                        getattr(tenant, "backchannel_enabled", False),
                    ),
                ),
            ),
        )
    )
    backchannel_frequency = _coerce_float(
        merged_snapshot.get(
            "backchannel_frequency",
            turn_block.get("backchannel_frequency"),
        ),
        0.5,
    )
    raw_bc_words = (
        merged_snapshot.get("backchannel_words")
        or turn_block.get("backchannel_words")
        or ["yeah", "uh-huh", "got it", "mm-hmm", "okay"]
    )
    backchannel_words = [str(w).strip() for w in raw_bc_words if str(w).strip()]

    reminder_timeout_ms = _coerce_int(
        merged_snapshot.get(
            "reminder_trigger_ms",
            merged_snapshot.get(
                "reminder_timeout_ms",
                turn_block.get(
                    "reminder_trigger_ms",
                    turn_block.get("reminder_timeout_ms"),
                ),
            ),
        ),
        10000,
    )
    reminder_max_count = _coerce_int(
        merged_snapshot.get(
            "reminder_max_count",
            turn_block.get("reminder_max_count"),
        ),
        2,
    )
    boosted_keywords = _parse_boosted_keywords(
        merged_snapshot.get("boosted_keywords")
        or stt_block.get("boosted_keywords")
        or turn_block.get("boosted_keywords")
    )
    filler_trigger_ms = _coerce_int(
        merged_snapshot.get(
            "filler_trigger_ms",
            getattr(tenant, "filler_trigger_ms", 0),
        ),
        0,
    )

    ambient_sound = (
        merged_snapshot.get("ambient_sound")
        or audio_block.get("ambient_sound")
        or None
    )
    if isinstance(ambient_sound, str):
        ambient_sound = ambient_sound.strip() or None
    ambient_volume = _coerce_float(
        merged_snapshot.get("ambient_volume", audio_block.get("ambient_volume")),
        0.12,
    )
    denoise_mode = str(
        merged_snapshot.get("denoise_mode")
        or audio_block.get("denoise_mode")
        or "off"
    ).lower()

    voicemail_detection_enabled = bool(
        merged_snapshot.get("voicemail_detection_enabled", False)
    )
    voicemail_action = str(
        merged_snapshot.get("voicemail_action") or "hangup"
    ).lower()
    voicemail_message = merged_snapshot.get("voicemail_message")
    ivr_navigation_enabled = bool(
        merged_snapshot.get("ivr_navigation_enabled", False)
    )
    ivr_goal = merged_snapshot.get("ivr_goal")

    transfer_targets = dict(merged_snapshot.get("transfer_targets") or {})
    if not transfer_targets and getattr(tenant, "transfer_number", None):
        transfer_targets["default"] = str(tenant.transfer_number)
    warm_transfer_enabled = bool(
        merged_snapshot.get("warm_transfer_enabled", False)
    )

    tools = await _resolve_scoped_tools(
        session,
        tenant_id=tenant_id,
        agent_id=agent.id if agent is not None else None,
        snapshot=merged_snapshot,
    )
    collection_ids, doc_ids = await _resolve_knowledge_scope(
        session,
        tenant_id=tenant_id,
        agent_id=agent.id if agent is not None else None,
        snapshot=merged_snapshot,
    )

    cfg = RuntimeConfig(
        tenant_id=tenant_id,
        agent_id=agent.id if agent is not None else None,
        agent_version_id=agent_version.id if agent_version is not None else None,
        agent_version_number=(
            agent_version.version_number if agent_version is not None else pinned_version_number
        ),
        experiment_id=experiment_id,
        variant_id=variant_id,
        source=source,
        system_prompt=system_prompt,
        greeting=greeting,
        language=language,
        auto_detect_language=auto_detect_language,
        llm_provider=llm_provider,
        llm_model=llm_model,
        llm_fallback_providers=llm_fallbacks,
        temperature=temperature,
        max_tokens=max_tokens,
        stt_provider=stt_provider,
        stt_model=stt_model,
        stt_fallback_providers=stt_fallbacks,
        tts_provider=tts_provider,
        tts_model=tts_model,
        tts_fallback_providers=tts_fallbacks,
        voice_id=voice_id,
        voice_settings=voice_settings,
        s2s_enabled=s2s_enabled,
        s2s_provider=s2s_provider,
        s2s_model=s2s_model,
        responsiveness=responsiveness,
        interruption_sensitivity=interruption_sensitivity,
        enable_smart_turn=enable_smart_turn,
        enable_backchannel=backchannel_enabled,
        backchannel_enabled=backchannel_enabled,
        backchannel_frequency=backchannel_frequency,
        backchannel_words=backchannel_words,
        reminder_trigger_ms=reminder_timeout_ms,
        reminder_timeout_ms=reminder_timeout_ms,
        reminder_max_count=reminder_max_count,
        boosted_keywords=boosted_keywords,
        filler_trigger_ms=filler_trigger_ms,
        ambient_sound=ambient_sound,
        ambient_volume=ambient_volume,
        denoise_mode=denoise_mode,
        voicemail_detection_enabled=voicemail_detection_enabled,
        voicemail_action=voicemail_action,
        voicemail_message=voicemail_message,
        ivr_navigation_enabled=ivr_navigation_enabled,
        ivr_goal=ivr_goal,
        transfer_targets=transfer_targets,
        warm_transfer_enabled=warm_transfer_enabled,
        tools=tools,
        knowledge_collection_ids=collection_ids,
        knowledge_document_ids=doc_ids,
        environment_id=getattr(agent, "environment_id", None) if agent is not None else None,
        raw_config=merged_snapshot,
    )
    log.info(
        "runtime.config.resolved",
        call_id=str(call.id) if call is not None else None,
        tenant_id=str(tenant_id),
        agent_id=str(cfg.agent_id) if cfg.agent_id else None,
        agent_version_id=str(cfg.agent_version_id) if cfg.agent_version_id else None,
        source=cfg.source,
    )
    return cfg
