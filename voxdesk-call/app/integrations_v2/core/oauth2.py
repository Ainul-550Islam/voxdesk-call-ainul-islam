"""OAuth 2.0 + PKCE (S256) authorization, callback exchange, single-flight refresh, and revocation."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import log
from app.db.integrations_v2_models import IntegrationConnection, OAuthStateNonce
from app.integrations_v2.core.connection_store import (
    mark_connection_error,
    open_secret_bundle,
    seal_secret_bundle,
)
from app.integrations_v2.core.errors import AuthExpired, IntegrationV2Error
from app.integrations_v2.core.http_client import Int2HttpClient, validate_vendor_url
from app.integrations_v2.settings import (
    ZOHO_ACCOUNTS_HOSTS,
    get_int2_settings,
)

STATE_TTL_SECONDS = 600
_REFRESH_LOCKS: dict[str, asyncio.Lock] = {}


@dataclass(frozen=True)
class OAuthProviderEndpoints:
    authorize_url: str
    token_url: str
    revoke_url: str = ""
    use_basic_client_auth: bool = False
    extra_authorize_params: dict[str, str] | None = None


def generate_pkce_pair() -> tuple[str, str]:
    """Generate RFC 7636 PKCE ``(code_verifier, code_challenge_s256)``."""
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def _hash_state_token(raw_state: str) -> str:
    return hashlib.sha256(raw_state.encode("utf-8")).hexdigest()


def build_oauth_endpoints(
    provider: str,
    *,
    instance_url: str = "",
    region: str = "us",
    extra: dict[str, Any] | None = None,
) -> OAuthProviderEndpoints:
    """Resolve vendor-authentic OAuth2 authorize/token/revoke URLs."""
    cfg = get_int2_settings()
    extra = extra or {}

    if provider == "zendesk":
        sub = (extra.get("subdomain") or "").strip().lower()
        base = instance_url.rstrip("/") if instance_url else (f"https://{sub}.zendesk.com" if sub else "")
        if not base:
            raise IntegrationV2Error("Zendesk requires 'subdomain' or 'instance_url'", provider=provider)
        validate_vendor_url(base, provider="zendesk")
        return OAuthProviderEndpoints(
            authorize_url=f"{base}/oauth/authorizations/new",
            token_url=f"{base}/oauth/tokens",
            revoke_url=f"{base}/api/v2/oauth/tokens/current.json",
        )

    if provider == "servicenow":
        base = instance_url.rstrip("/")
        if not base:
            raise IntegrationV2Error("ServiceNow requires 'instance_url'", provider=provider)
        validate_vendor_url(base, provider="servicenow")
        return OAuthProviderEndpoints(
            authorize_url=f"{base}/oauth_auth.do",
            token_url=f"{base}/oauth_token.do",
        )

    if provider in ("zoho_desk", "zoho_crm"):
        reg = (region or cfg.zoho_region or "us").lower()
        host = ZOHO_ACCOUNTS_HOSTS.get(reg)
        if not host:
            raise IntegrationV2Error(f"Unsupported Zoho region {reg!r}", provider=provider)
        return OAuthProviderEndpoints(
            authorize_url=f"https://{host}/oauth/v2/auth",
            token_url=f"https://{host}/oauth/v2/token",
            revoke_url=f"https://{host}/oauth/v2/token/revoke",
            extra_authorize_params={"access_type": "offline", "prompt": "consent"},
        )

    if provider == "freshsales":
        domain = (extra.get("domain") or "").strip().lower()
        base = (
            instance_url.rstrip("/")
            if instance_url
            else (f"https://{domain}.myfreshworks.com" if domain else "")
        )
        if not base:
            raise IntegrationV2Error(
                "Freshsales OAuth requires 'domain' or 'instance_url'", provider=provider
            )
        validate_vendor_url(base, provider="freshsales")
        return OAuthProviderEndpoints(
            authorize_url=f"{base}/org/oauth/v2/authorize",
            token_url=f"{base}/org/oauth/v2/token",
        )

    if provider == "dynamics365":
        entra_tid = (extra.get("entra_tenant_id") or cfg.dynamics_tenant_id or "common").strip()
        return OAuthProviderEndpoints(
            authorize_url=f"https://login.microsoftonline.com/{entra_tid}/oauth2/v2.0/authorize",
            token_url=f"https://login.microsoftonline.com/{entra_tid}/oauth2/v2.0/token",
        )

    if provider == "pipedrive":
        return OAuthProviderEndpoints(
            authorize_url="https://oauth.pipedrive.com/oauth/authorize",
            token_url="https://oauth.pipedrive.com/oauth/token",
            revoke_url="https://oauth.pipedrive.com/oauth/revoke",
            use_basic_client_auth=True,
        )

    if provider == "teams":
        entra_tid = (extra.get("entra_tenant_id") or cfg.teams_tenant_id or "common").strip()
        return OAuthProviderEndpoints(
            authorize_url=f"https://login.microsoftonline.com/{entra_tid}/oauth2/v2.0/authorize",
            token_url=f"https://login.microsoftonline.com/{entra_tid}/oauth2/v2.0/token",
        )

    if provider == "zoom":
        return OAuthProviderEndpoints(
            authorize_url="https://zoom.us/oauth/authorize",
            token_url="https://zoom.us/oauth/token",
            revoke_url="https://zoom.us/oauth/revoke",
            use_basic_client_auth=True,
        )

    if provider == "slack":
        return OAuthProviderEndpoints(
            authorize_url="https://slack.com/oauth/v2/authorize",
            token_url="https://slack.com/api/oauth.v2.access",
            revoke_url="https://slack.com/api/auth.revoke",
        )

    raise IntegrationV2Error(f"OAuth2 is not supported for provider {provider!r}", provider=provider)


async def start_oauth_flow(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    provider: str,
    scopes: list[str] | None = None,
    redirect_uri_override: str = "",
    instance_url: str = "",
    region: str = "us",
    extra_params: dict[str, Any] | None = None,
) -> tuple[str, OAuthStateNonce]:
    """Persist a single-use PKCE state nonce (10-min TTL) and return the vendor authorization URL."""
    cfg = get_int2_settings()
    client_id, client_secret, cfg_redirect = cfg.oauth_credentials(provider)
    if not (client_id.strip() and client_secret.strip()):
        raise IntegrationV2Error(
            f"OAuth client credentials for {provider!r} are not configured in environment",
            provider=provider,
        )
    redirect_uri = (redirect_uri_override or cfg_redirect).strip()
    if not redirect_uri:
        raise IntegrationV2Error(
            f"OAuth redirect_uri for {provider!r} is not configured", provider=provider
        )

    endpoints = build_oauth_endpoints(
        provider, instance_url=instance_url, region=region, extra=extra_params
    )

    raw_nonce = secrets.token_urlsafe(32)
    composite_state = f"{tenant_id}:{raw_nonce}"
    state_hash = _hash_state_token(composite_state)
    verifier, challenge = generate_pkce_pair()

    verifier_env, _ = seal_secret_bundle(
        {"code_verifier": verifier},
        tenant_id=tenant_id,
        provider=f"{provider}:pkce",
    )

    stored_extra = dict(extra_params or {})
    if instance_url:
        stored_extra["instance_url"] = instance_url
    if region:
        stored_extra["region"] = region
    if scopes:
        stored_extra["scopes"] = list(scopes)

    now = datetime.now(timezone.utc)
    state_row = OAuthStateNonce(
        tenant_id=tenant_id,
        user_id=user_id,
        provider=provider,
        state_hash=state_hash,
        pkce_verifier_secret_ref=verifier_env,
        redirect_uri=redirect_uri,
        extra_params=stored_extra,
        expires_at=now + timedelta(seconds=STATE_TTL_SECONDS),
    )
    session.add(state_row)
    await session.flush()

    query: dict[str, str] = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": composite_state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if scopes:
        sep = "," if provider in ("zoho_desk", "zoho_crm", "slack") else " "
        if provider == "slack":
            query["scope"] = sep.join(scopes)
        else:
            query["scope"] = sep.join(scopes)
    if endpoints.extra_authorize_params:
        query.update(endpoints.extra_authorize_params)

    return f"{endpoints.authorize_url}?{urlencode(query)}", state_row


async def exchange_oauth_callback(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    provider: str,
    state_token: str,
    code: str,
    http_client: Int2HttpClient | None = None,
) -> tuple[dict[str, Any], OAuthStateNonce]:
    """Validate single-use CSRF state + tenant binding + TTL and exchange ``code`` + PKCE verifier."""
    if not state_token or ":" not in state_token:
        raise AuthExpired("Invalid OAuth state parameter", provider=provider)
    embedded_tid, _ = state_token.split(":", 1)
    if embedded_tid != str(tenant_id):
        raise AuthExpired("OAuth state tenant mismatch", provider=provider)

    state_hash = _hash_state_token(state_token)
    stmt = select(OAuthStateNonce).where(
        OAuthStateNonce.state_hash == state_hash,
        OAuthStateNonce.tenant_id == tenant_id,
        OAuthStateNonce.provider == provider,
    )
    state_row = (await session.execute(stmt)).scalar_one_or_none()
    if state_row is None:
        raise AuthExpired("OAuth state nonce not found or does not belong to tenant", provider=provider)
    if state_row.consumed_at is not None:
        raise AuthExpired("OAuth state nonce was already consumed (replay blocked)", provider=provider)

    now = datetime.now(timezone.utc)
    exp = state_row.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if now >= exp:
        raise AuthExpired("OAuth state nonce has expired", provider=provider)

    # Mark consumed before outbound exchange so replays cannot race
    state_row.consumed_at = now
    await session.flush()

    pkce_bundle = open_secret_bundle(
        state_row.pkce_verifier_secret_ref,
        tenant_id=tenant_id,
        provider=f"{provider}:pkce",
    )
    code_verifier = str(pkce_bundle.get("code_verifier") or "")

    cfg = get_int2_settings()
    client_id, client_secret, _ = cfg.oauth_credentials(provider)
    if not (client_id and client_secret):
        raise IntegrationV2Error(f"Missing OAuth client credentials for {provider}", provider=provider)

    extra = dict(state_row.extra_params or {})
    endpoints = build_oauth_endpoints(
        provider,
        instance_url=str(extra.get("instance_url") or ""),
        region=str(extra.get("region") or "us"),
        extra=extra,
    )

    form_data: dict[str, str] = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": state_row.redirect_uri,
        "code_verifier": code_verifier,
    }
    auth_tuple: tuple[str, str] | None = None
    if endpoints.use_basic_client_auth:
        auth_tuple = (client_id, client_secret)
    else:
        form_data["client_id"] = client_id
        form_data["client_secret"] = client_secret

    client = http_client or Int2HttpClient(timeout_s=cfg.http_timeout_seconds)
    resp = await client.request(
        "POST",
        endpoints.token_url,
        provider=provider,
        tenant_id=str(tenant_id),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=form_data,
        auth=auth_tuple,
    )
    payload = resp.json()
    if provider == "slack" and payload.get("ok") is False:
        raise AuthExpired(
            f"Slack OAuth token exchange failed: {payload.get('error', 'unknown_error')}",
            provider=provider,
        )
    if "error" in payload and not payload.get("access_token"):
        raise AuthExpired(
            f"{provider} OAuth exchange error: {payload.get('error')}", provider=provider
        )
    return payload, state_row


def _lock_for_connection(connection_id: str) -> asyncio.Lock:
    lock = _REFRESH_LOCKS.get(connection_id)
    if lock is None:
        lock = asyncio.Lock()
        _REFRESH_LOCKS[connection_id] = lock
    return lock


async def refresh_connection_token(
    session: AsyncSession,
    row: IntegrationConnection,
    *,
    http_client: Int2HttpClient | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Single-flight OAuth2 refresh with refresh-token rotation persistence."""
    from sqlalchemy import inspect as sa_inspect

    ident = sa_inspect(row).identity
    conn_key = str(ident[0]) if ident else str(row.__dict__.get("id") or "")
    lock = _lock_for_connection(conn_key)
    async with lock:
        await session.refresh(row)
        now = datetime.now(timezone.utc)
        if not force and row.token_expires_at is not None:
            exp = row.token_expires_at
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if exp - now > timedelta(seconds=120):
                return open_secret_bundle(
                    row.token_secret_ref, tenant_id=row.tenant_id, provider=row.provider
                )

        if not row.refresh_secret_ref:
            await mark_connection_error(
                session,
                row,
                error_message="Refresh token unavailable; user re-authentication required",
                needs_reauth=True,
            )
            raise AuthExpired("No refresh token stored for connection", provider=row.provider)

        refresh_bundle = open_secret_bundle(
            row.refresh_secret_ref,
            tenant_id=row.tenant_id,
            provider=f"{row.provider}:refresh",
        )
        refresh_tok = str(refresh_bundle.get("refresh_token") or "")
        if not refresh_tok:
            await mark_connection_error(
                session, row, error_message="Empty refresh token", needs_reauth=True
            )
            raise AuthExpired("Empty refresh token", provider=row.provider)

        cfg = get_int2_settings()
        client_id, client_secret, _ = cfg.oauth_credentials(row.provider)
        eff_region = str((row.config_json or {}).get("region") or row.region or "us")
        endpoints = build_oauth_endpoints(
            row.provider,
            instance_url=row.instance_url,
            region=eff_region,
            extra=row.config_json,
        )

        form_data: dict[str, str] = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_tok,
        }
        auth_tuple: tuple[str, str] | None = None
        if endpoints.use_basic_client_auth:
            auth_tuple = (client_id, client_secret)
        else:
            form_data["client_id"] = client_id
            form_data["client_secret"] = client_secret

        client = http_client or Int2HttpClient(timeout_s=cfg.http_timeout_seconds)
        try:
            resp = await client.request(
                "POST",
                endpoints.token_url,
                provider=row.provider,
                tenant_id=str(row.tenant_id),
                connection_id=str(row.id),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data=form_data,
                auth=auth_tuple,
            )
            payload = resp.json()
        except AuthExpired as exc:
            await mark_connection_error(
                session, row, error_message=str(exc), needs_reauth=True
            )
            raise

        if "error" in payload and not payload.get("access_token"):
            await mark_connection_error(
                session,
                row,
                error_message=f"Refresh rejected: {payload.get('error')}",
                needs_reauth=True,
            )
            raise AuthExpired(
                f"Token refresh failed: {payload.get('error')}", provider=row.provider
            )

        new_access = str(payload.get("access_token") or "")
        if not new_access:
            raise AuthExpired("Vendor refresh response omitted access_token", provider=row.provider)

        existing = open_secret_bundle(
            row.token_secret_ref, tenant_id=row.tenant_id, provider=row.provider
        )
        existing["access_token"] = new_access
        if payload.get("api_domain"):
            existing["api_domain"] = payload["api_domain"]

        token_env, key_id = seal_secret_bundle(
            existing, tenant_id=row.tenant_id, provider=row.provider
        )
        row.token_secret_ref = token_env
        row.encryption_key_id = key_id

        rotated_refresh = str(payload.get("refresh_token") or "").strip()
        if rotated_refresh:
            ref_env, _ = seal_secret_bundle(
                {"refresh_token": rotated_refresh},
                tenant_id=row.tenant_id,
                provider=f"{row.provider}:refresh",
            )
            row.refresh_secret_ref = ref_env

        expires_in = payload.get("expires_in")
        if expires_in is not None:
            row.token_expires_at = now + timedelta(seconds=max(60, int(expires_in)))
        row.status = "active"
        row.last_error = ""
        row.updated_at = now
        await session.flush()
        log.info("int2.oauth.refreshed", provider=row.provider, connection_id=str(row.id))
        return existing


