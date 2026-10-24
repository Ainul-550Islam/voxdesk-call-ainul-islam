"""Cross-tenant fuzzing against the real application.

The probe takes every authenticated, tenant-scoped read route the app mounts,
replaces ``{tenant_id}`` with *another* tenant's id, sends the request with
tenant A's credential, and judges the answer. A ``200`` is a leak; a ``401``,
``403``, ``404`` or ``410`` is the only acceptable outcome.

The suite runs in both directions: against the shipped application, where it must
find nothing, and against a deliberately leaky probe app, where it must catch the
leak — a harness that cannot fail proves nothing.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from fastapi import Depends, FastAPI

from security_harness import cross_tenant_fuzz, models
from tests.conftest import auth_headers


def get_context() -> dict[str, str]:
    """A stand-in for the real auth dependency.

    The probe apps below are synthetic: they exist to prove the harness *can*
    fail, so their guard must look authenticated to the route classifier without
    reaching a database.
    """
    return {"tenant_id": "synthetic"}


async def test_the_real_application_refuses_every_foreign_tenant_read(
    client, app, tenant_a, tenant_b, owner_a
):
    headers = await auth_headers(client, owner_a)

    report = await cross_tenant_fuzz.run(
        app, client, foreign_tenant_id=str(tenant_b.id), headers=headers
    )

    assert report.failures() == [], [result.detail for result in report.failures()]
    assert report.status == models.PASS
    names = {result.name for result in report.results}
    assert "cross_tenant.foreign_read" in names


async def test_tenant_a_cannot_read_tenant_b_through_the_tenant_path(client, tenant_b, owner_a):
    headers = await auth_headers(client, owner_a)

    response = await client.get(
        f"/api/tenants/{tenant_b.id}/compliance/audit/verify", headers=headers
    )

    assert response.status_code in {403, 404}, response.text
    assert str(tenant_b.id) not in response.text


async def test_the_probe_list_is_generated_from_the_mounted_application(app):
    probes = cross_tenant_fuzz.build_probes(app)

    assert probes, "the harness found no tenant-scoped read route to probe"
    assert all("{tenant_id}" in probe.path for probe in probes)
    assert all(probe.method == "GET" for probe in probes)


async def test_a_leaky_route_is_reported_as_a_failure():
    """Negative control: the harness must fail on a route that ignores the tenant."""
    probe_app = FastAPI()

    @probe_app.get("/api/tenants/{tenant_id}/leak")
    async def leak(tenant_id: str, ctx=Depends(get_context)):  # noqa: ANN001, ARG001
        return {"tenant_id": tenant_id, "records": ["someone else's data"]}

    transport = httpx.ASGITransport(app=probe_app)
    foreign = str(uuid.uuid4())
    async with httpx.AsyncClient(transport=transport, base_url="http://probe") as probe_client:
        report = await cross_tenant_fuzz.run(
            probe_app,
            probe_client,
            foreign_tenant_id=foreign,
            headers={"X-Probe": "unused"},
        )

    assert report.status == models.FAIL
    assert any(result.name == "cross_tenant.foreign_read" for result in report.failures())


async def test_an_identifier_echoed_in_the_body_is_reported(app):
    """A refusal that still echoes the foreign id is a leak of a different kind."""
    probe_app = FastAPI()

    @probe_app.get("/api/tenants/{tenant_id}/echo")
    async def echo(tenant_id: str, ctx=Depends(get_context)):  # noqa: ANN001, ARG001
        return {"error": f"tenant {tenant_id} not found"}

    transport = httpx.ASGITransport(app=probe_app)
    foreign = str(uuid.uuid4())
    async with httpx.AsyncClient(transport=transport, base_url="http://probe") as probe_client:
        report = await cross_tenant_fuzz.run(
            probe_app, probe_client, foreign_tenant_id=foreign
        )

    assert any(result.name == "cross_tenant.identifier_leak" for result in report.failures())


async def test_no_client_is_a_skip_not_a_pass(app):
    report = await cross_tenant_fuzz.run(app, None, foreign_tenant_id=str(uuid.uuid4()))

    assert report.status == models.SKIP
    assert report.results, "a skip must carry a reason"


@pytest.mark.parametrize("status", [401, 403, 404, 410])
def test_the_refusal_statuses_are_the_ones_that_prove_isolation(status):
    assert status in cross_tenant_fuzz.REFUSAL_STATUSES
