"""Evidence runner: collect, summarise, persist, hash.

One run does four things, in this order:

1. every collector runs against the real database (and, for the change
   collector, the real repository API);
2. per-control statuses are folded with "worst wins" — a FAIL or ERROR anywhere
   dominates, and a control nobody produced evidence for is ``NOT_CONFIGURED``,
   never ``PASS``;
3. the run is persisted as an ``EvidenceSnapshot`` plus one ``ControlStatus``
   row per control;
4. a JSON document and a SHA-256 index are written under ``EVIDENCE_DIR`` so the
   output can be attached to an audit request afterwards.

A missing collector module is an ``ERROR`` for its controls, not a silent skip.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import uuid
from pathlib import Path
from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.compliance_v2.settings import ComplianceV2Settings, get_compliance_v2_settings
from app.compliance_v2.soc2.collectors_base import (
    EVIDENCE_ERROR,
    EVIDENCE_FAIL,
    EVIDENCE_NOT_CONFIGURED,
    EVIDENCE_PASS,
    CollectorResult,
    summarise,
)
from app.compliance_v2.soc2.control_map import ControlMap, load_control_map
from app.db.compliance_v2_models import ControlStatus, EvidenceSnapshot


def _iso(moment: dt.datetime | None = None) -> str:
    value = moment or dt.datetime.now(dt.timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def build_collectors(*, root: str | Path | None = None) -> dict[str, Any]:
    """Instantiate the collector set (lazily, so imports stay cheap)."""
    from app.compliance_v2.soc2.collectors_access import AccessCollector
    from app.compliance_v2.soc2.collectors_change import ChangeCollector
    from app.compliance_v2.soc2.collectors_incident import IncidentCollector
    from app.compliance_v2.soc2.collectors_ops import OpsCollector

    return {
        "access": AccessCollector(),
        "change": ChangeCollector(),
        "incident": IncidentCollector(),
        "ops": OpsCollector(root=root),
    }


@dataclasses.dataclass
class EvidenceRun:
    run_id: str
    started_at: str
    finished_at: str
    statuses: dict[str, str]
    results: list[CollectorResult]
    control_map: ControlMap
    document: dict[str, Any]

    @property
    def counts(self) -> dict[str, int]:
        counts = {
            EVIDENCE_PASS: 0,
            EVIDENCE_FAIL: 0,
            EVIDENCE_NOT_CONFIGURED: 0,
            EVIDENCE_ERROR: 0,
        }
        for status in self.statuses.values():
            counts[status] = counts.get(status, 0) + 1
        return counts

    @property
    def ok(self) -> bool:
        """A run is clean only when nothing failed and nothing errored."""
        counts = self.counts
        return counts[EVIDENCE_FAIL] == 0 and counts[EVIDENCE_ERROR] == 0

    def summary(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "counts": self.counts,
            "ok": self.ok,
            "controls": dict(sorted(self.statuses.items())),
            "unmapped_controls": list(self.control_map.unmapped),
            "matrix_source": self.control_map.source,
            "matrix_document_present": self.control_map.document_present,
            "collectors": {
                result.collector: {
                    "configured": result.configured,
                    "findings": list(result.findings),
                    "not_configured_reason": result.not_configured_reason,
                    "error": result.error,
                }
                for result in self.results
            },
        }


def _collector_detail(result: CollectorResult, control_id: str) -> dict[str, Any]:
    return {
        "collector": result.collector,
        "configured": result.configured,
        "findings": list(result.findings),
        "not_configured_reason": result.not_configured_reason,
        "items": [entry.as_dict() for entry in result.items_for(control_id)],
    }


async def run_evidence(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID | None = None,
    created_by: uuid.UUID | None = None,
    root: str | Path | None = None,
    settings: ComplianceV2Settings | None = None,
    collectors: dict[str, Any] | None = None,
    control_map: ControlMap | None = None,
) -> EvidenceRun:
    """Run every collector once and persist the snapshot. Flushes."""
    cfg = settings or get_compliance_v2_settings()
    started = _iso()
    mapping = control_map or load_control_map(root)
    active = collectors if collectors is not None else build_collectors(root=root)

    results: list[CollectorResult] = []
    for name in mapping_collector_order(mapping):
        collector = active.get(name)
        if collector is None:
            results.append(
                CollectorResult(
                    collector=name,
                    control_ids=tuple(
                        control for control, owner in mapping.controls.items() if owner == name
                    ),
                    configured=False,
                    error=f"collector {name!r} is not available in this deployment",
                )
            )
            continue
        try:
            results.append(await collector.collect(session))
        except Exception as exc:  # noqa: BLE001 - one broken source must not hide the rest
            results.append(
                CollectorResult(
                    collector=name,
                    control_ids=tuple(collector.control_ids),
                    configured=False,
                    error=f"{type(exc).__name__}: {exc}"[:300],
                )
            )

    control_ids = list(mapping.controls)
    statuses = summarise(results, control_ids)
    finished = _iso()
    run_id = str(uuid.uuid4())

    document = {
        "format": "voxdesk.compliance.evidence.v1",
        "run_id": run_id,
        "generated_at": finished,
        "tenant_id": str(tenant_id) if tenant_id else None,
        "counts": {
            status: list(statuses.values()).count(status)
            for status in (EVIDENCE_PASS, EVIDENCE_FAIL, EVIDENCE_NOT_CONFIGURED, EVIDENCE_ERROR)
        },
        "controls": {
            control: {
                "status": statuses[control],
                "collector": mapping.collector_for(control),
                "details": [
                    _collector_detail(result, control)
                    for result in results
                    if control in result.control_ids
                ],
            }
            for control in control_ids
        },
        "collectors": [result.as_dict() for result in results],
        "matrix": mapping.as_dict(),
    }

    snapshot = EvidenceSnapshot(
        tenant_id=tenant_id,
        run_key=f"{finished}:{run_id[:8]}",
        summary=document["counts"],
        pass_count=document["counts"][EVIDENCE_PASS],
        fail_count=document["counts"][EVIDENCE_FAIL],
        not_configured_count=document["counts"][EVIDENCE_NOT_CONFIGURED],
        error_count=document["counts"][EVIDENCE_ERROR],
        created_by=created_by,
    )
    session.add(snapshot)
    await session.flush()

    for control in control_ids:
        owner = mapping.collector_for(control)
        detail = next(
            (_collector_detail(result, control) for result in results if control in result.control_ids),
            {"collector": owner, "configured": False, "items": [], "findings": []},
        )
        body = json.dumps(detail, sort_keys=True, separators=(",", ":"), default=str)
        session.add(
            ControlStatus(
                tenant_id=tenant_id,
                snapshot_id=snapshot.id,
                control_id=control,
                collector=owner,
                status=statuses[control],
                detail=detail,
                sha256=hashlib.sha256(body.encode("utf-8")).hexdigest(),
            )
        )

    run = EvidenceRun(
        run_id=run_id,
        started_at=started,
        finished_at=finished,
        statuses=statuses,
        results=results,
        control_map=mapping,
        document=document,
    )
    stored = persist_evidence(run, snapshot=snapshot, settings=cfg)
    snapshot.file_path = str(stored["document"])
    snapshot.sha256 = stored["sha256"]
    await session.flush()
    return run


def mapping_collector_order(mapping: ControlMap) -> list[str]:
    """Collector names in a stable order, taken from the mapping."""
    order: list[str] = []
    for collector in mapping.controls.values():
        if collector not in order and collector != "unmapped":
            order.append(collector)
    return order


def persist_evidence(
    run: EvidenceRun,
    *,
    snapshot: EvidenceSnapshot,
    settings: ComplianceV2Settings,
) -> dict[str, Any]:
    """Write the run document and a SHA-256 index under ``EVIDENCE_DIR``."""
    directory = settings.evidence_path
    directory.mkdir(parents=True, exist_ok=True)
    stamp = run.finished_at.replace(":", "").replace("-", "")
    document_path = directory / f"evidence-{stamp}-{run.run_id[:8]}.json"
    body = json.dumps(run.document, indent=2, sort_keys=True, default=str)
    document_path.write_text(body, encoding="utf-8")
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()

    index_path = directory / "index.json"
    index: list[dict[str, Any]] = []
    if index_path.exists():
        try:
            parsed = json.loads(index_path.read_text(encoding="utf-8"))
            index = parsed if isinstance(parsed, list) else []
        except (OSError, ValueError):
            index = []
    index.append(
        {
            "run_id": run.run_id,
            "snapshot_id": str(snapshot.id),
            "file": document_path.name,
            "sha256": digest,
            "generated_at": run.finished_at,
            "counts": run.counts,
        }
    )
    index_path.write_text(json.dumps(index, indent=2, sort_keys=True), encoding="utf-8")
    return {"document": document_path, "sha256": digest, "index": index_path}


def summarise_snapshot(document: dict[str, Any], control_ids: Sequence[str]) -> dict[str, str]:
    """Read statuses back out of a stored document (used by the CLI and API)."""
    controls = document.get("controls") if isinstance(document, dict) else None
    if not isinstance(controls, dict):
        return {control: EVIDENCE_NOT_CONFIGURED for control in control_ids}
    return {
        control: str((controls.get(control) or {}).get("status", EVIDENCE_NOT_CONFIGURED))
        for control in control_ids
    }


__all__ = [
    "EvidenceRun",
    "build_collectors",
    "mapping_collector_order",
    "persist_evidence",
    "run_evidence",
    "summarise_snapshot",
]
