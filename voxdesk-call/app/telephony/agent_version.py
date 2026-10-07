"""Exact immutable AgentVersion resolution for telephony call sessions.

A call is bound to one published snapshot. Explicit version pins may continue to
use a superseded snapshot, but drafts, cross-agent versions, cross-tenant
versions, and snapshots published to another environment fail closed. When no
version is supplied, only the agent's persisted ``published_version_id`` is
eligible; this module never substitutes the numerically latest row.
"""
from __future__ import annotations

from app.core.value_types import dictionary_value

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Agent, AgentVersion
from app.db.models import Environment


class AgentVersionResolutionError(ValueError):
    """A requested/current version cannot safely serve this call."""

    def __init__(self, code: str, message: str, *, detail: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.detail = detail or {}


async def resolve_telephony_agent_version(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    agent: Agent,
    environment_id: UUID,
    requested_version_number: int | None = None,
) -> AgentVersion:
    """Resolve one exact tenant/agent/version snapshot for telephony.

    ``AgentVersion`` rows are immutable published snapshots. ``version_number``
    is unique within an agent; tenant and agent predicates are included anyway
    so a forged identifier can never widen the lookup. Explicit historical pins
    accept ``published`` and ``superseded`` status without requiring
    ``is_active``. An unpinned call follows the persisted current published
    pointer and must not fall back to ``MAX(version_number)``.
    """
    if agent.tenant_id != tenant_id:
        raise AgentVersionResolutionError(
            "AGENT_VERSION_TENANT_MISMATCH",
            "The agent is not owned by the requested tenant.",
        )
    if agent.environment_id is not None and agent.environment_id != environment_id:
        raise AgentVersionResolutionError(
            "AGENT_VERSION_ENVIRONMENT_MISMATCH",
            "The agent is not available in the selected environment.",
        )

    if requested_version_number is None:
        if agent.published_version_id is None:
            raise AgentVersionResolutionError(
                "AGENT_VERSION_NOT_PUBLISHED",
                "No version was pinned and the agent has no persisted current published version.",
                detail={"agent_id": str(agent.id)},
            )
        version = await session.scalar(
            select(AgentVersion).where(
                AgentVersion.id == agent.published_version_id,
                AgentVersion.tenant_id == tenant_id,
                AgentVersion.agent_id == agent.id,
            )
        )
        if version is None or str(version.status).lower() != "published":
            raise AgentVersionResolutionError(
                "AGENT_VERSION_NOT_PUBLISHED",
                "The persisted current published version pointer is missing or inconsistent.",
                detail={"agent_id": str(agent.id)},
            )
    else:
        version = await session.scalar(
            select(AgentVersion).where(
                AgentVersion.tenant_id == tenant_id,
                AgentVersion.agent_id == agent.id,
                AgentVersion.version_number == requested_version_number,
            )
        )
        if version is None:
            raise AgentVersionResolutionError(
                "AGENT_VERSION_NOT_FOUND",
                "The requested version does not belong to this tenant and agent.",
                detail={
                    "agent_id": str(agent.id),
                    "agent_version_number": requested_version_number,
                },
            )
        if str(version.status).lower() not in {"published", "superseded"}:
            raise AgentVersionResolutionError(
                "AGENT_VERSION_NOT_IMMUTABLE",
                "Only immutable published or superseded versions can be pinned to a call.",
                detail={
                    "agent_id": str(agent.id),
                    "agent_version_number": requested_version_number,
                    "status": str(version.status),
                },
            )

    if version.tenant_id != tenant_id or version.agent_id != agent.id:
        raise AgentVersionResolutionError(
            "AGENT_VERSION_SCOPE_MISMATCH",
            "The resolved version does not belong to the requested tenant and agent.",
        )

    environment = await session.get(Environment, environment_id)
    if environment is None or environment.tenant_id != tenant_id or environment.status != "active":
        raise AgentVersionResolutionError(
            "AGENT_VERSION_ENVIRONMENT_MISMATCH",
            "The selected environment is not active for this tenant.",
            detail={"environment_id": str(environment_id)},
        )

    if version.published_environment_id is not None:
        if version.published_environment_id != environment_id:
            raise AgentVersionResolutionError(
                "AGENT_VERSION_ENVIRONMENT_MISMATCH",
                "The requested version was published to a different environment.",
                detail={
                    "agent_id": str(agent.id),
                    "agent_version_number": version.version_number,
                    "environment_id": str(environment_id),
                    "published_environment_id": str(version.published_environment_id),
                },
            )
    else:
        version_environment = str(version.published_environment or "production").strip().lower()
        runtime_environment = str(environment.kind or "production").strip().lower()
        if version_environment != runtime_environment:
            raise AgentVersionResolutionError(
                "AGENT_VERSION_ENVIRONMENT_MISMATCH",
                "The version was published to a different named environment.",
                detail={
                    "agent_id": str(agent.id),
                    "agent_version_number": version.version_number,
                    "environment": runtime_environment,
                    "published_environment": version_environment,
                },
            )

    return version


def snapshot_profile(version: AgentVersion, agent: Agent) -> dict[str, str | int]:
    """Extract only prompt/voice values from the resolved persisted snapshot."""
    snapshot = version.config_snapshot
    if not isinstance(snapshot, dict):
        raise AgentVersionResolutionError(
            "AGENT_VERSION_SNAPSHOT_INVALID",
            "The resolved immutable version has no readable configuration snapshot.",
            detail={"agent_id": str(agent.id), "agent_version_number": version.version_number},
        )

    identity = dictionary_value(snapshot.get("identity"))
    model = dictionary_value(snapshot.get("model"))
    voice = dictionary_value(snapshot.get("voice"))
    greeting = (
        snapshot.get("greeting")
        or snapshot.get("first_message")
        or identity.get("greeting")
        or ""
    )
    system_prompt = (
        snapshot.get("system_prompt")
        or model.get("system_prompt")
        or identity.get("persona")
        or ""
    )
    voice_id = snapshot.get("voice_id") or voice.get("voice_id") or ""
    if not str(greeting).strip() or not str(system_prompt).strip():
        raise AgentVersionResolutionError(
            "AGENT_VERSION_SNAPSHOT_INCOMPLETE",
            "The resolved immutable version is missing its persisted greeting or system prompt.",
            detail={"agent_id": str(agent.id), "agent_version_number": version.version_number},
        )

    return {
        "agent_id": str(agent.id),
        "agent_name": str(agent.name),
        "version_number": int(version.version_number),
        "version_id": str(version.id),
        "greeting": str(greeting),
        "system_prompt": str(system_prompt),
        "voice_id": str(voice_id or "unconfigured"),
    }
