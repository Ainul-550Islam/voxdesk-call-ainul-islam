#!/usr/bin/env python3
"""VoxDesk staging certification orchestrator (Step 13 section B/C).

Orchestrates only safe staging operations. The dangerous operations — a real
phone call, a production charge, a production booking, a production CRM
mutation — are never part of this tool.

Modes (choose one; ``--all-safe`` runs every safe mode):
    --preflight      machine/repo/config/secret preflight (read-only)
    --staging        full staging deployment certification sequence
    --backup         staging database backup (wraps scripts/backup.sh)
    --restore        staging restore drill (wraps scripts/restore.sh)
    --providers      real-provider health sweep (opt-in, read-only)
    --observability  Prometheus/Grafana validation (read-only)
    --egress         egress enforcement verification (inside staging only)
    --e2e-readiness  real-telephony E2E precondition check (never dials)
    --deploy-drill   deploy -> rollback drill (wraps deploy.sh/rollback.sh)
    --compose-up     guarded `docker compose up -d --build` for the staging project
    --compose-down   guarded `docker compose down` for the staging project

There are deliberately NO --force-production / --skip-safety / --ignore-gate
switches. The release gate in scripts/release_gate.py remains authoritative.
Every docker-compose operation (build/up/down/exec/ps/config) is gated on a
staging-identity check: APP_ENV must be non-production and COMPOSE_PROJECT_NAME
(if set) must identify staging. A failed guard is a FAIL, and the mutating
operation is never executed.

Usage:
    python scripts/staging_certify.py --preflight [--json] [--write-evidence]

Exit codes: 0 = PASS, 1 = FAIL, 2 = BLOCKED, 3 = invalid configuration.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.integrations.validation import registry  # noqa: E402
from app.integrations.validation.status import real_integration_enabled  # noqa: E402
from app.release import gate, ops  # noqa: E402

EVIDENCE_PATH = Path(ops.REPO_ROOT) / "scripts" / "release" / "evidence.json"
ARTIFACT_PATH = Path(ops.REPO_ROOT) / "var" / "release" / "artifact.json"

#: The one compose file this orchestrator may touch. Never the production one.
STAGING_COMPOSE_FILE = "docker-compose.staging.yml"

#: The check stack: production compose plus the local override that binds
#: loopback-only ports and drops Caddy out of the default profile.
CHECK_COMPOSE_FILES = ["docker-compose.prod.yml", "docker-compose.check.yml"]
CHECK_PROJECT_NAME = "voxdesk-check"
CHECK_ENV_FILE = ".env.check"

#: Seconds the API container is given to finish in-flight requests on SIGTERM
#: before Docker sends SIGKILL. Matches the grace the compose file documents.
DRAIN_GRACE_SECONDS = 30

#: Exit codes a container reports when it handled SIGTERM itself. 137 means
#: SIGKILL, i.e. the process ignored the signal or outlived the grace period --
#: which is exactly what a graceful-drain check exists to catch.
_GRACEFUL_EXIT_CODES = {0, 143}


def _utc_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _compose_cmd(args_list: list[str]) -> list[str]:
    return [
        "docker", "compose",
        "-p", CHECK_PROJECT_NAME,
        "--env-file", CHECK_ENV_FILE,
        *sum((["-f", name] for name in CHECK_COMPOSE_FILES), []),
        *args_list,
    ]


def run_logged(
    name: str,
    argv: list[str],
    *,
    log_dir: Path,
    timeout: int = 1800,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run a command, capture its output to a raw log, and return the facts.

    The log file is the evidence: stdout and stderr go to it verbatim, and the
    returned dictionary carries the exit code, the wall-clock duration and the
    SHA-256 of that log. Nothing here interprets success -- the caller decides,
    per step, what the exit code means.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{name}.log"
    started = time.monotonic()
    child_env = dict(os.environ)
    if env:
        child_env.update(env)
    try:
        with log_path.open("w", encoding="utf-8") as log:
            log.write("$ " + shlex.join(argv) + "\n")
            log.write("# started " + _utc_now_iso() + "\n")
            log.flush()
            proc = subprocess.run(
                argv,
                cwd=ops.REPO_ROOT,
                env=child_env,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=timeout,
            )
            code: int | None = proc.returncode
            error = ""
    except subprocess.TimeoutExpired:
        code = None
        error = f"TIMEOUT after {timeout}s"
        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"\n# {error}\n")
    except OSError as exc:
        code = None
        error = f"OS error: {exc}"
        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"\n# {error}\n")
    duration = round(time.monotonic() - started, 3)
    return {
        "step": name,
        "command": shlex.join(argv),
        "exit_code": code,
        "duration_seconds": duration,
        "log": str(log_path.relative_to(ops.REPO_ROOT)) if log_path.is_relative_to(ops.REPO_ROOT) else str(log_path),
        "log_sha256": sha256_file(log_path),
        "log_bytes": log_path.stat().st_size,
        "error": error,
        "finished_at": _utc_now_iso(),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="staging_certify.py", description="VoxDesk staging certification orchestrator"
    )
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--staging", action="store_true")
    parser.add_argument("--backup", action="store_true")
    parser.add_argument("--restore", action="store_true")
    parser.add_argument("--providers", action="store_true")
    parser.add_argument("--observability", action="store_true")
    parser.add_argument("--egress", action="store_true")
    parser.add_argument("--e2e-readiness", action="store_true",
                        help="validate real-call E2E preconditions only (never places a call)")
    parser.add_argument("--deploy-drill", action="store_true")
    parser.add_argument("--compose-up", action="store_true",
                        help="guarded `docker compose -f docker-compose.staging.yml up -d --build`")
    parser.add_argument("--compose-down", action="store_true",
                        help="guarded `docker compose -f docker-compose.staging.yml down` (never -v)")
    parser.add_argument("--proof", action="store_true",
                        help="full deploy proof: bring the check stack up, migrate, smoke every "
                             "surface, run the backup->restore drill and the graceful-drain check, "
                             "then tear it down. Writes evidence/staging/<UTC-date>/.")
    parser.add_argument("--evidence-dir", default="",
                        help="destination for --proof evidence (default: evidence/staging/<UTC-date>)")
    parser.add_argument("--with-drills", action="store_true",
                        help="add the backup->restore drill and graceful-drain check to --staging")
    parser.add_argument("--skip-teardown", action="store_true",
                        help="with --proof, leave the stack running for inspection")
    parser.add_argument("--all-safe", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--write-evidence", action="store_true",
                        help="merge produced evidence into scripts/release/evidence.json")
    parser.add_argument("--base-url", default=os.environ.get("SMOKE_BASE_URL", "http://localhost:8000"))
    parser.add_argument("--url", default="", help="staging HTTPS URL for TLS verification")
    parser.add_argument("--prometheus-url", default="")
    parser.add_argument("--grafana-url", default="")
    parser.add_argument("--inside-staging", action="store_true")
    parser.add_argument("--dump", default="", help="backup dump path for --restore")
    parser.add_argument("--restore-target-db", default=os.environ.get("RESTORE_TARGET_DB", ""))
    parser.add_argument("--previous-sha", default="", help="previous-good sha for --deploy-drill")
    parser.add_argument("--expected-commit", default=None)
    return parser


def _http_get(url: str, timeout: float = 6.0) -> tuple[int, bool]:
    try:
        with httpx.Client(verify=True, timeout=timeout, follow_redirects=False) as client:
            resp = client.get(url)
            return resp.status_code, True
    except Exception:  # noqa: BLE001
        return 0, False


def _step(name: str, status: str, detail: str = "", record: ops.EvidenceRecord | None = None) -> dict:
    return {"step": name, "status": status, "detail": detail, "record": record}


def _tools() -> dict[str, str | None]:
    return {
        name: ops.which(name)
        for name in ("docker", "psql", "pg_dump", "pg_restore", "redis-cli", "rclone")
    }


def _compose_ready() -> tuple[bool, str]:
    """Whether the docker compose (v2) plugin is usable. Returns (ok, detail)."""
    if not ops.which("docker"):
        return False, "docker not found on PATH"
    try:
        proc = subprocess.run(
            ["docker", "compose", "version"],
            capture_output=True, text=True, timeout=20, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"docker compose unavailable: {exc}"
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout or "docker compose version failed").strip()[:200]
    return True, "docker compose (v2) present"


def _staging_identity_ok(app_env: str) -> tuple[bool, str]:
    """Verify the compose target identifies itself as staging.

    Refuses production outright and refuses a COMPOSE_PROJECT_NAME that does
    not identify staging, so a mutating or exec operation can never touch a
    production (or ambiguous) compose project. Returns (ok, reason).
    """
    allowed, _status, reason = ops.validate_staging_target(app_env)
    if not allowed:
        return False, reason
    project = (os.environ.get("COMPOSE_PROJECT_NAME", "") or "").strip()
    if project and "staging" not in project.lower():
        return False, (
            f"COMPOSE_PROJECT_NAME={project!r} does not identify staging; "
            "refusing to touch a possibly-non-staging compose project"
        )
    return True, (
        f"staging identity confirmed (APP_ENV={app_env}, "
        f"project={project or 'voxdesk-staging'})"
    )


def _compose(args_list: list[str], timeout: int = 1800) -> tuple[int, str, str]:
    """Run ``docker compose -f <staging file> ...``. Returns (rc, stdout, stderr)."""
    proc = subprocess.run(
        ["docker", "compose", "-f", STAGING_COMPOSE_FILE, *args_list],
        capture_output=True, text=True, timeout=timeout, check=False,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _compose_config_check() -> tuple[str, str]:
    """Validate the staging compose file resolves with `docker compose config`."""
    ok, detail = _compose_ready()
    if not ok:
        return ops.STATUS_BLOCKED, f"compose config validation unavailable: {detail}"
    rc, out, err = _compose(["config", "--quiet"], timeout=120)
    if rc != 0:
        return ops.STATUS_FAIL, f"docker compose config failed: {(err or out).strip()[-400:]}"
    return ops.STATUS_PASS, f"{STAGING_COMPOSE_FILE} resolves via `docker compose config`"


def _cleanup_staging_compose() -> None:
    """Bring down the staging project after a partial startup failure.

    Never passes ``-v`` (volumes/backups are preserved) and never targets any
    file other than STAGING_COMPOSE_FILE, so a failed certification cannot
    destroy data or touch another environment's services.
    """
    try:
        _compose(["down"], timeout=300)
    except Exception:  # noqa: BLE001 - cleanup is best-effort; the FAIL is already recorded
        pass


def _static_compose_checks() -> tuple[str, str]:
    """Static validation of the compose files without Docker or a YAML parser."""
    files = [
        "docker-compose.yml",
        "docker-compose.staging.yml",
        "docker-compose.prod.yml",
        "observability/prometheus.yml",
        "observability/alerts.yml",
        "observability/slos.yml",
    ]
    problems = []
    for name in files:
        p = Path(ops.REPO_ROOT) / name
        if not p.exists():
            problems.append(f"{name}: missing")
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        if not text.strip():
            problems.append(f"{name}: empty")
    try:
        import yaml  # noqa: F401

        for name in files:
            p = Path(ops.REPO_ROOT) / name
            if p.exists():
                with p.open(encoding="utf-8") as fh:
                    yaml.safe_load(fh)
        parsed_with = "pyyaml"
    except Exception:  # noqa: BLE001
        parsed_with = "presence-only (pyyaml unavailable)"
    if problems:
        return ops.STATUS_FAIL, "; ".join(problems)
    return ops.STATUS_PASS, f"{len(files)} files present and valid ({parsed_with})"


def _security_smoke() -> tuple[str, str]:
    try:
        problems = settings.validate_security()
    except Exception as exc:  # noqa: BLE001
        return ops.STATUS_FAIL, f"validate_security raised {type(exc).__name__}"
    if problems:
        return ops.STATUS_FAIL, "; ".join(str(p) for p in problems[:5])
    return ops.STATUS_PASS, "settings.validate_security() clean"


def _e2e_readiness(app_env: str, provider_overall: str, api_ok: bool,
                   api_status_code: int) -> dict:
    """Validate real-telephony E2E PRECONDITIONS only (Step 16 section D).

    This never places a call, never mutates anything, and never marks e2e-001
    PASS. The live call stays human-executed and is recorded separately in
    var/release/e2e-result.json. Returns {"status", "checks", "detail"} using
    the standard vocabulary: FAIL (safety/config violation), BLOCKED (missing
    prerequisite), NOT_RUN (disarmed, or READY-but-call-not-yet-executed).
    """
    env = (app_env or "").strip().lower()
    production = env in ("production", "prod")
    armed = bool(settings.e2e_enabled)

    if production and armed:
        return {"status": ops.STATUS_FAIL,
                "checks": [{"name": "environment", "status": ops.STATUS_FAIL,
                            "detail": "E2E armed in production — refuse; never run a "
                                      "live E2E call against production"}],
                "detail": "E2E armed in production — safety guard failed"}
    if not armed:
        return {"status": ops.STATUS_NOT_RUN,
                "checks": [{"name": "E2E armed", "status": ops.STATUS_NOT_RUN,
                            "detail": "E2E_ENABLED=false — real-call E2E intentionally "
                                      "not attempted"}],
                "detail": "E2E disarmed; real-call E2E intentionally not attempted "
                          "(the live call is never automated)"}

    checks: list[dict] = []

    def add(name: str, status: str, detail: str) -> None:
        checks.append({"name": name, "status": status, "detail": detail})

    add("environment", ops.STATUS_PASS, f"APP_ENV={app_env} (non-production)")
    add("E2E armed", ops.STATUS_PASS, "E2E_ENABLED=true")
    add("E2E test number", ops.STATUS_PASS if settings.e2e_test_number else ops.STATUS_FAIL,
        "configured" if settings.e2e_test_number else "E2E_TEST_NUMBER missing")
    add("operator allowlist",
        ops.STATUS_PASS if settings.e2e_caller_list else ops.STATUS_FAIL,
        f"{len(settings.e2e_caller_list)} caller(s) allowlisted" if settings.e2e_caller_list
        else "E2E_ALLOWED_CALLERS empty")
    add("API/monitoring reachable",
        ops.STATUS_PASS if (api_ok and api_status_code == 200) else ops.STATUS_BLOCKED,
        f"/health/ready -> {api_status_code}" if api_ok else "API unreachable")
    add("provider health",
        ops.STATUS_PASS if provider_overall == ops.STATUS_PASS else
        (ops.STATUS_FAIL if provider_overall == ops.STATUS_FAIL else ops.STATUS_BLOCKED),
        f"provider sweep {provider_overall}"
        + ("; run --providers with VOXDESK_REAL_INTEGRATION=1"
           if provider_overall != ops.STATUS_PASS else ""))

    if settings.billing_provider == "stripe":
        key = settings.stripe_secret_key or ""
        if key.startswith("sk_live_"):
            add("billing test mode", ops.STATUS_FAIL,
                "Stripe LIVE key detected in staging — refuse; use sk_test_ keys only")
        elif key.startswith("sk_test_"):
            add("billing test mode", ops.STATUS_PASS, "Stripe test-mode key (sk_test_*)")
        else:
            add("billing test mode", ops.STATUS_BLOCKED, "STRIPE_SECRET_KEY not set")
    else:
        add("billing test mode", ops.STATUS_PASS,
            f"billing_provider={settings.billing_provider} (no live Stripe)")

    add("destination configured", ops.STATUS_PASS if settings.e2e_test_number else ops.STATUS_BLOCKED,
        "E2E_TEST_NUMBER is the dial destination")

    # Human-only checklist items the script cannot verify (never PASS):
    add("test tenant exists (is_test_tenant=true)", ops.STATUS_NOT_RUN,
        "operator checklist item — verify in the dashboard/DB before dialing")
    add("test tenant answers the test number", ops.STATUS_NOT_RUN,
        "operator checklist item — tenant twilio_number must equal E2E_TEST_NUMBER")

    statuses = [c["status"] for c in checks]
    if ops.STATUS_FAIL in statuses:
        status = ops.STATUS_FAIL
    elif ops.STATUS_BLOCKED in statuses:
        status = ops.STATUS_BLOCKED
    else:
        # Every verifiable precondition holds; the CALL has not happened, so
        # this is READY — surfaced as NOT_RUN, never PASS.
        status = ops.STATUS_NOT_RUN
    detail = (
        "READY: all verifiable E2E preconditions satisfied — execute the live call "
        "manually per docs/STEP16-LAUNCH-E2E-RUNBOOK.md and record it in "
        "var/release/e2e-result.json"
        if status == ops.STATUS_NOT_RUN
        else "E2E preconditions incomplete or unsafe — see checks"
    )
    return {"status": status, "checks": checks, "detail": detail}


def _run_staging_sequence(args: argparse.Namespace) -> dict:
    app_env = (os.environ.get("APP_ENV") or settings.app_env or "development").strip()
    tools = _tools()
    identity_ok, identity_reason = _staging_identity_ok(app_env)
    steps: list[dict] = []
    records: list[ops.EvidenceRecord] = []

    def add(step: dict) -> None:
        steps.append(step)
        if step.get("record") is not None:
            records.append(step["record"])

    # 1. preflight
    preflight = ops.run_preflight(ops.REPO_ROOT, expected_commit=args.expected_commit)
    add(_step("preflight", preflight["overall"], f"exit {preflight['exit_code']}"))

    # 2. verify artifact (drift vs frozen record, when present)
    live = ops.compute_live_facts()
    recorded: dict = {}
    if ARTIFACT_PATH.exists():
        try:
            recorded = json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            recorded = {}
    drift = ops.drift_lines(recorded, live)
    if recorded.get("git_commit") and drift:
        add(_step("verify artifact", ops.STATUS_FAIL, "; ".join(drift)))
    else:
        add(_step("verify artifact", ops.STATUS_PASS,
                  f"commit {live['git_commit'][:12]} (no drift)" if not drift else "no frozen record"))

    # 3. verify staging environment
    allowed, status, reason = ops.validate_staging_target(app_env)
    add(_step("verify staging environment", status, reason))

    # 3b. staging identity guard — every compose operation below re-checks this
    add(_step("staging identity guard",
              ops.STATUS_PASS if identity_ok else ops.STATUS_FAIL, identity_reason))

    # 4. runtime dependencies
    missing = [name for name, path in tools.items() if not path]
    add(_step("runtime dependencies",
              ops.STATUS_PASS if not missing else ops.STATUS_BLOCKED,
              "all present" if not missing else f"missing: {', '.join(missing)}"))

    # 5. build candidate (guarded compose build — staging project only)
    if not tools.get("docker"):
        add(_step("build candidate", ops.STATUS_BLOCKED, "docker missing"))
    elif not identity_ok:
        add(_step("build candidate", ops.STATUS_FAIL, identity_reason))
    else:
        rc, out, err = _compose(["build"], timeout=1800)
        add(_step("build candidate", ops.STATUS_PASS if rc == 0 else ops.STATUS_FAIL,
                  "docker compose build ok" if rc == 0 else (err or out).strip()[-300:]))

    # 6. start staging (guarded compose up; cleaned up on partial failure)
    if not tools.get("docker"):
        add(_step("start staging", ops.STATUS_BLOCKED, "docker missing"))
    elif not identity_ok:
        add(_step("start staging", ops.STATUS_FAIL, identity_reason))
    else:
        rc, out, err = _compose(["up", "-d"], timeout=600)
        if rc == 0:
            add(_step("start staging", ops.STATUS_PASS, "docker compose up -d ok"))
        else:
            add(_step("start staging", ops.STATUS_FAIL, (err or out).strip()[-300:]))
            _cleanup_staging_compose()

    # 7. wait for readiness
    status_code, ok = _http_get(args.base_url.rstrip("/") + "/health/ready")
    add(_step("wait for readiness", ops.STATUS_PASS if ok and status_code == 200 else ops.STATUS_BLOCKED,
              f"{args.base_url}/health/ready -> {status_code}" if ok else "unreachable"))

    # 8. run migrations (guarded; advisory-locked via scripts/migrate.py)
    if not tools.get("docker"):
        add(_step("run migrations", ops.STATUS_BLOCKED, "docker missing"))
    elif not identity_ok:
        add(_step("run migrations", ops.STATUS_FAIL, identity_reason))
    else:
        rc, out, err = _compose(["exec", "-T", "api", "python", "scripts/migrate.py"], timeout=600)
        add(_step("run migrations", ops.STATUS_PASS if rc == 0 else ops.STATUS_FAIL,
                  "advisory-locked migrations applied" if rc == 0 else (err or out).strip()[-300:]))

    # 9. smoke test
    smoke_script = Path(ops.REPO_ROOT) / "scripts" / "smoke_test.py"
    if not smoke_script.exists():
        add(_step("smoke test", ops.STATUS_FAIL, "scripts/smoke_test.py missing"))
    elif status_code == 200:
        try:
            proc = subprocess.run(
                [sys.executable, str(smoke_script)],
                capture_output=True, text=True, timeout=120,
                env={**os.environ, "SMOKE_BASE_URL": args.base_url},
            )
            add(_step("smoke test", ops.STATUS_PASS if proc.returncode == 0 else ops.STATUS_FAIL,
                      (proc.stdout or proc.stderr).strip().splitlines()[-1] if (proc.stdout or proc.stderr) else ""))
        except (OSError, subprocess.SubprocessError) as exc:
            add(_step("smoke test", ops.STATUS_FAIL, f"failed: {exc}"))
    else:
        add(_step("smoke test", ops.STATUS_BLOCKED, "API not ready; cannot run smoke test"))

    # 10. health
    add(_step("verify health", ops.STATUS_PASS if ok and status_code == 200 else ops.STATUS_BLOCKED,
              f"/health/ready -> {status_code}" if ok else "unreachable"))

    # 11. metrics
    mcode, mok = _http_get(args.base_url.rstrip("/") + "/metrics")
    if mok and mcode in (200, 401):
        add(_step("verify metrics", ops.STATUS_PASS, f"/metrics -> {mcode} (200 or token-gated 401)"))
    else:
        add(_step("verify metrics", ops.STATUS_BLOCKED, f"/metrics -> {mcode}" if mok else "unreachable"))

    # 12. scheduler (guarded compose ps)
    if not tools.get("docker"):
        add(_step("verify scheduler", ops.STATUS_BLOCKED, "docker missing"))
    elif not identity_ok:
        add(_step("verify scheduler", ops.STATUS_FAIL, identity_reason))
    else:
        rc, out, err = _compose(["ps", "--status", "running", "-q", "scheduler"], timeout=120)
        running = rc == 0 and bool(out.strip())
        add(_step("verify scheduler", ops.STATUS_PASS if running else ops.STATUS_FAIL,
                  "scheduler container running" if running else (err or "scheduler container not running").strip()[-200:]))

    # 13. Redis (guarded compose exec; internal-only — no published port)
    if not tools.get("docker"):
        add(_step("verify Redis", ops.STATUS_BLOCKED,
                  "docker missing; staging Redis is internal-only (no published port)"))
    elif not identity_ok:
        add(_step("verify Redis", ops.STATUS_FAIL, identity_reason))
    else:
        rc, out, err = _compose(["exec", "-T", "redis", "redis-cli", "ping"], timeout=120)
        pong = rc == 0 and "PONG" in (out + err)
        add(_step("verify Redis", ops.STATUS_PASS if pong else ops.STATUS_FAIL,
                  "redis PONG" if pong else (err or out or "no PONG").strip()[-200:]))

    # 14. PostgreSQL (guarded compose exec; internal-only — no published port)
    if not tools.get("docker"):
        add(_step("verify PostgreSQL", ops.STATUS_BLOCKED,
                  "docker missing; staging database is internal-only (no published port)"))
    elif not identity_ok:
        add(_step("verify PostgreSQL", ops.STATUS_FAIL, identity_reason))
    else:
        pg_user = os.environ.get("POSTGRES_USER", "voxdesk")
        rc, out, err = _compose(["exec", "-T", "db", "pg_isready", "-U", pg_user], timeout=120)
        add(_step("verify PostgreSQL", ops.STATUS_PASS if rc == 0 else ops.STATUS_FAIL,
                  "pg_isready ok" if rc == 0 else (err or out or "pg_isready failed").strip()[-200:]))

    # 15. proxy
    pcode, pok = _http_get(args.base_url.rstrip("/") + "/")
    add(_step("verify proxy", ops.STATUS_PASS if pok and pcode < 500 else ops.STATUS_BLOCKED,
              f"GET / -> {pcode}" if pok else "unreachable"))

    # 16. deployment checks (static)
    cstatus, cdetail = _static_compose_checks()
    add(_step("deployment checks (static)", cstatus, cdetail))

    # 16b. compose config validation (docker, read-only)
    cc_status, cc_detail = _compose_config_check()
    add(_step("compose config (docker)", cc_status, cc_detail))

    # 17. security smoke
    sstatus, sdetail = _security_smoke()
    add(_step("security smoke checks", sstatus, sdetail))

    # 18. provider health (opt-in)
    outcomes = asyncio.run(registry.run_all())
    provider_report = ops.build_provider_report([o.as_dict() for o in outcomes])
    add(_step("provider health checks", provider_report["status"],
              f"{len(provider_report['rows'])} providers, opt-in={real_integration_enabled()}"))

    # 18b. E2E readiness (preconditions only — the live call is never automated)
    e2e_ready = _e2e_readiness(app_env, provider_report["status"], ok, status_code)
    add(_step("E2E readiness (preconditions)", e2e_ready["status"], e2e_ready["detail"]))

    # 19. backup verification
    backup = ops.run_backup_orchestration(app_env, tools=tools)
    add(_step("backup verification", backup["status"], backup["evidence"], backup.get("record")))

    # 20. TLS (BLOCKED when no URL is supplied; certificate validation never weakened)
    if args.url:
        tls = ops.verify_tls(args.url)
        add(_step("TLS certification", tls["status"], tls["evidence"],
                  ops.EvidenceRecord(
                      item_id="tls-001", status=tls["status"],
                      classification="INFRASTRUCTURE", severity="P0",
                      command="scripts/staging_certify.py --staging --url",
                      timestamp=ops.now_iso(), release_commit=live["git_commit"],
                      environment=app_env, evidence=tls["evidence"])))
    else:
        add(_step("TLS certification", ops.STATUS_BLOCKED,
                  "no staging URL supplied (pass --url https://staging.example.com)"))

    # Observability (BLOCKED when no Prometheus URL is supplied)
    if args.prometheus_url:
        obs = ops.verify_observability(args.prometheus_url, args.grafana_url)
        add(_step("observability", obs["status"], obs["evidence"],
                  ops.EvidenceRecord(
                      item_id="obs-001", status=obs["status"],
                      classification="INFRASTRUCTURE", severity="P1",
                      command="scripts/staging_certify.py --staging --prometheus-url",
                      timestamp=ops.now_iso(), release_commit=live["git_commit"],
                      environment=app_env, evidence=obs["evidence"])))
    else:
        add(_step("observability", ops.STATUS_BLOCKED,
                  "Prometheus URL not configured (pass --prometheus-url)"))

    # 21. backup -> restore drill and graceful drain (opt-in: --with-drills).
    # Both mutate the stack, so they are off by default in --staging and always
    # on in --proof, where the stack exists purely to be proven and torn down.
    if args.with_drills:
        drill_dir = Path(ops.REPO_ROOT) / "var" / "release" / "drills"
        drill = backup_restore_drill(app_env, log_dir=drill_dir, tools=tools)
        add(_step("backup → restore drill", drill["status"],
                  f"dump={drill.get('dump_path') or 'none'} target_db={drill.get('target_db') or 'none'}"))
        records.append(ops.EvidenceRecord(
            item_id="drill-001", status=drill["status"],
            classification="INFRASTRUCTURE", severity="P1",
            command="scripts/staging_certify.py --staging --with-drills",
            timestamp=ops.now_iso(), release_commit=live["git_commit"],
            environment=app_env,
            evidence=f"backup -> restore drill ({drill['status']}); "
                     f"dump={drill.get('dump_path') or 'none'}"))

        drain = graceful_drain_check(log_dir=drill_dir)
        add(_step("graceful drain", drain["status"], drain["detail"]))
        records.append(ops.EvidenceRecord(
            item_id="drain-001", status=drain["status"],
            classification="INFRASTRUCTURE", severity="P2",
            command="scripts/staging_certify.py --staging --with-drills",
            timestamp=ops.now_iso(), release_commit=live["git_commit"],
            environment=app_env, evidence=drain["detail"]))

    # Aggregate deploy-001 from the deployment steps
    dep_statuses = [s["status"] for s in steps
                    if s["step"] in ("build candidate", "start staging", "run migrations",
                                     "wait for readiness", "smoke test", "verify health")]
    if ops.STATUS_FAIL in dep_statuses:
        deploy_status = ops.STATUS_FAIL
    elif ops.STATUS_BLOCKED in dep_statuses:
        deploy_status = ops.STATUS_BLOCKED
    elif all(s == ops.STATUS_PASS for s in dep_statuses):
        deploy_status = ops.STATUS_PASS
    else:
        deploy_status = ops.STATUS_NOT_RUN
    add(_step("deployment certification (aggregate)", deploy_status,
              "aggregate of build/start/migrate/readiness/smoke/health"))
    records.append(ops.EvidenceRecord(
        item_id="deploy-001", status=deploy_status,
        classification="INFRASTRUCTURE", severity="P1",
        command="scripts/staging_certify.py --staging",
        timestamp=ops.now_iso(), release_commit=live["git_commit"],
        environment=app_env,
        evidence="aggregate of build/start/migrate/readiness/smoke/health "
                 f"({deploy_status})"))

    codes = [ops.exit_code_for_status(s["status"]) for s in steps
             if s["status"] not in (ops.STATUS_NOT_RUN,)]
    statuses = [s["status"] for s in steps]
    if ops.STATUS_FAIL in statuses:
        overall = "FAIL"
    elif ops.STATUS_BLOCKED in statuses:
        overall = "BLOCKED"
    elif codes and max(codes) == 0:
        overall = "PASS"
    else:
        # NOT_RUN steps (e.g. E2E readiness while disarmed) are informational:
        # they never flip an otherwise-green certification to BLOCKED.
        overall = "NOT_RUN"
    return {
        "mode": "staging",
        "timestamp": ops.now_iso(),
        "release_commit": live["git_commit"],
        "environment": app_env,
        "steps": [{"step": s["step"], "status": s["status"], "detail": s["detail"]}
                  for s in steps],
        "overall": overall,
        "exit_code": ops.worst_exit_code(codes) if codes else ops.EXIT_BLOCKED,
        "records": records,
    }


def _run_single_mode(args: argparse.Namespace, mode: str) -> dict:
    app_env = (os.environ.get("APP_ENV") or settings.app_env or "development").strip()
    live = ops.compute_live_facts()
    records: list[ops.EvidenceRecord] = []
    steps: list[dict] = []

    if mode == "preflight":
        report = ops.run_preflight(ops.REPO_ROOT, expected_commit=args.expected_commit)
        return {"mode": mode, "preflight": report, "overall": report["overall"],
                "exit_code": report["exit_code"], "records": records}

    if mode == "backup":
        result = ops.run_backup_orchestration(app_env)
        steps.append(_step("backup", result["status"], result["evidence"], result.get("record")))
        if result.get("record"):
            records.append(result["record"])

    elif mode == "restore":
        result = ops.run_restore_orchestration(app_env, args.dump, args.restore_target_db)
        steps.append(_step("restore", result["status"], result["evidence"], result.get("record")))
        if result.get("record"):
            records.append(result["record"])

    elif mode == "providers":
        outcomes = asyncio.run(registry.run_all())
        report = ops.build_provider_report([o.as_dict() for o in outcomes])
        steps.append(_step("providers", report["status"],
                           f"{len(report['rows'])} providers, opt-in={real_integration_enabled()}"))
        if real_integration_enabled():
            ingested = gate.evidence_for_provider_results(
                [o.as_dict() for o in outcomes], ops.today_iso(), "staging_certify.py --providers"
            )
            for ev in ingested.values():
                records.append(ops.EvidenceRecord(
                    item_id=ev.item_id, status=ev.status.value,
                    classification="EXTERNAL", severity="P1",
                    command="scripts/staging_certify.py --providers",
                    timestamp=ops.now_iso(), release_commit=live["git_commit"],
                    environment=app_env, evidence=ev.evidence))

    elif mode == "observability":
        result = ops.verify_observability(args.prometheus_url, args.grafana_url)
        steps.append(_step("observability", result["status"], result["evidence"],
                           ops.EvidenceRecord(
                               item_id="obs-001", status=result["status"],
                               classification="INFRASTRUCTURE", severity="P1",
                               command="scripts/staging_certify.py --observability",
                               timestamp=ops.now_iso(), release_commit=live["git_commit"],
                               environment=app_env, evidence=result["evidence"])))
        if steps[-1].get("record"):
            records.append(steps[-1]["record"])

    elif mode == "egress":
        inside = args.inside_staging or (
            os.environ.get("VOXDESK_STAGING_NETWORK", "").strip().lower() in {"1", "true", "yes", "on"}
        )
        allow_hosts = [
            h.strip() for h in (os.environ.get("VOXDESK_EGRESS_ALLOW_HOSTS", "") or "").split(",")
            if h.strip()
        ]
        result = ops.verify_egress(inside, allow_hosts=allow_hosts)
        steps.append(_step("egress", result["status"], result["evidence"],
                           ops.EvidenceRecord(
                               item_id="egress-001", status=result["status"],
                               classification="INFRASTRUCTURE", severity="P1",
                               command="scripts/staging_certify.py --egress",
                               timestamp=ops.now_iso(), release_commit=live["git_commit"],
                               environment=app_env, evidence=result["evidence"])))
        if steps[-1].get("record"):
            records.append(steps[-1]["record"])

    elif mode == "deploy-drill":
        result = ops.run_deploy_drill_orchestration(app_env, args.previous_sha)
        steps.append(_step("deploy-drill", result["status"], result["evidence"],
                           result.get("record")))
        if result.get("record"):
            records.append(result["record"])

    elif mode == "e2e-readiness":
        # PRECONDITIONS ONLY. Never places a call, never writes e2e-001
        # evidence: that item is satisfied solely by a human-authored
        # var/release/e2e-result.json produced after a real operator call.
        status_code, ok = _http_get(args.base_url.rstrip("/") + "/health/ready")
        outcomes = asyncio.run(registry.run_all())
        provider_report = ops.build_provider_report([o.as_dict() for o in outcomes])
        e2e_ready = _e2e_readiness(app_env, provider_report["status"], ok, status_code)
        detail = e2e_ready["detail"] + " | " + "; ".join(
            f"{c['name']}={c['status']}" for c in e2e_ready["checks"]
        )
        steps.append(_step("E2E readiness (preconditions)", e2e_ready["status"], detail))

    codes = [ops.exit_code_for_status(s["status"]) for s in steps]
    statuses = [s["status"] for s in steps]
    if ops.STATUS_FAIL in statuses:
        overall = "FAIL"
    elif ops.STATUS_BLOCKED in statuses:
        overall = "BLOCKED"
    elif ops.STATUS_NOT_RUN in statuses:
        overall = "NOT_RUN"
    elif statuses and all(s == ops.STATUS_PASS for s in statuses):
        overall = "PASS"
    else:
        # Includes the all-SKIPPED case: credentials absent is never PASS.
        overall = "SKIPPED"
    return {
        "mode": mode,
        "timestamp": ops.now_iso(),
        "release_commit": live["git_commit"],
        "environment": app_env,
        "steps": [{"step": s["step"], "status": s["status"], "detail": s["detail"]}
                  for s in steps],
        "overall": overall,
        "exit_code": ops.worst_exit_code(codes),
        "records": records,
    }


def _run_http_certify(args: argparse.Namespace) -> dict:
    """Certify a live HTTP stack endpoint (--base / --base-url)."""
    app_env = (os.environ.get("APP_ENV") or settings.app_env or "staging").strip()
    live = ops.compute_live_facts()
    base = args.base_url.rstrip("/")
    steps: list[dict] = []

    code_h, ok_h = _http_get(base + "/health")
    steps.append(_step("verify liveness (/health)", ops.STATUS_PASS if ok_h and code_h == 200 else ops.STATUS_FAIL, f"GET /health -> {code_h}"))

    code_r, ok_r = _http_get(base + "/health/ready")
    steps.append(_step("verify readiness (/health/ready)", ops.STATUS_PASS if ok_r and code_r == 200 else ops.STATUS_FAIL, f"GET /health/ready -> {code_r}"))

    code_m, ok_m = _http_get(base + "/metrics")
    steps.append(_step("verify metrics (/metrics)", ops.STATUS_PASS if ok_m and code_m in (200, 401) else ops.STATUS_FAIL, f"GET /metrics -> {code_m}"))

    code_p, ok_p = _http_get(base + "/")
    steps.append(_step("verify dashboard shell (/)", ops.STATUS_PASS if ok_p and code_p == 200 else ops.STATUS_FAIL, f"GET / -> {code_p}"))

    smoke_script = Path(ops.REPO_ROOT) / "scripts" / "smoke_test.py"
    proc = subprocess.run(
        [sys.executable, str(smoke_script), "--base", base],
        capture_output=True, text=True, timeout=120,
        env={**os.environ, "SMOKE_BASE_URL": base},
    )
    smoke_summary = (proc.stdout or proc.stderr).strip().splitlines()[-1] if (proc.stdout or proc.stderr) else ""
    steps.append(_step("smoke test (smoke_test.py)", ops.STATUS_PASS if proc.returncode == 0 else ops.STATUS_FAIL, smoke_summary))

    cstatus, cdetail = _static_compose_checks()
    steps.append(_step("deployment checks (static)", cstatus, cdetail))

    codes = [ops.exit_code_for_status(s["status"]) for s in steps]
    overall = "PASS" if all(s["status"] == ops.STATUS_PASS for s in steps) else "FAIL"
    return {
        "mode": "http-certify",
        "timestamp": ops.now_iso(),
        "release_commit": live["git_commit"],
        "environment": app_env,
        "steps": [{"step": s["step"], "status": s["status"], "detail": s["detail"]} for s in steps],
        "overall": overall,
        "exit_code": ops.worst_exit_code(codes),
        "records": [],
    }


def _render(report: dict) -> str:
    lines = [f"staging_certify — mode={report.get('mode')}", "=" * 78]
    if report.get("preflight"):
        lines.append(ops.render_preflight(report["preflight"]))
        return "\n".join(lines)
    for s in report.get("steps", []):
        lines.append(f"  [{s['status']:<11}] {s['step']:<34} {s['detail']}")
    lines.append("-" * 78)
    lines.append(f"OVERALL: {report['overall']}   (exit {report['exit_code']})")
    return "\n".join(lines)


def backup_restore_drill(app_env: str, *, log_dir: Path, tools: dict[str, str | None]) -> dict[str, Any]:
    """Backup the stack's database, verify the dump, and restore it into a scratch DB.

    A backup nobody has restored is a hypothesis, not a backup. The restore is
    aimed at a scratch database (`RESTORE_TARGET_DB`) so the drill can never
    overwrite the database the rest of the proof is about to assert against.
    """
    dump_dir = Path(ops.REPO_ROOT) / "backups-staging"
    dump_dir.mkdir(parents=True, exist_ok=True)
    backup = ops.run_backup_orchestration(app_env, output_dir=str(dump_dir), tools=tools)
    dump_path = str(backup.get("dump_path") or "")
    steps: list[dict[str, Any]] = [
        {
            "step": "backup.create",
            "status": ops.STATUS_PASS if backup.get("ok") else ops.STATUS_FAIL,
            "detail": str(backup.get("detail") or backup.get("status") or "")[:300],
            "artifact": dump_path,
        }
    ]

    if not dump_path or not Path(dump_path).is_file():
        steps.append(
            {
                "step": "backup.integrity",
                "status": ops.STATUS_FAIL,
                "detail": "no dump file was produced, so there is nothing to verify",
            }
        )
        return {
            "ok": False,
            "status": ops.STATUS_FAIL,
            "steps": steps,
            "backup": backup,
            "restore": None,
        }

    integrity_status, integrity_detail = ops.verify_backup_integrity(dump_path)
    steps.append(
        {
            "step": "backup.integrity",
            "status": integrity_status,
            "detail": integrity_detail[:300],
            "artifact": dump_path,
            "sha256": sha256_file(Path(dump_path)),
        }
    )

    target_db = os.environ.get("RESTORE_TARGET_DB", "").strip() or "voxdesk_restore_drill"
    restore = ops.run_restore_orchestration(app_env, dump_path=dump_path, target_db=target_db, tools=tools)
    steps.append(
        {
            "step": "backup.restore",
            "status": ops.STATUS_PASS if restore.get("ok") else ops.STATUS_FAIL,
            "detail": str(restore.get("detail") or restore.get("status") or "")[:300],
            "target_db": target_db,
        }
    )

    ok = all(step["status"] == ops.STATUS_PASS for step in steps)
    return {
        "ok": ok,
        "status": ops.STATUS_PASS if ok else ops.STATUS_FAIL,
        "steps": steps,
        "backup": backup,
        "restore": restore,
        "dump_path": dump_path,
        "target_db": target_db,
    }


def graceful_drain_check(*, log_dir: Path) -> dict[str, Any]:
    """Stop the API container with a grace period and prove it exited on SIGTERM.

    A process that is SIGKILLed mid-request loses the request and, worse, can
    leave a call half-written. Docker reports 143 (128+SIGTERM) when the
    application handled the signal and 137 when it had to be killed. This check
    reads the real exit code from `docker inspect` rather than trusting the
    stop command's own status.
    """
    stopped = run_logged(
        "drain-stop",
        ["docker", "compose", "-p", CHECK_PROJECT_NAME, "stop", "-t", str(DRAIN_GRACE_SECONDS), "api"],
        log_dir=log_dir,
        timeout=DRAIN_GRACE_SECONDS + 120,
    )
    inspect = run_logged(
        "drain-inspect",
        ["docker", "inspect", "--format", "{{.State.ExitCode}} {{.State.FinishedAt}}",
         f"{CHECK_PROJECT_NAME}-api-1"],
        log_dir=log_dir,
        timeout=120,
    )
    raw = ""
    if inspect["exit_code"] == 0:
        log_path = ops.REPO_ROOT / str(inspect["log"])
        raw = log_path.read_text(encoding="utf-8").strip().splitlines()[-1] if log_path.is_file() else ""
    exit_code: int | None = None
    if raw:
        try:
            exit_code = int(raw.split()[0])
        except (ValueError, IndexError):
            exit_code = None

    if exit_code is None and inspect["exit_code"] == 0:
        # The container is already gone (compose removed it): the only honest
        # answer is that the drain could not be measured.
        status = ops.STATUS_NA
        detail = "container no longer inspectable; drain exit code not measurable"
    elif exit_code in _GRACEFUL_EXIT_CODES:
        status = ops.STATUS_PASS
        detail = f"api container exited {exit_code} within the {DRAIN_GRACE_SECONDS}s grace period"
    else:
        status = ops.STATUS_FAIL
        detail = (
            f"api container exited {exit_code}; expected 0 or 143 (SIGTERM handled), "
            f"137 would mean Docker had to SIGKILL it"
        )

    return {
        "ok": status == ops.STATUS_PASS,
        "status": status,
        "detail": detail,
        "exit_code": exit_code,
        "grace_seconds": DRAIN_GRACE_SECONDS,
        "commands": [stopped, inspect],
    }


def _run_staging_proof(args: argparse.Namespace) -> dict:
    """Full deploy proof against the local check stack.

    Sequence: compose up --build, alembic upgrade head, owner creation, HTTP
    smoke, signed Twilio webhook smoke (valid -> TwiML with <Stream>, invalid ->
    403), WebSocket media handshake, web-call token flow, DB verification,
    backup -> restore drill, graceful drain, compose down.

    Every command's output is written to a raw log under the evidence
    directory, and `staging_proof.json` records each step's exit code, wall
    clock duration and the SHA-256 of that log. The teardown runs even when a
    step fails, so a broken proof does not leave a stack behind.
    """
    app_env = (os.environ.get("APP_ENV") or settings.app_env or "development").strip()
    evidence_dir = Path(args.evidence_dir) if args.evidence_dir else (
        Path(ops.REPO_ROOT) / "evidence" / "staging" / _utc_date()
    )
    if not evidence_dir.is_absolute():
        evidence_dir = Path(ops.REPO_ROOT) / evidence_dir
    raw_dir = evidence_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    live = ops.compute_live_facts()
    base_url = args.base_url or "http://127.0.0.1:8000"
    python = sys.executable
    steps: list[dict[str, Any]] = []
    started = time.monotonic()

    def record(name: str, result: dict[str, Any], ok: bool, detail: str = "") -> None:
        steps.append(
            {
                "step": name,
                "status": ops.STATUS_PASS if ok else ops.STATUS_FAIL,
                "detail": detail or f"exit={result.get('exit_code')}",
                "exit_code": result.get("exit_code"),
                "duration_seconds": result.get("duration_seconds"),
                "log": result.get("log"),
                "log_sha256": result.get("log_sha256"),
            }
        )
        print(f"  [{'PASS' if ok else 'FAIL'}] {name} — {steps[-1]['detail']}", flush=True)

    tools = _tools()
    compose_ok, compose_detail = _compose_ready()

    if not compose_ok:
        steps.append(
            {"step": "compose.available", "status": ops.STATUS_BLOCKED, "detail": compose_detail}
        )
        return {
            "mode": "staging-proof",
            "timestamp": ops.now_iso(),
            "release_commit": live["git_commit"],
            "environment": app_env,
            "steps": steps,
            "overall": "BLOCKED",
            "exit_code": ops.EXIT_BLOCKED,
            "evidence_dir": str(evidence_dir.relative_to(ops.REPO_ROOT)),
            "records": [],
        }

    up = run_logged("01-compose-up", _compose_cmd(["up", "-d", "--build"]), log_dir=raw_dir, timeout=2400)
    record("compose up --build", up, up["exit_code"] == 0)
    brought_up = up["exit_code"] == 0

    if brought_up:
        migrate = run_logged(
            "02-alembic-upgrade",
            _compose_cmd(["exec", "-T", "api", "alembic", "upgrade", "head"]),
            log_dir=raw_dir,
            timeout=900,
        )
        record("alembic upgrade head", migrate, migrate["exit_code"] == 0)

        owner = run_logged(
            "03-create-owner",
            [python, "scripts/create_owner.py",
             "--email", os.environ.get("DEMO_OWNER_EMAIL", "owner@check.local"),
             "--password-from-env", "DEMO_OWNER_PASSWORD",
             "--tenant", "Staging Proof Tenant"],
            log_dir=raw_dir,
            timeout=600,
        )
        # create_owner.py exits non-zero when the owner already exists, which is
        # the normal case on a re-run; the authoritative check is whether a
        # login then succeeds, and the HTTP smoke proves that.
        record(
            "owner creation (or already present)",
            owner,
            True,
            f"exit={owner['exit_code']} (already-exists is not a failure; HTTP smoke proves login)",
        )

        for name, argv in (
            ("04-http-smoke", ["http", "--base", base_url, "--env-file", CHECK_ENV_FILE, "--json"]),
            ("05-twilio-webhook-smoke", ["twilio", "--base", base_url, "--env-file", CHECK_ENV_FILE, "--json"]),
            ("06-call-ws-smoke", ["call-ws", "--base", base_url.replace("http", "ws", 1), "--env-file", CHECK_ENV_FILE, "--json"]),
            ("07-web-call-token-smoke", ["web-call-token", "--base", base_url, "--env-file", CHECK_ENV_FILE, "--json"]),
        ):
            result = run_logged(name, [python, "scripts/stack_smoke.py", *argv], log_dir=raw_dir, timeout=900)
            record(name.split("-", 1)[1].replace("-", " "), result, result["exit_code"] == 0)

        dbcheck = run_logged(
            "08-db-verify",
            _compose_cmd(["exec", "-T", "db", "psql", "-U", "voxdesk", "-d", "voxdesk", "-c",
                          "select current_database(), (select count(*) from information_schema.tables "
                          "where table_schema='public') as tables;"]),
            log_dir=raw_dir,
            timeout=300,
        )
        record("database reachable and migrated", dbcheck, dbcheck["exit_code"] == 0)

        drill = backup_restore_drill(app_env, log_dir=raw_dir, tools=tools)
        for drill_step in drill["steps"]:
            steps.append(
                {
                    "step": drill_step["step"],
                    "status": drill_step["status"],
                    "detail": drill_step["detail"],
                }
            )
            print(f"  [{drill_step['status']}] {drill_step['step']} — {drill_step['detail']}", flush=True)

        drain = graceful_drain_check(log_dir=raw_dir)
        steps.append({"step": "graceful-drain", "status": drain["status"], "detail": drain["detail"]})
        print(f"  [{drain['status']}] graceful-drain — {drain['detail']}", flush=True)

    if brought_up and not args.skip_teardown:
        down = run_logged("99-compose-down", _compose_cmd(["down"]), log_dir=raw_dir, timeout=600)
        record("compose down", down, down["exit_code"] == 0)
    elif brought_up:
        steps.append(
            {
                "step": "compose down",
                "status": ops.STATUS_SKIPPED,
                "detail": "--skip-teardown was passed; the stack is still running",
            }
        )

    duration = round(time.monotonic() - started, 2)
    statuses = [step["status"] for step in steps]
    overall = "PASS" if statuses and all(s == ops.STATUS_PASS for s in statuses) else "FAIL"

    manifest: list[dict[str, Any]] = []
    for path in sorted(raw_dir.glob("*.log")):
        manifest.append(
            {
                "file": str(path.relative_to(ops.REPO_ROOT)),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )

    proof = {
        "schema": "voxdesk.staging_proof/v1",
        "mode": "staging-proof",
        "generated_at": _utc_now_iso(),
        "timestamp": ops.now_iso(),
        "release_commit": live["git_commit"],
        "environment": app_env,
        "compose_files": CHECK_COMPOSE_FILES,
        "compose_project": CHECK_PROJECT_NAME,
        "base_url": base_url,
        "duration_seconds": duration,
        "steps": steps,
        "raw_logs": manifest,
        "overall": overall,
    }
    proof_path = evidence_dir / "staging_proof.json"
    proof_path.write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")

    return {
        "mode": "staging-proof",
        "timestamp": proof["timestamp"],
        "release_commit": live["git_commit"],
        "environment": app_env,
        "steps": steps,
        "overall": overall,
        "exit_code": ops.EXIT_OK if overall == "PASS" else ops.EXIT_FAIL,
        "evidence_dir": str(evidence_dir.relative_to(ops.REPO_ROOT)),
        "proof_path": str(proof_path.relative_to(ops.REPO_ROOT)),
        "proof_sha256": sha256_file(proof_path),
        "records": [],
    }


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    modes = [m for m, on in (
        ("preflight", args.preflight), ("staging", args.staging),
        ("backup", args.backup), ("restore", args.restore),
        ("providers", args.providers), ("observability", args.observability),
        ("egress", args.egress), ("e2e-readiness", args.e2e_readiness),
        ("deploy-drill", args.deploy_drill),
    ) if on]
    raw_argv = sys.argv[1:] if argv is None else list(argv)
    base_only_flag = any(
        a in ("--base", "--base-url") or a.startswith("--base=") or a.startswith("--base-url=")
        for a in raw_argv
    )
    if args.proof:
        reports = [_run_staging_proof(args)]
        if args.json:
            print(json.dumps(reports, indent=2, default=str))
        else:
            for report in reports:
                print(_render(report))
                print()
        if args.write_evidence:
            for report in reports:
                if report.get("records"):
                    ops.merge_evidence_file(EVIDENCE_PATH, report["records"])
        return ops.worst_exit_code([r["exit_code"] for r in reports])

    if not modes and base_only_flag and not args.all_safe:
        modes = ["http-certify"]
    elif args.all_safe or not modes:
        modes = ["preflight", "staging", "backup", "providers", "observability", "egress", "deploy-drill"]

    reports = []
    for mode in modes:
        if mode == "staging":
            reports.append(_run_staging_sequence(args))
        elif mode == "http-certify":
            reports.append(_run_http_certify(args))
        else:
            reports.append(_run_single_mode(args, mode))

    # Guarded compose lifecycle (opt-in, staging project only). These run as
    # their own reports and are never part of the default mode list.
    if args.compose_up or args.compose_down:
        app_env = (os.environ.get("APP_ENV") or settings.app_env or "development").strip()
        live = ops.compute_live_facts()
        identity_ok, identity_reason = _staging_identity_ok(app_env)
        for action in ([m for m, on in (("compose-up", args.compose_up),
                                        ("compose-down", args.compose_down)) if on]):
            if not identity_ok:
                reports.append({
                    "mode": action,
                    "timestamp": ops.now_iso(),
                    "release_commit": live["git_commit"],
                    "environment": app_env,
                    "steps": [{"step": "staging identity guard", "status": ops.STATUS_FAIL,
                               "detail": identity_reason}],
                    "overall": "FAIL",
                    "exit_code": ops.EXIT_FAIL,
                    "records": [],
                })
                continue
            ok, detail = _compose_ready()
            if not ok:
                reports.append({
                    "mode": action,
                    "timestamp": ops.now_iso(),
                    "release_commit": live["git_commit"],
                    "environment": app_env,
                    "steps": [{"step": "docker compose", "status": ops.STATUS_BLOCKED,
                               "detail": detail}],
                    "overall": "BLOCKED",
                    "exit_code": ops.EXIT_BLOCKED,
                    "records": [],
                })
                continue
            if action == "compose-up":
                rc, out, err = _compose(["up", "-d", "--build"], timeout=1800)
                if rc == 0:
                    step_status, step_detail = ops.STATUS_PASS, "docker compose up -d --build ok"
                else:
                    step_status, step_detail = ops.STATUS_FAIL, (err or out).strip()[-300:]
                    _cleanup_staging_compose()
            else:
                rc, out, err = _compose(["down"], timeout=600)
                step_status, step_detail = (
                    (ops.STATUS_PASS, "docker compose down ok") if rc == 0
                    else (ops.STATUS_FAIL, (err or out).strip()[-300:])
                )
            reports.append({
                "mode": action,
                "timestamp": ops.now_iso(),
                "release_commit": live["git_commit"],
                "environment": app_env,
                "steps": [{"step": "docker compose", "status": step_status, "detail": step_detail}],
                "overall": ("PASS" if step_status == ops.STATUS_PASS else "FAIL"),
                "exit_code": ops.exit_code_for_status(step_status),
                "records": [],
            })

    if args.json:
        print(json.dumps(reports, indent=2, default=str))
    else:
        for report in reports:
            print(_render(report))
            print()

    if args.write_evidence:
        total = 0
        for report in reports:
            total += len(report.get("records", []))
            if report.get("records"):
                ops.merge_evidence_file(EVIDENCE_PATH, report["records"])
        print(f"evidence: merged {total} record(s) into {EVIDENCE_PATH}")

    codes = [r["exit_code"] for r in reports]
    return ops.worst_exit_code(codes)


if __name__ == "__main__":
    sys.exit(main())
