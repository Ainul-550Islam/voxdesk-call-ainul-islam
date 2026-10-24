"""On-demand run of the security regression harness.

The suites that need no HTTP client are executed in the API process: the
authorisation matrix over the mounted routes, the SSRF corpus (static and
resolution), and the rate-limit boundaries. The header/CORS and cross-tenant
suites need a client bound to the application under test and a second tenant's
credential; running them from inside a request would mean the request acting as
its own client, so they are reported as SKIP here and run in CI, where
``python -m security_harness.runner --app`` executes all of them.

A SKIP in this response is therefore an honest "not run here", never a pass.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.compliance_v2.common import audit_action, scoped_tenant
from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.db.session import get_session

router = APIRouter(prefix="/security", tags=["compliance-v2-security"])


def _suite_payload(report: Any) -> dict[str, Any]:
    document = report.as_dict()
    return {
        "suite": document["suite"],
        "status": document["status"],
        "counts": document["counts"],
        "checks": [
            {"name": check["name"], "status": check["status"], "detail": check["detail"]}
            for check in document["checks"]
        ],
    }


@router.post("/harness/run")
async def run_harness(
    tenant_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_EVIDENCE_RUN)),
    session: AsyncSession = Depends(get_session),
):
    """Run the in-process suites against the live application object."""
    scoped_tenant(ctx, tenant_id)

    from fastapi import FastAPI

    from security_harness import authz_matrix, rate_limit_probe, ssrf_probe

    app: FastAPI | None = None
    # ``request.app`` is the application this request is being served by, which
    # is exactly what the route-level suites must inspect.
    app = request.app

    suites = {
        "authz_matrix": authz_matrix.run(app),
        "ssrf": ssrf_probe.run(),
        "ssrf_resolution": await ssrf_probe.run_resolution(),
        "rate_limits": rate_limit_probe.run(app),
    }
    statuses = {name: report.status for name, report in suites.items()}

    await audit_action(
        session,
        ctx=ctx,
        request=request,
        event_type="security_harness_run",
        resource_type="security_harness",
        resource_id=tenant_id,
        detail={"statuses": statuses},
    )
    await session.commit()
    return {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "suites": {name: _suite_payload(report) for name, report in suites.items()},
        "not_run_here": [
            {
                "suite": "cross_tenant",
                "reason": "needs a second tenant's credential; run in CI with --app",
            },
            {
                "suite": "security_headers",
                "reason": "needs a synchronous client bound to the app; run in CI with --app",
            },
        ],
    }


@router.get("/harness/allowlist")
async def public_allowlist(
    tenant_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_EVIDENCE_READ)),
):
    """The reviewed public-route allowlist, so a reviewer can read it from the API."""
    from security_harness.authz_matrix import PUBLIC_ROUTES

    scoped_tenant(ctx, tenant_id)
    return {
        "count": len(PUBLIC_ROUTES),
        "routes": [
            {"path": path, "reason": reason} for path, reason in sorted(PUBLIC_ROUTES.items())
        ],
    }


@router.get("/harness/corpus")
async def ssrf_corpus(
    tenant_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_EVIDENCE_READ)),
):
    """The SSRF destinations the harness requires to be refused."""
    from security_harness.ssrf_probe import DANGEROUS_URLS, SAFE_URLS

    scoped_tenant(ctx, tenant_id)
    return {
        "dangerous": [{"url": case.url, "family": case.reason} for case in DANGEROUS_URLS],
        "safe": [{"url": case.url, "purpose": case.reason} for case in SAFE_URLS],
    }


__all__ = ["router"]
