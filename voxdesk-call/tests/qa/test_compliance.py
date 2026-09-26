"""AI findings stay suggestions until a person confirms them."""

from __future__ import annotations

import uuid

import pytest

from app.db.models import Speaker, Turn
from app.qa.compliance import acknowledge_remediation, add_finding, create_policy, dispose, evaluate_phrase
from app.qa.exceptions import InvalidTransition
from tests.acd_support import live_call, production


@pytest.mark.asyncio
async def test_rule_and_ai_findings_need_a_human_to_confirm(db, tenant_a):
    env = await production(db, tenant_a)
    call = await live_call(db, tenant_a, env)
    db.add(Turn(call_id=call.id, speaker=Speaker.ASSISTANT, text="Hello, how can I help?"))
    await db.flush()
    policy = await create_policy(
        db,
        tenant_id=tenant_a.id,
        code="recording-notice",
        category="required_disclosure_missing",
        required_phrase="this call may be recorded",
        severity="high",
    )
    later = await create_policy(
        db,
        tenant_id=tenant_a.id,
        code="recording-notice",
        category="required_disclosure_missing",
        required_phrase="this call may be recorded",
    )
    assert later.version == policy.version + 1
    missing = await evaluate_phrase(db, tenant_id=tenant_a.id, call_id=call.id, policy=policy)
    assert missing is not None
    assert missing.status == "open"
    assert missing.source == "rule"
    assert missing.policy_version == policy.version
    suggested, created = await add_finding(
        db,
        tenant_id=tenant_a.id,
        call_id=call.id,
        policy=policy,
        source="ai",
        confidence=70,
    )
    assert created is True
    assert suggested.status == "suggested"
    with pytest.raises(InvalidTransition):
        dispose(suggested, "confirmed", actor_is_human=False)
    dispose(suggested, "confirmed", actor_is_human=True)
    assert suggested.status == "confirmed"
    acknowledge_remediation(suggested)
    assert suggested.status == "remediated"
    assert suggested.remediation_state == "acknowledged"
    with pytest.raises(InvalidTransition):
        acknowledge_remediation(missing)


@pytest.mark.asyncio
async def test_phrase_present_does_not_create_a_finding_and_duplicate_is_idempotent(db, tenant_a):
    env = await production(db, tenant_a)
    call = await live_call(db, tenant_a, env)
    db.add(Turn(call_id=call.id, speaker=Speaker.ASSISTANT, text="This call may be recorded."))
    await db.flush()
    policy = await create_policy(
        db,
        tenant_id=tenant_a.id,
        code=f"notice-{uuid.uuid4().hex[:6]}",
        category="consent_evidence",
        required_phrase="this call may be recorded",
    )
    assert await evaluate_phrase(db, tenant_id=tenant_a.id, call_id=call.id, policy=policy) is None
    first, created = await add_finding(
        db,
        tenant_id=tenant_a.id,
        call_id=call.id,
        policy=policy,
        source="human",
        confidence=None,
        idempotency_key="human-once",
    )
    second, again = await add_finding(
        db,
        tenant_id=tenant_a.id,
        call_id=call.id,
        policy=policy,
        source="human",
        confidence=None,
        idempotency_key="human-once",
    )
    assert created is True
    assert again is False
    assert second.id == first.id
    assert first.status == "open"
