/**
 * VoxDesk CLI core (`sdk/cli`).
 *
 * The command layer is a pure function: `run(argv, io)` takes arguments and an
 * injected `{fetch, out, err, env}` and returns an exit code. That is what makes
 * it testable under `node --test` without a running API, and it is also why the
 * binary in `bin/` is eleven lines.
 *
 * Conventions that a script depends on:
 *   - `--json` prints machine-readable output and nothing else;
 *   - a non-2xx response exits 1 and prints the server's error code, not a stack;
 *   - a missing API key exits 2 with one line, because a stack trace in a CI log
 *     is worse than useless.
 */

"use strict";

const DEFAULT_BASE = "https://api.voxdesk.example";

class CliError extends Error {
  constructor(code, message, exitCode) {
    super(message);
    this.code = code;
    this.exitCode = exitCode == null ? 1 : exitCode;
  }
}

function usage() {
  return [
    "voxdesk <resource> <action> [options]",
    "",
    "  agents list",
    "  agents get <agent_id>",
    "  calls get <call_id>",
    "  workflows catalog",
    "  knowledge search <query>",
    "",
    "Options:",
    "  --base-url <url>   API origin (env VOXDESK_BASE_URL)",
    "  --json             machine-readable output",
    "  --help             this text",
  ].join("\n");
}

function parseArgs(argv) {
  const positional = [];
  const flags = {};
  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index];
    if (token === "--json") {
      flags.json = true;
    } else if (token === "--help" || token === "-h") {
      flags.help = true;
    } else if (token === "--base-url") {
      flags.baseUrl = argv[index + 1];
      index += 1;
    } else if (token.startsWith("--base-url=")) {
      flags.baseUrl = token.slice("--base-url=".length);
    } else if (token.startsWith("-")) {
      throw new CliError("unknown_flag", `unknown flag: ${token}`, 2);
    } else {
      positional.push(token);
    }
  }
  return { positional, flags };
}

function route(positional) {
  const [resource, action, ...rest] = positional;
  const table = {
    "agents list": { method: "GET", path: () => "/api/agents" },
    "agents get": { method: "GET", path: (id) => `/api/agents/${encodeURIComponent(id)}` },
    "calls get": { method: "GET", path: (id) => `/api/calls/${encodeURIComponent(id)}` },
    "workflows catalog": { method: "GET", path: () => "/api/workflow-library/catalog" },
    "knowledge search": { method: "POST", path: () => "/api/knowledge/search" },
  };
  const key = `${resource} ${action}`;
  const entry = table[key];
  if (!entry) {
    throw new CliError("unknown_command", `unknown command: ${key || "(none)"}`, 2);
  }
  if (key !== "agents list" && key !== "workflows catalog" && rest.length === 0) {
    throw new CliError("missing_argument", `${key} needs an argument`, 2);
  }
  return { ...entry, args: rest };
}

async function request(ctx, method, path, body) {
  const url = `${ctx.baseUrl}${path}`;
  let response;
  try {
    response = await ctx.fetch(url, {
      method,
      headers: {
        "X-API-Key": ctx.apiKey,
        "Content-Type": "application/json",
        "User-Agent": "voxdesk-cli/1.0.0",
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (error) {
    throw new CliError("network", `cannot reach ${url}: ${error.message}`);
  }
  let payload = null;
  try {
    payload = await response.json();
  } catch (error) {
    payload = null;
  }
  if (!response.ok) {
    const detail = payload && payload.detail;
    const code = (detail && detail.code) || `http_${response.status}`;
    const message = (detail && detail.message) || (typeof detail === "string" ? detail : response.statusText);
    throw new CliError(code, `${code}: ${message}`);
  }
  return payload;
}

function renderTable(rows, columns) {
  if (!rows.length) {
    return "(empty)";
  }
  const widths = columns.map((column) =>
    Math.max(column.length, ...rows.map((row) => String(row[column] == null ? "" : row[column]).length))
  );
  const line = (values) => values.map((value, index) => String(value).padEnd(widths[index])).join("  ");
  return [line(columns), line(widths.map((width) => "-".repeat(width))), ...rows.map((row) => line(columns.map((column) => row[column] == null ? "" : row[column])))].join("\n");
}

async function run(argv, io) {
  const ctx = {
    fetch: io.fetch,
    out: io.out || (() => {}),
    err: io.err || (() => {}),
    apiKey: io.env && (io.env.VOXDESK_API_KEY || io.env.VOXDESK_TOKEN),
    baseUrl: (io.env && io.env.VOXDESK_BASE_URL) || DEFAULT_BASE,
  };
  let parsed;
  try {
    parsed = parseArgs(argv);
  } catch (error) {
    ctx.err(error.message);
    return error.exitCode;
  }
  if (parsed.flags.help || parsed.positional.length === 0) {
    ctx.out(usage());
    return parsed.flags.help ? 0 : 2;
  }
  if (parsed.flags.baseUrl) {
    ctx.baseUrl = parsed.flags.baseUrl.replace(/\/$/, "");
  }
  if (!ctx.apiKey) {
    ctx.err("no API key: set VOXDESK_API_KEY");
    return 2;
  }
  try {
    const command = route(parsed.positional);
    const payload = await request(ctx, command.method, command.path(command.args[0]), command.method === "POST" ? { query: command.args.join(" ") } : undefined);
    if (parsed.flags.json) {
      ctx.out(JSON.stringify(payload, null, 2));
      return 0;
    }
    if (payload && Array.isArray(payload.integrations)) {
      ctx.out(renderTable(payload.integrations.map((row) => ({ key: row.key, name: row.name, auth: row.auth })), ["key", "name", "auth"]));
    } else if (payload && Array.isArray(payload.agents)) {
      ctx.out(renderTable(payload.agents, ["id", "name", "status"]));
    } else {
      ctx.out(JSON.stringify(payload, null, 2));
    }
    return 0;
  } catch (error) {
    ctx.err(error instanceof CliError ? error.message : `unexpected error: ${error.message}`);
    return error instanceof CliError ? error.exitCode : 1;
  }
}

module.exports = { run, parseArgs, route, renderTable, usage, CliError };
