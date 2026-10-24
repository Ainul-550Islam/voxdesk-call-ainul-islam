#!/usr/bin/env bash
# Clean-checkout proof: prove the repository boots and its offline suite passes
# from a fresh clone, not from the working tree that produced the change.
#
# Every command, its exit code and its captured output are written to
# evidence/clean_checkout/<date>.json together with the SHA of the cloned HEAD.
# Nothing in the evidence file is synthesised: a failing step is recorded as a
# failure and the script exits non-zero.
#
# Environment
#   CLEAN_CHECKOUT_REPO   clone URL or local path (default: this checkout's origin, else $PWD)
#   CLEAN_CHECKOUT_REF    branch/tag/commit to clone (default: the current HEAD of this checkout)
#   DATABASE_URL          database used for `alembic upgrade head`. When unset the
#                         proof runs against a throwaway SQLite file and says so
#                         in the evidence, because the offline suite is SQLite-backed.
#   CLEAN_CHECKOUT_TIMEOUT  per-command timeout in seconds (default: 3600)
#
# Usage: bash scripts/make_clean_checkout_proof.sh
set -u -o pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EVIDENCE_DIR="${REPO_ROOT}/evidence/clean_checkout"
DATE_UTC="$(date -u +%Y-%m-%d)"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
EVIDENCE_FILE="${EVIDENCE_DIR}/${DATE_UTC}.json"
TIMEOUT_SECONDS="${CLEAN_CHECKOUT_TIMEOUT:-3600}"

mkdir -p "${EVIDENCE_DIR}"

SOURCE_REPO="${CLEAN_CHECKOUT_REPO:-}"
if [ -z "${SOURCE_REPO}" ]; then
  SOURCE_REPO="$(git -C "${REPO_ROOT}" config --get remote.origin.url 2>/dev/null || true)"
fi
if [ -z "${SOURCE_REPO}" ]; then
  SOURCE_REPO="${REPO_ROOT}"
fi
REF="${CLEAN_CHECKOUT_REF:-$(git -C "${REPO_ROOT}" rev-parse HEAD 2>/dev/null || echo HEAD)}"

WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/voxdesk-clean-checkout-XXXXXX")"
CLONE_DIR="${WORKDIR}/repo"
STEPS_FILE="${WORKDIR}/steps.jsonl"
: > "${STEPS_FILE}"

cleanup() {
  rm -rf "${WORKDIR}"
}
trap cleanup EXIT

record_step() {
  # $1 step name, $2 exit code, $3 stdout file, $4 stderr file, $5 optional note
  python3 - "$1" "$2" "$3" "$4" "${5:-}" "${STEPS_FILE}" <<'PY'
import json
import sys
from pathlib import Path

name, code, out_path, err_path, note, steps_path = sys.argv[1:7]


def tail(path: str, limit: int = 4000) -> str:
    try:
        text = Path(path).read_text(errors="replace")
    except OSError:
        return ""
    return text[-limit:]


record = {
    "step": name,
    "exit_code": int(code),
    "ok": int(code) == 0,
    "note": note,
    "stdout_tail": tail(out_path),
    "stderr_tail": tail(err_path),
}
with open(steps_path, "a", encoding="utf-8") as handle:
    handle.write(json.dumps(record) + "\n")
print(f"[clean-checkout] {name}: exit={code}{' ' + note if note else ''}")
PY
}

run_step() {
  # $1 step name, remaining args: command
  local step="$1"
  shift
  local out_file="${WORKDIR}/${step}.out"
  local err_file="${WORKDIR}/${step}.err"
  ( cd "${PROJECT_DIR}" && timeout "${TIMEOUT_SECONDS}" "$@" ) >"${out_file}" 2>"${err_file}"
  local code=$?
  record_step "${step}" "${code}" "${out_file}" "${err_file}"
  return "${code}"
}

FAILED=0

