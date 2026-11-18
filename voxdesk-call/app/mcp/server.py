"""MCP server: JSON-RPC 2.0 over injected tool handlers (PART 14, P14-07).

Implements the parts of the Model Context Protocol a hosted agent platform
actually needs:

* `initialize` -- protocol version negotiation and capability advertisement;
* `tools/list` -- the tenant's available tools with JSON Schemas;
* `tools/call` -- one tool invocation, with the tenant resolved from the
  authenticated context and never from the request body;
* `ping` and `notifications/*` -- no-ops that must not error.

Design rules that are enforced, not documented:

* a tool name that is not registered is `-32601`, not a 500;
* a tool that raises becomes `isError: true` with the message, because MCP
  clients show that to a model and swallowing it hides failures;
* every call is checked against the tenant's allowed tool set *before* the
  handler runs -- the protocol has no notion of tenancy, so it is ours to add.
"""

from __future__ import annotations

import inspect
import traceback
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping

PROTOCOL_VERSION = "2024-11-05"
SUPPORTED_VERSIONS = (PROTOCOL_VERSION, "2024-10-07")
SERVER_NAME = "voxdesk-mcp"
SERVER_VERSION = "1.0.0"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

Handler = Callable[[Mapping[str, Any], "CallContext"], Awaitable[Any]]


class JsonRpcError(Exception):
    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Handler | None = None
    #: Scopes the caller's credentials must carry.
    scopes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


@dataclass(frozen=True)
class CallContext:
    tenant_id: uuid.UUID
    scopes: tuple[str, ...]
    request_id: str = ""
    client: str = ""

    def require(self, *needed: str) -> None:
        missing = [scope for scope in needed if scope not in self.scopes]
        if missing:
            raise JsonRpcError(INVALID_PARAMS, f"missing scope(s): {', '.join(missing)}")


