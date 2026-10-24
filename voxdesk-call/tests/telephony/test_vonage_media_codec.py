"""The Vonage live media codec: L16 at 16 kHz, the 20 ms chunking and the
outbound pacer that keeps the stream jitter safe.

The assertions are exact byte counts, so a change in the framing is a
change in the wire format the carrier expects."""

from __future__ import annotations

import json

import pytest

from app.telephony.providers.vonage_media import (
    VONAGE_FRAME_BYTES,
    OutboundPacer,
    VonageFrameSerializer,
    VonageMediaState,
    chunk_l16,
    decode_media_payload,
    l16_16k_to_pcm16_8k,
    pcm16_8k_to_l16_16k,
    resample_pcm16,
)


def test_8k_to_16k_doubles_the_length() -> None:
    pcm = b"\x00\x01" * 160
    l16 = pcm16_8k_to_l16_16k(pcm)
    assert len(l16) == len(pcm) * 2


def test_16k_to_8k_halves_the_length() -> None:
    l16 = b"\x00\x01" * 320
    pcm = l16_16k_to_pcm16_8k(l16)
    assert len(pcm) == len(l16) // 2


def test_round_trip_preserves_a_constant_signal() -> None:
    # A constant signal survives the interpolation exactly.
    pcm = b"\x10\x00" * 160
    l16 = pcm16_8k_to_l16_16k(pcm)
    back = l16_16k_to_pcm16_8k(l16)
    assert back == pcm


def test_resample_rejects_an_unsupported_ratio() -> None:
    with pytest.raises(ValueError):
        resample_pcm16(b"\x00\x00", source_rate=8000, target_rate=44100)


def test_resample_is_identity_for_equal_rates() -> None:
    pcm = b"\x01\x02\x03\x04"
    assert resample_pcm16(pcm, source_rate=8000, target_rate=8000) is pcm


def test_chunk_l16_splits_into_20ms_frames() -> None:
    payload = b"\x00" * (VONAGE_FRAME_BYTES * 2 + 10)
    chunks = chunk_l16(payload)
    assert len(chunks) == 3
    assert len(chunks[0]) == VONAGE_FRAME_BYTES
    assert len(chunks[2]) == 10


def test_chunk_l16_of_an_empty_payload_is_empty() -> None:
    assert chunk_l16(b"") == []


@pytest.mark.asyncio
async def test_pacer_counts_the_frames_it_served() -> None:
    pacer = OutboundPacer(frame_ms=20)
    await pacer.pace()
    await pacer.pace()
    assert pacer.frames_sent == 2


def test_pacer_reset_forgets_the_clock() -> None:
    pacer = OutboundPacer(frame_ms=20)
    pacer.reset()
    assert pacer.frames_sent == 0


def test_media_state_hands_out_whole_frames() -> None:
    state = VonageMediaState()
    state.buffer(b"\x00" * (VONAGE_FRAME_BYTES + 5))
    assert state.take() == b"\x00" * VONAGE_FRAME_BYTES
    # The trailing partial frame stays in the buffer.
    assert state.take() == b""


def test_media_state_clear_drops_queued_audio() -> None:
    state = VonageMediaState()
    state.buffer(b"\x00" * 100)
    assert state.clear() == 100
    assert state.take() == b""


@pytest.mark.asyncio
async def test_serializer_round_trips_audio() -> None:
    serializer = VonageFrameSerializer(sample_rate=8000, stream_sid="s1", call_sid="c1")
    frame = await serializer.serialize(_output_frame(b"\x01\x02" * 160))
    assert isinstance(frame, (bytes, bytearray))
    decoded = await serializer.deserialize(bytes(frame))
    assert decoded is not None
    assert decoded.audio == b"\x01\x02" * 160


@pytest.mark.asyncio
async def test_serializer_decodes_a_control_event() -> None:
    serializer = VonageFrameSerializer(sample_rate=8000, stream_sid="s1", call_sid="c1")
    payload = json.dumps({"event": "dtmf", "digit": "5"})
    decoded = await serializer.deserialize(payload)
    assert decoded is not None
    assert decoded.__class__.__name__ == "InputDTMFFrame"


@pytest.mark.asyncio
async def test_serializer_ignores_a_malformed_control_event() -> None:
    serializer = VonageFrameSerializer(sample_rate=8000, stream_sid="s1", call_sid="c1")
    assert await serializer.deserialize("{not json") is None
    assert await serializer.deserialize(json.dumps({"event": "unknown"})) is None


def test_decode_media_payload_reads_base64() -> None:
    import base64

    raw = b"\x01\x02\x03"
    encoded = base64.b64encode(raw).decode("ascii")
    assert decode_media_payload(encoded) == raw


def _output_frame(audio: bytes):
    from pipecat.frames.frames import OutputAudioRawFrame

    return OutputAudioRawFrame(audio=audio, sample_rate=8000, num_channels=1)
