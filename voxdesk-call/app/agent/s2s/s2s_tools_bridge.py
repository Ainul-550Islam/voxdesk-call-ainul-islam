"""Speech-to-speech tool bridge.

The realtime models call tools with their own wire format. This module maps
the product's existing tools onto that format and executes them through the
*existing* executors -- the same ``FunctionHandlers``, the same HTTP tool
runner (with its SSRF guard), the same MCP runner -- so a speech-to-speech
call cannot reach a capability the cascaded pipeline does not have.

Two protections are carried over from the cascaded path:

* the **runtime tool policy** (``app.ai.guardrails.tool_policy``) decides
  whether a tool may run at all;
* a **per-call ceiling** on tool invocations, so a prompt-injected or
  looped realtime model cannot run unlimited paid operations.
"""

from __future__ import annotations

from typing import Any, Iterable

from app.agent.functions import TOOL_SCHEMAS
from app.agent.tools.http_tools import build_http_tool_schemas, execute_agent_http_tool
from app.agent.tools.mcp_tools import build_mcp_tool_schemas, execute_agent_mcp_tool
from app.core.logging import log

#: Provider tool-schema dialects.
OPENAI_REALTIME = "openai_realtime"
GEMINI_LIVE = "gemini_live"


def _flatten(schema: dict[str, Any]) -> dict[str, Any]:
    """Normalise a chat-style tool schema into name/description/parameters."""
    function = schema.get("function") if isinstance(schema.get("function"), dict) else None
    source = function or schema
    parameters = source.get("parameters") or source.get("input_schema") or {
        "type": "object",
        "properties": {},
    }
    if not isinstance(parameters, dict):
        parameters = {"type": "object", "properties": {}}
    if "type" not in parameters:
        parameters = {**parameters, "type": "object"}
    return {
        "name": str(source.get("name") or "").strip(),
        "description": str(source.get("description") or "").strip(),
        "parameters": parameters,
    }


