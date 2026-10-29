#!/usr/bin/env bash
# Untrack generated artifacts and file historical reports, without losing content.
#
# Dry run by default: `bash scripts/repo_cleanup.sh` prints every action it
# would take and changes nothing. `bash scripts/repo_cleanup.sh --apply`
# performs them.
#
# The script is idempotent -- running it twice, or running it on a repo that
# has already been cleaned, reports zero actions. It never deletes a file from
# the working tree: `git rm --cached` leaves the developer's copy exactly where
# it is and only removes it from the object database of every future clone.
# Historical reports and prompt logs are *moved* with `git mv`, so their
# content stays in git under docs/archive/history/.
#
# Exit codes: 0 success (or nothing to do), 2 the script cannot run.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

APPLY=0
JSON_OUT=""
VERBOSE=0
for arg in "$@"; do
  case "$arg" in
    --apply)   APPLY=1 ;;
    --json)    JSON_OUT="${JSON_OUT:-/dev/stdout}" ;;
    --verbose) VERBOSE=1 ;;
    -h|--help)
      sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "repo_cleanup: unknown argument '$arg' (try --help)" >&2; exit 2 ;;
  esac
done

command -v git >/dev/null 2>&1 || { echo "repo_cleanup: git is not installed" >&2; exit 2; }
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || {
  echo "repo_cleanup: $REPO_ROOT is not a git working tree" >&2; exit 2; }

HISTORY_DIR="docs/archive/history"
# Historical reports and the batch-apply scripts that produced them. They are
# genuine project history, so they are moved -- not untracked -- into
# docs/archive/history/.
HISTORY_FILES=(
  PROMPT2_REPORT.md
  PROMPT2_FINAL_REPORT.md
  PROMPT2_IMPLEMENTATION_REPORT.md
  PART_0_REPORT.md
  PART_1_REPORT.md
  PROMPT1_TO_PROMPT2_ISSUES.md
  PROMPT1_TO_PROMPT2_FIXES_APPLIED.md
  apply_batch01.sh
  apply_batch02.sh
  apply_batch03.sh
  apply_batch04.sh
  apply_batch05.sh
  apply_batch06.sh
  apply_batch07.sh
)

MAX_BYTES=1000000
ALLOWLIST_FILE="scripts/tracked_artifact_allowlist.txt"

declare -a UNTRACK_LOG=() UNTRACK_ZIP=() UNTRACK_LARGE=() MOVED=() GITIGNORE_ADDED=()
COUNT_UNTRACK_LOG=0; COUNT_UNTRACK_ZIP=0; COUNT_UNTRACK_LARGE=0; COUNT_MOVED=0

log()  { [[ $VERBOSE -eq 1 ]] && printf '%s\n' "$*"; return 0; }
act()  { printf '  %-12s %s\n' "$1" "$2"; }
run()  { if [[ $APPLY -eq 1 ]]; then "$@"; fi; }

# --- parse the allowlist -----------------------------------------------------
declare -A ALLOWED=()
if [[ -f "$ALLOWLIST_FILE" ]]; then
  while IFS= read -r line; do
    line="${line%%#*}"
    line="$(printf '%s' "$line" | tr -d '[:space:]')"
    [[ -n "$line" ]] && ALLOWED["$line"]=1
  done < "$ALLOWLIST_FILE"
fi

echo "voxdesk repo cleanup -- $( [[ $APPLY -eq 1 ]] && echo APPLY || echo 'DRY RUN')"
echo "repository: $REPO_ROOT"
echo

# --- 1. generated logs -------------------------------------------------------
echo "1. tracked *.log files"
while IFS= read -r -d '' path; do
  [[ -n "$path" ]] || continue
  COUNT_UNTRACK_LOG=$((COUNT_UNTRACK_LOG + 1))
  UNTRACK_LOG+=("$path")
  act "untrack" "$path"
  run git rm --cached --quiet -- "$path"
done < <(git ls-files -z -- '*.log')
[[ $COUNT_UNTRACK_LOG -eq 0 ]] && echo "  (none -- already clean)"
echo

# --- 2. delivery archives ----------------------------------------------------
echo "2. tracked *.zip archives"
while IFS= read -r -d '' path; do
  [[ -n "$path" ]] || continue
  COUNT_UNTRACK_ZIP=$((COUNT_UNTRACK_ZIP + 1))
  UNTRACK_ZIP+=("$path")
  act "untrack" "$path"
  run git rm --cached --quiet -- "$path"
done < <(git ls-files -z -- '*.zip')
[[ $COUNT_UNTRACK_ZIP -eq 0 ]] && echo "  (none -- already clean)"
echo

# --- 3. oversized tracked files ---------------------------------------------
echo "3. tracked files larger than ${MAX_BYTES} bytes (outside the allowlist)"
while IFS= read -r -d '' path; do
  [[ -n "$path" ]] || continue
  [[ -f "$path" ]] || continue
  [[ -n "${ALLOWED[$path]:-}" ]] && { log "allowlisted, keeping: $path"; continue; }
  size=$(wc -c < "$path")
  if [[ "$size" -gt "$MAX_BYTES" ]]; then
    COUNT_UNTRACK_LARGE=$((COUNT_UNTRACK_LARGE + 1))
    UNTRACK_LARGE+=("$path")
    act "untrack" "$path (${size} bytes)"
    run git rm --cached --quiet -- "$path"
  fi