def _schema(properties: Mapping[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": dict(properties),
        "required": list(required or []),
        "additionalProperties": False,
    }


def default_tools() -> dict[str, ToolSpec]:
    """The tool surface we expose. Handlers are wired by `register_defaults`."""
    return {
        "list_agents": ToolSpec(
            "list_agents",
            "List the agents in this workspace with their ids and status",
            _schema({}, []),
            scopes=("agents:read",),
        ),
        "get_call": ToolSpec(
            "get_call",
            "Fetch one call's summary, outcome and duration",
            _schema({"call_id": {"type": "string", "description": "Call UUID"}}, ["call_id"]),
            scopes=("calls:read",),
        ),
        "search_knowledge": ToolSpec(
            "search_knowledge",
            "Semantic search over this workspace's knowledge base",
            _schema(
                {
                    "query": {"type": "string"},
                    "top_k": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
                },
                ["query"],
            ),
            scopes=("knowledge:read",),
        ),
        "create_contact": ToolSpec(
            "create_contact",
            "Create or update a contact by phone number",
            _schema(
                {
                    "phone": {"type": "string"},
                    "name": {"type": "string"},
                    "email": {"type": "string"},
                },
                ["phone"],
            ),
            scopes=("contacts:write",),
        ),
        "run_preset": ToolSpec(
            "run_preset",
            "Run a workflow preset function (send_sms, book_appointment, ...)",
            _schema(
                {
                    "preset": {"type": "string"},
                    "arguments": {"type": "object"},
                },
                ["preset", "arguments"],
            ),
            scopes=("workflows:write",),
        ),
    }


class MCPServer:
    def __init__(
        self,
        *,
        tools: Mapping[str, ToolSpec] | None = None,
        allowed_tools: Callable[[uuid.UUID], Awaitable[set[str]]] | None = None,
        server_name: str = SERVER_NAME,
    ) -> None:
        self._tools = dict(tools or default_tools())
        self._allowed = allowed_tools
        self._server_name = server_name
        self._initialized: set[uuid.UUID] = set()

    # -- registration -------------------------------------------------------
    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def tools(self) -> list[ToolSpec]:
        return list(self._tools.values())

    # -- protocol -----------------------------------------------------------
    async def handle(
        self, request: Mapping[str, Any], context: CallContext
    ) -> dict[str, Any] | None:
        """Dispatch one JSON-RPC message. Returns None for notifications."""
        if not isinstance(request, Mapping):
            return _error(None, PARSE_ERROR, "a JSON-RPC request must be an object")
        method = request.get("method")
        request_id = request.get("id")
        if not isinstance(method, str):
            return _error(request_id, INVALID_REQUEST, "missing method")
        if method.startswith("notifications/"):
            # Notifications never get a response, by spec.
            return None

        context = CallContext(
            tenant_id=context.tenant_id,
            scopes=context.scopes,
            request_id=str(request_id or ""),
            client=context.client,
        )
        params = request.get("params") or {}
        if not isinstance(params, Mapping):
            return _error(request_id, INVALID_PARAMS, "params must be an object")

        try:
            if method == "initialize":
                return _result(request_id, self._initialize(params, context))
            if method == "ping":
                return _result(request_id, {})
            if method == "tools/list":
                return _result(request_id, {"tools": await self._list(context)})
            if method == "tools/call":
                return _result(request_id, await self._call(params, context))
            raise JsonRpcError(METHOD_NOT_FOUND, f"unknown method: {method}")
        except JsonRpcError as exc:
            return _error(request_id, exc.code, exc.message, exc.data)
        except Exception as exc:  # never leak a traceback to a client
            return _error(
                request_id,
                INTERNAL_ERROR,
                f"{type(exc).__name__}: {exc}"[:300],
            )

    def _initialize(self, params: Mapping[str, Any], context: CallContext) -> dict[str, Any]:
        requested = str(params.get("protocolVersion") or PROTOCOL_VERSION)
        # Negotiate down rather than refusing: a client on an older revision is
        # better served by a working session than by an error it cannot act on.
        version = requested if requested in SUPPORTED_VERSIONS else PROTOCOL_VERSION
        self._initialized.add(context.tenant_id)
        return {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": self._server_name, "version": SERVER_VERSION},
        }

    async def _list(self, context: CallContext) -> list[dict[str, Any]]:
        allowed = await self._allowed_for(context)
        return [spec.as_dict() for name, spec in self._tools.items() if name in allowed]

    async def _allowed_for(self, context: CallContext) -> set[str]:
        if self._allowed is None:
            return set(self._tools)
        return await self._allowed(context.tenant_id)

    async def _call(self, params: Mapping[str, Any], context: CallContext) -> dict[str, Any]:
        name = params.get("name")
        if not isinstance(name, str) or not name:
            raise JsonRpcError(INVALID_PARAMS, "tools/call needs a tool name")
        spec = self._tools.get(name)
        if spec is None:
            raise JsonRpcError(METHOD_NOT_FOUND, f"unknown tool: {name}")
        allowed = await self._allowed_for(context)
        if name not in allowed:
            # Not "unknown tool": the tool exists, this tenant may not use it.
            raise JsonRpcError(INVALID_PARAMS, f"tool {name!r} is not enabled for this workspace")
        context.require(*spec.scopes)
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, Mapping):
            raise JsonRpcError(INVALID_PARAMS, "arguments must be an object")
        missing = [
            key
            for key in spec.input_schema.get("required", [])
            if key not in arguments or arguments[key] in (None, "")
        ]
        if missing:
            raise JsonRpcError(INVALID_PARAMS, f"missing argument(s): {', '.join(missing)}")
        if spec.handler is None:
            raise JsonRpcError(INTERNAL_ERROR, f"tool {name!r} has no handler")

        try:
            output = spec.handler(arguments, context)
            if inspect.isawaitable(output):
                output = await output
        except JsonRpcError:
            raise
        except Exception as exc:
            # MCP distinguishes a protocol error from a tool error. A failing
            # tool is a *result* with isError, so the model can react to it.
            return {
                "content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"[:1000]}],
                "isError": True,
            }
        return {"content": [{"type": "text", "text": _render(output)}], "isError": False}


def _render(output: Any) -> str:
    import json

    if isinstance(output, str):
        return output
    try:
        return json.dumps(output, default=str, ensure_ascii=False)[:20000]
    except Exception:
        return str(output)[:20000]


def _result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        payload["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": payload}


def trace(exc: BaseException) -> str:
    """Full trace for server logs; the client only ever sees the message."""
    return "".join(traceback.format_exception_only(type(exc), exc)).strip()
