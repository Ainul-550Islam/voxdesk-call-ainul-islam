"""Unified Helpdesk v2 API routes (tickets from call, ticket detail, comments, requester lookup, voice logs)."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.integrations_v2_routes import PERM_INT_ADMIN, get_int2_http_client
from app.audit.service import record_enterprise_audit
from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.db.session import get_session
from app.integrations_v2.core.connection_store import get_tenant_connection
from app.integrations_v2.core.errors import IntegrationV2Error
from app.integrations_v2.core.writeback import (
    CallWritebackInput,
    execute_writeback_now,
)
from app.integrations_v2.helpdesk.models import (
    HelpdeskProviderV2,
    TicketCreateInput,
    TicketPriority,
    TicketStatus,
    TicketUpdateInput,
)
from app.integrations_v2.helpdesk.ticket_service import (
    create_ticket_from_call,
    find_or_create_requester,
)
from app.integrations_v2.register_all import instantiate_provider_v2
from app.resilience.idempotency import (
    IdempotencyError,
    claim_request,
    complete_request,
)

router = APIRouter(prefix="/api/v1/helpdesk", tags=["helpdesk-v2"])


class CreateTicketRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_id: uuid.UUID
    call_id: str = Field(default="", max_length=120)
    subject: str = Field(default="", max_length=240)
    description: str = Field(default="", max_length=8000)
    priority: Literal["low", "normal", "high", "urgent"] = "normal"
    status: Literal["new", "open", "pending", "resolved", "closed"] = "open"
    requester_name: str = Field(default="", max_length=160)
    requester_email: str = Field(default="", max_length=254)
    requester_phone: str = Field(default="", max_length=32)
    tags: list[str] = Field(default_factory=list)
    custom_fields: dict[str, Any] = Field(default_factory=dict)


class AddTicketCommentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_id: uuid.UUID
    body: str = Field(min_length=1, max_length=8000)
    public: bool = False


class UpdateTicketRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_id: uuid.UUID
    subject: str | None = Field(default=None, max_length=240)
    status: Literal["new", "open", "pending", "resolved", "closed"] | None = None
    priority: Literal["low", "normal", "high", "urgent"] | None = None
    comment_body: str | None = Field(default=None, max_length=8000)
    comment_public: bool = False
    tags: list[str] | None = None
    custom_fields: dict[str, Any] | None = None


class LogCallTicketRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_id: uuid.UUID
    call_id: str = Field(min_length=1, max_length=120)
    subject: str = Field(default="Voice Call", max_length=240)
    summary: str = Field(min_length=1, max_length=8000)
    disposition: str = Field(default="completed", max_length=64)
    duration_seconds: int = Field(default=0, ge=0)
    direction: Literal["inbound", "outbound"] = "inbound"
    caller_phone: str = Field(default="", max_length=32)
    caller_email: str = Field(default="", max_length=254)
    external_ticket_id: str = Field(default="", max_length=160)
    recording_id: uuid.UUID | None = None


async def _load_helpdesk_provider(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    connection_id: uuid.UUID,
    http_client: Any,
) -> tuple[Any, HelpdeskProviderV2]:
    try:
        conn_row = await get_tenant_connection(
            db, tenant_id=tenant_id, connection_id=connection_id
        )
    except IntegrationV2Error as exc:
        raise HTTPException(status_code=404, detail=exc.safe_message) from exc

    provider = instantiate_provider_v2(conn_row, http_client=http_client)
    if not isinstance(provider, HelpdeskProviderV2):
        raise HTTPException(
            status_code=422, detail=f"Connection {conn_row.provider} is not a helpdesk provider"
        )
    return conn_row, provider


@router.get("/requesters/lookup")
async def lookup_helpdesk_requester_route(
    connection_id: uuid.UUID,
    phone: str = Query(default="", max_length=32),
    email: str = Query(default="", max_length=254),
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_READ)),
    db: AsyncSession = Depends(get_session),
    http_client: Any = Depends(get_int2_http_client),
) -> dict[str, Any]:
    if not (phone.strip() or email.strip()):
        raise HTTPException(status_code=422, detail="Provide 'phone' or 'email'")
    _, provider = await _load_helpdesk_provider(
        db, tenant_id=ctx.tenant.id, connection_id=connection_id, http_client=http_client
    )
    try:
        req = await find_or_create_requester(provider, email=email, phone=phone)
        tickets = await provider.search_tickets(email=email, phone=phone, limit=10)
        return {"requester": req, "tickets": [t.to_dict() for t in tickets]}
    except IntegrationV2Error as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.safe_message) from exc


@router.get("/tickets/search")
async def search_helpdesk_tickets_route(
    connection_id: uuid.UUID,
    email: str = Query(default="", max_length=254),
    phone: str = Query(default="", max_length=32),
    query: str = Query(default="", max_length=200),
    limit: int = Query(default=10, ge=1, le=50),
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_READ)),
    db: AsyncSession = Depends(get_session),
    http_client: Any = Depends(get_int2_http_client),
) -> dict[str, Any]:
    _, provider = await _load_helpdesk_provider(
        db, tenant_id=ctx.tenant.id, connection_id=connection_id, http_client=http_client
    )
    try:
        tickets = await provider.search_tickets(
            email=email, phone=phone, query=query, limit=limit
        )
        return {"tickets": [t.to_dict() for t in tickets]}
    except IntegrationV2Error as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.safe_message) from exc


@router.post("/tickets", status_code=201)
async def create_helpdesk_ticket_route(
    req: CreateTicketRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ctx: TenantContext = Depends(require_permission(PERM_INT_ADMIN)),
    db: AsyncSession = Depends(get_session),
    http_client: Any = Depends(get_int2_http_client),
) -> dict[str, Any]:
    conn_row, provider = await _load_helpdesk_provider(
        db, tenant_id=ctx.tenant.id, connection_id=req.connection_id, http_client=http_client
    )
    claim = None
    if idempotency_key:
        try:
            claim = await claim_request(
                db,
                tenant_id=ctx.tenant.id,
                operation="int2.helpdesk.create_ticket",
                key=idempotency_key,
                request_data={"connection_id": str(req.connection_id), "call_id": req.call_id},
            )
            if claim.replayed and claim.receipt.resource_id:
                existing_ticket = await provider.get_ticket(claim.receipt.resource_id)
                return {"ticket": existing_ticket.to_dict(), "replayed": True}
        except IdempotencyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    try:
        if req.call_id:
            ticket = await create_ticket_from_call(
                db,
                connection=conn_row,
                call_id=req.call_id,
                subject=req.subject,
                summary_override=req.description,
                priority=TicketPriority(req.priority),
                requester_email=req.requester_email,
                requester_phone=req.requester_phone,
                requester_name=req.requester_name,
                http_client=http_client,
            )
        else:
            if not (req.subject.strip() and req.description.strip()):
                raise HTTPException(
                    status_code=422,
                    detail="Either 'call_id' or non-empty 'subject' and 'description' are required",
                )
            ticket = await provider.create_ticket(
                TicketCreateInput(
                    subject=req.subject,
                    description=req.description,
                    priority=TicketPriority(req.priority),
                    status=TicketStatus(req.status),
                    requester_name=req.requester_name,
                    requester_email=req.requester_email,
                    requester_phone=req.requester_phone,
                    tags=req.tags,
                    custom_fields=req.custom_fields,
                    idempotency_key=idempotency_key or "",
                )
            )

        if claim is not None:
            await complete_request(
                db, claim.receipt, resource_type="helpdesk_ticket", resource_id=ticket.external_id
            )
        await record_enterprise_audit(
            db,
            tenant_id=ctx.tenant.id,
            user_id=ctx.user.id,
            action="integrations_v2.helpdesk.ticket_created",
            resource_type="helpdesk_ticket",
            resource_id=ticket.external_id,
            detail={"provider": conn_row.provider},
        )
        await db.commit()
        return {"ticket": ticket.to_dict(), "replayed": False}
    except IntegrationV2Error as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.http_status, detail=exc.safe_message) from exc


@router.get("/tickets/{ticket_id}")
async def get_helpdesk_ticket_route(
    ticket_id: str,
    connection_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_READ)),
    db: AsyncSession = Depends(get_session),
    http_client: Any = Depends(get_int2_http_client),
) -> dict[str, Any]:
    _, provider = await _load_helpdesk_provider(
        db, tenant_id=ctx.tenant.id, connection_id=connection_id, http_client=http_client
    )
    try:
        ticket = await provider.get_ticket(ticket_id)
        return {"ticket": ticket.to_dict()}
    except IntegrationV2Error as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.safe_message) from exc


@router.post("/tickets/{ticket_id}/comments", status_code=201)
async def add_helpdesk_ticket_comment_route(
    ticket_id: str,
    req: AddTicketCommentRequest,
    ctx: TenantContext = Depends(require_permission(PERM_INT_ADMIN)),
    db: AsyncSession = Depends(get_session),
    http_client: Any = Depends(get_int2_http_client),
) -> dict[str, Any]:
    conn_row, provider = await _load_helpdesk_provider(
        db, tenant_id=ctx.tenant.id, connection_id=req.connection_id, http_client=http_client
    )
    try:
        comment = await provider.add_comment(
            ticket_id, body=req.body, public=req.public
        )
        await record_enterprise_audit(
            db,
            tenant_id=ctx.tenant.id,
            user_id=ctx.user.id,
            action="integrations_v2.helpdesk.comment_added",
            resource_type="helpdesk_ticket",
            resource_id=ticket_id,
            detail={"provider": conn_row.provider, "public": req.public},
        )
        await db.commit()
        return {
            "comment": {
                "external_id": comment.external_id,
                "ticket_id": comment.ticket_id,
                "public": comment.public,
            }
        }
    except IntegrationV2Error as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.http_status, detail=exc.safe_message) from exc


@router.patch("/tickets/{ticket_id}")
async def update_helpdesk_ticket_route(
    ticket_id: str,
    req: UpdateTicketRequest,
    ctx: TenantContext = Depends(require_permission(PERM_INT_ADMIN)),
    db: AsyncSession = Depends(get_session),
    http_client: Any = Depends(get_int2_http_client),
) -> dict[str, Any]:
    conn_row, provider = await _load_helpdesk_provider(
        db, tenant_id=ctx.tenant.id, connection_id=req.connection_id, http_client=http_client
    )
    try:
        ticket = await provider.update_ticket(
            ticket_id,
            TicketUpdateInput(
                subject=req.subject,
                status=TicketStatus(req.status) if req.status else None,
                priority=TicketPriority(req.priority) if req.priority else None,
                comment_body=req.comment_body,
                comment_public=req.comment_public,
                tags=req.tags,
                custom_fields=req.custom_fields,
            ),
        )
        await record_enterprise_audit(
            db,
            tenant_id=ctx.tenant.id,
            user_id=ctx.user.id,
            action="integrations_v2.helpdesk.ticket_updated",
            resource_type="helpdesk_ticket",
            resource_id=ticket_id,
            detail={"provider": conn_row.provider},
        )
        await db.commit()
        return {"ticket": ticket.to_dict()}
    except IntegrationV2Error as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.http_status, detail=exc.safe_message) from exc


@router.post("/calls/log")
async def log_helpdesk_voice_call_route(
    req: LogCallTicketRequest,
    ctx: TenantContext = Depends(require_permission(PERM_INT_ADMIN)),
    db: AsyncSession = Depends(get_session),
    http_client: Any = Depends(get_int2_http_client),
) -> dict[str, Any]:
    conn_row, provider = await _load_helpdesk_provider(
        db, tenant_id=ctx.tenant.id, connection_id=req.connection_id, http_client=http_client
    )
    try:
        ledger, executed = await execute_writeback_now(
            db,
            connection=conn_row,
            writeback_input=CallWritebackInput(
                call_id=req.call_id,
                subject=req.subject,
                summary=req.summary,
                disposition=req.disposition,
                duration_seconds=req.duration_seconds,
                direction=req.direction,
                caller_phone=req.caller_phone,
                caller_email=req.caller_email,
                external_ticket_id=req.external_ticket_id,
                recording_id=req.recording_id,
            ),
            dispatcher=provider.log_voice_call,
            action="helpdesk_call_log",
            entity="ticket_call_log",
        )
        await record_enterprise_audit(
            db,
            tenant_id=ctx.tenant.id,
            user_id=ctx.user.id,
            action="integrations_v2.helpdesk.call_logged",
            resource_type="writeback_ledger",
            resource_id=str(ledger.id),
            detail={"provider": conn_row.provider, "deduplicated": not executed},
        )
        await db.commit()
        return {
            "ledger_id": str(ledger.id),
            "external_id": ledger.external_id,
            "status": ledger.status,
            "deduplicated": not executed,
        }
    except IntegrationV2Error as exc:
        await db.commit()
        raise HTTPException(status_code=exc.http_status, detail=exc.safe_message) from exc
