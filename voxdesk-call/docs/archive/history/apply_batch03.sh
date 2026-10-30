#!/usr/bin/env bash
#
# Batch 03 — compliance control plane (SIEM, tamper-evident audit, KMS envelope,
# residency, SOC 2 evidence, HIPAA safeguards, security harness).
#
# Usage:
#   ./apply_batch03.sh [--skip-verify] [REPO_ROOT]
#
# After patching, the script verifies the result by running alembic, ruff,
# ``make verify-truth`` and the offline test suites. Any failure exits non-zero
# and names the exact command that failed. ``--skip-verify`` is for a machine
# without a database or the runtime dependencies; it is a deliberate opt-out,
# not a default.
#
# The script is idempotent: every edit is guarded by a marker comment, so running
# it twice changes nothing the second time. It never rewrites the alembic chain,
# never replaces a shared file with a full copy, and stops with a clear message
# when a prerequisite is missing.
set -euo pipefail

VERIFY=1
ARGS=()
for arg in "$@"; do
  case "$arg" in
    --skip-verify) VERIFY=0 ;;
    -h|--help)
      printf 'usage: %s [--skip-verify] [REPO_ROOT]\n' "$0"
      exit 0
      ;;
    *) ARGS+=("$arg") ;;
  esac
done
ROOT="${ARGS[0]:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
cd "$ROOT"

fail() { printf 'apply_batch03: %s\n' "$*" >&2; exit 1; }
note() { printf 'apply_batch03: %s\n' "$*"; }

# ---------------------------------------------------------------- prereqs ---

[ -f pyproject.toml ] || fail "pyproject.toml not found in $ROOT; run this from the repository root"
[ -f alembic.ini ] || fail "alembic.ini not found in $ROOT; run this from the repository root"
[ -f app/main.py ] || fail "app/main.py not found in $ROOT; this is not the voxdesk-call application root"
[ -d app/compliance_v2 ] || fail "app/compliance_v2 is missing; unzip the patch into the repository root first"

# 0065 declares down_revision "0064_carrier_s2s". Applying it onto a tree that
# does not contain that revision would leave alembic with a dangling parent.
if ! compgen -G "alembic/versions/0064_carrier_s2s*.py" > /dev/null; then
  fail "alembic/versions/0064_carrier_s2s.py is missing: apply Batch 02 first (0065_compliance_v2 declares it as down_revision)"
fi

python3 - <<'PY' || fail "shared-file patching failed (see the message above)"
from pathlib import Path
import sys

TRACKED = []


def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    Path(path).write_text(text, encoding="utf-8")


def insert_after(path: str, marker: str, anchor: str, block: str) -> None:
    """Insert ``block`` after the first line containing ``anchor``.

    Idempotent on ``marker``: if any line already carries it, the file is left
    untouched. A missing anchor is a hard error — silently skipping an edit is
    how a deployment ends up with code that imports what nothing registered.
    """
    text = read(path)
    if marker in text:
        print(f"  = {path} (already patched)")
        return
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if anchor in line:
            lines.insert(index + 1, block)
            write(path, "".join(lines))
            TRACKED.append(path)
            print(f"  + {path}")
            return
    sys.exit(f"anchor not found in {path}: {anchor!r}")


def insert_before(path: str, marker: str, anchor: str, block: str) -> None:
    text = read(path)
    if marker in text:
        print(f"  = {path} (already patched)")
        return
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if anchor in line:
            lines.insert(index, block)
            write(path, "".join(lines))
            TRACKED.append(path)
            print(f"  + {path}")
            return
    sys.exit(f"anchor not found in {path}: {anchor!r}")


def replace_once(path: str, marker: str, old: str, new: str) -> None:
    """Replace one exact block, guarded by ``marker``.

    Used for the two host handlers that must consult the minimum-necessary
    filter. A whole-file copy would overwrite whatever another batch did to the
    same module, and a line-number patch would silently land in the wrong
    function after any upstream edit — so the block is matched verbatim and a
    miss is a hard error.
    """
    text = read(path)
    if marker in text:
        print(f"  = {path} (already patched)")
        return
    if old not in text:
        sys.exit(f"expected block not found in {path}: {old.splitlines()[0]!r}")
    write(path, text.replace(old, new, 1))
    TRACKED.append(path)
    print(f"  + {path}")

def append(path: str, marker: str, block: str) -> None:
    text = read(path)
    if marker in text:
        print(f"  = {path} (already patched)")
        return
    if not text.endswith("\n"):
        text += "\n"
    write(path, text + block)
    TRACKED.append(path)
    print(f"  + {path}")


