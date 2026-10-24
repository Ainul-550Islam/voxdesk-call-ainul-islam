"""Data residency: object-key grammar, tenant pins and the cross-region guard.

The key grammar is the part that actually enforces residency: every stored
object carries its region in the key, so a read can be refused by inspecting a
string instead of trusting the caller. These tests pin down that grammar, the
pin lifecycle (configured → pinned only with proof), and the refusal path.
"""

from __future__ import annotations

import uuid

import pytest

from app.compliance_v2.residency import (
    CrossRegionKey,
    CrossRegionViolation,
    StorageKeyError,
    assert_region,
    build_key,
    check_region,
    classify,
    enforce_region,
    parse,
    pin_document,
    region_catalogue,
    region_of,
    resolve_pin,
    rewrite_to_region,
)


def test_build_and_parse_a_regional_object_key(compliance_settings):
    tenant = uuid.uuid4()
    key = build_key(
        region="us-east-1",
        kind="recordings",
        tenant_id=tenant,
        name="call-123.mp3",
    )

    parsed = parse(key)

    assert parsed.region == "us-east-1"
    assert parsed.kind == "recordings"
    assert parsed.tenant_id == str(tenant)
    assert parsed.name == "call-123.mp3"
    assert region_of(key) == "us-east-1"
    assert classify(key) == "regional"


def test_every_declared_kind_builds(compliance_settings):
    from app.compliance_v2.residency import KINDS

    for kind in sorted(KINDS):
        key = build_key(region="eu-west-1", kind=kind, tenant_id=uuid.uuid4(), name="x.bin")
        assert region_of(key) == "eu-west-1"


def test_a_legacy_key_is_classified_rather_than_rejected_silently():
    assert classify("recordings/call-1.mp3") == "legacy"
    assert classify("") == "invalid"


def test_region_label_is_validated(compliance_settings):
    from app.compliance_v2.residency import RegionError

    with pytest.raises(RegionError):
        build_key(region="US East 1", kind="recordings", tenant_id=uuid.uuid4(), name="x")
    with pytest.raises(StorageKeyError):
        build_key(region="us-east-1", kind="not-a-kind", tenant_id=uuid.uuid4(), name="x")


def test_assert_region_refuses_a_key_from_another_region(compliance_settings):
    tenant = uuid.uuid4()
    key = build_key(region="eu-west-1", kind="exports", tenant_id=tenant, name="export.zip")

    with pytest.raises(CrossRegionKey) as excinfo:
        assert_region(key, expected_region="us-east-1", tenant_id=tenant)

    assert "eu-west-1" in str(excinfo.value)


def test_assert_region_refuses_another_tenants_key(compliance_settings):
    key = build_key(region="us-east-1", kind="exports", tenant_id=uuid.uuid4(), name="x.zip")

    with pytest.raises(CrossRegionKey):
        assert_region(key, expected_region="us-east-1", tenant_id=uuid.uuid4())


def test_rewrite_moves_a_key_between_regions(compliance_settings):
    tenant = uuid.uuid4()
    key = build_key(region="us-east-1", kind="transcripts", tenant_id=tenant, name="t.jsonl")

    moved = rewrite_to_region(key, region="eu-west-1")

    assert region_of(moved) == "eu-west-1"
    assert parse(moved).name == "t.jsonl"
    assert parse(moved).tenant_id == str(tenant)


def test_region_catalogue_reports_what_is_configured(compliance_settings):
    catalogue = region_catalogue()

    assert catalogue["configured"] is True
    assert catalogue["default_region"] == "us-east-1"
    assert sorted(region["code"] for region in catalogue["regions"]) == ["eu-west-1", "us-east-1"]
    assert catalogue["regions"][0]["storage_prefix"].startswith("regions/")


def test_region_catalogue_is_not_configured_without_data_regions(monkeypatch):
    from app.compliance_v2.residency import regions as regions_module
    from app.compliance_v2.settings import reset_compliance_v2_settings

    monkeypatch.setenv("DATA_REGIONS", "")
    monkeypatch.setenv("DEFAULT_REGION", "")
    reset_compliance_v2_settings()
    regions_module.registry.cache_clear()
    try:
        catalogue = region_catalogue()
        assert catalogue["configured"] is False
        assert catalogue["regions"] == []
        assert catalogue["default_region"] == ""
    finally:
        reset_compliance_v2_settings()
        regions_module.registry.cache_clear()


async def test_tenant_without_a_pin_falls_back_to_the_default_region(db, tenant_a, compliance_settings):
    document = await pin_document(db, tenant_a.id)

    assert document["status"] == "configured"
    assert document["region"] == "us-east-1"
    assert document["source"] == "default"
    assert document["verified"] is False
    assert await resolve_pin(db, tenant_a.id) is None


async def test_a_verified_intent_becomes_the_effective_region(
    db, tenant_a, governance_scope, compliance_settings
):
    from app.governance.models import ResidencyIntent

    db.add(
        ResidencyIntent(
            tenant_id=tenant_a.id,
            organization_id=governance_scope.organization_id,
            requested_region="eu-west-1",
            status="verified",
            version=1,
            physical_residency_proven=True,
            verification_metadata={"receipt": "VER-1"},
        )
    )
    await db.commit()

    document = await pin_document(db, tenant_a.id)
    record = await resolve_pin(db, tenant_a.id)

    assert document["status"] == "configured"
    assert document["region"] == "eu-west-1"
    assert document["source"] == "intent"
    assert document["verified"] is True
    assert record is not None and record.region == "eu-west-1"


async def test_check_region_allows_a_matching_request(
    db, tenant_a, governance_scope, compliance_settings
):
    from app.governance.models import ResidencyIntent

    db.add(
        ResidencyIntent(
            tenant_id=tenant_a.id,
            organization_id=governance_scope.organization_id,
            requested_region="eu-west-1",
            status="verified",
            version=1,
            physical_residency_proven=True,
            verification_metadata={},
        )
    )
    await db.commit()

    allowed = await check_region(db, tenant_id=tenant_a.id, requested_region="eu-west-1")
    blocked = await check_region(db, tenant_id=tenant_a.id, requested_region="us-east-1")

    assert allowed.allowed is True
    assert blocked.allowed is False
    assert blocked.pinned_region == "eu-west-1"
    assert blocked.reason


async def test_enforce_region_requires_a_known_region(db, tenant_a, compliance_settings):
    with pytest.raises(CrossRegionViolation):
        await enforce_region(
            db,
            tenant_id=tenant_a.id,
            requested_region="mars-north-1",
            operation="write",
        )


async def test_enforce_region_permits_the_pinned_region(db, tenant_a, compliance_settings):
    # No verified intent: the tenant sits on the deployment default, and the
    # write to that region is allowed.


    decision = await enforce_region(
        db,
        tenant_id=tenant_a.id,
        requested_region="us-east-1",
        operation="write_export",
        resource="exports/abc",
    )

    assert decision.allowed is True
    assert decision.pinned_region == "us-east-1"
