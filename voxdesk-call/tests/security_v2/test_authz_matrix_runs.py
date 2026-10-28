"""Pentest-fix regression harness: the OpenAPI-driven authorization matrix.

Every mounted route is classified by the dependency it declares. The
matrix must find no route that mutates state while depending on nothing
more than an authenticated caller, and every route reachable without a
credential must be on the reviewed allowlist.
"""

from __future__ import annotations

from fastapi import Depends, FastAPI

from security_harness import authz_matrix, models



def _names(report: models.HarnessReport) -> set[str]:
    return {result.name for result in report.results}


def test_authz_matrix_reports_no_failures(app):
    report = authz_matrix.run(app)

    assert report.status in {models.PASS, models.REVIEW}
    assert report.failures() == []
    assert {"authz.inventory", "authz.public_allowlist", "authz.tenant_path_requires_auth"} <= _names(
        report
    )


def test_every_public_route_is_on_the_reviewed_allowlist(app):
    infos = authz_matrix.route_infos(app)

    assert authz_matrix.unknown_public_routes(infos) == []
    assert len(authz_matrix.PUBLIC_ROUTES) >= 73
    assert "/api/platform/tls/ask" in authz_matrix.PUBLIC_ROUTES
    assert "/api/whitelabel/public/" in authz_matrix.PUBLIC_PREFIXES
    report = authz_matrix.run(app)
    allowlist = next(result for result in report.results if result.name == "authz.public_allowlist")
    assert allowlist.status == models.PASS, allowlist.detail


def test_the_compliance_routes_are_authenticated_and_permissioned(app):
    infos = authz_matrix.route_infos(app)
    compliance = [
        info
        for info in infos
        if "/compliance/" in info.path and info.path.startswith("/api/tenants/")
    ]

    assert compliance, "the Batch 03 routes must be mounted"
    assert all(info.authenticated for info in compliance)
    assert [info.path for info in compliance if not info.permission] == []


def test_a_route_added_without_a_permission_is_reported_not_ignored():
    """A synthetic app: the matrix must notice an authenticated-only mutation."""
    from app.auth.dependencies import get_context

    probe_app = FastAPI()

    @probe_app.post("/api/tenants/{tenant_id}/probe/thing")
    async def probe_create(tenant_id: str, ctx=Depends(get_context)):  # noqa: ANN001, ARG001
        return {"ok": True}

    report = authz_matrix.run(probe_app)

    assert report.status in {models.REVIEW, models.FAIL}
    assert authz_matrix.unprotected_mutations(authz_matrix.route_infos(probe_app))
