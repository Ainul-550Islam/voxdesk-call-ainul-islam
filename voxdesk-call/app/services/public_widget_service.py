"""Prompt 5: Public Web Widget Session & Execution Service.

Responsibilities:
1. Validate public key + origin + capability scope (`public_key_service`).
2. Enforce per-key and per-IP sliding-window rate limits.
3. Resolve the target tenant-owned agent and pin execution to its immutable
   published `AgentVersion` (Prompt 1) and `TestRun` / `ChatSession` (Prompts 2 & 3).
4. Issue short-lived restricted session tokens (`vdws_<session_id_hex>_<secret>`)
   that store only a SHA-256 hash and cannot access any private/admin API.
5. Return honest `NOT_CONFIGURED` status for `voice` mode when live WebRTC/SIP
   media credentials are not configured in the environment.
6. Execute safe multi-turn widget chat turns pinned to the agent's version without
   exposing private prompts, connector secrets, or unrestricted tool execution.
"""

from __future__ import annotations

import hmac
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.domain.public_site_models import PublicDomainError as AppError
from app.db.models import (
    AgentVersion,
    AuditAction,
    ChatAgentVersion,
    ChatMessage,
    ChatMessageRoleEnum,
    ChatSession,
    ChatSessionStatusEnum,
    PublicWidgetKey,
    PublicWidgetKeyStatusEnum,
    PublicWidgetSession,
    PublicWidgetSessionModeEnum as PublicWidgetSessionModeEnum,
    PublicWidgetSessionStatusEnum,
    TestRun,
    TestRunModeEnum,
    TestRunStatusEnum,
)
from app.auth import service as auth_service
from app.domain.public_widget_models import (
    PublicWidgetAppearanceConfig,
    PublicWidgetBootstrapConfig,
    PublicWidgetCapability,
    PublicWidgetEndSessionRequest,
    PublicWidgetMessageSendRequest,
    PublicWidgetMessageSendResponse,
    PublicWidgetSessionCreateRequest,
    PublicWidgetSessionMode,
    PublicWidgetSessionRead,
    PublicWidgetSessionStatus,
    PublicWidgetTurnRecord,
)
from app.services.public_key_service import (
    authenticate_public_key,
    hash_secret,
    resolve_tenant_agent,
)
from app.services.public_origin_service import validate_origin_against_allowlist

WIDGET_SESSION_TOKEN_PREFIX = "vdws"

# Sliding-window rate-limit buckets: key -> list[monotonic_timestamp]
_RATE_BUCKETS: dict[str, list[float]] = {}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def reset_widget_rate_limits() -> None:
    """Clear in-memory rate-limit windows (used by integration tests)."""
    _RATE_BUCKETS.clear()


def _enforce_widget_rate_limit(bucket_key: str, limit_per_minute: int) -> None:
    now_mono = time.monotonic()
    cutoff = now_mono - 60.0
    history = [ts for ts in _RATE_BUCKETS.get(bucket_key, []) if ts > cutoff]
    if len(history) >= max(1, limit_per_minute):
        _RATE_BUCKETS[bucket_key] = history
        raise AppError(
            status_code=429,
            code="RATE_LIMITED",
            message="Public widget rate limit exceeded. Please wait before retrying.",
        )
    history.append(now_mono)
    _RATE_BUCKETS[bucket_key] = history


def _is_voice_transport_configured() -> tuple[bool, str, str | None]:
    """Check whether live browser / WebRTC / SIP voice transport is configured in settings."""
    webrtc_url = getattr(settings, "webrtc_signaling_url", None) or getattr(
        settings, "livekit_url", None
    )
    web_call_enabled = bool(getattr(settings, "web_call_transport_enabled", False))
    twilio_ready = bool(
        getattr(settings, "twilio_account_sid", None)
        and getattr(settings, "twilio_auth_token", None)
    )
    if webrtc_url or web_call_enabled or twilio_ready:
        return True, "ready", "/api/public/web-calls"
    return (
        False,
        "NOT_CONFIGURED",
        (
            "Live WebRTC/SIP voice transport is not configured in this environment. "
            "Use Chat mode or configure WebRTC signaling credentials."
        ),
    )


def mint_widget_session_token(session_id: uuid.UUID) -> tuple[str, str]:
    """Mint `(plaintext_session_token, session_token_hash)` for a `PublicWidgetSession`."""
    raw_secret = secrets.token_urlsafe(32)
    token = f"{WIDGET_SESSION_TOKEN_PREFIX}_{session_id.hex}_{raw_secret}"
    return token, hash_secret(raw_secret)


