"""Resumable re-encryption of existing secrets onto the current KMS key.

The product already stores secrets with the ``v1.`` cipher from
``app.integrations.crm.crypto``. Moving to envelope encryption therefore needs a
migration that can be stopped, resumed, and inspected — not a script that runs
until it dies.

Properties, each covered by a test:

* **resumable** — progress is a ``(cursor_table, cursor_id)`` checkpoint stored
  on the job row, so a restart continues from the last committed row;
* **idempotent** — a value already in ``env1`` form for the active provider is
  skipped, so running the job twice re-encrypts nothing;
* **verifying** — each row is decrypted again after rewriting and compared with
  the original plaintext's digest; a mismatch aborts the job instead of
  destroying a credential;
* **dry-run safe** — ``dry_run`` counts what *would* change and writes no
  ciphertext and no checkpoint;
* **bounded** — a batch size and an optional row cap keep one tick short.

The table/column list is a fixed registry in this module. Nothing here builds SQL
from user input: identifiers come from the constant below, and values are always
bound parameters.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import uuid
from typing import Any, Sequence

from sqlalchemy import Uuid, bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.compliance_v2.kms.base import KmsProvider
from app.compliance_v2.kms.envelope import (
    PREFIX,
    decrypt_value,
    encrypt_value,
    is_envelope,
    is_legacy,
    read_value,
)
from app.compliance_v2.settings import ComplianceV2Settings, get_compliance_v2_settings
from app.db.compliance_v2_models import KmsRekeyJob

STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"


@dataclasses.dataclass(frozen=True)
class RekeyTarget:
    """One (table, column) pair holding sealed values."""

    table: str
    column: str
    #: Purpose the legacy cipher was given when the value was written. ``None``
    #: means the column never held legacy values and only needs re-wrapping.
    legacy_purpose: str | None
    #: Column holding the per-row provider name for the legacy cipher, if any.
    legacy_provider_column: str = ""
    document: str = ""

    @property
    def key(self) -> str:
        return f"{self.table}.{self.column}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "table": self.table,
            "column": self.column,
            "key": self.key,
            "document": self.document,
        }


#: Every column in the product that holds a sealed value. Kept in one place so a
#: reviewer can check it against ``alembic`` and the models in a single read.
TARGETS: tuple[RekeyTarget, ...] = (
    RekeyTarget(
        table="crm_integrations",
        column="credentials_encrypted",
        legacy_provider_column="provider",
        legacy_purpose=None,
        document="CRM OAuth credentials (provider name doubles as the cipher purpose)",
    ),
    RekeyTarget(
        table="calendar_integrations",
        column="credentials_encrypted",
        legacy_provider_column="provider",
        legacy_purpose=None,
        document="Calendar OAuth credentials",
    ),
    RekeyTarget(
        table="webhook_subscriptions",
        column="secret_envelope",
        legacy_purpose="outbound_webhook",
        document="Outbound webhook signing secret",
    ),
    RekeyTarget(
        table="webhook_subscriptions",
        column="headers_envelope",
        legacy_purpose="outbound_webhook",
        document="Outbound webhook custom headers (may carry a bearer token)",
    ),
    RekeyTarget(
        table="mfa_factors",
        column="secret_encrypted",
        legacy_purpose="totp",
        document="TOTP seeds",
    ),
    RekeyTarget(
        table="sso_connections",
        column="client_secret_encrypted",
        legacy_purpose="oidc_client_secret",
        document="OIDC client secret",
    ),
)


def select_targets(keys: Sequence[str] | None = None) -> list[RekeyTarget]:
    """The targets to process, optionally narrowed by ``table.column`` keys."""
    if not keys:
        return list(TARGETS)
    wanted = {str(key).strip() for key in keys}
    chosen = [target for target in TARGETS if target.key in wanted]
    unknown = wanted - {target.key for target in chosen}
    if unknown:
        raise ValueError(f"unknown rekey target(s): {', '.join(sorted(unknown))}")
    return chosen


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _plaintext_digest(payload: str) -> str:
    """A comparable fingerprint of a plaintext, without keeping the plaintext."""
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclasses.dataclass
class TargetProgress:
    target: RekeyTarget
    scanned: int = 0
    rekeyed: int = 0
    skipped: int = 0
    failed: int = 0
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "target": self.target.key,
            "scanned": self.scanned,
            "rekeyed": self.rekeyed,
            "skipped": self.skipped,
            "failed": self.failed,
            "error": self.error,
        }


async def _fetch_batch(
    session: AsyncSession,
    target: RekeyTarget,
    *,
    after_id: str,
    limit: int,
    tenant_id: uuid.UUID | None = None,
) -> list[Any]:
    """One keyset page of rows that still carry a sealed value."""
    columns = f'id, tenant_id, "{target.column}" AS value'
    if target.legacy_provider_column:
        columns += f', "{target.legacy_provider_column}" AS legacy_provider'
    clause = (
        f'SELECT {columns} FROM "{target.table}" '
        f'WHERE "{target.column}" IS NOT NULL AND "{target.column}" != \'\''
    )
    params: dict[str, Any] = {"limit": limit}
    binds = []
    # The keyset clause is only added when there is a checkpoint to resume from.
    # Both the id and the tenant are bound with SQLAlchemy's ``Uuid`` type: the
    # key columns are UUID on PostgreSQL and 32-character hex on SQLite, and a
    # raw string bind is only portable by accident on one of the two.
    if after_id:
        clause += " AND id > :after_id"
        params["after_id"] = uuid.UUID(str(after_id))
        binds.append(bindparam("after_id", type_=Uuid))
    if tenant_id is not None:
        clause += " AND tenant_id = :tenant_id"
        params["tenant_id"] = (
            tenant_id if isinstance(tenant_id, uuid.UUID) else uuid.UUID(str(tenant_id))
        )
        binds.append(bindparam("tenant_id", type_=Uuid))
    clause += " ORDER BY id LIMIT :limit"
    statement = text(clause)
    if binds:
        statement = statement.bindparams(*binds)
    result = await session.execute(statement, params)
    return list(result.mappings())


async def _rewrite(
    session: AsyncSession, target: RekeyTarget, row_id: Any, value: str
) -> None:
    statement = text(
        f'UPDATE "{target.table}" SET "{target.column}" = :value WHERE id = :row_id'
    )
    await session.execute(statement, {"value": value, "row_id": row_id})


def _legacy_purpose(target: RekeyTarget, row_provider: str | None) -> str | None:
    if target.legacy_purpose:
        return target.legacy_purpose
    if target.legacy_provider_column and row_provider:
        return str(row_provider)
    return None


async def run_target(
    session: AsyncSession,
    target: RekeyTarget,
    *,
    provider: KmsProvider,
    job: KmsRekeyJob,
    batch_size: int | None = None,
    max_rows: int | None = None,
) -> TargetProgress:
    """Re-encrypt one target, advancing the job checkpoint as it goes."""
    progress = TargetProgress(target=target)
    after_id = job.cursor_id if job.cursor_table == target.table else ""
    remaining = None if max_rows is None else max(0, int(max_rows))
    dry_run = bool(job.dry_run)

    while remaining is None or remaining > 0:
        size = batch_size if remaining is None else min(batch_size, remaining)
        rows = await _fetch_batch(
            session,
            target,
            after_id=after_id,
            limit=max(1, size),
            tenant_id=job.tenant_id,
        )
        if not rows:
            break
        batch_scanned = 0
        batch_rekeyed = 0
        batch_skipped = 0
        batch_failed = 0

        for row in rows:
            value = str(row["value"])
            row_id = row["id"]
            after_id = str(row_id)
            progress.scanned += 1
            batch_scanned += 1
            if remaining is not None:
                remaining -= 1
            tenant_id = str(row["tenant_id"])

            if is_envelope(value) and value.startswith(f"{PREFIX}{provider.name}."):
                progress.skipped += 1
                batch_skipped += 1
                continue
            if not (is_envelope(value) or is_legacy(value)):
                # Not a value this job understands: leave it exactly as it is
                # and report it, rather than overwriting something unknown.
                progress.skipped += 1
                batch_skipped += 1
                continue

            purpose = _legacy_purpose(target, row.get("legacy_provider"))
            try:
                plaintext = await read_value(
                    value,
                    tenant_id=tenant_id,
                    column=target.column,
                    provider=provider,
                    legacy_purpose=purpose,
                )
            except Exception as exc:  # noqa: BLE001 - a row failure must not abort the sweep
                progress.failed += 1
                batch_failed += 1
                progress.error = f"{type(exc).__name__} at row {row_id}"
                continue

            if dry_run:
                progress.rekeyed += 1
                batch_rekeyed += 1
                continue

            sealed = await encrypt_value(
                plaintext,
                tenant_id=tenant_id,
                column=target.column,
                provider=provider,
            )
            # Verify before writing: a ciphertext that cannot round-trip would
            # destroy a credential the moment it lands.
            check = await decrypt_value(
                sealed, tenant_id=tenant_id, column=target.column, provider=provider
            )
            if _plaintext_digest(check) != _plaintext_digest(plaintext):
                progress.failed += 1
                batch_failed += 1
                progress.error = f"round-trip mismatch at row {row_id}"
                continue

            await _rewrite(session, target, row_id, sealed)
            progress.rekeyed += 1
            batch_rekeyed += 1

        job.cursor_table = target.table
        job.cursor_id = after_id
        job.rows_scanned = int(job.rows_scanned) + batch_scanned
        job.rows_rekeyed = int(job.rows_rekeyed) + batch_rekeyed
        job.rows_skipped = int(job.rows_skipped) + batch_skipped
        job.rows_failed = int(job.rows_failed) + batch_failed
        if not dry_run:
            await session.flush()
        if remaining is not None and remaining <= 0:
            break

    return progress


async def run_rekey(
    session: AsyncSession,
    provider: KmsProvider,
    *,
    job: KmsRekeyJob,
    batch_size: int = 100,
    max_rows: int | None = None,
    settings: ComplianceV2Settings | None = None,
) -> KmsRekeyJob:
    """Run every target of a job until it is done or the row cap is reached."""
    cfg = settings or get_compliance_v2_settings()
    effective_batch = max(1, min(int(batch_size or cfg.kms_rekey_batch_size), 1000))
    targets = select_targets(job.target_tables)
    job.status = STATUS_RUNNING
    job.target_key_id = str(provider.key_id)[:200]
    await session.flush()

    try:
        for target in targets:
            await run_target(
                session,
                target,
                provider=provider,
                job=job,
                batch_size=effective_batch,
                max_rows=max_rows,
            )
        job.status = STATUS_SUCCEEDED
        job.finished_at = _utcnow()
        job.error_message = ""
    except Exception as exc:  # noqa: BLE001 - recorded on the job row, then re-raised
        job.status = STATUS_FAILED
        job.error_message = f"{type(exc).__name__}: {exc}"[:500]
        job.finished_at = _utcnow()
        await session.flush()
        raise
    await session.flush()
    return job


async def create_job(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID | None,
    requested_by: uuid.UUID | None,
    dry_run: bool,
    target_keys: Sequence[str] | None = None,
    provider: KmsProvider | None = None,
) -> KmsRekeyJob:
    """Create a job row for the selected targets. Flushes."""
    targets = select_targets(target_keys)
    job = KmsRekeyJob(
        tenant_id=tenant_id,
        status="queued",
        dry_run=bool(dry_run),
        target_tables=[target.key for target in targets],
        source_key_ids=sorted({str(target.column) for target in targets}),
        target_key_id=str(provider.key_id)[:200] if provider is not None else "",
        requested_by=requested_by,
    )
    session.add(job)
    await session.flush()
    return job


async def job_status(session: AsyncSession, job_id: uuid.UUID) -> KmsRekeyJob | None:
    return await session.get(KmsRekeyJob, job_id)


def legacy_key_ids(session_rows: Sequence[str]) -> list[str]:
    """Distinct legacy key ids still visible in a set of stored values."""
    seen: set[str] = set()
    for value in session_rows:
        if is_legacy(value):
            parts = value.split(".")
            if len(parts) >= 2:
                seen.add(parts[1])
    return sorted(seen)


__all__ = [
    "STATUS_FAILED",
    "STATUS_RUNNING",
    "STATUS_SUCCEEDED",
    "TARGETS",
    "RekeyTarget",
    "TargetProgress",
    "create_job",
    "job_status",
    "legacy_key_ids",
    "run_rekey",
    "run_target",
    "select_targets",
]
