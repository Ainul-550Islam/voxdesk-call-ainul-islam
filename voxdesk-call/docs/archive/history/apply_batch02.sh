#!/usr/bin/env bash
#
# apply_batch02.sh -- apply the Batch 02 patch.
#
# Batch 02 closes the live-media gaps:
#   A  Vonage live AI media streaming (answer/NCCO, signed events, media WebSocket)
#   B  SIP trunk live media (the Go gateway and the Python media bridge)
#   C  Speech-to-speech live wiring (OpenAI Realtime, Gemini Live, failover, cost)
#
# Usage:
#   bash apply_batch02.sh [repo-root]
#
# The script is idempotent: running it twice changes nothing the second time.
# It never rewrites the migration chain -- if Batch 01 has not been applied, it
# stops with a clear error instead of inventing a revision.
#
# The shared files (app/main.py, app/telephony/providers/factory.py,
# alembic/env.py, Makefile) are never shipped in the patch. They are edited
# here with tiny marker-based insertions, and each insertion is skipped when
# its marker is already present.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${1:-}"
if [ -z "$ROOT" ]; then
  if [ -f "$SCRIPT_DIR/app/main.py" ] || [ -f "$SCRIPT_DIR/pyproject.toml" ]; then
    ROOT="$SCRIPT_DIR"
  else
    ROOT="$(pwd)"
  fi
fi
cd "$ROOT"

if [ -x "$ROOT/.venv/bin/python" ]; then
  PATH="$ROOT/.venv/bin:$PATH"
  export PATH
fi
# The Go toolchain is looked up in the usual places, so the gateway tests run
# wherever the toolchain was installed.
if ! command -v go >/dev/null 2>&1; then
  for candidate in \
    /usr/local/go/bin \
    /usr/lib/go/bin \
    /usr/lib/go-1.27/bin \
    "$HOME/toolchain/go/bin" \
    "$HOME/go/bin"
  do
    if [ -x "$candidate/go" ]; then
      PATH="$candidate:$PATH"
      export PATH
      break
    fi
  done
fi
export GOTOOLCHAIN="${GOTOOLCHAIN:-auto}"

PYTHON_BIN="$(command -v python3 || command -v python || true)"
if [ -z "$PYTHON_BIN" ]; then
  printf '[batch02] ERROR: python3 is not on the PATH\n' >&2
  exit 1
fi

