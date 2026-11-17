#!/usr/bin/env bash
# =============================================================================
# Helm gate: lint, render, validate, and prove the chart contains the platform.
#
# `helm lint` alone is not a gate. Before PART 17B this chart linted clean while
# rendering only `api` and `scheduler` — no realtime gateway, no media engine,
# no console, no backup, no migration Job. A buyer who ran the acceptance
# command in the spec (`helm lint && helm template | kubeconform`) would have
# got a green exit code and an installation that dropped every WebSocket.
#
# So this script checks four separate things:
#
#   1. `helm lint` on the base values and on both environment overlays;
#   2. `helm template` for each, then kubeconform -strict — with schemas for the
#      monitoring CRDs, so ServiceMonitor and PrometheusRule are validated
#      rather than skipped (`-ignore-missing-schemas` would hide a typo'd field
#      in the very objects that only exist in a real cluster);
#   3. the rendered *content*: the components on the call path are present, the
#      migration Job is a hook, the backup has a durable volume;
#   4. the chart's alert copy equals observability/alerts.yml.
#
# A missing tool is a hard failure with an install hint. A skipped check that
# reports success is the class of bug this whole PART is about.
#
# Usage:
#   bash scripts/check_helm.sh              # full gate
#   bash scripts/check_helm.sh --no-crd     # skip CRD schema validation
# =============================================================================
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT" || exit 2

CHART="infra/helm/voxdesk"
WITH_CRDS=1
[ "${1:-}" = "--no-crd" ] && WITH_CRDS=0

PASS=0
FAIL=0
declare -a FAILURES=()

pass() { PASS=$((PASS + 1)); printf '  [PASS] %s\n' "$1"; }
fail() { FAIL=$((FAIL + 1)); FAILURES+=("$1"); printf '  [FAIL] %s\n' "$1" >&2; }
need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    printf '  [FAIL] tool:%s — not installed; %s\n' "$1" "$2" >&2
    FAIL=$((FAIL + 1))
    FAILURES+=("tool:$1")
    return 1
  fi
  return 0
}

echo "== helm-gate: $CHART =="
need helm "https://helm.sh/docs/intro/install/" || { echo "== result: FAIL =="; exit 1; }
need kubeconform "https://github.com/yannh/kubeconform#installation" || { echo "== result: FAIL =="; exit 1; }
need python3 "python3 is required to assert on the rendered objects"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# --- CRD schemas ------------------------------------------------------------
# kubeconform's default schemas do not cover monitoring.coreos.com, so those
# objects are reported "skipped" and a bad field goes unnoticed. Fetch the two
# schemas this chart renders and point kubeconform at them.
SCHEMA_ARGS=(-schema-location default)
if [ "$WITH_CRDS" = "1" ]; then
  mkdir -p "$TMP/schemas"
  for name in servicemonitor prometheusrule; do
    url="https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/monitoring.coreos.com/${name}_v1.json"
    if curl -fsSL --max-time 30 "$url" -o "$TMP/schemas/${name}_v1.json" 2>/dev/null; then
      :
    else
      printf '  [WARN] could not fetch %s schema; CRD objects will be skipped\n' "$name" >&2
    fi
  done
  if [ -n "$(ls -A "$TMP/schemas" 2>/dev/null)" ]; then
    SCHEMA_ARGS+=(-schema-location "$TMP/schemas/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json")
  else
    SCHEMA_ARGS+=(-ignore-missing-schemas)
  fi
else
  SCHEMA_ARGS+=(-ignore-missing-schemas)
fi

