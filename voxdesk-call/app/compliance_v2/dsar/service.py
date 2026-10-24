"""DSAR request lifecycle: receive, verify, execute, package.

A request is a row with a status, a due date and an audit trail; execution reads
the registry, builds the export document, writes it as a signed bundle next to
the audit exports, and records what happened per table in the ledger. Erasure
uses the same matchers, so the two paths cannot disagree.

Deliberate choices:

* **identity verification is a gate, not a checkbox.** A request cannot be
  executed while ``identity_verified`` is false; exporting someone's data on an
  unverified request is exactly the breach a DSAR process exists to prevent.
* **legal hold wins.** A hold blocks erasure and the request stays open with the
  reason recorded, instead of failing half way through the registry.
* **re-running is safe.** A completed request returns its existing bundle rather
  than writing a second, differently redacted copy.
* **the clock is explicit.** ``due_at`` is set at intake from
  ``DSAR_DUE_DAYS`` and ``tick`` flags open requests past it.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import uuid
from pathlib import Path
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_event
from app.compliance_v2.audit_integrity.signing import Signer, load_signer, sign_bytes
from app.compliance_v2.dsar import erase as dsar_erase
from app.compliance_v2.dsar import export as dsar_export
from app.compliance_v2.dsar.registry import registry_document
from app.compliance_v2.hipaa.phi_tagging import PhiPolicyView, load_policy
from app.compliance_v2.settings import ComplianceV2Settings, get_compliance_v2_settings
from app.db.compliance_v2_models import DsarLedger, DsarRequest
from app.db.models import UserRole

STATUS_RECEIVED = "received"
STATUS_VERIFYING = "verifying"
STATUS_IN_PROGRESS = "in_progress"
STATUS_COMPLETED = "completed"
STATUS_REJECTED = "rejected"
STATUS_FAILED = "failed"

KIND_EXPORT = "export"
KIND_ERASE = "erase"
KINDS = (KIND_EXPORT, KIND_ERASE)

#: Statuses that have not reached a terminal state.
OPEN_STATUSES = (STATUS_RECEIVED, STATUS_VERIFYING, STATUS_IN_PROGRESS)

SIGNING_CONTEXT = b"voxdesk.dsar.bundle.v1\n"


class DsarServiceError(RuntimeError):
    code = "dsar_error"


class RequestNotFound(DsarServiceError):
    code = "dsar_request_not_found"


class InvalidTransition(DsarServiceError):
    code = "dsar_invalid_transition"


class IdentityNotVerified(DsarServiceError):
    code = "dsar_identity_not_verified"


@dataclasses.dataclass(frozen=True)
class ExecutionResult:
    request: DsarRequest
    bundle_id: str
    bundle_path: str
    bundle_sha256: str
    counts: dict[str, int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.request.id),
            "kind": self.request.kind,
            "status": self.request.status,
            "bundle_id": self.bundle_id,
            "bundle_path": self.bundle_path,
            "bundle_sha256": self.bundle_sha256,
            "counts": self.counts,
            "completed_at": _iso(self.request.completed_at),
        }


def _utcnow(moment: dt.datetime | None = None) -> dt.datetime:
    value = moment or dt.datetime.now(dt.timezone.utc)
    return value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value


def _aware(value: dt.datetime | None) -> dt.datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value


def _iso(value: dt.datetime | None) -> str:
    aware = _aware(value)
    return aware.isoformat() if aware else ""


async def get_request(
    session: AsyncSession, *, tenant_id: uuid.UUID, request_id: uuid.UUID
) -> DsarRequest:
    row = await session.scalar(
        select(DsarRequest).where(
            DsarRequest.id == request_id, DsarRequest.tenant_id == tenant_id
        )
    )
    if row is None:
        raise RequestNotFound("DSAR request not found")
    return row


async def create_request(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    kind: str,
    subject_type: str,
    subject_key: str,
    requested_by: uuid.UUID | None = None,
    note: str = "",
    settings: ComplianceV2Settings | None = None,
    now: dt.datetime | None = None,
) -> DsarRequest:
    """Record an intake. Flushes; never commits."""
    cfg = settings or get_compliance_v2_settings()
    if kind not in KINDS:
        raise DsarServiceError(f"unknown DSAR kind {kind!r}")
    subject = dsar_export.build_subject(subject_type, subject_key)
    moment = _utcnow(now)
    row = DsarRequest(
        tenant_id=tenant_id,
        kind=kind,
        subject_type=subject.subject_type,
        subject_key=subject.key,
        status=STATUS_RECEIVED,
        due_at=moment + dt.timedelta(days=cfg.dsar_due_days),
        requested_by=requested_by,
        summary={"note": (note or "")[:500], "received_at": moment.isoformat()},
    )
    session.add(row)
    await session.flush()
    await record_event(
        session,
        tenant_id=tenant_id,
        actor_user_id=requested_by,
        event_type="dsar_received",
        resource_type="dsar_request",
        resource_id=row.id,
        detail={"kind": kind, "subject_type": subject.subject_type, "due_at": _iso(row.due_at)},
    )
    return row


async def mark_verified(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    request_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    evidence: str = "",
) -> DsarRequest:
    """Confirm the requester's identity and pass the gate. Flushes."""
    row = await get_request(session, tenant_id=tenant_id, request_id=request_id)
    if row.status not in (STATUS_RECEIVED, STATUS_VERIFYING):
        raise InvalidTransition(f"cannot verify a request in state {row.status!r}")
    row.identity_verified = True
    row.status = STATUS_VERIFYING
    summary = dict(row.summary or {})
    summary["verification"] = {"actor": str(actor_user_id or ""), "evidence": (evidence or "")[:200]}
    row.summary = summary
    await session.flush()
    await record_event(
        session,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        event_type="dsar_identity_verified",
        resource_type="dsar_request",
        resource_id=row.id,
        detail={"evidence": (evidence or "")[:200]},
    )
    return row


