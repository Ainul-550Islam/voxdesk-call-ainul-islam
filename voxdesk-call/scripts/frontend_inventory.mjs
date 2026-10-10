#!/usr/bin/env node
/**
 * scripts/frontend_inventory.mjs
 *
 * Inventories all routes, page components, and API bindings across:
 *   1. `dashboard/` (Vite + React shipped UI: `src/app/router.tsx`, `src/app/app.tsx`, `src/App.jsx`, `src/pages/`)
 *   2. `dashboard-next/` (Next.js App Router shadow/roadmap UI: `app/.../page.tsx`, `lib/api.ts`)
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

function resolveImportToFile(baseDir, importPath) {
  const candidates = [
    path.resolve(baseDir, importPath),
    path.resolve(baseDir, `${importPath}.tsx`),
    path.resolve(baseDir, `${importPath}.ts`),
    path.resolve(baseDir, `${importPath}.jsx`),
    path.resolve(baseDir, `${importPath}.js`),
    path.resolve(baseDir, importPath, 'index.tsx'),
    path.resolve(baseDir, importPath, 'index.ts'),
    path.resolve(baseDir, importPath, 'index.jsx'),
    path.resolve(baseDir, importPath, 'index.js'),
  ];
  for (const c of candidates) {
    if (fs.existsSync(c) && fs.statSync(c).isFile()) {
      return path.relative(ROOT, c);
    }
  }
  return null;
}

function extractApiLiteralsFromFile(relFilePath) {
  if (!relFilePath) return [];
  const absPath = path.join(ROOT, relFilePath);
  if (!fs.existsSync(absPath)) return [];
  const text = fs.readFileSync(absPath, 'utf8');
  const apis = new Set();

  const pathRe = /[`"']((?:\/api|\/auth|\/health|\/scim|\/ws)\/[^`"'\s]*)[`"']/g;
  for (const m of text.matchAll(pathRe)) {
    apis.add(m[1]);
  }

  const importRe = /from\s+['"]([^'"]*(?:\/api\/|\/hooks\/|\/lib\/)[^'"]+)['"]/g;
  for (const m of text.matchAll(importRe)) {
    const resolved = resolveImportToFile(path.dirname(absPath), m[1]);
    if (resolved) {
      const modText = fs.readFileSync(path.join(ROOT, resolved), 'utf8');
      for (const pm of modText.matchAll(pathRe)) {
        apis.add(pm[1]);
      }
      // Also follow 1 hop from hook -> api
      for (const hm of modText.matchAll(importRe)) {
        const resolvedHookApi = resolveImportToFile(
          path.dirname(path.join(ROOT, resolved)),
          hm[1],
        );
        if (resolvedHookApi) {
          const apiText = fs.readFileSync(path.join(ROOT, resolvedHookApi), 'utf8');
          for (const apm of apiText.matchAll(pathRe)) {
            apis.add(apm[1]);
          }
        }
      }
    }
  }

  return [...apis].sort();
}

function parseImportsMap(absFilePath) {
  if (!fs.existsSync(absFilePath)) return {};
  const text = fs.readFileSync(absFilePath, 'utf8');
  const baseDir = path.dirname(absFilePath);
  const componentToFile = {};
  const importLineRe = /import\s+(?:\{\s*([^}]+)\s*\}|([A-Za-z0-9_]+))\s+from\s+['"]([^'"]+)['"]/g;
  for (const m of text.matchAll(importLineRe)) {
    const named = m[1];
    const def = m[2];
    const spec = m[3];
    const resolved = resolveImportToFile(baseDir, spec);
    if (named) {
      for (const part of named.split(',')) {
        const trimmed = part.trim();
        if (!trimmed) continue;
        const asMatch = trimmed.match(/^([A-Za-z0-9_]+)(?:\s+as\s+([A-Za-z0-9_]+))?$/);
        if (asMatch) {
          const localName = asMatch[2] || asMatch[1];
          componentToFile[localName] = resolved;
        }
      }
    } else if (def) {
      componentToFile[def] = resolved;
    }
  }
  return componentToFile;
}

function inventoryDashboardVite() {
  const routerPath = path.join(ROOT, 'dashboard/src/app/router.tsx');
  const appPath = path.join(ROOT, 'dashboard/src/app/app.tsx');
  const legacyAppPath = path.join(ROOT, 'dashboard/src/App.jsx');
  const routerText = fs.readFileSync(routerPath, 'utf8');
  const appText = fs.readFileSync(appPath, 'utf8');

  const appImports = parseImportsMap(appPath);
  const legacyImports = parseImportsMap(legacyAppPath);

  // Parse switch (route.component) in app.tsx
  const caseToRenderedComponent = {};
  const switchIdx = appText.indexOf('switch (route.component)');
  const switchBody = switchIdx !== -1 ? appText.slice(switchIdx) : appText;
  const caseRe = /((?:case\s+'[^']+'\s*:\s*)+)return\s+(?:<([A-Z][A-Za-z0-9_]*)|\([\s\S]*?<([A-Z][A-Za-z0-9_]*))/g;
  for (const m of switchBody.matchAll(caseRe)) {
    const casesChunk = m[1];
    const renderedComp = m[2] || m[3];
    for (const cm of casesChunk.matchAll(/case\s+'([^']+)'/g)) {
      caseToRenderedComponent[cm[1]] = renderedComp;
    }
  }

  // Parse ROUTES array from router.tsx (supporting both direct entries and ...routeAliases([...], {...}))
  const routesStart = routerText.indexOf('export const ROUTES: RouteConfig[] = [');
  const routesEnd = routerText.indexOf('];', routesStart);
  const routesBody = routerText.slice(routesStart, routesEnd);

  const rawRoutes = [];
  for (const line of routesBody.split('\n')) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('//')) continue;

    if (trimmed.startsWith('...routeAliases(')) {
      const aliasMatch = trimmed.match(/^\.\.\.routeAliases\(\[([^\]]+)\],\s*\{(.+)\}\)/);
      if (aliasMatch) {
        const paths = [...aliasMatch[1].matchAll(/'([^']+)'/g)].map((m) => m[1]);
        const props = aliasMatch[2];
        const title = (props.match(/title:\s*'([^']+)'/) || [])[1] || '';
        const component = (props.match(/component:\s*'([^']+)'/) || [])[1] || '';
        const category = (props.match(/category:\s*'([^']+)'/) || [])[1] || '';
        const exact = (props.match(/exact:\s*(true|false)/) || [])[1] === 'true';
        const legacyPath = (props.match(/legacyPath:\s*'([^']+)'/) || [])[1] || null;
        for (const p of paths) {
          rawRoutes.push({ path: p, title, component, category, exact, legacyPath });
        }
      }
    } else if (trimmed.startsWith('{')) {
      const pathVal = (trimmed.match(/path:\s*'([^']+)'/) || [])[1];
      const title = (trimmed.match(/title:\s*'([^']+)'/) || [])[1] || '';
      const component = (trimmed.match(/component:\s*'([^']+)'/) || [])[1] || '';
      const category = (trimmed.match(/category:\s*'([^']+)'/) || [])[1] || '';
      const exact = (trimmed.match(/exact:\s*(true|false)/) || [])[1] === 'true';
      const legacyPath = (trimmed.match(/legacyPath:\s*'([^']+)'/) || [])[1] || null;
      if (pathVal && component) {
        rawRoutes.push({ path: pathVal, title, component, category, exact, legacyPath });
      }
    }
  }

  const routes = rawRoutes.map((r) => {
    const renderedComponent = caseToRenderedComponent[r.component] || null;
    let componentFile = renderedComponent ? appImports[renderedComponent] || null : null;
    if (renderedComponent === 'LegacyConsoleLoading') {
      componentFile = 'dashboard/src/App.jsx';
    }
    const fileExists = componentFile ? fs.existsSync(path.join(ROOT, componentFile)) : false;
    const apiEndpoints = extractApiLiteralsFromFile(componentFile);
    return {
      ...r,
      rendered_component: renderedComponent,
      component_file: componentFile,
      component_exists: fileExists,
      reachable: Boolean(renderedComponent && fileExists),
      api_endpoints: apiEndpoints,
    };
  });

  // Inventory top-level page entry files under dashboard/src/pages/
  const allPageFiles = walkFiles(
    path.join(ROOT, 'dashboard/src/pages'),
    (f) => {
      if (f.includes('__tests__') || /\.test\./.test(f)) return false;
      const rel = path.relative(path.join(ROOT, 'dashboard/src/pages'), f);
      const isDirectChild = !rel.includes(path.sep) && /\.(tsx|jsx)$/.test(f);
      const isPageFile = /Page\.(tsx|jsx)$/.test(path.basename(f));
      return isDirectChild || isPageFile;
    },
  ).map((f) => path.relative(ROOT, f));

  const mountedInAppTsx = new Set(routes.map((r) => r.component_file).filter(Boolean));
  const mountedInLegacyConsole = new Set(Object.values(legacyImports).filter(Boolean));

  const pageFilesInventory = allPageFiles.map((relFile) => {
    const inAppTsx = mountedInAppTsx.has(relFile);
    const inLegacyApp = mountedInLegacyConsole.has(relFile);
    return {
      file: relFile,
      registered_in_app_router: inAppTsx,
      registered_in_operator_console: inLegacyApp,
      reachable: inAppTsx || inLegacyApp,
      api_endpoints: extractApiLiteralsFromFile(relFile),
    };
  });

  return {
    total_registered_routes: routes.length,
    public_and_auth_routes_count: routes.filter((r) => r.category !== 'dashboard').length,
    protected_dashboard_routes_count: routes.filter((r) => r.category === 'dashboard').length,
    reachable_routes_count: routes.filter((r) => r.reachable).length,
    broken_route_registrations_count: routes.filter((r) => !r.reachable).length,
    total_page_entry_files: allPageFiles.length,
    reachable_page_entry_files_count: pageFilesInventory.filter((p) => p.reachable).length,
    unmounted_legacy_page_files: pageFilesInventory
      .filter((p) => !p.reachable)
      .map((p) => p.file),
    routes,
    page_files: pageFilesInventory,
  };
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
      shipped_in_production_image: false,
      api_methods: [...apiMethods].sort(),
      has_catch_fallback: hasCatchFallback,
      has_error_state: hasErrorState,
    };
  });

  return {
    total_page_files: routes.length,
    app_router_reachable_count: routes.filter((r) => r.app_router_reachable).length,
    shipped_in_production_image: false,
    routes,
  };
}

const report = {
  generated_at: new Date().toISOString(),
  dashboard_vite: inventoryDashboardVite(),
  dashboard_next: inventoryDashboardNext(),
};

process.stdout.write(JSON.stringify(report, null, 2) + '\n');
