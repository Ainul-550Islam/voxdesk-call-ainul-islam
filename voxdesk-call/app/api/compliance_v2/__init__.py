"""HTTP surface of the Batch 03 compliance control plane.

Each module owns one area; this package composes them into one tenant-scoped
router under ``/api/tenants/{tenant_id}/compliance`` so ``app/main.py`` mounts
the whole surface with a single ``include_router`` (Batch 06). The area modules
keep their own prefixes, dependencies and tests:

* :mod:`siem_routes` — destinations, delivery status, dead letters, redrive;
* :mod:`audit_routes` — chain verification, signing key, signed export bundles;
* :mod:`kms_routes` — provider health, sealed-column inventory, rekey jobs;
* :mod:`residency_routes` — region registry, tenant pinning, violations;
* :mod:`evidence_routes` — control status, evidence runs, snapshot download;
* :mod:`access_review_routes` — campaigns and per-account decisions;
* :mod:`incident_routes` — incidents, timelines, breach clocks;
* :mod:`dsar_routes` — subject access and erasure requests;
* :mod:`hipaa_routes` — PHI policy, break-glass, session limits;
* :mod:`security_routes` — the pentest-fix regression harness.

Every router here resolves the tenant from the path, checks it against the
caller's scope, and requires an explicit permission; nothing in this package
holds deployment configuration — that lives in
:mod:`app.compliance_v2.settings`.
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

# BATCH06:compliance_v2_surface_composition
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
