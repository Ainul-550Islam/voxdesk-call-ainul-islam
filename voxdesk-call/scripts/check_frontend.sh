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
# Step 1 — Shipped UI: dashboard/ (Vite + React 18)
# ------------------------------------------------------------------
echo "[Step 1/7] Checking shipped UI: dashboard/ (Vite)..."
(
  cd "$ROOT_DIR/dashboard"
  if [ ! -d node_modules ]; then
    npm ci
  fi
  npx tsc --noEmit
  npm test -- --reporter=json --outputFile="$REPORT_DIR/dashboard_vitest.json"
  npm run build
)

# ------------------------------------------------------------------
# Step 2 — Next.js UI: dashboard-next/
# ------------------------------------------------------------------
echo "[Step 2/7] Checking shadow/roadmap UI: dashboard-next/ (Next.js)..."
(
  cd "$ROOT_DIR/dashboard-next"
  if [ ! -d node_modules ]; then
    npm ci
  fi
  npx tsc --noEmit
  npm test -- --reporter=json --outputFile="$REPORT_DIR/dashboard_next_vitest.json"
  npm run build
)

# ------------------------------------------------------------------
# Step 3 — Generated-tail & fake-verified scan
# ------------------------------------------------------------------
echo "[Step 3/7] Running generated-tail & verified:true/real:true scan..."
python3 scripts/generated_tail_scan.py dashboard/src --json-out "$REPORT_DIR/generated_tail_scan.json"
python3 scripts/strip_generated_tails.py --check dashboard/src > "$REPORT_DIR/strip_generated_tails_check.json"

# ------------------------------------------------------------------
# Step 4 — Dependency vulnerability audit (npm audit)
# ------------------------------------------------------------------
echo "[Step 4/7] Running npm audit on dashboard/ and dashboard-next/..."
(
  cd "$ROOT_DIR/dashboard"
  npm audit --omit=dev --audit-level=high --json > "$REPORT_DIR/dashboard_audit_prod.json" || true
  npm audit --json > "$REPORT_DIR/dashboard_audit_full.json" || true
)
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
# Step 7 — Playwright browser smoke
# ------------------------------------------------------------------
echo "[Step 7/7] Running Playwright headless browser smoke (dashboard/)..."
(
  cd "$ROOT_DIR/dashboard"
  npm run e2e
)

echo "=================================================================="
echo "SELL CHECK 2/4 — All 7 steps completed."
echo "=================================================================="
