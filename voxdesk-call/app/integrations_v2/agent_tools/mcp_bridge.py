"""Bridge exposing enabled Integrations v2 tools to VoxDesk's MCP & API-tool surfaces."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.integrations_v2_models import IntegrationConnection, ToolEnablement
from app.db.models import UserRole
from app.integrations_v2.agent_tools.executor import AgentToolExecutor
from app.integrations_v2.agent_tools.tool_defs import resolve_tool_spec


async def list_mcp_v2_tools(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    agent_id: str = "",
) -> list[dict[str, Any]]:
    """Return MCP-compatible tool descriptors for all active, enabled v2 tools in ``tenant_id``."""
    stmt = (
        select(ToolEnablement, IntegrationConnection)
        .join(
            IntegrationConnection,
            IntegrationConnection.id == ToolEnablement.connection_id,
        )
        .where(
            ToolEnablement.tenant_id == tenant_id,
            ToolEnablement.enabled.is_(True),
            IntegrationConnection.tenant_id == tenant_id,
            IntegrationConnection.status == "active",
        )
    )
    rows = (await session.execute(stmt)).all()

    chosen: dict[str, tuple[ToolEnablement, IntegrationConnection]] = {}
    for en, conn in rows:
        spec = resolve_tool_spec(en.tool_name)
        if spec is None:
            continue
        if en.agent_id == "" and spec.name not in chosen:
            chosen[spec.name] = (en, conn)
        elif agent_id and en.agent_id == agent_id:
            chosen[spec.name] = (en, conn)

    descriptors: list[dict[str, Any]] = []
    for canonical_name, (en, conn) in sorted(chosen.items()):
        spec = resolve_tool_spec(canonical_name)
        if spec is None:
            continue
        desc = spec.to_mcp_tool()
        desc["name"] = en.tool_name
        desc["canonical_name"] = spec.name
        desc["provider"] = conn.provider
        desc["connection_id"] = str(conn.id)
        desc["timeout_ms"] = en.timeout_ms
        desc["max_calls_per_conversation"] = en.max_calls_per_conversation
        descriptors.append(desc)
    return descriptors


async def call_mcp_v2_tool(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    tool_name: str,
    arguments: dict[str, Any],
    call_id: str = "",
    agent_id: str = "",
    actor_user_id: uuid.UUID | None = None,
    actor_role: UserRole | None = None,
    http_client: Any = None,
) -> dict[str, Any]:
    executor = AgentToolExecutor(session, http_client=http_client)
    res = await executor.execute(
        tenant_id=tenant_id,
        tool_name=tool_name,
        arguments=arguments,
        call_id=call_id,
        agent_id=agent_id,
        actor_user_id=actor_user_id,
        actor_role=actor_role,
    )
    return res.to_dict()
