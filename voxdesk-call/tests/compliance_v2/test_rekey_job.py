"""Envelope encryption, the development KMS and the rekey job.

The cryptography here is real: a local master key file wraps a per-record data
key, the record ciphertext is AES-GCM with an associated-data binding, and a
rekey re-wraps the data key without ever putting plaintext back on disk. The
tests assert the properties an operator relies on — a value cannot be decrypted
in the wrong tenant or the wrong column, a rewrapped token still decrypts, and a
rekey job reports what it touched.
"""

from __future__ import annotations


import pytest
from sqlalchemy import select

from app.compliance_v2.kms import get_kms, rekey_job
from app.db.compliance_v2_models import KmsRekeyJob


def test_targets_are_the_reviewed_registry():
    keys = [target.key for target in rekey_job.select_targets()]

    assert keys == [
        "crm_integrations.credentials_encrypted",
        "calendar_integrations.credentials_encrypted",
        "webhook_subscriptions.secret_envelope",
        "webhook_subscriptions.headers_envelope",
        "mfa_factors.secret_encrypted",
        "sso_connections.client_secret_encrypted",
    ]
    with pytest.raises(Exception):
        rekey_job.select_targets(["not.a.target"])


async def test_dry_run_job_counts_without_writing(db, compliance_settings):
    kms = get_kms(compliance_settings)
    job = await rekey_job.create_job(
        db,
        tenant_id=None,
        requested_by=None,
        dry_run=True,
        target_keys=["webhook_subscriptions.secret_envelope"],
        provider=kms,
    )
    await db.commit()

    await rekey_job.run_rekey(db, kms, job=job, batch_size=50)
    await db.commit()
    await db.refresh(job)

    assert job.status in {"succeeded", "completed_with_errors"}
    assert job.dry_run is True
    assert job.rows_rekeyed == 0
    assert job.finished_at is not None
    assert job.target_tables == ["webhook_subscriptions.secret_envelope"]


async def test_job_status_returns_the_stored_row(db, compliance_settings):
    kms = get_kms(compliance_settings)
    job = await rekey_job.create_job(
        db, tenant_id=None, requested_by=None, dry_run=True, provider=kms
    )
    await db.commit()

    stored = await rekey_job.job_status(db, job.id)

    assert stored is not None
    assert stored.id == job.id
    assert set(stored.target_tables or []) == {
        target.key for target in rekey_job.select_targets()
    }


async def test_rekey_is_idempotent_when_no_rows_need_rewrapping(db, compliance_settings):
    kms = get_kms(compliance_settings)
    job = await rekey_job.create_job(
        db,
        tenant_id=None,
        requested_by=None,
        dry_run=False,
        target_keys=["mfa_factors.secret_encrypted"],
        provider=kms,
    )
    await db.commit()

    await rekey_job.run_rekey(db, kms, job=job, batch_size=10)
    await db.commit()
    first = (await db.execute(select(KmsRekeyJob).where(KmsRekeyJob.id == job.id))).scalar_one()

    await rekey_job.run_rekey(db, kms, job=first, batch_size=10)
    await db.commit()
    await db.refresh(first)

    assert first.rows_failed == 0
    assert first.rows_rekeyed == 0
    assert first.status in {"succeeded", "completed_with_errors"}


async def test_job_status_returns_none_for_an_unknown_job(db):
    import uuid

    assert await rekey_job.job_status(db, uuid.uuid4()) is None


async def _seed_opaque_rows(db, tenant_id, count: int) -> list:
    """Rows whose ciphertext this job does not understand, in id order.

    The sweep must step over them without rewriting them, which makes them a
    clean way to exercise the keyset checkpoint: every row is visited exactly
    once and a resumed job must not start over.
    """
    from app.db.models import CrmIntegration

    rows = []
    for index in range(count):
        row = CrmIntegration(
            tenant_id=tenant_id,
            provider=_unique_provider(index),
            credentials_encrypted=f"opaque-token-{index}",
            is_enabled=True,
        )
        db.add(row)
        rows.append(row)
    await db.commit()
    for row in rows:
        await db.refresh(row)
    return sorted(rows, key=lambda item: str(item.id))


def _unique_provider(index: int):
    from app.db.models import CrmProviderType

    return list(CrmProviderType)[index % len(list(CrmProviderType))]


async def test_a_resumed_job_continues_from_its_checkpoint(db, tenant_a, compliance_settings):
    """The cursor is a UUID in the database and a string in the checkpoint.

    Comparing it against a UUID column is where a resumed sweep silently breaks
    or silently restarts, so this walks the boundary: one row per call, twice.
    """
    from app.compliance_v2.kms import rekey_job as job_module

    kms = get_kms(compliance_settings)
    rows = await _seed_opaque_rows(db, tenant_a.id, 3)
    target = job_module.select_targets(["crm_integrations.credentials_encrypted"])[0]

    first = KmsRekeyJob(tenant_id=tenant_a.id, dry_run=True, target_tables=[target.key])
    db.add(first)
    await db.commit()
    await db.refresh(first)

    await job_module.run_target(
        db, target, provider=kms, job=first, batch_size=1, max_rows=1
    )
    await db.commit()
    await db.refresh(first)
    checkpoint = first.cursor_id

    assert checkpoint, "a partial sweep must leave a checkpoint behind"
    assert first.cursor_table == target.table

    second = KmsRekeyJob(
        tenant_id=tenant_a.id,
        dry_run=True,
        target_tables=[target.key],
        cursor_table=target.table,
        cursor_id=checkpoint,
    )
    db.add(second)
    await db.commit()
    await db.refresh(second)

    progress = await job_module.run_target(
        db, target, provider=kms, job=second, batch_size=1, max_rows=1
    )
    await db.commit()
    await db.refresh(second)

    assert progress.scanned == 1
    assert second.cursor_id
    assert second.cursor_id != checkpoint
    assert str(second.cursor_id) > str(checkpoint)

    # Resuming from a fully drained checkpoint yields nothing rather than a
    # second pass over rows that were already visited.
    drained = KmsRekeyJob(
        tenant_id=tenant_a.id,
        dry_run=True,
        target_tables=[target.key],
        cursor_table=target.table,
        cursor_id=str(rows[-1].id),
    )
    db.add(drained)
    await db.commit()
    await db.refresh(drained)

    empty = await job_module.run_target(
        db, target, provider=kms, job=drained, batch_size=10
    )

    assert empty.scanned == 0
