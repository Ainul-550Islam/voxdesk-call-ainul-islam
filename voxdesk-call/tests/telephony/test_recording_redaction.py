# File: tests/telephony/test_recording_redaction.py — synthetic audio, known spans, exact offsets
"""Recording redaction tests (PART 15, P15-10).

The audio is generated in the test: a constant tone with known word boundaries.
That makes the assertion exact rather than approximate -- the samples inside the
PII span are silent (or a beep), the samples outside it are untouched, and the
buffer length never changes.

The interesting cases are the ones that break a naive implementation:

* a card number split into four STT words,
* two PII spans close together, where the padding must not swallow the speech
  between them,
* an innocent 16-digit order number (no Luhn check) that must NOT be muted.
"""

from __future__ import annotations

import io
import struct
import wave

import pytest

from app.telephony import recording_redaction as rr

SAMPLE_RATE = 8000
AMPLITUDE = 3000


def _wav(duration_ms: int) -> bytes:
    frames = struct.pack(f"<{SAMPLE_RATE * duration_ms // 1000}h", *([AMPLITUDE] * (SAMPLE_RATE * duration_ms // 1000)))
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as sink:
        sink.setnchannels(1)
        sink.setsampwidth(2)
        sink.setframerate(SAMPLE_RATE)
        sink.writeframes(frames)
    return buffer.getvalue()


def _samples(wav_bytes: bytes) -> tuple[int, ...]:
    with wave.open(io.BytesIO(wav_bytes)) as source:
        raw = source.readframes(source.getnframes())
        assert source.getframerate() == SAMPLE_RATE
    return struct.unpack(f"<{len(raw) // 2}h", raw)


def _at(samples: tuple[int, ...], ms: float) -> int:
    return samples[int(ms / 1000.0 * SAMPLE_RATE)]


# ------------------------------------------------------------------ planning ---


def test_single_pii_word_is_found():
    words = [
        rr.Word("call", 0, 300),
        rr.Word("+14155550123", 300, 900),
        rr.Word("tomorrow", 900, 1300),
    ]
    plan = rr.plan_redaction(words, duration_ms=2000)
    assert len(plan.segments) == 1
    assert plan.kinds == ("phone",)
    segment = plan.segments[0]
    # Padded by the default, but never past the neighbouring speech.
    assert segment.start_ms < 300
    assert segment.end_ms > 900
    assert segment.end_ms <= (900 + 1300) / 2


def test_card_split_across_words_is_found():
    """``4111 1111 1111 1111`` arrives as four words; the run is the card."""
    words = [
        rr.Word("my", 0, 100),
        rr.Word("card", 100, 300),
        rr.Word("4111", 300, 500),
        rr.Word("1111", 500, 700),
        rr.Word("1111", 700, 900),
        rr.Word("1111", 900, 1100),
        rr.Word("thanks", 1100, 1400),
    ]
    plan = rr.plan_redaction(words, duration_ms=2000)
    assert plan.kinds == ("card",)
    assert plan.segments[0].start_ms <= 300
    assert plan.segments[0].end_ms >= 1100


def test_innocent_long_number_is_not_redacted():
    """A 16-digit order id that fails Luhn is not a card, and is not muted."""
    words = [
        rr.Word("order", 0, 200),
        rr.Word("1234", 200, 400),
        rr.Word("5678", 400, 600),
        rr.Word("9012", 600, 800),
        rr.Word("3456", 800, 1000),
    ]
    plan = rr.plan_redaction(words, duration_ms=1500)
    assert plan.segments == ()
    assert plan.kinds == ()


def test_two_spans_keep_the_speech_between_them():
    words = [
        rr.Word("+14155550123", 0, 400),
        rr.Word("and", 400, 600),
        rr.Word("a@b.com", 600, 1000),
    ]
    plan = rr.plan_redaction(words, duration_ms=1500, policy=rr.RedactionPolicy(pad_ms=0))
    first, second = plan.segments
    # The pad is clipped at the midpoint of the gap in both directions, so the
    # word "and" is never inside a redacted span.
    assert first.end_ms <= 500
    assert second.start_ms >= 500


def test_padding_never_exceeds_the_recording():
    words = [rr.Word("+14155550123", 0, 300)]
    plan = rr.plan_redaction(words, duration_ms=350, policy=rr.RedactionPolicy(pad_ms=500))
    segment = plan.segments[0]
    assert segment.start_ms == 0
    assert segment.end_ms <= 350


def test_minimum_segment_is_widened():
    words = [rr.Word("+14155550123", 100, 110)]
    plan = rr.plan_redaction(words, duration_ms=1000, policy=rr.RedactionPolicy(pad_ms=0))
    assert plan.segments[0].duration_ms >= rr.MIN_SEGMENT_MS


# ------------------------------------------------------------------- audio ---


def test_mute_zeroes_only_the_span():
    words = [rr.Word("+14155550123", 400, 800)]
    redacted, plan = rr.redact_wav(
        _wav(2000), words=words, policy=rr.RedactionPolicy(mode=rr.MODE_MUTE)
    )
    assert len(plan.segments) == 1
    samples = _samples(redacted)
    assert len(samples) == SAMPLE_RATE * 2
    assert _at(samples, 600) == 0, "inside the span must be silent"
    assert _at(samples, 100) == AMPLITUDE, "before the span must be untouched"
    assert _at(samples, 1800) == AMPLITUDE, "after the span must be untouched"


def test_beep_is_a_tone_not_silence():
    words = [rr.Word("+14155550123", 400, 800)]
    redacted, _ = rr.redact_wav(
        _wav(2000), words=words, policy=rr.RedactionPolicy(mode=rr.MODE_BEEP)
    )
    samples = _samples(redacted)
    window = samples[int(0.45 * SAMPLE_RATE) : int(0.75 * SAMPLE_RATE)]
    assert any(value != 0 for value in window), "a beep must not be silence"
    assert all(abs(value) <= int(0.25 * 32767) for value in window), "the beep stays quiet"
    assert _at(samples, 100) == AMPLITUDE


def test_output_length_is_identical():
    words = [rr.Word("+14155550123", 200, 600)]
    original = _wav(1200)
    redacted, _ = rr.redact_wav(original, words=words)
    assert len(_samples(redacted)) == len(_samples(original))


def test_unknown_mode_is_refused():
    with pytest.raises(ValueError):
        rr.RedactionPolicy(mode="shred")
    with pytest.raises(ValueError):
        rr.apply_redaction(b"", sample_rate=SAMPLE_RATE, segments=[], mode="shred")


def test_no_segments_returns_the_same_bytes():
    payload = _wav(500)
    with wave.open(io.BytesIO(payload)) as source:
        frames = source.readframes(source.getnframes())
    assert rr.apply_redaction(frames, sample_rate=SAMPLE_RATE, segments=[], mode=rr.MODE_MUTE) == frames


# --------------------------------------------------------------- the pair ---


def test_transcript_and_audio_agree_on_what_was_redacted():
    text, kinds = rr.redact_transcript("call me on +14155550123 or a@b.com")
    assert text == "call me on [PHONE] or [EMAIL]"
    assert set(kinds) == {"phone", "email"}


def test_content_hash_changes_with_the_payload():
    first = rr.content_hash(_wav(300))
    second = rr.content_hash(_wav(301))
    assert first != second
    assert first == rr.content_hash(_wav(300))