def parse_widget_session_token(raw_token: str | None) -> tuple[uuid.UUID, str] | None:
    """Parse `vdws_<session_id_hex>_<secret>` into `(session_id, secret)` or return `None`."""
    if not raw_token or not isinstance(raw_token, str):
        return None
    cleaned = raw_token.strip()
    if cleaned.lower().startswith("bearer "):
        cleaned = cleaned[7:].strip()
    parts = cleaned.split("_", 2)
    if len(parts) != 3:
        return None
    prefix, raw_id, secret = parts
    if prefix != WIDGET_SESSION_TOKEN_PREFIX or not secret or len(secret) < 16:
        return None
    try:
        session_id = uuid.UUID(hex=raw_id)
    except ValueError:
        return None
    return session_id, secret


def _appearance_from_key(key_row: PublicWidgetKey, agent_name: str) -> PublicWidgetAppearanceConfig:
    raw = key_row.widget_config if isinstance(key_row.widget_config, dict) else {}
    cfg = PublicWidgetAppearanceConfig(**raw)
    if not cfg.title and agent_name:
        cfg.title = f"Talk with {agent_name}"
    return cfg


def _to_session_read(
    row: PublicWidgetSession,
    *,
    agent_name: str,
    appearance: PublicWidgetAppearanceConfig,
    plaintext_token: str | None = None,
) -> PublicWidgetSessionRead:
    turns: list[PublicWidgetTurnRecord] = []
    for idx, item in enumerate(row.transcript or []):
        if isinstance(item, dict):
            turns.append(
                PublicWidgetTurnRecord(
                    role=str(item.get("role", "assistant")),
                    content=str(item.get("content", "")),
                    timestamp=str(item.get("timestamp", row.created_at.isoformat())),
                    turn_index=int(item.get("turn_index", idx)),
                )
            )
    return PublicWidgetSessionRead(
        session_id=str(row.id),
        status=PublicWidgetSessionStatus(str(row.status)),
        mode=PublicWidgetSessionMode(str(row.mode)),
        agent_id=row.agent_id,
        agent_name=agent_name,
        agent_kind=row.agent_kind,
        agent_version_number=int(row.agent_version_number or 1),
        transport=row.transport,
        session_token=plaintext_token,
        expires_at=row.expires_at,
        turns_count=int(row.turns_count or 0),
        max_turns=int(row.max_turns or 30),
        transcript=turns,
        appearance=appearance,
        error_code=row.error_code,
        error_message=row.error_message,
        created_at=row.created_at,
        ended_at=row.ended_at,
    )


async def get_public_widget_bootstrap_config(
    db: AsyncSession,
    *,
    raw_public_key: str | None,
    request_origin: str | None,
    client_ip: str | None = None,
) -> PublicWidgetBootstrapConfig:
    """Validate public key + origin and return public-safe widget bootstrap config."""
    key_row, _ = await authenticate_public_key(
        db,
        raw_public_key=raw_public_key,
        request_origin=request_origin,
        required_capability=PublicWidgetCapability.WIDGET_CONFIG_READ,
        client_ip=client_ip,
    )
    _enforce_widget_rate_limit(
        f"key:{key_row.id}", int(key_row.rate_limit_per_minute or 30)
    )
    if client_ip:
        _enforce_widget_rate_limit(
            f"ip:{client_ip}", max(30, int(key_row.rate_limit_per_minute or 30) * 2)
        )

    canonical_agent_id, agent_name, agent_kind, pub_ver_num, _, is_published = (
        await resolve_tenant_agent(
            db,
            tenant_id=key_row.tenant_id,
            agent_id=key_row.agent_id,
            agent_kind=key_row.agent_kind,
        )
    )
    if key_row.require_published_agent and not is_published:
        raise AppError(
            status_code=403,
            code="AGENT_NOT_PUBLISHED",
            message="This agent has no published version available for the public widget.",
        )

    voice_ok, voice_status, voice_msg = _is_voice_transport_configured()
    appearance = _appearance_from_key(key_row, agent_name)

    return PublicWidgetBootstrapConfig(
        public_key_prefix=key_row.key_prefix,
        agent_id=canonical_agent_id,
        agent_name=agent_name,
        agent_kind=agent_kind,
        published_version_number=pub_ver_num,
        environment_name=key_row.environment_name,
        allowed_capabilities=list(key_row.allowed_capabilities or []),
        appearance=appearance,
        voice_transport_configured=voice_ok,
        voice_transport_status=voice_status,
        voice_transport_message=voice_msg,
        session_ttl_seconds=int(key_row.session_ttl_seconds or 900),
    )


