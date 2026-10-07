"""Evidence-aware deployment readiness; unobserved prerequisites never become READY."""
from __future__ import annotations

import re
from sqlalchemy import select, text

from app.deployment.adapters.registry import ArtifactRegistryAdapter
from app.providers.compatibility import capabilities_for

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def _result(status: str, evidence: str | None = None) -> dict:
    return {"status": status, "evidence": evidence}


async def _approval_and_governance(session, scope, revision) -> tuple[tuple[str, str], tuple[str, str]]:
    if session is None or scope is None:
        return "NOT_VERIFIED", "scoped approval and policy records were not loaded"
    from app.review.models import ReviewCase
    from app.governance.models import GovernancePolicyDecision
    approval = await session.scalar(select(ReviewCase).where(
        ReviewCase.tenant_id == scope.tenant_id,
        ReviewCase.organization_id == scope.organization_id,
        ReviewCase.environment_id == scope.environment_id,
        ReviewCase.case_type == "deployment",
        ReviewCase.subject_type == "deployment_revision",
        ReviewCase.subject_id == str(revision.id),
        ReviewCase.status == "approved",
    ))
    decision = None
    if revision.policy_decision_id:
        decision = await session.scalar(select(GovernancePolicyDecision).where(
            GovernancePolicyDecision.id == revision.policy_decision_id,
            GovernancePolicyDecision.tenant_id == scope.tenant_id,
            GovernancePolicyDecision.organization_id == scope.organization_id,
            GovernancePolicyDecision.environment_id == scope.environment_id,
            GovernancePolicyDecision.decision == "allow",
        ))
    return ("PASS" if decision else "FAIL", "persisted allow policy decision" if decision else "persisted allow policy decision missing"), ("PASS" if approval else "FAIL", "approved human review record" if approval else "approved human review record missing")


async def evaluate_readiness(*, target, revision, registry: ArtifactRegistryAdapter | None = None, session=None, scope=None) -> dict:
    """Inspect persisted target/revision prerequisites and report honest per-check states."""
    checks: dict[str, dict] = {}

    def put(name: str, status: str, evidence: str | None = None) -> None:
        checks[name] = _result(status, evidence)

    target_present = bool(target and getattr(target, "id", None))
    revision_present = bool(revision and getattr(revision, "id", None))
    put("environment", "PASS" if target_present and getattr(target, "environment_id", None) else "FAIL", "persisted target environment identity" if target_present and getattr(target, "environment_id", None) else "target/environment missing")
    put("target", "PASS" if target_present else "FAIL", "persisted deployment target" if target_present else "target missing")
    put("target_active", "PASS" if target_present and getattr(target, "status", "active") == "active" else "FAIL", "persisted target status")
    put("revision", "PASS" if revision_present else "FAIL", "persisted deployment revision" if revision_present else "revision missing")
    target_match = revision_present and target_present and getattr(revision, "target_id", getattr(target, "id", None)) == target.id
    put("revision_target_match", "PASS" if target_match else "FAIL", "revision is bound to the scoped target" if target_match else "revision/target identity mismatch")

    digest = getattr(revision, "artifact_digest", "") if revision_present else ""
    digest_ok = bool(_DIGEST.fullmatch(digest or ""))
    put("artifact_digest_format", "PASS" if digest_ok else "FAIL", "approved SHA-256 artifact digest" if digest_ok else "approved artifact digest missing or malformed")
    put("artifact_reference", "PASS" if revision_present and bool(getattr(revision, "artifact_reference", "")) else "FAIL", "persisted artifact reference" if revision_present and getattr(revision, "artifact_reference", "") else "artifact reference missing")
    put("runtime_version", "PASS" if revision_present and bool(getattr(revision, "runtime_version", "")) else "FAIL", "persisted expected runtime version" if revision_present and getattr(revision, "runtime_version", "") else "runtime version missing")
    put("migration_revision", "PASS" if revision_present and bool(getattr(revision, "migration_revision", "")) else "NOT_VERIFIED", "deployment migration target recorded" if revision_present and getattr(revision, "migration_revision", "") else "migration target missing")

    if session is None:
        put("migration_compatibility", "NOT_VERIFIED", "database migration state was not queried")
    else:
        try:
            current = (await session.execute(text("SELECT version_num FROM alembic_version"))).scalar_one_or_none()
            expected = getattr(revision, "migration_revision", None) if revision_present else None
            put("migration_compatibility", "PASS" if current == expected and expected else "FAIL", "database Alembic revision matches deployment target" if current == expected and expected else "database Alembic revision does not match deployment target")
        except Exception:
            put("migration_compatibility", "NOT_AVAILABLE", "Alembic version table could not be queried")

    prerequisites = getattr(target, "prerequisites", {}) or {} if target_present else {}
    secrets = prerequisites.get("secret_references") if isinstance(prerequisites, dict) else None
    put("secret_references", "NOT_VERIFIED" if secrets else "PASS", "secret values are never read by preflight" if secrets else "no external secret references declared")
    for name, key in (("network_rules", "network_rules"), ("monitoring", "monitoring"), ("backup_restore", "backup_restore")):
        value = prerequisites.get(key) if isinstance(prerequisites, dict) else None
        put(name, "NOT_VERIFIED" if value else "NOT_AVAILABLE", "configuration claim is not authoritative runtime evidence" if value else "no authoritative prerequisite verifier is configured")
    put("residency", "NOT_VERIFIED", "residency intent is not proof of physical placement")

    provider_name = prerequisites.get("provider") if isinstance(prerequisites, dict) else None
    if provider_name:
        report = capabilities_for(str(provider_name))
        put("provider_capability", "PASS" if report.capable and report.configured else "FAIL" if not report.capable else "NOT_VERIFIED", report.api_surface or report.reason)
    else:
        put("provider_capability", "NOT_AVAILABLE", "no required deployment provider declared")

    if revision_present and registry:
        resolution = await registry.resolve(revision.artifact_reference, digest)
        put("artifact_registry", "PASS" if resolution.available and resolution.exists and resolution.immutable else "FAIL" if resolution.available else "NOT_AVAILABLE", resolution.reason or ("registry resolved immutable matching digest" if resolution.immutable else "registry digest is not verified"))
    else:
        put("artifact_registry", "NOT_AVAILABLE", "registry verifier is not configured")

    if revision_present and target_present and session is not None and scope is not None:
        policy_result, approval_result = await _approval_and_governance(session, scope, revision)
        put("governance", policy_result[0], policy_result[1])
        put("approval", approval_result[0], approval_result[1])
    else:
        put("governance", "NOT_VERIFIED", "scoped governance decision was not loaded")
        put("approval", "NOT_VERIFIED", "scoped human approval was not loaded")

    values = [item["status"] for item in checks.values()]
    if "FAIL" in values:
        overall = "NOT_READY"
    elif all(value == "PASS" for value in values):
        overall = "READY"
    else:
        overall = "NOT_VERIFIED"
    # Compatibility projection: only PASS is true; unavailable/unknown is never true.
    legacy_checks = {name: value["status"] == "PASS" for name, value in checks.items()}
    return {
        "readiness": overall,
        "checks": legacy_checks,
        "check_results": checks,
        "missing_prerequisites": [name for name, value in checks.items() if value["status"] != "PASS"],
        "verification_state": "not_verified",
        "deployment_observed": False,
        "runtime_verified": False,
        "residency_proven": False,
        "reason": "Preflight evaluates persisted evidence only; no deployment, runtime health, or physical residency is inferred.",
    }
