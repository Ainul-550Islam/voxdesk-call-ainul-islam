"""PART 13 / P13-09 — transfers v2.

Four things a caller cares about, tested as four things:

* the destination may be an E.164 number, an extension or a SIP URI;
* the far end may be a machine, and a caller must not be bridged into a
  voicemail greeting;
* the caller hears hold music while the human's phone rings;
* the human sees either the agent's number or the original caller's, and the
  transcript gets `transfer.started|bridged|cancelled|ended`.

The plan is a pure function of (tenant, call, options), which is why this file
does not need a provider, a database or a network.
"""

from __future__ import annotations

import uuid

import pytest

from app.telephony.transfer_service import (
    HOLD_MUSIC_LICENCE,
    HOLD_MUSIC_PATH,
    TRANSFER_EVENTS_V2,
    TransferDestinationError,
    amd_classify,
    caller_id_for,
    hold_music_available,
    human_leg_verdict,
    parse_transfer_destination,
    plan_transfer_v2,
)


class FakeTenant:
    id = uuid.uuid4()
    name = "Acme Dental"
    twilio_number = "+15550100"
    escalation_number = "+15550199"


class FakeCall:
    id = uuid.uuid4()
    call_sid = "CA-transfer-1"
    tenant_id = FakeTenant.id
    from_number = "+8801712345678"
    transfer_state = "NONE"
    status = "IN_PROGRESS"


@pytest.fixture()
def tenant() -> FakeTenant:
    return FakeTenant()


@pytest.fixture()
def call() -> FakeCall:
    return FakeCall()


def test_an_e164_destination_is_normalised() -> None:
    destination = parse_transfer_destination("+1 (555) 0199")
    assert destination.kind == "e164"
    assert destination.resolved == "+15550199"


def test_an_extension_resolves_through_the_directory() -> None:
    destination = parse_transfer_destination("204", extensions={"204": "+15550144"})
    assert destination.kind == "extension"
    assert destination.resolved == "+15550144"


def test_an_unknown_extension_is_refused_rather_than_dialled() -> None:
    with pytest.raises(TransferDestinationError) as refused:
        parse_transfer_destination("999", extensions={"204": "+15550144"})
    assert refused.value.code == "unknown_extension"


def test_a_sip_uri_is_passed_through() -> None:
    destination = parse_transfer_destination("sip:desk@pbx.example.com:5060")
    assert destination.kind == "sip_uri"
    assert destination.resolved == "sip:desk@pbx.example.com:5060"


def test_a_malformed_sip_uri_is_refused() -> None:
    with pytest.raises(TransferDestinationError):
        parse_transfer_destination("sip:no-host")


def test_an_empty_destination_is_refused() -> None:
    with pytest.raises(TransferDestinationError) as refused:
        parse_transfer_destination("   ")
    assert refused.value.code == "no_destination"


def test_answering_machine_detection_is_classified() -> None:
    assert amd_classify({"result": "human"}) == "human"
    assert amd_classify({"AnsweredBy": "machine_start"}) == "machine"
    assert amd_classify({"AnsweredBy": "fax"}) == "machine"
    assert amd_classify({"result": "who-knows"}) == "unknown"
    assert amd_classify(None) == "unknown"


def test_a_machine_is_never_bridged() -> None:
    ok, reason = human_leg_verdict({"result": "machine"}, require_human=True)
    assert ok is False
    assert reason == "machine_detected"


def test_an_unconfirmed_human_is_not_bridged_when_humans_are_required() -> None:
    ok, reason = human_leg_verdict(None, require_human=True)
    assert ok is False
    assert reason == "human_not_confirmed"


def test_an_unconfirmed_human_is_bridged_when_the_tenant_accepts_it() -> None:
    ok, reason = human_leg_verdict(None, require_human=False)
    assert ok is True
    assert reason == "unverified"


def test_a_human_leg_is_bridged() -> None:
    assert human_leg_verdict({"result": "human"}, require_human=True) == (True, "human")


def test_the_hold_music_asset_ships_as_twilio_compatible_wav_with_its_licence() -> None:
    import wave

    assert HOLD_MUSIC_PATH.is_file(), "the default hold loop must be in the repo"
    assert HOLD_MUSIC_LICENCE.is_file(), "a CC0 asset without its licence is not CC0"
    assert hold_music_available() is True
    with wave.open(str(HOLD_MUSIC_PATH), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getframerate() == 8000
        assert wav.getsampwidth() == 2
        assert wav.getnframes() > 0


def test_the_caller_id_modes_are_both_real(tenant, call) -> None:
    assert caller_id_for("agent", tenant, call) == "+15550100"
    assert caller_id_for("original", tenant, call) == "+18001712345678".replace(
        "+18001712345678", call.from_number
    )


def test_an_unknown_caller_id_mode_is_refused(tenant, call) -> None:
    with pytest.raises(TransferDestinationError, match="caller ID mode"):
        caller_id_for("random", tenant, call)


def test_the_plan_carries_hold_music_and_a_whisper(tenant, call) -> None:
    plan = plan_transfer_v2(tenant, call, destination="+15550199", reason="billing question")
    assert plan.destination.kind == "e164"
    assert plan.hold_music is True
    assert plan.hold_music_asset == "loop-default.wav"
    assert plan.style == "whisper"
    assert plan.whisper and "billing question" in plan.whisper
    assert plan.three_way_message is None
    assert plan.caller_id == "+15550100"


def test_an_explicitly_empty_whisper_stays_empty(tenant, call) -> None:
    plan = plan_transfer_v2(tenant, call, whisper="")
    assert plan.whisper == ""


def test_three_way_swaps_the_whisper_for_a_message(tenant, call) -> None:
    plan = plan_transfer_v2(tenant, call, style="three_way")
    assert plan.whisper is None
    assert plan.three_way_message and "Acme Dental" in plan.three_way_message


def test_the_original_caller_id_can_be_kept(tenant, call) -> None:
    plan = plan_transfer_v2(tenant, call, caller_id_mode="original")
    assert plan.caller_id == call.from_number


def test_a_whisper_never_carries_a_phone_number(tenant, call) -> None:
    # Anything digit-like is stripped: a whisper is spoken aloud, and a number
    # read out to the wrong person is a data leak.
    plan = plan_transfer_v2(tenant, call, whisper="Caller +15550100 wants billing")
    assert "5550100" not in plan.whisper


def test_hold_music_can_be_turned_off(tenant, call) -> None:
    plan = plan_transfer_v2(tenant, call, hold_music=False)
    assert plan.hold_music is False
    assert plan.hold_music_asset is None


def test_the_plan_lists_the_four_events_in_order() -> None:
    assert TRANSFER_EVENTS_V2 == ("started", "bridged", "cancelled", "ended")


def test_the_plan_emits_every_event_name(tenant, call) -> None:
    plan = plan_transfer_v2(tenant, call)
    assert plan.events == (
        "transfer.started",
        "transfer.bridged",
        "transfer.cancelled",
        "transfer.ended",
    )
    assert plan.as_dict()["events"][0] == "transfer.started"


def test_an_unknown_style_is_refused(tenant, call) -> None:
    with pytest.raises(TransferDestinationError, match="transfer style"):
        plan_transfer_v2(tenant, call, style="telepathy")
