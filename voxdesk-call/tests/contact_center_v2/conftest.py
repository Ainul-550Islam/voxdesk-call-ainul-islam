"""CC2 fixtures use the repository's real database, auth and model metadata.

No carrier result is fabricated as production evidence. Tests that cross a
carrier boundary replace the provider adapter and assert the exact ambiguous or
acknowledged outcome at that boundary; carrier-dependent proof lives in live/.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center.models import AgentPresence
from app.contact_center_v2.repository import utcnow
from app.db.contact_center_v2_models import DialerPolicy, SoftphoneSession
from app.db.models import Campaign, Environment, Lead, Tenant, User
from app.leads.consent import record_consent
from app.telephony.number_provisioning import PhoneNumber
from app.telephony.number_trust import NumberTrustProfile

ALL_DAYS = list(range(7))
OPEN_WINDOWS = [{"days": ALL_DAYS, "opens_at": "00:00", "closes_at": "23:59"}]
SAFE_HARBOR = "This call may be monitored. You may request a callback or ask to be removed."


@dataclass
class Cc2Scope:
    tenant: Tenant
    environment: Environment
    campaign: Campaign


@pytest_asyncio.fixture
async def cc2_scope(db: AsyncSession, tenant_a: Tenant) -> Cc2Scope:
    environment = (
        await db.execute(
            select(Environment).where(
                Environment.tenant_id == tenant_a.id,
                Environment.kind == "production",
                Environment.status == "active",
            )
        )
    ).scalar_one()
    tenant_a.outbound_enabled = True
    tenant_a.is_active = True
    campaign = Campaign(
        tenant_id=tenant_a.id,
        environment_id=environment.id,
        name="CC2 test campaign",
        is_active=True,
    )
    db.add(campaign)
    await db.commit()
    return Cc2Scope(tenant=tenant_a, environment=environment, campaign=campaign)


@pytest_asyncio.fixture
async def cc2_caller(db: AsyncSession, cc2_scope: Cc2Scope) -> PhoneNumber:
    number = PhoneNumber(
        tenant_id=cc2_scope.tenant.id,
        environment_id=cc2_scope.environment.id,
        provider="twilio",
        e164="+15550001000",
        external_id="PN-CC2-TEST",
        status="active",
        active_key="+15550001000",
        capabilities={"voice": True, "sms": False, "mms": False},
        capability_source="provider_api",
    )
    db.add(number)
    await db.flush()
    db.add(NumberTrustProfile(
        tenant_id=cc2_scope.tenant.id,
        phone_number_id=number.id,
        e164=number.e164,
        provider="twilio",
        shaken_attestation="A",
        spam_status="clean",
        a2p_registration="not_applicable",
        provider_payload={"source": "test_adapter"},
        last_checked=datetime.now(timezone.utc),
    ))
    await db.commit()
    return number


@pytest_asyncio.fixture
async def lead_factory(db: AsyncSession, cc2_scope: Cc2Scope):
    async def create(
        *,
        phone: str = "+15550002000",
        timezone_name: str = "UTC",
        consent: bool = True,
        custom_fields: dict[str, Any] | None = None,
    ) -> Lead:
        fields = dict(custom_fields or {})
        fields["timezone"] = timezone_name
        lead = Lead(
            tenant_id=cc2_scope.tenant.id,
            environment_id=cc2_scope.environment.id,
            campaign_id=cc2_scope.campaign.id,
            name="Test recipient",
            phone=phone,
            custom_fields=fields,
        )
        db.add(lead)
        await db.flush()
        if consent:
            await record_consent(
                db, lead, channel="voice", decision="granted", source="signed_test_form"
            )
        await db.commit()
        return lead
    return create


@pytest_asyncio.fixture
async def policy_factory(db: AsyncSession, cc2_scope: Cc2Scope, cc2_caller: PhoneNumber):
    async def create(*, mode: str = "predictive", **overrides: Any) -> DialerPolicy:
        values: dict[str, Any] = {
            "tenant_id": cc2_scope.tenant.id,
            "environment_id": cc2_scope.environment.id,
            "campaign_id": cc2_scope.campaign.id,
            "mode": mode,
            "caller_id_number": cc2_caller.e164,
            "pacing_ratio": Decimal("1"),
            "abandonment_cap": Decimal("0.03"),
            "max_inflight": 10,
            "calling_windows": OPEN_WINDOWS,
            "holiday_dates": [],
            "safe_harbor_message": SAFE_HARBOR,
            "consent_required": True,
            "jurisdiction": "US",
        }
        values.update(overrides)
        policy = DialerPolicy(**values)
        db.add(policy)
        await db.commit()
        return policy
    return create


@pytest_asyncio.fixture
async def available_presence(db: AsyncSession, agent_a: User) -> AgentPresence:
    row = AgentPresence(
        tenant_id=agent_a.tenant_id,
        user_id=agent_a.id,
        state="available",
        last_seen=utcnow(),
        state_changed_at=utcnow(),
        capacity=1,
        active_count=0,
    )
    db.add(row)
    await db.commit()
    return row


@pytest_asyncio.fixture
async def softphone_registration(
    db: AsyncSession,
    cc2_scope: Cc2Scope,
    agent_a: User,
) -> SoftphoneSession:
    row = SoftphoneSession(
        tenant_id=cc2_scope.tenant.id,
        environment_id=cc2_scope.environment.id,
        user_id=agent_a.id,
        registration_key=f"test-{uuid.uuid4().hex}",
        request_digest=uuid.uuid4().hex * 2,
        client_identity=f"agent-{agent_a.id.hex}",
        device_label="Test adapter registration",
        status="active",
        expires_at=datetime.now(timezone.utc).replace(microsecond=0) + timedelta(minutes=5),
        last_heartbeat_at=utcnow(),
        revoked_at=None,
    )
    db.add(row)
    await db.commit()
    return row


def unique_phone() -> str:
    return f"+1555{uuid.uuid4().int % 10**7:07d}"
