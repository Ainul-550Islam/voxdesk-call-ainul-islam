#!/usr/bin/env node
/**
 * dashboard/scripts/write-route-registry.mjs
 *
 * Regenerate `src/__tests__/fixtures/route-registry.json` from the real router.
 *
 * That fixture is asserted against `ROUTES` by
 * `src/__tests__/no-generated-tails.test.ts` ("preserves every route, metadata
 * field, alias, and declaration order"). It is a deliberately frozen copy of the
 * route table: adding a route has to be an explicit, reviewable edit, not a
 * silent side effect of the test run. This script is the only supported way to
 * make that edit, so the diff shows exactly which routes changed.
 *
 * Usage:
 *   npm run routes:registry        # rewrite the fixture
 *   npm run routes:registry:check  # exit 1 when the fixture is stale
 *
 * The module is loaded through Vite's SSR loader so the TypeScript router is
 * compiled by the same toolchain the app uses — a hand-rolled transpile would let
 * the fixture drift from what production builds.
 */
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { createServer } from 'vite';

const FIXTURE = path.resolve('src/__tests__/fixtures/route-registry.json');

function serialise(routes) {
  // Two-space JSON plus a trailing newline: the fixture is read by a test, but it
  // is also reviewed in pull requests, so it has to be diffable.
  return `${JSON.stringify(routes, null, 2)}\n`;
}

const server = await createServer({
  configFile: path.resolve('vite.config.js'),
  server: { middlewareMode: true },
  appType: 'custom',
  logLevel: 'error',
  // This process only loads one module. Dependency pre-bundling would scan
  // index.html, race against the immediate `server.close()` below and print an
  // "outdated request" stack that looks like a failure but is not.
  optimizeDeps: { noDiscovery: true, include: [] },
});

try {
  const { ROUTES } = await server.ssrLoadModule('/src/app/router.tsx');
  if (!Array.isArray(ROUTES) || ROUTES.length === 0) {
    throw new Error('router.tsx exported no routes; refusing to write an empty registry');
  }
  const next = serialise(ROUTES);
  const current = fs.existsSync(FIXTURE) ? fs.readFileSync(FIXTURE, 'utf8') : '';
  const check = process.argv.includes('--check');

  if (next === current) {
    console.log(`route registry is current (${ROUTES.length} routes)`);
    process.exitCode = 0;
  } else if (check) {
    console.error(`route registry is stale: ${FIXTURE} does not match ROUTES (${ROUTES.length} routes)`);
    console.error('run `npm run routes:registry` and review the diff');
    process.exitCode = 1;
  } else {
    fs.writeFileSync(FIXTURE, next);
    console.log(`wrote ${path.relative(process.cwd(), FIXTURE)} (${ROUTES.length} routes)`);
    process.exitCode = 0;
  }
} finally {
  await server.close().catch(() => {
    // A close that races the last transform is not an error for this script: the
    // file has already been written or compared by the time we get here.
  });
}
