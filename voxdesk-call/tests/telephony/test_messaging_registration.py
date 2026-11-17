"""PART 13 / P13-08 — A2P 10DLC and toll-free registration as a state machine.

The carrier owns the state; we mirror it. So the tests are about *edges*:

* a campaign cannot be submitted before its brand is approved;
* a retried webhook never moves a registration backwards;
* a terminal state is terminal (except suspension, which the carrier can do);
* the validation rules match the rejections carriers actually send.

The webhook payloads below are the shapes Twilio posts, including the retry that
arrives after the decision.
"""

from __future__ import annotations

import pytest

from app.telephony.messaging_registration import (
    APPROVED,
    DRAFT,
    IN_REVIEW,
    REJECTED,
    SUBMITTED,
    SUSPENDED,
    RegistrationError,
    apply_carrier_status,
    blocked_by,
    can_transition,
    normalise_status,
    next_step,
    plan_submission,
    sms_send_permitted,
    validate_brand,
    validate_business_profile,
    validate_campaign,
    validate_toll_free,
)


BRAND = {
    "brand_name": "Acme Dental",
    "business_profile_id": "bp_1",
    "vertical": "HEALTHCARE",
}

CAMPAIGN = {
    "brand_id": "brand_1",
    "description": "Appointment reminders",
    "message_flow": "Customer opts in at the front desk.",
    "opt_in_flow": "Verbal consent recorded in the CRM.",
    "sample_messages": [
        "Acme Dental: your appointment is tomorrow at 9am. Reply STOP to opt out.",
        "Acme Dental: we missed you today. Reply STOP to opt out.",
    ],
    "use_cases": ["APPOINTMENT_REMINDERS"],
}


def test_the_happy_path_walks_forward() -> None:
    assert apply_carrier_status(DRAFT, "SUBMITTED").state == SUBMITTED
    assert apply_carrier_status(SUBMITTED, "PENDING").state == IN_REVIEW
    assert apply_carrier_status(IN_REVIEW, "APPROVED").state == APPROVED


def test_a_retried_webhook_never_moves_a_registration_backwards() -> None:
    late = apply_carrier_status(APPROVED, "PENDING")
    assert late.applied is False
    assert late.state == APPROVED
    assert late.reason


def test_a_duplicate_event_is_reported_as_ignored() -> None:
    duplicate = apply_carrier_status(APPROVED, "APPROVED")
    assert duplicate.applied is False
    assert duplicate.reason == "duplicate event"


def test_a_rejected_registration_cannot_be_revived_by_a_late_approval() -> None:
    assert apply_carrier_status(REJECTED, "APPROVED").applied is False


def test_a_suspended_brand_can_be_reinstated() -> None:
    assert apply_carrier_status(APPROVED, "SUSPENDED").state == SUSPENDED
    assert apply_carrier_status(SUSPENDED, "APPROVED").state == APPROVED


def test_an_unknown_carrier_status_is_refused() -> None:
    with pytest.raises(RegistrationError, match="not recognised"):
        normalise_status("PROBABLY_FINE")


def test_carrier_spellings_are_normalised() -> None:
    assert normalise_status("approved") == APPROVED
    assert normalise_status("FAILED") == REJECTED
    assert normalise_status("verified") == APPROVED
    assert normalise_status("in review") == IN_REVIEW


def test_the_edge_table_is_closed() -> None:
    assert can_transition(DRAFT, SUBMITTED) is True
    assert can_transition(DRAFT, APPROVED) is False, "a brand cannot skip the carrier"


def test_a_campaign_waits_for_its_brand() -> None:
    blockers = blocked_by("a2p_campaign", {"a2p_brand": IN_REVIEW})
    assert blockers == ["a2p_brand"]
    assert blocked_by("a2p_campaign", {"a2p_brand": APPROVED}) == []


def test_the_submission_plan_lists_every_blocker_at_once() -> None:
    plan = plan_submission(
        "a2p_campaign",
        {"a2p_brand": IN_REVIEW},
        {
            "brand_id": "brand_1",
            "description": "Reaching out to existing clients",
            "message_flow": "test",
            "opt_in_flow": "test",
            "sample_messages": ["Hi there!"],
            "brand_id_2": "x",
        },
    )
    assert plan.allowed is False
    # Three separate problems: the brand, one sample too few, and no opt-out word.
    assert "a2p_brand" in plan.blockers
    assert any("two sample messages" in blocker for blocker in plan.blockers)
    assert any("opt-out" in blocker for blocker in plan.blockers)


def test_a_complete_campaign_is_allowed() -> None:
    plan = plan_submission("a2p_campaign", {"a2p_brand": APPROVED}, CAMPAIGN)
    assert plan.allowed is True
    assert plan.blockers == []


def test_the_next_step_is_a_sentence_not_an_error_code() -> None:
    assert "1-3 business days" in next_step("a2p_brand", IN_REVIEW)
    waiting = next_step("a2p_campaign", DRAFT, {"a2p_brand": SUBMITTED})
    assert "a2p_brand" in waiting


def test_the_business_profile_validates_like_the_carrier() -> None:
    good = validate_business_profile(
        {
            "legal_business_name": "Acme Dental LLC",
            "business_type": "PRIVATE_PROFIT",
            "website": "https://acme.example.com",
            "contact_email": "ops@acme.example.com",
            "ein": "12-3456789",
            "state": "CA",
        }
    )
    assert good.ok is True, good.errors

    bad = validate_business_profile({"ein": "1234", "state": "California"})
    assert bad.ok is False
    assert any("ein must be 9 digits" in error for error in bad.errors)
    assert any("two-letter" in error for error in bad.errors)


def test_a_brand_needs_a_known_vertical() -> None:
    assert validate_brand({**BRAND, "vertical": "CRYPTO"}).ok is False
    assert validate_brand(BRAND).ok is True


def test_an_embedded_link_needs_a_domain() -> None:
    payload = {**CAMPAIGN, "contains_embedded_links": True}
    result = validate_campaign(payload)
    assert any("link_domain" in error for error in result.errors)


def test_a_toll_free_number_must_be_a_us_toll_free() -> None:
    assert validate_toll_free(
        {"phone_number": "+18005551234", "business_name": "Acme", "sample_messages": ["hi"]}
    ).ok is True
    refused = validate_toll_free(
        {"phone_number": "+8801712345678", "business_name": "Acme", "sample_messages": ["hi"]}
    )
    assert refused.ok is False


def test_sms_is_blocked_until_the_campaign_is_approved() -> None:
    allowed, reason = sms_send_permitted({"a2p_campaign": IN_REVIEW})
    assert allowed is False
    assert "blocked" in reason
    assert sms_send_permitted({"a2p_campaign": APPROVED}) == (True, "approved")


def test_toll_free_traffic_checks_the_toll_free_registration() -> None:
    allowed, reason = sms_send_permitted({"a2p_campaign": APPROVED}, toll_free=True)
    assert allowed is False
    assert "toll_free_verification" in reason


def test_an_unknown_kind_is_refused() -> None:
    with pytest.raises(RegistrationError, match="unknown registration kind"):
        plan_submission("carrier_pigeon", {}, {})
