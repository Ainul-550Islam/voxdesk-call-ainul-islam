"""Public-boundary HMAC-verified inbound webhook, challenge-handshake, Slack slash-command, and interactivity endpoints."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.integrations_v2_routes import get_int2_http_client
from app.db.integrations_v2_models import IntegrationConnection
from app.db.session import get_session
from app.integrations_v2.collab.slack import (
    handle_slack_interaction,
    handle_slack_slash_command,
)
from app.integrations_v2.core.errors import IntegrationV2Error, WebhookSignatureError
from app.integrations_v2.core.webhook_inbound import receive_inbound_webhook

router = APIRouter(prefix="/webhooks/int2", tags=["integrations-v2-webhooks"])


async def _load_webhook_connection(
    db: AsyncSession, *, provider: str, connection_id: uuid.UUID
) -> IntegrationConnection:
    stmt = select(IntegrationConnection).where(
        IntegrationConnection.id == connection_id,
        IntegrationConnection.provider == provider.lower().strip(),
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None or row.status in ("disabled", "revoked"):
        raise HTTPException(status_code=404, detail="Webhook endpoint not found")
    return row


@router.get("/{provider}/{connection_id}")
async def verify_vendor_webhook_handshake_get(
    provider: str,
    connection_id: uuid.UUID,
    validation_token: str = Query(default="", alias="validationToken", max_length=2048),
    challenge: str = Query(default="", max_length=1024),
    db: AsyncSession = Depends(get_session),
):
    await _load_webhook_connection(
        db, provider=provider, connection_id=connection_id
    )
    if validation_token:
        return PlainTextResponse(content=validation_token, status_code=200)
    if challenge:
        return {"challenge": challenge}
    return {"ok": True, "provider": provider.lower()}


@router.post("/{provider}/{connection_id}")
async def receive_vendor_webhook_route(
    provider: str,
    connection_id: uuid.UUID,
    request: Request,
    validation_token: str = Query(default="", alias="validationToken", max_length=2048),
    db: AsyncSession = Depends(get_session),
):
    conn_row = await _load_webhook_connection(
        db, provider=provider, connection_id=connection_id
    )
    raw_body = await request.body()
    headers = {k: v for k, v in request.headers.items()}
    try:
        result = await receive_inbound_webhook(
            db,
            connection=conn_row,
            raw_body=raw_body,
            headers=headers,
            validation_token=validation_token,
        )
        await db.commit()
    except WebhookSignatureError as exc:
        await db.rollback()
        raise HTTPException(status_code=401, detail=exc.safe_message) from exc
    except IntegrationV2Error as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.http_status, detail=exc.safe_message) from exc

    if result.status == "challenge":
        if result.challenge_plain_text is not None:
            return PlainTextResponse(content=result.challenge_plain_text, status_code=200)
        if result.challenge_response is not None:
            return result.challenge_response
    return {"ok": True, "status": result.status, "event_id": result.event_id}


@router.post("/slack/{connection_id}/commands")
async def slack_slash_command_route(
    connection_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_session),
    http_client: Any = Depends(get_int2_http_client),
) -> dict[str, Any]:
    conn_row = await _load_webhook_connection(
        db, provider="slack", connection_id=connection_id
    )
    raw_body = await request.body()
    headers = {k: v for k, v in request.headers.items()}
    try:
        reply = await handle_slack_slash_command(
            db,
            connection=conn_row,
            raw_body=raw_body,
            headers=headers,
            http_client=http_client,
        )
        await db.commit()
        return reply
    except WebhookSignatureError as exc:
        await db.rollback()
        raise HTTPException(status_code=401, detail=exc.safe_message) from exc
    except IntegrationV2Error as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.http_status, detail=exc.safe_message) from exc


@router.post("/slack/{connection_id}/interactions")
async def slack_interactions_route(
    connection_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    conn_row = await _load_webhook_connection(
        db, provider="slack", connection_id=connection_id
    )
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