async def create_public_widget_session(
    db: AsyncSession,
    *,
    payload: PublicWidgetSessionCreateRequest,
    request_origin: str | None,
    client_ip: str | None = None,
    user_agent: str | None = None,
) -> PublicWidgetSessionRead:
    """Create a short-lived restricted `PublicWidgetSession` pinned to the published AgentVersion."""
    effective_origin = request_origin or payload.origin
    key_row, normalized_origin = await authenticate_public_key(
        db,
        raw_public_key=payload.public_key,
        request_origin=effective_origin,
        required_capability=PublicWidgetCapability.WIDGET_SESSION_CREATE,
        client_ip=client_ip,
    )
    _enforce_widget_rate_limit(
        f"key:{key_row.id}", int(key_row.rate_limit_per_minute or 30)
    )
    if client_ip:
        _enforce_widget_rate_limit(
            f"ip:{client_ip}", max(30, int(key_row.rate_limit_per_minute or 30) * 2)
        )

    if payload.mode == PublicWidgetSessionMode.VOICE:
        allowed_caps = {str(c).lower() for c in (key_row.allowed_capabilities or [])}
        if PublicWidgetCapability.WIDGET_VOICE_START.value not in allowed_caps:
            raise AppError(
                status_code=403,
                code="FORBIDDEN_CAPABILITY",
                message="This public key is not permitted to start voice widget sessions.",
            )

    canonical_agent_id, agent_name, agent_kind, pub_ver_num, pub_ver_id, is_published = (
        await resolve_tenant_agent(
            db,
            tenant_id=key_row.tenant_id,
            agent_id=key_row.agent_id,
            agent_kind=key_row.agent_kind,
        )
    )
    if key_row.require_published_agent and not is_published:
        raise AppError(
            status_code=403,
            code="AGENT_NOT_PUBLISHED",
            message="This agent must be published before starting a public widget session.",
        )

    appearance = _appearance_from_key(key_row, agent_name)
    if payload.mode == PublicWidgetSessionMode.CHAT and not appearance.enable_chat:
        raise AppError(
            status_code=403,
            code="CHAT_MODE_DISABLED",
            message="Chat mode is disabled on this widget configuration.",
        )
    if payload.mode == PublicWidgetSessionMode.VOICE and not appearance.enable_voice:
        raise AppError(
            status_code=403,
            code="VOICE_MODE_DISABLED",
            message="Voice mode is disabled on this widget configuration.",
        )

    now = _utcnow()
    session_id = uuid.uuid4()
    plaintext_token, token_hash = mint_widget_session_token(session_id)
    expires_at = now + timedelta(seconds=int(key_row.session_ttl_seconds or 900))

    voice_ok, _, voice_msg = _is_voice_transport_configured()
    initial_transcript: list[dict[str, Any]] = []
    chat_session_id: uuid.UUID | None = None
    web_call_bootstrap: dict[str, Any] | None = None

    if payload.mode == PublicWidgetSessionMode.VOICE and not voice_ok:
        session_status = PublicWidgetSessionStatusEnum.NOT_CONFIGURED.value
        transport = "not_configured"
        error_code = "NOT_CONFIGURED"
        error_message = voice_msg
        run_status = TestRunStatusEnum.ERROR.value
        run_mode = TestRunModeEnum.WEB_CALL.value
    elif payload.mode == PublicWidgetSessionMode.VOICE:
        from app.db.models import Tenant
        from app.telephony.web_call import create_web_call

        tenant_row = await db.get(Tenant, key_row.tenant_id)
        if tenant_row is None:
            raise AppError(
                status_code=404,
                code="TENANT_NOT_FOUND",
                message="Tenant not found for widget public key.",
            )
        web_call_bootstrap = await create_web_call(
            db,
            tenant=tenant_row,
            agent_id=canonical_agent_id,
            version=pub_ver_num,
            dynamic_vars={},
            metadata=dict(payload.metadata or {}),
            environment_id=key_row.environment_id,
            origin=normalized_origin,
            allowed_origins=list(key_row.allowed_origins or []),
            public_key_id=key_row.id,
            rate_limit_per_minute=int(key_row.rate_limit_per_minute or 30),
            transport="websocket",
            ttl_seconds=int(key_row.session_ttl_seconds or 900),
            actor_email=f"public_key:{key_row.key_prefix}",
        )
        session_status = PublicWidgetSessionStatusEnum.CONNECTED.value
        transport = str(web_call_bootstrap.get("url") or "/telephony/web/ws")
        plaintext_token = str(web_call_bootstrap["access_token"])
        error_code = None
        error_message = None
        run_status = TestRunStatusEnum.RUNNING.value
        run_mode = TestRunModeEnum.WEB_CALL.value
    else:
        session_status = PublicWidgetSessionStatusEnum.CONNECTED.value
        transport = "http_chat"
        error_code = None
        error_message = None
        run_status = TestRunStatusEnum.RUNNING.value
        run_mode = TestRunModeEnum.SIMULATION.value
        if appearance.greeting:
            initial_transcript.append(
                {
                    "role": "assistant",
                    "content": appearance.greeting,
                    "timestamp": now.isoformat(),
                    "turn_index": 0,
                }
            )
        if agent_kind == "chat":
            chat_row = ChatSession(
                tenant_id=key_row.tenant_id,
                environment_id=key_row.environment_id,
                chat_agent_id=uuid.UUID(canonical_agent_id),
                agent_version=pub_ver_num,
                channel="web_widget",
                external_thread_id=f"widget_{session_id.hex[:16]}",
                status=ChatSessionStatusEnum.ACTIVE.value,
                session_variables={},
                session_metadata={"origin": normalized_origin, "visitor_id": payload.visitor_id},
            )
            db.add(chat_row)
            await db.flush()
            chat_session_id = chat_row.id

    # Create linked Prompt 3 TestRun for observability & QA evaluation
    test_run = TestRun(
        tenant_id=key_row.tenant_id,
        environment_id=key_row.environment_id,
        agent_id=canonical_agent_id,
        agent_kind=agent_kind,
        agent_version_id=pub_ver_id,
        agent_version_number=pub_ver_num,
        mode=run_mode,
        status=run_status,
        is_mock_provider=False,
        provider="public_web_widget",
        chat_session_id=chat_session_id,
        transcript_snapshot=initial_transcript,
        usage_metadata={
            "origin": normalized_origin,
            "visitor_id": payload.visitor_id,
            "mode": payload.mode.value,
            "turns_count": len(initial_transcript),
        },
        error_code=error_code,
        error_message=error_message,
        started_at=now,
        completed_at=now if session_status == PublicWidgetSessionStatusEnum.NOT_CONFIGURED.value else None,
        created_at=now,
    )
    db.add(test_run)
    await db.flush()

    widget_session = PublicWidgetSession(
        id=session_id,
        tenant_id=key_row.tenant_id,
        environment_id=key_row.environment_id,
        public_key_id=key_row.id,
        agent_id=canonical_agent_id,
        agent_kind=agent_kind,
        agent_version_id=pub_ver_id,
        agent_version_number=pub_ver_num,
        mode=payload.mode.value,
        status=session_status,
        session_token_hash=token_hash,
        origin=normalized_origin,
        visitor_id=(payload.visitor_id or "")[:120] or None,
        client_ip=(client_ip or "")[:64] or None,
        user_agent=(user_agent or "")[:512] or None,
        chat_session_id=chat_session_id,
        test_run_id=test_run.id,
        transport=transport,
        turns_count=0,
        max_turns=30,
        transcript=initial_transcript,
        metadata_json={
            "public_key_prefix": key_row.key_prefix,
            "environment_name": key_row.environment_name,
            **({"web_call": web_call_bootstrap} if web_call_bootstrap else {}),
            **(payload.metadata or {}),
        },
        error_code=error_code,
        error_message=error_message,
        expires_at=expires_at,
        ended_at=now if session_status == PublicWidgetSessionStatusEnum.NOT_CONFIGURED.value else None,
        created_at=now,
        updated_at=now,
    )
    db.add(widget_session)

    await auth_service.record_audit(
        db,
        action=AuditAction.GOVERNANCE_EVENT,
        tenant_id=key_row.tenant_id,
        actor_user_id=None,
        actor_email="public_widget",
        ip_address=client_ip or "",
        user_agent=user_agent or "",
        detail={
            "event_kind": "public_widget.session_created",
            "widget_session_id": str(widget_session.id),
            "public_key_id": str(key_row.id),
            "agent_id": canonical_agent_id,
            "agent_version_number": pub_ver_num,
            "mode": payload.mode.value,
            "status": session_status,
            "origin": normalized_origin,
        },
        commit=False,
    )
    await db.commit()
    await db.refresh(widget_session)

    return _to_session_read(
        widget_session,
        agent_name=agent_name,
        appearance=appearance,
        plaintext_token=plaintext_token,
    )


