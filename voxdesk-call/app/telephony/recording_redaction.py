# File: app/telephony/recording_redaction.py — mute or beep PII inside a recording
"""Recording redaction from STT word timestamps (PART 15, P15-10).

Redacting a transcript is easy; redacting the *audio* is the requirement, because
the recording is what leaks. The pieces we need already exist:

* the transcription layer returns per-word timings,
* :mod:`app.gdpr.redact` already decides what a phone number, card, SSN or email
  looks like (and its Luhn check is what stops a 16-digit order id being
  mistaken for a card).

This module joins them: words -> spans -> padded, merged audio segments ->
samples zeroed (``mute``) or replaced with a tone (``beep``).

Two properties the tests pin down:

1. Only the samples inside a span change. A redactor that shifts the timeline by
   a sample breaks every downstream alignment, so the output is the same length
   as the input, always.
2. Padding never merges two spans into one long silence *when they were
   adjacent anyway* -- but a pad that would swallow speech between two spans is
   clipped at the midpoint, so redaction cannot be used to hide the words
   around a card number.
"""

from __future__ import annotations

import hashlib
import io
import re
import math
import struct
import wave
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from app.gdpr.redact import (  # the module's names: `app.gdpr.redact` is the function
    TOKEN_CARD,
    TOKEN_EMAIL,
    TOKEN_PHONE,
    TOKEN_SSN,
    redact as redact_text,
)

MODE_MUTE = "mute"
MODE_BEEP = "beep"
MODES: tuple[str, ...] = (MODE_MUTE, MODE_BEEP)

#: Padding around a PII word, so the attack starts before the detector does.
DEFAULT_PAD_MS = 120

#: Beep frequency/level. 1 kHz at ~20 % of full scale is audible but not
#: startling; a redaction should not sound like an error.
BEEP_HZ = 1000.0
BEEP_AMPLITUDE = 0.20

#: Minimum segment. A 30 ms hole in speech reads as a glitch; 80 ms reads as a
#: censor bleep.
MIN_SEGMENT_MS = 80

TOKEN_KINDS = {
    TOKEN_EMAIL: "email",
    TOKEN_PHONE: "phone",
    TOKEN_CARD: "card",
    TOKEN_SSN: "ssn",
}


@dataclass(frozen=True, slots=True)
class Word:
    """One STT word with its offsets in the recording."""

    text: str
    start_ms: float
    end_ms: float

    def __post_init__(self) -> None:
        if self.end_ms < self.start_ms:
            raise ValueError(f"word {self.text!r} ends before it starts")


@dataclass(frozen=True, slots=True)
class Segment:
    """A span of audio to redact."""

    start_ms: float
    end_ms: float
    kind: str = "pii"

    @property
    def duration_ms(self) -> float:
        return self.end_ms - self.start_ms

    def as_dict(self) -> dict:
        return {
            "start_ms": round(self.start_ms, 2),
            "end_ms": round(self.end_ms, 2),
            "kind": self.kind,
        }


@dataclass(frozen=True, slots=True)
class RedactionPolicy:
    """What to do with a span, and what to keep."""

    mode: str = MODE_BEEP
    pad_ms: int = DEFAULT_PAD_MS
    keep_original: bool = True

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"unknown redaction mode {self.mode!r}")
        if self.pad_ms < 0:
            raise ValueError("pad_ms cannot be negative")


@dataclass(frozen=True, slots=True)
class RedactionPlan:
    """The segments found, plus everything needed to explain them."""

    segments: tuple[Segment, ...] = ()
    words: int = 0
    kinds: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        return {
            "segments": [segment.as_dict() for segment in self.segments],
            "words": self.words,
            "kinds": list(self.kinds),
            "redacted_ms": round(sum(s.duration_ms for s in self.segments), 2),
        }


def _pii_kind(text: str) -> str | None:
    """Reuse the canonical redactor to classify a single word.

    The word is wrapped in spaces so the redactor's word boundaries behave the
    same way they do in a sentence.
    """
    result = redact_text(f" {text} ")
    for token, kind in TOKEN_KINDS.items():
        if token in result.text:
            return kind
    return None