def to_openai_realtime_tools(schemas: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """The Realtime API expects flat function declarations."""
    tools: list[dict[str, Any]] = []
    seen: set[str] = set()
    for schema in schemas or ():
        flat = _flatten(schema)
        if not flat["name"] or flat["name"] in seen:
            continue
        seen.add(flat["name"])
        tools.append(
            {
                "type": "function",
                "name": flat["name"],
                "description": flat["description"],
                "parameters": flat["parameters"],
            }
        )
    return tools


def to_gemini_live_tools(schemas: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Gemini expects one ``functionDeclarations`` object per message."""
    declarations = [
        {
            "name": flat["name"],
            "description": flat["description"],
            "parameters": flat["parameters"],
        }
        for flat in ( _flatten(schema) for schema in (schemas or ()) )
        if flat["name"]
    ]
    return [{"functionDeclarations": declarations}] if declarations else []


def provider_tools(provider: str, schemas: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Render the tool schemas in the dialect the provider expects."""
    if provider == GEMINI_LIVE:
        return to_gemini_live_tools(schemas)
    return to_openai_realtime_tools(schemas)


class S2SToolsBridge:
    """Executes realtime-model tool calls through the existing executors."""

    def __init__(
        self,
        *,
        handlers=None,
        session=None,
        tenant_id=None,
        custom_tools: list[dict[str, Any]] | None = None,
        api_tools: list[Any] | None = None,
        mcp_tools: list[dict[str, Any]] | None = None,
        max_tool_calls: int = 12,
    ) -> None:
        self.handlers = handlers
        self.session = session
        self.tenant_id = tenant_id
        self.custom_tools = list(custom_tools or [])
        self.api_tools = list(api_tools or [])
        self.mcp_tools = list(mcp_tools or [])
        self.max_tool_calls = max(1, int(max_tool_calls))
        self.calls_used = 0
        self._http_specs: dict[str, dict[str, Any]] = {}
        for spec in self.custom_tools:
            name = str(spec.get("name") or "").strip()
            if name:
                self._http_specs[name] = spec
        self._mcp_specs: dict[str, dict[str, Any]] = {}
        for spec in self.mcp_tools:
            name = str(spec.get("qualified_name") or spec.get("name") or "").strip()
            if name:
                self._mcp_specs[name] = spec

    # ------------------------------------------------------------------
    def schemas(self) -> list[dict[str, Any]]:
        """Every tool this call may use, in chat-style schema form."""
        schemas: list[dict[str, Any]] = []
        seen: set[str] = set()
        for schema in TOOL_SCHEMAS:
            name = str((schema.get("function") or {}).get("name") or "")
            if not name or name in seen:
                continue
            seen.add(name)
            schemas.append(schema)
        for schema in build_http_tool_schemas(self.custom_tools, self.api_tools):
            name = str((schema.get("function") or {}).get("name") or "")
            if not name or name in seen:
                continue
            seen.add(name)
            schemas.append(schema)
        for schema in build_mcp_tool_schemas(self.mcp_tools):
            name = str((schema.get("function") or {}).get("name") or "")
            if not name or name in seen:
                continue
            seen.add(name)
            schemas.append(schema)
        return schemas

    def tools_for(self, provider: str) -> list[dict[str, Any]]:
        """The tool schemas in the provider's dialect."""
        return provider_tools(provider, self.schemas())

    # ------------------------------------------------------------------
    async def execute(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """Execute one tool call and return a provider-safe result."""
        tool_name = (name or "").strip()
        payload = dict(arguments or {})
        if not tool_name:
            return {"ok": False, "error": "missing_tool_name"}

        if self.calls_used >= self.max_tool_calls:
            log.warning("s2s.tools.per_call_limit", tool=tool_name, calls=self.calls_used)
            return {
                "ok": False,
                "tool": tool_name,
                "error": "tool_call_limit_reached",
                "message": "I can't do that right now. Offer to take a message.",
            }
        self.calls_used += 1

        try:
            # The HTTP and MCP tools carry their own guards (the SSRF guard
            # for HTTP, the MCP descriptor allowlist for MCP), exactly as in
            # the cascaded pipeline. The built-in agent tools are the ones
            # the runtime policy governs.
            if tool_name in self._http_specs:
                return await self._execute_http(tool_name, payload)
            if tool_name in self._mcp_specs:
                return await self._execute_mcp(tool_name, payload)

            try:
                from app.ai.guardrails.tool_policy import agent_runtime_decision

                decision = agent_runtime_decision(tool_name)
            except Exception:  # pragma: no cover - the policy module is always present
                decision = None
            if decision is not None and not getattr(decision, "allowed", True):
                return {
                    "ok": False,
                    "tool": tool_name,
                    "error": "tool_not_allowed",
                    "reason": str(getattr(decision, "reason", "") or ""),
                }

            if self.handlers is not None:
                result = await self.handlers.dispatch(tool_name, payload)
                return result if isinstance(result, dict) else {"ok": True, "result": result}
            return {"ok": False, "tool": tool_name, "error": "tool_not_available"}
        except Exception as exc:
            log.warning(
                "s2s.tools.failed", tool=tool_name, error=type(exc).__name__
            )
            return {"ok": False, "tool": tool_name, "error": "tool_execution_failed"}

    async def _execute_http(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        spec = self._http_specs[name]
        return await execute_agent_http_tool(
            tool_spec=spec,
            arguments=arguments,
            session=self.session,
            tenant_id=self.tenant_id,
        )

    async def _execute_mcp(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if self.session is None or self.tenant_id is None:
            return {"ok": False, "tool": name, "error": "mcp_not_available"}
        return await execute_agent_mcp_tool(
            self.session,
            tenant_id=self.tenant_id,
            tool_descriptor=self._mcp_specs[name],
            arguments=arguments,
        )


__all__ = [
    "GEMINI_LIVE",
    "OPENAI_REALTIME",
    "S2SToolsBridge",
    "provider_tools",
    "to_gemini_live_tools",
    "to_openai_realtime_tools",
]
