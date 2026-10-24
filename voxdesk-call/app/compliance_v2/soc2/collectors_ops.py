"""Operational evidence: backups, encryption at rest, retention, vulns, SLO.

Every source is a file or a table that either exists in this deployment or does
not. None of them is synthesised:

* ``evidence/dr/*.json`` — a real disaster-recovery drill record with a
  timestamp; the collector reports its age and fails when it is stale;
* encryption at rest — whether a KMS provider is configured and healthy, and
  whether the key ring that seals secrets is present;
* retention — the most recent retention run recorded in the audit log;
* vulnerability scans — ``pip-audit``/``trivy`` JSON output, when the CI job has
  left one behind;
* SLO — the request-error counters the metrics module already keeps.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.compliance_v2.settings import get_compliance_v2_settings
from app.compliance_v2.soc2.collectors_base import CollectorResult, EvidenceCollector, item
from app.db.models import AuditLog

CONTROL_BOUNDARY = "CC6.6"
CONTROL_KEY_MANAGEMENT = "CC6.7"
CONTROL_LOGGING = "CC7.3"
CONTROL_AVAILABILITY = "A1.1"
CONTROL_BACKUP = "A1.2"
CONTROL_CONFIDENTIALITY = "C1.1"
CONTROL_DISPOSAL = "C1.2"
CONTROL_PROCESSING = "PI1.1"
CONTROL_WEBHOOK_INTEGRITY = "PI1.4"

#: Directories searched for drill records and scan output, relative to the root.
DR_DIR = Path("evidence") / "dr"
SECURITY_DIR = Path("evidence") / "security"
SCAN_CANDIDATES = (
    Path("evidence") / "security" / "pip-audit.json",
    Path("evidence") / "security" / "trivy.json",
    Path("reports") / "check" / "pip-audit.json",
)


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _aware(value: dt.datetime | None) -> dt.datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value


@dataclasses.dataclass(frozen=True)
class DrillRecord:
    path: str
    recorded_at: dt.datetime
    payload: dict[str, Any]

    @property
    def age_hours(self) -> float:
        return (_utcnow() - self.recorded_at).total_seconds() / 3600


def latest_drill(root: Path) -> DrillRecord | None:
    """The newest readable drill record, or ``None`` when none exists."""
    directory = root / DR_DIR
    if not directory.is_dir():
        return None
    best: DrillRecord | None = None
    for path in sorted(directory.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        stamp = None
        for key in ("completed_at", "finished_at", "recorded_at", "timestamp", "started_at"):
            value = payload.get(key) if isinstance(payload, dict) else None
            if isinstance(value, str):
                try:
                    stamp = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
                except ValueError:
                    stamp = None
            if stamp is not None:
                break
        if stamp is None:
            stamp = dt.datetime.fromtimestamp(path.stat().st_mtime, dt.timezone.utc)
        record = DrillRecord(path=str(path.relative_to(root)), recorded_at=_aware(stamp) or _utcnow(), payload=payload if isinstance(payload, dict) else {})
        if best is None or record.recorded_at > best.recorded_at:
            best = record
    return best


def load_scan_report(root: Path) -> tuple[str, dict] | None:
    for candidate in SCAN_CANDIDATES:
        path = root / candidate
        if not path.is_file():
            continue
        try:
            return str(candidate), json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return None


def load_security_harness(root: Path) -> tuple[str, dict] | None:
    """The newest ``security_harness`` report, if the CI job has produced one."""
    directory = root / SECURITY_DIR
    if not directory.is_dir():
        return None
    candidates = sorted(directory.glob("*.json"))
    for path in reversed(candidates):
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(parsed, dict) and isinstance(parsed.get("checks"), list):
            return str(path.relative_to(root)), parsed
    return None


class OpsCollector(EvidenceCollector):
    """Backups, encryption, retention, vulnerability scanning and SLO."""

    name = "ops"
    control_ids = (
        CONTROL_BOUNDARY,
        CONTROL_KEY_MANAGEMENT,
        CONTROL_LOGGING,
        CONTROL_AVAILABILITY,
        CONTROL_BACKUP,
        CONTROL_CONFIDENTIALITY,
        CONTROL_DISPOSAL,
        CONTROL_PROCESSING,
        CONTROL_WEBHOOK_INTEGRITY,
    )

    def __init__(self, *, root: str | Path | None = None, kms_health=None) -> None:
        self._root = Path(root) if root is not None else Path.cwd()
        self._kms_health = kms_health

    async def _encryption_state(self) -> dict[str, Any]:
        """Ask the configured provider for its real state; never assume it."""
        cfg = get_compliance_v2_settings()
        state: dict[str, Any] = {
            "kms_provider": cfg.kms_provider,
            "kms_key_id": cfg.kms_key_id,
            "development_provider": cfg.kms_provider == "local_dev",
        }
        try:
            if self._kms_health is not None:
                health = await self._kms_health()
                state.update(
                    {
                        "kms_ok": bool(getattr(health, "ok", False)),
                        "kms_detail": getattr(health, "detail", ""),
                    }
                )
            else:
                from app.compliance_v2.kms import get_kms

                health = await get_kms(cfg).health()
                state.update({"kms_ok": health.ok, "kms_detail": health.detail})
        except Exception as exc:  # noqa: BLE001 - an unconfigured KMS is a finding, not a crash
            state.update({"kms_ok": False, "kms_detail": f"{type(exc).__name__}: {exc}"})

        try:
            from app.auth.identity.secrets import encryption_available

            state["secret_key_ring_present"] = bool(encryption_available())
        except Exception:  # noqa: BLE001 - missing key ring is a legitimate state
            state["secret_key_ring_present"] = False
        return state

    async def collect(self, session: AsyncSession) -> "CollectorResult":
        cfg = get_compliance_v2_settings()
        findings: list[str] = []
        items = []

        # ---- backups / DR ------------------------------------------------
        drill = latest_drill(self._root)
        if drill is None:
            return self._result(
                configured=False,
                not_configured_reason="no disaster-recovery drill record under evidence/dr/",
            )
        drill_payload = {
            "record": drill.path,
            "recorded_at": drill.recorded_at.isoformat(),
            "age_hours": round(drill.age_hours, 2),
            "max_age_hours": cfg.evidence_dr_max_age_hours,
        }
        items.append(item(self.name, CONTROL_BACKUP, "evidence/dr", drill_payload))
        if drill.age_hours > cfg.evidence_dr_max_age_hours:
            findings.append(
                f"the newest disaster-recovery drill is {drill.age_hours:.0f}h old "
                f"(limit {cfg.evidence_dr_max_age_hours}h)"
            )

        # ---- encryption --------------------------------------------------
        encryption = await self._encryption_state()
        items.append(item(self.name, CONTROL_CONFIDENTIALITY, "kms", encryption))
        items.append(
            item(
                self.name,
                CONTROL_KEY_MANAGEMENT,
                "kms",
                {
                    "provider": encryption.get("kms_provider"),
                    "key_id": encryption.get("kms_key_id"),
                    "healthy": encryption.get("kms_ok"),
                    "secret_key_ring_present": encryption.get("secret_key_ring_present"),
                    "envelope_format": "env1",
                },
            )
        )
        if not encryption.get("kms_ok"):
            findings.append("the configured KMS provider is not healthy")
        if encryption.get("development_provider") and cfg.is_production:
            findings.append("a development KMS provider is configured in production")
        if not encryption.get("secret_key_ring_present"):
            findings.append("no secret encryption key ring is configured")

        # ---- retention ---------------------------------------------------
        retention_row = (
            await session.execute(
                select(AuditLog.created_at, AuditLog.event_type)
                .where(AuditLog.event_type.in_(("retention_purge", "retention_executed", "call_deleted")))
                .order_by(AuditLog.created_at.desc())
                .limit(1)
            )
        ).first()
        retention_payload: dict[str, Any] = {
            "last_retention_event_at": _aware(retention_row[0]).isoformat() if retention_row else "",
            "last_retention_event": retention_row[1] if retention_row else "",
        }
        items.append(item(self.name, CONTROL_DISPOSAL, "audit_logs", retention_payload))
        if retention_row is None:
            findings.append("no retention run is recorded in the audit log")

        # ---- vulnerability scanning --------------------------------------
        scan = load_scan_report(self._root)
        if scan is None:
            items.append(
                item(
                    self.name,
                    CONTROL_LOGGING,
                    "audit_logs",
                    {
                        "audit_rows": int(
                            (await session.execute(select(func.count(AuditLog.id)))).scalar_one()
                        ),
                        "vulnerability_scan": "not_present",
                    },
                )
            )
        else:
            source, report = scan
            items.append(item(self.name, CONTROL_LOGGING, source, {"scan_present": True}))
            high = _count_severities(report)
            items.append(item(self.name, CONTROL_PROCESSING, source, {"severity_counts": high}))
            if high.get("critical", 0) or high.get("high", 0):
                findings.append(
                    f"the newest vulnerability scan reports {high.get('critical', 0)} critical and "
                    f"{high.get('high', 0)} high finding(s)"
                )

        # ---- processing integrity / webhooks ------------------------------
        from app.db.models import WebhookDelivery

        deliveries = (
            await session.execute(
                select(WebhookDelivery.status, func.count(WebhookDelivery.id)).group_by(
                    WebhookDelivery.status
                )
            )
        ).all()
        items.append(
            item(
                self.name,
                CONTROL_WEBHOOK_INTEGRITY,
                "webhook_deliveries",
                {"status_counts": {str(status): int(count) for status, count in deliveries}},
            )
        )

        # ---- boundary protection (pentest regression harness) --------------
        harness = load_security_harness(self._root)
        if harness is not None:
            source, report = harness
            checks = report.get("checks") if isinstance(report, dict) else None
            failed = [
                str((entry or {}).get("check", ""))
                for entry in (checks or [])
                if isinstance(entry, dict) and not entry.get("ok", False)
            ]
            items.append(
                item(
                    self.name,
                    CONTROL_BOUNDARY,
                    source,
                    {
                        "harness_run_at": report.get("generated_at", "") if isinstance(report, dict) else "",
                        "checks": len(checks or []),
                        "failed_checks": failed,
                    },
                )
            )
            if failed:
                findings.append(
                    "the pentest regression harness reports failing checks: " + ", ".join(failed[:5])
                )

        # ---- availability --------------------------------------------------
        items.append(
            item(
                self.name,
                CONTROL_AVAILABILITY,
                "prometheus",
                {
                    "slo_target": cfg.evidence_slo_target,
                    "scrape_registry": _registry_has_metrics(),
                },
            )
        )

        return self._result(configured=True, items=items, findings=findings)


def _registry_has_metrics() -> bool:
    """True when this process has registered at least one Prometheus collector.

    An empty registry means the instrumentation never loaded, which is worth
    reporting; it is not an assertion that anything is being scraped, so the
    value is evidence for a human, not a verdict.
    """
    try:
        from prometheus_client import REGISTRY

        return len(list(REGISTRY.collect())) > 0
    except Exception:  # noqa: BLE001 - the registry is optional at runtime
        return False


def _count_severities(report: Any) -> dict[str, int]:
    """Count critical/high findings in a pip-audit or trivy JSON document."""
    counts = {"critical": 0, "high": 0}
    if not isinstance(report, dict):
        return counts
    vulnerabilities = report.get("vulnerabilities")
    if isinstance(vulnerabilities, list):
        for entry in vulnerabilities:
            severity = str((entry or {}).get("severity", "")).lower()
            if severity in counts:
                counts[severity] += 1
        return counts
    results = report.get("Results")
    if isinstance(results, list):
        for result in results:
            for entry in (result or {}).get("Vulnerabilities") or []:
                severity = str((entry or {}).get("Severity", "")).lower()
                if severity in counts:
                    counts[severity] += 1
        return counts
    dependencies = report.get("dependencies")
    if isinstance(dependencies, list):
        for dependency in dependencies:
            for entry in (dependency or {}).get("vulns") or []:
                counts["high"] += 1
        return counts
    return counts


__all__ = [
    "DR_DIR",
    "SECURITY_DIR",
    "OpsCollector",
    "latest_drill",
    "load_scan_report",
    "load_security_harness",
]
