"""Provider-neutral OAuth authorization-code lifecycle for connectors."""

from __future__ import annotations
import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.identity.exceptions import IdentitySecretsUnavailable
from app.auth.identity.secrets import PURPOSE_PKCE_VERIFIER, decrypt_text, encrypt_text
from app.core.config import settings
from app.core.ssrf import validate_outbound_url, validate_resolved_outbound_url
from app.core.tracing import span
from app.db.models import Connector, ConnectorOAuth
from app.security.secret_store import SecretStoreError, get_secret_store


class OAuthError(RuntimeError):
    """OAuth state, transport, provider, or credential failure."""


@dataclass(frozen=True)
class OAuthConfig:
    authorization_url: str
    token_url: str
    client_id: str
    client_secret_ref: str
    redirect_uri: str
    scopes: tuple[str, ...]
    revoke_url: str | None = None


def _sign_state(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hmac.new(settings.jwt_secret.encode(), raw.encode(), hashlib.sha256).hexdigest()
    return f"{secrets.token_urlsafe(8)}.{digest}.{raw.encode().hex()}"


def _open_state(state: str) -> dict:
    try:
        _nonce, digest, encoded = state.split(".", 2)
        raw = bytes.fromhex(encoded).decode()
        expected = hmac.new(settings.jwt_secret.encode(), raw.encode(), hashlib.sha256).hexdigest()
        payload = json.loads(raw)
        if not hmac.compare_digest(expected, digest) or int(payload.get("exp", 0)) < int(
            time.time()
        ):
            raise ValueError
        return payload
    except (ValueError, TypeError, json.JSONDecodeError):
        raise OAuthError("oauth state is invalid or expired") from None


async def authorization_url(
    session: AsyncSession, connector: Connector, config: OAuthConfig
) -> str:
    validate_outbound_url(config.authorization_url, require_https=True)
    validate_outbound_url(config.redirect_uri, require_https=True)
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    try:
        sealed_verifier, _ = encrypt_text(
            verifier,
            tenant_id=str(connector.tenant_id),
            purpose=PURPOSE_PKCE_VERIFIER,
        )
    except IdentitySecretsUnavailable as exc:
        raise OAuthError("OAuth PKCE encryption is not configured") from exc
    payload = {
        "connector_id": str(connector.id),
        "tenant_id": str(connector.tenant_id),
        "exp": int(time.time()) + 600,
        "nonce": secrets.token_urlsafe(18),
        # The verifier is sealed before it enters signed state; the browser
        # never receives a usable PKCE verifier.
        "pkce_verifier": sealed_verifier,
    }
    oauth_row = (
        await session.execute(
            select(ConnectorOAuth).where(
                ConnectorOAuth.connector_id == connector.id,
                ConnectorOAuth.tenant_id == connector.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if oauth_row is None:
        oauth_row = ConnectorOAuth(tenant_id=connector.tenant_id, connector_id=connector.id)
        session.add(oauth_row)
    oauth_row.state = "authorizing"
    oauth_row.oauth_state_nonce = payload["nonce"]
    oauth_row.oauth_state_expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
    await session.flush()
    params = {
        "response_type": "code",
        "client_id": config.client_id,
        "redirect_uri": config.redirect_uri,
        "scope": " ".join(config.scopes),
        "state": _sign_state(payload),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{config.authorization_url}?{urlencode(params)}"


async def _token_request(url: str, body: dict) -> dict:
    await validate_resolved_outbound_url(url, require_https=True)
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
        with span("voxdesk.oauth.token", provider="oauth"):
            response = await client.post(url, data=body)
    if response.status_code >= 400:
        raise OAuthError(f"oauth provider rejected the token request ({response.status_code})")
    if response.status_code == 204 or not response.content:
        return {}
    try:
        data = response.json()
    except ValueError as exc:
        raise OAuthError("oauth provider returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise OAuthError("oauth provider returned an invalid token object")
    return data


async def _save_tokens(
    session: AsyncSession,
    connector: Connector,
    config: OAuthConfig,
    data: dict,
    row: ConnectorOAuth | None = None,
) -> ConnectorOAuth:
    access = data.get("access_token")
    if not isinstance(access, str) or not access:
        raise OAuthError("oauth provider did not return an access token")
    store = get_secret_store()
    access_ref = store.put(str(connector.tenant_id), f"oauth:{connector.provider}:access", access)
    refresh = data.get("refresh_token")
    refresh_ref = (
        store.put(str(connector.tenant_id), f"oauth:{connector.provider}:refresh", refresh)
        if isinstance(refresh, str) and refresh
        else (row.refresh_token_ref if row else None)
    )
    if row is None:
        row = ConnectorOAuth(tenant_id=connector.tenant_id, connector_id=connector.id)
        session.add(row)
    row.state, row.scopes = "connected", list(config.scopes)
    row.access_token_ref, row.refresh_token_ref = access_ref, refresh_ref
    row.provider_account_ref = (
        str(data.get("account_id")) if data.get("account_id") is not None else None
    )
    row.expires_at = datetime.now(timezone.utc) + timedelta(
        seconds=int(data.get("expires_in", 3600))
    )
    await session.flush()
    return row


async def exchange_callback(
    session: AsyncSession, connector: Connector, config: OAuthConfig, state: str, code: str
) -> ConnectorOAuth:
    payload = _open_state(state)
    if payload.get("connector_id") != str(connector.id) or payload.get("tenant_id") != str(
        connector.tenant_id
    ):
        raise OAuthError("oauth state does not belong to this connector")
    oauth_row = (
        await session.execute(
            select(ConnectorOAuth).where(
                ConnectorOAuth.connector_id == connector.id,
                ConnectorOAuth.tenant_id == connector.tenant_id,
            )
        )
    ).scalar_one_or_none()
    expires_at = oauth_row.oauth_state_expires_at if oauth_row is not None else None
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if (
        oauth_row is None
        or oauth_row.state != "authorizing"
        or oauth_row.oauth_state_nonce != payload.get("nonce")
        or expires_at is None
        or expires_at < datetime.now(timezone.utc)
    ):
        raise OAuthError("oauth state is invalid, expired, or already consumed")
    # Consume the durable nonce before the provider exchange. A callback can
    # therefore be used only once even if a provider repeats the redirect.
    oauth_row.state = "exchanging"
    oauth_row.oauth_state_nonce = None
    oauth_row.oauth_state_expires_at = None
    sealed_verifier = payload.get("pkce_verifier")
    if not isinstance(sealed_verifier, str) or not sealed_verifier:
        raise OAuthError("OAuth state has no PKCE verifier")
    try:
        verifier = decrypt_text(
            sealed_verifier,
            tenant_id=str(connector.tenant_id),
            purpose=PURPOSE_PKCE_VERIFIER,
        )
        secret = get_secret_store().get(
            str(connector.tenant_id), f"oauth:{connector.provider}", config.client_secret_ref
        )
    except (IdentitySecretsUnavailable, SecretStoreError) as exc:
        raise OAuthError("OAuth callback credentials are unavailable") from exc
    data = await _token_request(
        config.token_url,
        {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": config.client_id,
            "client_secret": secret,
            "redirect_uri": config.redirect_uri,
            "code_verifier": verifier,
        },
    )
    return await _save_tokens(session, connector, config, data, oauth_row)


async def refresh_access_token(
    session: AsyncSession, connector: Connector, config: OAuthConfig
) -> ConnectorOAuth:
    row = (
        await session.execute(
            select(ConnectorOAuth).where(
                ConnectorOAuth.connector_id == connector.id,
                ConnectorOAuth.tenant_id == connector.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if row is None or not row.refresh_token_ref:
        raise OAuthError("connector has no refresh token")
    try:
        client_secret = get_secret_store().get(
            str(connector.tenant_id), f"oauth:{connector.provider}", config.client_secret_ref
        )
        refresh = get_secret_store().get(
            str(connector.tenant_id), f"oauth:{connector.provider}:refresh", row.refresh_token_ref
        )
    except SecretStoreError as exc:
        raise OAuthError("oauth refresh credentials are unavailable") from exc
    data = await _token_request(
        config.token_url,
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh,
            "client_id": config.client_id,
            "client_secret": client_secret,
        },
    )
    return await _save_tokens(session, connector, config, data, row)


async def disconnect(session: AsyncSession, connector: Connector, config: OAuthConfig) -> None:
    row = (
        await session.execute(
            select(ConnectorOAuth).where(
                ConnectorOAuth.connector_id == connector.id,
                ConnectorOAuth.tenant_id == connector.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return
    if config.revoke_url and row.access_token_ref:
        try:
            token = get_secret_store().get(
                str(connector.tenant_id), f"oauth:{connector.provider}:access", row.access_token_ref
            )
        except SecretStoreError:
            token = None
        if token:
            await _token_request(config.revoke_url, {"token": token, "client_id": config.client_id})
    row.state, row.access_token_ref, row.refresh_token_ref = "disconnected", None, None
    await session.flush()
