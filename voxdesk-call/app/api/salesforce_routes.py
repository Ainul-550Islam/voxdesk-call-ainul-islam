# File: app/api/salesforce_routes.py — Salesforce CRM OAuth2 + PKCE, connection lifecycle, SOQL queries, and write-back (Part 1F / Gate G1)
"""
Salesforce CRM API backed by ``SalesforceProvider`` and ``CrmIntegration``.
- OAuth2 Web-Server flow with S256 PKCE stored durably in ``SalesforceOAuthState``
- AES-256-GCM encrypted token storage via ``app.integrations.crm.crypto``
- Unified ``CrmIntegration(provider=SALESFORCE)`` and ``SalesforceConnection`` persistence
- Escaped SOQL entity queries (Contact, Lead, Account, Case, Opportunity, Task)
- Direct sObject write-back, describe, API limits, and health check
- Distributed rate limiting, durable idempotency, and atomic AuditLog writes
"""
from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_enterprise_audit
from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.core.config import settings
from app.core.rate_limit import enforce_tenant_rate_limit
from app.db.enterprise_models import (
    CrmWritebackLog,
    SalesforceConnection,
    SalesforceOAuthState,
)
from app.db.models import CrmIntegration, CrmProviderType
from app.db.session import get_session
from app.integrations.crm import crypto
from app.integrations.crm.base import ProviderContext
from app.integrations.crm.errors import CrmConfigurationError, CrmError, CrmNotFound
from app.integrations.crm.providers.salesforce import (
    DEFAULT_API_VERSION,
    DEFAULT_LOGIN_URL,
    SalesforceProvider,
    build_authorization_url,
    generate_pkce_pair,
    validate_salesforce_instance_url,
)
from app.resilience.idempotency import (
    get_idempotent_resource_id,
    store_idempotent_resource_id,
)
from app.tenancy.isolation import HierarchyError, to_http

router = APIRouter(prefix="/api/integrations/salesforce", tags=["salesforce"])

MAX_URL_LENGTH = 500
MAX_TOKEN_LENGTH = 4000
OAUTH_STATE_TTL_MINUTES = 10
DEFAULT_PAGE_LIMIT = 20
MAX_PAGE_LIMIT = 100
MAX_SEARCH_LENGTH = 100
ALLOWED_ENTITY_TYPES = {
    "contact",
    "lead",
    "account",
    "case",
    "task",
    "note",
    "opportunity",
}
ALLOWED_ACTIONS = {"create", "update", "upsert", "delete"}
SALESFORCE_API_VERSION = DEFAULT_API_VERSION


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())


class SalesforceConnectRequest(_Strict):
    instance_url: str = Field(min_length=10, max_length=MAX_URL_LENGTH, pattern=r"^https://")
    access_token: str = Field(min_length=10, max_length=MAX_TOKEN_LENGTH)
    refresh_token: str = Field(default="", max_length=MAX_TOKEN_LENGTH)
    client_id: Optional[str] = Field(default=None, max_length=256)
    client_secret: Optional[str] = Field(default=None, max_length=512)
    id_token: Optional[str] = Field(default=None, max_length=MAX_TOKEN_LENGTH)
    scope: Optional[str] = Field(default=None, max_length=500)
    token_type: str = Field(default="Bearer", max_length=20)
    expires_in: Optional[int] = Field(default=None, ge=0)
    meta: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("instance_url")
    @classmethod
    def validate_instance_url(cls, v: str) -> str:
        return validate_salesforce_instance_url(v)


class SalesforceOAuthAuthorizeRequest(_Strict):
    redirect_uri: str = Field(min_length=10, max_length=500, pattern=r"^https://")
    scope: str = Field(default="api refresh_token openid", max_length=500)
    state: Optional[str] = Field(default=None, max_length=128)
    login_url: str = Field(default=DEFAULT_LOGIN_URL, max_length=MAX_URL_LENGTH)
    client_id: Optional[str] = Field(default=None, max_length=256)


class SalesforceOAuthCallbackRequest(_Strict):
    code: str = Field(min_length=5, max_length=2000)
    state: str = Field(min_length=8, max_length=128)
    instance_url: Optional[str] = Field(default=None, max_length=MAX_URL_LENGTH)
    client_id: Optional[str] = Field(default=None, max_length=256)
    client_secret: Optional[str] = Field(default=None, max_length=512)


class SalesforceConnectionOut(_Strict):
    id: str
    tenant_id: str
    instance_url: str
    is_active: bool
    last_sync_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    meta: Dict[str, Any]
    health: str = "unknown"
    token_expires_at: Optional[str] = None


class SalesforceConnectionListOut(_Strict):
    connections: List[SalesforceConnectionOut]
    total: int


class SalesforceEntityOut(_Strict):
    id: str
    type: str
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    account_id: Optional[str] = None
    raw: Dict[str, Any]


class SalesforceEntityListOut(_Strict):
    entities: List[SalesforceEntityOut]
    total: int
    limit: int
    offset: int
    search: Optional[str] = None
    connected: bool = True
    instance_url: str


class SalesforceWritebackRequest(_Strict):
    entity_type: str = Field(
        pattern="^(contact|lead|account|case|task|note|opportunity)$"
    )
    entity_id: Optional[str] = Field(default=None, max_length=120)
    action: str = Field(pattern="^(create|update|upsert|delete)$")
    fields: Dict[str, Any] = Field(default_factory=dict)
    call_id: Optional[uuid.UUID] = None
    idempotency_key: Optional[str] = Field(default=None, min_length=8, max_length=128)


