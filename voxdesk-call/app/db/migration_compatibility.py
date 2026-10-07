"""Small, fail-closed compatibility helpers for Alembic metadata.

Alembic's default ``alembic_version.version_num`` is ``VARCHAR(32)``. Several
historical revision IDs exceeded that limit and were shortened in the current
revision graph. PostgreSQL installations using the default table could not
persist those old values, but SQLite or custom version tables may contain them.
This helper maps only the known historical aliases to the current IDs before
Alembic reads the version table.

No application data or schema is changed by this normalization. The helper
refuses ambiguous states (both old and canonical marker rows present) rather
than guessing which migration state is authoritative.
"""
from __future__ import annotations

from sqlalchemy import Connection, inspect, text


LEGACY_REVISION_ALIASES: dict[str, str] = {
    "0017_organization_environment_foundation": "0017_org_environment_foundation",
    "0018_organization_memberships_quotas": "0018_org_memberships_quotas",
    "0019_environment_scope_business_resources": "0019_env_scope_business_res",
    "0020_durable_enterprise_operations": "0020_durable_enterprise_ops",
    "0024_qa_conversation_intelligence": "0024_qa_conversation_intel",
    "0031_workflow_persistence_hardening": "0031_workflow_persistence_hard",
    "0032_enterprise_governance_foundation": "0032_enterprise_governance_found",
    "0034_review_and_specialized_persistence": "0034_review_specialized_persist",
    "0035_enterprise_compliance_roi_deployment": "0035_compliance_roi_deployment",
    "0036_runtime_deployment_observability": "0036_runtime_deployment_observ",
    "0038_agent_chat_contact_conductor": "0038_agent_chat_conductor",
    "0045_request_idempotency_receipts": "0045_request_idem_receipts",
}


def normalize_legacy_revision_aliases(connection: Connection) -> int:
    """Replace known overlength revision markers in ``alembic_version``.

    The update runs in its own transaction before ``MigrationContext`` is
    configured. Returning the number of rewritten rows is useful for tests and
    structured operator logging; no revision strings or credentials are logged
    by this helper.
    """
    rewritten = 0
    with connection.begin():
        inspector = inspect(connection)
        if not inspector.has_table("alembic_version"):
            return rewritten
        columns = {column["name"] for column in inspector.get_columns("alembic_version")}
        if "version_num" not in columns:
            return rewritten

        current = set(
            connection.execute(text("SELECT version_num FROM alembic_version")).scalars()
        )
        for legacy, canonical in LEGACY_REVISION_ALIASES.items():
            if legacy not in current:
                continue
            if canonical in current:
                raise RuntimeError(
                    "Ambiguous Alembic version state: both a legacy and canonical "
                    "revision marker are present. Resolve alembic_version manually "
                    "after verifying the database schema."
                )
            result = connection.execute(
                text(
                    "UPDATE alembic_version "
                    "SET version_num = :canonical "
                    "WHERE version_num = :legacy"
                ),
                {"canonical": canonical, "legacy": legacy},
            )
            if result.rowcount != 1:
                raise RuntimeError(
                    "Alembic revision compatibility update did not affect exactly "
                    "one version marker; refusing to continue."
                )
            current.remove(legacy)
            current.add(canonical)
            rewritten += 1
    return rewritten
