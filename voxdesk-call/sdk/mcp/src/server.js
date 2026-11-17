/**
 * VoxDesk MCP server over stdio (`sdk/mcp`).
 *
 * Transport is newline-delimited JSON-RPC 2.0 on stdin/stdout, which is what
 * every MCP client (Claude Desktop, Cursor, an editor plugin) speaks today.
 *
 * Two rules make this safe to run inside a client's process:
 *
 *   1. **stdout carries protocol only.** Log lines go to stderr; a stray
 *      `console.log` would corrupt the stream and the client would disconnect
 *      with an unhelpful parse error.
 *   2. **the API key never appears in a response.** Tools take no credentials;
 *      the key is read from the environment once, at start-up.
 *
 * The core (`createServer`) is transport-free so `node --test` can drive it
 * without spawning a process.
 */

"use strict";

const PROTOCOL_VERSION = "2024-11-05";
const SERVER_INFO = { name: "voxdesk-mcp", version: "1.0.0" };

const TOOLS = [
  {
    name: "list_agents",
    description: "List the voice and chat agents in this workspace",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
    request: () => ({ method: "GET", path: "/api/agents" }),
  },
  {
    name: "get_call",
    description: "Fetch one call's summary, outcome and duration",
    inputSchema: {
      type: "object",
      properties: { call_id: { type: "string" } },
      required: ["call_id"],
      additionalProperties: false,
    },
    request: (args) => ({ method: "GET", path: `/api/calls/${encodeURIComponent(args.call_id)}` }),
  },
  {
    name: "workflow_catalog",
    description: "List installable integrations with the permissions each needs",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
    request: () => ({ method: "GET", path: "/api/workflow-library/catalog" }),
  },
  {
    name: "search_knowledge",
    description: "Search this workspace's knowledge base",
    inputSchema: {
      type: "object",
      properties: { query: { type: "string" } },
      required: ["query"],
      additionalProperties: false,
    },
    request: (args) => ({ method: "POST", path: "/api/knowledge/search", body: { query: args.query } }),
  },
];

function error(id, code, message) {
  return { jsonrpc: "2.0", id, error: { code, message } };
}

function result(id, value) {
  return { jsonrpc: "2.0", id, result: value };
}

/**
 * @param {object} options
 * @param {function} options.fetch  fetch implementation
 * @param {string} options.apiKey
 * @param {string} [options.baseUrl]
 * @param {function} [options.log]  stderr logger
 */
function createServer(options) {
  const opts = options || {};
  const baseUrl = String(opts.baseUrl || "https://api.voxdesk.example").replace(/\/$/, "");
  const log = opts.log || (() => {});
  const tools = new Map(TOOLS.map((tool) => [tool.name, tool]));

  async function callApi(spec) {
    const init = {
      method: spec.method,
      headers: {
        "X-API-Key": opts.apiKey,
        "Content-Type": "application/json",
        "User-Agent": "voxdesk-mcp/1.0.0",
      },
    };
    if (spec.body !== undefined) {
      init.body = JSON.stringify(spec.body);
    }
    const response = await opts.fetch(`${baseUrl}${spec.path}`, init);
    const payload = await response.json().catch(() => null);
    if (!response.ok) {
      const detail = payload && payload.detail;
      const code = (detail && detail.code) || `http_${response.status}`;
      throw new Error(code);
    }
    return payload;
  }

  async function handleMessage(message) {
    if (!message || typeof message !== "object") {
      return error(null, -32600, "invalid request");
    }
    const { id, method, params } = message;
    if (typeof method !== "string") {
      return error(id === undefined ? null : id, -32600, "missing method");
    }
    if (method.startsWith("notifications/")) {
      return null;
    }
    if (method === "initialize") {
      const requested = (params && params.protocolVersion) || PROTOCOL_VERSION;
      return result(id, {
        protocolVersion: requested === PROTOCOL_VERSION ? requested : PROTOCOL_VERSION,
        capabilities: { tools: { listChanged: false } },
        serverInfo: SERVER_INFO,
      });
    }
    if (method === "ping") {
      return result(id, {});
    }
    if (method === "tools/list") {
      return result(id, { tools: TOOLS.map(({ request, ...rest }) => rest) });
    }
    if (method === "tools/call") {
      const name = params && params.name;
      const tool = tools.get(name);
      if (!tool) {
        return error(id, -32601, `unknown tool: ${name}`);
      }
      const args = (params && params.arguments) || {};
      const required = tool.inputSchema.required || [];
      const missing = required.filter((key) => args[key] === undefined || args[key] === "");
      if (missing.length) {
        return error(id, -32602, `missing argument(s): ${missing.join(", ")}`);
      }
      try {
        const payload = await callApi(tool.request(args));
        return result(id, {
          content: [{ type: "text", text: JSON.stringify(payload).slice(0, 20000) }],
          isError: false,
        });
      } catch (failure) {
        // A failing tool is a result, not a protocol error: the model needs to
        // see the failure to react to it.
        log(`tool ${name} failed: ${failure.message}`);
        return result(id, { content: [{ type: "text", text: String(failure.message) }], isError: true });
      }
    }
    return error(id, -32601, `unknown method: ${method}`);
  }

  return { handleMessage, tools: () => TOOLS.map(({ request, ...rest }) => rest) };
}

/**
 * Drive the server over two streams. Returns when input ends.
 */
async function serve({ input, output, server, log }) {
  let buffer = "";
  // Requests are answered strictly in arrival order: a client that sends
  // `initialize` then `tools/list` on one write must not receive them swapped.
  let chain = Promise.resolve();
  const write = (payload) => output.write(`${JSON.stringify(payload)}\n`);

  const processLine = async (line) => {
    let message;
    try {
      message = JSON.parse(line);
    } catch (parseError) {
      write(error(null, -32700, "invalid JSON"));
      return;
    }
    const reply = await server.handleMessage(message);
    if (reply) {
      write(reply);
    }
  };

  input.setEncoding("utf8");
  return new Promise((resolve) => {
    input.on("data", (chunk) => {
      buffer += chunk;
      let index = buffer.indexOf("\n");
      while (index >= 0) {
        const line = buffer.slice(0, index).trim();
        buffer = buffer.slice(index + 1);
        if (line) {
          chain = chain.then(() => processLine(line));
        }
        index = buffer.indexOf("\n");
      }
    });
    input.on("end", () => chain.then(resolve));
    input.on("close", () => chain.then(resolve));
    if (log) {
      log("voxdesk-mcp ready");
    }
  });
}

module.exports = { createServer, serve, TOOLS, PROTOCOL_VERSION };
