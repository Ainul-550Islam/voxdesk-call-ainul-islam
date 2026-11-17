#!/usr/bin/env node
"use strict";

const { run } = require("../src/index");

run(process.argv.slice(2), {
  fetch: globalThis.fetch,
  out: (line) => process.stdout.write(`${line}\n`),
  err: (line) => process.stderr.write(`${line}\n`),
  env: process.env,
}).then((code) => {
  process.exitCode = code;
});