class SalesforceWritebackOut(_Strict):
    id: str
    status: str
    entity_type: str
    entity_id: str
    provider: str = "salesforce"
    call_id: Optional[str] = None
    created_at: str


class SalesforceSyncRequest(_Strict):
    entity_types: List[str] = Field(
        default_factory=lambda: ["contact", "lead"], max_length=10
    )
    full_sync: bool = Field(default=False)
    limit_per_type: int = Field(default=100, ge=1, le=1000)


class SalesforceSyncOut(_Strict):
    sync_id: str
    status: str
    entity_types: List[str]
    total_fetched: int
    started_at: str
    completed_at: Optional[str] = None


class SalesforceHealthOut(_Strict):
    connected: bool
    is_active: bool
    instance_url: Optional[str] = None
    last_sync_at: Optional[str] = None
    token_valid: bool = True
    health: str
    checked_at: str
    api_version: str = SALESFORCE_API_VERSION


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _not_implemented(message: str = "Salesforce runtime operation is not configured.") -> HTTPException:
    return HTTPException(
        status_code=501,
        detail={
            "code": "SALESFORCE_RUNTIME_NOT_IMPLEMENTED",
            "message": message,
        },
    )


def _require_key_ring() -> crypto.KeyRing:
    key_ring = crypto.key_ring_from_settings()
    if key_ring is None:
        raise _not_implemented(
            "CRM credential encryption (CRM_ENCRYPTION_KEYS) is not configured."
        )
    return key_ring


def _to_out(row: SalesforceConnection) -> SalesforceConnectionOut:
    d = row.as_dict()
    health = "healthy" if d["is_active"] else "disabled"
    token_expires = None
    meta = d.get("meta") if isinstance(d.get("meta"), dict) else {}
    if "expires_in" in meta and d.get("updated_at"):
        try:
            updated = datetime.fromisoformat(str(d["updated_at"]).replace("Z", "+00:00"))
            token_expires = (
                updated + timedelta(seconds=int(meta.get("expires_in") or 3600))
            ).isoformat()
        except (ValueError, TypeError):
            token_expires = None

    return SalesforceConnectionOut(
        id=d["id"],
        tenant_id=d["tenant_id"],
        instance_url=d["instance_url"],
        is_active=d["is_active"],
        last_sync_at=d["last_sync_at"],
        created_at=d["created_at"],
        updated_at=d["updated_at"],
        meta=meta,
        health=health,
        token_expires_at=token_expires,
    )


async def _get_connection(
    session: AsyncSession, tenant_id: uuid.UUID
) -> Optional[SalesforceConnection]:
    result = await session.execute(
        select(SalesforceConnection).where(SalesforceConnection.tenant_id == tenant_id)
    )
    return result.scalar_one_or_none()


async def _require_connection(
    session: AsyncSession, tenant_id: uuid.UUID
) -> SalesforceConnection:
    conn = await _get_connection(session, tenant_id)
    if conn is None:
        raise HTTPException(status_code=404, detail="salesforce connection not found")
    if not conn.is_active:
        raise HTTPException(status_code=422, detail="salesforce connection is inactive")
    return conn


async def _upsert_connection_and_integration(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    instance_url: str,
    credentials: dict[str, Any],
    meta: dict[str, Any],
) -> SalesforceConnection:
    key_ring = _require_key_ring()
    clean_creds = {
        k: str(v)
        for k, v in credentials.items()
        if v is not None and str(v).strip() != ""
    }
    envelope, key_id = crypto.encrypt_credentials(
        clean_creds,
        tenant_id=str(tenant_id),
        provider=CrmProviderType.SALESFORCE.value,
        key_ring=key_ring,
    )
    refresh_envelope = ""
    if clean_creds.get("refresh_token"):
        refresh_envelope, _ = crypto.encrypt_credentials(
            {"refresh_token": clean_creds["refresh_token"]},
            tenant_id=str(tenant_id),
            provider=CrmProviderType.SALESFORCE.value,
            key_ring=key_ring,
        )

    now_naive = datetime.utcnow()
    now_utc = _now()

    integration = (
        await session.execute(
            select(CrmIntegration).where(
                CrmIntegration.tenant_id == tenant_id,
                CrmIntegration.provider == CrmProviderType.SALESFORCE,
            )
        )
    ).scalar_one_or_none()
    if integration is None:
        integration = CrmIntegration(
            tenant_id=tenant_id,
            provider=CrmProviderType.SALESFORCE,
            is_enabled=True,
            credentials_encrypted=envelope,
            credentials_key_id=key_id,
            credentials_updated_at=now_naive,
            config={"instance_url": instance_url, "api_version": SALESFORCE_API_VERSION},
            field_mappings={},
            subscribed_events=["call.completed", "lead.created", "lead.updated"],
        )
        session.add(integration)
        await session.flush()
    else:
        integration.is_enabled = True
        integration.credentials_encrypted = envelope
        integration.credentials_key_id = key_id
        integration.credentials_updated_at=now_naive
        cfg = dict(integration.config or {})
        cfg["instance_url"] = instance_url
        cfg.setdefault("api_version", SALESFORCE_API_VERSION)
        integration.config = cfg
        integration.updated_at = now_naive
        await session.flush()

    conn = await _get_connection(session, tenant_id)
    if conn is None:
        conn = SalesforceConnection(
            tenant_id=tenant_id,
            crm_integration_id=integration.id,
            instance_url=instance_url,
            access_token_encrypted=envelope,
            refresh_token_encrypted=refresh_envelope,
            is_active=True,
            meta=meta,
            created_at=now_utc,
            updated_at=now_utc,
        )
        session.add(conn)
    else:
        conn.crm_integration_id = integration.id
        conn.instance_url = instance_url
        conn.access_token_encrypted = envelope
        if refresh_envelope:
            conn.refresh_token_encrypted = refresh_envelope
        conn.is_active = True
        merged_meta = dict(conn.meta or {})
        merged_meta.update(meta)
        conn.meta = merged_meta
        conn.updated_at = now_utc

    await session.flush()
    return conn


