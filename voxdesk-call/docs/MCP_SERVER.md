# VoxDesk MCP server

Two implementations ship, sharing one contract (`app/mcp/server.py` is the
canonical dispatcher; `sdk/mcp/` is the Node client-facing server):

| Entry point | Transport | Use |
| --- | --- | --- |
| `python -m app.mcp.server` (via your ASGI mount) | in-process JSON-RPC | server-side agents inside VoxDesk |
| `npx @voxdesk/mcp` (`sdk/mcp`) | newline-delimited JSON-RPC on stdio | Claude Desktop, Cursor, editor plugins |

Protocol version: `2024-11-05` (older revisions negotiate down rather than fail).

## Tools

| Tool | Scope | What it does |
| --- | --- | --- |
| `list_agents` | `agents:read` | list agents with ids and status |
| `get_call` | `calls:read` | one call's summary, outcome and duration |
| `search_knowledge` | `knowledge:read` | semantic search over the tenant's knowledge base |
| `create_contact` | `contacts:write` | create/update a contact by phone |
| `run_preset` | `workflows:write` | run a workflow preset (`send_sms`, `book_appointment`, …) |

Every tool is tenant-scoped from the authenticated context, never from the
request body; a tool that is not enabled for the workspace is refused with
`isError` on the call rather than reported as "unknown tool".

## Claude Desktop / Cursor configuration

```json
{
  "mcpServers": {
    "voxdesk": {
      "command": "node",
      "args": ["/absolute/path/to/sdk/mcp/bin/server.js"],
      "env": {
        "VOXDESK_API_KEY": "vd_...",
        "VOXDESK_BASE_URL": "https://your-voxdesk-host"
      }
    }
  }
}
```

## Behaviour worth knowing

- **stdout carries protocol only.** Logs go to stderr; a stray `console.log`
  would corrupt the stream.
- The API key is read once from the environment and never appears in a response.
- A failing tool returns `isError: true` with the message (MCP clients show it to
  the model) — a protocol error is reserved for protocol problems.
- Requests are answered in arrival order.

## Evidence tier

`LOCAL` — the dispatcher and the stdio loop are covered by
`tests/mcp/test_mcp_server.py` (13 tests) and `sdk/mcp && npm test` (9 tests).
No live MCP client handshake has been recorded, so no `LIVE` claim is made.