_NUMERIC = re.compile(r"^[\d()+.\-]{2,}$")


def _numeric_runs(words: Sequence[Word]) -> list[list[Word]]:
    """Consecutive numeric words, which STT splits a card number into.

    "4111 1111 1111 1111" arrives as four words. Testing each one alone finds
    nothing; testing the run finds the card. This is the whole reason word-level
    detection is not enough.
    """
    runs: list[list[Word]] = []
    current: list[Word] = []
    for word in words:
        token = word.text.strip()
        if _NUMERIC.match(token):
            current.append(word)
            continue
        if current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    return runs


def pii_words(words: Sequence[Word]) -> list[Segment]:
    """Word-level PII spans, before padding or merging.

    Two passes: individual words (caught by the email/phone patterns) and
    numeric runs (which is how a card or an SSN actually arrives from the STT
    layer).
    """
    spans: list[Segment] = []
    covered: set[int] = set()

    for run in _numeric_runs(words):
        joined = " ".join(word.text.strip() for word in run)
        kind = _pii_kind(joined)
        if kind is None and len(run) > 1:
            # A run whose words were separated by the tokenizer still has to be
            # testable as one number.
            kind = _pii_kind("".join(word.text.strip() for word in run))
        if kind is None:
            continue
        spans.append(
            Segment(start_ms=float(run[0].start_ms), end_ms=float(run[-1].end_ms), kind=kind)
        )
        covered.update(id(word) for word in run)

    for word in words:
        if id(word) in covered:
            continue
        kind = _pii_kind(word.text)
        if kind is None:
            continue
        spans.append(Segment(start_ms=float(word.start_ms), end_ms=float(word.end_ms), kind=kind))
    spans.sort(key=lambda span: span.start_ms)
    return spans


def merge_segments(segments: Iterable[Segment], *, gap_ms: float = 0.0) -> list[Segment]:
    """Merge overlapping (or nearly touching) segments, keeping the first kind."""
    ordered = sorted(segments, key=lambda s: (s.start_ms, s.end_ms))
    merged: list[Segment] = []
    for segment in ordered:
        if not merged:
            merged.append(segment)
            continue
        last = merged[-1]
        if segment.start_ms <= last.end_ms + gap_ms:
            merged[-1] = Segment(
                start_ms=last.start_ms,
                end_ms=max(last.end_ms, segment.end_ms),
                kind=last.kind,
            )
            continue
        merged.append(segment)
    return merged


def _pad(spans: Sequence[Segment], pad_ms: float, duration_ms: float | None) -> list[Segment]:
    """Pad each span, clipping at the midpoint of the gap to its neighbours."""
    padded: list[Segment] = []
    for index, span in enumerate(spans):
        start = span.start_ms - pad_ms
        end = span.end_ms + pad_ms
        if index > 0:
            previous_end = spans[index - 1].end_ms
            midpoint = (previous_end + span.start_ms) / 2.0
            start = max(start, midpoint)
        if index + 1 < len(spans):
            next_start = spans[index + 1].start_ms
            midpoint = (span.end_ms + next_start) / 2.0
            end = min(end, midpoint)
        start = max(0.0, start)
        if duration_ms is not None:
            end = min(end, duration_ms)
        padded.append(Segment(start_ms=start, end_ms=max(start, end), kind=span.kind))
    return padded


