"""Concurrency limits accounted by the database, not by a dictionary.

The ``jobs`` table *is* the ledger: a running row is an occupied slot, so
"how many slots does tenant X hold" is a ``COUNT(*)`` over rows whose status
is ``running`` — the same view every worker and every replica sees. Process
memory holds nothing authoritative here (requirement: no Python dictionary
as the concurrency truth).

Enforcement is written into the claim itself: the admission conditions are
SQL predicates evaluated inside the candidate SELECT *and* re-checked in the
compare-and-set UPDATE, so two workers racing for the last slot of a tenant
cannot both win it — the loser's UPDATE matches zero rows and it moves to
the next candidate.

Limits are admission control, not hard guarantees about already-running
work: a worker that crashes holds its slot until lease expiry reaps it, and
a tenant at its limit simply waits (its jobs stay durably queued).

Fairness lives in the claim ordering (``app.jobs.queue``): priority first,
with age-based promotion so a busy tenant's high-priority stream cannot
starve anyone's low-priority rows, and the per-tenant limit stops one tenant
from occupying the global capacity.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from sqlalchemy import Select, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db.models import DurableJob
from app.jobs.types import JobType


@dataclass(frozen=True)
class ConcurrencyLimits:
    """Admission limits. Values are policy; the *counts* come from the DB."""

    #: Ceiling on simultaneously running jobs across all tenants.
    global_limit: int = 32
    #: Ceiling on simultaneously running jobs for one tenant.
    tenant_limit: int = 4
    #: Optional per-job-type ceilings. Types absent here are only bounded by
    #: the global and tenant limits. Outbox delivery is capped so a backlog
    #: of events cannot occupy every slot the tenant's other work needs.
    type_limits: Mapping[str, int] = field(
        default_factory=lambda: {JobType.OUTBOX_DELIVERY: 8}
    )

    def limit_for_type(self, job_type: str) -> int | None:
        return self.type_limits.get(job_type)


#: Default admission policy for the scheduler-run worker. Deliberately
#: modest: this product ships a single worker process beside the API, and a
#: tenant's durable work should interleave, not monopolise.
DEFAULT_LIMITS = ConcurrencyLimits()


def _running_subquery(
    *,
    alias_name: str,
    tenant_scoped: bool = False,
    type_scoped: bool = False,
):
    """COUNT of running rows, optionally correlated to the candidate row."""
    running = aliased(DurableJob, name=alias_name)
    conditions = [running.status == "running"]
    if tenant_scoped:
        conditions.append(running.tenant_id == DurableJob.tenant_id)
    if type_scoped:
        conditions.append(running.job_type == DurableJob.job_type)
    subquery = select(func.count()).select_from(running).where(*conditions)
    if tenant_scoped or type_scoped:
        subquery = subquery.correlate(DurableJob)
    return subquery.scalar_subquery()


def admission_conditions(limits: ConcurrencyLimits = DEFAULT_LIMITS) -> Sequence:
    """SQL predicates that make a candidate claimable under ``limits``.

    Applied to both the claim SELECT and the claim UPDATE, so the check and
    the write see the same rule and the write re-verifies it.
    """
    conditions = [
        _running_subquery(alias_name="jobs_running_global") < int(limits.global_limit),
        _running_subquery(alias_name="jobs_running_tenant", tenant_scoped=True)
        < int(limits.tenant_limit),
    ]
    if limits.type_limits:
        limit_case = case(
            *[(DurableJob.job_type == job_type, int(limit)) for job_type, limit in limits.type_limits.items()],
            else_=None,
        )
        type_count = _running_subquery(alias_name="jobs_running_type", type_scoped=True)
        conditions.append(or_(limit_case.is_(None), type_count < limit_case))
    return conditions


async def running_count(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID | None = None,
    job_type: str | None = None,
) -> int:
    """Occupied slots right now — the number the limits are compared to."""
    stmt: Select = select(func.count()).select_from(DurableJob).where(
        DurableJob.status == "running"
    )
    if tenant_id is not None:
        stmt = stmt.where(DurableJob.tenant_id == tenant_id)
    if job_type is not None:
        stmt = stmt.where(DurableJob.job_type == job_type)
    return int((await session.execute(stmt)).scalar_one() or 0)


async def admits(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    job_type: str,
    limits: ConcurrencyLimits = DEFAULT_LIMITS,
) -> bool:
    """Read-only admission probe (operator APIs, metrics, tests).

    The claim path does not call this — it carries the same rules as SQL
    predicates so the decision and the write are one atomic step.
    """
    if await running_count(session) >= limits.global_limit:
        return False
    if await running_count(session, tenant_id=tenant_id) >= limits.tenant_limit:
        return False
    type_limit = limits.limit_for_type(job_type)
    if type_limit is not None:
        if await running_count(session, job_type=job_type) >= type_limit:
            return False
    return True
