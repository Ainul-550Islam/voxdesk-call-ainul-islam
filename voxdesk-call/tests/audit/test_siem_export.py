"""The audit export a SIEM receives (PART 16, P16-06).

What a reviewer needs to believe, and what each test therefore proves:

* the *envelope* is stable and self-describing (schema version, batch id, tenant,
  count, and the ids in the batch) so a parser can be written once;
* **nothing sensitive leaves** — the field set is an allowlist, and a password,
  token or API key written into ``detail`` by a careless caller is redacted
  rather than shipped to a third party;
* the **signature is over the bytes as sent**, verifies across key rotation, and
  fails closed on an unknown key id, a stale timestamp, or a body altered in
  transit;
* the **cursor** resumes without skipping or repeating rows, including rows that
  share a timestamp — the classic silent data-loss bug in audit export;
* a tenant only ever sees its own rows, whatever filters are passed.

Rows are inserted directly rather than through `record_event` because the tests
are about the *export*, not the writer; the writer has its own suite.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

import app.db.models  # noqa: F401
from app.audit import siem_export as siem
from app.db.models import AuditAction, AuditLog, Tenant

pytestmark = pytest.mark.asyncio

KEY = "siem-test-key-0123456789abcdef"
KEY_ID = "siem-2026-11"


def _row(
    tenant: Tenant,
    *,
    created_at: datetime | None = None,
    detail: dict | None = None,
    event_type: str = "auth.login",
    action: AuditAction = AuditAction.LOGIN_SUCCESS,
    actor_email: str = "someone@example.com",
) -> AuditLog:
    return AuditLog(
        tenant_id=tenant.id,
        action=action,
        event_type=event_type,
        actor_type="human",
        actor_email=actor_email,
        ip_address="203.0.113.9",
        user_agent="pytest/1.0",
        result="success",
        detail=detail or {},
        created_at=created_at or datetime.now(timezone.utc),
    )


async def _seed(db, tenant: Tenant, count: int, *, base: datetime | None = None) -> list[AuditLog]:
    start = base or datetime(2026, 11, 1, tzinfo=timezone.utc)
    rows = []
    for index in range(count):
        row = _row(tenant, created_at=start + timedelta(seconds=index))
        db.add(row)
        rows.append(row)
    await db.commit()
    return rows


# ----------------------------------------------------------------- envelope ---


async def test_a_batch_has_a_stable_envelope_and_verifies(db, tenant_a):
    await _seed(db, tenant_a, 3)
    batch = await siem.export_batch(
        db, tenant_id=tenant_a.id, key=KEY, key_id=KEY_ID, limit=10
    )
    assert len(batch) == 3
    assert batch.complete is True
    assert siem.verify(batch.frame, batch.header, keys={KEY_ID: KEY}) is True

    lines = batch.frame.decode().rstrip("\n").split("\n")
    head = json.loads(lines[0])
    assert head["schema"] == siem.SCHEMA_VERSION
    assert head["tenant_id"] == str(tenant_a.id)
    assert head["count"] == 3
    assert len(head["record_ids"]) == 3
    assert len(lines) == 4  # header + three records, no trailing blank line
    record = json.loads(lines[1])
    assert set(record) == set(siem.EXPORTED_FIELDS) | {"detail"}
    assert record["occurred_at"].endswith("Z")


async def test_the_field_set_is_an_allowlist(db, tenant_a):
    """A column that is not named in EXPORTED_FIELDS never reaches the export."""
    row = _row(tenant_a, detail={"role": "admin"})
    db.add(row)
    await db.commit()
    record = siem.project_row(row)
    assert set(record) == set(siem.EXPORTED_FIELDS) | {"detail"}
    assert "tenant_id" in record and "detail" in record


async def test_ordering_is_deterministic_and_keyset_stable(db, tenant_a):
    stamp = datetime(2026, 11, 2, tzinfo=timezone.utc)
    for _ in range(5):  # identical timestamps: only the id breaks the tie
        db.add(_row(tenant_a, created_at=stamp))
    await db.commit()

    first = await siem.export_batch(db, tenant_id=tenant_a.id, key=KEY, key_id=KEY_ID, limit=3)
    second = await siem.export_batch(
        db, tenant_id=tenant_a.id, cursor=first.next_cursor, key=KEY, key_id=KEY_ID, limit=3
    )
    ids = [record["id"] for record in first.records + second.records]
    assert len(ids) == 5 and len(set(ids)) == 5, "keyset pagination lost or repeated a row"
    assert second.complete is True


async def test_an_empty_ledger_is_a_valid_empty_frame(db, tenant_a):
    batch = await siem.export_batch(db, tenant_id=tenant_a.id, key=KEY, key_id=KEY_ID)
    assert len(batch) == 0
    assert batch.complete is True
    assert batch.next_cursor is None
    assert siem.verify(batch.frame, batch.header, keys={KEY_ID: KEY}) is True


# ------------------------------------------------------------------ secrecy ---


async def test_a_secret_in_detail_never_leaves(db, tenant_a):
    row = _row(
        tenant_a,
        detail={
            "password": "hunter2",
            "api_key": "sk-live-abcdef123456",
            "nested": {"client_secret": "shhh", "role": "admin"},
            "note": "card on file is 4111 1111 1111 1111",
        },
        actor_email="real.person@example.com",
    )
    db.add(row)
    await db.commit()

    batch = await siem.export_batch(db, tenant_id=tenant_a.id, key=KEY, key_id=KEY_ID)
    body = batch.frame.decode()
    for secret in ("hunter2", "sk-live-abcdef123456", "shhh", "4111 1111 1111 1111"):
        assert secret not in body
    detail = json.loads(body.strip().split("\n")[1])["detail"]
    assert detail["nested"]["role"] == "admin"
    assert detail["nested"]["client_secret"].upper() == "[REDACTED]"
    assert "real.person@example.com" not in body, "the actor email is PII and must be masked"


async def test_a_known_secret_value_is_scrubbed_from_free_text(db, tenant_a, monkeypatch):
    monkeypatch.setattr(siem, "configured_secret_values", lambda: ("super-secret-value",))
    row = _row(tenant_a, detail={"note": "used super-secret-value for the relay"})
    db.add(row)
    await db.commit()
    batch = await siem.export_batch(db, tenant_id=tenant_a.id, key=KEY, key_id=KEY_ID)
    assert "super-secret-value" not in batch.frame.decode()


async def test_a_removed_name_cannot_be_smuggled_under_a_new_key(db, tenant_a):
    """Sensitive-key variants are redacted without brittle substring checks."""
    secrets = {
        "X-API-KEY": "x-api-key-sensitive-sentinel-91f0",
        "apiKey": "api-key-sensitive-sentinel-c24b",
        "Authorization": "Bearer auth-sensitive-sentinel-60de",
    }
    row = _row(tenant_a, detail=secrets)
    db.add(row)
    await db.commit()
    body = (
        await siem.export_batch(db, tenant_id=tenant_a.id, key=KEY, key_id=KEY_ID)
    ).frame.decode()
    exported = json.loads(body.strip().split("\n")[1])["detail"]
    assert all(exported[key] == "[REDACTED]" for key in secrets)
    assert all(value not in body for value in secrets.values())


# --------------------------------------------------------------- signature ---


async def test_a_rotated_key_verifies_and_an_unknown_key_does_not(db, tenant_a):
    await _seed(db, tenant_a, 1)
    batch = await siem.export_batch(db, tenant_id=tenant_a.id, key=KEY, key_id=KEY_ID)
    assert siem.verify(batch.frame, batch.header, keys={KEY_ID: KEY, "siem-2026-12": "next"})
    assert siem.verify(batch.frame, batch.header, keys={"siem-2026-12": "next"}) is False


async def test_a_tampered_body_fails_and_a_truncated_body_fails(db, tenant_a):
    await _seed(db, tenant_a, 2)
    batch = await siem.export_batch(db, tenant_id=tenant_a.id, key=KEY, key_id=KEY_ID)
    tampered = batch.frame.replace(b"auth.login", b"auth.logout")
    assert siem.verify(tampered, batch.header, keys={KEY_ID: KEY}) is False
    assert siem.verify(batch.frame[:-10], batch.header, keys={KEY_ID: KEY}) is False


async def test_a_stale_frame_is_refused(db, tenant_a):
    await _seed(db, tenant_a, 1)
    issued = 1_700_000_000
    batch = await siem.export_batch(
        db, tenant_id=tenant_a.id, key=KEY, key_id=KEY_ID, now=issued
    )
    assert siem.verify(batch.frame, batch.header, keys={KEY_ID: KEY}, now=issued + 5)
    assert not siem.verify(
        batch.frame, batch.header, keys={KEY_ID: KEY}, now=issued + siem.MAX_CLOCK_SKEW_SECONDS + 1
    )


async def test_signing_requires_a_key():
    with pytest.raises(siem.ExportError):
        siem.sign(b"{}", key="", key_id=KEY_ID)


# ------------------------------------------------------------------ filters ---


async def test_another_tenants_events_are_unreachable(db, tenant_a, tenant_b):
    await _seed(db, tenant_a, 2)
    await _seed(db, tenant_b, 3)
    batch = await siem.export_batch(db, tenant_id=tenant_a.id, key=KEY, key_id=KEY_ID, limit=50)
    assert len(batch) == 2
    assert {record["tenant_id"] for record in batch.records} == {str(tenant_a.id)}


async def test_event_type_prefix_and_window_filters(db, tenant_a):
    await _seed(db, tenant_a, 1, base=datetime(2026, 11, 1, tzinfo=timezone.utc))
    db.add(_row(tenant_a, event_type="billing.invoice_paid", action=AuditAction.ROLE_CHANGED,
                created_at=datetime(2026, 11, 5, tzinfo=timezone.utc)))
    db.add(_row(tenant_a, event_type="billing.refund", action=AuditAction.ROLE_CHANGED,
                created_at=datetime(2026, 11, 6, tzinfo=timezone.utc)))
    await db.commit()

    billing = await siem.export_batch(
        db, tenant_id=tenant_a.id, key=KEY, key_id=KEY_ID, action_prefix="billing."
    )
    assert len(billing) == 2

    window = await siem.export_batch(
        db,
        tenant_id=tenant_a.id,
        key=KEY,
        key_id=KEY_ID,
        since=datetime(2026, 11, 4, tzinfo=timezone.utc),
        until=datetime(2026, 11, 6, tzinfo=timezone.utc),
    )
    assert [record["event_type"] for record in window.records] == ["billing.invoice_paid"]


async def test_the_limit_is_bounded(db, tenant_a):
    with pytest.raises(siem.ExportError):
        await siem.export_batch(db, tenant_id=tenant_a.id, key=KEY, key_id=KEY_ID, limit=0)
    with pytest.raises(siem.ExportError):
        await siem.export_batch(
            db, tenant_id=tenant_a.id, key=KEY, key_id=KEY_ID, limit=siem.MAX_ROWS + 1
        )


async def test_a_made_up_cursor_is_refused(db, tenant_a):
    with pytest.raises(siem.ExportError):
        await siem.export_batch(
            db, tenant_id=tenant_a.id, key=KEY, key_id=KEY_ID, cursor="not-a-cursor"
        )


# ------------------------------------------------------------------- paging ---


async def test_export_all_pages_to_the_end_and_flags_the_last_batch(db, tenant_a):
    await _seed(db, tenant_a, 7)
    batches = await siem.export_all(
        db, tenant_id=tenant_a.id, key=KEY, key_id=KEY_ID, limit=3, max_batches=10
    )
    assert [len(batch) for batch in batches] == [3, 3, 1]
    assert batches[-1].complete is True
    ids = [record["id"] for batch in batches for record in batch.records]
    assert len(ids) == len(set(ids)) == 7


async def test_export_all_stops_at_its_batch_bound(db, tenant_a):
    await _seed(db, tenant_a, 10)
    batches = await siem.export_all(
        db, tenant_id=tenant_a.id, key=KEY, key_id=KEY_ID, limit=2, max_batches=2
    )
    assert len(batches) == 2
    assert batches[-1].complete is False, "the caller must be told there is more to read"


async def test_a_replayed_cursor_returns_the_same_rows(db, tenant_a):
    await _seed(db, tenant_a, 4)
    first = await siem.export_batch(db, tenant_id=tenant_a.id, key=KEY, key_id=KEY_ID, limit=2)
    again = await siem.export_batch(
        db, tenant_id=tenant_a.id, cursor=first.next_cursor, key=KEY, key_id=KEY_ID, limit=2
    )
    replay = await siem.export_batch(
        db, tenant_id=tenant_a.id, cursor=first.next_cursor, key=KEY, key_id=KEY_ID, limit=2
    )
    assert [r["id"] for r in again.records] == [r["id"] for r in replay.records]
    assert again.header["signature"] != "" and siem.verify(
        replay.frame, replay.header, keys={KEY_ID: KEY}
    )
