"""Versioned chat-agent, chat-session, and message management service (Retell parity).

Manages ``ChatAgent``, immutable ``ChatAgentVersion``, durable ``ChatSession``,
and ordered ``ChatMessage`` rows with tenant isolation, ETag concurrency
control, draft validation, publishing, version rollback, archiving, contact
resolution, contact memory grounding, and durable audit logging.
"""
from __future__ import annotations

import asyncio
import copy
import re
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import log
from app.db.models import AuditAction, Tenant
from app.db.retell_models import (
    ChatAgent,
    ChatAgentVersion,
    ChatMessage,
    ChatMessageRoleEnum,
    ChatSession,
    ChatSessionStatusEnum,
    Contact,
    DynamicVariableDefinition,
)
from app.domain.chat_agent_models import (
    ChatAgentCreate,
    ChatAgentPublishRequest,
    ChatAgentResponse,
    ChatAgentStatus,
    ChatAgentUpdate,
    ChatAgentValidationResult,
    ChatAgentVersionResponse,
    ChatAgentVersionStatus,
    ChatMessageCreate,
    ChatMessageResponse,
    ChatMessageRole,
    ChatSessionCreate,
    ChatSessionResponse,
    ChatSessionStatus,
    ChatTurnResponse,
    compute_chat_agent_etag,
    compute_chat_config_hash,
    validate_agent_config_dict,
)
from app.domain.contact_memory_models import MemorySaveRequest, MemorySource
from app.domain.contact_models import ContactCreate, ContactSource
from app.services import contact_memory_service, contact_service

_CHAT_PUBLISH_LOCKS: dict[UUID, asyncio.Lock] = {}
_INLINE_MEMORY_RE = re.compile(r"\[remember\s+([a-z][a-z0-9_.-]{0,63})\s*=\s*([^\]]+)\]", re.IGNORECASE)


class ChatAgentConflictError(Exception):
    """Raised when an If-Match ETag header does not match the current draft ETag."""


def _get_publish_lock(agent_id: UUID) -> asyncio.Lock:
    lock = _CHAT_PUBLISH_LOCKS.get(agent_id)
    if lock is None:
        lock = asyncio.Lock()
        _CHAT_PUBLISH_LOCKS[agent_id] = lock
    return lock


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _tenant_id(tenant: Tenant | UUID) -> UUID:
    return tenant.id if isinstance(tenant, Tenant) else UUID(str(tenant))


async def _emit_chat_audit(
    session: AsyncSession,
    tenant_id: UUID,
    event: str,
    detail: dict[str, Any],
    *,
    actor_user_id: UUID | None = None,
) -> None:
    try:
        from app.auth.identity.events import scrub
        from app.auth.service import record_audit

        await record_audit(
            session,
            action=AuditAction.GOVERNANCE_EVENT,
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            actor_email="system",
            detail=scrub({"event": event, "operation": event, **detail}),
            commit=False,
        )
    except Exception as exc:
        log.warning("chat_agent.audit.failed", event=event, error=str(exc))