async def set_legal_hold(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    request_id: uuid.UUID,
    hold: bool,
    actor_user_id: uuid.UUID | None,
    reason: str = "",
) -> DsarRequest:
    """Place or lift a legal hold. Flushes."""
    row = await get_request(session, tenant_id=tenant_id, request_id=request_id)
    row.legal_hold = bool(hold)
    summary = dict(row.summary or {})
    summary["legal_hold"] = {"active": bool(hold), "reason": (reason or "")[:200]}
    row.summary = summary
    await session.flush()
    await record_event(
        session,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        event_type="dsar_legal_hold_changed",
        resource_type="dsar_request",
        resource_id=row.id,
        detail={"active": bool(hold), "reason": (reason or "")[:200]},
    )
    return row


def _bundle_dir(cfg: ComplianceV2Settings) -> Path:
    return cfg.evidence_path / "dsar"


def _build_bundle_bytes(
    *,
    document: dict[str, Any],
    ledger: list[dict[str, Any]],
    request: DsarRequest,
    signer: Signer,
) -> bytes:
    """ZIP the export document with a signed manifest."""
    import io
    import zipfile

    document_bytes = dsar_export.canonical_json(document)
    ledger_bytes = dsar_export.canonical_json(ledger)
    manifest = {
        "format": "voxdesk.dsar.bundle.v1",
        "request_id": str(request.id),
        "tenant_id": str(request.tenant_id),
        "kind": request.kind,
        "subject_type": request.subject_type,
        "subject_key_sha256": hashlib.sha256(request.subject_key.encode()).hexdigest(),
        "generated_at": _utcnow().isoformat(),
        "files": {
            "export.json": hashlib.sha256(document_bytes).hexdigest(),
            "ledger.json": hashlib.sha256(ledger_bytes).hexdigest(),
        },
        "counts": document.get("counts", {}),
        "redacted_columns": document.get("redacted_columns", []),
        "signing_key_id": signer.key_id,
    }
    manifest_bytes = dsar_export.canonical_json(manifest)
    signature = sign_bytes(signer, SIGNING_CONTEXT + manifest_bytes)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("export.json", document_bytes)
        archive.writestr("ledger.json", ledger_bytes)
        archive.writestr("manifest.json", manifest_bytes)
        archive.writestr("manifest.sig", dsar_export.canonical_json(signature))
    return buffer.getvalue()