# 1. Mount the compliance router before the placeholder quarantine runs, and
#    after every router that already exists.
insert_before(
    "app/main.py",
    "# BATCH03:compliance_v2_routes",
    "# Quarantine synthetic route generators before the application starts serving.",
    """# BATCH03:compliance_v2_routes
from app.api.compliance_v2_routes import router as compliance_v2_router  # noqa: E402

app.include_router(compliance_v2_router)

""",
)

# 2. Register the ORM metadata wherever the model set is assembled. Both files
#    build `Base.metadata` before anything else imports the app, so a missing
#    import here shows up as absent tables rather than an error.
insert_after(
    "alembic/env.py",
    "# BATCH03:compliance_v2_models",
    "import app.compliance.models  # noqa: F401",
    "import app.db.compliance_v2_models  # noqa: F401  # BATCH03:compliance_v2_models\n",
)
insert_after(
    "tests/conftest.py",
    "# BATCH03:compliance_v2_models",
    "import app.compliance.models  # noqa: F401",
    "import app.db.compliance_v2_models  # noqa: F401  # BATCH03:compliance_v2_models\n",
)

# 3. The permission vocabulary. Enum members only; the role bundles themselves
#    live in app/compliance_v2/permissions.py.
insert_after(
    "app/auth/permissions.py",
    "# BATCH03:compliance_v2_permissions",
    '    VOICE_CLONE_WRITE = "voice_clone:write"',
    """
    # BATCH03:compliance_v2_permissions
    # Compliance control plane (Batch 03). Named in the same resource:action
    # vocabulary as everything above; the role bundles live in
    # app.compliance_v2.permissions.
    COMPLIANCE_SIEM_READ = "compliance_siem:read"
    COMPLIANCE_SIEM_WRITE = "compliance_siem:write"
    COMPLIANCE_AUDIT_VERIFY = "compliance_audit:verify"
    COMPLIANCE_AUDIT_EXPORT = "compliance_audit:export"
    COMPLIANCE_KMS_REKEY = "compliance_kms:rekey"
    COMPLIANCE_RESIDENCY_READ = "compliance_residency:read"
    COMPLIANCE_RESIDENCY_WRITE = "compliance_residency:write"
    COMPLIANCE_EVIDENCE_READ = "compliance_evidence:read"
    COMPLIANCE_EVIDENCE_RUN = "compliance_evidence:run"
    COMPLIANCE_ACCESS_REVIEW_READ = "compliance_access_review:read"
    COMPLIANCE_ACCESS_REVIEW_WRITE = "compliance_access_review:write"
    COMPLIANCE_INCIDENT_READ = "compliance_incident:read"
    COMPLIANCE_INCIDENT_WRITE = "compliance_incident:write"
    COMPLIANCE_DSAR_READ = "compliance_dsar:read"
    COMPLIANCE_DSAR_WRITE = "compliance_dsar:write"
    COMPLIANCE_PHI_POLICY_READ = "compliance_phi_policy:read"
    COMPLIANCE_PHI_POLICY_MANAGE = "compliance_phi_policy:manage"
    COMPLIANCE_BREAK_GLASS_REQUEST = "compliance_break_glass:request"
    COMPLIANCE_BREAK_GLASS_APPROVE = "compliance_break_glass:approve"
""",
)

# 4. Extend the role bundles. Appended at the end of the module, after
#    ROLE_PERMISSIONS is fully built.
append(
    "app/auth/rbac.py",
    "# BATCH03:compliance_v2_permissions",
    """

# BATCH03:compliance_v2_permissions
def _extend_roles_with_compliance_v2() -> None:
    \"\"\"Add the Batch 03 permission bundles to the roles that should hold them.

    ``_OWNER`` is derived from the enum, so the owner already holds every new
    permission; only the manager and admin bundles need extending. The bundle
    itself lives in ``app.compliance_v2.permissions`` so this file stays a
    policy document rather than growing a second table.
    \"\"\"
    from app.compliance_v2.permissions import compliance_v2_role_bundles

    for role, extra in compliance_v2_role_bundles().items():
        ROLE_PERMISSIONS[role] = ROLE_PERMISSIONS[role] | extra


_extend_roles_with_compliance_v2()
""",
)

