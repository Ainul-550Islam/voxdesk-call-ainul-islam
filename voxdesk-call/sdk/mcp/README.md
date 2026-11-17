# `@voxdesk/mcp`

A Model Context Protocol server for VoxDesk over stdio, for Claude Desktop,
Cursor and any other MCP client.

```bash
VOXDESK_API_KEY=vd_... VOXDESK_BASE_URL=https://your-voxdesk-host node bin/server.js
```

Client configuration and the tool list are documented in
[`docs/MCP_SERVER.md`](../../docs/MCP_SERVER.md).

## Tests

```bash
cd sdk/mcp && npm test    # node --test, 9 tests, no network
```

## Evidence tier

`LOCAL` — protocol and stdio loop are covered by unit tests; no live client
handshake has been recorded.