async def _build_provider(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    require_configured: bool = True,
) -> tuple[SalesforceConnection, SalesforceProvider]:
    conn = await _get_connection(session, tenant_id)
    if conn is None:
        if require_configured:
            raise _not_implemented("Salesforce connection is not configured for this tenant.")
        raise HTTPException(status_code=404, detail="salesforce connection not found")
    if not conn.is_active:
        raise HTTPException(status_code=422, detail="salesforce connection is inactive")

    key_ring = _require_key_ring()
    credentials: dict[str, Any] = {}
    if conn.access_token_encrypted:
        credentials = crypto.decrypt_credentials(
            conn.access_token_encrypted,
            tenant_id=str(tenant_id),
            provider=CrmProviderType.SALESFORCE.value,
            key_ring=key_ring,
        )
    if conn.refresh_token_encrypted and not credentials.get("refresh_token"):
        ref_data = crypto.decrypt_credentials(
            conn.refresh_token_encrypted,
            tenant_id=str(tenant_id),
            provider=CrmProviderType.SALESFORCE.value,
            key_ring=key_ring,
        )
        if ref_data.get("refresh_token"):
            credentials["refresh_token"] = ref_data["refresh_token"]

    client_id = getattr(settings, "salesforce_client_id", "") or credentials.get("client_id") or ""
    client_secret = (
        getattr(settings, "salesforce_client_secret", "") or credentials.get("client_secret") or ""
    )
    if client_id:
        credentials.setdefault("client_id", client_id)
    if client_secret:
        credentials.setdefault("client_secret", client_secret)

    provider = SalesforceProvider(
        ProviderContext(
            tenant_id=str(tenant_id),
            credentials=credentials,
            config={
                "instance_url": conn.instance_url,
                "api_version": SALESFORCE_API_VERSION,
            },
            field_mappings={},
            timeout_seconds=settings.crm_request_timeout_seconds,
        )
    )
    return conn, provider


def _build_salesforce_entity(type_name: str, raw: Dict[str, Any]) -> SalesforceEntityOut:
    first = raw.get("FirstName") or ""
    last = raw.get("LastName") or ""
    combined = f"{first} {last}".strip()
    name = raw.get("Name") or combined or raw.get("Subject") or raw.get("CaseNumber")
    return SalesforceEntityOut(
        id=str(raw.get("Id") or raw.get("id") or ""),
        type=type_name,
        name=name,
        email=raw.get("Email"),
        phone=raw.get("Phone"),
        account_id=raw.get("AccountId"),
        raw=raw,
    )


# ---------------------------------------------------------------------------
# Endpoints — OAuth connect, authorize, callback, connection CRUD
# ---------------------------------------------------------------------------


@router.post("/connect", response_model=SalesforceConnectionOut, status_code=201)
async def connect_salesforce(
    payload: SalesforceConnectRequest,
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_WRITE)),
    session: AsyncSession = Depends(get_session),
    x_idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    """POST /api/integrations/salesforce/connect — Connect Salesforce with AES-GCM encrypted tokens."""
    if payload is None:
        raise _not_implemented("Salesforce connect payload is required.")
    await enforce_tenant_rate_limit(ctx.tenant_id, "salesforce_connect", 20)

    if isinstance(x_idempotency_key, str) and x_idempotency_key.strip():
        cached_id = await get_idempotent_resource_id(
            session,
            tenant_id=ctx.tenant_id,
            operation="salesforce.connect",
            key=x_idempotency_key.strip(),
        )
        if cached_id:
            existing = await session.get(SalesforceConnection, uuid.UUID(cached_id))
            if existing and existing.tenant_id == ctx.tenant_id:
                return _to_out(existing)

    meta = dict(payload.meta or {})
    if payload.scope:
        meta["scope"] = payload.scope
    if payload.token_type:
        meta["token_type"] = payload.token_type
    if payload.expires_in is not None:
        meta["expires_in"] = payload.expires_in

    conn = await _upsert_connection_and_integration(
        session,
        tenant_id=ctx.tenant_id,
        instance_url=payload.instance_url,
        credentials={
            "access_token": payload.access_token,
            "refresh_token": payload.refresh_token,
            "client_id": payload.client_id,
            "client_secret": payload.client_secret,
            "id_token": payload.id_token,
            "instance_url": payload.instance_url,
        },
        meta=meta,
    )

    if isinstance(x_idempotency_key, str) and x_idempotency_key.strip():
        await store_idempotent_resource_id(
            session,
            tenant_id=ctx.tenant_id,
            operation="salesforce.connect",
            key=x_idempotency_key.strip(),
            resource_type="salesforce_connection",
            resource_id=str(conn.id),
            request_data={"instance_url": payload.instance_url},
        )

    await record_enterprise_audit(
        session,
        ctx.tenant_id,
        ctx.user_id,
        "salesforce.connected",
        {"instance_url": payload.instance_url},
        resource_type="salesforce_connection",
        resource_id=conn.id,
    )
    await session.commit()
    await session.refresh(conn)
    return _to_out(conn)


