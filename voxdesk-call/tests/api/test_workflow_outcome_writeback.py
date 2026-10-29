# File: tests/api/test_workflow_outcome_writeback.py — durable, tenant-scoped,
# idempotent post-call outcome write-back.
"""Tests for ``POST /api/workflows/calls/{call_id}/outcome-writeback``.

A write-back is a side effect in somebody else's system, so the properties that
matter are the ones that stop it happening twice or to the wrong tenant:

1. **Idempotency is durable and never re-dispatches.** Replaying a key returns
   the stored outcome; for the calendar target that is proven by asserting the
   appointment note was appended exactly once.
2. **Every target reports its own outcome.** A fan-out to two targets cannot be
   summarised by one status without hiding which one recorded the call.
3. **"Nothing to write to" is reported as skipped, never as success.** An empty
   target list succeeding would let a dashboard claim a CRM was updated when
   no CRM is connected.
4. **Tenant scoping.** Another tenant's call id is a 404.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.db.models  # noqa: F401
from app.db.models import (
    Appointment,
    AuditLog,
    Call,
    CallDirection,
    CallStatus,
    Environment,
    Lead,
    Tenant,
    UserRole,
)
from tests.conftest import auth_headers, make_tenant, make_user


def _path(call_id: uuid.UUID) -> str:
    return f"/api/workflows/calls/{call_id}/outcome-writeback"


async def _environment(db: AsyncSession, tenant: Tenant) -> Environment:
    row = await db.scalar(
        select(Environment).where(
            Environment.tenant_id == tenant.id, Environment.kind == "production"
        )
    )
    assert row is not None
    return row


async def _fresh(db: AsyncSession) -> None:
    """End the test session's read transaction.

    The routes run on their own session from the same engine, so a read in
    this session that started earlier would keep returning its own snapshot
    and miss what the request just committed.
    """
    await db.rollback()


async def _call(
    db: AsyncSession,
    tenant: Tenant,
    environment: Environment,
    **overrides: object,
) -> Call:
    started = datetime.now(timezone.utc) - timedelta(minutes=3)
    fields: dict[str, object] = {
        "tenant_id": tenant.id,
        "environment_id": environment.id,
        "call_sid": f"CA{uuid.uuid4().hex}",
        "direction": CallDirection.INBOUND,
        "status": CallStatus.COMPLETED,
        "from_number": "+15551230000",
        "to_number": tenant.twilio_number,
        "started_at": started,
        "ended_at": started + timedelta(seconds=95),
        "summary": "Caller booked a Tuesday cleaning.",
    }
    fields.update(overrides)
    call = Call(**fields)  # type: ignore[arg-type]
    db.add(call)
    await db.commit()
    await db.refresh(call)
    return call


# ------------------------------------------------------------- validation ---


@pytest.mark.asyncio
async def test_an_idempotency_key_is_required(client: AsyncClient, db: AsyncSession) -> None:
    tenant = await make_tenant(db, name="wb-nokey")
    owner = await make_user(db, tenant, UserRole.OWNER, email="wb-nokey@example.com")
    headers = await auth_headers(client, owner)
    environment = await _environment(db, tenant)
    call = await _call(db, tenant, environment)

    response = await client.post(
        _path(call.id), json={"target": "calendar"}, headers=headers
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "idempotency_key_required"


@pytest.mark.asyncio
async def test_a_too_short_idempotency_key_is_refused(client: AsyncClient, db: AsyncSession) -> None:
    tenant = await make_tenant(db, name="wb-shortkey")
    owner = await make_user(db, tenant, UserRole.OWNER, email="wb-shortkey@example.com")
    headers = await auth_headers(client, owner)
    environment = await _environment(db, tenant)
    call = await _call(db, tenant, environment)

    response = await client.post(
        _path(call.id),
        json={"target": "calendar"},
        headers={**headers, "Idempotency-Key": "short"},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_an_unknown_target_is_refused_before_any_work(client: AsyncClient, db: AsyncSession) -> None:
    tenant = await make_tenant(db, name="wb-target")
    owner = await make_user(db, tenant, UserRole.OWNER, email="wb-target@example.com")
    headers = await auth_headers(client, owner)
    environment = await _environment(db, tenant)
    call = await _call(db, tenant, environment)

    response = await client.post(
        _path(call.id),
        json={"target": "carrier_pigeon"},
        headers={**headers, "Idempotency-Key": "wb-target-key-0001"},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_another_tenants_call_is_a_404(client: AsyncClient, db: AsyncSession) -> None:
    ours = await make_tenant(db, name="wb-ours")
    theirs = await make_tenant(db, name="wb-theirs")
    owner = await make_user(db, ours, UserRole.OWNER, email="wb-scope@example.com")
    headers = await auth_headers(client, owner)
    their_environment = await _environment(db, theirs)
    their_call = await _call(db, theirs, their_environment)

    response = await client.post(
        _path(their_call.id),
        json={"target": "calendar"},
        headers={**headers, "Idempotency-Key": "wb-scope-key-0001"},
    )

    assert response.status_code == 404


# --------------------------------------------------------------- calendar ---


@pytest.mark.asyncio
async def test_calendar_writeback_with_no_booking_is_skipped_not_succeeded(
    client: AsyncClient, db: AsyncSession
) -> None:
    """A target that did nothing must not be reported as having done it."""
    tenant = await make_tenant(db, name="wb-nobooking")
    owner = await make_user(db, tenant, UserRole.OWNER, email="wb-nobooking@example.com")
    headers = await auth_headers(client, owner)
    environment = await _environment(db, tenant)
    call = await _call(db, tenant, environment)

    response = await client.post(
        _path(call.id),
        json={"target": "calendar", "payload": {"disposition": "completed"}},
        headers={**headers, "Idempotency-Key": "wb-nobooking-key-001"},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["target"] == "calendar"
    assert body["status"] == "skipped"
    assert body["replayed"] is False
    assert body["results"][0]["status"] == "skipped"
    assert body["results"][0]["error_code"] == "no_appointment_for_call"


@pytest.mark.asyncio
async def test_calendar_writeback_appends_the_outcome_to_the_appointment(
    client: AsyncClient, db: AsyncSession
) -> None:
    tenant = await make_tenant(db, name="wb-appointment")
    owner = await make_user(db, tenant, UserRole.OWNER, email="wb-appointment@example.com")
    headers = await auth_headers(client, owner)
    environment = await _environment(db, tenant)
    call = await _call(db, tenant, environment)
    appointment = Appointment(
        tenant_id=tenant.id,
        call_id=call.id,
        customer_name="Jane Doe",
        customer_phone="+15557778888",
        reason="Drain cleaning",
        starts_at=datetime.now(timezone.utc) + timedelta(days=1),
        ends_at=datetime.now(timezone.utc) + timedelta(days=1, minutes=30),
        notes="Booked over the phone.",
    )
    db.add(appointment)
    await db.commit()
    appointment_id = appointment.id

    response = await client.post(
        _path(call.id),
        json={
            "target": "calendar",
            "payload": {"disposition": "completed", "summary": "Confirmed the slot."},
        },
        headers={**headers, "Idempotency-Key": "wb-appointment-key-01"},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "succeeded"
    assert body["results"][0]["target"] == "calendar:appointment"
    assert body["results"][0]["writeback_id"] == str(appointment_id)

    await _fresh(db)
    # Read the column, not the entity: the session's identity map still holds
    # the row as the test left it, which would hide what the request wrote.
    notes = await db.scalar(
        select(Appointment.notes).where(Appointment.id == appointment_id)
    )
    assert "Call outcome: completed" in (notes or "")
    assert "Confirmed the slot." in (notes or "")
    # The pre-existing note is preserved, not overwritten.
    assert "Booked over the phone." in (notes or "")


@pytest.mark.asyncio
async def test_a_replay_returns_the_stored_outcome_and_does_not_write_twice(
    client: AsyncClient, db: AsyncSession
) -> None:
    """The whole point of the receipt: a retry cannot duplicate the effect."""
    tenant = await make_tenant(db, name="wb-replay")
    owner = await make_user(db, tenant, UserRole.OWNER, email="wb-replay@example.com")
    headers = await auth_headers(client, owner)
    environment = await _environment(db, tenant)
    call = await _call(db, tenant, environment)
    appointment = Appointment(
        tenant_id=tenant.id,
        call_id=call.id,
        customer_name="Replay Customer",
        customer_phone="+15557770000",
        reason="Follow-up",
        starts_at=datetime.now(timezone.utc) + timedelta(days=1),
        ends_at=datetime.now(timezone.utc) + timedelta(days=1, minutes=30),
    )
    db.add(appointment)
    await db.commit()
    appointment_id = appointment.id

    key = "wb-replay-key-0001"
    first = await client.post(
        _path(call.id),
        json={"target": "calendar", "payload": {"disposition": "completed"}},
        headers={**headers, "Idempotency-Key": key},
    )
    assert first.status_code == 201, first.text
    await _fresh(db)
    after_first = (
        await db.scalar(select(Appointment.notes).where(Appointment.id == appointment_id))
    ) or ""

    second = await client.post(
        _path(call.id),
        json={"target": "calendar", "payload": {"disposition": "completed"}},
        headers={**headers, "Idempotency-Key": key},
    )

    assert second.status_code == 201, second.text
    body = second.json()
    assert body["replayed"] is True
    assert body["idempotency_key"] == key
    assert body["status"] == first.json()["status"]

    await _fresh(db)
    await _fresh(db)
    after_second = (
        await db.scalar(select(Appointment.notes).where(Appointment.id == appointment_id))
    ) or ""
    # The note was appended once. A second append would mean the effect ran twice.
    assert after_second == after_first
    assert after_second.count("Call outcome:") == 1


@pytest.mark.asyncio
async def test_a_different_key_on_the_same_call_writes_a_second_note(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Idempotency is per key, so a genuinely new request still does the work."""
    tenant = await make_tenant(db, name="wb-twokeys")
    owner = await make_user(db, tenant, UserRole.OWNER, email="wb-twokeys@example.com")
    headers = await auth_headers(client, owner)
    environment = await _environment(db, tenant)
    call = await _call(db, tenant, environment)
    appointment = Appointment(
        tenant_id=tenant.id,
        call_id=call.id,
        customer_name="Two Keys",
        customer_phone="+15557770001",
        reason="Second call",
        starts_at=datetime.now(timezone.utc) + timedelta(days=1),
        ends_at=datetime.now(timezone.utc) + timedelta(days=1, minutes=30),
    )
    db.add(appointment)
    await db.commit()
    appointment_id = appointment.id

    for key in ("wb-twokeys-key-0001", "wb-twokeys-key-0002"):
        response = await client.post(
            _path(call.id),
            json={"target": "calendar", "payload": {"disposition": "completed"}},
            headers={**headers, "Idempotency-Key": key},
        )
        assert response.status_code == 201, response.text
        assert response.json()["replayed"] is False

    await _fresh(db)
    notes = (
        await db.scalar(select(Appointment.notes).where(Appointment.id == appointment_id))
    ) or ""
    assert notes.count("Call outcome:") == 2


