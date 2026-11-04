#!/usr/bin/env bash
# Run the full backend test suite against real services.
#
# Why a script instead of `pytest tests`:
#
#   * The suite needs a PostgreSQL database that is already migrated to head and a
#     reachable Redis. Several modules fail — correctly — when those are absent
#     (tests/jobs/test_recovery.py asserts a postgresql+asyncpg DATABASE_URL), so a
#     run without them is not a full run and this script says so instead of
#     reporting green.
#   * A single pytest process over 6,700 nodes holds every imported module in
#     memory at once. On a 2 GB machine that is an OOM kill, not a failure. The
#     suite is therefore executed in chunks, one pytest process per chunk, so peak
#     memory stays bounded by the largest chunk rather than by the union of all of
#     them. Use --chunk all to reproduce the single-process behaviour on a bigger
#     machine.
#
# Usage:
#   scripts/run_full_tests.sh                 # every chunk, then a summary
#   scripts/run_full_tests.sh -k billing_api  # extra args go to every pytest run
#   scripts/run_full_tests.sh --chunk truth   # one chunk
#   scripts/run_full_tests.sh --list          # print the chunks and exit
#   scripts/run_full_tests.sh --no-db         # unit chunks only, DB chunks marked SKIPPED
#
# Environment:
#   DATABASE_URL   postgresql+asyncpg://user@host:port/db (default: local disposable DB)
#   REDIS_URL      redis://host:port/db                     (default: local redis)
#   TEST_CHUNK_TIMEOUT  per-chunk timeout in seconds (default 1800)
#   JUNIT_DIR      where the per-chunk JUnit XML lands (default reports/junit)
#   PER_FILE_CHUNKS  chunks run one test file at a time (default "truth auth"); set it to
#                  a space-separated list, or to "" to disable per-file execution
#
# Exit codes:
#   0  every executed chunk passed
#   1  at least one chunk failed
#   2  services are missing and --no-db was not given (nothing was run)
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT" || exit 2

DATABASE_URL="${DATABASE_URL:-postgresql+asyncpg://voxdesk@127.0.0.1:5432/voxdesk}"
REDIS_URL="${REDIS_URL:-redis://127.0.0.1:6379/0}"
TEST_CHUNK_TIMEOUT="${TEST_CHUNK_TIMEOUT:-1800}"
JUNIT_DIR="${JUNIT_DIR:-reports/junit}"
PYTHON="${PYTHON:-python3}"

