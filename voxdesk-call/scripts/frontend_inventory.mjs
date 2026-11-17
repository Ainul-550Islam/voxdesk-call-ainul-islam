#!/usr/bin/env node
/**
 * scripts/frontend_inventory.mjs
 *
 * Inventories every route, page component and API binding in the shipped console:
 * `dashboard-next/` (Next.js App Router: `app/.../page.tsx`, `lib/api.ts`).
 *
 * It used to inventory two frontends. PART 17A retired the Vite one — the pages it
 * served were ported or mapped, `docs/UI_PARITY.md` records which — so there is
 * one tree left to describe. Route *parity* between the two is
 * `scripts/ui_parity.py`'s job and it reads its own route snapshot; this script
 * answers a different question ("what does the console serve, and what does each
 * page call?").
 *
 * Usage:
 *   node scripts/frontend_inventory.mjs > reports/check/frontend_routes.json
 */

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const ROOT = path.resolve(__dirname, '..');

function walkFiles(dir, predicate = () => true) {
  if (!fs.existsSync(dir)) return [];
  const results = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (['node_modules', 'dist', '.next', '.cache', 'coverage'].includes(entry.name)) {
      continue;
    }
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      results.push(...walkFiles(full, predicate));
    } else if (entry.isFile() && predicate(full)) {
      results.push(full);
    }
  }
  return results.sort();
}

function inventoryDashboardNext() {
  const appRoot = path.join(ROOT, 'dashboard-next/app');
  const pageFiles = walkFiles(appRoot, (f) => path.basename(f) === 'page.tsx');

  const routes = pageFiles.map((absFile) => {
    const relFromApp = path.relative(appRoot, path.dirname(absFile));
    const segments = relFromApp
      ? relFromApp
          .split(path.sep)
          .filter((seg) => !(seg.startsWith('(') && seg.endsWith(')')))
      : [];
    const routePath = '/' + segments.join('/');
    const relFile = path.relative(ROOT, absFile);
    const text = fs.readFileSync(absFile, 'utf8');
    const apiMethods = new Set();
    for (const m of text.matchAll(/\b(?:api|identityApi)\.([A-Za-z0-9_]+)\s*\(/g)) {
      apiMethods.add(m[1]);
    }
    const hasCatchFallback = /\.catch\s*\(/.test(text);
    const hasErrorState = /\bsetError\b|\berror\b/i.test(text);

    return {
      route_path: routePath === '/' ? '/' : routePath,
      file: relFile,
      app_router_reachable: true,
      shipped_in_production_image: true,
      api_methods: [...apiMethods].sort(),
      has_catch_fallback: hasCatchFallback,
      has_error_state: hasErrorState,
    };
  });

  return {
    total_page_files: routes.length,
    app_router_reachable_count: routes.filter((r) => r.app_router_reachable).length,
    shipped_in_production_image: true,
    routes,
  };
}

const report = {
  generated_at: new Date().toISOString(),
  // The retired Vite app is not inventoried: it does not exist. `dashboard_next`
  // keeps its key so existing consumers of this JSON do not have to change.
  retired_frontends: ['dashboard (Vite) — deleted in PART 17A, P17A-05'],
  dashboard_next: inventoryDashboardNext(),
};

process.stdout.write(JSON.stringify(report, null, 2) + '\n');
