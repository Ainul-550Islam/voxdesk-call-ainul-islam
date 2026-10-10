"""Browser web-call session and single-use JWT lifecycle (PART 3 — Gate G4).

Closes F-06 (`app/api/outbound_call_routes.py` `tok_{uuid}` fallback) and Retell parity #22:
- `create_web_call(session, tenant, agent_id, version, dynamic_vars, metadata)` ->
  `{call_id, access_token, transport, url}`
- Short-lived single-use HS256 JWT bound to `call_id`, `tenant_id`, `jti`, and `origin`
- Zero fallback tokens: fails closed if `settings.jwt_secret` is unconfigured
- Durable single-use replay protection via `RequestIdempotencyReceipt` + `Call.transfer_context`
- Per-public-key and per-tenant rate limiting via `app.core.rate_limit`
- Origin allowlist enforcement via `app.services.public_origin_service`
- Resolves `RuntimeConfig` via `app.runtime.agent_config_resolver.resolve_runtime_config` (PART 2E)
- Persists `Call(direction="web")`
"""

from __future__ import annotations

import hashlib
import secrets
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
import jwt
from jwt import InvalidTokenError as JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import sqltypes

from app.audit.service import record_event
from app.core.config import settings
from app.core.rate_limit import allow_identity_action
from app.db.models import (
    Agent,
    Call,
    CallDirection,
    CallStatus,
    Environment,
    RequestIdempotencyReceipt,
    Tenant,
    Turn,
)
from app.db.telephony_models import CallLatencyStat
from app.domain.public_site_models import PublicDomainError
from app.domain.public_widget_models import PublicWidgetCapability
from app.runtime.agent_config_resolver import (
    RuntimeConfig,
    resolve_runtime_config,
)
from app.services.public_key_service import authenticate_public_key
from app.services.public_origin_service import (
    normalize_origin,
    validate_origin_against_allowlist,
)
from app.telephony.media_gateway import media_gateway_manager

WEB_CALL_JWT_ALGORITHM = "HS256"
WEB_CALL_TOKEN_TYPE = "voxdesk_web_call"
DEFAULT_WEB_CALL_TTL_SECONDS = 300
DEFAULT_WEB_CALL_RATE_LIMIT_PER_MINUTE = 30


class _WebCallDirectionStr(str):
    """String subclass representing `CallDirection` value `'web'` with `.value` and `.name`."""

    @property
    def value(self) -> str:
        return "web"

    @property
    def name(self) -> str:
        return "WEB"


WEB_DIRECTION = _WebCallDirectionStr("web")


def _install_web_call_direction_support() -> None:
    """Allow `Call(direction="web")` on SQLAlchemy's `Enum(CallDirection)` column
    without mutating `CallDirection.__members__` (preserving protobuf enum contract parity).
    """
    if not hasattr(CallDirection, "WEB"):
        setattr(CallDirection, "WEB", WEB_DIRECTION)

    col_type = Call.__table__.c.direction.type
    if isinstance(col_type, sqltypes.Enum):
        for token in ("web", "WEB"):
            if token not in col_type.enums:
                col_type.enums.append(token)
            col_type._object_lookup[token] = WEB_DIRECTION
            col_type._valid_lookup[token] = "web"
        col_type._valid_lookup[WEB_DIRECTION] = "web"

        if not getattr(col_type, "_web_adapt_patched", False):
            orig_adapt = col_type.adapt

            def _adapt(*args: Any, **kwargs: Any) -> Any:
                adapted = orig_adapt(*args, **kwargs)
                if hasattr(adapted, "_object_lookup"):
                    adapted._object_lookup["web"] = WEB_DIRECTION
                    adapted._object_lookup["WEB"] = WEB_DIRECTION
                if hasattr(adapted, "_valid_lookup"):
                    adapted._valid_lookup[WEB_DIRECTION] = "web"
                    adapted._valid_lookup["web"] = "web"
                    adapted._valid_lookup["WEB"] = "web"
                return adapted

            col_type.adapt = _adapt  # type: ignore[method-assign]
            setattr(col_type, "_web_adapt_patched", True)

    if not getattr(sqltypes.Enum, "_web_elem_patched", False):
        orig_obj_val = sqltypes.Enum._object_value_for_elem

        def _patched_object_value_for_elem(self: sqltypes.Enum, elem: str) -> Any:
            if getattr(self, "name", None) == "calldirection" and elem in ("web", "WEB"):
                return WEB_DIRECTION
            return orig_obj_val(self, elem)

        sqltypes.Enum._object_value_for_elem = _patched_object_value_for_elem  # type: ignore[method-assign]
        setattr(sqltypes.Enum, "_web_elem_patched", True)


