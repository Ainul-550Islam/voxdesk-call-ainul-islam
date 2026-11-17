"""PART 13 / P13-12 — the six new disconnection reasons, and failed inbound rows.

A call log that says "failed" is a call log a customer cannot act on. These six
reasons exist so the traffic report can answer "how many calls did the carrier
block last month?" without a support engineer reading raw provider errors.

Two things are tested here:

* the mapping from SIP final responses and provider error codes onto those six
  names, including the honest default for a code we have never seen;
* the rule that a failed *inbound* call still creates a history row, because the
  phone rang and the customer has it in their log.
"""

from __future__ import annotations

import pytest

from app.telephony.qos import (
    PART13_REASONS,
    DisconnectionReason,
    SIP_STATUS_REASONS,
    explain_reason,
    reason_breakdown,
    reason_from_provider_error,
    reason_from_sip_status,
    record_failed_inbound,
)


def test_the_six_new_reasons_exist_by_name() -> None:
    assert set(PART13_REASONS) == {
        "invalid_destination",
        "telephony_provider_permission_denied",
        "telephony_provider_unavailable",
        "sip_routing_error",
        "marked_as_spam",
        "user_declined",
    }
    for name in PART13_REASONS:
        assert DisconnectionReason(name).value == name


def test_a_missing_number_is_an_invalid_destination() -> None:
    assert reason_from_sip_status(404) is DisconnectionReason.INVALID_DESTINATION
    assert reason_from_sip_status(484) is DisconnectionReason.INVALID_DESTINATION


def test_a_refused_trunk_is_a_permission_problem_not_a_busy_line() -> None:
    assert (
        reason_from_sip_status(403)
        is DisconnectionReason.TELEPHONY_PROVIDER_PERMISSION_DENIED
    )
    assert (
        reason_from_sip_status(401)
        is DisconnectionReason.TELEPHONY_PROVIDER_PERMISSION_DENIED
    )


def test_a_dead_gateway_is_provider_unavailable() -> None:
    assert (
        reason_from_sip_status(503) is DisconnectionReason.TELEPHONY_PROVIDER_UNAVAILABLE
    )
    assert reason_from_sip_status(408) is DisconnectionReason.TELEPHONY_PROVIDER_UNAVAILABLE


def test_a_routing_failure_is_its_own_reason() -> None:
    assert reason_from_sip_status(485) is DisconnectionReason.SIP_ROUTING_ERROR
    assert reason_from_sip_status(502) is DisconnectionReason.SIP_ROUTING_ERROR


def test_a_spam_block_is_named_as_such() -> None:
    assert reason_from_sip_status(606) is DisconnectionReason.MARKED_AS_SPAM
    assert reason_from_provider_error("13224") is DisconnectionReason.MARKED_AS_SPAM


def test_a_callee_decline_is_not_a_busy_line() -> None:
    assert reason_from_sip_status(603) is DisconnectionReason.USER_DECLINED
    assert reason_from_sip_status(486) is DisconnectionReason.USER_BUSY


def test_an_unmapped_sip_code_defaults_to_provider_unavailable() -> None:
    # Honest default: "something at the carrier failed" is true; inventing a
    # reason would be worse than admitting we do not know.
    assert reason_from_sip_status(499) is DisconnectionReason.TELEPHONY_PROVIDER_UNAVAILABLE


def test_provider_error_codes_map_onto_the_same_names() -> None:
    assert (
        reason_from_provider_error("21211") is DisconnectionReason.INVALID_DESTINATION
    )
    assert (
        reason_from_provider_error("unverified_number")
        is DisconnectionReason.TELEPHONY_PROVIDER_PERMISSION_DENIED
    )
    assert reason_from_provider_error("declined") is DisconnectionReason.USER_DECLINED


def test_a_provider_error_can_override_the_default() -> None:
    assert (
        reason_from_provider_error("something-new", default=DisconnectionReason.USER_BUSY)
        is DisconnectionReason.USER_BUSY
    )


def test_every_reason_has_a_sentence() -> None:
    for reason in DisconnectionReason:
        text = explain_reason(reason)
        assert text and not text.endswith("_"), reason
        assert "_" not in text


def test_an_unknown_reason_does_not_leak_the_enum_name() -> None:
    assert explain_reason("carrier_at_it_again") == "Call ended"


def test_the_sip_table_only_uses_known_reasons() -> None:
    for status, reason in SIP_STATUS_REASONS.items():
        assert isinstance(reason, DisconnectionReason), status


@pytest.mark.asyncio
async def test_a_failed_inbound_call_creates_a_history_row(db) -> None:
    from sqlalchemy import select

    from app.db.models import Call, CallStatus
    from tests.conftest import make_tenant

    tenant = await make_tenant(db, "Reasons Ltd")
    row = await record_failed_inbound(
        db,
        tenant.id,
        call_sid="CA-failed-inbound",
        from_number="+8801712345678",
        to_number="+15550100",
        reason=DisconnectionReason.TELEPHONY_PROVIDER_PERMISSION_DENIED,
    )
    await db.commit()

    stored = (
        await db.execute(select(Call).where(Call.call_sid == "CA-failed-inbound"))
    ).scalar_one_or_none()
    assert stored is not None
    assert stored.status is CallStatus.FAILED
    assert stored.failure_reason == "telephony_provider_permission_denied"
    assert stored.duration_seconds == 0.0
    assert "carrier refused" in (stored.summary or "")
    assert row.id == stored.id


@pytest.mark.asyncio
async def test_failed_inbound_history_is_idempotently_updated(db) -> None:
    from sqlalchemy import func, select

    from app.db.models import Call
    from tests.conftest import make_tenant

    tenant = await make_tenant(db, "Idempotent Reasons Ltd")
    first = await record_failed_inbound(
        db,
        tenant.id,
        call_sid="CA-failed-inbound-retry",
        from_number="+8801712345678",
        to_number="+15550101",
        reason=DisconnectionReason.INVALID_DESTINATION,
    )
    second = await record_failed_inbound(
        db,
        tenant.id,
        call_sid="CA-failed-inbound-retry",
        from_number="+8801712345678",
        to_number="+15550101",
        reason=DisconnectionReason.TELEPHONY_PROVIDER_UNAVAILABLE,
    )
    await db.commit()

    count = await db.scalar(
        select(func.count()).select_from(Call).where(
            Call.tenant_id == tenant.id,
            Call.call_sid == "CA-failed-inbound-retry",
        )
    )
    assert count == 1
    assert first.id == second.id
    assert second.failure_reason == "telephony_provider_unavailable"
    assert second.environment_id is not None


@pytest.mark.asyncio
async def test_the_breakdown_counts_by_reason(db) -> None:
    from tests.conftest import make_tenant

    tenant = await make_tenant(db, "Breakdown Ltd")
    for index, reason in enumerate(
        [
            DisconnectionReason.INVALID_DESTINATION,
            DisconnectionReason.INVALID_DESTINATION,
            DisconnectionReason.MARKED_AS_SPAM,
        ]
    ):
        await record_failed_inbound(
            db,
            tenant.id,
            call_sid=f"CA-breakdown-{index}",
            from_number="+8801712345678",
            to_number="+15550100",
            reason=reason,
        )
    await db.commit()

    counts = await reason_breakdown(db, tenant.id)
    assert counts["invalid_destination"] == 2
    assert counts["marked_as_spam"] == 1
