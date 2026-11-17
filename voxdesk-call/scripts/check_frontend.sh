#!/usr/bin/env bash
# scripts/check_frontend.sh — One-shot runner for SELL CHECK 2 of 4 (Frontend check and test)
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

REPORT_DIR="$ROOT_DIR/reports/check"
mkdir -p "$REPORT_DIR"

echo "=================================================================="
echo "SELL CHECK 2/4 — Frontend Check & Test Pipeline"
echo "Repository: $ROOT_DIR"
echo "=================================================================="

# ------------------------------------------------------------------
# Step 1 — Shipped UI: dashboard-next/ (Next.js, standalone output)
# ------------------------------------------------------------------
# The Vite app is gone (PART 17A, P17A-05). It is not built, not tested and not
# audited here, because it does not exist: `docs/UI_PARITY.md` records where each
# of its 116 routes went, and `scripts/ui_parity.py` is the gate that says so.
echo "[Step 1/7] Checking shipped UI: dashboard-next/ (Next.js)..."
(
  cd "$ROOT_DIR/dashboard-next"
  if [ ! -d node_modules ]; then
    npm ci
  fi
  npx tsc --noEmit
  npx tsc --noEmit -p vite-port/tsconfig.json
  npm test -- --reporter=json --outputFile="$REPORT_DIR/dashboard_next_vitest.json"
  npm run build
)

# ------------------------------------------------------------------
# Step 2 — Route parity: every retired Vite route has a shipped home
# ------------------------------------------------------------------
echo "[Step 2/7] Checking route parity (scripts/ui_parity.py)..."
python3 scripts/ui_parity.py --check

# ------------------------------------------------------------------
# Step 3 — Generated-tail & fake-verified scan
# ------------------------------------------------------------------
echo "[Step 3/7] Running generated-tail & verified:true/real:true scan..."
python3 scripts/generated_tail_scan.py dashboard-next --json-out "$REPORT_DIR/generated_tail_scan.json"
python3 scripts/strip_generated_tails.py --check dashboard-next > "$REPORT_DIR/strip_generated_tails_check.json"

# ------------------------------------------------------------------
# Step 4 — Dependency vulnerability audit (npm audit)
# ------------------------------------------------------------------
echo "[Step 4/7] Running npm audit on dashboard-next/..."
(
  cd "$ROOT_DIR/dashboard-next"
  npm audit --omit=dev --audit-level=high --json > "$REPORT_DIR/dashboard_next_audit_prod.json" || true
  npm audit --json > "$REPORT_DIR/dashboard_next_audit_full.json" || true
)

# ------------------------------------------------------------------
# Step 5 — Route & page inventory
# ------------------------------------------------------------------
echo "[Step 5/7] Building frontend route & page inventory..."
node scripts/frontend_inventory.mjs > "$REPORT_DIR/frontend_routes.json"

# ------------------------------------------------------------------
# Step 6 — Frontend -> backend API contract check
# ------------------------------------------------------------------
echo "[Step 6/7] Running frontend -> backend API contract check..."
python3 scripts/frontend_api_contract_check.py --routes "$REPORT_DIR/routes.csv" --json-out "$REPORT_DIR/frontend_api_contract.json"

# ------------------------------------------------------------------
# Step 7 — Playwright browser smoke against the shipped image
# ------------------------------------------------------------------
echo "[Step 7/7] Running Playwright headless browser smoke (dashboard-next/)..."
(
  cd "$ROOT_DIR/dashboard-next"
  npx playwright test e2e/shipped_image.spec.ts
)

# ------------------------------------------------------------------
# Step 8 — Operator journeys and the accessibility gate
#
# Both suites need a running front door. They are opt-in through
# E2E_BASE_URL so the contract checks above still run on a machine with no
# servers, and they are not skipped silently: if E2E_BASE_URL is set, a failure
# here fails the check.
#
# The console half of the accessibility gate is sliced in two because a single
# process auditing 56 authenticated pages on a small box gets the API killed by
# the OOM reaper mid-run — which then looks like 28 accessibility failures.
# ------------------------------------------------------------------
if [ -n "${E2E_BASE_URL:-}" ]; then
  echo "[Step 8/8] Running operator journeys + accessibility gate against $E2E_BASE_URL..."
  (
    cd "$ROOT_DIR/dashboard-next"
    export PLAYWRIGHT_BASE_URL="$E2E_BASE_URL"
    npx playwright test e2e/journeys.spec.ts
    npx playwright test e2e/a11y.spec.ts -g "public site"
    A11Y_SHARD=1/2 npx playwright test e2e/a11y.spec.ts -g "operator console"
    A11Y_SHARD=2/2 npx playwright test e2e/a11y.spec.ts -g "operator console"
    npx playwright test e2e/a11y.spec.ts -g "coverage of the accessibility gate"
  )
else
  echo "[Step 8/8] Skipped operator journeys + accessibility gate (set E2E_BASE_URL to enable)."
fi

echo "=================================================================="
echo "SELL CHECK 2/4 — All 7 steps completed."
echo "=================================================================="
