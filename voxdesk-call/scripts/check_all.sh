#!/usr/bin/env bash
# ==============================================================================
# VoxDesk — Master Verification Orchestrator (scripts/check_all.sh)
# Runs SELL CHECK 1 (backend), SELL CHECK 2 (frontend), SELL CHECK 3 (docker,
# if Docker is available), and SELL CHECK 4 (other) in order, and ALWAYS runs
# scripts/check_summary.py at the end even if an earlier step failed.
# Pass --summarize-only to regenerate reports/check/{SUMMARY.md,summary.json}
# from existing check artifacts without re-executing the 4 suites.
# ==============================================================================
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

OVERALL_RC=0

run_summary() {
  echo "[Summary] Aggregating CHECK 1-4 into reports/check/SUMMARY.md and reports/check/summary.json..."
  python3 scripts/check_summary.py || OVERALL_RC=1
}
trap run_summary EXIT

if [ "${1:-}" = "--summarize-only" ]; then
  exit 0
fi

echo "=================================================================="
echo "VoxDesk — Running Full Check Suite (CHECK 1 -> 2 -> 3 -> 4)"
echo "=================================================================="

echo "[1/4] Running SELL CHECK 1 (Backend)..."
bash scripts/check_backend.sh || OVERALL_RC=1

echo "[2/4] Running SELL CHECK 2 (Frontend)..."
bash scripts/check_frontend.sh || OVERALL_RC=1

if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  echo "[3/4] Running SELL CHECK 3 (Docker Run)..."
  bash scripts/check_docker.sh || OVERALL_RC=1
else
  echo "[3/4] SKIPPED SELL CHECK 3 (Docker daemon unavailable in current environment)"
fi

echo "[4/4] Running SELL CHECK 4 (Other Checks)..."
bash scripts/check_other.sh || OVERALL_RC=1

exit "$OVERALL_RC"
