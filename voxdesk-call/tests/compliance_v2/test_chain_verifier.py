"""Tamper-evident audit: chain walking and signed export bundles.

The chain under test is the repository's real one (``app.governance.evidence``);
this module does not reimplement hashing, it checks that the verifier detects
what an attacker would actually do — edit a stored payload, drop an event,
re-order two events — and that a bundle signed with one key does not verify
against another.
"""

from __future__ import annotations


from sqlalchemy import update

from app.compliance_v2.audit_integrity import chain_verifier, export_bundle, signing
from app.governance.evidence import append_event
from app.governance.models import EvidenceEvent


async def _append(db, scope, count: int) -> list:
    events = []
    for index in range(count):
        events.append(
            await append_event(
                db,
                scope,
                event_type="governance_evidence_recorded",
                payload={"index": index, "action": "call.completed", "tenant": str(scope.tenant_id)},
                actor_user_id=None,
                actor_type="system",
                correlation_id=f"corr-{index}",
                commit=False,
            )
        )
    await db.commit()
    return events


async def _bundle(db, scope, tenant, compliance_settings, count: int = 3) -> tuple[bytes, str]:
    events = await _append(db, scope, count)
    report = await chain_verifier.verify_chain(db, tenant_id=tenant.id)
    signer = signing.load_signer(settings=compliance_settings)
    request = export_bundle.BundleRequest(
        tenant_id=str(tenant.id),
        range_start=events[0].occurred_at,
        range_end=events[-1].occurred_at,
        export_id="export-1",
        chain_scope=report.chain_scope,
    )
    data = export_bundle.build_bundle(
        request=request,
        events=events,
        signer=signer,
        chain_report=chain_verifier.summarise(report),
    )
    return data, signer.public_key_pem


async def test_verify_chain_accepts_an_untouched_chain(db, tenant_a, governance_scope):
    await _append(db, governance_scope, 4)

    report = await chain_verifier.verify_chain(db, tenant_id=tenant_a.id)

    assert report.ok is True
    assert report.checked == 4
    assert report.issues == []
    assert report.head_sequence == report.checked
    assert len(report.head_hash) == 64


async def test_verify_chain_detects_an_edited_payload(db, tenant_a, governance_scope):
    await _append(db, governance_scope, 3)
    second = (
        await db.execute(
            EvidenceEvent.__table__.select()
            .where(EvidenceEvent.tenant_id == tenant_a.id)
            .order_by(EvidenceEvent.sequence)
        )
    ).mappings().all()[1]

    await db.execute(
        update(EvidenceEvent)
        .where(EvidenceEvent.id == second["id"])
        .values(payload={"index": 99, "action": "call.deleted"})
    )
    await db.commit()

    report = await chain_verifier.verify_chain(db, tenant_id=tenant_a.id)

    assert report.ok is False
    assert report.first_broken_sequence == second["sequence"]
    kinds = {issue.kind for issue in report.issues}
    assert "payload_hash_mismatch" in kinds or "hash_mismatch" in kinds


async def test_verify_chain_detects_a_deleted_event(db, tenant_a, governance_scope):
    await _append(db, governance_scope, 3)
    rows = (
        await db.execute(
            EvidenceEvent.__table__.select()
            .where(EvidenceEvent.tenant_id == tenant_a.id)
            .order_by(EvidenceEvent.sequence)
        )
    ).mappings().all()
    middle = rows[1]
    await db.execute(EvidenceEvent.__table__.delete().where(EvidenceEvent.id == middle["id"]))
    await db.commit()

    report = await chain_verifier.verify_chain(db, tenant_id=tenant_a.id)

    assert report.ok is False
    # The row *after* the hole is where the linkage first fails, so that is the
    # sequence the report names; the missing row itself no longer exists.
    assert report.first_broken_sequence == middle["sequence"] + 1
    kinds = {issue.kind for issue in report.issues}
    assert kinds & {"sequence_gap", "previous_hash_mismatch"}


async def test_verify_chain_rejects_a_wrong_expected_head(db, tenant_a, governance_scope):
    await _append(db, governance_scope, 2)

    report = await chain_verifier.verify_chain(
        db, tenant_id=tenant_a.id, expected_head_hash="0" * 64
    )

    assert report.ok is False
    assert any(issue.kind == "head_mismatch" for issue in report.issues)


async def test_verify_chain_is_tenant_scoped(db, tenant_a, governance_scope):
    from app.db.models import Organization
    from app.governance.context import GovernanceScope
    from tests.conftest import make_tenant

    other = await make_tenant(db, "Tenant B")
    other_org = await db.get(Organization, other.organization_id)
    other_scope = GovernanceScope(tenant=other, organization=other_org)

    await _append(db, governance_scope, 2)
    await _append(db, other_scope, 3)

    report = await chain_verifier.verify_chain(db, tenant_id=tenant_a.id)

    assert report.checked == 2
    assert report.chain_scope == f"tenant:{tenant_a.id}"
    assert report.ok is True
    other_report = await chain_verifier.verify_chain(db, tenant_id=other.id)
    assert other_report.checked == 3
    assert other_report.head_hash != report.head_hash


# ----------------------------------------------------------- signed bundles ---


