"""Normalized provider and number capabilities.

A flag is true only when the provider payload or the server configuration
confirms it. An absent flag stays false. That is "not confirmed", not a claim
that the country or provider lacks the feature forever.
"""

from __future__ import annotations

from dataclasses import dataclass

KNOWN = frozenset({"voice", "sms", "mms", "whatsapp", "recording", "transcription"})


@dataclass(frozen=True)
class CapabilitySet:
    voice: bool = False
    sms: bool = False
    mms: bool = False
    whatsapp: bool = False
    recording: bool = False
    transcription: bool = False
    country: str = ""
    region: str = ""
    source: str = "unconfirmed"

    def allows(self, name: str) -> bool:
        if name not in KNOWN:
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
        country=str(data.get("country") or "")[:8],
        region=str(data.get("region") or "")[:32],
        source=source,
    )
