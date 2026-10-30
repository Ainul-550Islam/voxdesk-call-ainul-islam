#!/usr/bin/env bash
# apply_batch05.sh — Idempotent post-unzip wiring & verification script for Batch 05 (Ecosystem Integrations v2).
# Safe to run multiple times; every shared-file hook is guarded by `# BATCH05:<id>`.
set -euo pipefail

HOOKS_ONLY=0
ZIP_ARG=""
for arg in "$@"; do
  case "${arg}" in
    --hooks-only)
      HOOKS_ONLY=1
      ;;
    *.zip)
      ZIP_ARG="${arg}"
      ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "${SCRIPT_DIR}/app/main.py" ]]; then
  REPO_ROOT="${SCRIPT_DIR}"
elif [[ -f "${SCRIPT_DIR}/voxdesk-call/app/main.py" ]]; then
  REPO_ROOT="${SCRIPT_DIR}/voxdesk-call"
elif [[ -f "./app/main.py" ]]; then
  REPO_ROOT="$(pwd)"
elif [[ -f "./voxdesk-call/app/main.py" ]]; then
  REPO_ROOT="$(cd ./voxdesk-call && pwd)"
else
  echo "ERROR: Could not locate VoxDesk application root (app/main.py)." >&2
  exit 1
fi
cd "${REPO_ROOT}"

PYTHON_BIN="${PYTHON:-python3}"

# Pre-flight: Batch 05 migration 0067_integrations_v2 chains directly after Batch 04's 0066_*.py.
if ! compgen -G "alembic/versions/0066_*.py" > /dev/null; then
  echo "ERROR: Batch 04 migration (alembic/versions/0066_*.py) not found." >&2
  echo "Apply Batches 01–04 before running apply_batch05.sh so Alembic maintains a single linear head." >&2
  exit 1
fi

if [[ -n "${ZIP_ARG}" && -f "${ZIP_ARG}" ]]; then
  unzip -qo "${ZIP_ARG}" -d "${REPO_ROOT}"
fi

"${PYTHON_BIN}" - <<'PY'
from __future__ import annotations

import pathlib
import sys

root = pathlib.Path(".").resolve()


def inject_after(path: pathlib.Path, anchor: str, marker: str, payload: str) -> None:
    if not path.exists():
        raise SystemExit(f"Missing target shared file: {path}")
    text = path.read_text(encoding="utf-8")
    if marker in text:
        return
    if anchor not in text:
        raise SystemExit(f"Anchor {anchor!r} not found in {path}")
    updated = text.replace(anchor, f"{anchor}\n{payload}", 1)
    path.write_text(updated, encoding="utf-8")


def append_once(path: pathlib.Path, marker: str, payload: str) -> None:
    if not path.exists():
        raise SystemExit(f"Missing target shared file: {path}")
    text = path.read_text(encoding="utf-8")
    if marker in text:
        return
    sep = "" if text.endswith("\n") else "\n"
    path.write_text(f"{text}{sep}{payload}\n", encoding="utf-8")


# 1. app/auth/permissions.py — add INTEGRATION_ADMIN alias on Permission enum
inject_after(
    root / "app/auth/permissions.py",
    '    INTEGRATION_WRITE = "integration:write"',
    "# BATCH05:permissions",
    '    INTEGRATION_ADMIN = "integration:write"  # BATCH05:permissions',
)

# 2. alembic/env.py — register Batch 05 ORM metadata
inject_after(
    root / "alembic/env.py",
    "from app.db.models import Base",
    "# BATCH05:integrations_v2_models",
    "import app.db.integrations_v2_models  # noqa: F401  # BATCH05:integrations_v2_models",
)

# 3. app/services/public_auth_boundary_service.py — allow HMAC-verified inbound webhooks through public boundary
inject_after(
    root / "app/services/public_auth_boundary_service.py",
    '    "/api/v1/public/health",',
    "# BATCH05:public_webhooks",
    '    "/webhooks/int2/",  # BATCH05:public_webhooks',
)

# 4. app/integrations/connector.py — register all 11 v2 providers into canonical connector registry
append_once(
    root / "app/integrations/connector.py",
    "# BATCH05:registry",
    (
        "\n# BATCH05:registry\n"
        "from app.integrations_v2.register_all import register_v2_into_connector_registry as _b05_register  # noqa: E402\n"
        "_b05_register()"
    ),
)

# 5. app/main.py — ensure ORM models loaded + include Batch 05 routers
inject_after(
    root / "app/main.py",
    "app.include_router(sms_router)",
    "# BATCH05:integrations_v2_routes",
    (
        "# BATCH05:integrations_v2_routes\n"
        "from app.db import integrations_v2_models as _b05_models  # noqa: E402, F401\n"
        "from app.api.integrations_v2_routes import router as integrations_v2_router  # noqa: E402\n"
        "from app.api.integrations_v2_webhooks import router as integrations_v2_webhooks_router  # noqa: E402\n"
        "from app.api.helpdesk_routes import router as helpdesk_v2_router  # noqa: E402\n"
        "from app.api.collab_routes import router as collab_v2_router  # noqa: E402\n"
        "from app.api.agent_tools_v2_routes import router as agent_tools_v2_router  # noqa: E402\n"
        "app.include_router(integrations_v2_router)\n"
        "app.include_router(integrations_v2_webhooks_router)\n"
        "app.include_router(helpdesk_v2_router)\n"
        "app.include_router(collab_v2_router)\n"
        "app.include_router(agent_tools_v2_router)"
    ),
)