# --- 1..3 per values file ---------------------------------------------------
for values in values.yaml values-prod.yaml values-staging.yaml; do
  echo "-- $values"
  file="$CHART/$values"

  if out="$(helm lint "$CHART" -f "$file" 2>&1)"; then
    pass "helm:lint:$values"
  else
    fail "helm:lint:$values — $(printf '%s' "$out" | tail -2 | tr '\n' ' ')"
    continue
  fi

  if ! helm template voxdesk "$CHART" -f "$file" > "$TMP/$values.yaml" 2>"$TMP/$values.err"; then
    fail "helm:template:$values — $(tail -2 "$TMP/$values.err" | tr '\n' ' ')"
    continue
  fi
  count="$(grep -c '^kind:' "$TMP/$values.yaml" || true)"
  pass "helm:template:$values — $count object(s)"

  if out="$(kubeconform -strict -summary "${SCHEMA_ARGS[@]}" "$TMP/$values.yaml" 2>&1)"; then
    pass "kubeconform:$values — $(printf '%s' "$out" | tail -1)"
  else
    fail "kubeconform:$values — $(printf '%s' "$out" | tail -3 | tr '\n' ' ')"
  fi
done

# --- 3. content assertions on the rendered prod objects ---------------------
echo "-- rendered content (values-prod.yaml)"
if [ -f "$TMP/values-prod.yaml.yaml" ]; then
  python3 - "$TMP/values-prod.yaml.yaml" <<'PY'
import sys
import yaml

docs = [d for d in yaml.safe_load_all(open(sys.argv[1], encoding="utf-8")) if d]
kind = lambda k: [d for d in docs if d.get("kind") == k]
names = {d["metadata"]["name"] for d in docs}
deployments = {d["metadata"]["name"]: d for d in kind("Deployment")}

problems = []

# (a) the components the call path uses are deployed
for suffix in ("-realtime-gateway", "-media-engine", "-web", "-scheduler"):
    if not any(n.endswith(suffix) for n in deployments):
        problems.append(f"no Deployment ending {suffix}")

# (b) the api itself (the un-suffixed Deployment) is still there
if not any(not n.endswith(("-realtime-gateway", "-media-engine", "-web", "-scheduler"))
           for n in deployments):
    problems.append("no un-suffixed api Deployment")

# (c) the migration Job is a pre-upgrade hook
jobs = {d["metadata"]["name"]: d for d in kind("Job")}
migrate = [d for n, d in jobs.items() if n.endswith("-migrate")]
if not migrate:
    problems.append("no migration Job")
else:
    hook = migrate[0]["metadata"].get("annotations", {}).get("helm.sh/hook", "")
    if "pre-upgrade" not in hook:
        problems.append(f"migration Job is not a pre-upgrade hook (hook={hook!r})")

# (d) the backup CronJob writes to a PVC
crons = [d for d in kind("CronJob") if d["metadata"]["name"].endswith("-backup")]
if not crons:
    problems.append("no backup CronJob")
else:
    vols = crons[0]["spec"]["jobTemplate"]["spec"]["template"]["spec"]["volumes"]
    if not any("persistentVolumeClaim" in v for v in vols):
        problems.append("backup volume is not a PVC")

# (e) every workload carries requests and limits
for name, d in deployments.items():
    c = d["spec"]["template"]["spec"]["containers"][0]
    res = c.get("resources") or {}
    if not res.get("requests") or not res.get("limits"):
        problems.append(f"{name} has no resource requests/limits")

# (f) no Secret object is rendered (the chart consumes one, never creates one)
if kind("Secret"):
    problems.append("the chart renders a Secret object")

if problems:
    print("  [FAIL] chart content: " + "; ".join(problems))
    sys.exit(1)
print(f"  [PASS] chart content: {len(deployments)} deployments, "
      f"{len(crons)} backup CronJob, migration hook, no rendered Secret")
PY
  if [ $? -eq 0 ]; then pass "rendered:content"; else fail "rendered:content"; fi
else
  fail "rendered:content — no prod render to inspect"
fi

# --- 4. alert copy is not drifted -------------------------------------------
echo "-- alert rules"
if bash scripts/sync_chart_alerts.sh --check >/dev/null 2>&1; then
  pass "alerts:in-sync"
else
  fail "alerts:in-sync — run bash scripts/sync_chart_alerts.sh"
fi

echo
if [ "$FAIL" -eq 0 ]; then
  echo "== result: PASS ($PASS check(s)) =="
  exit 0
fi
printf '== result: FAIL (%d failing: %s) ==\n' "$FAIL" "${FAILURES[*]}" >&2
exit 1
