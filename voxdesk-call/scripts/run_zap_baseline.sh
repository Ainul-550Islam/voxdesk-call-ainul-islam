#!/usr/bin/env bash
# OWASP ZAP baseline against a running VoxDesk API (PART 16, P16-04).
#
#   bash scripts/run_zap_baseline.sh [target-url]
#
# The baseline scan is *passive with a light spider*: it crawls what it is given,
# then reports what it observed. That is the right shape for us because the
# interesting parts of this API refuse anonymous access, so an aggressive active
# scan against a production-shaped stack mostly generates 401s. It is a
# regression net for headers, cookies, TLS-ish configuration, information
# leakage and the odd unauthenticated route — not a substitute for the
# authenticated manual test in docs/COMPLIANCE/PENTEST_SCOPE_AND_TEMPLATE.md.
#
# Exit codes, and why they matter to CI:
#   0  scan ran, no findings at or above the threshold
#   1  scan ran, findings at or above FAIL_ON were reported
#   2  the scan could not run (no ZAP, no target). NOT "passed".
#
# A scan that could not run is never reported as clean. If you see exit 2 in CI,
# the job must fail; the alternative is a green badge with no scan behind it.
set -uo pipefail

TARGET="${1:-${ZAP_TARGET:-http://127.0.0.1:8000}}"
REPORT_DIR="${ZAP_REPORT_DIR:-reports/dast}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
FAIL_ON="${ZAP_FAIL_ON:-High}"          # High | Medium | Low | Informational
SLUG="$(printf '%s' "${TARGET}" | sed 's#^https\?://##; s#[/:]#_#g')"
JSON_REPORT="${REPORT_DIR}/zap-baseline-${SLUG}-${STAMP}.json"
MD_REPORT="${REPORT_DIR}/zap-baseline-${SLUG}-${STAMP}.md"
LOG="${REPORT_DIR}/zap-baseline-${SLUG}-${STAMP}.log"

mkdir -p "${REPORT_DIR}"

note() { printf '%s\n' "$*"; }
refuse() { note "DAST NOT RUN: $*" >&2; note "DAST NOT RUN: $*" > "${REPORT_DIR}/LAST_RUN.txt"; exit 2; }

# --- prerequisites -----------------------------------------------------------
ZAP_CMD=""
if command -v zap-baseline.py >/dev/null 2>&1; then
  ZAP_CMD="zap-baseline.py"
elif command -v docker >/dev/null 2>&1; then
  ZAP_CMD="docker"
elif [ -n "${ZAP_HOME:-}" ] && [ -x "${ZAP_HOME}/zap.sh" ]; then
  ZAP_CMD="${ZAP_HOME}/zap.sh"
elif [ -x /home/user/ZAP_2.14.0/zap.sh ]; then
  ZAP_CMD="/home/user/ZAP_2.14.0/zap.sh"
fi
[ -n "${ZAP_CMD}" ] || refuse "no ZAP found (install zaproxy, docker, or set ZAP_HOME)"

# The target has to answer, or the scan reports an empty, useless pass.
if ! curl -fsS -m 5 -o /dev/null "${TARGET}/health" 2>/dev/null \
   && ! curl -fsS -m 5 -o /dev/null "${TARGET}/" 2>/dev/null; then
  refuse "target ${TARGET} is not answering"
fi

note "ZAP baseline: target=${TARGET} report=${JSON_REPORT} fail_on=${FAIL_ON}"

# --- run ---------------------------------------------------------------------
RC=0
SCAN_MODE="zap-baseline.py (passive)"
if [ "${ZAP_CMD}" = "docker" ]; then
  # -I = do not fail on warnings, -J/-w = machine + human report, -r = markdown.
  docker run --rm --network host \
    -v "$(pwd)/${REPORT_DIR}:/zap/wrk:rw" \
    ghcr.io/zaproxy/zaproxy:stable zap-baseline.py \
      -t "${TARGET}" -J "$(basename "${JSON_REPORT}")" -w "$(basename "${MD_REPORT}")" \
      -r "$(basename "${LOG}")" -I >/dev/null 2>&1 || RC=$?
elif [ "${ZAP_CMD}" = "zap-baseline.py" ]; then
  ( cd "${REPORT_DIR}" && zap-baseline.py -t "${TARGET}" \
      -J "$(basename "${JSON_REPORT}")" -w "$(basename "${MD_REPORT}")" \
      -r "$(basename "${LOG}")" -I ) >/dev/null 2>&1 || RC=$?
else
  # A plain ZAP install has no baseline script. `-quickurl` runs the traditional
  # spider and then an **active** scan with the default policy, which is more than
  # a baseline: acceptable only because the target is our own stack, and stated in
  # the report header so nobody mistakes this for a passive-only run.
  # `-quickout` resolves relative to the ZAP install directory, so it must be
  # absolute or the report silently lands nowhere.
  SCAN_MODE="spider+active (zap -quickurl)"
  "${ZAP_CMD}" -cmd -quickurl "${TARGET}" -quickout "$(cd "$(dirname "${JSON_REPORT}")" && pwd)/$(basename "${JSON_REPORT}")" \
      -quickprogress > "${LOG}" 2>&1 || RC=$?
fi

if [ ! -s "${JSON_REPORT}" ]; then
  refuse "ZAP produced no report (exit ${RC}); see ${LOG}"
fi

# --- evaluate ----------------------------------------------------------------
# The pass/fail decision is made from the report, not from ZAP's exit code: ZAP
# exits non-zero for warnings too, and a warning is not a ticket.
python3 - "${JSON_REPORT}" "${FAIL_ON}" <<'PY'
import json
import sys

path, threshold = sys.argv[1], sys.argv[2]
order = {"Informational": 0, "Low": 1, "Medium": 2, "High": 3}
rank = order[threshold]

with open(path, encoding="utf-8") as handle:
    report = json.load(handle)

alerts = []
site = report.get("site") or []
for entry in site:
    for alert in entry.get("alerts", []):
        risk = alert.get("riskdesc", "").split(" ")[0] or alert.get("riskcode", "")
        alerts.append((risk, alert.get("name", "?"), alert.get("count", "?"), alert.get("pluginid", "?")))

blocking = [alert for alert in alerts if order.get(alert[0], 0) >= rank and rank > 0]
print(f"alerts: {len(alerts)} total, {len(blocking)} at or above {threshold}")
for risk, name, count, plugin in sorted(alerts, key=lambda row: -order.get(row[0], 0)):
    print(f"  [{risk}] {name} (plugin {plugin}, {count} instance(s))")
sys.exit(1 if blocking else 0)
PY
STATUS=$?

{
  echo "target: ${TARGET}"
  echo "finished: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "report: ${JSON_REPORT}"
  echo "mode: ${SCAN_MODE}"
  echo "fail_on: ${FAIL_ON}"
  echo "exit: ${STATUS}"
  echo "note: unauthenticated surface only; authenticated manual testing is out of scope"
} > "${REPORT_DIR}/LAST_RUN.txt"

if [ "${STATUS}" -ne 0 ]; then
  note "DAST FAILED: findings at or above ${FAIL_ON}. Open a ticket for each before shipping." >&2
  exit 1
fi
note "DAST passed: no findings at or above ${FAIL_ON}."
exit 0
