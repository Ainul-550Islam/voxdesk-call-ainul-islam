"""Opt-in PostgreSQL proofs for canonical CC2 single-claim execution."""
from __future__ import annotations

import asyncio
import inspect
import os
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.contact_center_v2.acd.queue_manager import sweep_overflow
from app.contact_center_v2.dialer.dialer_service import run_tick
from app.contact_center_v2.policy import prepare_contacts
from app.contact_center_v2.repository import utcnow
from app.contact_center_v2.settings import settings as cc2_settings
from app.db.contact_center_v2_models import DialerAttempt, DialerContact, DialerPolicy
from app.db.models import Base, Call, Campaign, Environment, Lead
from app.leads.consent import record_consent
from app.telephony.number_provisioning import PhoneNumber
from app.telephony.number_trust import NumberTrustProfile
from tests.conftest import make_tenant
from tests.contact_center_v2.conftest import OPEN_WINDOWS, SAFE_HARBOR


pytestmark = pytest.mark.live


@pytest.fixture(autouse=True)
def require_explicit_postgres_proof():
    if os.environ.get("VOXDESK_LIVE_TESTS") != "1":
        pytest.skip("set VOXDESK_LIVE_TESTS=1 and CC2_POSTGRES_TEST_URL for the PostgreSQL proof")
    if not os.environ.get("CC2_POSTGRES_TEST_URL", "").startswith(
        ("postgresql://", "postgresql+asyncpg://")
    ):
        pytest.skip("CC2_POSTGRES_TEST_URL must name a disposable PostgreSQL database")


