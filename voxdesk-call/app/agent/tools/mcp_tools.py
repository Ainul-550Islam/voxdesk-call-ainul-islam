"""MCP (Model Context Protocol) Runtime Tool Adapter (Sub-Phase 2D).

Bridges tenant-configured `McpServer` and `McpTool` rows (`app/mcp/client.py`)
into LLM function-calling schemas and async execution handlers with SSRF protection
and PII scrubbing.
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import McpServer, McpTool
from app.mcp import client as mcp_client
from app.mcp.client import McpError

log = structlog.get_logger()


def build_mcp_tool_schemas(
    mcp_tools: list[dict[str, Any]] | tuple[dict[str, Any], ...],
) -> list[dict[str, Any]]:
    """Convert resolved MCP tool descriptors from `RuntimeConfig.mcp_tools` into LLM tool schemas."""
    schemas: list[dict[str, Any]] = []
    seen: set[str] = set()
    for tool in mcp_tools or ():
        name = str(tool.get("qualified_name") or tool.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        input_schema = dict(tool.get("input_schema") or {"type": "object", "properties": {}})
        if "type" not in input_schema:
            input_schema["type"] = "object"
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": str(tool.get("description") or f"Invoke MCP tool {name}."),
                    "parameters": input_schema,
                },
            }
        )
    return schemas


async def execute_agent_mcp_tool(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    tool_descriptor: dict[str, Any],
    arguments: dict[str, Any],
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Execute an MCP tool call via JSON-RPC `tools/call` against its parent `McpServer`."""
    server_id_raw = tool_descriptor.get("server_id")
    tool_id_raw = tool_descriptor.get("id")
    remote_name = str(tool_descriptor.get("remote_name") or tool_descriptor.get("name") or "")

    server: McpServer | None = None
    tool: McpTool | None = None

    if server_id_raw:
        try:
            sid = uuid.UUID(str(server_id_raw))
            server = (
                await session.execute(
                    select(McpServer).where(
                        McpServer.id == sid,
                        McpServer.tenant_id == tenant_id,
                    )
                )
            ).scalar_one_or_none()
        except ValueError:
            server = None

    if tool_id_raw and server is not None:
        try:
            tid = uuid.UUID(str(tool_id_raw))
            tool = (
                await session.execute(
                    select(McpTool).where(
                        McpTool.id == tid,
                        McpTool.server_id == server.id,
                    )
                )
            ).scalar_one_or_none()
        except ValueError:
            tool = None

    if server is None or tool is None:
        rows = (
            await session.execute(
                select(McpServer, McpTool)
                .join(McpTool, McpTool.server_id == McpServer.id)
                .where(
                    McpServer.tenant_id == tenant_id,
                    McpTool.name == remote_name,
                    McpTool.enabled.is_(True),
                )
            )
        ).first()
        if rows is not None:
            server, tool = rows[0], rows[1]

    if server is None or tool is None:
        return {
            "ok": False,
            "tool": remote_name,
            "error": f"MCP tool {remote_name!r} is not registered or enabled for this tenant.",
        }

    try:
        result = await mcp_client.execute(
            session,
            server,
            tool,
            arguments or {},
            timeout=timeout,
        )
        return {
            "ok": True,
            "tool": remote_name,
            "status": "succeeded",
            "result": result,
            "error": None,
        }
    except McpError as exc:
        return {
            "ok": False,
            "tool": remote_name,
            "status": "failed",
            "result": None,
            "error": str(exc),
        }