# 5. Scheduler loops: import plus one entry inside the existing gather, so the
#    compliance cycles stop with the same SIGTERM as every other loop.
insert_after(
    "scripts/scheduler.py",
    "# BATCH03:compliance_v2_jobs",
    "from app.core.logging import log  # noqa: F401  (configures structlog)",
    "from app.compliance_v2 import jobs as compliance_v2_jobs  # BATCH03:compliance_v2_jobs\n",
)
insert_after(
    "scripts/scheduler.py",
    "compliance_v2_jobs.run_all(_stop),",
    "durable_jobs_loop(), outbox_dispatch_loop(),",
    "        compliance_v2_jobs.run_all(_stop),  # BATCH03:compliance_v2_jobs\n",
)

# 6. Make targets. The Makefile itself stays as shipped.
append(
    "Makefile",
    "# BATCH03:compliance_v2_makefile",
    """
# BATCH03:compliance_v2_makefile
include Makefile.batch03.mk
""",
)

# 7. Minimum necessary on the two surfaces that return PHI to a person: the call
#    transcript and the call replay timeline. The filter itself lives in
#    app/api/compliance_v2/phi_guard.py and is inert unless HIPAA mode is on.
replace_once(
    "app/api/routes.py",
    "# BATCH03:phi_guard_transcript_import",
    """from app.auth.permissions import Permission
from app.auth.rbac import has_permission
""",
    """from app.api.compliance_v2.phi_guard import PhiGuard, phi_guard  # BATCH03:phi_guard_transcript_import
from app.auth.permissions import Permission
from app.auth.rbac import has_permission
from app.compliance_v2.hipaa.minimum_necessary import SURFACE_TRANSCRIPT  # BATCH03:phi_guard_transcript_import
""",
)
replace_once(
    "app/api/routes.py",
    "# BATCH03:phi_guard_transcript_body",
    """    session: AsyncSession = Depends(get_session),
):
    # Resolve both tenant and environment before loading a transcript.""",
    """    session: AsyncSession = Depends(get_session),
    phi: PhiGuard = Depends(phi_guard(SURFACE_TRANSCRIPT)),  # BATCH03:phi_guard_transcript_body
):
    # Resolve both tenant and environment before loading a transcript.""",
)
replace_once(
    "app/api/routes.py",
    "# BATCH03:phi_guard_transcript_return",
    """    return [
        {\"speaker\": t.speaker.value if hasattr(t.speaker, \"value\") else str(t.speaker),
         \"text\": _safe_call_text(t.text), \"at\": t.created_at}
        for t in sorted(call.turns, key=lambda x: x.created_at)
    ]""",
    """    turns = [
        {\"speaker\": t.speaker.value if hasattr(t.speaker, \"value\") else str(t.speaker),
         \"text\": _safe_call_text(t.text), \"at\": t.created_at}
        for t in sorted(call.turns, key=lambda x: x.created_at)
    ]
    # BATCH03:phi_guard_transcript_return
    # Minimum necessary: with HIPAA mode on, a role that may not see transcript
    # text on this surface gets the turn envelope without the words.
    filtered, _removed = phi.filter_records(turns, table=\"turns\")
    return filtered""",
)

replace_once(
    "app/api/call_search_export_routes.py",
    "# BATCH03:phi_guard_replay_import",
    """from app.auth.dependencies import TenantContext, require_permission
""",
    """from app.api.compliance_v2.phi_guard import PhiGuard, phi_guard  # BATCH03:phi_guard_replay_import
from app.auth.dependencies import TenantContext, require_permission
from app.compliance_v2.hipaa.minimum_necessary import SURFACE_REPLAY  # BATCH03:phi_guard_replay_import
""",
)
replace_once(
    "app/api/call_search_export_routes.py",
    "# BATCH03:phi_guard_replay_dep",
    """    ctx: TenantContext = Depends(require_permission(Permission.CALL_READ)),
    session: AsyncSession = Depends(get_session),
):
    \"\"\"GET /api/calls/{id}/replay — Replay transcript/timeline/recording_url with auth.\"\"\"""",
    """    ctx: TenantContext = Depends(require_permission(Permission.CALL_READ)),
    session: AsyncSession = Depends(get_session),
    phi: PhiGuard = Depends(phi_guard(SURFACE_REPLAY)),  # BATCH03:phi_guard_replay_dep
):
    \"\"\"GET /api/calls/{id}/replay — Replay transcript/timeline/recording_url with auth.\"\"\"""",
)
replace_once(
    "app/api/call_search_export_routes.py",
    "# BATCH03:phi_guard_replay_body",
    """            timeline.sort(key=lambda event: event[\"at\"])
        except Exception:
            __import__(\"logging\").getLogger(__name__).debug(\"suppressed_exception\", exc_info=True)
            timeline = []""",
    """            # BATCH03:phi_guard_replay_body
            # Minimum necessary: the same filter the transcript surface uses,
            # applied per timeline entry so a turn's words disappear while the
            # event itself stays visible as evidence that the turn happened.
            timeline, _removed = phi.filter_records(timeline, table=\"turns\")
            if turns and \"text\" not in phi.allowlist():
                transcript = None
            timeline.sort(key=lambda event: event[\"at\"])
        except Exception:
            __import__(\"logging\").getLogger(__name__).debug(\"suppressed_exception\", exc_info=True)
            timeline = []""",
)

