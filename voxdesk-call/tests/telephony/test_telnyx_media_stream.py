"""Telnyx AI media stream: wire frames, stream authentication and flow control.

Everything here is pure CPU work against the real stream object -- no carrier, no
socket, and no mock standing in for a provider. The audio conversion itself is
covered by ``test_telnyx_media_codec.py``.
"""

from __future__ import annotations

import base64
import json
import time

import pytest

from app.core.config import settings
from app.telephony.providers.telnyx_media import SIGNATURE_MAX_SKEW_SECONDS, verify_webhook_signature
from app.telephony.providers.telnyx_media import (
    EVENT_CLEAR,
    EVENT_MEDIA,
    EVENT_START,
    TelnyxMediaStream,
    TelnyxMediaStreamError,
    consume_stream_token,
    issue_stream_token,
    parse_event,
    reset_consumed_tokens,
)

pytestmark = pytest.mark.unit


def _pcm(samples: list[int]) -> bytes:
    return b"".join(int(value).to_bytes(2, "little", signed=True) for value in samples)


def _samples(pcm: bytes) -> list[int]:
    return [
        int.from_bytes(pcm[index : index + 2], "little", signed=True)
        for index in range(0, len(pcm), 2)
    ]


# --------------------------------------------------------------------------- #
# Wire frames
# --------------------------------------------------------------------------- #


def _media_frame(payload: bytes, *, sequence: int | None = None, encoding: str = "PCMU") -> str:
    media: dict[str, object] = {"payload": base64.b64encode(payload).decode("ascii")}
    if sequence is not None:
        media["sequence_number"] = sequence
    return json.dumps(
        {
            "event": "media",
            "stream_id": "stream-1",
            "media": {**media, "encoding": encoding},
        }
    )


def test_parse_start_frame_reads_the_media_format():
    event = parse_event(
        {
            "event": "start",
            "stream_id": "stream-1",
            "start": {
                "call_control_id": "v3:abc",
                "media_format": {"encoding": "PCMU", "sample_rate": 8000, "channels": 1},
            },
        }
    )
    assert event.event == EVENT_START
    assert event.is_start
    assert event.stream_id == "stream-1"
    assert event.call_control_id == "v3:abc"
    assert event.encoding == "PCMU"
    assert event.sample_rate == 8000


def test_parse_media_frame_decodes_the_payload():
    event = parse_event(_media_frame(b"\xff\x80", sequence=7))
    assert event.event == EVENT_MEDIA
    assert event.is_media
    assert event.payload == b"\xff\x80"
    assert event.sequence_number == 7


def test_parse_mark_clear_stop_and_dtmf_frames():
    assert parse_event({"event": "mark", "mark": {"name": "turn-1"}}).mark_name == "turn-1"
    assert parse_event({"event": "clear"}).is_clear
    assert parse_event({"event": "stop"}).is_stop
    assert parse_event({"event": "dtmf", "dtmf": {"digit": "5"}}).digit == "5"


def test_unknown_event_names_are_returned_not_raised():
    event = parse_event({"event": "conference.hold"})
    assert event.event == "conference.hold"
    assert not event.is_media


def test_parse_rejects_bad_json_bad_base64_and_empty_media():
    with pytest.raises(TelnyxMediaStreamError):
        parse_event("{not json")
    with pytest.raises(TelnyxMediaStreamError):
        parse_event({"event": "media", "media": {"payload": "!!!!"}})
    with pytest.raises(TelnyxMediaStreamError):
        parse_event({"event": "media", "media": {}})
    with pytest.raises(TelnyxMediaStreamError):
        parse_event([1, 2, 3])


# --------------------------------------------------------------------------- #
# Stream tokens
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _fresh_tokens():
    reset_consumed_tokens()
    yield
    reset_consumed_tokens()


def test_stream_token_is_single_use():
    token = issue_stream_token("call-1")
    assert consume_stream_token("call-1", token)
    assert not consume_stream_token("call-1", token)


def test_stream_token_is_bound_to_its_call():
    token = issue_stream_token("call-1")
    assert not consume_stream_token("call-2", token)


def test_stream_token_expires():
    issued = int(time.time()) - settings.telnyx_stream_token_ttl_seconds - 5
    token = issue_stream_token("call-1", issued_at=issued)
    assert not consume_stream_token("call-1", token)


def test_missing_or_empty_token_is_refused():
    assert not consume_stream_token("call-1", None)
    assert not consume_stream_token("call-1", "   ")
    with pytest.raises(TelnyxMediaStreamError):
        issue_stream_token("  ")


# --------------------------------------------------------------------------- #
# Stream flow control
# --------------------------------------------------------------------------- #


def _authorized_stream(*, call_id: str = "call-1", max_queued_frames: int = 4) -> TelnyxMediaStream:
    reset_consumed_tokens()
    stream = TelnyxMediaStream(call_id=call_id, max_queued_frames=max_queued_frames)
    assert stream.authorize(issue_stream_token(call_id))
    return stream