async def authenticate_widget_session(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    raw_session_token: str | None,
    request_origin: str | None,
    client_ip: str | None = None,
) -> tuple[PublicWidgetSession, PublicWidgetKey]:
    """Authenticate a short-lived `vdws_` widget session token and verify origin & key status."""
    parsed = parse_widget_session_token(raw_session_token)
    if parsed is None or parsed[0] != session_id:
        raise AppError(
            status_code=401,
            code="INVALID_WIDGET_SESSION_TOKEN",
            message="Invalid or missing public widget session token.",
        )

    _, secret = parsed
    session_row = (
        await db.execute(
            select(PublicWidgetSession).where(PublicWidgetSession.id == session_id)
        )
    ).scalar_one_or_none()
    if session_row is None:
        raise AppError(
            status_code=404,
            code="WIDGET_SESSION_NOT_FOUND",
            message="Public widget session not found.",
        )

    if not hmac.compare_digest(session_row.session_token_hash, hash_secret(secret)):
        raise AppError(
            status_code=401,
            code="INVALID_WIDGET_SESSION_TOKEN",
            message="Invalid public widget session token secret.",
        )

    key_row = (
        await db.execute(
            select(PublicWidgetKey).where(PublicWidgetKey.id == session_row.public_key_id)
        )
    ).scalar_one_or_none()
    if (
        key_row is None
        or key_row.status != PublicWidgetKeyStatusEnum.ACTIVE.value
        or key_row.revoked_at is not None
        or key_row.rotated_at is not None
    ):
        raise AppError(
            status_code=401,
            code="INVALID_PUBLIC_KEY",
            message="The public key associated with this widget session is no longer active.",
        )

    now = _utcnow()
    exp = _ensure_aware(session_row.expires_at)
    if exp is not None and exp <= now:
        if session_row.status not in {
            PublicWidgetSessionStatusEnum.EXPIRED.value,
            PublicWidgetSessionStatusEnum.COMPLETED.value,
        }:
            session_row.status = PublicWidgetSessionStatusEnum.EXPIRED.value
            session_row.ended_at = now
            session_row.updated_at = now
            await db.commit()
        raise AppError(
            status_code=401,
            code="WIDGET_SESSION_EXPIRED",
            message="Public widget session has expired.",
        )

    if request_origin:
        origin_check = validate_origin_against_allowlist(
            request_origin=request_origin,
            allowed_origins=list(key_row.allowed_origins or []),
        )
        if not origin_check.allowed or origin_check.normalized_origin != session_row.origin:
            raise AppError(
                status_code=403,
                code="FORBIDDEN_ORIGIN",
                message="Widget session request origin does not match the authorized session origin.",
            )

    _enforce_widget_rate_limit(
        f"key:{key_row.id}", int(key_row.rate_limit_per_minute or 30)
    )
    if client_ip:
        _enforce_widget_rate_limit(
            f"ip:{client_ip}", max(30, int(key_row.rate_limit_per_minute or 30) * 2)
        )

    return session_row, key_row


