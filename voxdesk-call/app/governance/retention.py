"""Fail-closed retention and legal-hold checks.

Retention policy is durable governance state. Physical deletion of evidence is
an operator/database lifecycle action and is intentionally not exposed as a
normal API mutation; this module supplies the shared guard for any approved
retention worker.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .context import GovernanceScope
from .exceptions import LegalHoldActive, MissingGovernanceConfiguration
from .models import RetentionRule

DEFAULT_RETENTION_DAYS = 365


def retention_cutoff(retention_days: int, *, now: dt.datetime | None = None) -> dt.datetime:
    if retention_days < 1:
        raise ValueError("retention_days must be positive")
    current = now or dt.datetime.now(dt.timezone.utc)
    return current - dt.timedelta(days=retention_days)


async def active_rule(
    session: AsyncSession, scope: GovernanceScope, evidence_type: str
) -> RetentionRule:
    row = await session.scalar(
        select(RetentionRule)
        .where(
            RetentionRule.tenant_id == scope.tenant_id,
            RetentionRule.organization_id == scope.organization_id,
            RetentionRule.evidence_type == evidence_type,
            RetentionRule.active.is_(True),
        )
        .order_by(RetentionRule.version.desc())
        .limit(1)
    )
    if row is None:
        raise MissingGovernanceConfiguration(
            f"No active retention rule for evidence type {evidence_type}"
        )
    return row


async def purge_cutoff(
    session: AsyncSession,
    scope: GovernanceScope,
    *,
    evidence_type: str,
    now: dt.datetime | None = None,
) -> dt.datetime:
    """Return an approved cutoff; never bypass a legal hold or missing rule."""
    row = await active_rule(session, scope, evidence_type)
    if row.legal_hold:
        raise LegalHoldActive(f"Legal hold is active for {evidence_type}")
    return retention_cutoff(row.retention_days, now=now)