def _start(stream: TelnyxMediaStream, encoding: str = "PCMU") -> None:
    stream.handle(
        {
            "event": "start",
            "stream_id": "stream-1",
            "start": {"call_control_id": "v3:abc", "media_format": {"encoding": encoding}},
        }
    )


def test_unauthorized_stream_refuses_every_frame():
    stream = TelnyxMediaStream(call_id="call-1")
    with pytest.raises(TelnyxMediaStreamError) as error:
        stream.handle(_media_frame(b"\xff"))
    assert error.value.code == "stream_unauthorized"
    assert error.value.status_code == 403


def test_an_unknown_token_leaves_the_stream_unauthorized():
    stream = TelnyxMediaStream(call_id="call-1")
    assert stream.authorize("not-a-real-token") is False
    assert not stream.authorized


def test_start_frame_sets_the_negotiated_format():
    stream = _authorized_stream()
    _start(stream, encoding="PCMA")
    assert stream.state.started
    assert stream.state.stream_id == "stream-1"
    assert stream.state.encoding == "PCMA"
    assert stream.state.call_control_id == "v3:abc"


def test_start_frame_with_an_unsupported_encoding_is_refused():
    stream = _authorized_stream()
    with pytest.raises(TelnyxMediaStreamError) as error:
        _start(stream, encoding="OPUS")
    assert error.value.code == "unsupported_encoding"


def test_media_before_start_is_a_conflict():
    stream = _authorized_stream()
    with pytest.raises(TelnyxMediaStreamError) as error:
        stream.handle(_media_frame(b"\xff"))
    assert error.value.code == "stream_not_started"
    assert error.value.status_code == 409


def test_inbound_media_is_counted_and_converted_to_pipeline_pcm():
    stream = _authorized_stream()
    _start(stream)
    event = stream.handle(_media_frame(bytes(160), sequence=1))
    pcm = stream.inbound_pcm(event)
    assert stream.state.frames_in == 1
    assert stream.state.inbound_samples == 160
    assert len(_samples(pcm)) == 320  # 8 kHz payload upsampled to the 16 kHz pipeline


def test_out_of_order_frames_are_counted_not_dropped():
    stream = _authorized_stream()
    _start(stream)
    stream.handle(_media_frame(bytes(160), sequence=5))
    stream.handle(_media_frame(bytes(160), sequence=4))
    assert stream.state.out_of_order_frames == 1
    assert stream.state.frames_in == 2


def test_outbound_frames_carry_the_negotiated_stream_id_and_payload():
    stream = _authorized_stream()
    _start(stream)
    frame = json.loads(stream.media_frame(_pcm([0] * 320)))
    assert frame["event"] == EVENT_MEDIA
    assert frame["stream_id"] == "stream-1"
    assert base64.b64decode(frame["media"]["payload"]) == bytes([0xFF] * 160)
    assert stream.state.frames_out == 1


def test_queue_drains_in_order_and_reports_fullness():
    stream = _authorized_stream(max_queued_frames=2)
    _start(stream)
    assert stream.queue_pcm(_pcm([0] * 320)) is True
    assert stream.queue_pcm(_pcm([1000] * 320)) is True
    assert stream.queue_pcm(_pcm([2000] * 320)) is False
    assert stream.state.dropped_outbound_frames == 1
    assert stream.queued_frames == 2

    frames = [json.loads(item) for item in stream.drain()]
    assert len(frames) == 2
    assert base64.b64decode(frames[0]["media"]["payload"]) == bytes([0xFF] * 160)
    assert base64.b64decode(frames[1]["media"]["payload"]) != bytes([0xFF] * 160)
    assert stream.queued_frames == 0


def test_clear_drops_queued_audio_for_barge_in():
    stream = _authorized_stream()
    _start(stream)
    stream.queue_pcm(_pcm([0] * 320))
    stream.queue_pcm(_pcm([0] * 320))
    frame = json.loads(stream.clear())
    assert frame["event"] == EVENT_CLEAR
    assert stream.queued_frames == 0
    assert stream.state.clears == 1
    assert stream.state.dropped_outbound_frames == 2


def test_a_carrier_clear_frame_also_drops_the_queue():
    stream = _authorized_stream()
    _start(stream)
    stream.queue_pcm(_pcm([0] * 320))
    stream.handle({"event": "clear", "stream_id": "stream-1"})
    assert stream.queued_frames == 0
    assert stream.state.clears == 1


def test_stop_frame_marks_the_stream_stopped():
    stream = _authorized_stream()
    _start(stream)
    stream.handle({"event": "stop", "stream_id": "stream-1"})
    assert stream.state.stopped


def test_mark_requires_a_name_and_is_counted():
    stream = _authorized_stream()
    _start(stream)
    assert json.loads(stream.mark("turn-1"))["mark"]["name"] == "turn-1"
    assert stream.state.marks_sent == 1
    with pytest.raises(TelnyxMediaStreamError):
        stream.mark("  ")


def test_sending_audio_before_start_is_refused_and_empty_frames_are_refused():
    stream = _authorized_stream()
    with pytest.raises(TelnyxMediaStreamError) as error:
        stream.queue_pcm(_pcm([0] * 320))
    assert error.value.code == "stream_not_started"
    _start(stream)
    with pytest.raises(TelnyxMediaStreamError):
        stream.media_frame(b"")


