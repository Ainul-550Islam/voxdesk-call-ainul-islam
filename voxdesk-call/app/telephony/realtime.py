"""
app/telephony/realtime.py
Media stream token signing and persisted agent runtime profile bookkeeping.

The legacy `RealtimeVoiceSessionOrchestrator.handle_ws_message` template reply
and UTF-8 text-as-audio path (F-05) have been removed in PART 3. Browser and
carrier media streams run through `app/telephony/web_transport.py`,
`app/telephony/twilio_handler.py`, and `app/telephony/telnyx_handler.py` into
`app/agent/pipeline.py::run_voice_agent`.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Agent, AgentVersion
from app.telephony.agent_version import (
    AgentVersionResolutionError,
    resolve_telephony_agent_version,
    snapshot_profile,
)
from app.telephony.exceptions import (
    CallSessionNotFoundError as CallSessionNotFoundError,
    MediaSessionError,
    TelephonyAuthorizationError as TelephonyAuthorizationError,
    TelephonyConfigurationError as ConfigurationError,
)


def _media_signing_key() -> bytes:
    """Return the configured JWT signing key or fail closed (no default secrets)."""
    secret = settings.jwt_secret
    if not secret or not secret.strip():
        raise ConfigurationError(
            "Media stream signing requires JWT_SECRET.",
            status_code=501,
            code="MEDIA_STREAM_NOT_CONFIGURED",
        )
    return secret.encode("utf-8")


def issue_media_stream_token(
    *,
    call_id: UUID,
    tenant_id: UUID,
    ttl_seconds: int = 3600,
) -> str:
    """Issue an HMAC-SHA256 media stream token bound to `call_id`, `tenant_id`, and `exp`."""
    exp = int(time.time()) + ttl_seconds
    secret = _media_signing_key()
    msg = f"{call_id}:{tenant_id}:{exp}".encode("utf-8")
    sig = hmac.new(secret, msg, hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"


def verify_media_stream_token(
    token: str | None,
    *,
    call_id: UUID,
    tenant_id: UUID,
) -> bool:
    """Verify an HMAC-SHA256 media stream token in constant time."""
    if not token or "." not in token:
        return False
    exp_str, sig = token.split(".", 1)
    try:
        exp = int(exp_str)
    except ValueError:
        return False
    if exp < int(time.time()):
        return False
    secret = _media_signing_key()
    msg = f"{call_id}:{tenant_id}:{exp}".encode("utf-8")
    expected = hmac.new(secret, msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig.strip(), expected)


async def resolve_agent_runtime_profile(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    agent_id: str | None,
    agent_version_id: str | None = None,
    agent_version_number: int | None = None,
    environment_id: UUID | None = None,
) -> dict[str, Any]:
    """Load the persisted published snapshot pinned to the call.

    Missing agents, missing version pointers, draft versions, and mismatched
    snapshots fail closed. This runtime never invents a default agent profile
    and never chooses the numerically newest version as a substitute.
    """
    if not agent_id:
        raise MediaSessionError(
            "A persisted agent binding is required for the voice runtime.",
            status_code=503,
            code="AGENT_RUNTIME_NOT_CONFIGURED",
            detail={"state": "NOT_CONFIGURED"},
        )

    try:
        agent_uuid = UUID(str(agent_id))
    except (TypeError, ValueError):
        raise MediaSessionError(
            "The persisted agent binding is not a valid agent UUID.",
            status_code=503,
            code="AGENT_RUNTIME_BINDING_INVALID",
            detail={"agent_id": str(agent_id)},
        ) from None

    agent_row = await session.scalar(
        select(Agent).where(
            Agent.id == agent_uuid,
            Agent.tenant_id == tenant_id,
            Agent.deleted_at.is_(None),
            Agent.archived_at.is_(None),
        )
    )
    if agent_row is None:
        raise MediaSessionError(
            "The bound agent is not available in this tenant.",
            status_code=503,
            code="AGENT_RUNTIME_AGENT_NOT_FOUND",
            detail={"agent_id": str(agent_uuid)},
        )

    if environment_id is None:
        raise MediaSessionError(
            "The call session has no persisted environment binding.",
            status_code=503,
            code="AGENT_RUNTIME_ENVIRONMENT_NOT_CONFIGURED",
            detail={"agent_id": str(agent_uuid)},
        )

    requested_number = agent_version_number
    if agent_version_id is not None:
        try:
            pinned_version_id = UUID(str(agent_version_id))
        except (TypeError, ValueError):
            raise MediaSessionError(
                "The call's persisted agent version id is invalid.",
                status_code=503,
                code="AGENT_RUNTIME_VERSION_ID_INVALID",
                detail={"agent_id": str(agent_uuid)},
            ) from None
        version = await session.scalar(
            select(AgentVersion).where(
                AgentVersion.id == pinned_version_id,
                AgentVersion.tenant_id == tenant_id,
                AgentVersion.agent_id == agent_row.id,
            )
        )
        if version is None or (requested_number is not None and version.version_number != requested_number):
            raise MediaSessionError(
                "The call's persisted agent version id and number do not resolve to the same snapshot.",
                status_code=503,
                code="AGENT_RUNTIME_VERSION_MISMATCH",
                detail={"agent_id": str(agent_uuid), "agent_version_number": requested_number},
            )
        requested_number = int(version.version_number)

    try:
        version_row = await resolve_telephony_agent_version(
            session,
            tenant_id=tenant_id,
            agent=agent_row,
            environment_id=environment_id,
            requested_version_number=requested_number,
        )
        profile = snapshot_profile(version_row, agent_row)
    except AgentVersionResolutionError as exc:
        raise MediaSessionError(
            str(exc),
            status_code=503,
            code=exc.code,
            detail=exc.detail,
        ) from None

    profile["environment_id"] = str(environment_id)
    return profile