@router.post("/oauth/authorize", response_model=dict)
async def oauth_authorize(
    payload: SalesforceOAuthAuthorizeRequest,
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """POST /api/integrations/salesforce/oauth/authorize — Initiate OAuth2 + PKCE flow."""
    if payload is None:
        raise _not_implemented("Salesforce OAuth authorize payload is required.")
    await enforce_tenant_rate_limit(ctx.tenant_id, "salesforce_oauth_authorize", 30)

    client_id = payload.client_id or getattr(settings, "salesforce_client_id", "")
    if not client_id:
        raise _not_implemented("Salesforce OAuth client_id is not configured.")

    state_token = payload.state or secrets.token_urlsafe(24)
    verifier, challenge = generate_pkce_pair()
    expires_at = _now() + timedelta(minutes=OAUTH_STATE_TTL_MINUTES)

    try:
        auth_url = build_authorization_url(
            client_id=client_id,
            redirect_uri=payload.redirect_uri,
            state=state_token,
            code_challenge=challenge,
            scope=payload.scope,
            login_url=payload.login_url,
        )
    except CrmConfigurationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

    await session.execute(
        delete(SalesforceOAuthState).where(
            SalesforceOAuthState.tenant_id == ctx.tenant_id,
            SalesforceOAuthState.state == state_token,
        )
    )
    oauth_row = SalesforceOAuthState(
        tenant_id=ctx.tenant_id,
        state=state_token,
        code_verifier=verifier,
        redirect_uri=payload.redirect_uri,
        instance_url=payload.login_url,
        scope=payload.scope,
        expires_at=expires_at,
    )
    session.add(oauth_row)
    await record_enterprise_audit(
        session,
        ctx.tenant_id,
        ctx.user_id,
        "salesforce.oauth_authorize",
        {"redirect_uri": payload.redirect_uri, "scope": payload.scope},
        resource_type="salesforce_oauth_state",
        resource_id=oauth_row.id,
    )
    await session.commit()
    return {
        "authorization_url": auth_url,
        "state": state_token,
        "code_challenge_method": "S256",
        "expires_at": expires_at.isoformat(),
    }


@router.post("/oauth/callback", response_model=SalesforceConnectionOut)
async def oauth_callback(
    payload: SalesforceOAuthCallbackRequest,
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """POST /api/integrations/salesforce/oauth/callback — Exchange OAuth2 code + PKCE verifier."""
    if payload is None:
        raise _not_implemented("Salesforce OAuth callback payload is required.")
    await enforce_tenant_rate_limit(ctx.tenant_id, "salesforce_oauth_callback", 30)

    state_row = (
        await session.execute(
            select(SalesforceOAuthState).where(
                SalesforceOAuthState.tenant_id == ctx.tenant_id,
                SalesforceOAuthState.state == payload.state,
            )
        )
    ).scalar_one_or_none()
    if state_row is None:
        raise HTTPException(status_code=400, detail="invalid or unknown OAuth state")

    exp = state_row.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if _now() >= exp:
        await session.delete(state_row)
        await session.commit()
        raise HTTPException(status_code=400, detail="OAuth state has expired")

    client_id = payload.client_id or getattr(settings, "salesforce_client_id", "")
    client_secret = payload.client_secret or getattr(
        settings, "salesforce_client_secret", ""
    )
    if not client_id or not client_secret:
        raise _not_implemented(
            "Salesforce OAuth client_id and client_secret are not configured."
        )

    login_url = payload.instance_url or state_row.instance_url or DEFAULT_LOGIN_URL
    temp_provider = SalesforceProvider(
        ProviderContext(
            tenant_id=str(ctx.tenant_id),
            credentials={},
            config={"instance_url": login_url},
            field_mappings={},
            timeout_seconds=settings.crm_request_timeout_seconds,
        )
    )
    try:
        token_data = await temp_provider.exchange_authorization_code(
            code=payload.code,
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=state_row.redirect_uri,
            code_verifier=state_row.code_verifier or None,
            login_url=login_url,
        )
    except CrmError as exc:
        raise HTTPException(status_code=502, detail=exc.safe_message) from None

    resolved_instance = validate_salesforce_instance_url(
        str(token_data.get("instance_url") or payload.instance_url or login_url)
    )
    await session.delete(state_row)

    conn = await _upsert_connection_and_integration(
        session,
        tenant_id=ctx.tenant_id,
        instance_url=resolved_instance,
        credentials={
            "access_token": str(token_data["access_token"]),
            "refresh_token": str(token_data.get("refresh_token") or ""),
            "client_id": client_id,
            "client_secret": client_secret,
            "instance_url": resolved_instance,
        },
        meta={
            "scope": token_data.get("scope") or state_row.scope,
            "token_type": token_data.get("token_type") or "Bearer",
        },
    )
    await record_enterprise_audit(
        session,
        ctx.tenant_id,
        ctx.user_id,
        "salesforce.oauth_connected",
        {"instance_url": resolved_instance},
        resource_type="salesforce_connection",
        resource_id=conn.id,
    )
    await session.commit()
    await session.refresh(conn)
    return _to_out(conn)


@router.get("/connection", response_model=SalesforceConnectionOut)
async def get_connection(
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_READ)),
    session: AsyncSession = Depends(get_session),
):
    row = await _get_connection(session, ctx.tenant_id)
    if row is None:
        raise HTTPException(status_code=404, detail="salesforce connection not found")
    return _to_out(row)