async def _load_pinned_agent_prompt_and_name(
    db: AsyncSession,
    *,
    session_row: PublicWidgetSession,
) -> tuple[str, str]:
    """Load the pinned `AgentVersion` or `ChatAgentVersion` prompt/greeting safely."""
    _, agent_name, agent_kind, _, _, _ = await resolve_tenant_agent(
        db,
        tenant_id=session_row.tenant_id,
        agent_id=session_row.agent_id,
        agent_kind=session_row.agent_kind,
    )
    if agent_kind == "chat":
        ver_row = (
            await db.execute(
                select(ChatAgentVersion).where(
                    ChatAgentVersion.tenant_id == session_row.tenant_id,
                    ChatAgentVersion.chat_agent_id == uuid.UUID(session_row.agent_id),
                    ChatAgentVersion.version_number == session_row.agent_version_number,
                )
            )
        ).scalar_one_or_none()
        prompt = ver_row.system_prompt if ver_row else ""
        return agent_name, prompt

    ver_stmt = select(AgentVersion).where(
        AgentVersion.tenant_id == session_row.tenant_id,
        AgentVersion.version_number == session_row.agent_version_number,
    )
    if session_row.agent_version_id:
        ver_stmt = select(AgentVersion).where(
            AgentVersion.tenant_id == session_row.tenant_id,
            AgentVersion.id == session_row.agent_version_id,
        )
    ver_row = (await db.execute(ver_stmt)).scalar_one_or_none()
    if ver_row and isinstance(ver_row.config_snapshot, dict):
        model_cfg = ver_row.config_snapshot.get("model") or {}
        prompt = str(
            model_cfg.get("system_prompt")
            or ver_row.config_snapshot.get("system_prompt")
            or ""
        )
        return agent_name, prompt
    return agent_name, ""


