"""PART 13 / P13-03 — the Vonage scope decision (ADR-004) is enforced, not aspirational.

An ADR that is not tested is a paragraph. These tests keep the capability lists
in `app/telephony/providers/vonage.py` honest:

* the supported list contains only what the adapter really implements;
* every unsupported capability has its own sentence, so the dashboard can say
  *why* rather than showing a bare "not supported";
* WhatsApp is not claimed on a voice carrier.
"""

from __future__ import annotations

import pytest

from app.telephony.providers.vonage import (
    VONAGE_SUPPORTED_CAPABILITIES,
    VONAGE_UNSUPPORTED_CAPABILITIES,
    unsupported_capability,
    vonage_media_capabilities,
)


def test_voice_and_call_control_are_claimed() -> None:
    for capability in ("voice", "call_control", "ncco", "recording", "dtmf"):
        assert capability in VONAGE_SUPPORTED_CAPABILITIES


def test_whatsapp_is_not_claimed_on_a_voice_carrier() -> None:
    assert "whatsapp" in VONAGE_UNSUPPORTED_CAPABILITIES


def test_media_streaming_is_never_silently_claimed() -> None:
    row = vonage_media_capabilities()["vonage"]
    if row["supported"]:
        # Credentials present: the row must say what is claimed, not just "yes".
        assert row["serializer"] == "VonageFrameSerializer"
        assert "ADR-004" in row["reason"]
    else:
        assert row["serializer"] is None
        assert row["encodings"] == []
        assert "ADR-004" in row["reason"] or "not enabled" in row["reason"]


def test_the_row_lists_both_sides_of_the_decision() -> None:
    row = vonage_media_capabilities()["vonage"]
    assert sorted(row["supported_capabilities"]) == sorted(VONAGE_SUPPORTED_CAPABILITIES)
    assert row["unsupported_capabilities"] == VONAGE_UNSUPPORTED_CAPABILITIES


def test_every_unsupported_capability_explains_itself() -> None:
    for capability, reason in VONAGE_UNSUPPORTED_CAPABILITIES.items():
        assert reason.strip(), capability
        assert len(reason) > 30, f"{capability} needs a sentence, not a label"
        assert not reason.endswith("_"), capability


def test_an_unknown_capability_is_named_in_the_refusal() -> None:
    message = unsupported_capability("teleportation")
    assert "teleportation" in message


@pytest.mark.parametrize("capability", sorted(VONAGE_UNSUPPORTED_CAPABILITIES))
def test_the_refusal_is_stable(capability: str) -> None:
    assert unsupported_capability(capability) == VONAGE_UNSUPPORTED_CAPABILITIES[capability]
