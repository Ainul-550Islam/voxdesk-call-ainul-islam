"""Deterministic sampling.

The bucket is sha256(tenant, window, rule, call) modulo 10000. The same
inputs select the same calls. A unique row stops the same rule and window
from sampling one call twice.
"""

from __future__ import annotations

import hashlib
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center.models import QueueEntry
from app.db.models import Call
from app.qa.exceptions import InvalidRubric
from app.qa.models import SampleSelection, SamplingRule
from app.telephony.qos import QosSample
from app.tenancy.isolation import NotFound

_KINDS = {
    "percentage",
    "count",
    "min_per_agent",
    "disposition",
    "queue",
    "agent",
    "compliance",
    "low_qos",
}


def bucket(tenant_id: uuid.UUID, window_key: str, rule_id: uuid.UUID, call_id: uuid.UUID) -> int:
    raw = f"{tenant_id}|{window_key}|{rule_id}|{call_id}".encode()
    return int(hashlib.sha256(raw).hexdigest()[:8], 16) % 10000


async def create_rule(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    name: str,
    kind: str,
    percent: int = 0,
    sample_count: int = 0,
    min_per_agent: int = 0,
    disposition: str = "",
    queue_id: uuid.UUID | None = None,
    agent_user_id: uuid.UUID | None = None,
    qos_threshold: int | None = None,
    environment_id: uuid.UUID | None = None,
) -> SamplingRule:
    if kind not in _KINDS:
        raise InvalidRubric("Unknown sampling kind")
    if not isinstance(percent, int) or percent < 0 or percent > 100:
        raise InvalidRubric("Percent must be 0..100")
    if sample_count < 0 or min_per_agent < 0:
        raise InvalidRubric("Sample counts cannot be negative")
    row = SamplingRule(
        tenant_id=tenant_id,
        environment_id=environment_id,
        name=name.strip()[:80],
        kind=kind,
        percent=percent,
        sample_count=sample_count,
        min_per_agent=min_per_agent,
        disposition=disposition[:32],
        queue_id=queue_id,
        agent_user_id=agent_user_id,
        qos_threshold=qos_threshold,
    )
    if not row.name:
        raise InvalidRubric("Sampling rule name is required")
    session.add(row)
    await session.flush()
    return row


def _disposition(call: Call) -> str:
    status = call.status.value if hasattr(call.status, "value") else str(call.status)
    return status


async def _agent_for(session: AsyncSession, tenant_id: uuid.UUID, call_id: uuid.UUID) -> uuid.UUID | None:
    from app.contact_center.models import RoutingAssignment

    row = (
        await session.execute(
            select(RoutingAssignment.user_id)
            .where(
                RoutingAssignment.tenant_id == tenant_id,
                RoutingAssignment.status == "active",
            )
            .join(QueueEntry, QueueEntry.id == RoutingAssignment.entry_id)
            .where(QueueEntry.call_id == call_id)
        )
    ).first()
    return None if row is None else row[0]


async def _eligible(
    session: AsyncSession, rule: SamplingRule, calls: list[Call]
) -> list[Call]:
    chosen: list[Call] = []
    for call in calls:
        if call.tenant_id != rule.tenant_id:
            continue
        if rule.environment_id is not None and call.environment_id != rule.environment_id:
            continue
        if rule.kind == "disposition" and _disposition(call) != rule.disposition:
            continue
        if rule.kind == "queue" and rule.queue_id is not None:
            entry = (
                await session.execute(
                    select(QueueEntry.id).where(
                        QueueEntry.tenant_id == rule.tenant_id,
                        QueueEntry.queue_id == rule.queue_id,
                        QueueEntry.call_id == call.id,
                    )
                )
            ).first()
            if entry is None:
                continue
        if rule.kind == "agent" and rule.agent_user_id is not None:
            agent = await _agent_for(session, rule.tenant_id, call.id)
            if agent != rule.agent_user_id:
                continue
        if rule.kind == "low_qos":
            sample = (
                await session.execute(
                    select(QosSample)
                    .where(QosSample.tenant_id == rule.tenant_id, QosSample.call_id == call.id)
                    .order_by(QosSample.received_at.desc())
                )
            ).scalars().first()
            if sample is None or sample.quality_index is None:
                continue
            if rule.qos_threshold is None or int(sample.quality_index) >= rule.qos_threshold:
                continue
        if rule.kind == "compliance":
            from app.qa.models import ComplianceFinding

            found = (
                await session.execute(
                    select(ComplianceFinding.id).where(
                        ComplianceFinding.tenant_id == rule.tenant_id,
                        ComplianceFinding.call_id == call.id,
                    )
                )
            ).first()
            if found is None:
                continue
        chosen.append(call)
    return chosen


async def _insert(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    rule_id: uuid.UUID,
    window_key: str,
    call_id: uuid.UUID,
) -> SampleSelection | None:
    row = SampleSelection(
        tenant_id=tenant_id,
        rule_id=rule_id,
        window_key=window_key,
        call_id=call_id,
    )
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:
        return None
    return row


async def run_rule(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    rule_id: uuid.UUID,
    window_key: str,
    call_ids: list[uuid.UUID],
) -> list[SampleSelection]:
    rule = await session.get(SamplingRule, rule_id)
    if rule is None or rule.tenant_id != tenant_id:
        raise NotFound()
    if not rule.enabled:
        return []
    if not window_key:
        raise InvalidRubric("Sampling window is required")
    calls = []
    for call_id in call_ids:
        call = await session.get(Call, call_id)
        if call is None or call.tenant_id != tenant_id:
            raise NotFound()
        calls.append(call)
    eligible = await _eligible(session, rule, calls)
    ranked = sorted(eligible, key=lambda call: (bucket(tenant_id, window_key, rule.id, call.id), str(call.id)))
    picked: list[Call] = []
    if rule.kind == "percentage":
        cutoff = rule.percent * 100
        picked = [call for call in ranked if bucket(tenant_id, window_key, rule.id, call.id) < cutoff]
    elif rule.kind == "count":
        picked = ranked[: rule.sample_count]
    elif rule.kind == "min_per_agent":
        per_agent: dict[str, list[Call]] = {}
        for call in ranked:
            agent = await _agent_for(session, tenant_id, call.id)
            key = "unassigned" if agent is None else str(agent)
            per_agent.setdefault(key, []).append(call)
        for group in per_agent.values():
            picked.extend(group[: rule.min_per_agent])
    else:
        limit = rule.sample_count or len(ranked)
        picked = ranked[:limit]
    created: list[SampleSelection] = []
    for call in picked:
        row = await _insert(
            session,
            tenant_id=tenant_id,
            rule_id=rule.id,
            window_key=window_key,
            call_id=call.id,
        )
        if row is not None:
            created.append(row)
    existing = list(
        (
            await session.execute(
                select(SampleSelection).where(
                    SampleSelection.tenant_id == tenant_id,
                    SampleSelection.rule_id == rule.id,
                    SampleSelection.window_key == window_key,
                )
            )
        ).scalars()
    )
    return existing