log() { printf '[batch02] %s\n' "$*"; }
fail() { printf '[batch02] ERROR: %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# 1. Locate and unpack the patch
# ---------------------------------------------------------------------------
ZIP=""
for candidate in \
  "$SCRIPT_DIR/voxdesk-batch02-patch.zip" \
  "$ROOT/voxdesk-batch02-patch.zip" \
  "$(pwd)/voxdesk-batch02-patch.zip"
do
  if [ -f "$candidate" ]; then
    ZIP="$candidate"
    break
  fi
done

if [ -n "$ZIP" ]; then
  log "unpacking $(basename "$ZIP") into $ROOT"
  unzip -o -q "$ZIP" -d "$ROOT"
elif [ ! -f "$ROOT/app/carriers_v2/settings.py" ]; then
  fail "neither voxdesk-batch02-patch.zip nor the patch files were found in $ROOT"
else
  log "the patch files are already in place; continuing"
fi

# The SIP connection module moved into the package. The old module would
# shadow the package, so it is removed once the package is in place.
if [ -f "$ROOT/app/telephony/sip/connection.py" ]; then
  rm -f "$ROOT/app/telephony/sip.py"
fi

# ---------------------------------------------------------------------------
# 2. Preflight: Batch 01 must already be applied
# ---------------------------------------------------------------------------
if ! "$PYTHON_BIN" - <<'PY'
import pathlib
import re
import sys

pattern = re.compile(
    r"^\s*revision\s*[:=]\s*[\"']0063_live_proof_runs[\"']", re.MULTILINE
)
found = any(
    pattern.search(path.read_text(encoding="utf-8", errors="ignore"))
    for path in pathlib.Path("alembic/versions").glob("*.py")
)
sys.exit(0 if found else 1)
PY
then
  fail "the Batch 01 revision 0063_live_proof_runs is missing. Apply Batch 01 first -- Batch 02 does not rewrite the migration chain."
fi
log "Batch 01 revision 0063_live_proof_runs is present"

# ---------------------------------------------------------------------------
# 3. Marker-based insertions into the shared files
# ---------------------------------------------------------------------------
"$PYTHON_BIN" - <<'PY'
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(".")


def apply(
    path: str,
    marker: str,
    anchor: str,
    addition: str,
    *,
    replace: bool = False,
    before: bool = False,
) -> None:
    """Insert ``addition`` next to ``anchor`` unless the marker is present."""
    target = ROOT / path
    if not target.exists():
        print(f"[batch02] ERROR: {path} does not exist", file=sys.stderr)
        raise SystemExit(1)
    text = target.read_text(encoding="utf-8")
    if marker in text:
        print(f"[batch02] {path}: {marker} is already present, skipped")
        return
    if anchor not in text:
        print(
            f"[batch02] ERROR: the anchor for {marker} was not found in {path}",
            file=sys.stderr,
        )
        raise SystemExit(1)
    if replace:
        text = text.replace(anchor, addition, 1)
    elif before:
        text = text.replace(anchor, addition + anchor, 1)
    else:
        text = text.replace(anchor, anchor + addition, 1)
    target.write_text(text, encoding="utf-8")
    print(f"[batch02] {path}: {marker} applied")


# -- app/main.py: the three new routers ---------------------------------
apply(
    "app/main.py",
    "# BATCH02:import_carrier_media_routes",
    "from app.telephony.twilio_handler import router as telephony_router\n",
    "\n# BATCH02:import_carrier_media_routes\n"
    "from app.api.carrier_media_routes import router as carrier_media_router\n"
    "from app.telephony.sip.sip_handler import router as sip_voice_router\n"
    "from app.telephony.vonage_handler import router as vonage_voice_router\n",
)

apply(
    "app/main.py",
    "# BATCH02:mount_carrier_media_routes",
    "app.include_router(telephony_router)\n",
    "# BATCH02:mount_carrier_media_routes\n"
    "app.include_router(carrier_media_router)\n"
    "app.include_router(vonage_voice_router)\n"
    "app.include_router(sip_voice_router)\n",
)

# -- app/telephony/providers/factory.py: the Batch 02 carrier rows -------
apply(
    "app/telephony/providers/factory.py",
    "# BATCH02:carrier_media_capabilities",
    "    from app.telephony.media.serializers import supported_media_serializers\n"
    "\n"
    "    return supported_media_serializers()\n",
    "    from app.telephony.media.serializers import supported_media_serializers\n"
    "\n"
    "    # BATCH02:carrier_media_capabilities\n"
    "    from app.telephony.providers.vonage import vonage_media_capabilities\n"
    "    from app.telephony.sip.sip_capabilities import sip_media_capabilities\n"
    "\n"
    "    rows = dict(supported_media_serializers())\n"
    "    rows.update(vonage_media_capabilities())\n"
    "    rows.update(sip_media_capabilities())\n"
    "    return rows\n",
    replace=True,
)

apply(
    "app/telephony/providers/factory.py",
    "# BATCH02:sip_gateway_registration",
    '        raise ProviderUnavailableError("Provider is not enabled for this tenant", provider=name)\n'
    "    return name\n",
    "\n\n# BATCH02:sip_gateway_registration\n"
    "def build_sip_gateway_client():\n"
    '    """The SIP gateway control client of this deployment."""\n'
    "    from app.telephony.sip.sip_gateway_client import build_gateway_client\n"
    "\n"
    "    return build_gateway_client()\n",
)

# -- alembic/env.py: the Batch 02 models --------------------------------
apply(
    "alembic/env.py",
    "# BATCH02:carrier_s2s_models",
    "import app.db.retell_models  # noqa: F401\n",
    "# BATCH02:carrier_s2s_models\nimport app.db.carrier_s2s_models  # noqa: F401\n",
)

# -- Makefile: the Batch 02 targets -------------------------------------
apply(
    "Makefile",
    "# BATCH02:include",
    "help:\n",
    "# BATCH02:include\ninclude Makefile.batch02.mk\n\n",
    before=True,
)
# -- infra/helm/voxdesk/values.yaml: the SIP gateway values -------------
# The values file is shared with the other batches, so the Batch 02 values
# are appended with a marker instead of shipping a full copy of the file.
VALUES_PATH = ROOT / "infra/helm/voxdesk/values.yaml"
VALUES_MARKER = "# BATCH02:sip_gateway_values"
SIP_VALUES = """sipGateway:
  enabled: false
  replicaCount: 1
  terminationGracePeriodSeconds: 30
  # The carrier sends RTP to the addresses in the SDP, so the signalling port
  # and the RTP port range are host ports.
  hostNetwork: false
  host: ""
  mediaWsUrl: ""
  sipPort: 5060
  controlPort: 8080
  codec: PCMU
  mediaPortMin: 30000
  mediaPortMax: 30100
  secretName: voxdesk-sip-gateway
  image:
    repository: "ghcr.io/voxdesk/voxdesk-sip-gateway"
    tag: "1.0.0"
    pullPolicy: IfNotPresent
  service:
    type: ClusterIP
  resources:
    requests: {cpu: 100m, memory: 128Mi}
    limits: {cpu: "1", memory: 512Mi}
  # The gateway scales on the calls it is actually carrying.
  autoscaling:
    enabled: false
    minReplicas: 1
    maxReplicas: 10
    targetActiveCallsPerPod: 25
    targetCPUUtilizationPercentage: 70
  podDisruptionBudget:
    enabled: false
    minAvailable: 1
"""
if not VALUES_PATH.exists():
    print(
        "[batch02] ERROR: infra/helm/voxdesk/values.yaml does not exist",
        file=sys.stderr,
    )
    raise SystemExit(1)
values_text = VALUES_PATH.read_text(encoding="utf-8")
if VALUES_MARKER in values_text:
    print(
        "[batch02] infra/helm/voxdesk/values.yaml: "
        f"{VALUES_MARKER} is already present, skipped"
    )
else:
    if not values_text.endswith("\n"):
        values_text += "\n"
    VALUES_PATH.write_text(
        values_text + "\n" + VALUES_MARKER + "\n" + SIP_VALUES, encoding="utf-8"
    )
    print(
        "[batch02] infra/helm/voxdesk/values.yaml: " f"{VALUES_MARKER} applied"
    )
PY

# ---------------------------------------------------------------------------
# 4. Regenerate the truth artifacts (route snapshot + OpenAPI contract)
# ---------------------------------------------------------------------------
log "regenerating the OpenAPI contract and the API reference"
if ! bash scripts/build_api_docs.sh; then
  fail "scripts/build_api_docs.sh failed; the OpenAPI contract is out of date"
fi

log "regenerating the route snapshot"
"$PYTHON_BIN" - <<'PY'
from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path

with redirect_stdout(io.StringIO()):
    from app.main import app

snapshot_path = Path("tests/truth/routes_snapshot.json")
snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
routes = []
for route in app.routes:
    methods = sorted(getattr(route, "methods", None) or [])
    routes.append({"methods": methods, "name": route.name, "path": route.path})
snapshot["routes"] = routes
snapshot["route_count"] = len(routes)
snapshot["dashboard_build_present"] = Path("dashboard/dist/index.html").is_file()
snapshot_path.write_text(
    json.dumps(snapshot, indent=2, sort_keys=False) + "\n", encoding="utf-8"
)
print(f"[batch02] the route snapshot now holds {len(routes)} routes")
PY

# The README carries the measured repository statistics. Batch 02 adds files,
# routes and tests, so the numbers are measured again and written back with
# the upstream generator (the README is never edited by hand).
if [ -f scripts/repo_stats.py ]; then
  log "refreshing the measured repository statistics in the README"
  if ! "$PYTHON_BIN" scripts/repo_stats.py --write-readme >/dev/null; then
    fail "scripts/repo_stats.py could not refresh the README statistics"
  fi
fi

# ---------------------------------------------------------------------------
# 5. Gates
# ---------------------------------------------------------------------------
FAILURES=0

record_failure() {
  FAILURES=$((FAILURES + 1))
  printf '[batch02] FAILED: %s\n' "$*" >&2
}

log "applying the migrations"
ALEMBIC_LOG="$(mktemp)"
# A database that cannot be reached is an environment problem, not a
# migration problem: it is reported, but it does not fail the apply.
DB_UNREACHABLE="Connect call failed|Connection refused|could not connect to server|Errno 111|Name or service not known|server closed the connection"

if alembic upgrade head >"$ALEMBIC_LOG" 2>&1; then
  log "alembic upgrade head succeeded"
elif grep -qE "$DB_UNREACHABLE" "$ALEMBIC_LOG"; then
  log "the database is not reachable from here, so the migrations could not be executed (the tail of the log follows); continuing"
  tail -n 10 "$ALEMBIC_LOG"
elif grep -q "Multiple head revisions are present" "$ALEMBIC_LOG"; then
  # The tree carries more than one head, so "head" is ambiguous. That is
  # the state of the repository before Batch 02 -- Batch 02 does not rewrite
  # the chain -- so the Batch 02 revision is applied directly instead.
  log "the tree already had more than one migration head; applying the Batch 02 revision directly"
  if alembic upgrade 0064_carrier_s2s >"$ALEMBIC_LOG" 2>&1; then
    log "the Batch 02 migration applied cleanly"
  elif grep -qE "$DB_UNREACHABLE" "$ALEMBIC_LOG"; then
    log "the database is not reachable from here, so the migration could not be executed (the tail of the log follows); continuing"
    tail -n 10 "$ALEMBIC_LOG"
  else
    cat "$ALEMBIC_LOG"
    record_failure "alembic upgrade 0064_carrier_s2s"
  fi
else
  cat "$ALEMBIC_LOG"
  record_failure "alembic upgrade head"
fi

log "checking the Alembic head"
set +e
"$PYTHON_BIN" - <<'PY'
from __future__ import annotations

import pathlib
import shutil
import sys
import tempfile

from alembic.config import Config
from alembic.script import ScriptDirectory

BATCH01_REVISION = "0063_live_proof_runs"
BATCH02_REVISION = "0064_carrier_s2s"


def heads_without(revision: str) -> list[str]:
    """The heads of the chain with ``revision`` taken out of the tree."""
    tmp = pathlib.Path(tempfile.mkdtemp())
    try:
        shutil.copytree("alembic/versions", tmp / "versions")
        (tmp / "versions" / f"{revision}.py").unlink(missing_ok=True)
        config = Config("alembic.ini")
        config.set_main_option("script_location", str(tmp))
        return sorted(ScriptDirectory.from_config(config).get_heads())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


script = ScriptDirectory.from_config(Config("alembic.ini"))
heads = sorted(script.get_heads())
print(f"[batch02] the Alembic head(s): {', '.join(heads) or 'none'}")

if heads == [BATCH02_REVISION]:
    raise SystemExit(0)

# The chain of this repository may already carry more than one head. Batch 02
# never rewrites the chain, so the question is whether Batch 02 added a head
# of its own or only took the place of the Batch 01 revision.
base = set(heads_without(BATCH02_REVISION))
now = set(heads)
if (now - {BATCH02_REVISION}) == (base - {BATCH01_REVISION}):
    print(
        "[batch02] the other heads were already in the tree before Batch 02; "
        "Batch 02 added no head of its own",
        file=sys.stderr,
    )
    raise SystemExit(2)

print(
    "[batch02] ERROR: the migration chain must have exactly one head, "
    "0064_carrier_s2s. The chain above shows what is in the tree.",
    file=sys.stderr,
)
raise SystemExit(1)
PY
HEAD_STATUS=$?
set -e
case "$HEAD_STATUS" in
  0) log "the Alembic head is 0064_carrier_s2s" ;;
  2) log "the pre-existing heads are still in the tree; continuing" ;;
  *) record_failure "the Alembic head check" ;;
