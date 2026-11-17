"""PART 13 / P13-01 — the RTP <-> WebSocket PCM bridge.

The bridge is the only component that touches raw packets, so it is tested at
that level: a real G.711 payload in, a real PCM frame out, a real DTMF packet to
a named event. No sockets, no network, no provider.

What is checked:

* G.711 mu-law and A-law decode to the ITU reference values, and the round trip
  keeps an 8 kHz signal recognisable;
* the jitter buffer plays in order, refuses late audio and never grows past its
  bound;
* an RFC 2833 keypress becomes exactly one start and one end event.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import struct
import sys
from pathlib import Path

import pytest

BRIDGE_DIR = Path(__file__).resolve().parents[2] / "services" / "sip-gateway"
if str(BRIDGE_DIR) not in sys.path:
    sys.path.insert(0, str(BRIDGE_DIR))

from bridge import codecs, dtmf, jitter, rtp  # noqa: E402
from bridge.main import RtpEndpoint, _session_headers  # noqa: E402
from bridge.session import FRAME_DTMF, BridgeSession, split_frames  # noqa: E402


def _event(event: int, duration: int, *, end: bool = False) -> bytes:
    return struct.pack(">BBH", event, 0x80 if end else 0x00, duration)


# ------------------------------------------------------------------ process


def test_media_handshake_uses_a_call_bound_hmac() -> None:
    headers = _session_headers("call-handshake", "gateway-secret")
    timestamp = headers["X-VoxDesk-SIP-Timestamp"]
    nonce = headers["X-VoxDesk-SIP-Nonce"]
    expected = hmac.new(
        b"gateway-secret",
        f"call-handshake:{timestamp}:{nonce}".encode(),
        hashlib.sha256,
    ).hexdigest()
    assert headers["Authorization"] == "Bearer gateway-secret"
    assert headers["X-VoxDesk-SIP-Call-Id"] == "call-handshake"
    assert headers["X-VoxDesk-SIP-Session"] == expected


def test_rtp_receive_queue_keeps_fresh_packets_when_full() -> None:
    queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=1)
    queue.put_nowait(b"old")
    endpoint = RtpEndpoint(queue)
    endpoint.datagram_received(b"new", ("127.0.0.1", 40000))
    assert queue.get_nowait() == b"new"
    assert endpoint.dropped_packets == 1


# ------------------------------------------------------------------ codecs


def test_mulaw_decodes_the_itu_reference_values() -> None:
    # 0xFF is silence in mu-law, 0x00 is the largest negative sample.
    assert codecs.MULAW_DECODE[0xFF] == 0
    assert codecs.MULAW_DECODE[0x00] == -32124
    assert codecs.MULAW_DECODE[0x80] == 32124


def test_alaw_decodes_the_itu_reference_values() -> None:
    # A-law inverts the even bits only, so the sign pair is 0xD5 / 0x55.
    assert codecs.ALAW_DECODE[0xD5] == 8
    assert codecs.ALAW_DECODE[0x55] == -8
    assert codecs._linear_to_alaw(8) == 0xD5
    assert codecs._linear_to_alaw(-8) == 0x55


def test_a_silent_frame_stays_silent_through_the_round_trip() -> None:
    pcm = codecs.to_pcm16(b"\xff" * codecs.FRAME_SAMPLES, "PCMU")
    assert len(pcm) == codecs.FRAME_SAMPLES * 4, "160 samples at 8 kHz become 320 at 16 kHz"
    back = codecs.from_pcm16(pcm, "PCMU")
    assert len(back) == codecs.FRAME_SAMPLES
    assert set(back) == {0xFF}


def test_a_sine_wave_survives_the_round_trip_within_g711_tolerance() -> None:
    import math

    samples = [
        int(8000 * math.sin(2 * math.pi * 440 * index / codecs.RTP_CLOCK_RATE))
        for index in range(codecs.FRAME_SAMPLES)
    ]
    encoded = codecs.encode_mulaw(samples)
    decoded = codecs.decode_mulaw(encoded)
    error = sum(abs(a - b) for a, b in zip(samples, decoded)) / len(samples)
    assert error < 400, f"G.711 is lossy, but not that lossy: mean error {error:.0f}"


def test_alaw_and_mulaw_disagree_on_the_same_byte() -> None:
    payload = bytes(range(0, 32))
    assert codecs.decode_mulaw(payload) != codecs.decode_alaw(payload)


def test_a_non_g711_codec_is_refused() -> None:
    with pytest.raises(ValueError, match="G.711"):
        codecs.codec_for("opus")
    assert codecs.codec_for("PCMA") == ("PCMA", 8)


def test_downsampling_halves_the_sample_count() -> None:
    assert len(codecs.downsample_2x([0] * 320)) == 160


# --------------------------------------------------------------------- rtp


def test_a_packet_round_trips() -> None:
    packet = rtp.build_packet(
        b"payload", payload_type=0, sequence=42, timestamp=1600, ssrc=99, marker=True
    )
    parsed = rtp.parse_packet(packet)
    assert parsed.sequence == 42
    assert parsed.timestamp == 1600
    assert parsed.ssrc == 99
    assert parsed.marker is True
    assert parsed.payload == b"payload"
    assert parsed.is_telephone_event is False


def test_a_telephone_event_packet_is_recognised() -> None:
    packet = rtp.build_packet(
        _event(5, 320), payload_type=101, sequence=1, timestamp=0, ssrc=1
    )
    assert rtp.parse_packet(packet).is_telephone_event is True


def test_a_truncated_packet_is_refused() -> None:
    with pytest.raises(ValueError, match="shorter than its header"):
        rtp.parse_packet(b"\x80\x00")


def test_a_wrong_version_is_refused() -> None:
    packet = bytearray(
        rtp.build_packet(b"", payload_type=0, sequence=0, timestamp=0, ssrc=0)
    )
    packet[0] = 0x40  # version 1
    with pytest.raises(ValueError, match="version"):
        rtp.parse_packet(bytes(packet))


def test_the_sequence_gap_wraps() -> None:
    assert rtp.sequence_gap(65534, 1) == 3
    assert rtp.sequence_gap(10, 11) == 1


def test_header_csrc_extension_and_padding_are_parsed() -> None:
    fixed = struct.pack(">BBHII", 0xB1, 0x80, 7, 800, 9)  # v2, P, X, one CSRC
    packet = (
        fixed
        + b"\x00\x00\x00\x01"  # CSRC
        + struct.pack(">HH", 0xBEDE, 1)  # extension profile, one 32-bit word
        + b"ext!"
        + b"hello\x00\x00\x03"  # payload plus three padding bytes
    )
    parsed = rtp.parse_packet(packet)
    assert parsed.payload == b"hello"
    assert parsed.marker is True
    assert parsed.sequence == 7


def test_truncated_csrc_and_extension_data_are_refused() -> None:
    csrc_truncated = struct.pack(">BBHII", 0x81, 0, 1, 2, 3)
    with pytest.raises(ValueError, match="CSRC"):
        rtp.parse_packet(csrc_truncated)

    extension_header_truncated = struct.pack(">BBHII", 0x90, 0, 1, 2, 3)
    with pytest.raises(ValueError, match="extension"):
        rtp.parse_packet(extension_header_truncated)

    extension_data_truncated = (
        struct.pack(">BBHII", 0x90, 0, 1, 2, 3) + struct.pack(">HH", 0xBEDE, 1)
    )
    with pytest.raises(ValueError, match="extension is truncated"):
        rtp.parse_packet(extension_data_truncated)


def test_invalid_rtp_padding_is_refused() -> None:
    base = struct.pack(">BBHII", 0xA0, 0, 1, 2, 3)  # v2 with padding bit
    with pytest.raises(ValueError, match="nonzero"):
        rtp.parse_packet(base + b"\x00")
    with pytest.raises(ValueError, match="exceeds"):
        rtp.parse_packet(base + b"\x02")


# ------------------------------------------------------------------ jitter


def test_the_buffer_plays_in_order() -> None:
    buffer = jitter.JitterBuffer(depth=2)
    buffer.push(1, b"one")
    buffer.push(2, b"two")
    assert buffer.pop() == b"one"
    # Depth is the target occupancy, so the next packet waits for one more.
    assert buffer.pop() is None
    buffer.push(3, b"three")
    assert buffer.pop() == b"two"


def test_the_buffer_waits_until_it_has_enough() -> None:
    buffer = jitter.JitterBuffer(depth=3)
    buffer.push(1, b"one")
    assert buffer.pop() is None, "playing immediately is how jitter becomes stutter"
    buffer.push(2, b"two")
    buffer.push(3, b"three")
    assert buffer.pop() == b"one"


def test_late_audio_is_dropped_not_replayed() -> None:
    buffer = jitter.JitterBuffer(depth=2)
    buffer.push(10, b"a")
    buffer.push(11, b"b")
    assert buffer.pop() == b"a"
    assert buffer.push(9, b"late") is False
    assert buffer.stats()["late"] == 1


def test_a_gap_widens_the_window_and_loss_is_counted() -> None:
    buffer = jitter.JitterBuffer(depth=2)
    buffer.push(1, b"one")
    buffer.push(3, b"three")
    buffer.push(4, b"four")
    assert buffer.pop() == b"one"
    # Sequence 2 is lost; once the buffer has enough later media, skip it.
    assert buffer.pop() == b"three", "skip forward rather than replaying stale audio"
    assert buffer.stats()["lost"] == 1
    assert buffer.depth >= 3


def test_packets_are_emitted_in_sequence_order_not_arrival_order() -> None:
    buffer = jitter.JitterBuffer(depth=3)
    buffer.push(100, b"one")
    buffer.push(102, b"three")
    buffer.push(101, b"two")
    assert buffer.pop() == b"one"
    assert buffer.pop() == b"two"
    buffer.push(103, b"four")
    assert buffer.pop() == b"three"


def test_sequence_wrap_is_forward_progress() -> None:
    buffer = jitter.JitterBuffer(depth=2)
    buffer.push(65535, b"last")
    buffer.push(0, b"zero")
    assert buffer.pop() == b"last"
    buffer.push(1, b"one")
    assert buffer.pop() == b"zero"
    buffer.push(2, b"two")
    assert buffer.pop() == b"one"
    assert buffer.stats()["late"] == 0


def test_duplicate_packets_are_not_played_twice() -> None:
    buffer = jitter.JitterBuffer(depth=2)
    buffer.push(1, b"one")
    assert buffer.push(1, b"duplicate") is False
    buffer.push(2, b"two")
    assert buffer.pop() == b"one"
    assert buffer.stats()["duplicates"] == 1


def test_the_jitter_window_stays_bounded_under_a_packet_flood() -> None:
    buffer = jitter.JitterBuffer(depth=jitter.MAX_DEPTH)
    assert buffer.push(0, b"first")
    for sequence in range(1, jitter.MAX_DEPTH + 3):
        buffer.push(sequence, b"packet")
    assert buffer.buffered == jitter.MAX_DEPTH
    assert buffer.stats()["overflow"] > 0


def test_the_window_never_exceeds_its_bound() -> None:
    buffer = jitter.JitterBuffer()
    for _ in range(100):
        buffer.widen()
    assert buffer.depth == jitter.MAX_DEPTH


def test_the_window_never_collapses() -> None:
    buffer = jitter.JitterBuffer()
    for _ in range(100):
        buffer.narrow()
    assert buffer.depth == jitter.MIN_DEPTH


def test_flush_empties_the_buffer_in_sequence_order() -> None:
    buffer = jitter.JitterBuffer(depth=2)
    buffer.push(1, b"one")
    buffer.push(2, b"two")
    assert buffer.flush() == [b"one", b"two"]
    assert buffer.buffered == 0


# -------------------------------------------------------------------- dtmf


def test_one_keypress_becomes_one_start_and_one_end() -> None:
    decoder = dtmf.DtmfDecoder()
    # A carrier's first packet may carry duration 0: remember it, emit nothing.
    assert decoder.feed(_event(1, 0)) is None
    start = decoder.feed(_event(1, 320))
    assert start.started is True
    # Repeats while the key is held must not produce a second press.
    assert decoder.feed(_event(1, 400)) is None
    end = decoder.feed(_event(1, 480, end=True))
    assert end.ended is True
    assert decoder.digits() == "1"
    assert len([key for key in decoder.events if key.started]) == 1


def test_a_sequence_of_digits_is_reassembled() -> None:
    decoder = dtmf.DtmfDecoder()
    for code in (1, 2, 3, 11):  # "123#"
        decoder.feed(_event(code, 160, end=True))
    assert decoder.digits() == "123#"


def test_a_click_too_short_to_be_a_press_is_ignored() -> None:
    decoder = dtmf.DtmfDecoder()
    assert decoder.feed(_event(5, 40)) is None
    assert decoder.feed(_event(5, 40, end=True)) is None
    assert decoder.digits() == ""


def test_an_unknown_event_code_is_not_a_digit() -> None:
    decoder = dtmf.DtmfDecoder()
    assert decoder.feed(_event(42, 320)) is None
    assert decoder.events == []


def test_a_short_payload_is_refused() -> None:
    with pytest.raises(ValueError, match="four bytes"):
        dtmf.DtmfDecoder().feed(b"\x01")


def test_a_single_ended_packet_emits_one_complete_keypress() -> None:
    decoder = dtmf.DtmfDecoder()
    key = decoder.feed(_event(1, 320, end=True), timestamp=1234)
    assert key is not None and key.started and key.ended
    assert decoder.digits() == "1"
    assert decoder.feed(_event(1, 320, end=True), timestamp=1234) is None
    assert decoder.digits() == "1"


def test_repeated_end_packets_are_deduplicated_but_next_press_is_kept() -> None:
    decoder = dtmf.DtmfDecoder()
    assert decoder.feed(_event(7, 320, end=True), timestamp=500) is not None
    assert decoder.feed(_event(7, 320, end=True), timestamp=500) is None
    assert decoder.feed(_event(7, 320, end=True), timestamp=800) is not None
    assert decoder.digits() == "77"


def test_reserved_telephone_event_flags_are_refused() -> None:
    payload = struct.pack(">BBH", 1, 0x10, 320)
    with pytest.raises(ValueError, match="reserved"):
        dtmf.DtmfDecoder().feed(payload)


# ----------------------------------------------------------------- session


def test_a_session_bridges_audio_packets_to_pcm_frames() -> None:
    session = BridgeSession(call_id="call-1", codec="PCMU")
    frames = []
    for sequence in range(6):
        packet = rtp.build_packet(
            b"\xff" * codecs.FRAME_SAMPLES,
            payload_type=0,
            sequence=sequence,
            timestamp=sequence * 160,
            ssrc=7,
        )
        frames.extend(session.ingest(packet))
    audio, events = split_frames(frames)
    assert audio, "the buffer must have released audio frames"
    assert events == []
    assert all(len(frame) == codecs.FRAME_SAMPLES * 4 for frame in audio)
    assert session.stats()["packets_in"] == 6


def test_a_session_turns_dtmf_packets_into_events() -> None:
    session = BridgeSession(call_id="call-2")
    packet = rtp.build_packet(_event(9, 320), payload_type=101, sequence=1, timestamp=0, ssrc=7)
    frames = session.ingest(packet)
    assert frames and frames[0].kind == FRAME_DTMF
    assert frames[0].meta["digit"] == "9"
    _, events = split_frames(frames)
    assert events[0]["type"] == "dtmf"


def test_dtmf_packets_advance_the_shared_rtp_sequence_without_fake_loss() -> None:
    session = BridgeSession(
        call_id="call-2-sequence",
        buffer=jitter.JitterBuffer(depth=2),
    )

    def audio(sequence: int) -> bytes:
        return rtp.build_packet(
            b"\xff" * codecs.FRAME_SAMPLES,
            payload_type=0,
            sequence=sequence,
            timestamp=sequence * 160,
            ssrc=7,
        )

    session.ingest(audio(10))
    session.ingest(audio(11))
    dtmf_packet = rtp.build_packet(
        _event(5, 320), payload_type=101, sequence=12, timestamp=2000, ssrc=7
    )
    assert any(frame.kind == FRAME_DTMF for frame in session.ingest(dtmf_packet))
    session.ingest(audio(13))
    session.ingest(audio(14))
    assert session.buffer.stats()["lost"] == 0


def test_a_session_rejects_an_unnegotiated_rtp_payload_type() -> None:
    session = BridgeSession(call_id="call-wrong-pt")
    packet = rtp.build_packet(
        b"\x00" * codecs.FRAME_SAMPLES,
        payload_type=8,
        sequence=1,
        timestamp=0,
        ssrc=7,
    )
    with pytest.raises(ValueError, match="unexpected RTP payload type"):
        session.ingest(packet)


def test_egress_produces_a_parseable_rtp_packet() -> None:
    session = BridgeSession(call_id="call-3", codec="PCMA", local_ssrc=4242)
    pcm = codecs.to_pcm16(b"\xd5" * codecs.FRAME_SAMPLES, "PCMA")
    packet = session.egress(pcm, sequence=5, timestamp=800)
    parsed = rtp.parse_packet(packet)
    assert parsed.payload_type == 8
    assert parsed.ssrc == 4242
    assert parsed.payload == b"\xd5" * codecs.FRAME_SAMPLES


def test_a_session_refuses_an_unbridgeable_codec() -> None:
    with pytest.raises(ValueError):
        BridgeSession(call_id="call-4", codec="opus")


def test_a_malformed_packet_does_not_kill_the_session() -> None:
    session = BridgeSession(call_id="call-5")
    with pytest.raises(ValueError):
        session.ingest(b"junk")
    # The process loop counts the packet and carries on; the session is intact.
    good = rtp.build_packet(
        b"\xff" * codecs.FRAME_SAMPLES, payload_type=0, sequence=0, timestamp=0, ssrc=1
    )
    assert session.ingest(good) == []  # still filling the jitter window
    assert session.packets_in == 1
