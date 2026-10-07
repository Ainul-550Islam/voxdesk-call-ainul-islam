"""Configurable compliance findings.

An AI row starts as ``suggested``. A phrase rule starts as ``open``. Neither
is ``confirmed`` until a reviewer says so. No jurisdiction is invented.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.qa.evidence import load_call, turns_for_call
from app.qa.exceptions import InvalidScore, InvalidTransition
from app.qa.models import ComplianceFinding, CompliancePolicy
from app.tenancy.isolation import NotFound

CATEGORIES = {
    "required_disclosure_missing",
    "policy_phrase_mismatch",
    "sensitive_data_handling",
    "consent_evidence",
    "prohibited_workflow",
}
SEVERITIES = {"low", "medium", "high", "critical"}
DISPOSITIONS = {"", "confirmed", "dismissed", "needs_follow_up"}


async def create_policy(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    code: str,
    category: str,
    severity: str = "medium",
    required_phrase: str = "",
) -> CompliancePolicy:
    cleaned = (code or "").strip().lower()
    if not cleaned or len(cleaned) > 64:
        raise InvalidScore("Policy code is required")
    if category not in CATEGORIES:
        raise InvalidScore("Unknown compliance category")
    if severity not in SEVERITIES:
        raise InvalidScore("Unknown severity")
    current = await session.scalar(
        select(CompliancePolicy.version)
        .where(CompliancePolicy.tenant_id == tenant_id, CompliancePolicy.code == cleaned)
        .order_by(CompliancePolicy.version.desc())
    )
    row = CompliancePolicy(
        tenant_id=tenant_id,
        code=cleaned,
        version=int(current or 0) + 1,
        category=category,
        required_phrase=(required_phrase or "")[:200],
        severity=severity,
    )
    session.add(row)
    await session.flush()
    return row


async def add_finding(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    call_id: uuid.UUID,
    policy: CompliancePolicy,
    source: str,
    confidence: int | None,
    evidence_id: uuid.UUID | None = None,
    review_id: uuid.UUID | None = None,
    idempotency_key: str | None = None,
) -> tuple[ComplianceFinding, bool]:
    call = await load_call(session, tenant_id, call_id)
    if policy.tenant_id != tenant_id:
        raise NotFound()
    if source not in {"ai", "rule", "human"}:
        raise InvalidScore("Unknown finding source")
    if confidence is not None and (not isinstance(confidence, int) or confidence < 0 or confidence > 100):
        raise InvalidScore("Finding confidence must be 0..100")
    if policy.severity not in SEVERITIES:
        raise InvalidScore("Unknown severity")
    status = "suggested" if source == "ai" else "open"
    key = idempotency_key or f"{source}:{policy.id}:{call.id}:{evidence_id or 'none'}"
    row = ComplianceFinding(
        tenant_id=tenant_id,
        environment_id=call.environment_id,
        review_id=review_id,
        call_id=call.id,
        policy_id=policy.id,
        policy_code=policy.code,
        policy_version=policy.version,
        status=status,
        severity=policy.severity,
        confidence=confidence,
        source=source,
        evidence_id=evidence_id,
        idempotency_key=key[:128],
    )
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:
        found = (
            await session.execute(
                select(ComplianceFinding).where(
                    ComplianceFinding.tenant_id == tenant_id,
                    ComplianceFinding.idempotency_key == row.idempotency_key,
                )
            )
        ).scalar_one()
        return found, False
    return row, True


async def evaluate_phrase(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    call_id: uuid.UUID,
    policy: CompliancePolicy,
) -> ComplianceFinding | None:
    if not policy.enabled or not policy.required_phrase:
        return None
    turns = await turns_for_call(session, call_id)
    needle = policy.required_phrase.lower()
    if any(needle in (turn.text or "").lower() for turn in turns):
        return None
    row, _created = await add_finding(
        session,
        tenant_id=tenant_id,
        call_id=call_id,
        policy=policy,
        source="rule",
        confidence=100,
        idempotency_key=f"rule:{policy.id}:{call_id}:missing-phrase",
    )
    return row


def dispose(finding: ComplianceFinding, disposition: str, *, actor_is_human: bool) -> ComplianceFinding:
    if disposition not in DISPOSITIONS or not disposition:
        raise InvalidScore("Unknown disposition")
    if not actor_is_human:
        raise InvalidTransition("Only a reviewer can confirm a finding")
    if finding.status == "remediated" and disposition != "confirmed":
        raise InvalidTransition("Remediated finding cannot be reopened here")
    if disposition == "confirmed":
        finding.status = "confirmed"
        finding.reviewer_disposition = "confirmed"
    elif disposition == "dismissed":
        finding.status = "dismissed"
        finding.reviewer_disposition = "dismissed"
    else:
        finding.reviewer_disposition = disposition
    return finding


def acknowledge_remediation(finding: ComplianceFinding) -> ComplianceFinding:
    if finding.status != "confirmed":
        raise InvalidTransition("Only a confirmed finding can be remediated")
    finding.status = "remediated"
    finding.remediation_state = "acknowledged"
    return finding