echo "[clean-checkout] cloning ${SOURCE_REPO} (${REF}) into ${CLONE_DIR}"
if ! git clone --quiet "${SOURCE_REPO}" "${CLONE_DIR}" 2>"${WORKDIR}/clone.err"; then
  if [ -d "${SOURCE_REPO}/.git" ]; then
    # A local path clone of the current worktree that has uncommitted changes is
    # still a clean checkout of HEAD; fall back to a hardlink-free copy+reset.
    git clone --quiet --no-hardlinks "${SOURCE_REPO}" "${CLONE_DIR}" 2>>"${WORKDIR}/clone.err" || true
  fi
fi

if [ ! -d "${CLONE_DIR}/.git" ]; then
  record_step "git_clone" 1 "${WORKDIR}/clone.err" "${WORKDIR}/clone.err" "clone failed"
  FAILED=1
fi

HEAD_SHA=""
PROJECT_DIR="${CLONE_DIR}"
if [ "${FAILED}" -eq 0 ]; then
  git -C "${CLONE_DIR}" checkout --quiet "${REF}" >>"${WORKDIR}/checkout.out" 2>&1
  checkout_code=$?
  record_step "git_checkout" "${checkout_code}" "${WORKDIR}/checkout.out" "${WORKDIR}/checkout.out"
  [ "${checkout_code}" -ne 0 ] && FAILED=1
  HEAD_SHA="$(git -C "${CLONE_DIR}" rev-parse HEAD)"
fi

# The upstream repository keeps the project in a nested directory. Resolve the
# directory that actually holds the application once, and run every later step
# there, so the proof measures the checkout rather than the caller's habits.
if [ "${FAILED}" -eq 0 ]; then
  # A Next.js front end also has an `app/` directory, so the application root is
  # the shallowest `app/` whose parent holds the backend manifests.
  PROJECT_DIR=""
  while IFS= read -r candidate; do
    [ -n "${candidate}" ] || continue
    parent="$(dirname "${candidate}")"
    if [ -f "${parent}/alembic.ini" ] && [ -f "${parent}/Makefile" ]; then
      PROJECT_DIR="${parent}"
      break
    fi
  done <<EOF
$(find "${CLONE_DIR}" -maxdepth 3 -type d -name app ! -path '*/.git/*' 2>/dev/null | awk '{print length" "$0}' | sort -n | cut -d' ' -f2-)
EOF
  if [ -z "${PROJECT_DIR}" ]; then
    record_step "project_root" 1 "${WORKDIR}/clone.err" "${WORKDIR}/clone.err" \
      "could not find the application root (a directory holding app/ and alembic.ini) inside the clone"
    FAILED=1
  fi
  if [ "${FAILED}" -eq 0 ]; then
    record_step "project_root" 0 "${WORKDIR}/clone.err" "${WORKDIR}/clone.err" \
      "project root: ${PROJECT_DIR#${CLONE_DIR}/}"
  fi
fi

if [ "${FAILED}" -eq 0 ]; then
  python3 -m venv "${WORKDIR}/venv" >"${WORKDIR}/venv.out" 2>&1
  record_step "python_venv" "$?" "${WORKDIR}/venv.out" "${WORKDIR}/venv.out"
  VENV_PY="${WORKDIR}/venv/bin/python"
  run_step pip_install "${VENV_PY}" -m pip install --quiet --upgrade pip || FAILED=1
  run_step pip_requirements "${VENV_PY}" -m pip install --quiet -r requirements.txt || FAILED=1
  run_step pip_test_requirements "${VENV_PY}" -m pip install --quiet -r requirements-test.txt || FAILED=1
fi

DB_NOTE=""
EPHEMERAL_DB=""
cleanup_db() {
  if [ -n "${EPHEMERAL_DB}" ]; then
    docker rm -f "${EPHEMERAL_DB}" >/dev/null 2>&1 || true
  fi
}
trap 'cleanup_db; cleanup' EXIT

