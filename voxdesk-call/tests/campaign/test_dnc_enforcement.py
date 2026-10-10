"""DNC enforcement tests across dial_lead, outbound_call_routes, batch_call_routes, and check_dnc (Part 1B / Gate G1)."""

from __future__ import annotations

import uuid
from datetime import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.api.batch_call_routes import (
    BatchCallCreate,
    RecipientInput,
    create_batch_call,
    start_batch_call,
)
from app.api.call_search_export_routes import DncCheckRequest, check_dnc
from app.api.outbound_call_routes import _check_dnc
from app.auth.dependencies import TenantContext
from app.db.enterprise_models import (
    BatchRecipient,
    BatchRecipientStatus,
    CallPolicy,
    DncEntry,
)
from app.db.models import Campaign, Environment, LeadStatus, User, UserRole
from app.telephony import dnc, outbound
from tests.conftest import make_lead, make_tenant


async def _seed_tenant_and_ctx(db):
    tenant = await make_tenant(db, "DNC Co")
    tenant.outbound_enabled = True
    tenant.outbound_caller_id = "+15550009999"
    tenant.outbound_window_open = time(0, 0)
    tenant.outbound_window_close = time(23, 59, 59)
    user = User(
        tenant_id=tenant.id,
        email=f"dnc-{uuid.uuid4().hex[:6]}@example.com",
        password_hash="x",
        role=UserRole.OWNER,
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(tenant)
    await db.refresh(user)

    production = (
        await db.execute(
            select(Environment).where(
                Environment.tenant_id == tenant.id,
                Environment.kind == "production",
            )
        )
    ).scalar_one()

    ctx = TenantContext(
        user=user,
        tenant=tenant,
        auth_method="session",
        environment_id=production.id,
    )
    return tenant, user, production, ctx


@pytest.mark.asyncio
async def test_dnc_entry_normalizes_phone_on_insert_and_blocks_all_paths(
    db, monkeypatch
):
    tenant, _, production, ctx = await _seed_tenant_and_ctx(db)

    # Insert a non-E.164 phone; listener normalizes it to +15551234567
    entry = DncEntry(
        tenant_id=tenant.id,
        phone="(555) 123-4567",
        reason="customer_opt_out",
        source="manual",
    )
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    assert entry.phone == "+15551234567"

    # 1. Centralized dnc.is_blocked blocks both raw and E.164 input
    blocked, reason = await dnc.is_blocked(db, tenant.id, "555-123-4567")
    assert blocked is True
    assert reason == "dnc_entry:customer_opt_out"

    # 2. dial_lead blocks the lead and marks LeadStatus.DNC without calling Twilio
    campaign = Campaign(
        tenant_id=tenant.id,
        environment_id=production.id,
        name="DNC Test Campaign",
        is_active=True,
        calls_per_minute=5,
    )
    db.add(campaign)
    await db.commit()
    await db.refresh(campaign)

    lead = await make_lead(
        db,
        tenant,
        phone="+15551234567",
        campaign_id=campaign.id,
        environment_id=production.id,
    )

    twilio_called = False

    def _fail_twilio():
        nonlocal twilio_called
        twilio_called = True
        raise AssertionError("Twilio must not be called for DNC numbers")

    monkeypatch.setattr(outbound, "_twilio", _fail_twilio)
    monkeypatch.setattr(outbound, "_twilio_client", _fail_twilio)

    call = await outbound.dial_lead(db, lead, campaign=campaign)
    assert call is None
    assert twilio_called is False
    assert lead.status == LeadStatus.DNC

    # 3. outbound_call_routes._check_dnc raises 403
    with pytest.raises(HTTPException) as exc_info:
        await _check_dnc(
            db,
            tenant.id,
            "+15551234567",
            environment_id=production.id,
        )
    assert exc_info.value.status_code == 403

    # 4. call_search_export_routes.check_dnc reports blocked=True
    dnc_resp = await check_dnc(
        DncCheckRequest(phone="5551234567"),
        ctx=ctx,
        session=db,
    )
    assert dnc_resp["blocked"] is True
    assert dnc_resp["dnc_entry"]["id"] == str(entry.id)


@pytest.mark.asyncio
async def test_dnc_policy_prefix_and_tenant_isolation_in_batch_routes(
    db, monkeypatch
):
    tenant_a, _, prod_a, ctx_a = await _seed_tenant_and_ctx(db)
    tenant_b, _, prod_b, _ = await _seed_tenant_and_ctx(db)

    # Tenant A has a DNC CallPolicy blocking +1800555 prefix
    prefix_policy = CallPolicy(
        tenant_id=tenant_a.id,
        agent_id="",
        policy_type="dnc",
        config={"blocked_prefixes": ["+1800555"]},
        is_enabled=True,
    )
    # Tenant B adds a TENANT-scoped DNC on +15557778888 (must NOT block Tenant A)
    tenant_b_only = DncEntry(
        tenant_id=tenant_b.id,
        phone="+15557778888",
        reason="tenant_b_private",
        source="manual",
    )
    db.add_all([prefix_policy, tenant_b_only])
    await db.commit()

    # Tenant A is blocked on +18005551212 via prefix policy
    blocked_prefix, reason_prefix = await dnc.is_blocked(
        db, tenant_a.id, "+18005551212"
    )
    assert blocked_prefix is True
    assert reason_prefix == "dnc_policy"

    # Tenant A is NOT blocked on Tenant B's private DNC number
    blocked_other, _ = await dnc.is_blocked(
        db, tenant_a.id, "+15557778888"
    )
    assert blocked_other is False

    # BatchCall creation in Tenant A marks +18005551212 as DNC_BLOCKED and dials +15557778888
    dialed_to: list[str] = []

    class _FakeCalls:
        def create(self, **kwargs):
            dialed_to.append(kwargs["to"])
            return SimpleNamespace(sid=f"CA{uuid.uuid4().hex[:16]}")

    monkeypatch.setattr(
        outbound, "_twilio", lambda: SimpleNamespace(calls=_FakeCalls())
    )

    batch_out = await create_batch_call(
        BatchCallCreate(
            name="DNC Batch Check",
            agent_id="agent-1",
            recipients=[
                RecipientInput(phone="+18005551212", name="Blocked Tollfree"),
                RecipientInput(phone="+15557778888", name="Allowed Recipient"),
            ],
        ),
        ctx=ctx_a,
        session=db,
        x_idempotency_key="batch-dnc-test-01",
    )
    await start_batch_call(
        uuid.UUID(batch_out.id),
        ctx=ctx_a,
        session=db,
    )

    assert dialed_to == ["+15557778888"]
    recs = (
        await db.execute(
            select(BatchRecipient).where(
                BatchRecipient.batch_id == uuid.UUID(batch_out.id)
            )
        )
    ).scalars().all()
    by_phone = {r.phone: r for r in recs}
    assert by_phone["+18005551212"].status == BatchRecipientStatus.DNC_BLOCKED.value
    assert by_phone["+18005551212"].call_id is None
    assert by_phone["+15557778888"].call_id is not None