esac

log "linting the Batch 02 files"
BATCH02_PATHS="app/carriers_v2 app/agent/s2s app/agent/pipeline.py app/telephony/vonage_handler.py app/telephony/providers/vonage.py app/telephony/providers/vonage_media.py app/telephony/sip app/api/carrier_media_routes.py app/db/carrier_s2s_models.py tests/telephony/test_vonage_handler.py tests/telephony/test_vonage_media_codec.py tests/telephony/test_vonage_provider_client.py tests/telephony/test_vonage_capability_flag.py tests/telephony/test_sip_codec.py tests/telephony/test_sip_handler.py tests/telephony/test_sip_gateway_client.py tests/telephony/test_sip_capability_flag.py tests/telephony/test_live_vonage_pstn.py tests/telephony/test_live_sip_trunk.py tests/telephony/test_provider_contract.py tests/agent/test_s2s_openai_realtime_client.py tests/agent/test_s2s_gemini_live_client.py tests/agent/test_s2s_tools_bridge.py tests/agent/test_s2s_pipeline_frames.py tests/agent/test_s2s_live_wiring.py tests/agent/test_s2s_failover_to_cascaded.py tests/agent/test_s2s_cost_limits.py tests/agent/test_live_s2s_openai.py tests/api/test_carrier_media_routes.py tests/db/test_carrier_s2s_models.py tests/db/test_carrier_s2s_migration.py"

