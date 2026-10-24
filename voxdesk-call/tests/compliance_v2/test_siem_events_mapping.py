"""The audit row -> SIEM event mapping.

Every sink ships this mapping's output to a third party, so the checks that
matter are: the fields a security analyst needs are all present and correctly
typed, the schema version is pinned, the watermark used by the dispatcher is
monotonic, and nothing secret can reach the destination.
"""

from __future__ import annotations

import datetime as dt
import uuid

from app.audit.service import record_event
from app.compliance_v2.siem.events import (
    SCHEMA_VERSION,
    SiemEvent,
    from_audit_log,
    is_after_watermark,
    probe_event,
    watermark_of,
)
from app.db.models import AuditLog

UTC = dt.timezone.utc


async def _audit_row(db, tenant, **kwargs) -> AuditLog:  # noqa: ANN001
    detail = kwargs.pop("detail", {"note": "hello"})
    await record_event(
        db,
        tenant_id=tenant.id,
        actor_user_id=kwargs.pop("actor_user_id", None),
        event_type=kwargs.pop("event_type", "call.completed"),
        resource_type=kwargs.pop("resource_type", "call"),
        resource_id=kwargs.pop("resource_id", uuid.uuid4()),
        detail=detail,
        **kwargs,
    )
    await db.commit()
    row = (
        await db.execute(
            AuditLog.__table__.select().order_by(AuditLog.created_at.desc()).limit(1)
        )
    ).first()
    assert row is not None
    return row


async def test_mapping_carries_the_fields_an_analyst_needs(db, tenant_a):
    row = await _audit_row(db, tenant_a, event_type="call.completed")

    event = from_audit_log(_as_orm(row), chain_hash="a" * 64)

    assert event.event_id == str(row.id)
    assert event.tenant_id == str(tenant_a.id)
    assert event.event_type == "call.completed"
    assert event.schema_version == SCHEMA_VERSION
    assert event.occurred_at.endswith("Z")
    assert event.chain_hash == "a" * 64
    assert event.detail["note"] == "hello"


def _as_orm(row) -> AuditLog:  # noqa: ANN001
    """Materialise a plain row into the ORM object the mapper expects."""
    return AuditLog(**dict(row._mapping))


def test_the_schema_version_is_pinned():
    assert SCHEMA_VERSION == "voxdesk.siem.audit.v1"


def test_a_probe_event_is_labelled_as_synthetic():
    event = probe_event(tenant_id="t-1", actor="suite")

    assert event.event_type == "siem_sink_probe"
    assert event.detail["probe"] is True
    assert event.actor_id == "suite"


def test_no_secret_shaped_key_survives_the_mapping():
    row = AuditLog(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        actor_user_id=None,
        actor_type="system",
        event_type="integration.updated",
        action="integration_updated",
        result="success",
        resource_type="integration",
        resource_id=str(uuid.uuid4()),
        created_at=dt.datetime.now(UTC),
        detail={
            "api_key": "sk-live-should-never-ship",
            "password": "hunter2",
            "note": "rotated the token",
        },
    )

    event = from_audit_log(row)
    serialised = event.canonical_json()

    assert "sk-live-should-never-ship" not in serialised
    assert "hunter2" not in serialised
    assert event.detail["api_key"] == "[REDACTED]"


def test_watermark_is_the_created_at_and_id_pair():
    moment = dt.datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    row = AuditLog(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        event_type="call.completed",
        action="call_completed",
        result="success",
        created_at=moment,
        detail={},
    )

    occurred_at, row_id = watermark_of(row)

    assert occurred_at == moment
    assert row_id == row.id


def test_watermark_comparison_is_cursor_safe():
    base = dt.datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    first = uuid.UUID("00000000-0000-0000-0000-000000000001")
    second = uuid.UUID("00000000-0000-0000-0000-000000000002")

    def after(moment: dt.datetime, row_id: uuid.UUID, at: dt.datetime, wid: uuid.UUID) -> bool:
        return is_after_watermark(moment, row_id, watermark_at=at, watermark_id=wid)

    assert after(base + dt.timedelta(seconds=1), second, base, first)
    assert not after(base, first, base, first)
    # Same instant, higher id: the row that arrived later in the same
    # millisecond must still be delivered.
    assert after(base, second, base, first)
    assert not after(base, first, base, second)
    assert after(base, second, None, None)


def test_a_machine_actor_is_preserved(db, tenant_a):
    row = AuditLog(
        id=uuid.uuid4(),
        tenant_id=tenant_a.id,
        actor_user_id=None,
        actor_type="api_key",
        event_type="api_key.used",
        action="api_key_used",
        result="success",
        resource_type="api_key",
        resource_id=str(uuid.uuid4()),
        created_at=dt.datetime.now(UTC),
        detail={"key_id": "k-1"},
    )

    event = from_audit_log(row)

    assert event.actor_type == "api_key"
    assert isinstance(event, SiemEvent)
