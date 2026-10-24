"""The speech-to-speech tool bridge.

The realtime models call tools with their own wire format. The bridge maps
the product's tools onto that format and runs them through the *existing*
executors, so a speech-to-speech call cannot reach a capability the cascaded
pipeline does not have. The runtime tool policy and the per-call ceiling are
carried over unchanged.
"""

from __future__ import annotations

import pytest

from app.agent.s2s.s2s_tools_bridge import (
    S2SToolsBridge,
    provider_tools,
    to_gemini_live_tools,
    to_openai_realtime_tools,
)


# ---------------------------------------------------------------------------
# The schema dialects
# ---------------------------------------------------------------------------
def test_openai_tool_schemas_are_flat() -> None:
    schemas = [
        {
            "type": "function",
            "function": {
                "name": "book_appointment",
                "description": "Book an appointment",
                "parameters": {
                    "type": "object",
                    "properties": {"when": {"type": "string"}},
                    "required": ["when"],
                },
            },
        }
    ]
    tools = to_openai_realtime_tools(schemas)
    assert tools[0]["name"] == "book_appointment"
    assert tools[0]["parameters"]["type"] == "object"


def test_gemini_tool_schemas_use_function_declarations() -> None:
    schemas = [
        {
            "type": "function",
            "function": {
                "name": "book_appointment",
                "description": "Book an appointment",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    tools = to_gemini_live_tools(schemas)
    assert tools[0]["functionDeclarations"][0]["name"] == "book_appointment"


def test_provider_tools_picks_the_dialect() -> None:
    schemas = [
        {
            "type": "function",
            "function": {"name": "x", "description": "d", "parameters": {"type": "object"}},
        }
    ]
    assert provider_tools("openai_realtime", schemas)[0]["name"] == "x"
    assert provider_tools("gemini_live", schemas)[0]["functionDeclarations"]


def test_a_schema_without_a_name_is_dropped() -> None:
    assert to_openai_realtime_tools([{"type": "function", "function": {}}]) == []
    assert to_gemini_live_tools([{"type": "function", "function": {}}]) == []


def test_the_bridge_renders_the_builtin_tools_for_a_provider() -> None:
    bridge = S2SToolsBridge()
    openai_tools = bridge.tools_for("openai_realtime")
    assert openai_tools
    assert all(tool["type"] == "function" for tool in openai_tools)
    gemini_tools = bridge.tools_for("gemini_live")
    assert gemini_tools
    assert all("functionDeclarations" in tool for tool in gemini_tools)


# ---------------------------------------------------------------------------
# The executor
# ---------------------------------------------------------------------------
class _Handlers:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def dispatch(self, name: str, arguments: dict) -> dict:
        self.calls.append((name, arguments))
        return {"ok": True, "tool": name}


@pytest.mark.asyncio
async def test_the_bridge_executes_a_tool() -> None:
    handlers = _Handlers()
    bridge = S2SToolsBridge(handlers=handlers)
    result = await bridge.execute("book_appointment", {"when": "tomorrow"})
    assert result["ok"] is True
    assert handlers.calls == [("book_appointment", {"when": "tomorrow"})]


@pytest.mark.asyncio
async def test_the_bridge_enforces_the_per_call_ceiling() -> None:
    handlers = _Handlers()
    bridge = S2SToolsBridge(handlers=handlers, max_tool_calls=2)
    assert (await bridge.execute("book_appointment", {}))["ok"] is True
    assert (await bridge.execute("take_message", {}))["ok"] is True
    refused = await bridge.execute("cancel_appointment", {})
    assert refused["ok"] is False
    assert refused["error"] == "tool_call_limit_reached"
    assert len(handlers.calls) == 2


@pytest.mark.asyncio
async def test_the_bridge_reports_an_unknown_tool() -> None:
    bridge = S2SToolsBridge(handlers=_Handlers())
    result = await bridge.execute("nope", {})
    assert result["ok"] is False
    assert result["error"] == "tool_not_allowed"


@pytest.mark.asyncio
async def test_the_bridge_refuses_a_tool_outside_the_runtime_policy() -> None:
    handlers = _Handlers()
    bridge = S2SToolsBridge(handlers=handlers)
    result = await bridge.execute("refund_payment", {})
    assert result["ok"] is False
    assert result["error"] == "tool_not_allowed"
    assert handlers.calls == []


@pytest.mark.asyncio
async def test_the_bridge_refuses_an_empty_tool_name() -> None:
    bridge = S2SToolsBridge(handlers=_Handlers())
    result = await bridge.execute("", {})
    assert result["ok"] is False
    assert result["error"] == "missing_tool_name"


@pytest.mark.asyncio
async def test_a_failing_tool_is_reported_and_not_raised() -> None:
    class _Exploding:
        async def dispatch(self, name: str, arguments: dict) -> dict:
            raise RuntimeError("the tool blew up")

    bridge = S2SToolsBridge(handlers=_Exploding())
    result = await bridge.execute("book_appointment", {})
    assert result["ok"] is False
    assert result["error"] == "tool_execution_failed"


@pytest.mark.asyncio
async def test_the_bridge_without_handlers_reports_the_missing_executor() -> None:
    bridge = S2SToolsBridge()
    result = await bridge.execute("book_appointment", {})
    assert result["ok"] is False
    assert result["error"] == "tool_not_available"


# ---------------------------------------------------------------------------
# The SSRF guard
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_bridge_blocks_a_tool_that_points_at_a_private_address() -> None:
    """A realtime model cannot reach the metadata service of the host."""
    bridge = S2SToolsBridge(
        handlers=_Handlers(),
        custom_tools=[
            {
                "name": "fetch_metadata",
                "description": "Read the instance metadata",
                "method": "GET",
                "url": "http://169.254.169.254/latest/meta-data/",
                "schema": {
                    "name": "fetch_metadata",
                    "method": "GET",
                    "url": "http://169.254.169.254/latest/meta-data/",
                },
            }
        ],
    )
    result = await bridge.execute("fetch_metadata", {})
    assert result["ok"] is False
    assert "SSRF" in str(result.get("error") or "")


@pytest.mark.asyncio
async def test_the_bridge_blocks_a_loopback_tool() -> None:
    bridge = S2SToolsBridge(
        handlers=_Handlers(),
        custom_tools=[
            {
                "name": "fetch_local",
                "description": "Read the local admin port",
                "method": "GET",
                "url": "http://127.0.0.1:8080/admin",
                "schema": {
                    "name": "fetch_local",
                    "method": "GET",
                    "url": "http://127.0.0.1:8080/admin",
                },
            }
        ],
    )
    result = await bridge.execute("fetch_local", {})
    assert result["ok"] is False
    assert "SSRF" in str(result.get("error") or "")


@pytest.mark.asyncio
async def test_the_bridge_blocks_a_private_address_the_model_supplied() -> None:
    """The URL is rendered from the arguments, so the guard must run after."""
    bridge = S2SToolsBridge(
        handlers=_Handlers(),
        custom_tools=[
            {
                "name": "fetch_url",
                "description": "Fetch a URL",
                "method": "GET",
                "url": "{target}",
                "schema": {
                    "name": "fetch_url",
                    "method": "GET",
                    "url": "{target}",
                },
            }
        ],
    )
    result = await bridge.execute("fetch_url", {"target": "http://10.0.0.5/secrets"})
    assert result["ok"] is False
    assert "SSRF" in str(result.get("error") or "")
