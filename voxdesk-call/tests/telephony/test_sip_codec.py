"""The SIP codec: the exact G.711 tables, the 8 kHz to 16 kHz resampling
and the RFC 2833 telephone event parser."""

from __future__ import annotations

import pytest

from app.telephony.sip.sip_codec import (
    alaw_to_linear,
    encode_telephone_event,
    g711_to_pcm16,
    parse_telephone_event,
    pcm16_to_g711,
    pcm16_to_pcma,
    pcm16_to_pcmu,
    resample_8k_to_16k,
    resample_16k_to_8k,
    resample_pcm16,
    pcma_to_pcm16,
    pcmu_to_pcm16,
    ulaw_to_linear,
    linear_to_alaw,
    linear_to_ulaw,
)


def test_pcmu_round_trip() -> None:
    pcm = b"\x00\x10" * 160
    encoded = pcm16_to_pcmu(pcm)
    assert len(encoded) == len(pcm) // 2
    decoded = pcmu_to_pcm16(encoded)
    # mu-law is lossy; the error stays inside a few LSBs.
    assert len(decoded) == len(pcm)
    assert _max_error(pcm, decoded) <= 64


def test_pcma_round_trip() -> None:
    pcm = b"\x00\x10" * 160
    encoded = pcm16_to_pcma(pcm)
    assert len(encoded) == len(pcm) // 2
    decoded = pcma_to_pcm16(encoded)
    assert len(decoded) == len(pcm)
    # A-law quantises to 256 steps at this amplitude.
    assert _max_error(pcm, decoded) <= 256


def test_g711_reference_vectors() -> None:
    # The zero codes and the clipping points of ITU-T G.711.
    assert alaw_to_linear(0xD5) > 0
    assert alaw_to_linear(0x55) < 0
    assert ulaw_to_linear(0xFF) == 0
    assert linear_to_ulaw(0) == 0xFF
    assert linear_to_ulaw(-1) == 0x7E
    assert linear_to_alaw(0) == 0xD5


def test_g711_dispatch_honours_the_codec() -> None:
    pcm = b"\x00\x10" * 80
    assert g711_to_pcm16(pcm16_to_pcmu(pcm), "PCMU") == pcmu_to_pcm16(
        pcm16_to_pcmu(pcm)
    )
    assert g711_to_pcm16(pcm16_to_pcma(pcm), "PCMA") == pcma_to_pcm16(
        pcm16_to_pcma(pcm)
    )


def test_g711_rejects_an_unknown_codec() -> None:
    with pytest.raises(ValueError):
        g711_to_pcm16(b"\x00", "OPUS")
    with pytest.raises(ValueError):
        pcm16_to_g711(b"\x00\x00", "OPUS")


def test_resampling_doubles_and_halves() -> None:
    pcm = b"\x00\x10" * 160
    up = resample_8k_to_16k(pcm)
    assert len(up) == len(pcm) * 2
    down = resample_16k_to_8k(up)
    assert len(down) == len(pcm)


def test_resample_rejects_an_unsupported_ratio() -> None:
    with pytest.raises(ValueError):
        resample_pcm16(b"\x00\x00", source_rate=8000, target_rate=44100)


def test_telephone_event_round_trip() -> None:
    payload = encode_telephone_event(event=5, end=True, volume=10, duration_ms=160)
    assert len(payload) == 4
    parsed = parse_telephone_event(payload)
    assert parsed is not None
    assert parsed.event == 5
    assert parsed.end is True
    assert parsed.volume == 10
    assert parsed.duration_ms == 160


def test_telephone_event_rejects_a_short_payload() -> None:
    assert parse_telephone_event(b"\x00\x01") is None


def _max_error(left: bytes, right: bytes) -> int:
    import array

    a = array.array("h")
    a.frombytes(left)
    b = array.array("h")
    b.frombytes(right)
    return max(abs(int(x) - int(y)) for x, y in zip(a, b))


# ---------------------------------------------------------------------------
# The media protocol
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_serializer_speaks_binary_media_and_json_control() -> None:
    """The audio travels as binary frames, the control events as JSON."""
    from pipecat.frames.frames import InputAudioRawFrame, OutputAudioRawFrame
    from pipecat.serializers.base_serializer import FrameSerializerType

    from app.telephony.sip.sip_handler import SipG711Serializer

    serializer = SipG711Serializer(stream_sid="call-1", call_sid="call-1", codec="PCMU")
    assert serializer.type == FrameSerializerType.BINARY

    # Outbound: the pipeline audio becomes the raw G.711 payload.
    pcm = b"\x00\x10" * 160
    payload = await serializer.serialize(
        OutputAudioRawFrame(audio=pcm, sample_rate=8000, num_channels=1)
    )
    assert isinstance(payload, (bytes, bytearray))
    assert bytes(payload) == pcm16_to_pcmu(pcm)

    # Inbound: the raw G.711 payload becomes pipeline audio again.
    frame = await serializer.deserialize(bytes(payload))
    assert isinstance(frame, InputAudioRawFrame)
    assert frame.sample_rate == 8000
    assert frame.num_channels == 1
    # mu-law is lossy, so the round trip is compared with the decoded
    # payload rather than with the original samples.
    assert frame.audio == pcmu_to_pcm16(pcm16_to_pcmu(pcm))


@pytest.mark.asyncio
async def test_the_serializer_reads_a_dtmf_control_event() -> None:
    from pipecat.frames.frames import InputDTMFFrame

    from app.telephony.sip.sip_handler import SipG711Serializer

    serializer = SipG711Serializer(codec="PCMU")
    frame = await serializer.deserialize('{"event": "dtmf", "digit": "7"}')
    assert isinstance(frame, InputDTMFFrame)
    assert frame.button.value == "7"


@pytest.mark.asyncio
async def test_the_serializer_ignores_a_malformed_control_event() -> None:
    from app.telephony.sip.sip_handler import SipG711Serializer

    serializer = SipG711Serializer(codec="PCMU")
    assert await serializer.deserialize("not json") is None
    assert await serializer.deserialize("") is None
    assert await serializer.deserialize(b"") is None