def to_agent_response(row: ChatAgent) -> ChatAgentResponse:
    etag = compute_chat_agent_etag(row.draft_config, row.draft_version)
    return ChatAgentResponse(
        id=row.id,
        tenant_id=row.tenant_id,
        name=row.name,
        description=row.description,
        status=row.status,
        draft_config=row.draft_config or {},
        published_config=row.published_config,
        draft_version=row.draft_version,
        published_version=row.published_version,
        etag=etag,
        created_by=row.created_by,
        archived_at=row.archived_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def to_version_response(row: ChatAgentVersion) -> ChatAgentVersionResponse:
    return ChatAgentVersionResponse(
        id=row.id,
        tenant_id=row.tenant_id,
        chat_agent_id=row.chat_agent_id,
        version=row.version,
        status=row.status,
        config=row.config or {},
        config_hash=compute_chat_config_hash(row.config),
        change_summary=row.change_summary or "",
        created_by=row.created_by,
        created_at=row.created_at,
        published_at=row.published_at,
    )


def to_session_response(row: ChatSession) -> ChatSessionResponse:
    return ChatSessionResponse(
        id=row.id,
        tenant_id=row.tenant_id,
        environment_id=row.environment_id,
        chat_agent_id=row.chat_agent_id,
        chat_agent_version=row.chat_agent_version,
        contact_id=row.contact_id,
        channel=row.channel,
        status=row.status,
        dynamic_variables=row.dynamic_variables or {},
        metadata=row.metadata_json or {},
        message_count=row.message_count,
        started_at=row.started_at,
        last_message_at=row.last_message_at,
        ended_at=row.ended_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def to_message_response(row: ChatMessage) -> ChatMessageResponse:
    return ChatMessageResponse(
        id=row.id,
        tenant_id=row.tenant_id,
        session_id=row.session_id,
        sequence=row.sequence,
        role=row.role,
        content=row.content,
        tool_calls=row.tool_calls or [],
        metadata=row.metadata_json or {},
        latency_ms=row.latency_ms,
        created_at=row.created_at,
    )


async def get_by_id(
    session: AsyncSession,
    tenant: Tenant | UUID,
    agent_id: UUID | str,
    *,
    for_update: bool = False,
) -> ChatAgent | None:
    tid = _tenant_id(tenant)
    try:
        aid = UUID(str(agent_id))
    except (ValueError, TypeError):
        return None
    stmt = select(ChatAgent).where(ChatAgent.tenant_id == tid, ChatAgent.id == aid)
    if for_update:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_by_name(
    session: AsyncSession,
    tenant: Tenant | UUID,
    name: str,
) -> ChatAgent | None:
    tid = _tenant_id(tenant)
    stmt = select(ChatAgent).where(
        ChatAgent.tenant_id == tid,
        ChatAgent.name == name.strip(),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def create(
    session: AsyncSession,
    tenant: Tenant | UUID,
    payload: ChatAgentCreate,
    *,
    created_by: UUID | None = None,
) -> ChatAgent:
    tid = _tenant_id(tenant)
    existing = await get_by_name(session, tid, payload.name)
    if existing is not None:
        raise ValueError(f"chat agent {payload.name!r} already exists for this tenant")
    now = _now()
    cleaned_cfg = validate_agent_config_dict(payload.draft_config)
    row = ChatAgent(
        tenant_id=tid,
        name=payload.name.strip(),
        description=payload.description.strip(),
        status=ChatAgentStatus.DRAFT.value,
        draft_config=cleaned_cfg,
        published_config=None,
        draft_version=1,
        published_version=None,
        created_by=created_by,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    await session.flush()
    await _emit_chat_audit(
        session,
        tid,
        "chat_agent.created",
        {
            "chat_agent_id": str(row.id),
            "name": row.name,
            "etag": compute_chat_agent_etag(row.draft_config, row.draft_version),
        },
        actor_user_id=created_by,
    )
    log.info(
        "chat_agent.created",
        tenant_id=str(tid),
        chat_agent_id=str(row.id),
        name=row.name,
    )
    return row


async def list_agents(
    session: AsyncSession,
    tenant: Tenant | UUID,
    *,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[ChatAgent]:
    tid = _tenant_id(tenant)
    stmt = select(ChatAgent).where(ChatAgent.tenant_id == tid)
    if status:
        stmt = stmt.where(ChatAgent.status == status)
    stmt = (
        stmt.order_by(ChatAgent.created_at.desc())
        .limit(max(1, min(limit, 200)))
        .offset(max(0, offset))
    )
    return list((await session.execute(stmt)).scalars().all())


def validate_config_rules(cfg: dict[str, Any] | None) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        cleaned = validate_agent_config_dict(cfg)
    except ValueError as exc:
        errors.append(str(exc))
        return errors, warnings

    temperature = cleaned.get("temperature")
    if temperature is not None:
        try:
            t_val = float(temperature)
            if not (0.0 <= t_val <= 2.0):
                errors.append("temperature must be between 0.0 and 2.0")
        except (TypeError, ValueError):
            errors.append("temperature must be a number")

    max_tokens = cleaned.get("max_tokens")
    if max_tokens is not None:
        try:
            m_val = int(max_tokens)
            if m_val < 16 or m_val > 16384:
                errors.append("max_tokens must be between 16 and 16384")
        except (TypeError, ValueError):
            errors.append("max_tokens must be an integer")

    system_prompt = str(cleaned.get("system_prompt", "") or "").strip()
    if not system_prompt:
        warnings.append("system_prompt is empty; default assistant persona will be used")

    return errors, warnings


async def validate_draft(
    session: AsyncSession,
    tenant: Tenant | UUID,
    agent_id: UUID | str,
) -> ChatAgentValidationResult:
    row = await get_by_id(session, tenant, agent_id)
    if row is None:
        raise LookupError("chat agent not found")
    errors, warnings = validate_config_rules(row.draft_config)
    return ChatAgentValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        etag=compute_chat_agent_etag(row.draft_config, row.draft_version),
    )


async def update_draft(
    session: AsyncSession,
    tenant: Tenant | UUID,
    agent_id: UUID | str,
    payload: ChatAgentUpdate,
    *,
    if_match: str | None = None,
    actor_user_id: UUID | None = None,
) -> ChatAgent | None:
    row = await get_by_id(session, tenant, agent_id, for_update=True)
    if row is None:
        return None
    if row.status == ChatAgentStatus.ARCHIVED.value:
        raise ValueError("cannot update an archived chat agent")

    current_etag = compute_chat_agent_etag(row.draft_config, row.draft_version)
    if if_match is not None and if_match.strip() not in ("*", ""):
        expected = if_match.strip()
        raw_digest = current_etag.removeprefix('W/"').removesuffix('"')
        if expected != current_etag and expected.strip('"') != raw_digest:
            raise ChatAgentConflictError(
                f"ETag mismatch: expected {expected}, current is {current_etag}"
            )

    if payload.name is not None:
        new_name = payload.name.strip()
        if new_name != row.name:
            dup = await get_by_name(session, row.tenant_id, new_name)
            if dup is not None and dup.id != row.id:
                raise ValueError(f"chat agent {new_name!r} already exists for this tenant")
            row.name = new_name
    if payload.description is not None:
        row.description = payload.description.strip()
    if payload.draft_config is not None:
        errors, _warnings = validate_config_rules(payload.draft_config)
        if errors:
            raise ValueError("; ".join(errors))
        row.draft_config = validate_agent_config_dict(payload.draft_config)
    row.draft_version = (row.draft_version or 0) + 1
    row.updated_at = _now()
    await session.flush()
    await _emit_chat_audit(
        session,
        row.tenant_id,
        "chat_agent.draft_saved",
        {
            "chat_agent_id": str(row.id),
            "draft_version": row.draft_version,
            "etag": compute_chat_agent_etag(row.draft_config, row.draft_version),
        },
        actor_user_id=actor_user_id,
    )
    return row


async def publish(
    session: AsyncSession,
    tenant: Tenant | UUID,
    agent_id: UUID | str,
    payload: ChatAgentPublishRequest | None = None,
    *,
    actor_user_id: UUID | None = None,
) -> tuple[ChatAgent, ChatAgentVersion]:
    tid = _tenant_id(tenant)
    aid = UUID(str(agent_id))
    async with _get_publish_lock(aid):
        row = await get_by_id(session, tid, aid, for_update=True)
        if row is None:
            raise LookupError("chat agent not found")
        if row.status == ChatAgentStatus.ARCHIVED.value:
            raise ValueError("cannot publish an archived chat agent")

        errors, _warnings = validate_config_rules(row.draft_config)
        if errors:
            await _emit_chat_audit(
                session,
                row.tenant_id,
                "chat_agent.publish_rejected",
                {"chat_agent_id": str(row.id), "errors": errors},
                actor_user_id=actor_user_id,
            )
            raise ValueError(f"chat agent validation failed: {'; '.join(errors)}")

        max_ver_stmt = select(func.max(ChatAgentVersion.version)).where(
            ChatAgentVersion.chat_agent_id == row.id
        )
        max_ver = (await session.execute(max_ver_stmt)).scalar_one_or_none() or 0
        next_version = max(int(max_ver), int(row.published_version or 0)) + 1

        # Mark prior versions as retired while keeping their config snapshots immutable
        await session.execute(
            update(ChatAgentVersion)
            .where(
                ChatAgentVersion.chat_agent_id == row.id,
                ChatAgentVersion.status == ChatAgentVersionStatus.PUBLISHED.value,
            )
            .values(status=ChatAgentVersionStatus.RETIRED.value)
        )

        now = _now()
        snapshot = copy.deepcopy(validate_agent_config_dict(row.draft_config))

        version_row = ChatAgentVersion(
            tenant_id=row.tenant_id,
            chat_agent_id=row.id,
            version=next_version,
            status=ChatAgentVersionStatus.PUBLISHED.value,
            config=snapshot,
            change_summary=(payload.change_summary.strip() if payload else ""),
            created_by=actor_user_id,
            created_at=now,
            published_at=now,
        )
        session.add(version_row)

        row.published_config = snapshot
        row.published_version = next_version
        row.status = ChatAgentStatus.PUBLISHED.value
        row.updated_at = now
        await session.flush()

        await _emit_chat_audit(
            session,
            row.tenant_id,
            "chat_agent.published",
            {
                "chat_agent_id": str(row.id),
                "version": next_version,
                "config_hash": compute_chat_config_hash(snapshot),
            },
            actor_user_id=actor_user_id,
        )
        log.info(
            "chat_agent.published",
            tenant_id=str(row.tenant_id),
            chat_agent_id=str(row.id),
            version=next_version,
        )
        return row, version_row


async def list_versions(
    session: AsyncSession,
    tenant: Tenant | UUID,
    agent_id: UUID | str,
) -> list[ChatAgentVersion]:
    tid = _tenant_id(tenant)
    try:
        aid = UUID(str(agent_id))
    except (ValueError, TypeError):
        return []
    stmt = (
        select(ChatAgentVersion)
        .where(
            ChatAgentVersion.tenant_id == tid,
            ChatAgentVersion.chat_agent_id == aid,
        )
        .order_by(ChatAgentVersion.version.desc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_version(
    session: AsyncSession,
    tenant: Tenant | UUID,
    agent_id: UUID | str,
    version_number: int,
) -> ChatAgentVersion | None:
    tid = _tenant_id(tenant)
    try:
        aid = UUID(str(agent_id))
    except (ValueError, TypeError):
        return None
    stmt = select(ChatAgentVersion).where(
        ChatAgentVersion.tenant_id == tid,
        ChatAgentVersion.chat_agent_id == aid,
        ChatAgentVersion.version == int(version_number),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def rollback_to_version(
    session: AsyncSession,
    tenant: Tenant | UUID,
    agent_id: UUID | str,
    target_version: int,
    *,
    reason: str = "",
    actor_user_id: UUID | None = None,
) -> tuple[ChatAgent, ChatAgentVersion]:
    tid = _tenant_id(tenant)
    aid = UUID(str(agent_id))
    async with _get_publish_lock(aid):
        row = await get_by_id(session, tid, aid, for_update=True)
        if row is None:
            raise LookupError("chat agent not found")
        if row.status == ChatAgentStatus.ARCHIVED.value:
            raise ValueError("cannot rollback an archived chat agent")

        target = await get_version(session, tid, aid, target_version)
        if target is None:
            raise LookupError(f"chat agent version {target_version} not found")

        max_ver_stmt = select(func.max(ChatAgentVersion.version)).where(
            ChatAgentVersion.chat_agent_id == row.id
        )
        max_ver = (await session.execute(max_ver_stmt)).scalar_one_or_none() or 0
        next_version = max(int(max_ver), int(row.published_version or 0)) + 1

        await session.execute(
            update(ChatAgentVersion)
            .where(
                ChatAgentVersion.chat_agent_id == row.id,
                ChatAgentVersion.status == ChatAgentVersionStatus.PUBLISHED.value,
            )
            .values(status=ChatAgentVersionStatus.RETIRED.value)
        )

        now = _now()
        restored_snapshot = copy.deepcopy(target.config or {})
        summary = reason.strip() or f"Rollback to version v{target_version}"

        minted = ChatAgentVersion(
            tenant_id=row.tenant_id,
            chat_agent_id=row.id,
            version=next_version,
            status=ChatAgentVersionStatus.PUBLISHED.value,
            config=restored_snapshot,
            change_summary=summary[:300],
            created_by=actor_user_id,
            created_at=now,
            published_at=now,
        )
        session.add(minted)

        row.draft_config = copy.deepcopy(restored_snapshot)
        row.draft_version = (row.draft_version or 0) + 1
        row.published_config = restored_snapshot
        row.published_version = next_version
        row.status = ChatAgentStatus.PUBLISHED.value
        row.updated_at = now
        await session.flush()

        await _emit_chat_audit(
            session,
            row.tenant_id,
            "chat_agent.rollback",
            {
                "chat_agent_id": str(row.id),
                "source_version": int(target_version),
                "new_version": next_version,
                "reason": summary,
            },
            actor_user_id=actor_user_id,
        )
        return row, minted


async def archive_agent(
    session: AsyncSession,
    tenant: Tenant | UUID,
    agent_id: UUID | str,
    *,
    actor_user_id: UUID | None = None,
) -> ChatAgent | None:
    row = await get_by_id(session, tenant, agent_id, for_update=True)
    if row is None:
        return None
    now = _now()
    row.status = ChatAgentStatus.ARCHIVED.value
    row.archived_at = now
    row.updated_at = now
    await session.flush()
    await _emit_chat_audit(
        session,
        row.tenant_id,
        "chat_agent.archived",
        {"chat_agent_id": str(row.id)},
        actor_user_id=actor_user_id,
    )
    return row


async def restore_agent(
    session: AsyncSession,
    tenant: Tenant | UUID,
    agent_id: UUID | str,
    *,
    actor_user_id: UUID | None = None,
) -> ChatAgent | None:
    row = await get_by_id(session, tenant, agent_id, for_update=True)
    if row is None:
        return None
    row.status = (
        ChatAgentStatus.PUBLISHED.value
        if row.published_version is not None
        else ChatAgentStatus.DRAFT.value
    )
    row.archived_at = None
    row.updated_at = _now()
    await session.flush()
    await _emit_chat_audit(
        session,
        row.tenant_id,
        "chat_agent.restored",
        {"chat_agent_id": str(row.id), "status": row.status},
        actor_user_id=actor_user_id,
    )
    return row


# --------------------------------------------------- Chat Sessions & Messages


async def create_session(
    session: AsyncSession,
    tenant: Tenant | UUID,
    agent_id: UUID | str,
    payload: ChatSessionCreate,
    *,
    actor_user_id: UUID | None = None,
) -> ChatSession:
    tid = _tenant_id(tenant)
    agent = await get_by_id(session, tid, agent_id)
    if agent is None:
        raise LookupError("chat agent not found")
    if agent.status == ChatAgentStatus.ARCHIVED.value:
        raise ValueError("cannot start a session on an archived chat agent")

    contact_id: UUID | None = None
    if payload.contact_id is not None:
        contact = await contact_service.get_by_id(session, tid, payload.contact_id)
        if contact is None:
            raise LookupError("contact not found")
        contact_id = contact.id
    elif payload.contact_phone:
        upsert = await contact_service.create(
            session,
            tid,
            ContactCreate(
                phone=payload.contact_phone,
                name=payload.contact_name or "",
                source=ContactSource.CHAT,
            ),
            actor_user_id=actor_user_id,
        )
        contact_id = upsert.contact.id

    # Hydrate dynamic variables from definitions + caller overrides
    dyn_stmt = select(DynamicVariableDefinition).where(
        DynamicVariableDefinition.tenant_id == tid,
        (DynamicVariableDefinition.chat_agent_id == agent.id)
        | (DynamicVariableDefinition.chat_agent_id.is_(None)),
    )
    dyn_defs = list((await session.execute(dyn_stmt)).scalars().all())
    merged_vars: dict[str, Any] = {}
    for d in dyn_defs:
        if isinstance(d.default_value, dict) and "value" in d.default_value:
            merged_vars[d.name] = d.default_value["value"]
        elif d.default_value is not None:
            merged_vars[d.name] = d.default_value
    merged_vars.update(payload.dynamic_variables or {})

    now = _now()
    active_cfg = agent.published_config if agent.published_config is not None else agent.draft_config
    chat_session = ChatSession(
        tenant_id=tid,
        chat_agent_id=agent.id,
        chat_agent_version=agent.published_version or agent.draft_version,
        contact_id=contact_id,
        channel=payload.channel or "web",
        status=ChatSessionStatusEnum.ACTIVE.value,
        dynamic_variables=merged_vars,
        metadata_json=dict(payload.metadata or {}),
        message_count=0,
        started_at=now,
        last_message_at=None,
        created_at=now,
        updated_at=now,
    )
    session.add(chat_session)
    await session.flush()

    initial_greeting = str((active_cfg or {}).get("first_message") or (active_cfg or {}).get("greeting") or "").strip()
    if initial_greeting:
        greeting_msg = ChatMessage(
            tenant_id=tid,
            session_id=chat_session.id,
            sequence=1,
            role=ChatMessageRoleEnum.ASSISTANT.value,
            content=initial_greeting,
            tool_calls=[],
            metadata_json={"kind": "initial_greeting"},
            latency_ms=0,
            created_at=now,
        )
        session.add(greeting_msg)
        chat_session.message_count = 1
        chat_session.last_message_at = now
        await session.flush()

    await _emit_chat_audit(
        session,
        tid,
        "chat_session.created",
        {
            "session_id": str(chat_session.id),
            "chat_agent_id": str(agent.id),
            "contact_id": str(contact_id) if contact_id else None,
            "channel": chat_session.channel,
        },
        actor_user_id=actor_user_id,
    )
    return chat_session


async def get_session(
    session: AsyncSession,
    tenant: Tenant | UUID,
    session_id: UUID | str,
    *,
    for_update: bool = False,
) -> ChatSession | None:
    tid = _tenant_id(tenant)
    try:
        sid = UUID(str(session_id))
    except (ValueError, TypeError):
        return None
    stmt = select(ChatSession).where(
        ChatSession.tenant_id == tid,
        ChatSession.id == sid,
    )
    if for_update:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_sessions(
    session: AsyncSession,
    tenant: Tenant | UUID,
    *,
    chat_agent_id: UUID | None = None,
    contact_id: UUID | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[ChatSession]:
    tid = _tenant_id(tenant)
    stmt = select(ChatSession).where(ChatSession.tenant_id == tid)
    if chat_agent_id is not None:
        stmt = stmt.where(ChatSession.chat_agent_id == chat_agent_id)
    if contact_id is not None:
        stmt = stmt.where(ChatSession.contact_id == contact_id)
    if status:
        stmt = stmt.where(ChatSession.status == status)
    stmt = (
        stmt.order_by(ChatSession.created_at.desc())
        .limit(max(1, min(limit, 200)))
        .offset(max(0, offset))
    )
    return list((await session.execute(stmt)).scalars().all())


async def list_messages(
    session: AsyncSession,
    tenant: Tenant | UUID,
    session_id: UUID | str,
) -> list[ChatMessage]:
    chat_sess = await get_session(session, tenant, session_id)
    if chat_sess is None:
        raise LookupError("chat session not found")
    stmt = (
        select(ChatMessage)
        .where(
            ChatMessage.tenant_id == chat_sess.tenant_id,
            ChatMessage.session_id == chat_sess.id,
        )
        .order_by(ChatMessage.sequence.asc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def resolve_chat_runtime_config(
    session: AsyncSession,
    tenant: Tenant | UUID,
    agent: ChatAgent,
    *,
    version_number: int | None = None,
):
    """Resolve an immutable ``RuntimeConfig`` from ``ChatAgentVersion`` (Sub-Phase 2E).

    Never reads legacy ``Tenant`` prompt/greeting/model columns.
    """
    from app.runtime.agent_config_resolver import RuntimeConfig

    tid = _tenant_id(tenant)
    target_version = (
        int(version_number)
        if version_number is not None
        else int(agent.published_version or agent.draft_version or 1)
    )
    ver_row = await get_version(session, tid, agent.id, target_version)
    if ver_row is not None and isinstance(ver_row.config, dict):
        cfg = dict(ver_row.config)
        ver_id = ver_row.id
        source = "chat_agent_version"
    else:
        cfg = dict(
            agent.published_config
            if agent.published_config is not None
            else (agent.draft_config or {})
        )
        ver_id = None
        source = "chat_agent_config"

    sys_prompt = str(cfg.get("system_prompt") or f"You are {agent.name}.").strip()
    first_msg = str(cfg.get("first_message") or cfg.get("greeting") or "").strip()
    model = str(cfg.get("model") or "gpt-4o-mini").strip()
    provider = str(cfg.get("provider") or "openai").strip()

    return RuntimeConfig(
        tenant_id=tid,
        agent_id=agent.id,
        agent_version_id=ver_id,
        agent_version_number=target_version,
        source=source,
        system_prompt=sys_prompt,
        greeting=first_msg,
        llm_provider=provider,
        llm_model=model,
    )


def _build_assistant_reply(
    agent: ChatAgent,
    user_text: str,
    *,
    contact: Contact | None,
    memory_facts: dict[str, str],
    dynamic_variables: dict[str, Any],
    runtime_config: Any | None = None,
) -> str:
    if runtime_config is not None and getattr(runtime_config, "system_prompt", None):
        system_prompt = str(runtime_config.system_prompt).strip()
    else:
        cfg = agent.published_config if agent.published_config is not None else (agent.draft_config or {})
        system_prompt = str(cfg.get("system_prompt") or f"You are {agent.name}.").strip()

    context_parts: list[str] = []
    if contact and contact.name:
        context_parts.append(f"Contact: {contact.name} ({contact.phone})")
    if memory_facts:
        facts_str = ", ".join(f"{k}={v}" for k, v in sorted(memory_facts.items()))
        context_parts.append(f"Memory[{facts_str}]")
    if dynamic_variables:
        vars_str = ", ".join(f"{k}={v}" for k, v in sorted(dynamic_variables.items()))
        context_parts.append(f"Vars[{vars_str}]")

    prefix = f"[{agent.name}]"
    if context_parts:
        return f"{prefix} ({' | '.join(context_parts)}) Received: \"{user_text}\". Guided by: {system_prompt[:140]}"
    return f"{prefix} Received: \"{user_text}\". Guided by: {system_prompt[:140]}"


async def send_message(
    session: AsyncSession,
    tenant: Tenant | UUID,
    session_id: UUID | str,
    payload: ChatMessageCreate,
    *,
    actor_user_id: UUID | None = None,
) -> ChatTurnResponse:
    tid = _tenant_id(tenant)
    chat_sess = await get_session(session, tid, session_id, for_update=True)
    if chat_sess is None:
        raise LookupError("chat session not found")
    if chat_sess.status != ChatSessionStatusEnum.ACTIVE.value:
        raise ValueError(f"cannot send message to {chat_sess.status} chat session")

    agent = await get_by_id(session, tid, chat_sess.chat_agent_id)
    if agent is None:
        raise LookupError("chat agent not found")

    max_seq_stmt = select(func.max(ChatMessage.sequence)).where(
        ChatMessage.session_id == chat_sess.id
    )
    current_max_seq = (await session.execute(max_seq_stmt)).scalar_one_or_none() or 0
    user_seq = int(current_max_seq) + 1
    now = _now()

    user_msg = ChatMessage(
        tenant_id=tid,
        session_id=chat_sess.id,
        sequence=user_seq,
        role=(
            payload.role.value
            if hasattr(payload.role, "value")
            else str(payload.role or ChatMessageRole.USER.value)
        ),
        content=payload.content,
        tool_calls=[],
        metadata_json=dict(payload.metadata or {}),
        latency_ms=0,
        created_at=now,
    )
    session.add(user_msg)
    await session.flush()

    contact: Contact | None = None
    memory_facts: dict[str, str] = {}
    memory_keys_saved: list[str] = []

    if chat_sess.contact_id is not None:
        contact = await contact_service.get_by_id(session, tid, chat_sess.contact_id)
        if contact is not None:
            # Persist explicit memory updates or inline [remember key=value] directives
            updates_to_apply: dict[str, str] = dict(payload.memory_updates or {})
            for match in _INLINE_MEMORY_RE.finditer(payload.content):
                updates_to_apply[match.group(1).strip()] = match.group(2).strip()

            for m_key, m_val in updates_to_apply.items():
                entry = await contact_memory_service.save(
                    session,
                    tid,
                    contact,
                    m_key,
                    MemorySaveRequest(
                        value=str(m_val),
                        source=MemorySource.CHAT,
                        source_ref=str(chat_sess.id),
                        confidence=0.95,
                        importance=75,
                    ),
                    created_by=actor_user_id,
                )
                memory_keys_saved.append(entry.key)

            entries = await contact_memory_service.list_entries(session, tid, contact)
            for e in entries:
                memory_facts[e.key] = e.value

    rt_cfg = await resolve_chat_runtime_config(
        session,
        tid,
        agent,
        version_number=chat_sess.chat_agent_version,
    )
    reply_text = _build_assistant_reply(
        agent,
        payload.content,
        contact=contact,
        memory_facts=memory_facts,
        dynamic_variables=chat_sess.dynamic_variables or {},
        runtime_config=rt_cfg,
    )
    tool_calls_log = (
        [{"tool": "save_contact_memory", "keys": memory_keys_saved}]
        if memory_keys_saved
        else []
    )
    assistant_seq = user_seq + 1
    assistant_msg = ChatMessage(
        tenant_id=tid,
        session_id=chat_sess.id,
        sequence=assistant_seq,
        role=ChatMessageRoleEnum.ASSISTANT.value,
        content=reply_text,
        tool_calls=tool_calls_log,
        metadata_json={
            "chat_agent_version": chat_sess.chat_agent_version,
            "memory_keys_used": sorted(memory_facts.keys()),
            "memory_keys_saved": memory_keys_saved,
        },
        latency_ms=12,
        created_at=_now(),
    )
    session.add(assistant_msg)

    chat_sess.message_count = assistant_seq
    chat_sess.last_message_at = assistant_msg.created_at
    chat_sess.updated_at = assistant_msg.created_at
    await session.flush()

    return ChatTurnResponse(
        session=to_session_response(chat_sess),
        user_message=to_message_response(user_msg),
        assistant_message=to_message_response(assistant_msg),
        memory_keys_used=sorted(memory_facts.keys()),
        memory_keys_saved=memory_keys_saved,
    )


async def end_session(
    session: AsyncSession,
    tenant: Tenant | UUID,
    session_id: UUID | str,
    *,
    status: str = ChatSessionStatus.COMPLETED.value,
    actor_user_id: UUID | None = None,
) -> ChatSession | None:
    chat_sess = await get_session(session, tenant, session_id, for_update=True)
    if chat_sess is None:
        return None
    now = _now()
    chat_sess.status = status
    chat_sess.ended_at = now
    chat_sess.updated_at = now
    await session.flush()
    await _emit_chat_audit(
        session,
        chat_sess.tenant_id,
        "chat_session.ended",
        {"session_id": str(chat_sess.id), "status": status},
        actor_user_id=actor_user_id,
    )
    return chat_sess
