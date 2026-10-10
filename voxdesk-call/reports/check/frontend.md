# Frontend Check Summary (`reports/check/frontend.md`)

See [`reports/check/FRONTEND_CHECK.md`](./FRONTEND_CHECK.md) and [`reports/check/frontend.json`](./frontend.json) for the complete SELL CHECK 2 of 4 verification report and machine-readable artifacts.

- **Step 1 (`dashboard/` Vite Shipped UI)**: `npm ci` (`0`), `npx tsc --noEmit` (`0`), `npm test` (`50` files, `562` passed, `0` failed, `0` `expect(true).toBe(true)` placeholders), `npm run build` (`0`, `2.94s`, `index.js` `910.87 kB` / `220.77 kB` gzip, `index.css` `169.98 kB` / `17.84 kB` gzip).
- **Step 2 (`dashboard-next/` Next.js Shadow UI)**: `npm ci` (`0`), `npx tsc --noEmit` (`0`), `npm test` (`7` files, `85` passed, `0` failed), `npm run build` (`0`, `49/49` static pages, `52` routes, `.next` `153 MB`). Not packaged in `Dockerfile` or `docker-compose.yml`.
- **Step 3 (Generated-Tail Scan)**: `0` files with generated tails (down from `84`), `0` generated lines (down from `50,425`), `0` `verified: true, real: true` pairs, `0` silent API catches.
- **Step 4 (`npm audit`)**: `dashboard/` has `0` vulnerabilities (`0` critical, `0` high); `dashboard-next/` has `3` prod (`1` critical, `2` high) and `6` total (`3` critical, `2` high, `1` moderate).
- **Step 5 (Route & Page Inventory)**: `102` registered routes in `dashboard/src/app/router.tsx` (`102` reachable, `0` broken registrations); `51` `app/**/page.tsx` routes in `dashboard-next/`.
- **Step 6 (Frontend -> Backend API Contract)**: `357` matched real backend routes (`200` in `dashboard/`, `157` in `dashboard-next/`), `0` template-clone routes, `0` unmatched routes, `0` `/api/agents/{id}/agent-<x>` calls, `0` silent `catch` blocks in `dashboard/src/api/`.
- **Step 7 (Playwright Browser Smoke)**: `3 passed (10.3s)` on headless Chromium (`/usr/bin/chromium` v154.0.8037.92).