@router.get("/connections", response_model=SalesforceConnectionListOut)
async def list_connections(
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_READ)),
    session: AsyncSession = Depends(get_session),
):
    rows = (
        await session.execute(
            select(SalesforceConnection).where(
                SalesforceConnection.tenant_id == ctx.tenant_id
            )
        )
    ).scalars().all()
    return SalesforceConnectionListOut(
        connections=[_to_out(r) for r in rows], total=len(rows)
    )


@router.patch("/connection", response_model=SalesforceConnectionOut)
async def update_connection(
    instance_url: Optional[str] = None,
    is_active: Optional[bool] = None,
    meta: Optional[Dict[str, Any]] = None,
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    row = await _require_connection(session, ctx.tenant_id)
    if instance_url:
        try:
            row.instance_url = validate_salesforce_instance_url(instance_url)
        except CrmConfigurationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
    if is_active is not None:
        row.is_active = is_active
    if meta is not None:
        existing_meta = dict(row.meta or {})
        existing_meta.update(meta)
        row.meta = existing_meta
    row.updated_at = _now()
    await record_enterprise_audit(
        session,
        ctx.tenant_id,
        ctx.user_id,
        "salesforce.connection_updated",
        {"is_active": row.is_active, "instance_url": row.instance_url},
        resource_type="salesforce_connection",
        resource_id=row.id,
    )
    await session.commit()
    await session.refresh(row)
    return _to_out(row)


@router.delete("/connection")
async def disconnect_salesforce(
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    row = await _get_connection(session, ctx.tenant_id)
    if row is None:
        raise HTTPException(status_code=404, detail="salesforce connection not found")
    integration = (
        await session.execute(
            select(CrmIntegration).where(
                CrmIntegration.tenant_id == ctx.tenant_id,
                CrmIntegration.provider == CrmProviderType.SALESFORCE,
            )
        )
    ).scalar_one_or_none()
    if integration is not None:
        integration.is_enabled = False
        integration.credentials_encrypted = None
        integration.credentials_key_id = None
    await session.delete(row)
    await record_enterprise_audit(
        session,
        ctx.tenant_id,
        ctx.user_id,
        "salesforce.disconnected",
        {"tenant_id": str(ctx.tenant_id)},
        resource_type="salesforce_connection",
        resource_id=row.id,
    )
    await session.commit()
    return {"deleted": True, "tenant_id": str(ctx.tenant_id)}


@router.post("/connection/refresh", response_model=SalesforceConnectionOut)
async def refresh_token(
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """POST /api/integrations/salesforce/connection/refresh — Refresh access token using refresh_token."""
    conn, provider = await _build_provider(session, ctx.tenant_id, require_configured=True)
    try:
        token_data = await provider.refresh_access_token()
    except CrmConfigurationError as exc:
        raise _not_implemented(str(exc)) from None
    except CrmError as exc:
        raise HTTPException(status_code=502, detail=exc.safe_message) from None

    updated_creds = dict(provider.context.credentials)
    updated_creds["access_token"] = str(token_data["access_token"])
    if token_data.get("refresh_token"):
        updated_creds["refresh_token"] = str(token_data["refresh_token"])
    resolved_instance = str(token_data.get("instance_url") or conn.instance_url)

    conn = await _upsert_connection_and_integration(
        session,
        tenant_id=ctx.tenant_id,
        instance_url=resolved_instance,
        credentials=updated_creds,
        meta=dict(conn.meta or {}),
    )
    await record_enterprise_audit(
        session,
        ctx.tenant_id,
        ctx.user_id,
        "salesforce.token_refreshed",
        {"instance_url": resolved_instance},
        resource_type="salesforce_connection",
        resource_id=conn.id,
    )
    await session.commit()
    await session.refresh(conn)
    return _to_out(conn)


@router.get("/health", response_model=SalesforceHealthOut)
async def health_check(
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_READ)),
    session: AsyncSession = Depends(get_session),
):
    """GET /api/integrations/salesforce/health — Live connection health check."""
    conn = await _get_connection(session, ctx.tenant_id)
    if conn is None:
        return SalesforceHealthOut(
            connected=False,
            is_active=False,
            health="disconnected",
            checked_at=_now_iso(),
            token_valid=False,
        )
    if not conn.is_active:
        return SalesforceHealthOut(
            connected=False,
            is_active=False,
            instance_url=conn.instance_url,
            last_sync_at=conn.last_sync_at.isoformat() if conn.last_sync_at else None,
            token_valid=False,
            health="disabled",
            checked_at=_now_iso(),
        )

    try:
        _, provider = await _build_provider(session, ctx.tenant_id, require_configured=False)
        res = await provider.health_check()
        return SalesforceHealthOut(
            connected=res.connected,
            is_active=conn.is_active,
            instance_url=conn.instance_url,
            last_sync_at=conn.last_sync_at.isoformat() if conn.last_sync_at else None,
            token_valid=res.connected,
            health="healthy" if res.connected else "unhealthy",
            checked_at=_now_iso(),
        )
    except HTTPException:
        return SalesforceHealthOut(
            connected=False,
            is_active=conn.is_active,
            instance_url=conn.instance_url,
            last_sync_at=conn.last_sync_at.isoformat() if conn.last_sync_at else None,
            token_valid=False,
            health="unverified",
            checked_at=_now_iso(),
        )


# ---------------------------------------------------------------------------
# Entity listing — contacts, leads, accounts, cases, opportunities, tasks
# ---------------------------------------------------------------------------


async def _list_entity_route(
    entity_type: str,
    search: Optional[str],
    limit: int,
    offset: int,
    ctx: TenantContext,
    session: AsyncSession,
) -> SalesforceEntityListOut:
    await enforce_tenant_rate_limit(ctx.tenant_id, f"salesforce_{entity_type}", 30)
    conn, provider = await _build_provider(session, ctx.tenant_id, require_configured=False)
    clean_search = search if isinstance(search, str) else None
    try:
        records = await provider.query_entities(
            entity_type, search=clean_search, limit=limit, offset=offset
        )
        entities = [_build_salesforce_entity(entity_type, r) for r in records]
        return SalesforceEntityListOut(
            entities=entities,
            total=len(entities),
            limit=limit,
            offset=offset,
            search=clean_search,
            connected=True,
            instance_url=conn.instance_url,
        )
    except HierarchyError as exc:
        raise to_http(exc) from None
    except CrmError as exc:
        raise HTTPException(status_code=502, detail=exc.safe_message) from None


@router.get("/contacts", response_model=SalesforceEntityListOut)
async def list_contacts(
    search: Optional[str] = Query(default=None, max_length=MAX_SEARCH_LENGTH),
    limit: int = Query(DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    offset: int = Query(0, ge=0),
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_READ)),
    session: AsyncSession = Depends(get_session),
):
    return await _list_entity_route("contact", search, limit, offset, ctx, session)


