"""Collaboration v2 API routes (notification rule CRUD, idempotent meetings, real notify/test, Slack endpoints)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.integrations_v2_routes import PERM_INT_ADMIN, get_int2_http_client
from app.audit.service import record_enterprise_audit
from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.db.integrations_v2_models import IntegrationConnection, NotificationRule
from app.db.session import get_session
from app.integrations_v2.collab.meeting_service import schedule_meeting
from app.integrations_v2.collab.notification_rules import (
    SUPPORTED_NOTIFICATION_EVENTS,
    dispatch_notification_rule,
)
from app.integrations_v2.collab.slack import (
    SlackProvider,
    build_slack_call_blocks,
    handle_slack_interaction,
    handle_slack_slash_command,
)
from app.integrations_v2.collab.teams import TeamsProvider, build_teams_call_adaptive_card
from app.integrations_v2.collab.zoom import ZoomProvider
from app.integrations_v2.core.connection_store import get_tenant_connection
from app.integrations_v2.core.errors import IntegrationV2Error, WebhookSignatureError
from app.integrations_v2.register_all import instantiate_provider_v2

router = APIRouter(prefix="/api/v1/collab", tags=["collab-v2"])
_slack_public_router = APIRouter(prefix="/webhooks/int2/slack", tags=["collab-v2"])


class NotificationRuleUpsertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_id: uuid.UUID
    event_type: str = Field(min_length=3, max_length=64)
    team_id: str = Field(default="", max_length=120)
    channel_id: str = Field(min_length=1, max_length=160)
    channel_name: str = Field(default="", max_length=160)
    template: str = Field(default="", max_length=2000)
    rate_limit_per_min: int = Field(default=30, ge=1, le=600)
    dedupe_window_seconds: int = Field(default=120, ge=0, le=86400)
    enabled: bool = True
    filter_config: dict[str, Any] = Field(default_factory=dict)


class CreateCollabMeetingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_id: uuid.UUID
    topic: str = Field(min_length=1, max_length=200)
    start_time_iso: str = Field(default="", max_length=64)
    duration_minutes: int = Field(default=30, ge=5, le=240)
    lead_id: str = Field(default="", max_length=120)
    call_id: str = Field(default="", max_length=120)
    agenda: str = Field(default="", max_length=2000)


class NotifyTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rule_id: uuid.UUID | None = None
    connection_id: uuid.UUID | None = None
    team_id: str = Field(default="", max_length=120)
    channel_id: str = Field(default="", max_length=160)
    title: str = Field(default="VoxDesk Notification Test", max_length=150)
    summary: str = Field(default="Live channel delivery verification", min_length=1, max_length=2800)
    caller_phone: str = Field(default="", max_length=32)


def _serialize_rule(r: NotificationRule) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "connection_id": str(r.connection_id),
        "event_type": r.event_type,
        "team_id": r.team_id,
        "channel_id": r.channel_id,
        "channel_name": r.channel_name,
        "template": r.template,
        "rate_limit_per_min": r.rate_limit_per_min,
        "dedupe_window_seconds": r.dedupe_window_seconds,
        "enabled": r.enabled,
        "filter_config": r.filter_config,
    }


@router.get("/channels")
async def list_collab_channels_route(
    connection_id: uuid.UUID,
    team_id: str = Query(default="", max_length=120),
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_READ)),
    db: AsyncSession = Depends(get_session),
    http_client: Any = Depends(get_int2_http_client),
) -> dict[str, Any]:
    try:
        conn_row = await get_tenant_connection(
            db, tenant_id=ctx.tenant.id, connection_id=connection_id
        )
    except IntegrationV2Error as exc:
        raise HTTPException(status_code=404, detail=exc.safe_message) from exc

    provider = instantiate_provider_v2(conn_row, http_client=http_client)
    try:
        if isinstance(provider, SlackProvider):
            return {"provider": "slack", "channels": await provider.list_channels()}
        if isinstance(provider, TeamsProvider):
            if not team_id:
                return {"provider": "teams", "teams": await provider.list_joined_teams(), "channels": []}
            return {"provider": "teams", "channels": await provider.list_channels(team_id)}
        raise HTTPException(status_code=422, detail="Provider does not list channels")
    except IntegrationV2Error as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.safe_message) from exc


@router.get("/rules")
@router.get("/routes")
async def list_notification_rules_route(
    connection_id: uuid.UUID | None = Query(default=None),
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_READ)),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    stmt = select(NotificationRule).where(NotificationRule.tenant_id == ctx.tenant.id)
    if connection_id is not None:
        stmt = stmt.where(NotificationRule.connection_id == connection_id)
    rows = list((await db.execute(stmt)).scalars().all())
    serialized = [_serialize_rule(r) for r in rows]
    return {
        "supported_events": sorted(SUPPORTED_NOTIFICATION_EVENTS),
        "rules": serialized,
        "routes": serialized,
    }


@router.post("/rules", status_code=201)
@router.post("/routes", status_code=201)
async def create_notification_rule_route(
    req: NotificationRuleUpsertRequest,
    ctx: TenantContext = Depends(require_permission(PERM_INT_ADMIN)),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    if req.event_type not in SUPPORTED_NOTIFICATION_EVENTS:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported event_type {req.event_type!r}. Supported: {sorted(SUPPORTED_NOTIFICATION_EVENTS)}",
        )
    try:
        conn = await get_tenant_connection(
            db, tenant_id=ctx.tenant.id, connection_id=req.connection_id
        )
    except IntegrationV2Error as exc:
        raise HTTPException(status_code=404, detail=exc.safe_message) from exc

    existing = (
        await db.execute(
            select(NotificationRule).where(
                NotificationRule.tenant_id == ctx.tenant.id,
                NotificationRule.connection_id == req.connection_id,
                NotificationRule.event_type == req.event_type,
                NotificationRule.channel_id == req.channel_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.team_id = req.team_id
        existing.channel_name = req.channel_name
        existing.template = req.template
        existing.rate_limit_per_min = req.rate_limit_per_min
        existing.dedupe_window_seconds = req.dedupe_window_seconds
        existing.enabled = req.enabled
        existing.filter_config = req.filter_config
        row = existing
    else:
        row = NotificationRule(
            tenant_id=ctx.tenant.id,
            environment_id=conn.environment_id,
            connection_id=req.connection_id,
            event_type=req.event_type,
            team_id=req.team_id,
            channel_id=req.channel_id,
            channel_name=req.channel_name,
            template=req.template,
            rate_limit_per_min=req.rate_limit_per_min,
            dedupe_window_seconds=req.dedupe_window_seconds,
            enabled=req.enabled,
            filter_config=req.filter_config,
        )
        db.add(row)
    await db.flush()
    await record_enterprise_audit(
        db,
        tenant_id=ctx.tenant.id,
        user_id=ctx.user.id,
        action="integrations_v2.collab.rule_saved",
        resource_type="int2_collab_route",
        resource_id=str(row.id),
        detail={"event_type": req.event_type, "channel_id": req.channel_id},
    )
    await db.commit()
    out = _serialize_rule(row)
    return {"rule": out, "route": out}


@router.put("/rules/{rule_id}")
async def update_notification_rule_route(
    rule_id: uuid.UUID,
    req: NotificationRuleUpsertRequest,
    ctx: TenantContext = Depends(require_permission(PERM_INT_ADMIN)),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    row = (
        await db.execute(
            select(NotificationRule).where(
                NotificationRule.id == rule_id,
                NotificationRule.tenant_id == ctx.tenant.id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Notification rule not found")
    if req.event_type not in SUPPORTED_NOTIFICATION_EVENTS:
        raise HTTPException(status_code=422, detail=f"Unsupported event_type {req.event_type!r}")
    row.team_id = req.team_id
    row.channel_id = req.channel_id
    row.channel_name = req.channel_name
    row.template = req.template
    row.rate_limit_per_min = req.rate_limit_per_min
    row.dedupe_window_seconds = req.dedupe_window_seconds
    row.enabled = req.enabled
    row.filter_config = req.filter_config
    await record_enterprise_audit(
        db,
        tenant_id=ctx.tenant.id,
        user_id=ctx.user.id,
        action="integrations_v2.collab.rule_updated",
        resource_type="int2_collab_route",
        resource_id=str(row.id),
        detail={"event_type": row.event_type, "enabled": row.enabled},
    )
    await db.commit()
    return {"rule": _serialize_rule(row)}


@router.delete("/rules/{rule_id}")
@router.delete("/routes/{rule_id}")
async def delete_notification_rule_route(
    rule_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(PERM_INT_ADMIN)),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    row = (
        await db.execute(
            select(NotificationRule).where(
                NotificationRule.id == rule_id,
                NotificationRule.tenant_id == ctx.tenant.id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Notification rule not found")
    await record_enterprise_audit(
        db,
        tenant_id=ctx.tenant.id,
        user_id=ctx.user.id,
        action="integrations_v2.collab.rule_deleted",
        resource_type="int2_collab_route",
        resource_id=str(rule_id),
        detail={"event_type": row.event_type},
    )
    await db.delete(row)
    await db.commit()
    return {"deleted": True}


@router.post("/meetings", status_code=201)
async def create_collab_meeting_route(
    req: CreateCollabMeetingRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ctx: TenantContext = Depends(require_permission(PERM_INT_ADMIN)),
    db: AsyncSession = Depends(get_session),
    http_client: Any = Depends(get_int2_http_client),
) -> dict[str, Any]:
    try:
        conn_row = await get_tenant_connection(
            db, tenant_id=ctx.tenant.id, connection_id=req.connection_id
        )
    except IntegrationV2Error as exc:
        raise HTTPException(status_code=404, detail=exc.safe_message) from exc

    start_dt = (
        datetime.fromisoformat(req.start_time_iso.replace("Z", "+00:00"))
        if req.start_time_iso
        else None
    )
    try:
        rec, created = await schedule_meeting(
            db,
            connection=conn_row,
            topic=req.topic,
            when=start_dt,
            duration_minutes=req.duration_minutes,
            lead_id=req.lead_id,
            call_id=req.call_id,
            agenda=req.agenda,
            idempotency_key=idempotency_key or "",
            http_client=http_client,
        )
        await record_enterprise_audit(
            db,
            tenant_id=ctx.tenant.id,
            user_id=ctx.user.id,
            action="integrations_v2.collab.meeting_scheduled",
            resource_type="int2_meeting",
            resource_id=str(rec.id),
            detail={"provider": conn_row.provider, "replayed": not created},
        )
        await db.commit()
        return {
            "provider": conn_row.provider,
            "replayed": not created,
            "meeting": {
                "id": str(rec.id),
                "meeting_id": rec.external_meeting_id,
                "join_url": rec.join_url,
                "topic": rec.topic,
                "start_time": rec.start_time.isoformat(),
                "duration_minutes": rec.duration_minutes,
            },
        }
    except IntegrationV2Error as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.http_status, detail=exc.safe_message) from exc


@router.post("/notify/test")
@router.post("/alerts/send")
async def send_collab_test_notification_route(
    req: NotifyTestRequest,
    ctx: TenantContext = Depends(require_permission(PERM_INT_ADMIN)),
    db: AsyncSession = Depends(get_session),
    http_client: Any = Depends(get_int2_http_client),
) -> dict[str, Any]:
    """Perform a REAL send against Slack/Teams/Zoom and return the upstream message ID or error."""
    if req.rule_id is not None:
        rule = (
            await db.execute(
                select(NotificationRule).where(
                    NotificationRule.id == req.rule_id,
                    NotificationRule.tenant_id == ctx.tenant.id,
                )
            )
        ).scalar_one_or_none()
        if rule is None:
            raise HTTPException(status_code=404, detail="Notification rule not found")
        conn_row = await get_tenant_connection(
            db, tenant_id=ctx.tenant.id, connection_id=rule.connection_id
        )
        try:
            outcome = await dispatch_notification_rule(
                db,
                rule=rule,
                connection=conn_row,
                context={
                    "title": req.title,
                    "summary": req.summary,
                    "caller_phone": req.caller_phone,
                    "event_type": rule.event_type,
                },
                http_client=http_client,
            )
            await record_enterprise_audit(
                db,
                tenant_id=ctx.tenant.id,
                user_id=ctx.user.id,
                action="integrations_v2.collab.notify_test",
                resource_type="int2_collab_route",
                resource_id=str(rule.id),
                detail={"provider": outcome.provider, "status": outcome.status},
            )
            await db.commit()
            return {
                "provider": outcome.provider,
                "status": outcome.status,
                "message_id": outcome.message_id,
                "rendered_text": outcome.rendered_text,
            }
        except IntegrationV2Error as exc:
            await db.rollback()
            raise HTTPException(status_code=exc.http_status, detail=exc.safe_message) from exc

    if req.connection_id is None or not req.channel_id:
        raise HTTPException(
            status_code=422, detail="Provide 'rule_id' or ('connection_id' and 'channel_id')"
        )
    try:
        conn_row = await get_tenant_connection(
            db, tenant_id=ctx.tenant.id, connection_id=req.connection_id
        )
    except IntegrationV2Error as exc:
        raise HTTPException(status_code=404, detail=exc.safe_message) from exc

    provider = instantiate_provider_v2(conn_row, http_client=http_client)
    try:
        if isinstance(provider, SlackProvider):
            blocks = build_slack_call_blocks(
                header=req.title, summary=req.summary, caller_phone=req.caller_phone
            )
            sent = await provider.post_message(
                channel=req.channel_id, text=req.summary, blocks=blocks
            )
            msg_id = str(sent.get("ts") or "")
        elif isinstance(provider, TeamsProvider):
            if not req.team_id:
                raise HTTPException(status_code=422, detail="Teams requires 'team_id'")
            card = build_teams_call_adaptive_card(
                title=req.title, summary=req.summary, caller_phone=req.caller_phone
            )
            msg_id = await provider.post_channel_adaptive_card(
                team_id=req.team_id,
                channel_id=req.channel_id,
                card=card,
                summary_text=req.title,
            )
        elif isinstance(provider, ZoomProvider):
            msg_id = await provider.send_chat_message(
                to_channel=req.channel_id, message=f"{req.title}: {req.summary}"
            )
        else:
            raise HTTPException(status_code=422, detail="Provider does not support channel alerts")

        await record_enterprise_audit(
            db,
            tenant_id=ctx.tenant.id,
            user_id=ctx.user.id,
            action="integrations_v2.collab.notify_test",
            resource_type="int2_connection",
            resource_id=str(conn_row.id),
            detail={"provider": conn_row.provider, "channel_id": req.channel_id},
        )
        await db.commit()
        return {"provider": conn_row.provider, "status": "sent", "message_id": msg_id}
    except IntegrationV2Error as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.http_status, detail=exc.safe_message) from exc


@_slack_public_router.post("/{connection_id}/slash")
async def collab_slack_slash_route(
    connection_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_session),
    http_client: Any = Depends(get_int2_http_client),
) -> dict[str, Any]:
    conn_row = (
        await db.execute(
            select(IntegrationConnection).where(
                IntegrationConnection.id == connection_id,
                IntegrationConnection.provider == "slack",
            )
        )
    ).scalar_one_or_none()
    if conn_row is None or conn_row.status in ("disabled", "revoked"):
        raise HTTPException(status_code=404, detail="Slack connection not found")
    raw_body = await request.body()
    headers = {k: v for k, v in request.headers.items()}
    try:
        res = await handle_slack_slash_command(
            db,
            connection=conn_row,
            raw_body=raw_body,
            headers=headers,
            http_client=http_client,
        )
        await db.commit()
        return res
    except WebhookSignatureError as exc:
        await db.rollback()
        raise HTTPException(status_code=401, detail=exc.safe_message) from exc
    except IntegrationV2Error as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.http_status, detail=exc.safe_message) from exc


@_slack_public_router.post("/{connection_id}/interactivity")
async def collab_slack_interactivity_route(
    connection_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    conn_row = (
        await db.execute(
            select(IntegrationConnection).where(
                IntegrationConnection.id == connection_id,
                IntegrationConnection.provider == "slack",
            )
        )
    ).scalar_one_or_none()
    if conn_row is None or conn_row.status in ("disabled", "revoked"):
        raise HTTPException(status_code=404, detail="Slack connection not found")
    raw_body = await request.body()
    headers = {k: v for k, v in request.headers.items()}
    try:
        res = await handle_slack_interaction(
            db, connection=conn_row, raw_body=raw_body, headers=headers
        )
        await db.commit()
        return res
    except WebhookSignatureError as exc:
        await db.rollback()
        raise HTTPException(status_code=401, detail=exc.safe_message) from exc
    except IntegrationV2Error as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.http_status, detail=exc.safe_message) from exc


_combined_collab_router = APIRouter()
_combined_collab_router.include_router(router)
_combined_collab_router.include_router(_slack_public_router)
router = _combined_collab_router
