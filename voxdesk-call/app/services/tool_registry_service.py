"""Agent Tool Registry Service (Sub-Phase 2D).

Manages built-in telephony tools (`end_call`, `send_dtmf`, `navigate_ivr`,
`escalate_to_human`), custom HTTP `AgentTool` definitions, and tenant `McpTool`
discovery/execution.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.functions import TOOL_CONTRACTS, TOOL_SCHEMAS
from app.agent.tools.http_tools import execute_agent_http_tool
from app.db.enterprise_models import AgentTool


def list_builtin_tools() -> list[dict[str, Any]]:
    """Return all built-in runtime tools with their function schemas and contracts."""
    schema_by_name = {
        entry["function"]["name"]: entry["function"]
        for entry in TOOL_SCHEMAS
        if "function" in entry
    }
    items: list[dict[str, Any]] = []
    for name, contract in sorted(TOOL_CONTRACTS.items()):
        fn = schema_by_name.get(name, {})
        items.append(
            {
                "name": name,
                "description": fn.get("description", ""),
                "parameters": fn.get("parameters", {"type": "object", "properties": {}}),
                "effect": contract.get("effect"),
                "scope": contract.get("scope"),
                "agent_runtime": contract.get("agent_runtime", False),
            }
        )
    return items


async def list_agent_custom_tools(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    agent_id: str,
    enabled_only: bool = True,
) -> list[AgentTool]:
    stmt = select(AgentTool).where(
        AgentTool.tenant_id == tenant_id,
        AgentTool.agent_id == str(agent_id),
    )
    if enabled_only:
        stmt = stmt.where(AgentTool.is_enabled.is_(True))
    return list((await session.execute(stmt)).scalars().all())


async def test_agent_custom_tool(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    tool: AgentTool,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Execute a live test invocation of an `AgentTool` using `execute_agent_http_tool`."""
    return await execute_agent_http_tool(
        tool_spec={
            "id": str(tool.id),
            "name": tool.name,
            "description": tool.description,
            "schema": dict(tool.schema or {}),
            "auth_binding": dict(tool.auth_binding or {}),
        },
        arguments=arguments or {},
        session=session,
        tenant_id=tenant_id,
    )
