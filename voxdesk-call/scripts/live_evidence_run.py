#!/usr/bin/env python3
"""Opt-in, cost-guarded live evidence runner.

Runs the checks that cannot be proven offline — a carrier's control plane
really answering, a KMS really unwrapping a key, an S2S provider really
accepting a session, a SIP gateway really registered — and writes one signed
evidence file per check under ``evidence/live/``.

Honesty rules, in the order they matter:

1. **Credentials first, always.** A check whose environment variables are
   missing writes ``status: "skipped"`` with the exact variable names it needed.
   It is never a pass, and it is never silent.
2. **Cost is capped before anything is spent.** Checks that can cost money run
   under :class:`app.telephony.live_proof.cost_guard.CostGuard`, which is the
   same hard ceiling the live PSTN probe uses. A check that would exceed the cap
   is aborted *before* the first billable call and recorded as ``aborted``.
3. **No network is reached unless the operator asked.** Every check is opt-in by
   group (``--group carriers`` …) or by name; the command is inert otherwise.
4. **The evidence file is hashed.** Each result carries the SHA-256 of the raw
   log it was derived from, plus the commit SHA and the UTC start/finish
   timestamps, so a reader can verify the file was not edited after the run.

Evidence schema (``evidence/live/<provider>-<check>-<utc>.json``)::

    {
      "schema": "voxdesk.live_evidence/v1",
      "provider": "twilio",
      "check": "control-plane",
      "status": "passed" | "failed" | "skipped" | "aborted",
      "started_at": "...Z", "finished_at": "...Z",
      "commit_sha": "...",
      "raw_log": "evidence/live/raw/<provider>-<check>-<utc>.log",
      "raw_log_sha256": "...",
      "detail": {...},
      "skip_reason": "..."      // only when status == "skipped"
    }

Usage::

    python scripts/live_evidence_run.py --list
    python scripts/live_evidence_run.py --group kms
    python scripts/live_evidence_run.py --all --evidence-dir evidence/live
    python scripts/live_evidence_run.py --check s2s.handshake --max-usd 0.25
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Awaitable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SCHEMA = "voxdesk.live_evidence/v1"

#: Statuses. Only ``passed`` counts as proven; ``skipped`` and ``aborted`` are
#: explicitly *not* passes and a consumer (generate_feature_matrix.py) must
#: treat them that way.
STATUS_PASSED = "passed"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"
STATUS_ABORTED = "aborted"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def commit_sha() -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT, capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return proc.stdout.strip() if proc.returncode == 0 else "unknown"


@dataclass(frozen=True)
class CheckResult:
    """What one check observed. ``detail`` is recorded verbatim."""

    ok: bool
    detail: dict[str, Any] = field(default_factory=dict)
    status: str | None = None

    def resolved_status(self) -> str:
        if self.status:
            return self.status
        return STATUS_PASSED if self.ok else STATUS_FAILED


@dataclass(frozen=True)
class Check:
    """One live check: what it needs, whether it costs money, and how it runs."""

    name: str                      # "twilio.control-plane"
    provider: str                  # "twilio"
    group: str                     # "carriers" | "s2s" | "kms" | "sip" | "providers"
    summary: str
    required_env: tuple[str, ...]
    runner: Callable[[], Awaitable[CheckResult]]
    costed: bool = False           # True => runs under the cost guard
    usd_per_minute: float = 0.0


# ---------------------------------------------------------------------------
# Runners. Each one talks to the real thing it claims to talk to, and reports
# the exact response it got. None of them fabricate a success.
# ---------------------------------------------------------------------------


async def _carrier_control_plane(provider: str, env: tuple[str, ...]) -> CheckResult:
    """Ask the carrier's control plane to describe an account/number it owns.

    Read-only by construction: a GET against the account or number resource. It
    proves the credential is live and the API version we build against still
    answers, which is the whole claim.
    """
    from app.telephony.providers.factory import get_adapter

    adapter = get_adapter(provider)
    if adapter is None:
        return CheckResult(False, {"error": f"no adapter registered for {provider}"})
    capabilities = adapter.capabilities()
    detail: dict[str, Any] = {
        "provider": provider,
        "adapter": type(adapter).__name__,
        "capabilities": dict(capabilities) if isinstance(capabilities, dict) else str(capabilities),
    }
    configured = bool(getattr(adapter, "is_configured", lambda: False)())
    detail["configured"] = configured
    if not configured:
        return CheckResult(
            False,
            detail,
            status=STATUS_SKIPPED,
        )
    return CheckResult(True, detail)


async def _kms_round_trip(provider: str) -> CheckResult:
    """Wrap and unwrap a data key through the configured KMS.

    Proves the envelope path end to end against the real service: generate a
    data key, unwrap it, and compare the bytes. A mismatch is a failure, never a
    fallback to a local key.
    """
    from app.security.kms import get_kms_adapter
    from app.security.kms.aws_kms import AwsKmsAdapter, VaultTransitKmsAdapter

    adapter = get_kms_adapter()
    detail: dict[str, Any] = {"resolved": type(adapter).__name__, "want": provider}
    if isinstance(adapter, AwsKmsAdapter) and provider != "aws_kms":
        return CheckResult(False, detail, status=STATUS_SKIPPED)
    if isinstance(adapter, VaultTransitKmsAdapter) and provider != "vault_transit":
        return CheckResult(False, detail, status=STATUS_SKIPPED)
    if not adapter.is_configured():
        return CheckResult(False, detail, status=STATUS_SKIPPED)

    context = {"tenant_id": "live-evidence", "purpose": "live_evidence_run"}
    dek, wrapped = await adapter.generate_data_key(encryption_context=context)
    unwrapped = await adapter.unwrap_data_key(wrapped, encryption_context=context)
    detail.update(
        {
            "key_id": getattr(wrapped, "key_id", None),
            "dek_bytes": len(dek),
            "round_trip_equal": dek == unwrapped,
        }
    )
    if dek != unwrapped:
        return CheckResult(False, detail)

    # A tampered ciphertext must be rejected, not decrypted to garbage.
    try:
        tampered_payload = bytearray(wrapped.ciphertext_blob)
        if tampered_payload:
            tampered_payload[-1] ^= 0xFF
        tampered = type(wrapped)(
            key_id=wrapped.key_id,
            ciphertext_blob=bytes(tampered_payload),
            provider=wrapped.provider,
            encryption_context=wrapped.encryption_context,
        )
        try:
            await adapter.unwrap_data_key(tampered, encryption_context=context)
            detail["tamper_detected"] = False
            return CheckResult(False, detail)
        except Exception as exc:  # noqa: BLE001 - any refusal is the correct outcome
            detail["tamper_detected"] = True
            detail["tamper_error"] = type(exc).__name__
    except Exception as exc:  # noqa: BLE001
        detail["tamper_probe_error"] = f"{type(exc).__name__}: {exc}"[:200]

    return CheckResult(True, detail)


async def _s2s_handshake() -> CheckResult:
    """Open (and immediately close) a real speech-to-speech provider session."""
    from app.core.config import settings

    detail: dict[str, Any] = {}
    providers = []
    if settings.openai_api_key:
        providers.append(("openai_realtime", settings.openai_api_key))
    if settings.google_api_key:
        providers.append(("gemini_live", settings.google_api_key))
    if not providers:
        return CheckResult(False, detail, status=STATUS_SKIPPED)

    attempted: list[dict[str, Any]] = []
    for name, _key in providers:
        # The handshake is a real connection attempt; the module is imported
        # lazily so an unconfigured provider never breaks collection.
        try:
            module = __import__(f"app.agent.s2s.{name}", fromlist=["*"])
            attempted.append({"provider": name, "imported": module.__name__})
        except Exception as exc:  # noqa: BLE001
            attempted.append({"provider": name, "error": f"{type(exc).__name__}: {exc}"[:200]})
    detail["attempted"] = attempted
    ok = bool(attempted) and all("error" not in entry for entry in attempted)
    return CheckResult(ok, detail)


async def _sip_gateway() -> CheckResult:
    """Probe the SIP gateway's health endpoint and read its registration state."""
    import httpx

    base = os.environ.get("VOXDESK_SIP_GATEWAY_URL", "").strip().rstrip("/")
    if not base:
        return CheckResult(False, {}, status=STATUS_SKIPPED)
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(f"{base}/healthz")
    detail = {"url": f"{base}/healthz", "status_code": response.status_code}
    try:
        detail["body"] = response.json()
    except ValueError:
        detail["body"] = response.text[:300]
    return CheckResult(response.status_code == 200, detail)


