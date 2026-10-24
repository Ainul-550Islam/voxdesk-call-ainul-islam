"""REST API routes for Integrations v2 catalog, OAuth2+PKCE, API-key connections, field mappings, sync, and matrix."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_enterprise_audit
from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.db.integrations_v2_models import (
    FieldMappingProfile,
    FieldMappingRule,
    SyncRun,
)
from app.db.session import get_session
from app.integrations_v2.catalog import build_provider_catalog
from app.integrations_v2.core.connection_store import (
    get_tenant_connection,
    list_tenant_connections,
    mask_connection,
    upsert_connection,
)
from app.integrations_v2.core.errors import IntegrationV2Error
from app.integrations_v2.core.field_mapping import (
    SUPPORTED_DIRECTIONS,
    SUPPORTED_ENTITIES,
    SUPPORTED_TRANSFORMS,
    MappingRuleSpec,
    execute_field_mapping,
)
from app.integrations_v2.core.oauth2 import (
    exchange_oauth_callback,
    refresh_connection_token,
    revoke_connection_tokens,
    start_oauth_flow,
)
from app.integrations_v2.core.sync_engine import run_incremental_sync
from app.integrations_v2.crm import CrmProviderV2
from app.integrations_v2.helpdesk.models import HelpdeskProviderV2
from app.integrations_v2.matrix import build_capability_matrix
from app.integrations_v2.register_all import (
    get_provider_class_v2,
    instantiate_provider_v2,
)
from app.resilience.idempotency import (
    IdempotencyError,
    claim_request,
    complete_request,
)

PERM_INT_ADMIN = getattr(Permission, "INTEGRATION_ADMIN", Permission.INTEGRATION_WRITE)

router = APIRouter(prefix="/api/v1/integrations", tags=["integrations-v2"])


def get_int2_http_client() -> Any:
    """Overridable dependency for injecting mock HTTP transports in protocol tests."""
    return None


def _raise_http(exc: IntegrationV2Error) -> None:
    raise HTTPException(
        status_code=exc.http_status,
        detail={"code": exc.code, "message": exc.safe_message, "provider": exc.provider},
    ) from exc


class ConnectProviderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scopes: list[str] = Field(default_factory=list)
    redirect_uri: str = Field(default="", max_length=512)
    instance_url: str = Field(default="", max_length=512)
    region: str = Field(default="us", max_length=32)
    extra_params: dict[str, Any] = Field(default_factory=dict)
    credentials: dict[str, str] | None = None
    webhook_secret: str = Field(default="", max_length=512)
    display_name: str = Field(default="", max_length=160)
    external_account_id: str = Field(default="default", max_length=160)
    config: dict[str, Any] = Field(default_factory=dict)
    verify_now: bool = False


class OAuthStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(min_length=2, max_length=48)
    scopes: list[str] = Field(default_factory=list)
    redirect_uri: str = Field(default="", max_length=512)
    instance_url: str = Field(default="", max_length=512)
    region: str = Field(default="us", max_length=32)
    extra_params: dict[str, Any] = Field(default_factory=dict)


class OAuthCallbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(min_length=2, max_length=48)
    code: str = Field(min_length=1, max_length=2048)
    state: str = Field(min_length=8, max_length=512)
    display_name: str = Field(default="", max_length=160)
    external_account_id: str = Field(default="default", max_length=160)
    config: dict[str, Any] = Field(default_factory=dict)


class ApiKeyConnectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(min_length=2, max_length=48)
    display_name: str = Field(default="", max_length=160)
    external_account_id: str = Field(default="default", max_length=160)
    instance_url: str = Field(default="", max_length=512)
    region: str = Field(default="us", max_length=32)
    credentials: dict[str, str] = Field(min_length=1)
    webhook_secret: str = Field(default="", max_length=512)
    config: dict[str, Any] = Field(default_factory=dict)
    verify_now: bool = False


class FieldMappingItemInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity_type: str = Field(default="Contact", min_length=2, max_length=48)
    direction: Literal["inbound", "outbound", "bidirectional"] = "bidirectional"
    voxdesk_field: str = Field(min_length=1, max_length=120)
    external_field: str = Field(min_length=1, max_length=160)
    transform: str = Field(default="identity", max_length=32)
    transform_config: dict[str, Any] = Field(default_factory=dict)
    required: bool = False
    default_value: str | None = Field(default=None, max_length=256)


class FieldMappingBulkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity_type: str = Field(default="Contact", min_length=2, max_length=48)
    profile_name: str = Field(default="default", min_length=1, max_length=120)
    rules: list[FieldMappingItemInput] = Field(max_length=100)


class FieldMappingPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    direction: Literal["inbound", "outbound"] = "inbound"
    rules: list[FieldMappingItemInput] = Field(max_length=100)
    sample_payload: dict[str, Any]


class TriggerSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity_type: Literal["Contact", "Lead", "Ticket"] = "Contact"
    conflict_policy: Literal["external_wins", "voxdesk_wins", "newest_wins"] = "newest_wins"


# --- Catalog & Matrix ---


@router.get("/catalog")
@router.get("/v2/providers")
async def get_integrations_catalog(
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_READ)),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    providers = await build_provider_catalog(db, tenant_id=ctx.tenant.id)
    return {
        "providers": providers,
        "supported_transforms": sorted(SUPPORTED_TRANSFORMS),
        "supported_entities": sorted(SUPPORTED_ENTITIES),
        "supported_directions": sorted(SUPPORTED_DIRECTIONS),
    }


@router.get("/matrix")
@router.get("/v2/matrix")
async def get_v2_verification_matrix(
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_READ)),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    matrix = await build_capability_matrix(db, tenant_id=ctx.tenant.id)
    counts: dict[str, int] = {"LIVE_VERIFIED": 0, "PROTOCOL_TESTED": 0, "NOT_CONFIGURED": 0}
    for item in matrix:
        st = item["verification_status"]
        counts[st] = counts.get(st, 0) + 1
    return {"summary": counts, "providers": matrix}


# --- Connect & OAuth2 ---


async def _connect_api_key_impl(
    *,
    req: ApiKeyConnectRequest,
    ctx: TenantContext,
    db: AsyncSession,
    http_client: Any,
    idempotency_key: str | None,
) -> dict[str, Any]:
    claim = None
    if idempotency_key:
        try:
            claim = await claim_request(
                db,
                tenant_id=ctx.tenant.id,
                operation="int2.connect.api_key",
                key=idempotency_key,
                request_data={
                    "provider": req.provider,
                    "external_account_id": req.external_account_id,
                },
            )
            if claim.replayed and claim.receipt.resource_id:
                existing = await get_tenant_connection(
                    db,
                    tenant_id=ctx.tenant.id,
                    connection_id=uuid.UUID(claim.receipt.resource_id),
                )
                return {"connection": mask_connection(existing), "replayed": True}
        except IdempotencyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    try:
        cls = get_provider_class_v2(req.provider)
        conn = await upsert_connection(
            db,
            tenant_id=ctx.tenant.id,
            provider=cls.slug,
            category=cls.category,
            auth_kind="api_key",
            credentials=dict(req.credentials),
            display_name=req.display_name or cls.display_name,
            external_account_id=req.external_account_id,
            instance_url=req.instance_url,
            region=req.region,
            config=req.config,
            webhook_secret=req.webhook_secret,
            created_by_user_id=ctx.user.id,
        )
        if req.verify_now:
            provider = instantiate_provider_v2(conn, http_client=http_client)
            hs = await provider.health()
            conn.last_health_at = hs.checked_at
            conn.last_health_ok = hs.ok
            if hs.account_id and conn.external_account_id == "default":
                conn.external_account_id = hs.account_id[:160]

        if claim is not None:
            await complete_request(
                db, claim.receipt, resource_type="int2_connection", resource_id=str(conn.id)
            )
        await record_enterprise_audit(
            db,
            tenant_id=ctx.tenant.id,
            user_id=ctx.user.id,
            action="integrations_v2.api_key.connected",
            resource_type="int2_connection",
            resource_id=str(conn.id),
            detail={"provider": cls.slug, "verified": bool(conn.last_health_ok)},
        )
        await db.commit()
        return {"connection": mask_connection(conn), "replayed": False}
    except IntegrationV2Error as exc:
        await db.rollback()
        _raise_http(exc)


@router.post("/v2/connections/api-key", status_code=201)
async def connect_with_api_key_route(
    req: ApiKeyConnectRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ctx: TenantContext = Depends(require_permission(PERM_INT_ADMIN)),
    db: AsyncSession = Depends(get_session),
    http_client: Any = Depends(get_int2_http_client),
) -> dict[str, Any]:
    return await _connect_api_key_impl(
        req=req, ctx=ctx, db=db, http_client=http_client, idempotency_key=idempotency_key
    )


@router.post("/v2/oauth/start")
async def start_v2_oauth_route(
    req: OAuthStartRequest,
    ctx: TenantContext = Depends(require_permission(PERM_INT_ADMIN)),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    try:
        cls = get_provider_class_v2(req.provider)
        scopes = req.scopes or list(cls.default_scopes)
        auth_url, state_row = await start_oauth_flow(
            db,
            tenant_id=ctx.tenant.id,
            user_id=ctx.user.id,
            provider=cls.slug,
            scopes=scopes,
            redirect_uri_override=req.redirect_uri,
            instance_url=req.instance_url,
            region=req.region,
            extra_params=req.extra_params,
        )
        await record_enterprise_audit(
            db,
            tenant_id=ctx.tenant.id,
            user_id=ctx.user.id,
            action="integrations_v2.oauth.started",
            resource_type="int2_oauth_state",
            resource_id=str(state_row.id),
            detail={"provider": cls.slug},
        )
        await db.commit()
        return {
            "provider": cls.slug,
            "authorize_url": auth_url,
            "expires_at": state_row.expires_at.isoformat(),
        }
    except IntegrationV2Error as exc:
        await db.rollback()
        _raise_http(exc)


@router.post("/{provider}/connect")
async def connect_provider_route(
    provider: str,
    req: ConnectProviderRequest | None = None,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ctx: TenantContext = Depends(require_permission(PERM_INT_ADMIN)),
    db: AsyncSession = Depends(get_session),
    http_client: Any = Depends(get_int2_http_client),
) -> dict[str, Any]:
    body = req or ConnectProviderRequest()
    cls = get_provider_class_v2(provider)
    if body.credentials:
        return await _connect_api_key_impl(
            req=ApiKeyConnectRequest(
                provider=cls.slug,
                display_name=body.display_name,
                external_account_id=body.external_account_id,
                instance_url=body.instance_url,
                region=body.region,
                credentials=body.credentials,
                webhook_secret=body.webhook_secret,
                config=body.config,
                verify_now=body.verify_now,
            ),
            ctx=ctx,
            db=db,
            http_client=http_client,
            idempotency_key=idempotency_key,
        )
    return await start_v2_oauth_route(
        OAuthStartRequest(
            provider=cls.slug,
            scopes=body.scopes,
            redirect_uri=body.redirect_uri,
            instance_url=body.instance_url,
            region=body.region,
            extra_params=body.extra_params,
        ),
        ctx=ctx,
        db=db,
    )


async def _complete_oauth_callback(
    *,
    provider: str,
    code: str,
    state: str,
    display_name: str,
    external_account_id: str,
    config: dict[str, Any],
    ctx: TenantContext,
    db: AsyncSession,
    http_client: Any,
) -> dict[str, Any]:
    try:
        cls = get_provider_class_v2(provider)
        token_payload, state_row = await exchange_oauth_callback(
            db,
            tenant_id=ctx.tenant.id,
            provider=cls.slug,
            state_token=state,
            code=code,
            http_client=http_client,
        )
        extra = dict(state_row.extra_params or {})
        merged_cfg = {**extra, **config}

        ext_acct = external_account_id
        if cls.slug == "slack" and isinstance(token_payload.get("team"), dict):
            ext_acct = str(token_payload["team"].get("id") or ext_acct)

        creds: dict[str, Any] = {
            "access_token": str(token_payload.get("access_token") or ""),
        }
        if token_payload.get("api_domain"):
            creds["api_domain"] = str(token_payload["api_domain"])

        conn = await upsert_connection(
            db,
            tenant_id=ctx.tenant.id,
            provider=cls.slug,
            category=cls.category,
            auth_kind="oauth2",
            credentials=creds,
            refresh_token=str(token_payload.get("refresh_token") or ""),
            expires_in=int(token_payload["expires_in"])
            if token_payload.get("expires_in")
            else None,
            display_name=display_name or cls.display_name,
            external_account_id=ext_acct,
            instance_url=str(extra.get("instance_url") or ""),
            region=str(extra.get("region") or "us"),
            scopes=list(extra.get("scopes") or cls.default_scopes),
            config=merged_cfg,
            created_by_user_id=ctx.user.id,
        )
        await record_enterprise_audit(
            db,
            tenant_id=ctx.tenant.id,
            user_id=ctx.user.id,
            action="integrations_v2.oauth.connected",
            resource_type="int2_connection",
            resource_id=str(conn.id),
            detail={"provider": cls.slug, "external_account_id": ext_acct},
        )
        await db.commit()
        return {"connection": mask_connection(conn)}
    except IntegrationV2Error as exc:
        await db.rollback()
        _raise_http(exc)


@router.get("/{provider}/callback")
async def oauth_get_callback_route(
    provider: str,
    code: str = Query(min_length=1, max_length=2048),
    state: str = Query(min_length=8, max_length=512),
    ctx: TenantContext = Depends(require_permission(PERM_INT_ADMIN)),
    db: AsyncSession = Depends(get_session),
    http_client: Any = Depends(get_int2_http_client),
) -> dict[str, Any]:
    return await _complete_oauth_callback(
        provider=provider,
        code=code,
        state=state,
        display_name="",
        external_account_id="default",
        config={},
        ctx=ctx,
        db=db,
        http_client=http_client,
    )


@router.post("/v2/oauth/callback")
async def complete_v2_oauth_callback_route(
    req: OAuthCallbackRequest,
    ctx: TenantContext = Depends(require_permission(PERM_INT_ADMIN)),
    db: AsyncSession = Depends(get_session),
    http_client: Any = Depends(get_int2_http_client),
) -> dict[str, Any]:
    return await _complete_oauth_callback(
        provider=req.provider,
        code=req.code,
        state=req.state,
        display_name=req.display_name,
        external_account_id=req.external_account_id,
        config=req.config,
        ctx=ctx,
        db=db,
        http_client=http_client,
    )


# --- Connections CRUD, Test, Refresh ---


@router.get("/connections")
@router.get("/v2/connections")
async def list_v2_connections_route(
    category: str | None = Query(default=None),
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_READ)),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    rows = await list_tenant_connections(db, tenant_id=ctx.tenant.id, category=category)
    return {"connections": [mask_connection(r) for r in rows]}


@router.get("/connections/{connection_id}")
@router.get("/v2/connections/{connection_id}")
async def get_v2_connection_route(
    connection_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_READ)),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    try:
        conn = await get_tenant_connection(
            db, tenant_id=ctx.tenant.id, connection_id=connection_id
        )
        return {"connection": mask_connection(conn)}
    except IntegrationV2Error as exc:
        raise HTTPException(status_code=404, detail=exc.safe_message) from exc


@router.post("/connections/{connection_id}/test")
@router.post("/v2/connections/{connection_id}/health")
async def check_v2_connection_health_route(
    connection_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_READ)),
    db: AsyncSession = Depends(get_session),
    http_client: Any = Depends(get_int2_http_client),
) -> dict[str, Any]:
    try:
        conn = await get_tenant_connection(
            db, tenant_id=ctx.tenant.id, connection_id=connection_id
        )
    except IntegrationV2Error as exc:
        raise HTTPException(status_code=404, detail=exc.safe_message) from exc

    now = datetime.now(timezone.utc)
    try:
        provider = instantiate_provider_v2(conn, http_client=http_client)
        status = await provider.health()
        conn.last_health_at = status.checked_at
        conn.last_health_ok = status.ok
        conn.last_error = ""
        conn.updated_at = now
        await db.commit()
        return {"health": status.to_dict(), "connection": mask_connection(conn)}
    except IntegrationV2Error as exc:
        conn.last_health_at = now
        conn.last_health_ok = False
        conn.last_error = exc.safe_message[:500]
        await db.commit()
        _raise_http(exc)


@router.post("/connections/{connection_id}/refresh")
@router.post("/v2/connections/{connection_id}/refresh")
async def refresh_v2_connection_route(
    connection_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(PERM_INT_ADMIN)),
    db: AsyncSession = Depends(get_session),
    http_client: Any = Depends(get_int2_http_client),
) -> dict[str, Any]:
    try:
        conn = await get_tenant_connection(
            db, tenant_id=ctx.tenant.id, connection_id=connection_id
        )
    except IntegrationV2Error as exc:
        raise HTTPException(status_code=404, detail=exc.safe_message) from exc

    try:
        await refresh_connection_token(db, conn, http_client=http_client, force=True)
        await record_enterprise_audit(
            db,
            tenant_id=ctx.tenant.id,
            user_id=ctx.user.id,
            action="integrations_v2.connection.refreshed",
            resource_type="int2_connection",
            resource_id=str(conn.id),
            detail={"provider": conn.provider},
        )
        await db.commit()
        return {"connection": mask_connection(conn)}
    except IntegrationV2Error as exc:
        await db.commit()
        _raise_http(exc)


@router.delete("/connections/{connection_id}")
@router.delete("/v2/connections/{connection_id}")
async def disconnect_v2_connection_route(
    connection_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(PERM_INT_ADMIN)),
    db: AsyncSession = Depends(get_session),
    http_client: Any = Depends(get_int2_http_client),
) -> dict[str, Any]:
    try:
        conn = await get_tenant_connection(
            db, tenant_id=ctx.tenant.id, connection_id=connection_id
        )
    except IntegrationV2Error as exc:
        raise HTTPException(status_code=404, detail=exc.safe_message) from exc

    revoked = await revoke_connection_tokens(conn, http_client=http_client)
    provider_slug = conn.provider
    await record_enterprise_audit(
        db,
        tenant_id=ctx.tenant.id,
        user_id=ctx.user.id,
        action="integrations_v2.connection.disconnected",
        resource_type="int2_connection",
        resource_id=str(conn.id),
        detail={"provider": provider_slug, "token_revoked": revoked},
    )
    await db.delete(conn)
    await db.commit()
    return {"deleted": True, "provider": provider_slug, "token_revoked": revoked}


# --- Field Mapping & Live Sample Fetch ---


@router.get("/connections/{connection_id}/mapping")
@router.get("/v2/connections/{connection_id}/mappings")
async def list_field_mappings_route(
    connection_id: uuid.UUID,
    entity_type: str | None = Query(default=None),
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_READ)),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    try:
        await get_tenant_connection(
            db, tenant_id=ctx.tenant.id, connection_id=connection_id
        )
    except IntegrationV2Error as exc:
        raise HTTPException(status_code=404, detail=exc.safe_message) from exc

    stmt = select(FieldMappingRule).where(
        FieldMappingRule.tenant_id == ctx.tenant.id,
        FieldMappingRule.connection_id == connection_id,
    )
    if entity_type:
        stmt = stmt.where(FieldMappingRule.entity_type == entity_type)
    rows = list((await db.execute(stmt)).scalars().all())
    return {
        "rules": [
            {
                "id": str(r.id),
                "entity_type": r.entity_type,
                "direction": r.direction,
                "voxdesk_field": r.voxdesk_field,
                "external_field": r.external_field,
                "transform": r.transform,
                "transform_config": r.transform_config,
                "required": r.required,
                "default_value": r.default_value,
            }
            for r in rows
        ]
    }


@router.put("/connections/{connection_id}/mapping")
@router.put("/v2/connections/{connection_id}/mappings")
async def replace_field_mappings_route(
    connection_id: uuid.UUID,
    req: FieldMappingBulkRequest,
    ctx: TenantContext = Depends(require_permission(PERM_INT_ADMIN)),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    if req.entity_type not in SUPPORTED_ENTITIES:
        raise HTTPException(
            status_code=422, detail=f"Unsupported entity_type {req.entity_type!r}"
        )
    for item in req.rules:
        if item.transform not in SUPPORTED_TRANSFORMS:
            raise HTTPException(
                status_code=422, detail=f"Unsupported transform {item.transform!r}"
            )
        if item.direction not in SUPPORTED_DIRECTIONS:
            raise HTTPException(
                status_code=422, detail=f"Unsupported direction {item.direction!r}"
            )

    try:
        conn = await get_tenant_connection(
            db, tenant_id=ctx.tenant.id, connection_id=connection_id
        )
    except IntegrationV2Error as exc:
        raise HTTPException(status_code=404, detail=exc.safe_message) from exc

    await db.execute(
        delete(FieldMappingRule).where(
            FieldMappingRule.tenant_id == ctx.tenant.id,
            FieldMappingRule.connection_id == conn.id,
            FieldMappingRule.entity_type == req.entity_type,
        )
    )
    created: list[FieldMappingRule] = []
    serialized_rules: list[dict[str, Any]] = []
    for item in req.rules:
        row = FieldMappingRule(
            tenant_id=ctx.tenant.id,
            connection_id=conn.id,
            entity_type=req.entity_type,
            direction=item.direction,
            voxdesk_field=item.voxdesk_field,
            external_field=item.external_field,
            transform=item.transform,
            transform_config=item.transform_config,
            required=item.required,
            default_value=item.default_value,
        )
        db.add(row)
        created.append(row)
        serialized_rules.append(item.model_dump())

    prof = (
        await db.execute(
            select(FieldMappingProfile).where(
                FieldMappingProfile.connection_id == conn.id,
                FieldMappingProfile.entity_type == req.entity_type,
                FieldMappingProfile.name == req.profile_name,
            )
        )
    ).scalar_one_or_none()
    if prof is None:
        prof = FieldMappingProfile(
            tenant_id=ctx.tenant.id,
            environment_id=conn.environment_id,
            connection_id=conn.id,
            entity_type=req.entity_type,
            name=req.profile_name,
            rules_json=serialized_rules,
            is_active=True,
        )
        db.add(prof)
    else:
        prof.rules_json = serialized_rules
        prof.updated_at = datetime.now(timezone.utc)

    await record_enterprise_audit(
        db,
        tenant_id=ctx.tenant.id,
        user_id=ctx.user.id,
        action="integrations_v2.mappings.updated",
        resource_type="int2_connection",
        resource_id=str(conn.id),
        detail={"entity_type": req.entity_type, "rule_count": len(created)},
    )
    await db.commit()
    return {"entity_type": req.entity_type, "saved_count": len(created)}


@router.get("/connections/{connection_id}/sample-payload")
async def fetch_live_sample_payload_route(
    connection_id: uuid.UUID,
    entity_type: Literal["Contact", "Ticket"] = Query(default="Contact"),
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_READ)),
    db: AsyncSession = Depends(get_session),
    http_client: Any = Depends(get_int2_http_client),
) -> dict[str, Any]:
    """Fetch one real record from the connected vendor API for mapping validation (never fake rows)."""
    try:
        conn = await get_tenant_connection(
            db, tenant_id=ctx.tenant.id, connection_id=connection_id
        )
    except IntegrationV2Error as exc:
        raise HTTPException(status_code=404, detail=exc.safe_message) from exc

    provider = instantiate_provider_v2(conn, http_client=http_client)
    try:
        if entity_type == "Contact" and isinstance(provider, CrmProviderV2):
            async for batch in provider.iter_updated_contacts(None):
                if batch.items:
                    return {"entity_type": "Contact", "sample": batch.items[0].raw_fields}
                break
            return {"entity_type": "Contact", "sample": None}
        if entity_type == "Ticket" and isinstance(provider, HelpdeskProviderV2):
            async for batch in provider.iter_updated_tickets(None):
                if batch.items:
                    return {"entity_type": "Ticket", "sample": batch.items[0].raw_fields}
                break
            return {"entity_type": "Ticket", "sample": None}
        raise HTTPException(
            status_code=422,
            detail=f"Provider {conn.provider} does not expose {entity_type} sample pull",
        )
    except IntegrationV2Error as exc:
        _raise_http(exc)


@router.post("/connections/mapping/preview")
@router.post("/v2/mappings/preview")
async def preview_field_mappings_route(
    req: FieldMappingPreviewRequest,
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_READ)),
) -> dict[str, Any]:
    specs = [
        MappingRuleSpec(
            voxdesk_field=r.voxdesk_field,
            external_field=r.external_field,
            direction=r.direction,
            transform=r.transform,
            transform_config=r.transform_config,
            required=r.required,
            default_value=r.default_value,
        )
        for r in req.rules
    ]
    res = execute_field_mapping(
        rules=specs, payload=req.sample_payload, direction=req.direction
    )
    return {
        "ok": res.ok,
        "output": res.output,
        "errors": [{"field": e.field, "message": e.message} for e in res.errors],
    }


# --- Incremental Sync ---


@router.post("/connections/{connection_id}/sync")
@router.post("/v2/connections/{connection_id}/sync")
async def trigger_manual_sync_route(
    connection_id: uuid.UUID,
    req: TriggerSyncRequest | None = None,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ctx: TenantContext = Depends(require_permission(PERM_INT_ADMIN)),
    db: AsyncSession = Depends(get_session),
    http_client: Any = Depends(get_int2_http_client),
) -> dict[str, Any]:
    body = req or TriggerSyncRequest()
    try:
        conn = await get_tenant_connection(
            db, tenant_id=ctx.tenant.id, connection_id=connection_id
        )
    except IntegrationV2Error as exc:
        raise HTTPException(status_code=404, detail=exc.safe_message) from exc

    claim = None
    if idempotency_key:
        try:
            claim = await claim_request(
                db,
                tenant_id=ctx.tenant.id,
                operation="int2.sync.trigger",
                key=idempotency_key,
                request_data={
                    "connection_id": str(connection_id),
                    "entity_type": body.entity_type,
                },
            )
            if claim.replayed and claim.receipt.resource_id:
                existing_run = await db.get(SyncRun, uuid.UUID(claim.receipt.resource_id))
                if existing_run and existing_run.tenant_id == ctx.tenant.id:
                    return {
                        "sync_run": _serialize_sync_run(existing_run),
                        "replayed": True,
                    }
        except IdempotencyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    provider = instantiate_provider_v2(conn, http_client=http_client)
    try:
        if body.entity_type in ("Contact", "Lead") and isinstance(provider, CrmProviderV2):
            stream_fn = provider.iter_updated_contacts
        elif body.entity_type == "Ticket" and isinstance(provider, HelpdeskProviderV2):
            stream_fn = provider.iter_updated_tickets
        else:
            raise HTTPException(
                status_code=422,
                detail=f"Provider {conn.provider} does not support incremental sync for {body.entity_type}",
            )

        sync_run = await run_incremental_sync(
            db,
            connection=conn,
            entity_type=body.entity_type,
            page_stream_factory=stream_fn,
            conflict_policy=body.conflict_policy,
        )
        if claim is not None:
            await complete_request(
                db, claim.receipt, resource_type="int2_sync_run", resource_id=str(sync_run.id)
            )
        await record_enterprise_audit(
            db,
            tenant_id=ctx.tenant.id,
            user_id=ctx.user.id,
            action="integrations_v2.sync.triggered",
            resource_type="int2_sync_run",
            resource_id=str(sync_run.id),
            detail={"provider": conn.provider, "entity_type": body.entity_type},
        )
        await db.commit()
        return {"sync_run": _serialize_sync_run(sync_run), "replayed": False}
    except IntegrationV2Error as exc:
        await db.commit()
        _raise_http(exc)


def _serialize_sync_run(r: SyncRun) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "connection_id": str(r.connection_id),
        "entity_type": r.entity_type,
        "status": r.status,
        "cursor_before": r.cursor_before,
        "cursor_after": r.cursor_after,
        "records_seen": r.records_seen,
        "records_upserted": r.records_upserted,
        "records_failed": r.records_failed,
        "dead_letters": r.dead_letters,
        "error_summary": r.error_summary,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
    }


@router.get("/connections/{connection_id}/sync-runs")
@router.get("/v2/connections/{connection_id}/sync-runs")
async def list_sync_runs_route(
    connection_id: uuid.UUID,
    limit: int = Query(default=20, ge=1, le=100),
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_READ)),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    try:
        await get_tenant_connection(
            db, tenant_id=ctx.tenant.id, connection_id=connection_id
        )
    except IntegrationV2Error as exc:
        raise HTTPException(status_code=404, detail=exc.safe_message) from exc

    stmt = (
        select(SyncRun)
        .where(
            SyncRun.tenant_id == ctx.tenant.id,
            SyncRun.connection_id == connection_id,
        )
        .order_by(SyncRun.started_at.desc())
        .limit(limit)
    )
    rows = list((await db.execute(stmt)).scalars().all())
    return {"sync_runs": [_serialize_sync_run(r) for r in rows]}


@router.get("/sync-runs")
async def list_all_tenant_sync_runs_route(
    limit: int = Query(default=50, ge=1, le=200),
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_READ)),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    stmt = (
        select(SyncRun)
        .where(SyncRun.tenant_id == ctx.tenant.id)
        .order_by(SyncRun.started_at.desc())
        .limit(limit)
    )
    rows = list((await db.execute(stmt)).scalars().all())
    return {"sync_runs": [_serialize_sync_run(r) for r in rows]}
