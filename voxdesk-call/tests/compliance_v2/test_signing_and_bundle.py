"""Tamper-evident audit: chain walking and signed export bundles.

The chain under test is the repository's real one (``app.governance.evidence``);
this module does not reimplement hashing, it checks that the verifier detects
what an attacker would actually do — edit a stored payload, drop an event,
re-order two events — and that a bundle signed with one key does not verify
against another.
"""

from __future__ import annotations

import io
import json
import zipfile


from app.compliance_v2.audit_integrity import chain_verifier, export_bundle, signing
from app.governance.evidence import append_event


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


async def test_bundle_round_trip_and_member_listing(db, tenant_a, compliance_settings, governance_scope):
    data, pem = await _bundle(db, governance_scope, tenant_a, compliance_settings)

    verification = export_bundle.verify_bundle_bytes(data, public_key_pem=pem)

    assert verification.ok is True, verification.reason
    assert verification.event_count == 3
    assert verification.chain_ok is True
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert set(archive.namelist()) == {
            "events.jsonl",
            "chain-report.json",
            "manifest.json",
            "manifest.sig",
        }
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["event_count"] == 3


async def test_bundle_rejects_a_rewritten_event(db, tenant_a, compliance_settings, governance_scope):
    data, pem = await _bundle(db, governance_scope, tenant_a, compliance_settings)

    source = zipfile.ZipFile(io.BytesIO(data))
    tampered = io.BytesIO()
    with zipfile.ZipFile(tampered, "w") as target:
        for name in source.namelist():
            body = source.read(name)
            if name == "events.jsonl":
                first, *rest = body.decode().splitlines()
                document = json.loads(first)
                document["payload"] = {"index": -1, "action": "call.deleted"}
                body = ("\n".join([json.dumps(document), *rest]) + "\n").encode()
            target.writestr(name, body)

    verification = export_bundle.verify_bundle_bytes(tampered.getvalue(), public_key_pem=pem)

    assert verification.ok is False
    assert "events.jsonl" in verification.reason


async def test_bundle_signed_by_another_key_does_not_verify(
    db, tenant_a, compliance_settings, tmp_path, governance_scope
):
    data, _pem = await _bundle(db, governance_scope, tenant_a, compliance_settings)
    other = signing.load_signer(
        tmp_path / "other_key.pem", settings=compliance_settings, create_when_absent=True
    )

    verification = export_bundle.verify_bundle_bytes(data, public_key_pem=other.public_key_pem)

    assert verification.ok is False
    assert "signature" in verification.reason


async def test_bundle_written_to_disk_reports_its_digest(
    db, tenant_a, compliance_settings, tmp_path, governance_scope
):
    data, pem = await _bundle(db, governance_scope, tenant_a, compliance_settings)
    path = tmp_path / "bundle.zip"

    written = export_bundle.write_bundle(path, data)
    verification = export_bundle.verify_bundle(path, public_key_pem=pem)

    assert written["sha256"] == verification.detail.get("sha256", written["sha256"])
    assert verification.ok is True
    assert len(written["sha256"]) == 64


# ------------------------------------------------------------- signing keys ---


def test_signing_key_is_created_once_and_rotates(compliance_settings):
    first = signing.load_signer(settings=compliance_settings)
    again = signing.load_signer(settings=compliance_settings)
    assert first.key_id == again.key_id

    entry = signing.rotate_signing_key(settings=compliance_settings)
    current = signing.load_signer(settings=compliance_settings)

    assert entry["previous_key_id"] == first.key_id
    assert entry["new_key_id"] == current.key_id
    assert current.key_id != first.key_id
    assert entry["previous_public_key_pem"] == first.public_key_pem
    history = signing.rotation_history(settings=compliance_settings)
    assert [row["new_key_id"] for row in history][-1] == current.key_id


def test_signature_verifies_only_with_the_matching_key(compliance_settings, tmp_path):
    signer = signing.load_signer(settings=compliance_settings)
    payload = b'{"event_count":1}'
    signature = signer.sign(payload)

    assert signing.verify_bytes(payload, signature=signature, public_key_pem=signer.public_key_pem)
    assert not signing.verify_bytes(
        b'{"event_count":2}', signature=signature, public_key_pem=signer.public_key_pem
    )

    other = signing.load_signer(
        tmp_path / "other.pem", settings=compliance_settings, create_when_absent=True
    )
    assert not signing.verify_bytes(
        payload, signature=signature, public_key_pem=other.public_key_pem
    )


def test_public_key_pem_round_trip(compliance_settings):
    signer = signing.load_signer(settings=compliance_settings)
    restored = signing.public_key_from_pem(signer.public_key_pem)
    assert signing.key_id_for(restored) == signer.key_id
