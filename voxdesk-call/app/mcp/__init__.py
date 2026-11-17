"""Model Context Protocol surface (PART 14, P14-07).

`server.py` is a transport-agnostic JSON-RPC dispatcher: give it a request
dict and it returns a response dict. Stdio, SSE and HTTP handlers all call the
same `MCPServer.handle`. Keeping the protocol out of the transport is what makes
the conformance tests possible without a subprocess.

`routes.py` mounts it on FastAPI for the hosted endpoint.
"""

from app.mcp.server import MCPServer, ToolSpec

__all__ = ["MCPServer", "ToolSpec"]