async def revoke_connection_tokens(
    row: IntegrationConnection,
    *,
    http_client: Int2HttpClient | None = None,
) -> bool:
    """Best-effort vendor token revocation when disconnecting an OAuth2 connection."""
    if not row.token_secret_ref or row.auth_kind == "api_key":
        return False
    try:
        endpoints = build_oauth_endpoints(
            row.provider,
            instance_url=row.instance_url,
            region=row.region,
            extra=row.config_json,
        )
    except IntegrationV2Error:
        return False

    if not endpoints.revoke_url:
        return False

    creds = open_secret_bundle(
        row.token_secret_ref, tenant_id=row.tenant_id, provider=row.provider
    )
    access_token = str(creds.get("access_token") or "")
    if not access_token:
        return False

    cfg = get_int2_settings()
    client_id, client_secret, _ = cfg.oauth_credentials(row.provider)
    client = http_client or Int2HttpClient(timeout_s=5.0, max_retries=1)

    try:
        if row.provider == "zendesk":
            await client.request(
                "DELETE",
                endpoints.revoke_url,
                provider=row.provider,
                tenant_id=str(row.tenant_id),
                headers={"Authorization": f"Bearer {access_token}"},
            )
        elif row.provider == "slack":
            await client.request(
                "POST",
                endpoints.revoke_url,
                provider=row.provider,
                tenant_id=str(row.tenant_id),
                headers={"Authorization": f"Bearer {access_token}"},
            )
        else:
            auth_tuple = (client_id, client_secret) if endpoints.use_basic_client_auth else None
            await client.request(
                "POST",
                endpoints.revoke_url,
                provider=row.provider,
                tenant_id=str(row.tenant_id),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data={"token": access_token},
                auth=auth_tuple,
            )
        return True
    except Exception as exc:
        log.warning(
            "int2.oauth.revoke_failed",
            provider=row.provider,
            connection_id=str(row.id),
            error=str(exc)[:120],
        )
        return False