_install_web_call_direction_support()


class WebCallSecurityError(HTTPException):
    """Typed security/validation error for browser web calls."""

    def __init__(self, *, status_code: int, code: str, message: str) -> None:
        super().__init__(
            status_code=status_code,
            detail={"code": code, "message": message},
        )
        self.code = code
        self.message = message


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


_LOCAL_RATE_WINDOWS: dict[str, list[float]] = {}


def reset_web_call_rate_limits() -> None:
    """Reset in-memory web-call rate limit windows (used by security tests)."""
    _LOCAL_RATE_WINDOWS.clear()


def _signing_secret() -> str:
    import os

    app_env = (os.getenv("APP_ENV") or getattr(settings, "app_env", "") or "").strip().lower()
    explicit_media_key = (
        os.getenv("WEB_CALL_JWT_SECRET")
        or os.getenv("REALTIME_MEDIA_SIGNING_KEY")
        or getattr(settings, "realtime_media_signing_key", "")
        or ""
    ).strip()
    if explicit_media_key:
        return explicit_media_key

    if app_env in {"production", "staging", "prod"}:
        raise WebCallSecurityError(
            status_code=503,
            code="WEB_CALL_SIGNING_KEY_MISSING",
            message="REALTIME_MEDIA_SIGNING_KEY or WEB_CALL_JWT_SECRET must be configured in production.",
        )

    secret = (settings.jwt_secret or "").strip()
    if not secret:
        raise WebCallSecurityError(
            status_code=503,
            code="WEB_CALL_SIGNING_KEY_MISSING",
            message="Web call token signing requires a configured signing secret.",
        )
    return secret


def _build_ws_url(*, base_url: str | None = None) -> str:
    raw_base = (base_url or getattr(settings, "public_base_url", "") or "").strip().rstrip("/")
    if raw_base.startswith("https://"):
        return f"wss://{raw_base[len('https://'):]}/telephony/web/ws"
    if raw_base.startswith("http://"):
        return f"ws://{raw_base[len('http://'):]}/telephony/web/ws"
    return "/telephony/web/ws"


async def _enforce_rate_limit(
    *,
    scope_key: str,
    limit_per_minute: int,
) -> None:
    cap = max(1, int(limit_per_minute))
    now_mono = time.monotonic()
    bucket = _LOCAL_RATE_WINDOWS.setdefault(scope_key, [])
    cutoff = now_mono - 60.0
    while bucket and bucket[0] <= cutoff:
        bucket.pop(0)
    if len(bucket) >= cap:
        raise WebCallSecurityError(
            status_code=429,
            code="RATE_LIMITED",
            message="Web call rate limit exceeded. Please wait before starting another call.",
        )
    bucket.append(now_mono)

    allowed = await allow_identity_action(
        action="web_call.create",
        who=scope_key,
        limit=cap,
        window=60.0,
    )
    if not allowed:
        raise WebCallSecurityError(
            status_code=429,
            code="RATE_LIMITED",
            message="Web call rate limit exceeded. Please wait before starting another call.",
        )


async def _resolve_agent_uuid(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    agent_id: str | uuid.UUID,
) -> Agent:
    agent_row: Agent | None = None
    try:
        agent_uuid = agent_id if isinstance(agent_id, uuid.UUID) else uuid.UUID(str(agent_id))
        agent_row = await session.scalar(
            select(Agent).where(
                Agent.id == agent_uuid,
                Agent.tenant_id == tenant_id,
                Agent.deleted_at.is_(None),
                Agent.archived_at.is_(None),
            )
        )
    except (ValueError, TypeError):
        agent_row = None

    if agent_row is None:
        agent_row = await session.scalar(
            select(Agent).where(
                Agent.external_key == str(agent_id).strip(),
                Agent.tenant_id == tenant_id,
                Agent.deleted_at.is_(None),
                Agent.archived_at.is_(None),
            )
        )

    if agent_row is None:
        raise WebCallSecurityError(
            status_code=404,
            code="AGENT_NOT_FOUND",
            message="Agent not found in this tenant.",
        )
    return agent_row