async def execute(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    request_id: uuid.UUID,
    actor_user_id: uuid.UUID | None = None,
    role: UserRole | str = UserRole.OWNER,
    settings: ComplianceV2Settings | None = None,
    signer: Signer | None = None,
    policy: PhiPolicyView | None = None,
    now: dt.datetime | None = None,
) -> ExecutionResult:
    """Run an export or erasure request and record the ledger."""
    cfg = settings or get_compliance_v2_settings()
    row = await get_request(session, tenant_id=tenant_id, request_id=request_id)
    if row.status == STATUS_COMPLETED:
        return ExecutionResult(
            request=row,
            bundle_id=row.bundle_id,
            bundle_path=row.bundle_path,
            bundle_sha256=row.bundle_sha256,
            counts=dict((row.summary or {}).get("counts", {})),
        )
    if not row.identity_verified:
        raise IdentityNotVerified("the requester's identity has not been verified")
    if row.legal_hold and row.kind == KIND_ERASE:
        summary = dict(row.summary or {})
        summary["blocked_by_legal_hold"] = True
        row.summary = summary
        await session.flush()
        await record_event(
            session,
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            event_type="dsar_erasure_blocked",
            resource_type="dsar_request",
            resource_id=row.id,
            detail={"reason": "legal_hold"},
        )
        return ExecutionResult(request=row, bundle_id="", bundle_path="", bundle_sha256="", counts={})

    subject = dsar_export.build_subject(row.subject_type, row.subject_key)
    effective_policy = policy or await load_policy(session, tenant_id)
    row.status = STATUS_IN_PROGRESS
    await session.flush()

    collected = await dsar_export.collect(session, tenant_id=tenant_id, subject=subject)
    counts = dsar_export.summarise(collected)
    ledger: list[dict[str, Any]] = []

    if row.kind == KIND_ERASE:
        results = await dsar_erase.erase_subject(
            session, tenant_id=tenant_id, subject=subject, legal_hold=row.legal_hold
        )
        verification = await dsar_erase.verify_erasure(
            session, tenant_id=tenant_id, subject=subject
        )
        for result in results:
            ledger.append(
                {
                    "table": result.table,
                    "action": result.action,
                    "rows": result.rows,
                    "detail": result.detail,
                }
            )
            session.add(
                DsarLedger(
                    tenant_id=tenant_id,
                    request_id=row.id,
                    table_name=result.table[:120],
                    action=result.action[:24],
                    rows_affected=result.rows,
                    detail=result.detail,
                )
            )
        document = {
            "format": "voxdesk.dsar.erasure.v1",
            "request_id": str(row.id),
            "tenant_id": str(tenant_id),
            "subject": subject.as_dict(),
            "ledger": ledger,
            "counts": counts,
            "registry": registry_document(),
            "verification": verification,
        }
    else:
        document = dsar_export.build_export(
            collected,
            subject=subject,
            tenant_id=tenant_id,
            request_id=row.id,
            policy=effective_policy,
            role=role,
            registry=registry_document(),
        )
        for table in collected:
            session.add(
                DsarLedger(
                    tenant_id=tenant_id,
                    request_id=row.id,
                    table_name=table.entry.table[:120],
                    action="export",
                    rows_affected=table.count,
                    detail={"capped": table.capped, "columns": list(table.entry.columns)},
                )
            )
            ledger.append(
                {"table": table.entry.table, "action": "export", "rows": table.count}
            )

    active_signer = signer or load_signer(settings=cfg)
    payload = _build_bundle_bytes(
        document=document, ledger=ledger, request=row, signer=active_signer
    )
    bundle_id = f"DSAR-{_utcnow(now).strftime('%Y%m%d')}-{uuid.uuid4().hex[:8]}"
    target = _bundle_dir(cfg) / f"{bundle_id}.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()

    row.bundle_id = bundle_id
    row.bundle_path = str(target)
    row.bundle_sha256 = digest
    summary = dict(row.summary or {})
    summary["counts"] = counts
    summary["redacted_columns"] = document.get("redacted_columns", [])
    summary["capped_tables"] = document.get("capped_tables", [])
    rows_affected = sum(entry["rows"] for entry in ledger)
    summary["rows_touched"] = rows_affected
    if row.kind == KIND_ERASE:
        summary["verification"] = verification
    row.summary = summary
    row.status = STATUS_COMPLETED
    row.completed_at = _utcnow(now)
    row.error_message = ""
    await session.flush()

    await record_event(
        session,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        event_type="dsar_completed",
        resource_type="dsar_request",
        resource_id=row.id,
        detail={
            "kind": row.kind,
            "bundle_id": bundle_id,
            "bundle_sha256": digest,
            "counts": counts,
            "rows_touched": rows_affected,
            "erasure_verified": bool(summary.get("verification", {}).get("ok", True)),
        },
    )
    return ExecutionResult(
        request=row,
        bundle_id=bundle_id,
        bundle_path=str(target),
        bundle_sha256=digest,
        counts=counts,
    )


