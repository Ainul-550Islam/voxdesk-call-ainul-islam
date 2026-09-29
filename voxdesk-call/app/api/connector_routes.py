from __future__ import annotations
import uuid
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.db.models import Connector
from app.db.session import get_session
from app.integrations.connector import dispatch, registry, validate_connector
from app.integrations.oauth import (
    OAuthConfig,
    OAuthError,
    authorization_url,
    disconnect,
    exchange_callback,
    refresh_access_token,
)

router = APIRouter(prefix="/api/connectors", tags=["connectors"])


class ConnectorCreate(BaseModel):
    provider: str
    kind: str = "generic"
    environment_id: uuid.UUID | None = None
    config: dict = Field(default_factory=dict)
    credential_ref: str | None = None


class DispatchRequest(BaseModel):
    operation: str
    payload: dict = Field(default_factory=dict)


def _oauth_config(row: Connector) -> OAuthConfig:
    config = dict(row.config or {}).get("oauth")
    if not isinstance(config, dict):
        raise HTTPException(422, "connector has no OAuth configuration")
    required = ("authorization_url", "token_url", "client_id", "client_secret_ref", "redirect_uri")
    if any(not isinstance(config.get(key), str) or not config[key] for key in required):
        raise HTTPException(422, "OAuth configuration is incomplete")
    if not config["client_secret_ref"].startswith("secret://"):
        raise HTTPException(422, "OAuth client secret must be an opaque secret:// reference")
    scopes = config.get("scopes", [])
    if not isinstance(scopes, list) or not all(isinstance(scope, str) for scope in scopes):
        raise HTTPException(422, "OAuth scopes must be a list of strings")
    return OAuthConfig(
        authorization_url=config["authorization_url"],
        token_url=config["token_url"],
        client_id=config["client_id"],
        client_secret_ref=config["client_secret_ref"],
        redirect_uri=config["redirect_uri"],
        scopes=tuple(scopes),
        revoke_url=config.get("revoke_url"),
    )


def _connector(session: AsyncSession, connector_id: uuid.UUID, tenant_id: uuid.UUID):
    return session.execute(
        select(Connector).where(Connector.id == connector_id, Connector.tenant_id == tenant_id)
    )


def _contains_plaintext_secret(value: object) -> bool:
    forbidden = {
        "token",
        "access_token",
        "refresh_token",
        "secret",
        "client_secret",
        "password",
        "api_key",
        "signing_secret",
    }
    if isinstance(value, dict):
        for key, item in value.items():
            key_name = str(key).lower()
            if key_name in forbidden and not key_name.endswith("_ref"):
                return True
            if _contains_plaintext_secret(item):
                return True
    elif isinstance(value, list):
        return any(_contains_plaintext_secret(item) for item in value)
    return False


@router.get("/providers")
async def providers(ctx: TenantContext = Depends(require_permission(Permission.CONNECTOR_READ))):
    return [
        {
            "provider": name,
            "kind": registry.get(name).kind,
            "capabilities": sorted(registry.get(name).capabilities),
        }
        for name in registry.providers()
    ]