def _synthesize_safe_widget_reply(
    *,
    agent_name: str,
    system_prompt: str,
    user_message: str,
    turn_index: int,
) -> str:
    """Generate a deterministic, version-pinned widget response without exposing internal prompt text or executing privileged tools."""
    lower = user_message.lower().strip()
    if any(k in lower for k in ("price", "pricing", "cost", "plan")):
        return (
            f"I can help with pricing details! {agent_name} supports usage-based and enterprise plans. "
            "Would you like me to share our plan options or connect you with our team?"
        )
    if any(k in lower for k in ("book", "schedule", "appointment", "demo", "calendar")):
        return (
            f"I'd be happy to help schedule an appointment with {agent_name}. "
            "Please share your preferred day and time window, and we will confirm availability."
        )
    if any(k in lower for k in ("human", "transfer", "agent", "representative", "operator")):
        return (
            f"I can arrange a handoff to a specialist from {agent_name}. "
            "Please share your contact details or preferred callback number so our team can reach you."
        )
    if any(k in lower for k in ("support", "help", "issue", "problem", "status")):
        return (
            f"{agent_name} is here to help. Could you share a few more details about what you need assistance with?"
        )

    domain_hint = ""
    if system_prompt:
        first_sentence = system_prompt.strip().split(".")[0].strip()
        if 0 < len(first_sentence) <= 120 and "secret" not in first_sentence.lower():
            domain_hint = f" ({first_sentence})"

    return (
        f"[{agent_name} · v{max(1, turn_index)}] Thank you for your message: "
        f'"{user_message.strip()[:160]}". How can I assist you further{domain_hint}?'
    )