@pytest.mark.asyncio
async def test_the_same_key_with_a_different_body_is_a_conflict(
    client: AsyncClient, db: AsyncSession
) -> None:
    tenant = await make_tenant(db, name="wb-conflict")
    owner = await make_user(db, tenant, UserRole.OWNER, email="wb-conflict@example.com")
    headers = await auth_headers(client, owner)
    environment = await _environment(db, tenant)
    call = await _call(db, tenant, environment)
    key = "wb-conflict-key-0001"

    first = await client.post(
        _path(call.id),
        json={"target": "calendar", "payload": {"disposition": "completed"}},
        headers={**headers, "Idempotency-Key": key},
    )
    assert first.status_code == 201, first.text

    second = await client.post(
        _path(call.id),
        json={"target": "calendar", "payload": {"disposition": "no_show"}},
        headers={**headers, "Idempotency-Key": key},
    )

    assert second.status_code == 409


# ------------------------------------------------------------------- crm ---


@pytest.mark.asyncio
async def test_a_crm_target_with_no_connection_reports_skipped(
    client: AsyncClient, db: AsyncSession
) -> None:
    """No CRM connected means nothing was written, and the answer says so."""
    tenant = await make_tenant(db, name="wb-nocrm")
    owner = await make_user(db, tenant, UserRole.OWNER, email="wb-nocrm@example.com")
    headers = await auth_headers(client, owner)
    environment = await _environment(db, tenant)
    call = await _call(db, tenant, environment)

    response = await client.post(
        _path(call.id),
        json={"target": "crm", "payload": {"disposition": "completed"}},
        headers={**headers, "Idempotency-Key": "wb-nocrm-key-0001"},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "skipped"
    assert body["results"][0]["error_code"] == "no_active_connection"
    assert "crm" in body["results"][0]["error"]


@pytest.mark.asyncio
async def test_a_webhook_target_with_no_endpoint_reports_skipped(
    client: AsyncClient, db: AsyncSession
) -> None:
    tenant = await make_tenant(db, name="wb-nowebhook")
    owner = await make_user(db, tenant, UserRole.OWNER, email="wb-nowebhook@example.com")
    headers = await auth_headers(client, owner)
    environment = await _environment(db, tenant)
    call = await _call(db, tenant, environment)

    response = await client.post(
        _path(call.id),
        json={"target": "webhook", "payload": {"disposition": "completed"}},
        headers={**headers, "Idempotency-Key": "wb-nowebhook-key-001"},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["results"][0]["status"] == "skipped"
    assert body["results"][0]["error_code"] == "webhook_not_configured"


# ----------------------------------------------------------------- audit ---


@pytest.mark.asyncio
async def test_the_writeback_is_audited_with_its_per_target_outcomes(
    client: AsyncClient, db: AsyncSession
) -> None:
    tenant = await make_tenant(db, name="wb-audit")
    owner = await make_user(db, tenant, UserRole.OWNER, email="wb-audit@example.com")
    headers = await auth_headers(client, owner)
    environment = await _environment(db, tenant)
    call = await _call(db, tenant, environment)
    tenant_id = tenant.id
    call_id = call.id

    response = await client.post(
        _path(call_id),
        json={"target": "calendar"},
        headers={**headers, "Idempotency-Key": "wb-audit-key-00001"},
    )
    assert response.status_code == 201, response.text

    await _fresh(db)
    audit = (
        await db.scalars(
            select(AuditLog).where(
                AuditLog.tenant_id == tenant_id,
                AuditLog.event_type == "workflow.outcome_writeback",
            )
        )
    ).all()
    assert len(audit) == 1
    assert audit[0].resource_type == "call"
    assert str(audit[0].resource_id) == str(call_id)
    assert audit[0].detail["target"] == "calendar"


@pytest.mark.asyncio
async def test_writebacks_require_the_integration_sync_permission(
    client: AsyncClient, db: AsyncSession
) -> None:
    tenant = await make_tenant(db, name="wb-rbac")
    viewer = await make_user(db, tenant, UserRole.VIEWER, email="wb-viewer@example.com")
    headers = await auth_headers(client, viewer)
    environment = await _environment(db, tenant)
    call = await _call(db, tenant, environment)

    response = await client.post(
        _path(call.id),
        json={"target": "calendar"},
        headers={**headers, "Idempotency-Key": "wb-rbac-key-000001"},
    )

    assert response.status_code == 403


# ----------------------------------------------------------------- caller ---


@pytest.mark.asyncio
async def test_the_caller_email_comes_from_the_linked_lead_and_is_never_guessed(
    db: AsyncSession
) -> None:
    """A wrong email silently attaches the call to the wrong contact."""
    tenant = await make_tenant(db, name="wb-lead")
    environment = await _environment(db, tenant)

    lead = Lead(
        tenant_id=tenant.id,
        environment_id=environment.id,
        name="Linked Caller",
        phone="+15551230000",
        email="linked@example.com",
    )
    db.add(lead)
    await db.commit()
    await db.refresh(lead)

    call = await _call(db, tenant, environment, lead_id=lead.id)

    from app.api.workflow_event_routes import _caller_email

    assert await _caller_email(db, call) == "linked@example.com"

    unlinked = await _call(db, tenant, environment)
    assert await _caller_email(db, unlinked) == ""


@pytest.mark.asyncio
async def test_the_duration_is_derived_from_the_call_timestamps(
    db: AsyncSession,
) -> None:
    tenant = await make_tenant(db, name="wb-duration")
    environment = await _environment(db, tenant)
    call = await _call(db, tenant, environment)

    from app.api.workflow_event_routes import _duration_seconds

    assert _duration_seconds(call) == 95

    open_call = await _call(db, tenant, environment, ended_at=None)
    assert _duration_seconds(open_call) == 0
