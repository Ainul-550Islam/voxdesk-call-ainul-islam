#!/usr/bin/env bash
# =============================================================================
# Keep the chart's copy of the Prometheus rules identical to the canonical file.
#
# `observability/alerts.yml` is mounted into Prometheus by docker-compose and is
# the file the docs point at. Helm cannot read a path outside the chart, so the
# chart carries a copy at `infra/helm/voxdesk/alerts.yml`, loaded through
# `.Files.Get`. A copy drifts; this script is the thing that stops it, and
# `tests/deployment/test_helm_chart_completeness.py` asserts the equality on
# every commit so CI catches a hand-edit that skipped this script.
#
#   bash scripts/sync_chart_alerts.sh          # copy canonical -> chart
#   bash scripts/sync_chart_alerts.sh --check  # exit 1 if they differ
# =============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CANONICAL="$REPO_ROOT/observability/alerts.yml"
COPY="$REPO_ROOT/infra/helm/voxdesk/alerts.yml"
MODE="${1:-sync}"

if [ ! -f "$CANONICAL" ]; then
  echo "error: missing $CANONICAL" >&2
  exit 2
fi

case "$MODE" in
  --check)
    if cmp -s "$CANONICAL" "$COPY"; then
      echo "chart alerts are in sync with observability/alerts.yml"
      exit 0
    fi
    echo "error: infra/helm/voxdesk/alerts.yml has drifted from observability/alerts.yml" >&2
    diff -u "$COPY" "$CANONICAL" | head -40 >&2 || true
    echo "run: bash scripts/sync_chart_alerts.sh" >&2
    exit 1
    ;;
  sync)
    install -m 644 "$CANONICAL" "$COPY"
    echo "copied observability/alerts.yml -> infra/helm/voxdesk/alerts.yml ($(wc -l < "$COPY") lines)"
    ;;
  *)
    echo "usage: $0 [--check]" >&2
    exit 2
    ;;
esac