def test_stream_state_serialises_every_counter():
    stream = _authorized_stream()
    _start(stream)
    payload = stream.state.as_dict()
    for key in (
        "stream_id",
        "encoding",
        "sample_rate",
        "authorized",
        "started",
        "frames_in",
        "frames_out",
        "clears",
        "marks_sent",
        "dropped_outbound_frames",
        "out_of_order_frames",
    ):
        assert key in payload
    assert payload["authorized"] is True


def test_a_stream_requires_a_call_id():
    with pytest.raises(TelnyxMediaStreamError):
        TelnyxMediaStream(call_id=" ")


# --------------------------------------------------------------------------- #
# Webhook signature (real Ed25519, not a stub)
# --------------------------------------------------------------------------- #


def _signed(*, body: bytes | None = None, timestamp: int | None = None) -> tuple[str, str, str, bytes]:
    """A real keypair, a real signature: (public_key, signature, timestamp, body)."""
    from nacl.signing import SigningKey

    key = SigningKey.generate()
    public_key = base64.b64encode(bytes(key.verify_key)).decode("ascii")
    payload = body if body is not None else b'{"data":{"event_type":"call.initiated"}}'
    stamp = str(timestamp if timestamp is not None else int(time.time()))
    signature = base64.b64encode(key.sign(f"{stamp}|".encode("utf-8") + payload).signature).decode("ascii")
    return public_key, signature, stamp, payload


async def test_a_valid_signature_is_accepted():
    public_key, signature, timestamp, body = _signed()

    verified, reason = await verify_webhook_signature(
        public_key=public_key, signature=signature, timestamp=timestamp, body=body
    )

    assert verified is True
    assert reason == "telnyx_signature"


async def test_a_tampered_body_is_rejected():
    public_key, signature, timestamp, body = _signed()

    verified, reason = await verify_webhook_signature(
        public_key=public_key, signature=signature, timestamp=timestamp, body=body + b"{}"
    )

    assert verified is False
    assert reason == "signature_rejected"


async def test_a_signature_from_another_key_is_rejected():
    _, signature, timestamp, body = _signed()
    other_public_key, _sig, _ts, _body = _signed()

    verified, reason = await verify_webhook_signature(
        public_key=other_public_key, signature=signature, timestamp=timestamp, body=body
    )

    assert verified is False
    assert reason == "signature_rejected"


async def test_a_stale_timestamp_is_rejected():
    public_key, signature, _timestamp, body = _signed()
    stale = str(int(time.time()) - SIGNATURE_MAX_SKEW_SECONDS - 30)

    verified, reason = await verify_webhook_signature(
        public_key=public_key, signature=signature, timestamp=stale, body=body
    )

    assert verified is False
    assert reason == "timestamp_rejected"


async def test_the_signature_is_bound_to_the_path_telnyx_calls():
    public_key, signature, timestamp, body = _signed()

    rejected, reason = await verify_webhook_signature(
        public_key=public_key,
        signature=signature,
        timestamp=timestamp,
        body=body,
        path="/api/admin/transfer",
    )
    accepted, _ = await verify_webhook_signature(
        public_key=public_key,
        signature=signature,
        timestamp=timestamp,
        body=body,
        path="/telephony/telnyx/ws",
    )

    assert rejected is False
    assert reason == "endpoint_mismatch"
    assert accepted is True


@pytest.mark.parametrize(
    ("signature", "reason"),
    [
        ("", "signature_missing"),
        ("   ", "signature_missing"),
        ("not-base64!!", "signature_rejected"),
        ("c2hvcnQ=", "signature_rejected"),  # decodes, but is not 64 bytes
    ],
)
async def test_a_missing_or_malformed_signature_never_verifies(signature, reason):
    public_key, _signature, timestamp, body = _signed()

    verified, got = await verify_webhook_signature(
        public_key=public_key, signature=signature, timestamp=timestamp, body=body
    )

    assert verified is False
    assert got == reason


async def test_a_bad_timestamp_is_rejected_before_the_crypto():
    public_key, signature, _timestamp, body = _signed()

    verified, reason = await verify_webhook_signature(
        public_key=public_key, signature=signature, timestamp="not-a-number", body=body
    )

    assert verified is False
    assert reason == "timestamp_rejected"


async def test_without_a_public_key_nothing_verifies():
    """A stream with no signing key cannot start, so there is no pass-through."""
    _public_key, signature, timestamp, body = _signed()

    verified, reason = await verify_webhook_signature(
        public_key="", signature=signature, timestamp=timestamp, body=body
    )

    assert verified is False
    assert reason == "configuration_missing"


async def test_the_configured_key_is_used_when_the_caller_passes_none(monkeypatch):
    public_key, signature, timestamp, body = _signed()
    monkeypatch.setattr(settings, "telnyx_public_key", public_key)

    verified, _reason = await verify_webhook_signature(
        signature=signature, timestamp=timestamp, body=body
    )

    assert verified is True