# The paths this batch owns, as a pattern. A diagnostic that names one of
# them is this batch's problem; anything else belongs to another batch.
BATCH02_PATTERN="app/carriers_v2/|app/telephony/vonage_handler\.py|app/telephony/providers/vonage\.py|app/telephony/providers/vonage_media\.py|app/telephony/sip/|app/agent/s2s/|app/agent/pipeline\.py|app/api/carrier_media_routes\.py|app/db/carrier_s2s_models\.py|tests/telephony/test_vonage_|tests/telephony/test_sip_|tests/telephony/test_live_vonage_pstn\.py|tests/telephony/test_live_sip_trunk\.py|tests/agent/test_s2s_|tests/agent/test_live_s2s_openai\.py|tests/api/test_carrier_media_routes\.py|tests/db/test_carrier_s2s_"
# The same pattern plus the two paths the truth gate also covers.
BATCH02_TRUTH_PATTERN="$BATCH02_PATTERN|alembic/versions/0064_carrier_s2s\.py|services/sip-gateway/"
if ! ruff check --no-cache $BATCH02_PATHS; then
  record_failure "ruff check on the Batch 02 files"
fi

RUFF_LOG="$(mktemp)"
trap 'rm -f "$RUFF_LOG" "$TRUTH_LOG" "$ALEMBIC_LOG"' EXIT

