"""Normalized provider and number capabilities.

A flag is true only when the provider payload or the server configuration
confirms it. An absent flag stays false. That is "not confirmed", not a claim
that the country or provider lacks the feature forever.
"""

from __future__ import annotations

from dataclasses import dataclass

KNOWN = frozenset({"voice", "sms", "mms", "whatsapp", "recording", "transcription"})

#: Live call-control capabilities. They are separate from the media/transport
#: flags above because a carrier can stream audio without offering a verified
#: way to park a caller on hold, and "the vendor sells it" is not the same as
#: "this deployment confirmed it". Both default to False: unconfirmed is False,
#: never True.
CONTROL_KNOWN = frozenset({"supports_hold", "supports_cancel_transfer"})


@dataclass(frozen=True)
class CapabilitySet:
    voice: bool = False
    sms: bool = False
    mms: bool = False
    whatsapp: bool = False
    recording: bool = False
    transcription: bool = False
    #: Provider-confirmed ability to redirect a live call to hold TwiML and
    #: back again (pause/resume).
    supports_hold: bool = False
    #: Provider-confirmed ability to pull a live call back from an in-flight
    #: transfer leg and return it to the AI media stream.
    supports_cancel_transfer: bool = False
    country: str = ""
    region: str = ""
    source: str = "unconfirmed"

    def allows(self, name: str) -> bool:
        if name not in KNOWN:
            return False
        return bool(getattr(self, name))

    def allows_control(self, name: str) -> bool:
        if name not in CONTROL_KNOWN:
            return False
        return bool(getattr(self, name))

    def as_dict(self) -> dict:
        return {
            "voice": self.voice,
            "sms": self.sms,
            "mms": self.mms,
            "whatsapp": self.whatsapp,
            "recording": self.recording,
            "transcription": self.transcription,
            "supports_hold": self.supports_hold,
            "supports_cancel_transfer": self.supports_cancel_transfer,
            "country": self.country,
            "region": self.region,
            "source": self.source,
        }


def from_provider_features(
    features: list | tuple | None,
    *,
    country: str = "",
    region: str = "",
    source: str = "provider_api",
) -> CapabilitySet:
    """Build a set from names the provider actually returned.

    Unknown names are ignored. WhatsApp is not inferred from SMS.
    """
    names = {str(item).strip().lower() for item in (features or [])}
    return CapabilitySet(
        voice="voice" in names,
        sms="sms" in names,
        mms="mms" in names,
        whatsapp="whatsapp" in names,
        recording="recording" in names,
        transcription="transcription" in names,
        # A carrier inventory payload never confirms call-control capabilities;
        # only the deployment's own adapter may set them.
        supports_hold=False,
        supports_cancel_transfer=False,
        country=country[:8],
        region=region[:32],
        source=source if features else "unconfirmed",
    )


def from_mapping(raw: dict | None, *, source: str) -> CapabilitySet:
    data = raw or {}
    return CapabilitySet(
        voice=bool(data.get("voice")),
        sms=bool(data.get("sms")),
        mms=bool(data.get("mms")),
        whatsapp=bool(data.get("whatsapp")),
        recording=bool(data.get("recording")),
        transcription=bool(data.get("transcription")),
        supports_hold=bool(data.get("supports_hold")),
        supports_cancel_transfer=bool(data.get("supports_cancel_transfer")),
        country=str(data.get("country") or "")[:8],
        region=str(data.get("region") or "")[:32],
        source=source,
    )