done < <(git ls-files -z)
[[ $COUNT_UNTRACK_LARGE -eq 0 ]] && echo "  (none -- already clean)"
echo

# --- 4. historical reports and apply scripts ---------------------------------
echo "4. historical reports and batch-apply scripts -> $HISTORY_DIR/"
for path in "${HISTORY_FILES[@]}"; do
  [[ -f "$path" ]] || { log "absent, skipping: $path"; continue; }
  target="$HISTORY_DIR/$(basename "$path")"
  if [[ "$path" == "$target" ]]; then
    log "already archived: $path"
    continue
  fi
  COUNT_MOVED=$((COUNT_MOVED + 1))
  MOVED+=("$path -> $target")
  act "move" "$path -> $target"
  if [[ $APPLY -eq 1 ]]; then
    mkdir -p "$(dirname "$target")"
    if git mv -f "$path" "$target" 2>/dev/null; then :; else
      mv -f "$path" "$target"
      git add -- "$target"
      git rm --cached --quiet -- "$path" 2>/dev/null || true
    fi
  fi
done
[[ $COUNT_MOVED -eq 0 ]] && echo "  (none -- already archived)"
echo

# --- 5. .gitignore -----------------------------------------------------------
echo "5. .gitignore guard block"
BEGIN="# >>> voxdesk:repo-cleanup >>>"
END="# <<< voxdesk:repo-cleanup <<<"
BLOCK=$(cat <<'EOF'
# >>> voxdesk:repo-cleanup >>>
# Managed by scripts/repo_cleanup.sh -- keep the markers intact.
# Run output from local runs and CI.
*.log
logs/
# Previous delivery archives; the current one is produced by scripts/make_deliverable.sh.
*.zip
# Generated evidence: regenerate it, do not commit it (see scripts/verify_no_tracked_artifacts.py).
reports/*.json
reports/**/*.json
evidence/
coverage.xml
htmlcov/
.pytest_cache/
# <<< voxdesk:repo-cleanup <<<
EOF
)
if [[ -f .gitignore ]] && grep -qF "$BEGIN" .gitignore; then
  echo "  (present -- unchanged)"
else
  act "append" ".gitignore"
  GITIGNORE_ADDED+=(".gitignore")
  if [[ $APPLY -eq 1 ]]; then
    [[ -f .gitignore ]] && [[ -n "$(tail -c 1 .gitignore)" ]] && printf '\n' >> .gitignore
    printf '%s\n' "$BLOCK" >> .gitignore
  fi
fi
echo

# --- summary -----------------------------------------------------------------
TOTAL=$((COUNT_UNTRACK_LOG + COUNT_UNTRACK_ZIP + COUNT_UNTRACK_LARGE + COUNT_MOVED))
echo "-----------------------------------------------------------------"
printf '%-28s %s\n' "logs to untrack"        "$COUNT_UNTRACK_LOG"
printf '%-28s %s\n' "archives to untrack"    "$COUNT_UNTRACK_ZIP"
printf '%-28s %s\n' "oversized to untrack"   "$COUNT_UNTRACK_LARGE"
printf '%-28s %s\n' "reports moved to history" "$COUNT_MOVED"
printf '%-28s %s\n' "total actions"          "$TOTAL"
echo "-----------------------------------------------------------------"
if [[ $APPLY -eq 0 ]]; then
  if [[ $TOTAL -eq 0 ]]; then
    echo "Nothing to do; the repository is already clean."
  else
    echo "Dry run only. Re-run with --apply to perform these actions."
    echo "Nothing was changed."
  fi
else
  echo "Applied. Review with 'git status' and commit:"
  echo "  git commit -m 'chore(repo): untrack generated artifacts, archive historical reports'"
fi

if [[ -n "$JSON_OUT" ]]; then
  python3 - "$JSON_OUT" <<'PYEOF'
import json, sys, os
out = sys.argv[1]
payload = {
    "schema": "voxdesk.repo_cleanup/v1",
    "apply": os.environ.get("APPLY") == "1",
    "counts": {
        "untrack_log": int(os.environ.get("COUNT_UNTRACK_LOG", 0)),
        "untrack_zip": int(os.environ.get("COUNT_UNTRACK_ZIP", 0)),
        "untrack_large": int(os.environ.get("COUNT_UNTRACK_LARGE", 0)),
        "moved": int(os.environ.get("COUNT_MOVED", 0)),
        "total": int(os.environ.get("TOTAL", 0)),
    },
    "untracked_logs": os.environ.get("UNTRACK_LOG", "").split(":") if os.environ.get("UNTRACK_LOG") else [],
    "untracked_archives": os.environ.get("UNTRACK_ZIP", "").split(":") if os.environ.get("UNTRACK_ZIP") else [],
    "untracked_large": os.environ.get("UNTRACK_LARGE", "").split(":") if os.environ.get("UNTRACK_LARGE") else [],
    "moved_files": os.environ.get("MOVED", "").split(":") if os.environ.get("MOVED") else [],
}
text = json.dumps(payload, indent=2) + "\n"
if out == "/dev/stdout":
    sys.stdout.write(text)
else:
    with open(out, "w", encoding="utf-8") as handle:
        handle.write(text)
PYEOF
fi

exit 0
