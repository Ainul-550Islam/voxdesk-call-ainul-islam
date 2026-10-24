"""The Batch 03 compliance control plane, mounted as one tenant-scoped router.

Every area lives in its own module under :mod:`app.api.compliance_v2`; this file
only assembles them under ``/api/tenants/{tenant_id}/compliance`` so a client can
discover the surface in one request and so the route snapshot has a single
prefix to account for.

``GET /compliance`` itself is *not* this router: it is the pre-existing A2P and
do-not-call view in ``app/api/routes.py``, and shadowing it would silently change
what an operator's dashboard reads. The manifest therefore lives at
``/compliance/manifest``.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from app.api.compliance_v2 import (
    access_review_routes,
    audit_routes,
    dsar_routes,
    evidence_routes,
    hipaa_routes,
    incident_routes,
    kms_routes,
    residency_routes,
    security_routes,
    siem_routes,
)
from app.api.compliance_v2.common import scoped_tenant
from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission

MANIFEST_FORMAT = "voxdesk.compliance.manifest.v1"

#: Area name -> the module that serves it. Kept as data so the manifest and the
#: router assembly cannot drift apart.
AREAS = {
    "siem": siem_routes.router,
    "audit": audit_routes.router,
    "kms": kms_routes.router,
    "residency": residency_routes.router,
    "evidence": evidence_routes.router,
    "access-reviews": access_review_routes.router,
    "incidents": incident_routes.router,
    "dsar": dsar_routes.router,
    "hipaa": hipaa_routes.router,
    "security": security_routes.router,
}

router = APIRouter(prefix="/api/tenants/{tenant_id}/compliance", tags=["compliance-v2"])


@router.get("/manifest")
async def manifest(
    tenant_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_EVIDENCE_READ)),
):
    """The areas of this control plane and the paths they own.

    Reading the manifest is itself a permissioned, tenant-scoped operation: it
    describes an operator's configured controls, which is metadata about their
    security posture rather than public documentation.
    """
    scoped_tenant(ctx, tenant_id)
    return {
        "format": MANIFEST_FORMAT,
        "areas": {
            area: sorted(
                f"{'/'.join(sorted(route.methods))} {route.path}"
                for route in area_router.routes
            )
            for area, area_router in AREAS.items()
        },
        "note": (
            "Evidence and control status produced by this deployment. "
            "Reporting a control as passing makes no certification claim."
        ),
    }


for _area, _area_router in AREAS.items():
    router.include_router(_area_router)

__all__ = ["AREAS", "MANIFEST_FORMAT", "router"]