@router.get("")
async def list_connectors(
    ctx: TenantContext = Depends(require_permission(Permission.CONNECTOR_READ)),
    session: AsyncSession = Depends(get_session),
):
    rows = (
        (
            await session.execute(
                select(Connector)
                .where(Connector.tenant_id == ctx.tenant_id)
                .order_by(Connector.provider)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": str(r.id),
            "provider": r.provider,
            "kind": r.kind,
            "status": r.status,
            "capabilities": r.capabilities,
            "health_message": r.health_message,
        }
        for r in rows
    ]


@router.post("", status_code=201)
async def create_connector(
    body: ConnectorCreate,
    ctx: TenantContext = Depends(require_permission(Permission.CONNECTOR_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        registration = registry.get(body.provider)
    except KeyError as exc:
        raise HTTPException(422, str(exc)) from exc
    if body.credential_ref and not body.credential_ref.startswith("secret://"):
        raise HTTPException(422, "credential_ref must be an opaque secret:// reference")
    if _contains_plaintext_secret(body.config):
        raise HTTPException(
            422, "connector config must use secret references, not credential values"
        )
    row = Connector(
        tenant_id=ctx.tenant_id,
        environment_id=body.environment_id,
        provider=body.provider,
        kind=body.kind or registration.kind,
        capabilities=sorted(registration.capabilities),
        config=body.config,
        credential_ref=body.credential_ref,
    )
    session.add(row)
    await session.commit()
    return {"id": str(row.id), "provider": row.provider, "capabilities": row.capabilities}


@router.get("/{connector_id}/oauth/start")
async def oauth_start(
    connector_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.CONNECTOR_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    row = (await _connector(session, connector_id, ctx.tenant_id)).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "connector not found")
    try:
        url = await authorization_url(session, row, _oauth_config(row))
        await session.commit()
        return {"authorization_url": url}
    except OAuthError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/{connector_id}/oauth/callback")
async def oauth_callback(
    connector_id: uuid.UUID,
    state: str = Query(..., min_length=1),
    code: str = Query(..., min_length=1),
    ctx: TenantContext = Depends(require_permission(Permission.CONNECTOR_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    row = (await _connector(session, connector_id, ctx.tenant_id)).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "connector not found")
    try:
        oauth = await exchange_callback(session, row, _oauth_config(row), state, code)
        await session.commit()
        return {
            "connector_id": str(row.id),
            "state": oauth.state,
            "scopes": oauth.scopes,
            "expires_at": oauth.expires_at.isoformat() if oauth.expires_at else None,
        }
    except OAuthError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/{connector_id}/oauth/refresh")
async def oauth_refresh(
    connector_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.CONNECTOR_EXECUTE)),
    session: AsyncSession = Depends(get_session),
):
    row = (await _connector(session, connector_id, ctx.tenant_id)).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "connector not found")
    try:
        oauth = await refresh_access_token(session, row, _oauth_config(row))
        await session.commit()
        return {
            "state": oauth.state,
            "expires_at": oauth.expires_at.isoformat() if oauth.expires_at else None,
        }
    except OAuthError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.delete("/{connector_id}/oauth")
async def oauth_disconnect(
    connector_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.CONNECTOR_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    row = (await _connector(session, connector_id, ctx.tenant_id)).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "connector not found")
    await disconnect(session, row, _oauth_config(row))
    await session.commit()
    return {"connector_id": str(row.id), "state": "disconnected"}


@router.post("/{connector_id}/health")
async def health(
    connector_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.CONNECTOR_EXECUTE)),
    session: AsyncSession = Depends(get_session),
):
    row = (
        await session.execute(
            select(Connector).where(
                Connector.id == connector_id, Connector.tenant_id == ctx.tenant_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "connector not found")
    result = await validate_connector(session, row)
    await session.commit()
    return {
        "ok": result.ok,
        "status": result.status,
        "error_code": result.error_code,
        "duration_ms": round(result.duration_ms, 2),
    }


@router.post("/{connector_id}/dispatch")
async def dispatch_connector(
    connector_id: uuid.UUID,
    body: DispatchRequest,
    ctx: TenantContext = Depends(require_permission(Permission.CONNECTOR_EXECUTE)),
    session: AsyncSession = Depends(get_session),
):
    row = (
        await session.execute(
            select(Connector).where(
                Connector.id == connector_id, Connector.tenant_id == ctx.tenant_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "connector not found")
    result = await dispatch(session, row, body.operation, body.payload)
    await session.commit()
    if not result.ok:
        raise HTTPException(502, {"status": result.status, "error_code": result.error_code})
    return {
        "ok": True,
        "data": result.data,
        "attempts": result.attempts,
        "duration_ms": round(result.duration_ms, 2),
    }