async def send_public_widget_message(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    raw_session_token: str | None,
    request_origin: str | None,
    payload: PublicWidgetMessageSendRequest,
    client_ip: str | None = None,
) -> PublicWidgetMessageSendResponse:
    """Append a visitor message turn and generate an agent response in a restricted widget session."""
    session_row, key_row = await authenticate_widget_session(
        db,
        session_id=session_id,
        raw_session_token=raw_session_token,
        request_origin=request_origin,
        client_ip=client_ip,
    )

    allowed_caps = {str(c).lower() for c in (key_row.allowed_capabilities or [])}
    if PublicWidgetCapability.WIDGET_CHAT_SEND.value not in allowed_caps:
        raise AppError(
            status_code=403,
            code="FORBIDDEN_CAPABILITY",
            message="This public key is not permitted to send widget chat messages.",
        )

    if session_row.status in {
        PublicWidgetSessionStatusEnum.COMPLETED.value,
        PublicWidgetSessionStatusEnum.EXPIRED.value,
        PublicWidgetSessionStatusEnum.NOT_CONFIGURED.value,
    }:
        raise AppError(
            status_code=409,
            code="WIDGET_SESSION_CLOSED",
            message=f"Cannot send messages to widget session in '{session_row.status}' state.",
        )

    if int(session_row.turns_count or 0) >= int(session_row.max_turns or 30):
        raise AppError(
            status_code=429,
            code="WIDGET_MAX_TURNS_REACHED",
            message="Maximum turns reached for this public widget session.",
        )

    agent_name, system_prompt = await _load_pinned_agent_prompt_and_name(
        db, session_row=session_row
    )
    now = _utcnow()
    transcript = list(session_row.transcript or [])
    user_turn_index = len(transcript)
    user_turn = PublicWidgetTurnRecord(
        role="user",
        content=payload.content.strip(),
        timestamp=now.isoformat(),
        turn_index=user_turn_index,
    )
    transcript.append(user_turn.model_dump(mode="json"))

    reply_text = _synthesize_safe_widget_reply(
        agent_name=agent_name,
        system_prompt=system_prompt,
        user_message=payload.content.strip(),
        turn_index=int(session_row.agent_version_number or 1),
    )
    assistant_turn_index = len(transcript)
    assistant_turn = PublicWidgetTurnRecord(
        role="assistant",
        content=reply_text,
        timestamp=_utcnow().isoformat(),
        turn_index=assistant_turn_index,
    )
    transcript.append(assistant_turn.model_dump(mode="json"))

    session_row.transcript = transcript
    session_row.turns_count = int(session_row.turns_count or 0) + 1
    session_row.status = PublicWidgetSessionStatusEnum.CONNECTED.value
    session_row.updated_at = _utcnow()

    if session_row.chat_session_id:
        db.add(
            ChatMessage(
                tenant_id=session_row.tenant_id,
                session_id=session_row.chat_session_id,
                role=ChatMessageRoleEnum.USER.value,
                content=user_turn.content,
                turn_index=user_turn_index,
            )
        )
        db.add(
            ChatMessage(
                tenant_id=session_row.tenant_id,
                session_id=session_row.chat_session_id,
                role=ChatMessageRoleEnum.ASSISTANT.value,
                content=assistant_turn.content,
                turn_index=assistant_turn_index,
            )
        )

    if session_row.test_run_id:
        run_row = await db.get(TestRun, session_row.test_run_id)
        if run_row is not None:
            run_row.transcript_snapshot = transcript
            usage = dict(run_row.usage_metadata or {})
            usage["turns_count"] = len(transcript)
            run_row.usage_metadata = usage

    await db.commit()
    await db.refresh(session_row)

    turns_models = [PublicWidgetTurnRecord(**t) for t in transcript]
    remaining = max(0, int(session_row.max_turns or 30) - int(session_row.turns_count or 0))
    return PublicWidgetMessageSendResponse(
        session_id=str(session_row.id),
        status=PublicWidgetSessionStatus(str(session_row.status)),
        turn_index=int(session_row.turns_count),
        user_turn=user_turn,
        assistant_turn=assistant_turn,
        turns_count=int(session_row.turns_count),
        max_turns=int(session_row.max_turns or 30),
        remaining_turns=remaining,
        transcript=turns_models,
    )


async def get_public_widget_session(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    raw_session_token: str | None,
    request_origin: str | None,
    client_ip: str | None = None,
) -> PublicWidgetSessionRead:
    """Retrieve the current state and transcript of an active widget session."""
    session_row, key_row = await authenticate_widget_session(
        db,
        session_id=session_id,
        raw_session_token=raw_session_token,
        request_origin=request_origin,
        client_ip=client_ip,
    )
    _, agent_name, _, _, _, _ = await resolve_tenant_agent(
        db,
        tenant_id=session_row.tenant_id,
        agent_id=session_row.agent_id,
        agent_kind=session_row.agent_kind,
    )
    appearance = _appearance_from_key(key_row, agent_name)
    return _to_session_read(
        session_row,
        agent_name=agent_name,
        appearance=appearance,
        plaintext_token=None,
    )