@router.get("/leads", response_model=SalesforceEntityListOut)
async def list_leads(
    search: Optional[str] = Query(default=None, max_length=MAX_SEARCH_LENGTH),
    limit: int = Query(DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    offset: int = Query(0, ge=0),
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_READ)),
    session: AsyncSession = Depends(get_session),
):
    return await _list_entity_route("lead", search, limit, offset, ctx, session)


@router.get("/accounts", response_model=SalesforceEntityListOut)
async def list_accounts(
    search: Optional[str] = Query(default=None, max_length=MAX_SEARCH_LENGTH),
    limit: int = Query(DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    offset: int = Query(0, ge=0),
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_READ)),
    session: AsyncSession = Depends(get_session),
):
    return await _list_entity_route("account", search, limit, offset, ctx, session)


@router.get("/cases", response_model=SalesforceEntityListOut)
async def list_cases(
    search: Optional[str] = Query(default=None, max_length=MAX_SEARCH_LENGTH),
    limit: int = Query(DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    offset: int = Query(0, ge=0),
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_READ)),
    session: AsyncSession = Depends(get_session),
):
    return await _list_entity_route("case", search, limit, offset, ctx, session)


@router.get("/opportunities", response_model=SalesforceEntityListOut)
async def list_opportunities(
    search: Optional[str] = Query(default=None, max_length=MAX_SEARCH_LENGTH),
    limit: int = Query(DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    offset: int = Query(0, ge=0),
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_READ)),
    session: AsyncSession = Depends(get_session),
):
    return await _list_entity_route("opportunity", search, limit, offset, ctx, session)


@router.get("/tasks", response_model=SalesforceEntityListOut)
async def list_tasks(
    search: Optional[str] = Query(default=None, max_length=MAX_SEARCH_LENGTH),
    limit: int = Query(DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    offset: int = Query(0, ge=0),
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_READ)),
    session: AsyncSession = Depends(get_session),
):
    return await _list_entity_route("task", search, limit, offset, ctx, session)


@router.get("/{entity_type}/{entity_id}", response_model=SalesforceEntityOut)
async def get_entity(
    entity_type: str,
    entity_id: str,
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_READ)),
    session: AsyncSession = Depends(get_session),
):
    if entity_type not in ALLOWED_ENTITY_TYPES:
        raise HTTPException(status_code=422, detail=f"invalid entity type {entity_type}")
    await enforce_tenant_rate_limit(ctx.tenant_id, f"salesforce_get_{entity_type}", 60)
    _, provider = await _build_provider(session, ctx.tenant_id, require_configured=False)
    try:
        raw = await provider.get_sobject(entity_type, entity_id)
        return _build_salesforce_entity(entity_type, raw)
    except CrmNotFound:
        raise HTTPException(status_code=404, detail=f"{entity_type} {entity_id} not found") from None
    except CrmError as exc:
        raise HTTPException(status_code=502, detail=exc.safe_message) from None


# ---------------------------------------------------------------------------
# Write-back with CrmWritebackLog, idempotency, audit
# ---------------------------------------------------------------------------


