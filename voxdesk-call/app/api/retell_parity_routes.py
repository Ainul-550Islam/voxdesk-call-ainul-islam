"""HTTP API routes for Retell parity surfaces (Contacts, Memory, Chat Agents, Chat Sessions/Messages, Dynamic Variables, Agent Transfers).

Exposes tenant-isolated REST endpoints backed by ``app.db.retell_models`` and
``app.services.{contact_service,contact_memory_service,chat_agent_service,dynamic_variable_service,agent_transfer_service}``.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.db.session import get_session
from app.domain.chat_agent_models import (
    ChatAgentCreate,
    ChatAgentPublishRequest,
    ChatAgentResponse,
    ChatAgentRollbackRequest,
    ChatAgentUpdate,
    ChatAgentValidationResult,
    ChatAgentVersionResponse,
    ChatMessageCreate,
    ChatMessageResponse,
    ChatSessionCreate,
    ChatSessionResponse,
    ChatTurnResponse,
)
from app.domain.contact_memory_models import MemoryEntryResponse, MemorySaveRequest
from app.domain.contact_models import ContactCreate, ContactResponse, ContactUpdate
from app.domain.dynamic_variable_models import (
    DynamicVariableCreate,
    DynamicVariableResponse,
    DynamicVariableUpdate,
)
from app.domain.transfer_state_machine import TransferMode, TransferState
from app.services import (
    agent_transfer_service,
    chat_agent_service,
    contact_memory_service,
    contact_service,
    dynamic_variable_service,
)
from app.services.chat_agent_service import (
    ChatAgentConflictError,
    to_agent_response,
    to_message_response,
    to_session_response,
    to_version_response,
)

router = APIRouter(prefix="/api", tags=["retell-parity"])


class AgentTransferCreatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: TransferMode = Field(default=TransferMode.AGENT_TO_AGENT)
    idempotency_key: str = Field(..., min_length=1, max_length=128)
    call_id: UUID | None = None
    contact_id: UUID | None = None
    from_agent_ref: str = Field(default="", max_length=120)
    to_agent_ref: str = Field(default="", max_length=120)
    to_agent_id: UUID | None = None
    destination: str = Field(default="", max_length=120)
    reason: str = Field(default="", max_length=400)
    context_snapshot: dict[str, Any] = Field(default_factory=dict)


class AgentTransferTransitionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: TransferState
    failure_reason: str | None = Field(default=None, max_length=300)
    detail: dict[str, Any] = Field(default_factory=dict)


class ChatSessionEndPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = Field(default="completed", pattern="^(completed|escalated|expired)$")


# ------------------------------------------------------------------ Contacts


@router.post("/contacts", response_model=ContactResponse, status_code=status.HTTP_201_CREATED)
async def create_contact(
    payload: ContactCreate,
    ctx: TenantContext = Depends(require_permission(Permission.LEAD_CREATE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        res = await contact_service.create(
            session, ctx.tenant_id, payload, actor_user_id=ctx.user_id
        )
        await session.commit()
        return ContactResponse.model_validate(res.contact)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.get("/contacts", response_model=list[ContactResponse])
async def list_contacts(
    lifecycle: str | None = Query(default=None),
    search: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    ctx: TenantContext = Depends(require_permission(Permission.LEAD_READ)),
    session: AsyncSession = Depends(get_session),
):
    rows = await contact_service.list_contacts(
        session,
        ctx.tenant_id,
        lifecycle=lifecycle,
        search=search,
        limit=limit,
        offset=offset,
    )
    return [ContactResponse.model_validate(r) for r in rows]


@router.get("/contacts/{contact_id}", response_model=ContactResponse)
async def get_contact(
    contact_id: UUID,
    ctx: TenantContext = Depends(require_permission(Permission.LEAD_READ)),
    session: AsyncSession = Depends(get_session),
):
    row = await contact_service.get_by_id(session, ctx.tenant_id, contact_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Contact not found"
        )
    return ContactResponse.model_validate(row)


@router.patch("/contacts/{contact_id}", response_model=ContactResponse)
async def update_contact(
    contact_id: UUID,
    payload: ContactUpdate,
    ctx: TenantContext = Depends(require_permission(Permission.LEAD_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        row = await contact_service.update(
            session,
            ctx.tenant_id,
            contact_id,
            payload,
            actor_user_id=ctx.user_id,
        )
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Contact not found"
            )
        await session.commit()
        return ContactResponse.model_validate(row)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.post("/contacts/{contact_id}/archive", response_model=ContactResponse)
async def archive_contact(
    contact_id: UUID,
    ctx: TenantContext = Depends(require_permission(Permission.LEAD_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    row = await contact_service.archive(
        session, ctx.tenant_id, contact_id, actor_user_id=ctx.user_id
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Contact not found"
        )
    await session.commit()
    return ContactResponse.model_validate(row)


@router.post("/contacts/{contact_id}/restore", response_model=ContactResponse)
async def restore_contact(
    contact_id: UUID,
    ctx: TenantContext = Depends(require_permission(Permission.LEAD_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    row = await contact_service.restore(
        session, ctx.tenant_id, contact_id, actor_user_id=ctx.user_id
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Contact not found"
        )
    await session.commit()
    return ContactResponse.model_validate(row)


@router.delete("/contacts/{contact_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_contact(
    contact_id: UUID,
    ctx: TenantContext = Depends(require_permission(Permission.LEAD_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    deleted = await contact_service.delete_contact(
        session, ctx.tenant_id, contact_id, actor_user_id=ctx.user_id
    )
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Contact not found"
        )
    await session.commit()


# ----------------------------------------------------------- Contact Memory


@router.put("/contacts/{contact_id}/memory/{key}", response_model=MemoryEntryResponse)
async def save_contact_memory_entry(
    contact_id: UUID,
    key: str,
    payload: MemorySaveRequest,
    ctx: TenantContext = Depends(require_permission(Permission.LEAD_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    contact = await contact_service.get_by_id(session, ctx.tenant_id, contact_id)
    if contact is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Contact not found"
        )
    try:
        entry = await contact_memory_service.save(
            session,
            ctx.tenant_id,
            contact,
            key,
            payload,
            created_by=ctx.user_id,
        )
        await session.commit()
        return MemoryEntryResponse.model_validate(entry)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.get("/contacts/{contact_id}/memory", response_model=list[MemoryEntryResponse])
async def list_contact_memory_entries(
    contact_id: UUID,
    include_expired: bool = Query(default=False),
    ctx: TenantContext = Depends(require_permission(Permission.LEAD_READ)),
    session: AsyncSession = Depends(get_session),
):
    contact = await contact_service.get_by_id(session, ctx.tenant_id, contact_id)
    if contact is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Contact not found"
        )
    entries = await contact_memory_service.list_entries(
        session,
        ctx.tenant_id,
        contact,
        include_expired=include_expired,
    )
    return [MemoryEntryResponse.model_validate(e) for e in entries]


@router.get("/contacts/{contact_id}/memory/{key}", response_model=MemoryEntryResponse)
async def get_contact_memory_entry(
    contact_id: UUID,
    key: str,
    ctx: TenantContext = Depends(require_permission(Permission.LEAD_READ)),
    session: AsyncSession = Depends(get_session),
):
    contact = await contact_service.get_by_id(session, ctx.tenant_id, contact_id)
    if contact is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Contact not found"
        )
    try:
        entry = await contact_memory_service.get_entry(
            session, ctx.tenant_id, contact, key
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Memory key not found"
        )
    return MemoryEntryResponse.model_validate(entry)


@router.delete(
    "/contacts/{contact_id}/memory/{key}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_contact_memory_entry(
    contact_id: UUID,
    key: str,
    ctx: TenantContext = Depends(require_permission(Permission.LEAD_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    contact = await contact_service.get_by_id(session, ctx.tenant_id, contact_id)
    if contact is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Contact not found"
        )
    try:
        deleted = await contact_memory_service.delete_entry(
            session,
            ctx.tenant_id,
            contact,
            key,
            actor_user_id=ctx.user_id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Memory key not found"
        )
    await session.commit()


# -------------------------------------------------------------- Chat Agents


@router.post(
    "/chat-agents",
    response_model=ChatAgentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_chat_agent(
    payload: ChatAgentCreate,
    response: Response,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        row = await chat_agent_service.create(
            session, ctx.tenant_id, payload, created_by=ctx.user_id
        )
        await session.commit()
        out = to_agent_response(row)
        if out.etag:
            response.headers["ETag"] = out.etag
        return out
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc


@router.get("/chat-agents", response_model=list[ChatAgentResponse])
async def list_chat_agents(
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    rows = await chat_agent_service.list_agents(
        session, ctx.tenant_id, status=status_filter, limit=limit, offset=offset
    )
    return [to_agent_response(r) for r in rows]


@router.get("/chat-agents/{agent_id}", response_model=ChatAgentResponse)
async def get_chat_agent(
    agent_id: UUID,
    response: Response,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    row = await chat_agent_service.get_by_id(session, ctx.tenant_id, agent_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Chat agent not found"
        )
    out = to_agent_response(row)
    if out.etag:
        response.headers["ETag"] = out.etag
    return out


@router.patch("/chat-agents/{agent_id}", response_model=ChatAgentResponse)
async def update_chat_agent(
    agent_id: UUID,
    payload: ChatAgentUpdate,
    response: Response,
    if_match: str | None = Header(default=None, alias="If-Match"),
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        row = await chat_agent_service.update_draft(
            session,
            ctx.tenant_id,
            agent_id,
            payload,
            if_match=if_match,
            actor_user_id=ctx.user_id,
        )
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Chat agent not found"
            )
        await session.commit()
        out = to_agent_response(row)
        if out.etag:
            response.headers["ETag"] = out.etag
        return out
    except ChatAgentConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "etag_conflict",
                "message": "Conflict: Chat agent draft was modified by another session. Reload before saving.",
                "detail": str(exc),
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.post(
    "/chat-agents/{agent_id}/validate", response_model=ChatAgentValidationResult
)
async def validate_chat_agent(
    agent_id: UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        return await chat_agent_service.validate_draft(session, ctx.tenant_id, agent_id)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc


@router.post("/chat-agents/{agent_id}/publish", response_model=ChatAgentVersionResponse)
async def publish_chat_agent(
    agent_id: UUID,
    payload: ChatAgentPublishRequest,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        _agent, ver = await chat_agent_service.publish(
            session, ctx.tenant_id, agent_id, payload, actor_user_id=ctx.user_id
        )
        await session.commit()
        return to_version_response(ver)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except ValueError as exc:
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.get(
    "/chat-agents/{agent_id}/versions", response_model=list[ChatAgentVersionResponse]
)
async def list_chat_agent_versions(
    agent_id: UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    agent = await chat_agent_service.get_by_id(session, ctx.tenant_id, agent_id)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Chat agent not found"
        )
    versions = await chat_agent_service.list_versions(session, ctx.tenant_id, agent_id)
    return [to_version_response(v) for v in versions]


@router.get(
    "/chat-agents/{agent_id}/versions/{version_number:int}",
    response_model=ChatAgentVersionResponse,
)
async def get_chat_agent_version(
    agent_id: UUID,
    version_number: int,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    ver = await chat_agent_service.get_version(
        session, ctx.tenant_id, agent_id, version_number
    )
    if ver is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Chat agent version {version_number} not found",
        )
    return to_version_response(ver)


@router.post(
    "/chat-agents/{agent_id}/rollback", response_model=ChatAgentVersionResponse
)
async def rollback_chat_agent_version(
    agent_id: UUID,
    payload: ChatAgentRollbackRequest,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        _agent, minted = await chat_agent_service.rollback_to_version(
            session,
            ctx.tenant_id,
            agent_id,
            payload.target_version,
            reason=payload.reason,
            actor_user_id=ctx.user_id,
        )
        await session.commit()
        return to_version_response(minted)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.post("/chat-agents/{agent_id}/archive", response_model=ChatAgentResponse)
async def archive_chat_agent(
    agent_id: UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    row = await chat_agent_service.archive_agent(
        session, ctx.tenant_id, agent_id, actor_user_id=ctx.user_id
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Chat agent not found"
        )
    await session.commit()
    return to_agent_response(row)


@router.post("/chat-agents/{agent_id}/restore", response_model=ChatAgentResponse)
async def restore_chat_agent(
    agent_id: UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    row = await chat_agent_service.restore_agent(
        session, ctx.tenant_id, agent_id, actor_user_id=ctx.user_id
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Chat agent not found"
        )
    await session.commit()
    return to_agent_response(row)


# --------------------------------------------------- Chat Sessions & Messages


@router.post(
    "/chat-agents/{agent_id}/sessions",
    response_model=ChatSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_chat_agent_session(
    agent_id: UUID,
    payload: ChatSessionCreate,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        row = await chat_agent_service.create_session(
            session,
            ctx.tenant_id,
            agent_id,
            payload,
            actor_user_id=ctx.user_id,
        )
        await session.commit()
        return to_session_response(row)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.post(
    "/chat-sessions",
    response_model=ChatSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_chat_session_direct(
    payload: ChatSessionCreate,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    if payload.chat_agent_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="chat_agent_id is required when creating a session via /api/chat-sessions",
        )
    try:
        row = await chat_agent_service.create_session(
            session,
            ctx.tenant_id,
            payload.chat_agent_id,
            payload,
            actor_user_id=ctx.user_id,
        )
        await session.commit()
        return to_session_response(row)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.get("/chat-agents/{agent_id}/sessions", response_model=list[ChatSessionResponse])
async def list_chat_agent_sessions(
    agent_id: UUID,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    agent = await chat_agent_service.get_by_id(session, ctx.tenant_id, agent_id)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Chat agent not found"
        )
    rows = await chat_agent_service.list_sessions(
        session,
        ctx.tenant_id,
        chat_agent_id=agent_id,
        status=status_filter,
        limit=limit,
        offset=offset,
    )
    return [to_session_response(r) for r in rows]


@router.get("/chat-sessions", response_model=list[ChatSessionResponse])
async def list_all_chat_sessions(
    chat_agent_id: UUID | None = Query(default=None),
    contact_id: UUID | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    rows = await chat_agent_service.list_sessions(
        session,
        ctx.tenant_id,
        chat_agent_id=chat_agent_id,
        contact_id=contact_id,
        status=status_filter,
        limit=limit,
        offset=offset,
    )
    return [to_session_response(r) for r in rows]


@router.get("/chat-sessions/{session_id}", response_model=ChatSessionResponse)
async def get_chat_session(
    session_id: UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    row = await chat_agent_service.get_session(session, ctx.tenant_id, session_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Chat session not found"
        )
    return to_session_response(row)


@router.get(
    "/chat-sessions/{session_id}/messages", response_model=list[ChatMessageResponse]
)
async def list_chat_session_messages(
    session_id: UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        rows = await chat_agent_service.list_messages(
            session, ctx.tenant_id, session_id
        )
        return [to_message_response(r) for r in rows]
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc


@router.post(
    "/chat-sessions/{session_id}/messages",
    response_model=ChatTurnResponse,
    status_code=status.HTTP_201_CREATED,
)
async def send_chat_session_message(
    session_id: UUID,
    payload: ChatMessageCreate,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        turn = await chat_agent_service.send_message(
            session,
            ctx.tenant_id,
            session_id,
            payload,
            actor_user_id=ctx.user_id,
        )
        await session.commit()
        return turn
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.post("/chat-sessions/{session_id}/end", response_model=ChatSessionResponse)
async def end_chat_session(
    session_id: UUID,
    payload: ChatSessionEndPayload | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    row = await chat_agent_service.end_session(
        session,
        ctx.tenant_id,
        session_id,
        status=(payload.status if payload else "completed"),
        actor_user_id=ctx.user_id,
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Chat session not found"
        )
    await session.commit()
    return to_session_response(row)


# -------------------------------------------------------- Dynamic Variables


@router.post(
    "/dynamic-variables",
    response_model=DynamicVariableResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_dynamic_variable(
    payload: DynamicVariableCreate,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        row = await dynamic_variable_service.create(
            session, ctx.tenant_id, payload, created_by=ctx.user_id
        )
        await session.commit()
        return DynamicVariableResponse.model_validate(row)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.get("/dynamic-variables", response_model=list[DynamicVariableResponse])
async def list_dynamic_variables(
    chat_agent_id: UUID | None = Query(default=None),
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    rows = await dynamic_variable_service.list_variables(
        session, ctx.tenant_id, chat_agent_id=chat_agent_id
    )
    return [DynamicVariableResponse.model_validate(r) for r in rows]


@router.patch("/dynamic-variables/{variable_id}", response_model=DynamicVariableResponse)
async def update_dynamic_variable(
    variable_id: UUID,
    payload: DynamicVariableUpdate,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    row = await dynamic_variable_service.update(
        session, ctx.tenant_id, variable_id, payload
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Dynamic variable not found"
        )
    await session.commit()
    return DynamicVariableResponse.model_validate(row)


@router.delete(
    "/dynamic-variables/{variable_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_dynamic_variable(
    variable_id: UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    deleted = await dynamic_variable_service.delete_variable(
        session, ctx.tenant_id, variable_id
    )
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Dynamic variable not found"
        )
    await session.commit()


# ---------------------------------------------------------- Agent Transfers


@router.post(
    "/agent-transfers", response_model=dict, status_code=status.HTTP_201_CREATED
)
async def request_agent_transfer(
    payload: AgentTransferCreatePayload,
    ctx: TenantContext = Depends(require_permission(Permission.CALL_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        transfer, created = await agent_transfer_service.request_transfer(
            session,
            ctx.tenant_id,
            mode=payload.mode,
            idempotency_key=payload.idempotency_key,
            call_id=payload.call_id,
            contact_id=payload.contact_id,
            from_agent_ref=payload.from_agent_ref,
            to_agent_ref=payload.to_agent_ref,
            to_agent_id=payload.to_agent_id,
            destination=payload.destination,
            reason=payload.reason,
            context_snapshot=payload.context_snapshot,
            requested_by=ctx.user_id,
        )
        await session.commit()
        return {
            "id": str(transfer.id),
            "tenant_id": str(transfer.tenant_id),
            "mode": transfer.mode,
            "status": transfer.status,
            "created": created,
            "idempotency_key": transfer.idempotency_key,
            "created_at": transfer.created_at.isoformat(),
        }
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.get("/agent-transfers", response_model=list[dict])
async def list_agent_transfers(
    status_filter: str | None = Query(default=None, alias="status"),
    call_id: UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    ctx: TenantContext = Depends(require_permission(Permission.CALL_READ)),
    session: AsyncSession = Depends(get_session),
):
    rows = await agent_transfer_service.list_transfers(
        session,
        ctx.tenant_id,
        status=status_filter,
        call_id=call_id,
        limit=limit,
        offset=offset,
    )
    return [
        {
            "id": str(r.id),
            "tenant_id": str(r.tenant_id),
            "call_id": str(r.call_id) if r.call_id else None,
            "contact_id": str(r.contact_id) if r.contact_id else None,
            "mode": r.mode,
            "status": r.status,
            "from_agent_ref": r.from_agent_ref,
            "to_agent_ref": r.to_agent_ref,
            "to_agent_id": str(r.to_agent_id) if r.to_agent_id else None,
            "destination": r.destination,
            "reason": r.reason,
            "failure_reason": r.failure_reason,
            "created_at": r.created_at.isoformat(),
            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
        }
        for r in rows
    ]


@router.post("/agent-transfers/{transfer_id}/transition", response_model=dict)
async def transition_agent_transfer(
    transfer_id: UUID,
    payload: AgentTransferTransitionPayload,
    ctx: TenantContext = Depends(require_permission(Permission.CALL_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        row = await agent_transfer_service.transition_transfer(
            session,
            ctx.tenant_id,
            transfer_id,
            payload.status,
            failure_reason=payload.failure_reason,
            actor_user_id=ctx.user_id,
            detail=payload.detail,
        )
        await session.commit()
        return {
            "id": str(row.id),
            "status": row.status,
            "failure_reason": row.failure_reason,
            "updated_at": row.updated_at.isoformat(),
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        }
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


# ------------------------------------ Prompt 3: Retell-Parity Testing & Evals


@router.get("/test-suites")
async def list_retell_test_suites(
    status_filter: str | None = Query(default=None, alias="status"),
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    from app.services import simulation_service

    return await simulation_service.list_test_suites(
        session, ctx.tenant_id, status=status_filter
    )


@router.get("/test-runs")
async def list_retell_test_runs(
    suite_id: UUID | None = Query(default=None),
    agent_id: str | None = Query(default=None),
    mode: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    from app.services import simulation_service

    return await simulation_service.list_test_runs(
        session,
        ctx.tenant_id,
        suite_id=suite_id,
        agent_id=agent_id,
        mode=mode,
        status=status_filter,
        limit=limit,
    )
