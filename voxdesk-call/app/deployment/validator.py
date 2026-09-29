from __future__ import annotations
import re
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import Environment
from app.deployment.models import DeploymentRevision
from app.deployment.schemas import TARGET_TYPES
from app.governance.context import GovernanceScope
from app.tenancy import regions

_DIGEST=re.compile(r"^(sha256:)?[0-9a-f]{64}$")

async def validate_target(session:AsyncSession,scope:GovernanceScope,target,*,revision:DeploymentRevision|None=None)->dict:
    missing=[];checks={}
    environment=await session.scalar(select(Environment).where(Environment.id==target.environment_id,Environment.tenant_id==scope.tenant_id))
    checks["environment_exists_and_tenant_scoped"]=environment is not None
    if environment is None:missing.append("environment_not_found_in_tenant")
    elif environment.status!="active":missing.append("environment_not_active")
    checks["target_type_supported"]=target.target_type in TARGET_TYPES
    if not checks["target_type_supported"]:missing.append("unsupported_target_type")
    region_name=target.region
    if not region_name:missing.append("region_not_configured")
    else:
        try: supported=regions.is_supported(regions.normalize_label(region_name))
        except ValueError:supported=False
        checks["region_label_supported"]=bool(supported)
        if not supported:missing.append("region_not_supported_by_catalog")
        # Catalog membership is not physical residency proof; never report that it is.
        if target.data_residency_intent.get("required") and not target.data_residency_intent.get("authoritative_verification_reference"):
            missing.append("residency_authoritative_proof_unavailable")
    checks["artifact_revision_present"]=revision is not None
    if revision is None:missing.append("deployment_revision_not_created")
    else:
        checks["artifact_digest_format_valid"]=bool(_DIGEST.fullmatch(revision.artifact_digest.lower()))
        checks["artifact_reference_present"]=bool(revision.artifact_reference)
        if not checks["artifact_digest_format_valid"]:missing.append("artifact_digest_invalid")
        if not checks["artifact_reference_present"]:missing.append("artifact_reference_missing")
    # These checks require integrations that must verify actual platform state. No request-body booleans can satisfy them.
    for name in ("secrets_reference_verification","connector_configuration_verification","migration_compatibility_verification","governance_posture_verification","monitoring_verification","backup_restore_verification"):
        checks[name]="unavailable"
        missing.append(name+"_unavailable")
    if target.target_type=="air_gapped":
        for name in ("offline_package_manifest_verification","network_dependency_scan","offline_provider_verification","isolated_credential_source_verification","update_bundle_integrity_verification"):
            checks[name]="unavailable";missing.append(name+"_unavailable")
    return {"readiness":"READY" if not missing else "NOT_READY","missing_prerequisites":sorted(set(missing)),"checks":checks,"verification_state":"preflight_passed" if not missing else "not_verified","runtime_verified":False,"residency_proven":False,"reason":"Readiness is based only on independently checked records; no deployment or physical residency is asserted."}