@router.post("/writeback", response_model=SalesforceWritebackOut)
async def salesforce_writeback(
    payload: SalesforceWritebackRequest,
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_SYNC)),
    session: AsyncSession = Depends(get_session),
    x_idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    """POST /api/integrations/salesforce/writeback — Real write-back to Salesforce."""
    if payload is None:
        raise _not_implemented("Salesforce writeback payload is required.")
    await enforce_tenant_rate_limit(ctx.tenant_id, "salesforce_writeback", 60)

    idem_key = payload.idempotency_key or (
        x_idempotency_key if isinstance(x_idempotency_key, str) else None
    )
    if idem_key:
        cached_id = await get_idempotent_resource_id(
            session,
            tenant_id=ctx.tenant_id,
            operation="salesforce.writeback",
            key=idem_key,
        )
        if cached_id:
            existing = await session.get(CrmWritebackLog, uuid.UUID(cached_id))
            if existing and existing.tenant_id == ctx.tenant_id:
                return SalesforceWritebackOut(
                    id=str(existing.id),
                    status=existing.status,
                    entity_type=existing.entity_type,
                    entity_id=existing.entity_id,
                    provider="salesforce",
                    call_id=str(existing.call_id) if existing.call_id else None,
                    created_at=existing.created_at.isoformat(),
                )

    conn, provider = await _build_provider(session, ctx.tenant_id, require_configured=True)
    try:
        result = await provider.writeback_sobject(
            entity_type=payload.entity_type,
            action=payload.action,
            fields=payload.fields,
            entity_id=payload.entity_id,
        )
    except CrmError as exc:
        raise HTTPException(status_code=502, detail=exc.safe_message) from None

    call_id_val = payload.call_id or uuid.UUID(int=0)
    log_row = CrmWritebackLog(
        tenant_id=ctx.tenant_id,
        call_id=call_id_val,
        provider="salesforce",
        entity_type=payload.entity_type,
        entity_id=result.external_id,
        action=payload.action,
        status="completed",
        payload=dict(payload.fields or {}),
    )
    session.add(log_row)
    conn.last_sync_at = _now()
    await session.flush()

    if idem_key:
        await store_idempotent_resource_id(
            session,
            tenant_id=ctx.tenant_id,
            operation="salesforce.writeback",
            key=idem_key,
            resource_type="crm_writeback_log",
            resource_id=str(log_row.id),
            request_data={
                "entity_type": payload.entity_type,
                "action": payload.action,
            },
        )

    await record_enterprise_audit(
        session,
        ctx.tenant_id,
        ctx.user_id,
        "salesforce.writeback",
        {
            "entity_type": payload.entity_type,
            "entity_id": result.external_id,
            "action": payload.action,
        },
        resource_type="crm_writeback_log",
        resource_id=log_row.id,
    )
    await session.commit()
    await session.refresh(log_row)
    return SalesforceWritebackOut(
        id=str(log_row.id),
        status=log_row.status,
        entity_type=log_row.entity_type,
        entity_id=log_row.entity_id,
        provider="salesforce",
        call_id=str(payload.call_id) if payload.call_id else None,
        created_at=log_row.created_at.isoformat(),
    )


@router.get("/writebacks", response_model=dict)
async def list_writebacks(
    entity_type: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_READ)),
    session: AsyncSession = Depends(get_session),
):
    scope = [
        CrmWritebackLog.tenant_id == ctx.tenant_id,
        CrmWritebackLog.provider == "salesforce",
    ]
    if isinstance(entity_type, str) and entity_type:
        scope.append(CrmWritebackLog.entity_type == entity_type)
    if isinstance(status, str) and status:
        scope.append(CrmWritebackLog.status == status)

    total = (
        await session.execute(select(func.count(CrmWritebackLog.id)).where(*scope))
    ).scalar() or 0
    rows = (
        await session.execute(
            select(CrmWritebackLog)
            .where(*scope)
            .order_by(CrmWritebackLog.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
    ).scalars().all()
    return {
        "writebacks": [r.as_dict() for r in rows],
        "total": int(total),
        "limit": limit,
        "offset": offset,
    }


@router.post("/sync", response_model=SalesforceSyncOut)
async def sync_salesforce(
    payload: SalesforceSyncRequest,
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_SYNC)),
    session: AsyncSession = Depends(get_session),
):
    """POST /api/integrations/salesforce/sync — Fetch and verify entities across types."""
    if payload is None:
        raise _not_implemented("Salesforce sync payload is required.")
    await enforce_tenant_rate_limit(ctx.tenant_id, "salesforce_sync", 10)
    conn, provider = await _build_provider(session, ctx.tenant_id, require_configured=True)
    started_at = _now_iso()
    total_fetched = 0
    try:
        for etype in payload.entity_types:
            rows = await provider.query_entities(etype, limit=payload.limit_per_type)
            total_fetched += len(rows)
    except CrmError as exc:
        raise HTTPException(status_code=502, detail=exc.safe_message) from None

    conn.last_sync_at = _now()
    sync_id = uuid.uuid4()
    await record_enterprise_audit(
        session,
        ctx.tenant_id,
        ctx.user_id,
        "salesforce.synced",
        {"entity_types": payload.entity_types, "total_fetched": total_fetched},
        resource_type="salesforce_connection",
        resource_id=conn.id,
    )
    await session.commit()
    return SalesforceSyncOut(
        sync_id=str(sync_id),
        status="completed",
        entity_types=payload.entity_types,
        total_fetched=total_fetched,
        started_at=started_at,
        completed_at=_now_iso(),
    )