async def reject(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    request_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    reason: str,
) -> DsarRequest:
    """Close a request that will not be executed, with the reason recorded."""
    if not (reason or "").strip():
        raise DsarServiceError("a rejection reason is required")
    row = await get_request(session, tenant_id=tenant_id, request_id=request_id)
    if row.status in (STATUS_COMPLETED, STATUS_REJECTED):
        raise InvalidTransition(f"cannot reject a request in state {row.status!r}")
    row.status = STATUS_REJECTED
    row.error_message = reason.strip()[:500]
    row.completed_at = _utcnow()
    await session.flush()
    await record_event(
        session,
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        event_type="dsar_rejected",
        resource_type="dsar_request",
        resource_id=row.id,
        detail={"reason": row.error_message},
    )
    return row


async def mark_failed(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    request_id: uuid.UUID,
    error: str,
) -> DsarRequest:
    """Record a failed execution so it is visible rather than stuck."""
    row = await get_request(session, tenant_id=tenant_id, request_id=request_id)
    row.status = STATUS_FAILED
    row.error_message = (error or "execution failed")[:500]
    await session.flush()
    await record_event(
        session,
        tenant_id=tenant_id,
        event_type="dsar_failed",
        resource_type="dsar_request",
        resource_id=row.id,
        result="failure",
        detail={"error": row.error_message},
    )
    return row


async def list_requests(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    status: str | None = None,
    kind: str | None = None,
    limit: int = 100,
) -> Sequence[DsarRequest]:
    query = select(DsarRequest).where(DsarRequest.tenant_id == tenant_id)
    if status:
        query = query.where(DsarRequest.status == status)
    if kind:
        query = query.where(DsarRequest.kind == kind)
    query = query.order_by(DsarRequest.created_at.desc()).limit(max(1, min(limit, 500)))
    return list((await session.execute(query)).scalars())


async def ledger_for(
    session: AsyncSession, *, tenant_id: uuid.UUID, request_id: uuid.UUID
) -> list[DsarLedger]:
    rows = await session.execute(
        select(DsarLedger)
        .where(DsarLedger.tenant_id == tenant_id, DsarLedger.request_id == request_id)
        .order_by(DsarLedger.table_name)
    )
    return list(rows.scalars())


async def overdue(
    session: AsyncSession, *, tenant_id: uuid.UUID | None = None, now: dt.datetime | None = None
) -> list[DsarRequest]:
    """Open requests past their due date."""
    moment = _utcnow(now)
    query = select(DsarRequest).where(
        DsarRequest.status.in_(OPEN_STATUSES),
        DsarRequest.due_at.is_not(None),
        DsarRequest.due_at < moment,
    )
    if tenant_id is not None:
        query = query.where(DsarRequest.tenant_id == tenant_id)
    return list((await session.execute(query.order_by(DsarRequest.due_at))).scalars())


