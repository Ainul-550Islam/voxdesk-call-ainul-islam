"use strict";

const test = require("node:test");
const assert = require("node:assert");
const { EventEmitter } = require("node:events");
const { createServer, serve } = require("../src/server");

function api(body, ok) {
  return { ok: ok !== false, status: ok === false ? 500 : 200, json: async () => body };
}

function makeServer(handler) {
  const calls = [];
  const fetchStub = async (url, init) => {
    calls.push({ url, init });
    return handler(url, init);
  };
  return {
    calls,
    server: createServer({
      fetch: fetchStub,
      apiKey: "k-test",
      baseUrl: "https://api.test/",
      log: () => {},
    }),
  };
}

test("initialize echoes the supported protocol version", async () => {
  const { server } = makeServer(() => api({}));
  const reply = await server.handleMessage({ jsonrpc: "2.0", id: 1, method: "initialize", params: {} });
  assert.equal(reply.result.protocolVersion, "2024-11-05");
  assert.equal(reply.result.serverInfo.name, "voxdesk-mcp");
});

test("an unknown protocol version negotiates down instead of failing", async () => {
  const { server } = makeServer(() => api({}));
  const reply = await server.handleMessage({
    jsonrpc: "2.0",
    id: 1,
    method: "initialize",
    params: { protocolVersion: "1999-01-01" },
  });
  assert.equal(reply.result.protocolVersion, "2024-11-05");
});

test("tools/list never leaks the request builders", async () => {
  const { server } = makeServer(() => api({}));
  const reply = await server.handleMessage({ jsonrpc: "2.0", id: 2, method: "tools/list" });
  assert.ok(reply.result.tools.length >= 4);
  assert.ok(reply.result.tools.every((tool) => tool.request === undefined));
});

test("tools/call sends the API key and returns text content", async () => {
  const { server, calls } = makeServer(() => api({ agents: [{ id: "a1" }] }));
  const reply = await server.handleMessage({
    jsonrpc: "2.0",
    id: 3,
    method: "tools/call",
    params: { name: "list_agents", arguments: {} },
  });
  assert.equal(reply.result.isError, false);
  assert.equal(calls[0].url, "https://api.test/api/agents");
  assert.equal(calls[0].init.headers["X-API-Key"], "k-test");
  assert.match(reply.result.content[0].text, /a1/);
});

test("a missing argument is a protocol error, not a request", async () => {
  const { server, calls } = makeServer(() => api({}));
  const reply = await server.handleMessage({
    jsonrpc: "2.0",
    id: 4,
    method: "tools/call",
    params: { name: "get_call", arguments: {} },
  });
  assert.equal(reply.error.code, -32602);
  assert.equal(calls.length, 0);
});

test("an unknown tool is -32601", async () => {
  const { server } = makeServer(() => api({}));
  const reply = await server.handleMessage({
    jsonrpc: "2.0",
    id: 5,
    method: "tools/call",
    params: { name: "nope", arguments: {} },
  });
  assert.equal(reply.error.code, -32601);
});

test("a failing API call becomes isError:true with the server's code", async () => {
  const { server } = makeServer(() => api({ detail: { code: "call_not_found" } }, false));
  const reply = await server.handleMessage({
    jsonrpc: "2.0",
    id: 6,
    method: "tools/call",
    params: { name: "get_call", arguments: { call_id: "c1" } },
  });
  assert.equal(reply.result.isError, true);
  assert.match(reply.result.content[0].text, /call_not_found/);
});

test("notifications get no response and ping does", async () => {
  const { server } = makeServer(() => api({}));
  assert.equal(await server.handleMessage({ jsonrpc: "2.0", method: "notifications/initialized" }), null);
  assert.deepEqual((await server.handleMessage({ jsonrpc: "2.0", id: 7, method: "ping" })).result, {});
});

test("the stdio loop answers one line per request and survives bad JSON", async () => {
  const input = new EventEmitter();
  input.setEncoding = () => {};
  const written = [];
  const output = { write: (chunk) => written.push(chunk) };
  const { server } = makeServer(() => api({ agents: [] }));
  const done = serve({ input, output, server });
  input.emit("data", '{"jsonrpc":"2.0","id":1,"method":"ping"}\n');
  input.emit("data", "not json\n");
  input.emit("data", '{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n');
  await new Promise((resolve) => setImmediate(resolve));
  input.emit("end");
  await done;
  const messages = written.join("").trim().split("\n").map((line) => JSON.parse(line));
  assert.equal(messages[0].id, 1);
  assert.equal(messages[1].error.code, -32700);
  assert.ok(messages[2].result.tools.length >= 4);
});