@router.get("/writebacks/{writeback_id}", response_model=dict)
async def get_writeback(
    writeback_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_READ)),
    session: AsyncSession = Depends(get_session),
):
    row = await session.get(CrmWritebackLog, writeback_id)
    if row is None or row.tenant_id != ctx.tenant_id or row.provider != "salesforce":
        raise HTTPException(status_code=404, detail="writeback not found")
    return row.as_dict()


@router.get("/oauth/states")
async def list_oauth_states(
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    now = _now()
    rows = (
        await session.execute(
            select(SalesforceOAuthState).where(
                SalesforceOAuthState.tenant_id == ctx.tenant_id
            )
        )
    ).scalars().all()
    active = []
    expired = []
    for r in rows:
        exp = r.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if now < exp:
            active.append({"state": r.state, "expires_at": exp.isoformat()})
        else:
            expired.append({"state": r.state, "expired_at": exp.isoformat()})
    return {
        "active": active,
        "expired": expired,
        "total_active": len(active),
        "total_expired": len(expired),
        "at": _now_iso(),
    }


@router.delete("/oauth/states")
async def clear_oauth_states(
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    now = _now()
    res = await session.execute(
        delete(SalesforceOAuthState).where(
            SalesforceOAuthState.tenant_id == ctx.tenant_id,
            SalesforceOAuthState.expires_at <= now,
        )
    )
    await session.commit()
    return {"cleared": int(res.rowcount or 0), "at": _now_iso()}


@router.get("/config/required", response_model=dict)
async def required_config(
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
):
    del ctx
    has_client_id = bool(getattr(settings, "salesforce_client_id", ""))
    has_client_secret = bool(getattr(settings, "salesforce_client_secret", ""))
    has_redirect = bool(getattr(settings, "salesforce_redirect_uri", ""))
    return {
        "required": ["client_id", "client_secret", "redirect_uri", "instance_url"],
        "configured": {
            "client_id": has_client_id,
            "client_secret": has_client_secret,
            "redirect_uri": has_redirect,
        },
        "all_configured": has_client_id and has_client_secret,
        "docs": "https://help.salesforce.com/s/articleView?id=sf.remoteaccess_oauth.htm",
        "at": _now_iso(),
    }


@router.get("/api-limits", response_model=dict)
async def api_limits(
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_READ)),
    session: AsyncSession = Depends(get_session),
):
    """GET /api/integrations/salesforce/api-limits — Query live Salesforce org limits."""
    _, provider = await _build_provider(session, ctx.tenant_id, require_configured=True)
    try:
        limits = await provider.get_limits()
    except CrmError as exc:
        raise HTTPException(status_code=502, detail=exc.safe_message) from None
    return {"limits": limits, "checked_at": _now_iso()}


@router.get("/objects/describe", response_model=dict)
async def describe_objects(
    sobject: str = Query(default="Contact", max_length=80),
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_READ)),
    session: AsyncSession = Depends(get_session),
):
    """GET /api/integrations/salesforce/objects/describe — Describe Salesforce sObject metadata."""
    _, provider = await _build_provider(session, ctx.tenant_id, require_configured=True)
    target = sobject if isinstance(sobject, str) and sobject else "Contact"
    try:
        meta = await provider.describe_sobject(target)
    except CrmError as exc:
        raise HTTPException(status_code=502, detail=exc.safe_message) from None
    return {"sobject": target, "describe": meta, "checked_at": _now_iso()}


@router.post("/bulk/writeback", response_model=dict)
async def bulk_writeback(
    payloads: List[SalesforceWritebackRequest],
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_SYNC)),
    session: AsyncSession = Depends(get_session),
):
    """POST /api/integrations/salesforce/bulk/writeback — Bulk writeback up to 50 items."""
    if payloads is None:
        raise _not_implemented("Salesforce bulk writeback payloads are required.")
    if len(payloads) > 50:
        raise HTTPException(status_code=422, detail="bulk writeback maximum is 50 items")

    results = []
    for item in payloads:
        out = await salesforce_writeback(item, ctx=ctx, session=session, x_idempotency_key=item.idempotency_key)
        results.append(out.model_dump())
    return {"total": len(results), "results": results}


@router.get("/stats", response_model=dict)
async def salesforce_stats(
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_READ)),
    session: AsyncSession = Depends(get_session),
):
    conn = await _get_connection(session, ctx.tenant_id)
    total_writebacks_q = await session.execute(
        select(func.count(CrmWritebackLog.id)).where(
            CrmWritebackLog.tenant_id == ctx.tenant_id,
            CrmWritebackLog.provider == "salesforce",
        )
    )
    total_writebacks = total_writebacks_q.scalar() or 0

    by_type_q = await session.execute(
        select(CrmWritebackLog.entity_type, func.count(CrmWritebackLog.id))
        .where(
            CrmWritebackLog.tenant_id == ctx.tenant_id,
            CrmWritebackLog.provider == "salesforce",
        )
        .group_by(CrmWritebackLog.entity_type)
    )
    by_type = {et: int(c) for et, c in by_type_q.all()}

    return {
        "connected": conn is not None,
        "is_active": conn.is_active if conn else False,
        "instance_url": conn.instance_url if conn else None,
        "last_sync_at": (
            conn.last_sync_at.isoformat() if conn and conn.last_sync_at else None
        ),
        "total_writebacks": int(total_writebacks),
        "by_entity_type": by_type,
        "at": _now_iso(),
    }