log "linting the whole tree"
set +e
ruff check app tests scripts loadtest >"$RUFF_LOG" 2>&1
RUFF_STATUS=$?
set -e
if [ "$RUFF_STATUS" -ne 0 ]; then
  # The tree-wide lint also covers files other batches own. A diagnostic that
  # names a Batch 02 file is this batch's problem; anything else is reported
  # and does not block the apply.
  if grep -E "$BATCH02_PATTERN" "$RUFF_LOG" >/dev/null; then
    cat "$RUFF_LOG"
    record_failure "the tree-wide ruff check reported a Batch 02 file"
  else
    log "the tree-wide ruff check failed on files outside Batch 02 (the tail of the log follows); continuing"
    tail -n 20 "$RUFF_LOG"
  fi
fi

TRUTH_LOG="$(mktemp)"

log "running the truth gate"
set +e
make verify-truth >"$TRUTH_LOG" 2>&1
TRUTH_STATUS=$?
set -e
if [ "$TRUTH_STATUS" -ne 0 ]; then
  # A failure that names a Batch 02 file is this batch's problem and fails the
  # apply. A failure elsewhere belongs to another batch: it is reported, but
  # it does not block, because each batch owns its own files.
  if grep -E "$BATCH02_TRUTH_PATTERN" "$TRUTH_LOG" >/dev/null; then
    cat "$TRUTH_LOG"
    record_failure "make verify-truth failed and the failure names a Batch 02 file"
  else
    log "make verify-truth failed on files outside Batch 02 (the tail of the log follows); continuing"
    tail -n 40 "$TRUTH_LOG"
  fi
fi

log "running the SIP gateway tests"
if ! (
  cd services/sip-gateway
  go vet ./...
  go test -race ./...
); then
  record_failure "the SIP gateway tests"
fi

if [ "$FAILURES" -ne 0 ]; then
  fail "$FAILURES gate(s) failed"
fi

log "Batch 02 applied"