# Chunk name -> pytest targets. Ordered cheap-first so a broken environment fails
# fast instead of after forty minutes.
CHUNKS=(
  "truth:tests/truth"
  "smoke:tests/smoke"
  "api:tests/api"
  "auth:tests/auth"
  "jobs:tests/jobs"
  "outbox:tests/outbox"
  "qa:tests/qa"
  "telephony:tests/telephony"
  "integration:tests/integration"
  "security:tests/security"
  "services:tests/services"
  "root:tests/test_*.py"
)
# Chunks that need a migrated PostgreSQL database, not just Redis.
DB_CHUNKS=" jobs integration "
# Extra directories that exist in this repository are appended automatically, so a
# new test package is not silently skipped by a stale list.
for dir in tests/*/; do
  name="$(basename "$dir")"
  case "$name" in
    __pycache__) continue ;;
  esac
  if ! printf '%s\n' "${CHUNKS[@]}" | grep -q ":$dir\$\|^[a-z_]*:/${dir%/}\$"; then
    if printf '%s\n' "${CHUNKS[@]}" | grep -q "${dir%/}"; then continue; fi
    CHUNKS+=("$name:$dir")
  fi
done

# Guard against a name collision: two chunks must never share a log file.
declare -A SEEN_NAMES=()
for entry in "${CHUNKS[@]}"; do
  name="${entry%%:*}"
  if [[ -n "${SEEN_NAMES[$name]:-}" ]]; then
    echo "run_full_tests: duplicate chunk name '$name'" >&2
    exit 2
  fi
  SEEN_NAMES[$name]=1
done

ONLY_CHUNK=""
NO_DB=0
PYTEST_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --chunk) ONLY_CHUNK="${2:-}"; shift 2 ;;
    --chunk=*) ONLY_CHUNK="${1#*=}"; shift ;;
    --no-db) NO_DB=1; shift ;;
    --list)
      for entry in "${CHUNKS[@]}"; do printf '%-14s %s\n' "${entry%%:*}" "${entry#*:}"; done
      exit 0 ;;
    -h|--help)
      sed -n '2,40p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) PYTEST_ARGS+=("$1"); shift ;;
  esac
done

need() { command -v "$1" >/dev/null 2>&1; }
need "$PYTHON" || { echo "run_full_tests: $PYTHON is not installed" >&2; exit 2; }

port_open() {
  "$PYTHON" - "$1" "$2" <<'PY'
import socket, sys
host, port = sys.argv[1], int(sys.argv[2])
sock = socket.socket()
sock.settimeout(2.0)
try:
    sock.connect((host, port))
except OSError:
    sys.exit(1)
finally:
    sock.close()
PY
}

# postgresql+asyncpg://user:pass@host:port/db -> host port
parse_db() {
  "$PYTHON" - "$1" <<'PY'
import sys
from urllib.parse import urlsplit
url = sys.argv[1].replace("postgresql+asyncpg://", "postgresql://", 1)
parts = urlsplit(url)
print(parts.hostname or "127.0.0.1", parts.port or 5432)
PY
}

echo "== run_full_tests =="
echo "repository:   $REPO_ROOT"
echo "DATABASE_URL: $DATABASE_URL"
echo "REDIS_URL:    $REDIS_URL"
echo

DB_HOST_PORT="$(parse_db "$DATABASE_URL")"
DB_HOST="${DB_HOST_PORT% *}"
DB_PORT="${DB_HOST_PORT#* }"
REDIS_HOST_PORT="$("$PYTHON" - "$REDIS_URL" <<'PY'
import sys
from urllib.parse import urlsplit
parts = urlsplit(sys.argv[1])
print(parts.hostname or "127.0.0.1", parts.port or 6379)
PY
)"
REDIS_HOST="${REDIS_HOST_PORT% *}"
REDIS_PORT="${REDIS_HOST_PORT#* }"

HAVE_DB=0
if port_open "$DB_HOST" "$DB_PORT"; then
  HAVE_DB=1
  echo "postgres:     reachable at $DB_HOST:$DB_PORT"
else
  echo "postgres:     UNREACHABLE at $DB_HOST:$DB_PORT"
fi

HAVE_REDIS=0
if port_open "$REDIS_HOST" "$REDIS_PORT"; then
  HAVE_REDIS=1
  echo "redis:        reachable at $REDIS_HOST:$REDIS_PORT"
else
  echo "redis:        UNREACHABLE at $REDIS_HOST:$REDIS_PORT"
fi
echo

if [[ $HAVE_REDIS -eq 0 ]]; then
  cat >&2 <<EOF
run_full_tests: no Redis at $REDIS_HOST:$REDIS_PORT.

The identity rate limiter and the outbox worker both talk to Redis; without it a
large part of the suite exercises fallback paths instead of the code under test.
Start one, for example:

  redis-server --port $REDIS_PORT --bind 127.0.0.1 --save ''

Then re-run this script.
EOF
  exit 2
fi

if [[ $HAVE_DB -eq 0 && $NO_DB -eq 0 ]]; then
  cat >&2 <<EOF
run_full_tests: no PostgreSQL at $DB_HOST:$DB_PORT and --no-db was not given.

tests/jobs and tests/integration require a migrated database; skipping them
silently would make this run incomplete, so nothing was executed. Either start
PostgreSQL 16 and re-run, or pass --no-db to run the unit chunks only (the DB
chunks are then reported as SKIPPED with this reason).
EOF
  exit 2
fi

export DATABASE_URL REDIS_URL
export VOXDESK_TEST_SERVICES="postgres=$HAVE_DB redis=$HAVE_REDIS"

if [[ $HAVE_DB -eq 1 ]]; then
  echo "-- ensuring the database exists and is migrated to head"
  "$PYTHON" - "$DATABASE_URL" <<'PY'
import asyncio
import sys
from urllib.parse import urlsplit

import asyncpg


async def main() -> int:
    parts = urlsplit(sys.argv[1].replace("postgresql+asyncpg://", "postgresql://", 1))
    name = (parts.path or "/voxdesk").lstrip("/") or "voxdesk"
    admin_dsn = (
        f"postgresql://{parts.username or 'postgres'}@{parts.hostname or '127.0.0.1'}:{parts.port or 5432}/postgres"
    )
    if parts.password:
        admin_dsn = admin_dsn.replace("@", f":{parts.password}@", 1)
    conn = await asyncpg.connect(admin_dsn)
    try:
        exists = await conn.fetchval("select 1 from pg_database where datname = $1", name)
        if not exists:
            await conn.execute(f'CREATE DATABASE "{name}"')
            print(f"   created database {name}")
        else:
            print(f"   database {name} already exists")
    finally:
        await conn.close()
    return 0


raise SystemExit(asyncio.run(main()))
PY
  if ! alembic upgrade head; then
    echo "run_full_tests: alembic upgrade head failed; the DB chunks would fail for the wrong reason" >&2
    exit 2
  fi
  echo
fi

mkdir -p "$JUNIT_DIR"
RUN_STARTED_AT=$(date +%s)
declare -a RESULTS=()
FAILED=0
SKIPPED=0
TOTAL_PASSED=0
TOTAL_FAILED=0
TOTAL_ERRORS=0
TOTAL_SKIPS=0

# Chunks that run one test file per pytest process.
#
# tests/truth imports the whole FastAPI application in nearly every module, and a
# single pytest process over the directory holds all of those imports at once. On a
# 2 GB machine that is an OOM kill (exit 137), which reads as a failure but is a
# resource limit. Running file by file keeps each process small and still yields a
# single verdict for the chunk, because the counts and the worst status are summed.
PER_FILE_CHUNKS="${PER_FILE_CHUNKS:- truth auth}"

RUN_STATUS=0
RUN_SECONDS=0
RUN_SUMMARY=""
RUN_PASSED=0
RUN_FAILED=0
RUN_ERRORS=0
RUN_SKIPS=0

run_target() {  # $1 = junit/log label, rest = pytest targets
  local label="$1" start
  shift
  start=$(date +%s)
  timeout "$TEST_CHUNK_TIMEOUT" "$PYTHON" -m pytest "$@" -q -p no:cacheprovider \
    --junitxml="$JUNIT_DIR/$label.xml" "${PYTEST_ARGS[@]}" >"/tmp/run_full_tests_$label.log" 2>&1
  RUN_STATUS=$?
  RUN_SECONDS=$(( $(date +%s) - start ))
  # The last pytest summary line. Every outcome word has to be in the pattern:
  # matching only "passed"/"failed" silently reported zero for a file that was
  # entirely skipped, which under-counted the skip total (root: 0 instead of 16)
  # while leaving the pass total correct.
  RUN_SUMMARY="$(grep -E '[0-9]+ (passed|failed|skipped|error|errors|deselected|xfailed|xpassed)' \
    "/tmp/run_full_tests_$label.log" | tail -1)"
  RUN_PASSED=$(printf '%s' "$RUN_SUMMARY" | grep -oE '[0-9]+ passed' | grep -oE '[0-9]+' || true)
  RUN_FAILED=$(printf '%s' "$RUN_SUMMARY" | grep -oE '[0-9]+ failed' | grep -oE '[0-9]+' || true)
  RUN_ERRORS=$(printf '%s' "$RUN_SUMMARY" | grep -oE '[0-9]+ (error|errors)' | grep -oE '[0-9]+' || true)
  RUN_SKIPS=$(printf '%s' "$RUN_SUMMARY" | grep -oE '[0-9]+ skipped' | grep -oE '[0-9]+' || true)
  RUN_PASSED=${RUN_PASSED:-0}; RUN_FAILED=${RUN_FAILED:-0}
  RUN_ERRORS=${RUN_ERRORS:-0}; RUN_SKIPS=${RUN_SKIPS:-0}
}

for entry in "${CHUNKS[@]}"; do
  name="${entry%%:*}"
  target="${entry#*:}"
  if [[ -n "$ONLY_CHUNK" && "$name" != "$ONLY_CHUNK" ]]; then
    continue
  fi
  # Existence is checked on the literal path; a glob cannot be tested with -e, its
  # expansion below decides whether the chunk is MISSING.
  if [[ "$target" != *"*"* && ! -e "$target" ]]; then
    RESULTS+=("$name|MISSING|0|0|0|0")
    continue
  fi
  # A chunk target may be a glob (`tests/*.py`). Handed to pytest still quoted, it
  # would be read as a literal path and pytest would exit 4 with "file or directory
  # not found", which reads like a failure. Expand it here instead.
  TARGETS=()
  if [[ "$target" == *"*"* ]]; then
    mapfile -t TARGETS < <(compgen -G "$target")
    if [[ ${#TARGETS[@]} -eq 0 ]]; then
      RESULTS+=("$name|MISSING|0|0|0|0")
      continue
    fi
  else
    TARGETS=("$target")
  fi
  if [[ $HAVE_DB -eq 0 && "$DB_CHUNKS" == *" $name "* ]]; then
    echo "[$name] SKIPPED: needs PostgreSQL (--no-db)"
    RESULTS+=("$name|SKIPPED|0|0|0|0")
    SKIPPED=$((SKIPPED + 1))
    continue
  fi

  # Which chunks run one file per pytest process: the ones listed in
  # PER_FILE_CHUNKS, and any glob chunk (its elements are already files, and a
  # glob chunk such as `tests/*.py` is exactly the population that does not fit in
  # memory as one process).
  files=()
  per_file=0
  if [[ "$target" == *"*"* ]]; then
    files=("${TARGETS[@]}")
    per_file=1
  elif [[ " $PER_FILE_CHUNKS " == *" $name "* && -d "$target" ]]; then
    mapfile -t files < <(find "$target" -name 'test_*.py' -type f | sort)
    per_file=1
  fi

  if [[ $per_file -eq 1 ]]; then
    if [[ ${#files[@]} -eq 0 ]]; then
      RESULTS+=("$name|MISSING|0|0|0|0")
      continue
    fi
    echo "[$name] $target file by file (${#files[@]} files) ${PYTEST_ARGS[*]:-}"
    chunk_passed=0; chunk_failed=0; chunk_errors=0; chunk_skips=0
    chunk_seconds=0; chunk_status=0; failing=()
    for file in "${files[@]}"; do
      label="${name}.$(basename "$file" .py)"
      run_target "$label" "$file"
      chunk_passed=$((chunk_passed + RUN_PASSED))
      chunk_failed=$((chunk_failed + RUN_FAILED))
      chunk_errors=$((chunk_errors + RUN_ERRORS))
      chunk_skips=$((chunk_skips + RUN_SKIPS))
      chunk_seconds=$((chunk_seconds + RUN_SECONDS))
      if [[ $RUN_STATUS -ne 0 ]]; then
        # A timeout outranks a plain failure in the chunk verdict, and the first
        # non-zero status is kept so the exit code is never "clean" by accident.
        failing+=("$(basename "$file")")
        if [[ $RUN_STATUS -eq 124 || $chunk_status -eq 0 ]]; then
          chunk_status=$RUN_STATUS
        fi
      fi
    done
    seconds=$chunk_seconds
    passed=$chunk_passed; failed=$chunk_failed; errors=$chunk_errors; skips=$chunk_skips
    if [[ $chunk_status -eq 124 ]]; then
      verdict="TIMEOUT"; FAILED=$((FAILED + 1))
    elif [[ $chunk_status -ne 0 && $chunk_status -ne 5 ]]; then
      # `5` is pytest's "no tests collected": a file that holds none is not a
      # failure, and a glob chunk legitimately spans helper modules.
      verdict="FAIL"; FAILED=$((FAILED + 1))
    else
      verdict="PASS"
    fi
    summary="${passed} passed, ${failed} failed, ${errors} errors, ${skips} skipped"
    if [[ $((passed + failed + errors + skips)) -eq 0 ]]; then
      summary="$summary :: EMPTY (no test collected in this chunk)"
    fi
    if [[ ${#failing[@]} -gt 0 ]]; then
      summary="$summary :: failing: ${failing[*]}"
    fi
    echo "[$name] $verdict in ${seconds}s :: $summary"
    RESULTS+=("$name|$verdict|$passed|$failed|$errors|$skips")
  else
    echo "[$name] pytest ${TARGETS[*]} ${PYTEST_ARGS[*]:-}"
    run_target "$name" "${TARGETS[@]}"
    status=$RUN_STATUS
    seconds=$RUN_SECONDS
    summary=$RUN_SUMMARY
    passed=$RUN_PASSED; failed=$RUN_FAILED; errors=$RUN_ERRORS; skips=$RUN_SKIPS
    if [[ $status -eq 124 ]]; then
      verdict="TIMEOUT"
      FAILED=$((FAILED + 1))
    elif [[ $status -ne 0 ]]; then
      verdict="FAIL"
      FAILED=$((FAILED + 1))
    else
      verdict="PASS"
    fi
    echo "[$name] $verdict in ${seconds}s :: ${summary:-no summary}"
    RESULTS+=("$name|$verdict|$passed|$failed|$errors|$skips")
  fi
  TOTAL_PASSED=$((TOTAL_PASSED + passed))
  TOTAL_FAILED=$((TOTAL_FAILED + failed))
  TOTAL_ERRORS=$((TOTAL_ERRORS + errors))
  TOTAL_SKIPS=$((TOTAL_SKIPS + skips))
done

STALE_JUNIT=$(find "$JUNIT_DIR" -name '*.xml' ! -newermt "@$RUN_STARTED_AT" 2>/dev/null | sort)
if [[ -n "$STALE_JUNIT" ]]; then
  stale_count=$(printf '%s\n' "$STALE_JUNIT" | wc -l)
  echo
  echo "-- note: $JUNIT_DIR holds $stale_count JUnit file(s) this run did not write,"
  echo "   e.g.:"
  printf '%s\n' "$STALE_JUNIT" | head -5 | sed 's/^/     /'
  echo "   they are left over from an earlier run; summing the directory double-counts them."
fi

echo
echo "== summary =="
printf '%-14s %-8s %6s %6s %6s %6s %s\n' chunk result passed failed errors skipped log
for row in "${RESULTS[@]}"; do
  IFS='|' read -r name verdict passed failed errors skips <<<"$row"
  log="/tmp/run_full_tests_$name.log"
  [[ -f "$log" ]] || log="-"
  printf '%-14s %-8s %6s %6s %6s %6s %s\n' "$name" "$verdict" "$passed" "$failed" "$errors" "$skips" "$log"
done
echo
printf 'total: %s passed, %s failed, %s errors, %s skipped, %s chunk(s) not clean\n' \
  "$TOTAL_PASSED" "$TOTAL_FAILED" "$TOTAL_ERRORS" "$TOTAL_SKIPS" "$FAILED"
if [[ $SKIPPED -gt 0 ]]; then
  echo "note: $SKIPPED chunk(s) were skipped because PostgreSQL is unavailable; this run is incomplete."
fi

if [[ $FAILED -gt 0 ]]; then
  exit 1
fi
exit 0