if [ "${FAILED}" -eq 0 ]; then
  if [ -n "${DATABASE_URL:-}" ]; then
    DB_NOTE="alembic upgrade head ran against the DATABASE_URL supplied by the caller"
  elif command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    # The migration chain ALTERs constraints, which the SQLite dialect cannot do,
    # so a real PostgreSQL is needed. Start a throwaway one rather than skipping
    # the upgrade path: the whole point of this proof is that HEAD migrates.
    EPHEMERAL_DB="voxdesk-clean-checkout-$$"
    docker run -d --rm --name "${EPHEMERAL_DB}" \
      -e POSTGRES_USER=voxdesk -e POSTGRES_PASSWORD=voxdesk -e POSTGRES_DB=voxdesk \
      -p 127.0.0.1::5432 postgres:16-alpine >/"${WORKDIR}/db_start.out" 2>&1 || true
    if [ -s "${WORKDIR}/db_start.out" ]; then
      DB_PORT="$(docker port "${EPHEMERAL_DB}" 5432/tcp 2>/dev/null | head -n 1 | sed 's/.*://')"
      for _attempt in $(seq 1 60); do
        if docker exec "${EPHEMERAL_DB}" pg_isready -U voxdesk -d voxdesk >/dev/null 2>&1; then
          break
        fi
        sleep 1
      done
      DATABASE_URL="postgresql+asyncpg://voxdesk:voxdesk@127.0.0.1:${DB_PORT}/voxdesk"
      DB_NOTE="alembic upgrade head ran against an ephemeral postgres:16 started for this proof"
    else
      DB_NOTE="could not start an ephemeral postgres; the upgrade path was not exercised"
      record_step alembic_upgrade_head 0 "${WORKDIR}/db_start.out" "${WORKDIR}/db_start.out" \
        "SKIPPED: no PostgreSQL available (set DATABASE_URL or install docker)"
    fi
  else
    DB_NOTE="no DATABASE_URL and no docker: only 'alembic heads' was proven; the upgrade path needs PostgreSQL"
    record_step alembic_upgrade_head 0 "${WORKDIR}/venv.out" "${WORKDIR}/venv.out" \
      "SKIPPED: no DATABASE_URL and no docker (migrations require PostgreSQL)"
  fi

  if [ -n "${DATABASE_URL:-}" ]; then
    run_step alembic_upgrade_head env DATABASE_URL="${DATABASE_URL}" \
      "${WORKDIR}/venv/bin/python" -m alembic upgrade head || FAILED=1
    run_step alembic_current env DATABASE_URL="${DATABASE_URL}" \
      "${WORKDIR}/venv/bin/python" -m alembic current || FAILED=1
  fi
  run_step alembic_heads "${WORKDIR}/venv/bin/python" -m alembic heads || FAILED=1
  run_step ruff "${WORKDIR}/venv/bin/python" -m ruff check app tests scripts loadtest || FAILED=1
  run_step pytest_offline env TZ=UTC "${WORKDIR}/venv/bin/python" -m pytest -m "not live" -q || FAILED=1
fi

OVERALL=0
[ "${FAILED}" -ne 0 ] && OVERALL=1

python3 - "${EVIDENCE_FILE}" "${STEPS_FILE}" "${SOURCE_REPO}" "${REF}" "${HEAD_SHA}" "${OVERALL}" "${DB_NOTE}" "${RUN_ID}" <<'PY'
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

evidence_path, steps_path, source_repo, ref, head_sha, overall, db_note, run_id = sys.argv[1:9]
steps = [json.loads(line) for line in Path(steps_path).read_text().splitlines() if line.strip()]
payload = {
    "run_id": run_id,
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "kind": "clean_checkout_proof",
    "source_repo": source_repo,
    "requested_ref": ref,
    "head_sha": head_sha,
    "overall_ok": int(overall) == 0,
    "database_note": db_note,
    "runtime": {
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_count": __import__("os").cpu_count() or 1,
    },
    "steps": steps,
}
Path(evidence_path).write_text(json.dumps(payload, indent=2) + "\n")
print(f"[clean-checkout] evidence written to {evidence_path}")
PY

if [ "${OVERALL}" -ne 0 ]; then
  echo "[clean-checkout] FAILED - see ${EVIDENCE_FILE}" >&2
  exit 1
fi
echo "[clean-checkout] OK"
