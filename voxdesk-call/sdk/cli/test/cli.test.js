"use strict";

const test = require("node:test");
const assert = require("node:assert");
const { run, parseArgs, route, renderTable } = require("../src/index");

function harness(response) {
  const lines = [];
  const errors = [];
  const calls = [];
  const fetchStub = async (url, init) => {
    calls.push({ url, init });
    if (response instanceof Error) {
      throw response;
    }
    return response;
  };
  return {
    calls,
    lines,
    errors,
    io: {
      fetch: fetchStub,
      out: (line) => lines.push(line),
      err: (line) => errors.push(line),
      env: { VOXDESK_API_KEY: "test-key", VOXDESK_BASE_URL: "https://api.test" },
    },
  };
}

const ok = (body) => ({ ok: true, status: 200, json: async () => body, statusText: "OK" });

test("no api key exits 2 and never calls fetch", async () => {
  const h = harness(ok({}));
  const code = await run(["agents", "list"], { ...h.io, env: {} });
  assert.equal(code, 2);
  assert.match(h.errors[0], /no API key/);
  assert.equal(h.calls.length, 0);
});

test("workflows catalog sends the API key header and prints a table", async () => {
  const h = harness(ok({ integrations: [{ key: "slack", name: "Slack", auth: "oauth2" }] }));
  const code = await run(["workflows", "catalog"], h.io);
  assert.equal(code, 0);
  assert.equal(h.calls[0].url, "https://api.test/api/workflow-library/catalog");
  assert.equal(h.calls[0].init.headers["X-API-Key"], "test-key");
  assert.match(h.lines.join("\n"), /slack/);
});

test("--json prints only JSON", async () => {
  const h = harness(ok({ integrations: [] }));
  const code = await run(["workflows", "catalog", "--json"], h.io);
  assert.equal(code, 0);
  assert.deepEqual(JSON.parse(h.lines[0]), { integrations: [] });
});

test("a server error prints its code and exits 1", async () => {
  const h = harness({
    ok: false,
    status: 409,
    statusText: "Conflict",
    json: async () => ({ detail: { code: "review_missing", message: "not signed off" } }),
  });
  const code = await run(["calls", "get", "c-1"], h.io);
  assert.equal(code, 1);
  assert.match(h.errors[0], /review_missing/);
});

test("a network failure is reported, not thrown", async () => {
  const h = harness(new Error("ECONNREFUSED"));
  const code = await run(["agents", "list"], h.io);
  assert.equal(code, 1);
  assert.match(h.errors[0], /cannot reach/);
});

test("unknown commands and missing arguments exit 2", async () => {
  const h = harness(ok({}));
  assert.equal(await run(["nope", "nope"], h.io), 2);
  assert.equal(await run(["calls", "get"], h.io), 2);
});

test("parseArgs handles --base-url in both forms", () => {
  assert.equal(parseArgs(["--base-url", "https://x.test", "agents", "list"]).flags.baseUrl, "https://x.test");
  assert.equal(parseArgs(["--base-url=https://y.test", "agents", "list"]).flags.baseUrl, "https://y.test");
  assert.throws(() => parseArgs(["--nope"]));
});

test("route builds encoded paths", () => {
  assert.equal(route(["calls", "get", "a/b"]).path("a/b"), "/api/calls/a%2Fb");
});

test("renderTable pads columns and says (empty)", () => {
  assert.equal(renderTable([], ["a"]), "(empty)");
  const table = renderTable([{ a: "1", b: "22" }], ["a", "b"]);
  assert.match(table.split("\n")[0], /a +b/);
});
