"""A machine credential may only read the compliance surfaces it was scoped for.

Deployment automation is the common case here: a nightly job holds one API key
and should be able to pull evidence snapshots without being able to read an
incident report, a subject's export, or the audit trail. The scope check is the
only thing standing between a leaked automation key and the whole compliance
surface, so each read endpoint is exercised with a key that lacks its scope.
"""

from __future__ import annotations

import pytest

from app.auth.identity import api_keys
from app.auth.permissions import Permission


def _url(tenant_id, path: str = "") -> str:
    return f"/api/tenants/{tenant_id}/compliance{path}"


async def _machine_key(db, tenant, actor, *, scopes: list[str]) -> dict[str, str]:
    issued = await api_keys.create_key(
        db,
        tenant_id=tenant.id,
        actor=actor,
        name="nightly evidence pull",
        scopes=scopes,
        owner_user_id=actor.id,
        commit=True,
    )
    return {"Authorization": f"Bearer {issued.token}"}


@pytest.mark.parametrize(
    ("path", "scope"),
    [
        ("/evidence/snapshots", Permission.COMPLIANCE_EVIDENCE_READ),
        ("/siem/sinks", Permission.COMPLIANCE_SIEM_READ),
        ("/residency/pin", Permission.COMPLIANCE_RESIDENCY_READ),
        ("/dsar/requests", Permission.COMPLIANCE_DSAR_READ),
        ("/incidents", Permission.COMPLIANCE_INCIDENT_READ),
    ],
)
async def test_a_key_without_the_scope_cannot_read_the_surface(
    client, db, tenant_a, owner_a, path, scope
):
    headers = await _machine_key(db, tenant_a, owner_a, scopes=[Permission.CALL_READ.value])

    response = await client.get(_url(tenant_a.id, path), headers=headers)

    assert response.status_code == 403, response.text


@pytest.mark.parametrize(
    ("path", "scope"),
    [
        ("/evidence/snapshots", Permission.COMPLIANCE_EVIDENCE_READ),
        ("/siem/sinks", Permission.COMPLIANCE_SIEM_READ),
        ("/residency/pin", Permission.COMPLIANCE_RESIDENCY_READ),
        ("/dsar/requests", Permission.COMPLIANCE_DSAR_READ),
        ("/incidents", Permission.COMPLIANCE_INCIDENT_READ),
    ],
)
async def test_a_key_holding_the_scope_can_read_it(client, db, tenant_a, owner_a, path, scope):
    headers = await _machine_key(db, tenant_a, owner_a, scopes=[scope.value])

    response = await client.get(_url(tenant_a.id, path), headers=headers)

    assert response.status_code == 200, response.text


async def test_a_scoped_key_cannot_reach_a_foreign_tenant(
    client, db, tenant_a, tenant_b, owner_a
):
    headers = await _machine_key(
        db, tenant_a, owner_a, scopes=[Permission.COMPLIANCE_EVIDENCE_READ.value]
    )

    response = await client.get(_url(tenant_b.id, "/evidence/snapshots"), headers=headers)

    assert response.status_code in {403, 404}, response.text