async def tick(
    session: AsyncSession, *, tenant_id: uuid.UUID | None = None, now: dt.datetime | None = None
) -> dict:
    """Audit overdue requests once per check. Returns counts."""
    rows = await overdue(session, tenant_id=tenant_id, now=now)
    for row in rows:
        summary = dict(row.summary or {})
        if summary.get("overdue_flagged"):
            continue
        summary["overdue_flagged"] = _iso(_utcnow(now))
        row.summary = summary
        await record_event(
            session,
            tenant_id=row.tenant_id,
            event_type="dsar_overdue",
            resource_type="dsar_request",
            resource_id=row.id,
            result="failure",
            detail={"kind": row.kind, "due_at": _iso(row.due_at)},
        )
    await session.flush()
    return {"checked": len(rows), "flagged": sum(1 for row in rows if row.summary.get("overdue_flagged"))}


def as_dict(row: DsarRequest) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "tenant_id": str(row.tenant_id),
        "kind": row.kind,
        "subject_type": row.subject_type,
        "status": row.status,
        "identity_verified": row.identity_verified,
        "legal_hold": row.legal_hold,
        "due_at": _iso(row.due_at),
        "overdue": bool(row.due_at and _aware(row.due_at) < _utcnow() and row.status in OPEN_STATUSES),
        "bundle_id": row.bundle_id,
        "bundle_sha256": row.bundle_sha256,
        "counts": dict((row.summary or {}).get("counts", {})),
        "error_message": row.error_message,
        "created_at": _iso(row.created_at),
        "completed_at": _iso(row.completed_at),
    }


def verify_bundle(path: str | Path, *, public_key_pem: str) -> dict[str, Any]:
    """Re-verify a written bundle: member hashes and the manifest signature."""
    import zipfile

    from app.compliance_v2.audit_integrity.signing import verify_bytes

    target = Path(path)
    if not target.exists():
        return {"ok": False, "reason": "bundle_missing"}
    try:
        with zipfile.ZipFile(target) as archive:
            names = set(archive.namelist())
            if {"manifest.json", "manifest.sig"} - names:
                return {"ok": False, "reason": "bundle_incomplete"}
            manifest_bytes = archive.read("manifest.json")
            manifest = json.loads(manifest_bytes.decode())
            signature = json.loads(archive.read("manifest.sig").decode())
            for member, expected in (manifest.get("files") or {}).items():
                if member not in names:
                    return {"ok": False, "reason": f"missing_member:{member}"}
                if hashlib.sha256(archive.read(member)).hexdigest() != expected:
                    return {"ok": False, "reason": f"member_hash_mismatch:{member}"}
    except (OSError, ValueError, KeyError):
        return {"ok": False, "reason": "bundle_unreadable"}
    ok = verify_bytes(
        SIGNING_CONTEXT + manifest_bytes,
        signature=str(signature.get("signature", "")),
        public_key_pem=public_key_pem or str(signature.get("public_key_pem", "")),
    )
    return {
        "ok": bool(ok),
        "reason": "" if ok else "signature_invalid",
        "request_id": manifest.get("request_id", ""),
        "counts": manifest.get("counts", {}),
    }


__all__ = [
    "KIND_ERASE",
    "KIND_EXPORT",
    "KINDS",
    "OPEN_STATUSES",
    "STATUS_COMPLETED",
    "STATUS_FAILED",
    "STATUS_IN_PROGRESS",
    "STATUS_RECEIVED",
    "STATUS_REJECTED",
    "STATUS_VERIFYING",
    "DsarServiceError",
    "ExecutionResult",
    "IdentityNotVerified",
    "InvalidTransition",
    "RequestNotFound",
    "as_dict",
    "create_request",
    "execute",
    "get_request",
    "ledger_for",
    "list_requests",
    "mark_failed",
    "mark_verified",
    "overdue",
    "reject",
    "set_legal_hold",
    "tick",
    "verify_bundle",
]