async def _resolve_environment(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID | None = None,
) -> Environment:
    if environment_id is not None:
        env = await session.scalar(
            select(Environment).where(
                Environment.id == environment_id,
                Environment.tenant_id == tenant_id,
            )
        )
        if env is None:
            raise WebCallSecurityError(
                status_code=404,
                code="ENVIRONMENT_NOT_FOUND",
                message="Environment not found for this tenant.",
            )
        return env

    env = await session.scalar(
        select(Environment)
        .where(Environment.tenant_id == tenant_id)
        .order_by(Environment.created_at.asc())
    )
    if env is None:
        env = Environment(
            tenant_id=tenant_id,
            name="Production",
            slug="production",
            kind="production",
        )
        session.add(env)
        await session.flush()
    return env


def issue_web_call_jwt(
    *,
    call_id: uuid.UUID | str,
    tenant_id: uuid.UUID | str,
    environment_id: uuid.UUID | str | None = None,
    agent_id: uuid.UUID | str,
    agent_version: int | None = 1,
    jti: str | None = None,
    origin: str | None = None,
    transport: str = "websocket",
    ttl_seconds: int = DEFAULT_WEB_CALL_TTL_SECONDS,
) -> tuple[str, datetime]:
    """Mint a short-lived single-use HS256 JWT bound to `call_id`, `tenant_id`, `jti`, and `origin`."""
    secret = _signing_secret()
    now_ts = int(time.time())
    resolved_jti = jti or secrets.token_urlsafe(24)
    if int(ttl_seconds) < 0:
        exp_ts = now_ts + int(ttl_seconds)
    else:
        exp_ts = now_ts + max(30, min(int(ttl_seconds), 3600))
    expires_at = datetime.fromtimestamp(exp_ts, tz=timezone.utc)
    claims: dict[str, Any] = {
        "typ": WEB_CALL_TOKEN_TYPE,
        "sub": str(call_id),
        "call_id": str(call_id),
        "tenant_id": str(tenant_id),
        "environment_id": str(environment_id) if environment_id else "",
        "agent_id": str(agent_id),
        "agent_version": agent_version,
        "transport": transport,
        "jti": resolved_jti,
        "iat": now_ts,
        "exp": exp_ts,
    }
    if origin:
        claims["origin"] = origin
    token = jwt.encode(claims, secret, algorithm=WEB_CALL_JWT_ALGORITHM)
    return token, expires_at


def verify_web_call_jwt(
    token: str | None,
    *,
    request_origin: str | None = None,
) -> dict[str, Any]:
    """Decode and validate a web-call JWT's signature, expiry, type, and origin binding."""
    if not token or not isinstance(token, str) or not token.strip():
        raise WebCallSecurityError(
            status_code=401,
            code="MISSING_WEB_CALL_TOKEN",
            message="Missing web call access token.",
        )
    cleaned = token.strip()
    if cleaned.lower().startswith("bearer "):
        cleaned = cleaned[7:].strip()

    secret = _signing_secret()
    try:
        claims = jwt.decode(
            cleaned,
            secret,
            algorithms=[WEB_CALL_JWT_ALGORITHM],
            options={"require_exp": True, "require_sub": True},
        )
    except JWTError as exc:
        msg = str(exc).lower()
        if "expired" in msg:
            raise WebCallSecurityError(
                status_code=401,
                code="WEB_CALL_TOKEN_EXPIRED",
                message="Web call access token has expired.",
            ) from exc
        raise WebCallSecurityError(
            status_code=401,
            code="INVALID_WEB_CALL_TOKEN",
            message="Invalid web call access token.",
        ) from exc

    if claims.get("typ") != WEB_CALL_TOKEN_TYPE or not claims.get("jti") or not claims.get("call_id"):
        raise WebCallSecurityError(
            status_code=401,
            code="INVALID_WEB_CALL_TOKEN",
            message="Token is not a valid VoxDesk web-call token.",
        )

    expected_origin = claims.get("origin")
    if expected_origin:
        if not request_origin or not request_origin.strip():
            raise WebCallSecurityError(
                status_code=403,
                code="FORBIDDEN_ORIGIN",
                message="Origin header is required for origin-bound web call tokens.",
            )
        try:
            normalized_req = normalize_origin(request_origin, allow_wildcard_dev=False)
        except PublicDomainError as exc:
            raise WebCallSecurityError(
                status_code=403,
                code="FORBIDDEN_ORIGIN",
                message=exc.message,
            ) from exc
        if normalized_req != expected_origin:
            raise WebCallSecurityError(
                status_code=403,
                code="FORBIDDEN_ORIGIN",
                message=f"Request origin '{normalized_req}' does not match token origin '{expected_origin}'.",
            )

    return claims