def plan_redaction(
    words: Sequence[Word],
    *,
    policy: RedactionPolicy | None = None,
    duration_ms: float | None = None,
) -> RedactionPlan:
    """Words in, segments out. Deterministic; no audio needed."""
    effective = policy or RedactionPolicy()
    spans = pii_words(words)
    if not spans:
        return RedactionPlan(segments=(), words=len(words), kinds=())
    padded = _pad(merge_segments(spans), float(effective.pad_ms), duration_ms)
    # After padding, two spans may have grown into each other; merging again is
    # what keeps the beep from restarting mid-tone.
    final = merge_segments(padded)
    # Enforce the minimum on very short spans without pushing past the MIDI
    # next segment start.
    widened: list[Segment] = []
    for index, segment in enumerate(final):
        end = segment.end_ms
        if segment.duration_ms < MIN_SEGMENT_MS:
            limit = final[index + 1].start_ms if index + 1 < len(final) else None
            grown = segment.start_ms + MIN_SEGMENT_MS
            end = min(grown, limit) if limit is not None else grown
            if duration_ms is not None:
                end = min(end, duration_ms)
        widened.append(Segment(segment.start_ms, max(end, segment.end_ms), segment.kind))
    kinds = tuple(dict.fromkeys(segment.kind for segment in widened))
    return RedactionPlan(segments=tuple(widened), words=len(words), kinds=kinds)


def _sample_range(segment: Segment, sample_rate: int) -> tuple[int, int]:
    start = int(round(segment.start_ms / 1000.0 * sample_rate))
    end = int(round(segment.end_ms / 1000.0 * sample_rate))
    return max(0, start), max(0, end)


def apply_redaction(
    pcm16: bytes,
    *,
    sample_rate: int,
    segments: Sequence[Segment],
    mode: str = MODE_BEEP,
) -> bytes:
    """Return a same-length PCM16 buffer with the segments muted or beeped."""
    if mode not in MODES:
        raise ValueError(f"unknown redaction mode {mode!r}")
    if not segments:
        return bytes(pcm16)
    total_samples = len(pcm16) // 2
    samples = list(struct.unpack(f"<{total_samples}h", pcm16[: total_samples * 2]))
    remainder = pcm16[total_samples * 2 :]

    for segment in segments:
        start, end = _sample_range(segment, sample_rate)
        end = min(end, total_samples)
        if start >= end:
            continue
        if mode == MODE_MUTE:
            for index in range(start, end):
                samples[index] = 0
            continue
        for index in range(start, end):
            offset = index - start
            value = math.sin(2.0 * math.pi * BEEP_HZ * offset / sample_rate)
            samples[index] = int(max(-32768, min(32767, value * BEEP_AMPLITUDE * 32767)))

    return struct.pack(f"<{total_samples}h", *samples) + remainder


def redact_wav(
    wav_bytes: bytes,
    *,
    words: Sequence[Word],
    policy: RedactionPolicy | None = None,
) -> tuple[bytes, RedactionPlan]:
    """Redact a 16-bit mono WAV. Returns the new WAV and the plan that produced it."""
    effective = policy or RedactionPolicy()
    with wave.open(io.BytesIO(wav_bytes), "rb") as source:
        if source.getsampwidth() != 2:
            raise ValueError("only 16-bit PCM is supported")
        if source.getnchannels() != 1:
            raise ValueError("only mono recordings are supported")
        sample_rate = source.getframerate()
        frames = source.readframes(source.getnframes())

    duration_ms = len(frames) / 2 / sample_rate * 1000.0
    plan = plan_redaction(words, policy=effective, duration_ms=duration_ms)
    redacted = apply_redaction(
        frames, sample_rate=sample_rate, segments=plan.segments, mode=effective.mode
    )

    out = io.BytesIO()
    with wave.open(out, "wb") as sink:
        sink.setnchannels(1)
        sink.setsampwidth(2)
        sink.setframerate(sample_rate)
        sink.writeframes(redacted)
    return out.getvalue(), plan


def content_hash(payload: bytes) -> str:
    """Hash of a recording, for the "is this the redacted copy" check."""
    return hashlib.sha256(payload).hexdigest()


def redact_transcript(text: str) -> tuple[str, list[str]]:
    """The transcript side of the same decision, so both artefacts agree."""
    result = redact_text(text)
    kinds = [kind for token, kind in TOKEN_KINDS.items() if token in result.text]
    return result.text, kinds
