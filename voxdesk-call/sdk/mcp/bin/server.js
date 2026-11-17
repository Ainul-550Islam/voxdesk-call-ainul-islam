#!/usr/bin/env node
"use strict";

const { createServer, serve } = require("../src/server");

const log = (line) => process.stderr.write(`${line}\n`);

const apiKey = process.env.VOXDESK_API_KEY;
if (!apiKey) {
  log("VOXDESK_API_KEY is not set; every tool call will fail");
}

const server = createServer({
  fetch: globalThis.fetch,
  apiKey: apiKey || "",
  baseUrl: process.env.VOXDESK_BASE_URL,
  log,
});

serve({ input: process.stdin, output: process.stdout, server, log }).catch((failure) => {
  log(`fatal: ${failure.message}`);
  process.exitCode = 1;
});