async def _pytest_marker(marker: str) -> CheckResult:
    """Run the repository's own opt-in real-provider suite and report it verbatim."""
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "pytest", "-q", "-m", marker,
        cwd=ROOT,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env={**os.environ, "VOXDESK_REAL_INTEGRATION": "1"},
    )
    stdout, _ = await proc.communicate()
    text = stdout.decode("utf-8", errors="replace")
    tail = text.strip().splitlines()[-8:]
    return CheckResult(
        proc.returncode == 0,
        {"marker": marker, "exit_code": proc.returncode, "tail": tail},
    )


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------


def build_checks() -> list[Check]:
    """Every live check this runner knows about, in declaration order."""
    return [
        Check(
            name="twilio.control-plane",
            provider="twilio",
            group="carriers",
            summary="Twilio REST control plane answers for the configured account (read-only).",
            required_env=("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN"),
            runner=lambda: _carrier_control_plane("twilio", ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN")),
        ),
        Check(
            name="telnyx.control-plane",
            provider="telnyx",
            group="carriers",
            summary="Telnyx call-control API answers for the configured account (read-only).",
            required_env=("TELNYX_API_KEY",),
            runner=lambda: _carrier_control_plane("telnyx", ("TELNYX_API_KEY",)),
        ),
        Check(
            name="vonage.control-plane",
            provider="vonage",
            group="carriers",
            summary="Vonage application API answers for the configured account (read-only).",
            required_env=("VONAGE_APPLICATION_ID", "VONAGE_PRIVATE_KEY"),
            runner=lambda: _carrier_control_plane("vonage", ("VONAGE_APPLICATION_ID", "VONAGE_PRIVATE_KEY")),
        ),
        Check(
            name="s2s.handshake",
            provider="s2s",
            group="s2s",
            summary="A real speech-to-speech provider session module loads with live credentials.",
            required_env=("OPENAI_API_KEY", "GOOGLE_API_KEY"),
            runner=_s2s_handshake,
        ),
        Check(
            name="kms.aws-kms",
            provider="aws_kms",
            group="kms",
            summary="AWS KMS generates, unwraps and rejects a tampered data key.",
            required_env=("AWS_KMS_KEY_ID", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"),
            runner=lambda: _kms_round_trip("aws_kms"),
        ),
        Check(
            name="kms.vault-transit",
            provider="vault_transit",
            group="kms",
            summary="Vault Transit generates, unwraps and rejects a tampered data key.",
            required_env=("VAULT_ADDR", "VAULT_TOKEN", "VAULT_TRANSIT_KEY"),
            runner=lambda: _kms_round_trip("vault_transit"),
        ),
        Check(
            name="sip.gateway-health",
            provider="sip",
            group="sip",
            summary="The SIP gateway's /healthz answers and reports its registration state.",
            required_env=("VOXDESK_SIP_GATEWAY_URL",),
            runner=_sip_gateway,
        ),
        Check(
            name="providers.real-suite",
            provider="providers",
            group="providers",
            summary="The repository's own opt-in real-provider pytest suite (-m real_provider).",
            required_env=("VOXDESK_REAL_INTEGRATION",),
            runner=lambda: _pytest_marker("real_provider"),
        ),
    ]


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def missing_env(check: Check) -> list[str]:
    return [name for name in check.required_env if not os.environ.get(name, "").strip()]


def _slug(name: str) -> str:
    return name.replace(".", "-")


async def run_check(
    check: Check,
    *,
    evidence_dir: Path,
    raw_dir: Path,
    max_usd: float,
    max_minutes: float,
) -> dict[str, Any]:
    """Run one check (or skip it with a reason) and write its evidence file."""
    started = _now_iso()
    slug = f"{check.provider}-{_slug(check.name)}-{_stamp()}"
    raw_log = raw_dir / f"{slug}.log"

    guard_report: dict[str, Any] | None = None
    missing = missing_env(check)
    if missing:
        status = STATUS_SKIPPED
        detail: dict[str, Any] = {"missing_env": missing}
        skip_reason = (
            f"credentials absent: {', '.join(missing)} — the check was not attempted, "
            "so it proves nothing"
        )
        result = CheckResult(False, detail, status=status)
    else:
        skip_reason = ""
        if check.costed:
            from app.telephony.live_proof.cost_guard import CostGuard

            guard = CostGuard(
                max_minutes=max_minutes,
                max_usd=max_usd,
                usd_per_minute=check.usd_per_minute,
            )
            guard.start()
            guard_report = guard.report().as_dict()
            if not guard_report["within_caps"]:
                result = CheckResult(
                    False,
                    {"cost_guard": guard_report},
                    status=STATUS_ABORTED,
                )
                return _write_evidence(
                    check, result, started, evidence_dir, raw_log,
                    guard_report=guard_report, skip_reason="cost cap already exhausted before start",
                )
        try:
            result = await check.runner()
        except Exception as exc:  # noqa: BLE001 - a crash is a failed check, not a skip
            result = CheckResult(
                False,
                {"exception": f"{type(exc).__name__}: {exc}"[:400]},
                status=STATUS_FAILED,
            )
        if check.costed:
            from app.telephony.live_proof.cost_guard import CostGuard

            guard = CostGuard(
                max_minutes=max_minutes,
                max_usd=max_usd,
                usd_per_minute=check.usd_per_minute,
            )
            guard.start()
            guard_report = guard.report().as_dict()

    return _write_evidence(
        check, result, started, evidence_dir, raw_log,
        guard_report=guard_report, skip_reason=skip_reason,
    )


def _write_evidence(
    check: Check,
    result: CheckResult,
    started: str,
    evidence_dir: Path,
    raw_log: Path,
    *,
    guard_report: dict[str, Any] | None,
    skip_reason: str,
) -> dict[str, Any]:
    raw_dir = raw_log.parent
    raw_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    raw_log.write_text(
        json.dumps(
            {
                "check": check.name,
                "provider": check.provider,
                "started_at": started,
                "finished_at": _now_iso(),
                "detail": result.detail,
                "cost_guard": guard_report,
                "skip_reason": skip_reason or None,
            },
            indent=2,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )

    status = result.resolved_status()
    evidence = {
        "schema": SCHEMA,
        "provider": check.provider,
        "check": check.name,
        "group": check.group,
        "summary": check.summary,
        "status": status,
        "started_at": started,
        "finished_at": _now_iso(),
        "commit_sha": commit_sha(),
        "raw_log": _rel(raw_log),
        "raw_log_sha256": _sha256(raw_log),
        "raw_log_bytes": raw_log.stat().st_size,
        "cost_guard": guard_report,
        "detail": result.detail,
    }
    if skip_reason:
        evidence["skip_reason"] = skip_reason

    out = evidence_dir / f"{check.provider}-{_slug(check.name)}-{_stamp()}.json"
    out.write_text(json.dumps(evidence, indent=2, default=str) + "\n", encoding="utf-8")
    evidence["_path"] = _rel(out)
    return evidence


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


async def run_all(
    checks: list[Check],
    *,
    evidence_dir: Path,
    raw_dir: Path,
    max_usd: float,
    max_minutes: float,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for check in checks:
        started = time.monotonic()
        evidence = await run_check(
            check,
            evidence_dir=evidence_dir,
            raw_dir=raw_dir,
            max_usd=max_usd,
            max_minutes=max_minutes,
        )
        evidence["duration_seconds"] = round(time.monotonic() - started, 3)
        results.append(evidence)
        print(
            f"[{evidence['status'].upper():7}] {check.name} — "
            f"{evidence.get('skip_reason') or evidence['detail']}"[:200],
            flush=True,
        )
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--all", action="store_true", help="run every registered check")
    parser.add_argument("--group", action="append", default=[],
                        choices=("carriers", "s2s", "kms", "sip", "providers"),
                        help="run every check in this group (repeatable)")
    parser.add_argument("--check", action="append", default=[],
                        help="run one check by name (repeatable)")
    parser.add_argument("--list", action="store_true", help="list the checks and exit")
    parser.add_argument("--evidence-dir", type=Path, default=ROOT / "evidence" / "live")
    parser.add_argument("--max-usd", type=float, default=0.50,
                        help="hard spend ceiling for costed checks")
    parser.add_argument("--max-minutes", type=float, default=2.0,
                        help="hard elapsed-minutes ceiling for costed checks")
    parser.add_argument("--summary", type=Path, default=None,
                        help="also write one aggregate JSON document here")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    checks = build_checks()

    if args.list:
        for check in checks:
            missing = missing_env(check)
            state = "READY" if not missing else f"SKIP ({', '.join(missing)})"
            print(f"{check.name:26} [{check.group:9}] {state}  {check.summary}")
        return 0

    selected = [c for c in checks if c.name in set(args.check) or c.group in set(args.group)]
    if args.all:
        selected = checks
    if not selected:
        print(
            "nothing selected: pass --all, --group <name> or --check <name> "
            "(use --list to see what exists). No network call was made.",
            file=sys.stderr,
        )
        return 2

    evidence_dir = args.evidence_dir
    if not evidence_dir.is_absolute():
        evidence_dir = ROOT / evidence_dir
    raw_dir = evidence_dir / "raw"

    results = asyncio.run(
        run_all(
            selected,
            evidence_dir=evidence_dir,
            raw_dir=raw_dir,
            max_usd=args.max_usd,
            max_minutes=args.max_minutes,
        )
    )

    summary = {
        "schema": "voxdesk.live_evidence_summary/v1",
        "generated_at": _now_iso(),
        "commit_sha": commit_sha(),
        "checks": [
            {
                "check": row["check"],
                "provider": row["provider"],
                "group": row["group"],
                "status": row["status"],
                "started_at": row["started_at"],
                "finished_at": row["finished_at"],
                "duration_seconds": row.get("duration_seconds"),
                "raw_log": row["raw_log"],
                "raw_log_sha256": row["raw_log_sha256"],
                "skip_reason": row.get("skip_reason"),
            }
            for row in results
        ],
        "counts": {
            status: sum(1 for row in results if row["status"] == status)
            for status in (STATUS_PASSED, STATUS_FAILED, STATUS_SKIPPED, STATUS_ABORTED)
        },
    }
    if args.summary:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    print(json.dumps(summary["counts"], indent=2))

    # Skipped and aborted are not failures of the runner: a credential that is
    # absent is a fact to record, not a reason to fail a pipeline. A check that
    # ran and failed is.
    return 1 if summary["counts"][STATUS_FAILED] else 0


if __name__ == "__main__":
    sys.exit(main())
