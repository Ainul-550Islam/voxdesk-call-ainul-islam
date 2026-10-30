"""Durable agent-test session service backed by ``TestRun`` (Capability 6).

Persists interactive agent test sessions in ``test_runs`` so session state,
transcripts, events, and expiry survive worker restarts and horizontal scaling.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Agent, AgentVersion
from app.db.retell_models import TestRun, TestRunStatusEnum


class AgentTestError(Exception):
    """Base error for agent-test session operations."""


class AgentTestNotFoundError(AgentTestError):
    """Target agent or test session does not exist for this tenant."""


class AgentTestExpiredError(AgentTestError):
    """Persisted test session has passed its expiration timestamp."""


class AgentTestUnauthorizedError(AgentTestError):
    """Session token is missing or invalid."""


class AgentTestNotConfiguredError(AgentTestError):
    """Requested live transport requires unconfigured external credentials."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _signing_secret() -> bytes:
    secret_str = (settings.jwt_secret or settings.secret_key).strip()
    if not secret_str:
        raise AgentTestNotConfiguredError(
            "JWT_SECRET or SECRET_KEY must be configured to sign agent test tokens."
        )
    return hashlib.sha256(b"agent-test-v1:" + secret_str.encode("utf-8")).digest()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def mint_agent_test_token(
    *,
    session_id: uuid.UUID,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
    expires_at: datetime,
) -> str:
    payload = {
        "sid": str(session_id),
        "tid": str(tenant_id),
        "aid": str(agent_id),
        "exp": int(expires_at.timestamp()),
    }
    body = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).decode("ascii").rstrip("=")
    sig = hmac.new(_signing_secret(), body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"ats1.{body}.{sig}"


def verify_agent_test_token(
    token: str,
    *,
    expected_session_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    if not token or not isinstance(token, str) or not token.startswith("ats1."):
        raise AgentTestUnauthorizedError("Invalid agent-test session token.")
    parts = token.split(".")
    if len(parts) != 3:
        raise AgentTestUnauthorizedError("Malformed agent-test session token.")
    _, body, sig = parts
    expected_sig = hmac.new(_signing_secret(), body.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected_sig):
        raise AgentTestUnauthorizedError("Agent-test token signature mismatch.")
    pad = "=" * (-len(body) % 4)
    try:
        decoded = json.loads(base64.urlsafe_b64decode((body + pad).encode("ascii")).decode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise AgentTestUnauthorizedError("Corrupt agent-test token payload.") from exc
    if expected_session_id is not None and decoded.get("sid") != str(expected_session_id):
        raise AgentTestUnauthorizedError("Token does not match target session.")
    return decoded


def _carrier_configured() -> bool:
    return bool(
        (settings.twilio_account_sid and settings.twilio_auth_token)
        or settings.telnyx_api_key
        or (settings.vonage_api_key and settings.vonage_api_secret)
    )


async def _resolve_agent_snapshot(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
) -> tuple[Agent, AgentVersion | None, dict[str, Any]]:
    agent = await session.scalar(
        select(Agent).where(
            Agent.id == agent_id,
            Agent.tenant_id == tenant_id,
            Agent.deleted_at.is_(None),
        )
    )
    if agent is None:
        raise AgentTestNotFoundError("Agent not found")

    version: AgentVersion | None = None
    if agent.published_version_id is not None:
        version = await session.scalar(
            select(AgentVersion).where(
                AgentVersion.id == agent.published_version_id,
                AgentVersion.tenant_id == tenant_id,
            )
        )
    if version is None:
        version = await session.scalar(
            select(AgentVersion)
            .where(
                AgentVersion.agent_id == agent.id,
                AgentVersion.tenant_id == tenant_id,
            )
            .order_by(AgentVersion.version_number.desc())
        )
    snapshot = dict(version.config_snapshot if version and version.config_snapshot else (agent.current_draft_config or {}))
    return agent, version, snapshot


def _synthesize_agent_reply(
    *,
    agent_name: str,
    config_snapshot: dict[str, Any],
    prompt_override: str | None,
    user_text: str,
    turn_index: int,
) -> tuple[str, list[dict[str, Any]]]:
    """Generate a deterministic, agent-config-grounded response when offline."""
    clean_user = user_text.strip()
    greeting = str(
        config_snapshot.get("greeting")
        or config_snapshot.get("first_message")
        or f"Hello, this is {agent_name}. How can I help you today?"
    ).strip()
    sys_prompt = str(
        prompt_override
        or config_snapshot.get("system_prompt")
        or config_snapshot.get("prompt")
        or ""
    ).strip()
    configured_tools = config_snapshot.get("tools") or []
    tool_calls: list[dict[str, Any]] = []
    lowered = clean_user.lower()

    if isinstance(configured_tools, list):
        for tool_item in configured_tools:
            tname = (
                str(tool_item.get("name") or "")
                if isinstance(tool_item, dict)
                else str(tool_item)
            ).strip()
            if tname and any(tok in lowered for tok in tname.lower().replace("_", " ").split()):
                tool_calls.append({"name": tname, "arguments": {"query": clean_user}})
                break

    if not clean_user and turn_index == 0:
        return greeting, tool_calls

    role_clause = f" ({sys_prompt[:96]})" if sys_prompt else ""
    reply = (
        f"[{agent_name}]{role_clause} Understood your request: \"{clean_user[:240]}\". "
        f"How would you like to proceed?"
    )
    return reply, tool_calls


async def create_session(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None,
    agent_id: uuid.UUID,
    mode: str = "simulation",
    initial_prompt_override: str | None = None,
    mock_caller_persona: str | None = "curious_customer",
    timeout_sec: int = 120,
) -> dict[str, Any]:
    if mode == "phone_loopback" and not _carrier_configured():
        raise AgentTestNotConfiguredError(
            "phone_loopback requires a configured telephony carrier (Twilio, Telnyx, or Vonage)."
        )

    agent, version, snapshot = await _resolve_agent_snapshot(
        session, tenant_id=tenant_id, agent_id=agent_id
    )
    now = _now()
    expires_at = now + timedelta(seconds=max(10, min(int(timeout_sec), 600)))
    session_id = uuid.uuid4()
    token = mint_agent_test_token(
        session_id=session_id,
        tenant_id=tenant_id,
        agent_id=agent.id,
        expires_at=expires_at,
    )
    ws_url = f"/api/v1/agent-tests/{session_id}/ws?token={token}"

    input_meta = {
        "kind": "agent_test_session",
        "transport_mode": mode,
        "state": "ACTIVE",
        "expires_at": expires_at.isoformat(),
        "timeout_sec": int(timeout_sec),
        "token_hash": _token_hash(token),
        "ws_url": ws_url,
        "initial_prompt_override": initial_prompt_override,
        "mock_caller_persona": mock_caller_persona or "curious_customer",
        "agent_name": agent.name,
        "config_snapshot": snapshot,
        "latency_ms": 0,
    }

    greeting = str(snapshot.get("greeting") or snapshot.get("first_message") or "").strip()
    initial_transcript: list[dict[str, Any]] = []
    if greeting:
        initial_transcript.append(
            {
                "role": "assistant",
                "text": greeting,
                "timestamp": now.isoformat(),
                "latency_ms": 1,
                "tool_calls": [],
            }
        )

    row = TestRun(
        id=session_id,
        tenant_id=tenant_id,
        environment_id=agent.environment_id,
        agent_id=str(agent.id),
        agent_kind=agent.agent_type or "voice",
        agent_version_id=version.id if version else None,
        agent_version_number=int(version.version_number if version else (agent.published_version_number or 1)),
        mode="web_call" if mode == "live_webrtc" else ("phone_call" if mode == "phone_loopback" else "simulation"),
        status=TestRunStatusEnum.RUNNING.value,
        pinned_config_snapshot=input_meta,
        transcript_snapshot=initial_transcript,
        events_snapshot=[
            {
                "event": "session.created",
                "at": now.isoformat(),
                "data": {"mode": mode, "timeout_sec": int(timeout_sec)},
            }
        ],
        created_by=user_id,
        started_at=now,
        created_at=now,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)

    return {
        "id": str(row.id),
        "session_id": str(row.id),
        "agent_id": str(agent.id),
        "tenant_id": str(tenant_id),
        "mode": mode,
        "status": "active",
        "state": "ACTIVE",
        "ws_url": ws_url,
        "token": token,
        "expires_at": expires_at.isoformat(),
        "created_at": now.isoformat(),
        "transcript": initial_transcript,
    }


async def _load_session_row(
    session: AsyncSession,
    *,
    session_id: uuid.UUID,
    tenant_id: uuid.UUID,
    check_expiry: bool = True,
) -> TestRun:
    row = await session.scalar(
        select(TestRun).where(
            TestRun.id == session_id,
            TestRun.tenant_id == tenant_id,
        )
    )
    if row is None:
        raise AgentTestNotFoundError("Test session not found")
    meta = dict(row.pinned_config_snapshot or {})
    if meta.get("kind") != "agent_test_session":
        raise AgentTestNotFoundError("Test session not found")

    if check_expiry:
        exp_raw = meta.get("expires_at")
        if exp_raw:
            exp_dt = datetime.fromisoformat(str(exp_raw).replace("Z", "+00:00"))
            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=timezone.utc)
            if _now() >= exp_dt and meta.get("state") not in ("COMPLETED", "STOPPED"):
                meta["state"] = "EXPIRED"
                row.pinned_config_snapshot = meta
                row.status = TestRunStatusEnum.CANCELLED.value
                row.error_message = "Test session expired"
                await session.commit()
                raise AgentTestExpiredError("Agent test session has expired.")
        if meta.get("state") == "EXPIRED":
            raise AgentTestExpiredError("Agent test session has expired.")
    return row


async def get_session_state(
    session: AsyncSession,
    *,
    session_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> dict[str, Any]:
    row = await _load_session_row(
        session, session_id=session_id, tenant_id=tenant_id, check_expiry=True
    )
    meta = dict(row.pinned_config_snapshot or {})
    transcript = list(row.transcript_snapshot or [])
    state = str(meta.get("state") or "ACTIVE")
    latency_ms = int(meta.get("latency_ms") or row.duration_ms or 0)
    created_iso = row.created_at.isoformat() if row.created_at else _now().isoformat()
    return {
        "id": str(row.id),
        "session_id": str(row.id),
        "agent_id": row.agent_id,
        "state": state,
        "transcript": transcript,
        "latency_ms": latency_ms,
        "error": row.error_message,
        "created_at": created_iso,
        "expires_at": meta.get("expires_at"),
    }


async def process_event(
    session: AsyncSession,
    *,
    session_id: uuid.UUID,
    tenant_id: uuid.UUID,
    event: str,
    data: dict[str, Any] | None = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    row = await _load_session_row(
        session, session_id=session_id, tenant_id=tenant_id, check_expiry=True
    )
    now = _now()
    at_iso = timestamp or now.isoformat()
    payload_data = dict(data or {})
    meta = dict(row.pinned_config_snapshot or {})
    transcript = list(row.transcript_snapshot or [])
    events = list(row.events_snapshot or [])

    current_state = str(meta.get("state") or "ACTIVE")
    if current_state in ("COMPLETED", "STOPPED") and event not in ("stop", "end"):
        raise AgentTestExpiredError("Agent test session has already ended.")

    assistant_reply: str | None = None
    normalized_event = event.strip().lower()

    if normalized_event == "start":
        new_state = "LISTENING"
        if not transcript:
            reply_text, tools = _synthesize_agent_reply(
                agent_name=str(meta.get("agent_name") or "Agent"),
                config_snapshot=dict(meta.get("config_snapshot") or {}),
                prompt_override=meta.get("initial_prompt_override"),
                user_text="",
                turn_index=0,
            )
            assistant_reply = reply_text
            transcript.append(
                {
                    "role": "assistant",
                    "text": reply_text,
                    "timestamp": at_iso,
                    "latency_ms": max(1, int((time.perf_counter() - t0) * 1000)),
                    "tool_calls": tools,
                }
            )
    elif normalized_event in ("text", "utterance", "turn", "audio"):
        user_text = str(
            payload_data.get("text")
            or payload_data.get("utterance")
            or payload_data.get("transcript")
            or ("[audio frame]" if normalized_event == "audio" else "")
        ).strip()
        if not user_text:
            user_text = "[caller audio]"
        transcript.append(
            {
                "role": "user",
                "text": user_text,
                "timestamp": at_iso,
                "latency_ms": None,
                "tool_calls": [],
            }
        )
        reply_text, tools = _synthesize_agent_reply(
            agent_name=str(meta.get("agent_name") or "Agent"),
            config_snapshot=dict(meta.get("config_snapshot") or {}),
            prompt_override=meta.get("initial_prompt_override"),
            user_text=user_text,
            turn_index=len(transcript),
        )
        turn_latency = max(1, int((time.perf_counter() - t0) * 1000))
        assistant_reply = reply_text
        transcript.append(
            {
                "role": "assistant",
                "text": reply_text,
                "timestamp": _now().isoformat(),
                "latency_ms": turn_latency,
                "tool_calls": tools,
            }
        )
        meta["latency_ms"] = turn_latency
        new_state = "ACTIVE"
    elif normalized_event == "interrupt":
        new_state = "INTERRUPTED"
    elif normalized_event in ("stop", "end"):
        new_state = "COMPLETED"
        row.status = TestRunStatusEnum.PASSED.value
        row.completed_at = now
        if row.started_at:
            started = row.started_at
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            row.duration_ms = max(0, int((now - started).total_seconds() * 1000))
    else:
        raise ValueError(f"Unsupported event {event!r}")

    meta["state"] = new_state
    events.append({"event": normalized_event, "at": at_iso, "data": payload_data, "new_state": new_state})
    row.pinned_config_snapshot = meta
    row.transcript_snapshot = transcript
    row.events_snapshot = events
    await session.commit()
    await session.refresh(row)

    created_iso = row.created_at.isoformat() if row.created_at else at_iso
    return {
        "session_id": str(row.id),
        "event": normalized_event,
        "new_state": new_state,
        "at": _now().isoformat(),
        "reply": assistant_reply,
        "transcript": transcript,
        "session": {
            "id": str(row.id),
            "session_id": str(row.id),
            "agent_id": row.agent_id,
            "state": new_state,
            "created_at": created_iso,
            "transcript": transcript,
        },
    }


async def verify_ws_session(
    session: AsyncSession,
    *,
    session_id: uuid.UUID,
    token: str,
) -> tuple[TestRun, uuid.UUID]:
    claims = verify_agent_test_token(token, expected_session_id=session_id)
    tenant_id = uuid.UUID(str(claims["tid"]))
    row = await _load_session_row(
        session, session_id=session_id, tenant_id=tenant_id, check_expiry=True
    )
    meta = dict(row.pinned_config_snapshot or {})
    if not hmac.compare_digest(str(meta.get("token_hash") or ""), _token_hash(token)):
        raise AgentTestUnauthorizedError("Session token has been invalidated.")
    return row, tenant_id
