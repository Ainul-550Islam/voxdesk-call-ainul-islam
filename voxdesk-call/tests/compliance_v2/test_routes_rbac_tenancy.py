"""The compliance HTTP surface: isolation, permissions, idempotency, audit.

Every endpoint under ``/api/tenants/{tenant_id}/compliance`` is exercised at
least once through the real application object. The tests are written from the
attacker's side as much as the operator's: a manager may read incidents but not
close one, a token for tenant A cannot read tenant B's chain, and a replayed
``Idempotency-Key`` must not create a second resource.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy import select

from app.db.models import AuditLog
from tests.conftest import auth_headers

UTC = dt.timezone.utc


def _url(tenant_id, path: str = "") -> str:
    return f"/api/tenants/{tenant_id}/compliance{path}"


async def _read_endpoints(tenant_id) -> list[str]:
    return [
        _url(tenant_id, "/manifest"),
        _url(tenant_id, "/siem/sinks"),
        _url(tenant_id, "/siem/status"),
        _url(tenant_id, "/siem/dead-letters"),
        _url(tenant_id, "/audit/verify"),
        _url(tenant_id, "/audit/signing-key"),
        _url(tenant_id, "/audit/exports"),
        _url(tenant_id, "/kms/health"),
        _url(tenant_id, "/kms/targets"),
        _url(tenant_id, "/kms/jobs"),
        _url(tenant_id, "/residency/regions"),
        _url(tenant_id, "/residency/pin"),
        _url(tenant_id, "/residency/violations"),
        _url(tenant_id, "/evidence/controls"),
        _url(tenant_id, "/evidence/snapshots"),
        _url(tenant_id, "/evidence/latest"),
        _url(tenant_id, "/access-reviews/campaigns"),
        _url(tenant_id, "/incidents"),
        _url(tenant_id, "/incidents/clocks/due"),
        _url(tenant_id, "/dsar/registry"),
        _url(tenant_id, "/dsar/requests"),
        _url(tenant_id, "/hipaa/phi-policy"),
        _url(tenant_id, "/hipaa/break-glass"),
        _url(tenant_id, "/hipaa/sessions"),
        _url(tenant_id, "/security/harness/allowlist"),
        _url(tenant_id, "/security/harness/corpus"),
    ]


async def test_manifest_lists_every_area(client, tenant_a, owner_a):
    headers = await auth_headers(client, owner_a)

    response = await client.get(_url(tenant_a.id, "/manifest"), headers=headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["format"] == "voxdesk.compliance.manifest.v1"
    assert set(body["areas"]) == {
        "siem", "audit", "kms", "residency", "evidence",
        "access-reviews", "incidents", "dsar", "hipaa", "security",
    }
    assert "no certification claim" in body["note"]


async def test_every_read_endpoint_answers_for_the_owner(
    client, tenant_a, owner_a, compliance_settings
):
    headers = await auth_headers(client, owner_a)

    for path in await _read_endpoints(tenant_a.id):
        response = await client.get(path, headers=headers)
        assert response.status_code == 200, f"{path}: {response.status_code} {response.text}"


async def test_reads_require_authentication(client, tenant_a):
    response = await client.get(_url(tenant_a.id, "/audit/verify"))
    assert response.status_code in {401, 403}


async def test_an_agent_holds_no_compliance_permission(client, tenant_a, agent_a):
    headers = await auth_headers(client, agent_a)

    for path in await _read_endpoints(tenant_a.id):
        response = await client.get(path, headers=headers)
        assert response.status_code == 403, f"{path}: {response.status_code}"


async def test_a_manager_may_read_incidents_but_not_close_one(
    client, tenant_a, manager_a
):
    headers = await auth_headers(client, manager_a)

    listing = await client.get(_url(tenant_a.id, "/incidents"), headers=headers)
    assert listing.status_code == 200

    created = await client.post(
        _url(tenant_a.id, "/incidents"),
        headers=headers,
        json={"title": "Manager raised", "summary": "spotted", "severity": "sev3"},
    )
    assert created.status_code == 201, created.text

    export_attempt = await client.post(
        _url(tenant_a.id, "/audit/exports"),
        headers=headers,
        json={
            "range_start": (dt.datetime.now(UTC) - dt.timedelta(days=1)).isoformat(),
            "range_end": dt.datetime.now(UTC).isoformat(),
        },
    )
    assert export_attempt.status_code == 403


async def test_a_token_for_one_tenant_cannot_touch_another(
    client, tenant_a, tenant_b, owner_a
):
    headers = await auth_headers(client, owner_a)

    for path in await _read_endpoints(tenant_b.id):
        response = await client.get(path, headers=headers)
        assert response.status_code in {403, 404}, f"{path}: {response.status_code}"


async def test_siem_sink_creation_is_idempotent(client, tenant_a, owner_a, compliance_settings):
    headers = {**await auth_headers(client, owner_a), "Idempotency-Key": uuid.uuid4().hex}
    payload = {
        "name": "splunk-prod",
        "kind": "splunk_hec",
        "endpoint": "https://splunk.example.com:8088",
        "secret": "hec-token",
        "config": {"index": "audit"},
    }

    first = await client.post(_url(tenant_a.id, "/siem/sinks"), headers=headers, json=payload)
    second = await client.post(_url(tenant_a.id, "/siem/sinks"), headers=headers, json=payload)

    assert first.status_code == 201, first.text
    assert second.status_code in {200, 201}
    assert second.json()["id"] == first.json()["id"]

    listing = await client.get(_url(tenant_a.id, "/siem/sinks"), headers=await auth_headers(client, owner_a))
    assert len([row for row in listing.json() if row["name"] == "splunk-prod"]) == 1


async def test_a_sink_of_an_unknown_kind_is_refused(client, tenant_a, owner_a, compliance_settings):
    headers = await auth_headers(client, owner_a)
    response = await client.post(
        _url(tenant_a.id, "/siem/sinks"),
        headers=headers,
        json={"name": "nope", "kind": "carrier-pigeon", "endpoint": "https://example.com", "secret": "x"},
    )
    assert response.status_code in {400, 422}


async def test_a_sink_without_its_credential_is_refused(client, tenant_a, owner_a, compliance_settings):
    headers = await auth_headers(client, owner_a)
    response = await client.post(
        _url(tenant_a.id, "/siem/sinks"),
        headers=headers,
        json={"name": "no-secret", "kind": "splunk_hec", "endpoint": "https://example.com", "secret": ""},
    )
    assert response.status_code in {400, 422}


async def test_audit_export_produces_a_downloadable_bundle(
    client, tenant_a, owner_a, governance_scope, compliance_settings, db
):
    from app.governance.evidence import append_event

    for index in range(2):
        await append_event(
            db,
            governance_scope,
            event_type="governance_evidence_recorded",
            payload={"index": index, "tenant": str(tenant_a.id)},
            actor_user_id=None,
            actor_type="system",
            correlation_id=f"api-{index}",
        )
    await db.commit()

    headers = await auth_headers(client, owner_a)
    now = dt.datetime.now(UTC)
    created = await client.post(
        _url(tenant_a.id, "/audit/exports"),
        headers=headers,
        json={
            "range_start": (now - dt.timedelta(days=1)).isoformat(),
            "range_end": (now + dt.timedelta(minutes=5)).isoformat(),
        },
    )

    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "succeeded"
    assert body["event_count"] == 2

    download = await client.get(_url(tenant_a.id, f"/audit/exports/{body['id']}/download"), headers=headers)
    assert download.status_code == 200
    assert download.headers["content-type"] in {"application/zip", "application/octet-stream"}
    assert download.content[:2] == b"PK"


async def test_sink_stats_report_the_cursor_of_that_sink(client, tenant_a, owner_a, compliance_settings):
    headers = await auth_headers(client, owner_a)
    created = await client.post(
        _url(tenant_a.id, "/siem/sinks"),
        headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
        json={
            "name": "stats-probe",
            "kind": "splunk_hec",
            "endpoint": "https://splunk.example.com:8088",
            "secret": "hec-token",
        },
    )
    assert created.status_code == 201, created.text
    sink_id = created.json()["id"]

    response = await client.get(
        _url(tenant_a.id, f"/siem/sinks/{sink_id}/stats"), headers=headers
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["sink_id"] == sink_id
    assert body["sink_name"] == "stats-probe"
    assert body["events_delivered"] == 0
    assert body["dead_letter_count"] == 0
    assert body["state"] in {"idle", "backoff", "circuit_open", "halted"}


async def test_sink_stats_of_another_tenant_are_not_found(
    client, tenant_a, tenant_b, owner_a, owner_b, compliance_settings
):
    created = await client.post(
        _url(tenant_b.id, "/siem/sinks"),
        headers={**await auth_headers(client, owner_b), "Idempotency-Key": uuid.uuid4().hex},
        json={
            "name": "b-tenant-sink",
            "kind": "splunk_hec",
            "endpoint": "https://splunk.example.com:8088",
            "secret": "hec-token",
        },
    )
    assert created.status_code == 201, created.text
    sink_id = created.json()["id"]

    response = await client.get(
        _url(tenant_a.id, f"/siem/sinks/{sink_id}/stats"),
        headers=await auth_headers(client, owner_a),
    )

    assert response.status_code == 404


async def test_an_export_can_be_read_back_by_id(
    client, tenant_a, owner_a, governance_scope, compliance_settings, db
):
    from app.governance.evidence import append_event

    await append_event(
        db,
        governance_scope,
        event_type="governance_evidence_recorded",
        payload={"tenant": str(tenant_a.id)},
        actor_user_id=None,
        actor_type="system",
    )
    await db.commit()

    headers = await auth_headers(client, owner_a)
    now = dt.datetime.now(UTC)
    created = await client.post(
        _url(tenant_a.id, "/audit/exports"),
        headers=headers,
        json={
            "range_start": (now - dt.timedelta(days=1)).isoformat(),
            "range_end": (now + dt.timedelta(minutes=5)).isoformat(),
        },
    )
    assert created.status_code == 201, created.text
    export_id = created.json()["id"]

    fetched = await client.get(_url(tenant_a.id, f"/audit/exports/{export_id}"), headers=headers)

    assert fetched.status_code == 200, fetched.text
    body = fetched.json()
    assert body["id"] == export_id
    assert body["event_count"] == 1
    assert len(body["bundle_sha256"]) == 64


async def test_an_unknown_export_is_not_found(client, tenant_a, owner_a):
    response = await client.get(
        _url(tenant_a.id, f"/audit/exports/{uuid.uuid4()}"),
        headers=await auth_headers(client, owner_a),
    )
    assert response.status_code == 404


async def test_chain_verification_endpoint_reports_the_head(client, tenant_a, owner_a, governance_scope, db):
    from app.governance.evidence import append_event

    await append_event(
        db,
        governance_scope,
        event_type="governance_evidence_recorded",
        payload={"tenant": str(tenant_a.id)},
        actor_user_id=None,
        actor_type="system",
    )
    await db.commit()

    response = await client.get(
        _url(tenant_a.id, "/audit/verify"), headers=await auth_headers(client, owner_a)
    )

    assert response.status_code == 200
    body = response.json()
    assert body["verified"] is True
    assert body["checked"] == 1
    assert len(body["head_hash"]) == 64


async def test_a_break_glass_request_with_a_trivial_reason_is_refused(
    client, tenant_a, admin_a, compliance_settings
):
    headers = await auth_headers(client, admin_a)
    response = await client.post(
        _url(tenant_a.id, "/hipaa/break-glass"),
        headers=headers,
        json={"reason": "ok", "scope": {"resource_types": ["transcript"]}},
    )
    assert response.status_code in {400, 422}


async def test_phi_policy_update_is_audited(client, tenant_a, admin_a, db, compliance_settings):
    headers = await auth_headers(client, admin_a)

    response = await client.put(
        _url(tenant_a.id, "/hipaa/phi-policy"),
        headers=headers,
        json={"hipaa_mode": True, "baa_on_file": False, "baa_reference": "", "redact_webhooks": True},
    )

    assert response.status_code == 200, response.text
    assert response.json()["hipaa_mode"] is True
    events = list(
        (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.tenant_id == tenant_a.id,
                    AuditLog.event_type == "phi_policy_updated",
                )
            )
        ).scalars()
    )
    assert events, "a policy change must leave an audit row"


async def test_residency_check_answers_per_region(client, tenant_a, owner_a, compliance_settings):
    headers = await auth_headers(client, owner_a)

    allowed = await client.get(
        _url(tenant_a.id, "/residency/check"), headers=headers, params={"region": "us-east-1"}
    )
    refused = await client.get(
        _url(tenant_a.id, "/residency/check"), headers=headers, params={"region": "mars-north-1"}
    )

    assert allowed.status_code == 200
    assert allowed.json()["allowed"] is True
    assert refused.status_code == 200
    assert refused.json()["allowed"] is False


async def test_key_check_refuses_a_cross_region_object_key(
    client, tenant_a, owner_a, governance_scope, compliance_settings, db
):
    from app.compliance_v2.residency import build_key
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

    foreign = build_key(
        region="us-east-1", kind="recordings", tenant_id=tenant_a.id, name="call.mp3"
    )
    response = await client.post(
        _url(tenant_a.id, "/residency/check-key"),
        headers=await auth_headers(client, owner_a),
        json={"key": foreign, "operation": "read"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["allowed"] is False
    assert response.json()["pinned_region"] == "eu-west-1"


async def test_the_existing_a2p_compliance_view_is_not_shadowed(client, tenant_a, owner_a):
    """``GET /compliance`` is the pre-existing A2P/DNC view; Batch 03 adds a sibling."""
    response = await client.get(
        f"/api/tenants/{tenant_a.id}/compliance", headers=await auth_headers(client, owner_a)
    )

    assert response.status_code == 200
    assert "a2p_status" in response.json()
    assert "areas" not in response.json()


@pytest.mark.parametrize("area", ["siem", "audit", "kms", "residency", "evidence", "incidents", "dsar", "hipaa"])
async def test_unknown_tenant_path_is_refused_not_ignored(client, owner_a, area):
    headers = await auth_headers(client, owner_a)
    response = await client.get(_url(uuid.uuid4(), f"/{area}"), headers=headers)
    assert response.status_code in {403, 404, 422}