# 6. scripts/scheduler.py — wire Batch 05 background token-refresh, sync, webhook-renewal, and writeback-outbox loop
sched_path = root / "scripts/scheduler.py"
if sched_path.exists():
    inject_after(
        sched_path,
        "async def main() -> None:",
        "# BATCH05:integrations_v2_jobs",
        (
            "    # BATCH05:integrations_v2_jobs\n"
            "    async def integrations_v2_loop() -> None:\n"
            "        from app.integrations_v2.jobs import run_scheduled_tick\n"
            "        maker = get_sessionmaker()\n"
            "        while not _stop.is_set():\n"
            "            try:\n"
            "                async with maker() as db:\n"
            "                    await run_scheduled_tick(db)\n"
            "                    await db.commit()\n"
            "                observability.record_job_run('integrations_v2', ok=True)\n"
            "            except Exception as exc:\n"
            "                observability.record_job_run('integrations_v2', ok=False)\n"
            "                logger.error('scheduler.integrations_v2_failed', error=str(exc)[:300])\n"
            "            await _sleep(60)"
        ),
    )
    inject_after(
        sched_path,
        "        durable_jobs_loop(), outbox_dispatch_loop(),",
        "# BATCH05:integrations_v2_jobs_gather",
        "        integrations_v2_loop(),  # BATCH05:integrations_v2_jobs_gather",
    )

# 7. Makefile — include Makefile.batch05.mk
append_once(
    root / "Makefile",
    "# BATCH05:makefile",
    "-include Makefile.batch05.mk  # BATCH05:makefile",
)

# 8. dashboard-next/vitest.config.ts — include app/integrations-v2/__tests__/**/*.test.tsx
vitest_cfg = root / "dashboard-next/vitest.config.ts"
if vitest_cfg.exists():
    vt_text = vitest_cfg.read_text(encoding="utf-8")
    if "BATCH05:vitest" not in vt_text:
        if '"components/**/*.test.tsx"]' in vt_text:
            vt_text = vt_text.replace(
                '"components/**/*.test.tsx"]',
                '"components/**/*.test.tsx", "app/integrations-v2/__tests__/**/*.test.tsx" /* BATCH05:vitest */]',
                1,
            )
            vitest_cfg.write_text(vt_text, encoding="utf-8")
        else:
            inject_after(
                vitest_cfg,
                '"tests/**/*.test.tsx",',
                "BATCH05:vitest",
                '      "app/integrations-v2/__tests__/**/*.test.tsx", /* BATCH05:vitest */',
            )

sys.stdout.write("Batch 05 shared-file hooks applied cleanly.\n")
PY

if [[ -f "scripts/build_api_docs.sh" ]]; then
  PYTHON="${PYTHON_BIN}" bash scripts/build_api_docs.sh >/dev/null 2>&1 || "${PYTHON_BIN}" - <<'PY'
import json
import pathlib
from app.main import app

out = pathlib.Path("contracts/openapi.json")
if out.parent.exists():
    out.write_text(json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
fi

if [[ -f "tests/truth/routes_snapshot.json" ]]; then
  "${PYTHON_BIN}" - <<'PY'
from __future__ import annotations

import json
import pathlib
from app.main import app

snap_path = pathlib.Path("tests/truth/routes_snapshot.json")
build_present = pathlib.Path("dashboard/dist/index.html").is_file()
routes_payload = []
for route in app.routes:
    methods = sorted(getattr(route, "methods", None) or [])
    routes_payload.append(
        {
            "methods": methods,
            "name": getattr(route, "name", ""),
            "path": getattr(route, "path", ""),
        }
    )
snap_path.write_text(
    json.dumps(
        {
            "dashboard_build_present": build_present,
            "route_count": len(app.routes),
            "routes": routes_payload,
        },
        indent=2,
        sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
)
PY
fi

if [[ -f "scripts/repo_stats.py" ]]; then
  "${PYTHON_BIN}" scripts/repo_stats.py --write-readme >/dev/null
fi

if [[ "${HOOKS_ONLY}" -eq 1 ]]; then
  echo "Batch 05 hooks applied (--hooks-only)."
  exit 0
fi

# Verify single linear head 0067_integrations_v2 (and run live PostgreSQL migration if DATABASE_URL is provided)
HEAD_OUT="$("${PYTHON_BIN}" -m alembic heads)"
echo "${HEAD_OUT}"
if [[ "$(echo "${HEAD_OUT}" | grep -c '0067_integrations_v2')" -ne 1 ]] || [[ "$(echo "${HEAD_OUT}" | wc -l)" -ne 1 ]]; then
  echo "ERROR: Expected single Alembic head 0067_integrations_v2, got: ${HEAD_OUT}" >&2
  exit 1
fi
if [[ -n "${DATABASE_URL:-}" ]]; then
  "${PYTHON_BIN}" -m alembic upgrade head
fi

"${PYTHON_BIN}" -m ruff check app tests scripts
make verify-truth PYTHON="${PYTHON_BIN}"
"${PYTHON_BIN}" -m pytest -m "not live" tests/integrations_v2 -q

if [[ -d "dashboard-next" ]]; then
  (
    cd dashboard-next
    if [[ ! -d "node_modules" ]]; then
      npm ci --silent
    fi
    npx tsc --noEmit
    npx vitest run
    npm run build
  )
fi

echo "Batch 05 applied and verified successfully."
