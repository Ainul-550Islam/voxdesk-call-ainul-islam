"""Runtime agent tools package (Sub-Phase 2D).

Exports built-in telephony call tools (`end_call`, `send_dtmf`), IVR navigation
(`IVRNavigator`), voicemail detection & actions (`VoicemailDetector`), dynamic
HTTP tool execution (`http_tools`), and MCP server tool execution (`mcp_tools`).
"""

from app.agent.tools.builtin_calls import (
    END_CALL_TOOL_SCHEMA,
    SEND_DTMF_TOOL_SCHEMA,
    execute_end_call,
    execute_send_dtmf,
    validate_dtmf_digits,
)
from app.agent.tools.http_tools import (
    build_http_tool_schemas,
    execute_agent_http_tool,
)
from app.agent.tools.ivr_navigation import (
    NAVIGATE_IVR_TOOL_SCHEMA,
    IVRMenuRouter,
    build_ivr_navigator,
    execute_navigate_ivr,
)
from app.agent.tools.mcp_tools import (
    build_mcp_tool_schemas,
    execute_agent_mcp_tool,
)
from app.agent.tools.voicemail import (
    VoicemailAction,
    VoicemailFlowHandler,
    build_voicemail_detector,
)

__all__ = [
    "END_CALL_TOOL_SCHEMA",
    "IVRMenuRouter",
    "NAVIGATE_IVR_TOOL_SCHEMA",
    "SEND_DTMF_TOOL_SCHEMA",
    "VoicemailAction",
    "VoicemailFlowHandler",
    "build_http_tool_schemas",
    "build_ivr_navigator",
    "build_mcp_tool_schemas",
    "build_voicemail_detector",
    "execute_agent_http_tool",
    "execute_agent_mcp_tool",
    "execute_end_call",
    "execute_navigate_ivr",
    "execute_send_dtmf",
    "validate_dtmf_digits",
]
