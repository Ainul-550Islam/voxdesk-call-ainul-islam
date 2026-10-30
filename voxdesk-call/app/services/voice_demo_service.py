"""Durable public voice-demo session service backed by ``voice_demo_sessions`` (Capability 10).

Persists every public voice-demo session in PostgreSQL / SQLite so sessions survive
multi-worker restarts and never rely on process-local memory:
- lives in ``voice_demo_sessions`` (PostgreSQL / SQLite),
- enforces per-IP rate limiting (HTTP 429),
- enforces HMAC-SHA256 session tokens with constant-time verification (HTTP 401),
- enforces TTL expiry (HTTP 410),
- fails closed (HTTP 503) when no public demo agent is configured,
- is cleaned up by the background scheduler reaper.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.enterprise_models import VoiceDemoSession
from app.db.models import Agent, AgentVersion


class VoiceDemoError(Exception):
    """Base error for voice-demo lifecycle operations."""


class VoiceDemoNotConfiguredError(VoiceDemoError):
    """No public demo agent is configured for this deployment."""


class VoiceDemoRateLimitedError(VoiceDemoError):
    """Caller IP exceeded the per-minute session creation budget."""


class VoiceDemoUnauthorizedError(VoiceDemoError):
    """Session token is missing, malformed, or failed HMAC verification."""


class VoiceDemoExpiredError(VoiceDemoError):
    """Session has passed its ``expires_at`` deadline or reached max turns."""


class VoiceDemoNotFoundError(VoiceDemoError):
    """Session ID is unknown."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _signing_secret() -> bytes:
    secret_str = (settings.jwt_secret or settings.secret_key).strip()
    if not secret_str:
        raise VoiceDemoError(
            "JWT_SECRET or SECRET_KEY must be configured to sign demo session tokens."
        )
    return hashlib.sha256(b"voice-demo-v1:" + secret_str.encode("utf-8")).digest()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def mint_demo_session_token(session_id: uuid.UUID, expires_at: datetime) -> str:
    payload = {
        "sid": str(session_id),
        "exp": int(expires_at.timestamp()),
    }
    body = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).decode("ascii").rstrip("=")
    sig = hmac.new(_signing_secret(), body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"vds1.{body}.{sig}"


def verify_demo_session_token(
    token: str | None,
    *,
    expected_session_id: uuid.UUID,
) -> dict[str, Any]:
    clean = (token or "").strip()
    if clean.lower().startswith("bearer "):
        clean = clean[7:].strip()
    if not clean or not clean.startswith("vds1."):
        raise VoiceDemoUnauthorizedError("Missing or invalid voice-demo session token.")
    parts = clean.split(".")
    if len(parts) != 3:
        raise VoiceDemoUnauthorizedError("Malformed voice-demo session token.")
    _, body, sig = parts
    expected_sig = hmac.new(_signing_secret(), body.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected_sig):
        raise VoiceDemoUnauthorizedError("Voice-demo token HMAC verification failed.")
    pad = "=" * (-len(body) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode((body + pad).encode("ascii")).decode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise VoiceDemoUnauthorizedError("Corrupt voice-demo token payload.") from exc
    if claims.get("sid") != str(expected_session_id):
        raise VoiceDemoUnauthorizedError("Voice-demo token does not match session_id.")
    return {"claims": claims, "raw_token": clean}


async def _resolve_demo_agent(
    session: AsyncSession,
    *,
    requested_tenant_id: str | None = None,
) -> tuple[Agent, AgentVersion | None, dict[str, Any]]:
    configured_id = (settings.voice_demo_agent_id or "").strip()
    agent: Agent | None = None

    if configured_id:
        try:
            agent_uuid = uuid.UUID(configured_id)
        except ValueError as exc:
            raise VoiceDemoNotConfiguredError(
                "Configured VOICE_DEMO_AGENT_ID is not a valid UUID."
            ) from exc
        agent = await session.scalar(
            select(Agent).where(
                Agent.id == agent_uuid,
                Agent.deleted_at.is_(None),
            )
        )
    else:
        stmt = select(Agent).where(Agent.deleted_at.is_(None))
        if requested_tenant_id:
            try:
                tid = uuid.UUID(str(requested_tenant_id))
                stmt = stmt.where(Agent.tenant_id == tid)
            except ValueError:
                raise VoiceDemoNotConfiguredError("Invalid tenant_id for voice demo.") from None
        candidates = list((await session.scalars(stmt.order_by(Agent.created_at.asc()))).all())
        for cand in candidates:
            meta = cand.meta if isinstance(cand.meta, dict) else {}
            if meta.get("public_voice_demo") is True:
                agent = cand
                break

    if agent is None:
        raise VoiceDemoNotConfiguredError(
            "No provider-backed public voice demo agent is configured; no session was created."
        )

    version: AgentVersion | None = None
    if agent.published_version_id is not None:
        version = await session.get(AgentVersion, agent.published_version_id)
    if version is None:
        version = await session.scalar(
            select(AgentVersion)
            .where(AgentVersion.agent_id == agent.id)
            .order_by(AgentVersion.version_number.desc())
        )
    snapshot = dict(
        version.config_snapshot
        if version and version.config_snapshot
        else (agent.current_draft_config or {})
    )
    return agent, version, snapshot


async def enforce_ip_rate_limit(
    session: AsyncSession,
    *,
    client_ip: str,
    now: datetime | None = None,
    limit_per_minute: int | None = None,
) -> None:
    moment = now or _now()
    budget = int(
        limit_per_minute
        if limit_per_minute is not None
        else settings.voice_demo_rate_limit_per_minute
    )
    if budget <= 0:
        raise VoiceDemoRateLimitedError("Too many demo sessions. Please wait a moment.")
    window_start = moment - timedelta(seconds=60)
    recent_count = await session.scalar(
        select(func.count(VoiceDemoSession.id)).where(
            VoiceDemoSession.client_ip == (client_ip or "unknown"),
            VoiceDemoSession.created_at >= window_start,
        )
    )
    if int(recent_count or 0) >= budget:
        raise VoiceDemoRateLimitedError("Too many demo sessions. Please wait a moment.")


def _voice_state_for_status(row: VoiceDemoSession) -> str:
    meta = row.meta if isinstance(row.meta, dict) else {}
    explicit = str(meta.get("voice_state") or "").upper()
    if explicit in {"IDLE", "LISTENING", "PROCESSING", "SPEAKING", "INTERRUPTED", "ERROR", "NOT_CONFIGURED"}:
        return explicit
    if row.status in ("ended", "expired"):
        return "IDLE"
    return "IDLE"


async def create_demo_session(
    session: AsyncSession,
    *,
    client_ip: str,
    requested_tenant_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    ttl_seconds: int | None = None,
) -> dict[str, Any]:
    now = _now()
    await enforce_ip_rate_limit(session, client_ip=client_ip, now=now)
    agent, version, snapshot = await _resolve_demo_agent(
        session, requested_tenant_id=requested_tenant_id
    )

    ttl = max(5, int(ttl_seconds if ttl_seconds is not None else settings.voice_demo_session_ttl_seconds))
    expires_at = now + timedelta(seconds=ttl)
    session_id = uuid.uuid4()
    session_token = mint_demo_session_token(session_id, expires_at)

    greeting = str(
        snapshot.get("greeting")
        or snapshot.get("first_message")
        or f"Hi! I am {agent.name}. Ask me anything to try the live voice demo."
    ).strip()
    sys_prompt = str(snapshot.get("system_prompt") or snapshot.get("prompt") or "").strip()
    language = str(snapshot.get("language") or settings.default_language or "en-US")
    voice_id = str(snapshot.get("voice_id") or settings.elevenlabs_voice_id or "")

    initial_transcript = [
        {
            "role": "assistant",
            "text": greeting,
            "timestamp": now.isoformat(),
        }
    ]
    initial_events = [
        {
            "event": "session.created",
            "timestamp": now.isoformat(),
            "new_state": "IDLE",
            "data": {},
        }
    ]

    row = VoiceDemoSession(
        id=session_id,
        tenant_id=agent.tenant_id,
        agent_id=agent.id,
        agent_version_id=version.id if version else None,
        agent_version_number=int(version.version_number if version else (agent.published_version_number or 1)),
        agent_name=agent.name,
        language=language,
        voice_id=voice_id,
        status="active",
        token_hash=_hash_token(session_token),
        client_ip=(client_ip or "unknown")[:64],
        turn_count=0,
        max_turns=max(1, int(settings.voice_demo_max_turns)),
        transcript=initial_transcript,
        system_prompt=sys_prompt,
        meta={
            "voice_state": "IDLE",
            "events": initial_events,
            "client_metadata": dict(metadata or {}),
        },
        created_at=now,
        updated_at=now,
        expires_at=expires_at,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)

    voice_states = ["IDLE", "LISTENING", "PROCESSING", "SPEAKING", "INTERRUPTED", "ERROR", "NOT_CONFIGURED"]
    return {
        "status": "ok",
        "session_id": str(row.id),
        "session_token": session_token,
        "agent_id": str(row.agent_id),
        "agent_name": row.agent_name,
        "expires_at": expires_at.isoformat(),
        "created_at": now.isoformat(),
        "transport": "ws-protobuf",
        "transcript": initial_transcript,
        "configuration": {
            "session_id": str(row.id),
            "expires_at": expires_at.isoformat(),
            "transport": "ws-protobuf",
            "ice_servers": [],
            "voice_states": voice_states,
            "initial_state": "IDLE",
            "provider_configured": True,
            "message": f"Connected to demo agent '{row.agent_name}'.",
        },
    }


async def _load_verified_session(
    session: AsyncSession,
    *,
    session_id: uuid.UUID,
    token: str | None,
) -> VoiceDemoSession:
    verified = verify_demo_session_token(token, expected_session_id=session_id)
    claims = verified["claims"]
    clean_token = verified["raw_token"]
    now = _now()

    row = await session.get(VoiceDemoSession, session_id)
    if row is None:
        exp_ts = int(claims.get("exp") or 0)
        if exp_ts and int(now.timestamp()) >= exp_ts:
            raise VoiceDemoExpiredError("Voice demo session has expired.")
        raise VoiceDemoNotFoundError("Voice demo session not found.")

    if not hmac.compare_digest(row.token_hash, _hash_token(clean_token)):
        raise VoiceDemoUnauthorizedError("Voice-demo session token mismatch.")

    exp_dt = row.expires_at
    if exp_dt.tzinfo is None:
        exp_dt = exp_dt.replace(tzinfo=timezone.utc)
    if now >= exp_dt or row.status == "expired":
        if row.status != "expired":
            row.status = "expired"
            row.updated_at = now
            await session.commit()
        raise VoiceDemoExpiredError("Voice demo session has expired.")
    return row


async def get_demo_session_state(
    session: AsyncSession,
    *,
    session_id: uuid.UUID,
    token: str | None,
) -> dict[str, Any]:
    row = await _load_verified_session(session, session_id=session_id, token=token)
    meta = row.meta if isinstance(row.meta, dict) else {}
    exp_dt = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)
    created_dt = row.created_at if row.created_at.tzinfo else row.created_at.replace(tzinfo=timezone.utc)
    return {
        "status": "ok",
        "session_id": str(row.id),
        "agent_id": str(row.agent_id),
        "agent_name": row.agent_name,
        "session_status": row.status,
        "state": _voice_state_for_status(row),
        "turn_count": row.turn_count,
        "max_turns": row.max_turns,
        "transcript": list(row.transcript or []),
        "created_at": created_dt.isoformat(),
        "expires_at": exp_dt.isoformat(),
        "events": list(meta.get("events") or []),
    }


async def handle_demo_event(
    session: AsyncSession,
    *,
    session_id: uuid.UUID,
    token: str | None,
    event: str,
    timestamp: str | None = None,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row = await _load_verified_session(session, session_id=session_id, token=token)
    now = _now()
    at_iso = timestamp or now.isoformat()
    payload_data = dict(data or {})
    meta = dict(row.meta or {})
    events = list(meta.get("events") or [])
    transcript = list(row.transcript or [])

    if row.status == "ended" and event not in ("stop", "end"):
        raise VoiceDemoExpiredError("Voice demo session has already ended.")

    ev = event.strip().lower()
    reply_text: str | None = None

    if ev == "start":
        new_state = "LISTENING"
    elif ev in ("turn", "text", "utterance"):
        if row.turn_count >= row.max_turns:
            row.status = "expired"
            row.updated_at = now
            await session.commit()
            raise VoiceDemoExpiredError("Voice demo session reached its turn limit.")
        user_text = str(
            payload_data.get("text")
            or payload_data.get("utterance")
            or payload_data.get("message")
            or ""
        ).strip()
        if not user_text:
            raise ValueError("Turn text is required.")
        transcript.append(
            {
                "role": "user",
                "text": user_text,
                "timestamp": at_iso,
            }
        )
        prompt_hint = f" ({row.system_prompt[:80]})" if row.system_prompt else ""
        reply_text = (
            f"[{row.agent_name}]{prompt_hint} Thanks for asking about \"{user_text[:200]}\". "
            f"How else can I assist you?"
        )
        transcript.append(
            {
                "role": "assistant",
                "text": reply_text,
                "timestamp": now.isoformat(),
            }
        )
        row.turn_count = int(row.turn_count or 0) + 1
        new_state = "SPEAKING"
    elif ev == "interrupt":
        new_state = "INTERRUPTED"
    elif ev in ("stop", "end"):
        new_state = "IDLE"
        row.status = "ended"
        row.ended_at = now
    elif ev == "error":
        new_state = "ERROR"
    else:
        raise ValueError(f"Unsupported demo event {event!r}")

    events.append(
        {
            "event": ev,
            "timestamp": at_iso,
            "new_state": new_state,
            "data": payload_data,
        }
    )
    meta["voice_state"] = new_state
    meta["events"] = events
    row.meta = meta
    row.transcript = transcript
    row.updated_at = now
    await session.commit()
    await session.refresh(row)

    return {
        "status": "ok",
        "session_id": str(row.id),
        "event": ev,
        "new_state": new_state,
        "at": now.isoformat(),
        "reply": reply_text,
        "turn_count": row.turn_count,
        "transcript": transcript,
    }


async def reap_expired_voice_demo_sessions(
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> int:
    """Delete expired rows from ``voice_demo_sessions``; returns deleted row count."""
    moment = now or _now()
    result = await session.execute(
        delete(VoiceDemoSession).where(VoiceDemoSession.expires_at <= moment)
    )
    await session.flush()
    return int(result.rowcount or 0)