async def create_web_call(
    session: AsyncSession,
    tenant: Tenant,
    agent_id: str | uuid.UUID,
    version: int | None = None,
    dynamic_vars: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    *,
    environment_id: uuid.UUID | None = None,
    origin: str | None = None,
    allowed_origins: list[str] | None = None,
    public_key_id: uuid.UUID | None = None,
    rate_limit_per_minute: int = DEFAULT_WEB_CALL_RATE_LIMIT_PER_MINUTE,
    transport: str = "websocket",
    ttl_seconds: int = DEFAULT_WEB_CALL_TTL_SECONDS,
    actor_user_id: uuid.UUID | None = None,
    actor_email: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Create a browser web call (`Call(direction="web")`), resolve `RuntimeConfig`, and mint a single-use JWT."""
    # 1. Fail closed if signing secret is missing before touching DB
    _signing_secret()

    transport_norm = (transport or "websocket").strip().lower()
    if transport_norm not in {"websocket", "webrtc"}:
        raise WebCallSecurityError(
            status_code=422,
            code="UNSUPPORTED_WEB_TRANSPORT",
            message=f"Unsupported web transport '{transport}'. Supported: websocket, webrtc.",
        )

    # 2. Enforce per-public-key or per-tenant rate limit
    rate_scope = f"pk:{public_key_id}" if public_key_id else f"tenant:{tenant.id}"
    await _enforce_rate_limit(
        scope_key=rate_scope,
        limit_per_minute=rate_limit_per_minute,
    )

    # 3. Validate origin against allowlist if configured
    normalized_origin: str | None = None
    if allowed_origins is not None:
        check = validate_origin_against_allowlist(
            request_origin=origin,
            allowed_origins=allowed_origins,
        )
        if not check.allowed or not check.normalized_origin:
            raise WebCallSecurityError(
                status_code=403,
                code=check.error_code or "FORBIDDEN_ORIGIN",
                message=check.reason,
            )
        normalized_origin = check.normalized_origin
    elif origin and origin.strip():
        try:
            normalized_origin = normalize_origin(origin, allow_wildcard_dev=False)
        except PublicDomainError as exc:
            raise WebCallSecurityError(
                status_code=exc.status_code,
                code=exc.code,
                message=exc.message,
            ) from exc

    # 4. Resolve tenant-scoped agent & environment
    agent_row = await _resolve_agent_uuid(
        session,
        tenant_id=tenant.id,
        agent_id=agent_id,
    )
    env_row = await _resolve_environment(
        session,
        tenant_id=tenant.id,
        environment_id=environment_id,
    )

    # 5. Create Call(direction="web") and resolve RuntimeConfig from PART 2E resolver
    from app.db.models import AgentVersion

    explicit_version_row: AgentVersion | None = None
    if version is not None:
        explicit_version_row = await session.scalar(
            select(AgentVersion).where(
                AgentVersion.tenant_id == tenant.id,
                AgentVersion.agent_id == agent_row.id,
                AgentVersion.version_number == int(version),
            )
        )
        if explicit_version_row is None:
            raise WebCallSecurityError(
                status_code=404,
                code="AGENT_VERSION_NOT_FOUND",
                message=f"Agent version {version} not found for agent {agent_row.id}.",
            )

    call_id = uuid.uuid4()
    call_sid = f"web_{call_id.hex[:24]}"
    jti = secrets.token_urlsafe(24)
    now = _utcnow()

    call = Call(
        id=call_id,
        tenant_id=tenant.id,
        environment_id=env_row.id,
        call_sid=call_sid,
        from_number="web",
        to_number=f"agent:{agent_row.id}",
        status=CallStatus.RINGING,
        direction=WEB_DIRECTION,  # type: ignore[arg-type]
        started_at=now,
        agent_id=agent_row.id,
        agent_version_id=explicit_version_row.id if explicit_version_row is not None else None,
        transfer_context={},
    )
    session.add(call)
    await session.flush()

    runtime_cfg: RuntimeConfig = await resolve_runtime_config(
        session,
        call,
        tenant=tenant,
        agent_id=agent_row.id,
        environment=env_row.id,
    )

    token, expires_at = issue_web_call_jwt(
        call_id=call_id,
        tenant_id=tenant.id,
        environment_id=env_row.id,
        agent_id=agent_row.id,
        agent_version=runtime_cfg.agent_version_number or 1,
        jti=jti,
        origin=normalized_origin,
        transport=transport_norm,
        ttl_seconds=ttl_seconds,
    )

    context_payload: dict[str, Any] = {
        "channel": "web",
        "direction": "web",
        "transport": transport_norm,
        "web_call_token_jti": jti,
        "web_call_token_consumed": False,
        "web_call_token_expires_at": expires_at.isoformat(),
        "allowed_origin": normalized_origin,
        "public_key_id": str(public_key_id) if public_key_id else None,
        "dynamic_variables": dict(dynamic_vars or {}),
        "metadata": dict(metadata or {}),
        "agent_version_number": runtime_cfg.agent_version_number or 1,
    }
    call.transfer_context = context_payload

    receipt = RequestIdempotencyReceipt(
        tenant_id=tenant.id,
        environment_id=env_row.id,
        environment_scope=str(env_row.id),
        operation="web_call.access_token",
        key_digest=jti,
        request_digest=hashlib.sha256(f"{call_id}:{jti}".encode("utf-8")).hexdigest(),
        status="in_progress",
        resource_type="web_call",
        resource_id=str(call_id),
    )
    session.add(receipt)
    await session.flush()

    await record_event(
        session,
        tenant_id=tenant.id,
        actor_user_id=actor_user_id,
        actor_type="human" if actor_user_id else "api_key",
        actor_email=actor_email or "web_call",
        environment_id=env_row.id,
        event_type="web_call.created",
        resource_type="call",
        resource_id=call.id,
        result="success",
        detail={
            "call_id": str(call.id),
            "agent_id": str(agent_row.id),
            "agent_version": runtime_cfg.agent_version_number,
            "transport": transport_norm,
            "origin": normalized_origin,
            "public_key_id": str(public_key_id) if public_key_id else None,
        },
    )
    await session.commit()
    await session.refresh(call)

    ws_url = _build_ws_url(base_url=base_url)
    return {
        "call_id": str(call.id),
        "id": str(call.id),
        "call_sid": call.call_sid,
        "tenant_id": str(tenant.id),
        "environment_id": str(env_row.id),
        "agent_id": str(agent_row.id),
        "agent_version": runtime_cfg.agent_version_number,
        "agent_version_id": str(runtime_cfg.agent_version_id) if runtime_cfg.agent_version_id else None,
        "access_token": token,
        "token": token,
        "transport": transport_norm,
        "url": ws_url,
        "ws_url": ws_url,
        "offer_url": "/telephony/web/offer",
        "sample_rate": 16000,
        "encoding": "pcm_s16le",
        "origin": normalized_origin,
        "status": call.status.value if hasattr(call.status, "value") else str(call.status),
        "direction": "web",
        "expires_at": expires_at.isoformat(),
        "created_at": call.started_at.isoformat() if call.started_at else now.isoformat(),
    }


async def create_public_web_call(
    session: AsyncSession,
    *,
    raw_public_key: str | None,
    request_origin: str | None,
    agent_id: str | None = None,
    version: int | None = None,
    dynamic_vars: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    transport: str = "websocket",
    client_ip: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Authenticate a `PublicWidgetKey` + `Origin`, enforce rate limits, and create a web call."""
    try:
        key_row, normalized_origin = await authenticate_public_key(
            session,
            raw_public_key=raw_public_key,
            request_origin=request_origin,
            required_capability=PublicWidgetCapability.WIDGET_VOICE_START,
            client_ip=client_ip,
        )
    except PublicDomainError as exc:
        raise WebCallSecurityError(
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
        ) from exc

    if agent_id and str(agent_id).strip() != str(key_row.agent_id).strip():
        raise WebCallSecurityError(
            status_code=403,
            code="PUBLIC_KEY_AGENT_MISMATCH",
            message="Public key is not scoped to the requested agent_id.",
        )

    tenant = await session.get(Tenant, key_row.tenant_id)
    if tenant is None:
        raise WebCallSecurityError(
            status_code=404,
            code="TENANT_NOT_FOUND",
            message="Tenant not found for public key.",
        )

    return await create_web_call(
        session,
        tenant=tenant,
        agent_id=key_row.agent_id,
        version=version,
        dynamic_vars=dynamic_vars,
        metadata=metadata,
        environment_id=key_row.environment_id,
        origin=normalized_origin,
        allowed_origins=list(key_row.allowed_origins or []),
        public_key_id=key_row.id,
        rate_limit_per_minute=int(key_row.rate_limit_per_minute or DEFAULT_WEB_CALL_RATE_LIMIT_PER_MINUTE),
        transport=transport,
        ttl_seconds=min(int(key_row.session_ttl_seconds or DEFAULT_WEB_CALL_TTL_SECONDS), 3600),
        actor_email=f"public_key:{key_row.key_prefix}",
        base_url=base_url,
    )


async def consume_web_call_token(
    session: AsyncSession,
    token: str | None,
    *,
    request_origin: str | None = None,
) -> tuple[Call, Tenant, dict[str, Any]]:
    """Verify and atomically consume a single-use web-call `access_token`."""
    claims = verify_web_call_jwt(token, request_origin=request_origin)
    try:
        call_uuid = uuid.UUID(str(claims["call_id"]))
        tenant_uuid = uuid.UUID(str(claims["tenant_id"]))
    except (ValueError, TypeError) as exc:
        raise WebCallSecurityError(
            status_code=401,
            code="INVALID_WEB_CALL_TOKEN",
            message="Malformed call_id or tenant_id in web call token.",
        ) from exc

    jti = str(claims["jti"])
    call = await session.scalar(
        select(Call).where(
            Call.id == call_uuid,
            Call.tenant_id == tenant_uuid,
        )
    )
    if call is None:
        raise WebCallSecurityError(
            status_code=404,
            code="WEB_CALL_NOT_FOUND",
            message="Web call session not found.",
        )

    ctx = dict(call.transfer_context or {})
    if ctx.get("web_call_token_jti") != jti:
        raise WebCallSecurityError(
            status_code=401,
            code="INVALID_WEB_CALL_TOKEN",
            message="Token JTI does not match the web call session.",
        )

    if ctx.get("web_call_token_consumed") is True:
        raise WebCallSecurityError(
            status_code=401,
            code="TOKEN_ALREADY_USED",
            message="Web call access token is single-use and has already been consumed.",
        )

    receipt = await session.scalar(
        select(RequestIdempotencyReceipt).where(
            RequestIdempotencyReceipt.tenant_id == tenant_uuid,
            RequestIdempotencyReceipt.operation == "web_call.access_token",
            RequestIdempotencyReceipt.key_digest == jti,
        )
    )
    if receipt is not None and receipt.status == "succeeded":
        raise WebCallSecurityError(
            status_code=401,
            code="TOKEN_ALREADY_USED",
            message="Web call access token is single-use and has already been consumed.",
        )

    if call.status in (CallStatus.COMPLETED, CallStatus.CANCELLED, CallStatus.FAILED):
        raise WebCallSecurityError(
            status_code=409,
            code="WEB_CALL_ALREADY_ENDED",
            message=f"Web call is already in terminal state '{call.status.value}'.",
        )

    # Mark token as consumed durably
    ctx["web_call_token_consumed"] = True
    ctx["web_call_token_consumed_at"] = _utcnow().isoformat()
    call.transfer_context = ctx
    if receipt is not None:
        receipt.status = "succeeded"

    tenant = await session.get(Tenant, tenant_uuid)
    if tenant is None:
        raise WebCallSecurityError(
            status_code=404,
            code="TENANT_NOT_FOUND",
            message="Tenant not found for web call.",
        )

    await session.commit()
    await session.refresh(call)
    return call, tenant, claims


async def get_web_call(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    call_id: uuid.UUID,
) -> dict[str, Any]:
    """Retrieve a tenant-scoped web call with transcript turns and latency summary."""
    call = await session.scalar(
        select(Call).where(
            Call.id == call_id,
            Call.tenant_id == tenant_id,
        )
    )
    if call is None:
        raise WebCallSecurityError(
            status_code=404,
            code="WEB_CALL_NOT_FOUND",
            message="Web call not found.",
        )

    turns_rows = (
        await session.execute(
            select(Turn)
            .where(Turn.call_id == call.id)
            .order_by(Turn.created_at.asc(), Turn.id.asc())
        )
    ).scalars().all()

    latency_row = await session.scalar(
        select(CallLatencyStat).where(CallLatencyStat.call_id == call.id)
    )

    ctx = dict(call.transfer_context or {})
    direction_str = (
        "web"
        if str(getattr(call.direction, "value", call.direction)).lower() == "web"
        or ctx.get("direction") == "web"
        or call.from_number == "web"
        else str(getattr(call.direction, "value", call.direction)).lower()
    )

    return {
        "call_id": str(call.id),
        "id": str(call.id),
        "call_sid": call.call_sid,
        "tenant_id": str(call.tenant_id),
        "environment_id": str(call.environment_id),
        "agent_id": str(call.agent_id) if call.agent_id else None,
        "agent_version": ctx.get("agent_version_number"),
        "agent_version_id": str(call.agent_version_id) if call.agent_version_id else None,
        "direction": direction_str,
        "transport": ctx.get("transport", "websocket"),
        "status": call.status.value if hasattr(call.status, "value") else str(call.status),
        "duration_seconds": float(call.duration_seconds or 0.0),
        "avg_response_ms": call.avg_response_ms,
        "started_at": call.started_at.isoformat() if call.started_at else None,
        "ended_at": call.ended_at.isoformat() if call.ended_at else None,
        "origin": ctx.get("allowed_origin"),
        "metadata": ctx.get("metadata") or {},
        "dynamic_variables": ctx.get("dynamic_variables") or {},
        "transcript": [
            {
                "id": str(t.id),
                "speaker": t.speaker.value if hasattr(t.speaker, "value") else str(t.speaker),
                "text": t.text,
                "timestamp": t.created_at.isoformat() if t.created_at else None,
            }
            for t in turns_rows
        ],
        "latency": (
            {
                "turns": latency_row.turns,
                "stt_ttfb_p50_ms": latency_row.stt_ttfb_p50_ms,
                "stt_ttfb_p95_ms": latency_row.stt_ttfb_p95_ms,
                "llm_ttfb_p50_ms": latency_row.llm_ttfb_p50_ms,
                "llm_ttfb_p95_ms": latency_row.llm_ttfb_p95_ms,
                "tts_ttfb_p50_ms": latency_row.tts_ttfb_p50_ms,
                "tts_ttfb_p95_ms": latency_row.tts_ttfb_p95_ms,
                "e2e_p50_ms": latency_row.e2e_p50_ms,
                "e2e_p95_ms": latency_row.e2e_p95_ms,
                "e2e_p99_ms": latency_row.e2e_p99_ms,
                "interruptions": latency_row.interruptions,
            }
            if latency_row is not None
            else None
        ),
    }


async def end_web_call(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    call_id: uuid.UUID,
    reason: str = "ended_by_client",
    actor_user_id: uuid.UUID | None = None,
    actor_email: str | None = None,
) -> dict[str, Any]:
    """Terminate an active web call, close its media gateway session, and record audit."""
    call = await session.scalar(
        select(Call).where(
            Call.id == call_id,
            Call.tenant_id == tenant_id,
        )
    )
    if call is None:
        raise WebCallSecurityError(
            status_code=404,
            code="WEB_CALL_NOT_FOUND",
            message="Web call not found.",
        )

    now = _utcnow()
    if call.status not in (CallStatus.COMPLETED, CallStatus.CANCELLED, CallStatus.FAILED):
        call.status = CallStatus.COMPLETED
        call.ended_at = now
        if call.started_at:
            started_aware = (
                call.started_at
                if call.started_at.tzinfo is not None
                else call.started_at.replace(tzinfo=timezone.utc)
            )
            call.duration_seconds = max(0.0, round((now - started_aware).total_seconds(), 3))

    ctx = dict(call.transfer_context or {})
    ctx["ended_reason"] = reason
    ctx["web_call_token_consumed"] = True
    call.transfer_context = ctx

    media_gateway_manager.close_session(call.id)

    await record_event(
        session,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        actor_type="human" if actor_user_id else "api_key",
        actor_email=actor_email or "web_call",
        environment_id=call.environment_id,
        event_type="web_call.ended",
        resource_type="call",
        resource_id=call.id,
        result="success",
        detail={
            "call_id": str(call.id),
            "reason": reason,
            "duration_seconds": call.duration_seconds,
        },
    )
    from app.webhooks.call_event_bridge import publish_call_event

    await publish_call_event(session, call, "call_ended")
    await session.commit()
    return await get_web_call(session, tenant_id=tenant_id, call_id=call.id)