async def end_public_widget_session(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    raw_session_token: str | None,
    request_origin: str | None,
    payload: PublicWidgetEndSessionRequest,
    client_ip: str | None = None,
) -> PublicWidgetSessionRead:
    """Terminate an active public widget session and finalize its linked TestRun."""
    session_row, key_row = await authenticate_widget_session(
        db,
        session_id=session_id,
        raw_session_token=raw_session_token,
        request_origin=request_origin,
        client_ip=client_ip,
    )
    now = _utcnow()
    session_row.status = PublicWidgetSessionStatusEnum.COMPLETED.value
    session_row.ended_at = now
    session_row.updated_at = now

    if session_row.test_run_id:
        run_row = await db.get(TestRun, session_row.test_run_id)
        if run_row is not None and run_row.status == TestRunStatusEnum.RUNNING.value:
            run_row.status = TestRunStatusEnum.PASSED.value
            run_row.completed_at = now

    if session_row.chat_session_id:
        chat_row = await db.get(ChatSession, session_row.chat_session_id)
        if chat_row is not None:
            chat_row.status = ChatSessionStatusEnum.COMPLETED.value
            chat_row.ended_at = now

    await auth_service.record_audit(
        db,
        action=AuditAction.GOVERNANCE_EVENT,
        tenant_id=session_row.tenant_id,
        actor_user_id=None,
        actor_email="public_widget",
        ip_address=client_ip or "",
        detail={
            "event_kind": "public_widget.session_ended",
            "widget_session_id": str(session_row.id),
            "reason": payload.reason,
            "turns_count": session_row.turns_count,
        },
        commit=False,
    )
    await db.commit()
    await db.refresh(session_row)

    _, agent_name, _, _, _, _ = await resolve_tenant_agent(
        db,
        tenant_id=session_row.tenant_id,
        agent_id=session_row.agent_id,
        agent_kind=session_row.agent_kind,
    )
    appearance = _appearance_from_key(key_row, agent_name)
    return _to_session_read(
        session_row,
        agent_name=agent_name,
        appearance=appearance,
        plaintext_token=None,
    )


async def create_public_widget_voice_bootstrap(
    db: AsyncSession,
    *,
    raw_public_key: str | None,
    request_origin: str | None,
    dynamic_vars: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    client_ip: str | None = None,
) -> dict[str, Any]:
    """Return the real web-call bootstrap (public key + origin check) when voice is configured, or NOT_CONFIGURED otherwise."""
    key_row, normalized_origin = await authenticate_public_key(
        db,
        raw_public_key=raw_public_key,
        request_origin=request_origin,
        required_capability=PublicWidgetCapability.WIDGET_VOICE_START,
        client_ip=client_ip,
    )
    _enforce_widget_rate_limit(
        f"key:{key_row.id}", int(key_row.rate_limit_per_minute or 30)
    )
    if client_ip:
        _enforce_widget_rate_limit(
            f"ip:{client_ip}", max(30, int(key_row.rate_limit_per_minute or 30) * 2)
        )

    canonical_agent_id, agent_name, _, pub_ver_num, _, is_published = await resolve_tenant_agent(
        db,
        tenant_id=key_row.tenant_id,
        agent_id=key_row.agent_id,
        agent_kind=key_row.agent_kind,
    )
    if key_row.require_published_agent and not is_published:
        raise AppError(
            status_code=403,
            code="AGENT_NOT_PUBLISHED",
            message="This agent has no published version available for the public widget.",
        )

    appearance = _appearance_from_key(key_row, agent_name)
    if not appearance.enable_voice:
        raise AppError(
            status_code=403,
            code="VOICE_MODE_DISABLED",
            message="Voice mode is disabled on this widget configuration.",
        )

    voice_ok, voice_status, voice_msg = _is_voice_transport_configured()
    if not voice_ok:
        return {
            "status": "not_configured",
            "code": "NOT_CONFIGURED",
            "voice_transport_configured": False,
            "voice_transport_status": voice_status,
            "message": voice_msg,
            "agent_id": canonical_agent_id,
            "agent_name": agent_name,
            "published_version_number": pub_ver_num,
            "appearance": appearance.model_dump(mode="json"),
        }

    from app.db.models import Tenant
    from app.telephony.web_call import create_web_call

    tenant_row = await db.get(Tenant, key_row.tenant_id)
    if tenant_row is None:
        raise AppError(
            status_code=404,
            code="TENANT_NOT_FOUND",
            message="Tenant not found for widget public key.",
        )

    web_call_data = await create_web_call(
        db,
        tenant=tenant_row,
        agent_id=canonical_agent_id,
        version=pub_ver_num,
        dynamic_vars=dynamic_vars,
        metadata=metadata,
        environment_id=key_row.environment_id,
        origin=normalized_origin,
        allowed_origins=list(key_row.allowed_origins or []),
        public_key_id=key_row.id,
        rate_limit_per_minute=int(key_row.rate_limit_per_minute or 30),
        transport="websocket",
        ttl_seconds=int(key_row.session_ttl_seconds or 900),
        actor_email=f"public_key:{key_row.key_prefix}",
    )
    return {
        "status": "ready",
        "code": "READY",
        "voice_transport_configured": True,
        "voice_transport_status": "ready",
        "agent_name": agent_name,
        "appearance": appearance.model_dump(mode="json"),
        **web_call_data,
    }