# 8. A module header that advertises a line count is a claim that goes stale;
#    the repository's own truth test rejects it, so the header is rewritten to
#    describe the module instead. Nothing else in the file changes.
replace_once(
    "app/api/advanced_analytics_routes.py",
    "# BATCH03:analytics_header",
    """# File: app/api/advanced_analytics_routes.py \u2014 Enterprise advanced-analytics API \u2014 1050+ lines production \u2014 NO SKIP FULL CODE
\"\"\"advanced-analytics API \u2014 expanded production implementation 1050+ lines, full code, no placeholder.\"\"\"
""",
    """\"\"\"Enterprise advanced-analytics API.

Funnel, cohort, attribution and forecast reads over the persisted call and
revenue tables. The module header states what the code does rather than how
long it is: a line count is a claim that goes stale the moment anyone edits
the file, and it was already wrong.
\"\"\"
# BATCH03:analytics_header
""",
)

print(f"patched {len(TRACKED)} file(s)")
PY

# ------------------------------------------------- regenerate derived files ---
#
# The route snapshot and the OpenAPI contract are generated from the mounted
# application, so they are regenerated here rather than shipped as a copy: a
# stale snapshot is a test failure, and a hand-edited one is a lie.
note "regenerating tests/truth/routes_snapshot.json and contracts/openapi.json"
python3 - <<'PY' || fail "could not regenerate the route snapshot / OpenAPI contract (install the runtime requirements, then re-run)"
import json
from pathlib import Path

from app.main import app

snapshot = {
    "dashboard_build_present": Path("dashboard/dist/index.html").is_file(),
    "route_count": len(app.routes),
    "routes": [
        {
            "methods": sorted(getattr(route, "methods", None) or []),
            "name": getattr(route, "name", "") or "",
            "path": route.path,
        }
        for route in app.routes
    ],
}
# Both documents are built before either is written: a half-regenerated tree
# (new snapshot, old contract) is worse than an unchanged one.
snapshot_body = json.dumps(snapshot, indent=2) + "\n"
contract_body = json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"

Path("tests/truth/routes_snapshot.json").write_text(snapshot_body, encoding="utf-8")
Path("contracts/openapi.json").write_text(contract_body, encoding="utf-8")

compliance = [route for route in app.routes if "/compliance" in getattr(route, "path", "")]
print(f"  routes={snapshot['route_count']} openapi_paths={len(json.loads(contract_body)['paths'])} "
      f"compliance_routes={len(compliance)}")
PY

# ------------------------------------------------------------- verification ---
#
# Each gate is run and reported separately: a single "failed" at the end would
# tell the operator nothing, and the four failures have very different causes.
if [ "$VERIFY" = "1" ]; then
  FAILED=()
  run_gate() {
    local label="$1"
    shift
    note "gate: $label"
    if "$@"; then
      printf '  ok: %s\n' "$label"
    else
      printf '  FAILED: %s\n' "$label" >&2
      FAILED+=("$label")
    fi
  }

  run_gate "alembic upgrade head" python3 -m alembic upgrade head
  run_gate "ruff" python3 -m ruff check app/compliance_v2 app/api/compliance_v2 app/api/compliance_v2_routes.py \
    app/db/compliance_v2_models.py security_harness scripts tests/compliance_v2 tests/security_v2 \
    alembic/versions/0065_compliance_v2.py
  run_gate "make verify-truth" make verify-truth
  run_gate "pytest (not live)" python3 -m pytest -m "not live" tests/compliance_v2 tests/security_v2 -q

  if [ "${#FAILED[@]}" -gt 0 ]; then
    printf 'apply_batch03: %d gate(s) failed: %s\n' "${#FAILED[@]}" "${FAILED[*]}" >&2
    printf 'apply_batch03: the patch is applied; fix the gate above (or re-run with --skip-verify)\n' >&2
    exit 1
  fi
  note "all gates passed"
else
  note "verification skipped (--skip-verify)"
fi

note "done. Next:"
cat <<'EOF'
  make compliance-v2-verify
EOF