@pytest.mark.asyncio
async def test_postgres_skip_locked_claims_distinct_rows():
    """A locked row is skipped, not returned to a second concurrent claimant."""
    assert ".with_for_update(skip_locked=True)" in inspect.getsource(sweep_overflow)
    assert ".with_for_update(skip_locked=True)" in inspect.getsource(run_tick)
    url = os.environ["CC2_POSTGRES_TEST_URL"]
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(url, pool_size=4)
    table = f"cc2_skip_locked_{uuid.uuid4().hex}"
    try:
        async with engine.begin() as connection:
            await connection.exec_driver_sql(
                f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, claimed BOOLEAN NOT NULL DEFAULT FALSE)"
            )
            await connection.exec_driver_sql(f"INSERT INTO {table} (id) VALUES (1), (2)")

        first = await engine.connect()
        second = await engine.connect()
        try:
            await first.begin()
            locked = await first.exec_driver_sql(f"SELECT id FROM {table} WHERE id = 1 FOR UPDATE")
            assert locked.scalar_one() == 1

            await second.begin()
            available = await second.exec_driver_sql(
                f"SELECT id FROM {table} WHERE claimed = FALSE ORDER BY id FOR UPDATE SKIP LOCKED"
            )
            assert available.scalar_one() == 2
            await second.exec_driver_sql(f"UPDATE {table} SET claimed = TRUE WHERE id = 2")
            await second.commit()

            await first.exec_driver_sql(f"UPDATE {table} SET claimed = TRUE WHERE id = 1")
            await first.commit()
        finally:
            await first.close()
            await second.close()

        async with engine.connect() as connection:
            rows = await connection.exec_driver_sql(
                f"SELECT id FROM {table} WHERE claimed = TRUE ORDER BY id"
            )
            assert rows.scalars().all() == [1, 2]
    finally:
        async with engine.begin() as connection:
            await connection.exec_driver_sql(f"DROP TABLE IF EXISTS {table}")
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_canonical_run_ticks_never_dial_a_contact_twice(
    monkeypatch: pytest.MonkeyPatch,
):
    """Two real async workers claim distinct ORM rows on PostgreSQL.

    The carrier boundary alone is replaced with an acknowledged test adapter;
    policy locks, contact claims, attempts, calls, and tick scheduling all use
    the canonical implementation and real PostgreSQL transactions.
    """
    url = os.environ["CC2_POSTGRES_TEST_URL"]
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    schema = f"cc2_run_tick_{uuid.uuid4().hex}"
    admin = create_async_engine(url, pool_size=1)
    engine = None
    try:
        async with admin.begin() as connection:
            await connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        engine = create_async_engine(
            url,
            pool_size=8,
            max_overflow=4,
            connect_args={"server_settings": {"search_path": schema}},
        )
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session:
            tenant = await make_tenant(session, "CC2 PostgreSQL concurrency")
            tenant.outbound_enabled = True
            tenant.is_active = True
            environment = await session.scalar(
                select(Environment).where(
                    Environment.tenant_id == tenant.id,
                    Environment.kind == "production",
                    Environment.status == "active",
                )
            )
            assert environment is not None
            campaign = Campaign(
                tenant_id=tenant.id,
                environment_id=environment.id,
                name="Concurrent canonical ticks",
                is_active=True,
            )
            session.add(campaign)
            await session.flush()
            leads = []
            for index in range(2):
                lead = Lead(
                    tenant_id=tenant.id,
                    environment_id=environment.id,
                    campaign_id=campaign.id,
                    name=f"Recipient {index}",
                    phone=f"+1555000200{index}",
                    custom_fields={"timezone": "UTC"},
                )
                session.add(lead)
                await session.flush()
                await record_consent(
                    session,
                    lead,
                    channel="voice",
                    decision="granted",
                    source="signed_test_form",
                )
                leads.append(lead)
            caller = PhoneNumber(
                tenant_id=tenant.id,
                environment_id=environment.id,
                provider="twilio",
                e164="+15550001000",
                external_id=f"PN-{uuid.uuid4().hex}",
                status="active",
                active_key="+15550001000",
                capabilities={"voice": True, "sms": False, "mms": False},
                capability_source="provider_api",
            )
            session.add(caller)
            await session.flush()
            session.add(NumberTrustProfile(
                tenant_id=tenant.id,
                phone_number_id=caller.id,
                e164=caller.e164,
                provider="twilio",
                shaken_attestation="A",
                spam_status="clean",
                a2p_registration="not_applicable",
                provider_payload={"source": "test_adapter"},
                last_checked=utcnow(),
            ))
            policy = DialerPolicy(
                tenant_id=tenant.id,
                environment_id=environment.id,
                campaign_id=campaign.id,
                mode="ai_agent_blast",
                status="running",
                caller_id_number=caller.e164,
                pacing_ratio=Decimal("2"),
                abandonment_cap=Decimal("0.03"),
                max_inflight=10,
                calling_windows=OPEN_WINDOWS,
                holiday_dates=[],
                safe_harbor_message=SAFE_HARBOR,
                consent_required=True,
                jurisdiction="US",
            )
            session.add(policy)
            await session.flush()
            assert await prepare_contacts(session, policy) == {
                "leads": 2, "eligible": 2, "blocked": 0,
            }
            await session.commit()
            tenant_id, policy_id = tenant.id, policy.id

        monkeypatch.setattr(cc2_settings, "dialer_max_attempts_per_tick", 1)
        provider_calls = []

        async def acknowledged(session, _tenant, _campaign, dialed_lead, **kwargs):
            provider_sid = f"CA{uuid.uuid4().hex}"
            provider_calls.append(dialed_lead.id)
            call = Call(
                tenant_id=dialed_lead.tenant_id,
                environment_id=dialed_lead.environment_id,
                lead_id=dialed_lead.id,
                call_sid=provider_sid,
                from_number=kwargs["caller_id_number"],
                to_number=dialed_lead.phone,
            )
            session.add(call)
            await session.flush()
            await asyncio.sleep(0.05)
            return {"ok": True, "call_sid": provider_sid, "call_id": str(call.id)}

        monkeypatch.setattr("app.telephony.outbound.place_call", acknowledged)

        async def worker():
            async with maker() as session:
                result = await run_tick(
                    session, tenant_id=tenant_id, policy_id=policy_id, now=utcnow(),
                )
                await session.commit()
                return result

        results = await asyncio.gather(worker(), worker())
        assert [row["attempted"] for row in results] == [1, 1]
        async with maker() as session:
            attempts = (
                await session.execute(
                    select(DialerAttempt).where(DialerAttempt.policy_id == policy_id)
                )
            ).scalars().all()
            contacts = (
                await session.execute(
                    select(DialerContact).where(DialerContact.policy_id == policy_id)
                )
            ).scalars().all()
        assert len(attempts) == 2
        assert len({row.contact_id for row in attempts}) == 2
        assert len(set(provider_calls)) == 2
        assert {row.lead_id for row in attempts} == set(provider_calls)
        assert all(row.outcome == "ringing" for row in attempts)
        assert all(row.state == "dialing" for row in contacts)
    finally:
        if engine is not None:
            await engine.dispose()
        async with admin.begin() as connection:
            await connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await admin.dispose()
