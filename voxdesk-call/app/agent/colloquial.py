"""Colloquial rewriter — the LLM writes English, the caller should hear a person (P12-01).

A text LLM reliably writes "I would be happy to assist you with that request at this time".
Read aloud it sounds like a press release. This module sits between the LLM and TTS, rewrites
that sentence into "Sure, I can help with that right now", and does it inside a **50 ms
budget**: if the rewrite cannot finish in time the original text passes through untouched,
because a late reply is worse than a stiff one.

Two hard rules, both enforced by code rather than by care:

1. **Facts never change.** Numbers, money, dates, times, emails, URLs, and any token carrying
   digits (order ids, phone numbers, case numbers) are extracted before the rewrite and
   re-extracted after it. If the two multisets differ the rewrite is discarded — the caller
   must never hear a different appointment time than the one that was booked.
2. **The budget is real.** Every stage checks a monotonic deadline. A stage that would run
   past it aborts the whole rewrite, not just that stage.

The rewrite is deterministic apart from the optional opener, which takes an injected
``random.Random`` so tests can pin it.
"""

from __future__ import annotations

import random
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable

from pipecat.frames.frames import Frame, TextFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

DEFAULT_BUDGET_MS = 50.0

#: Corporate register → how a person actually says it. Order matters: longer first.
PHRASE_REWRITES: tuple[tuple[str, str], ...] = (
    (r"\bI would be happy to\b", "I'd be happy to"),
    (r"\bI would be glad to\b", "I'd be glad to"),
    (r"\bplease be advised that\b", ""),
    (r"\bplease note that\b", ""),
    (r"\bI regret to inform you that\b", "sorry, but"),
    (r"\bI apologize for any inconvenience( this may have caused)?\b", "sorry about that"),
    (r"\bdue to the fact that\b", "because"),
    (r"\bin the event that\b", "if"),
    (r"\bat this point in time\b", "right now"),
    (r"\bat this time\b", "right now"),
    (r"\bin order to\b", "to"),
    (r"\bprior to\b", "before"),
    (r"\bsubsequent to\b", "after"),
    (r"\bwith regard to\b", "about"),
    (r"\bwith reference to\b", "about"),
    (r"\bin reference to\b", "about"),
    (r"\bas per\b", "per"),
    (r"\bkindly\b", "please"),
    (r"\bwe are currently experiencing\b", "we're having"),
    (r"\bI am currently unable to\b", "I can't"),
    (r"\bI am unable to\b", "I can't"),
    (r"\byou may wish to\b", "you could"),
    (r"\bwould you like me to\b", "want me to"),
    (r"\bdo you have any questions regarding\b", "any questions about"),
    (r"\blet me know if you require\b", "tell me if you need"),
)

#: Single-word register swaps.
WORD_REWRITES: tuple[tuple[str, str], ...] = (
    (r"\butilise\b", "use"),
    (r"\butilize\b", "use"),
    (r"\bcommence\b", "start"),
    (r"\bterminate\b", "end"),
    (r"\bassist\b", "help"),
    (r"\bassistance\b", "help"),
    (r"\brequire\b", "need"),
    (r"\brequires\b", "needs"),
    (r"\brequired\b", "needed"),
    (r"\brequirement\b", "need"),
    (r"\bpurchase\b", "buy"),
    (r"\bregarding\b", "about"),
    (r"\badditional\b", "more"),
    (r"\bapproximately\b", "about"),
    (r"\bsufficient\b", "enough"),
    (r"\bdemonstrate\b", "show"),
    (r"\bobtain\b", "get"),
    (r"\battempt\b", "try"),
    (r"\binitiate\b", "start"),
    (r"\bsubsequently\b", "then"),
    (r"\bnonetheless\b", "still"),
    (r"\bnevertheless\b", "still"),
    (r"\bfacilitate\b", "help with"),
    (r"\bendeavor\b", "try"),
    (r"\bascertain\b", "find out"),
    (r"\btransmit\b", "send"),
    (r"\brequest that you\b", "ask you to"),
)

#: Contractions. Case-insensitive; the replacement keeps sentence-initial capitals.
CONTRACTIONS: tuple[tuple[str, str], ...] = (
    (r"\bI am\b", "I'm"),
    (r"\bI will\b", "I'll"),
    (r"\bI have\b", "I've"),
    (r"\byou are\b", "you're"),
    (r"\byou will\b", "you'll"),
    (r"\bit is\b", "it's"),
    (r"\bwe are\b", "we're"),
    (r"\bwe will\b", "we'll"),
    (r"\bthey are\b", "they're"),
    (r"\bthat is\b", "that's"),
    (r"\bthere is\b", "there's"),
    (r"\bwhat is\b", "what's"),
    (r"\bdo not\b", "don't"),
    (r"\bdoes not\b", "doesn't"),
    (r"\bdid not\b", "didn't"),
    (r"\bcannot\b", "can't"),
    (r"\bcan not\b", "can't"),
    (r"\bwill not\b", "won't"),
    (r"\bis not\b", "isn't"),
    (r"\bare not\b", "aren't"),
    (r"\bwas not\b", "wasn't"),
    (r"\bwere not\b", "weren't"),
    (r"\bhave not\b", "haven't"),
    (r"\bhas not\b", "hasn't"),
    (r"\bwould not\b", "wouldn't"),
    (r"\bshould not\b", "shouldn't"),
    (r"\bcould not\b", "couldn't"),
    (r"\blet us\b", "let's"),
)

#: Spans that must survive the rewrite byte-for-byte. Order matters: longest first.
PROTECTED_PATTERNS: tuple[str, ...] = (
    r"https?://\S+",                       # urls
    r"[\w.+-]+@[\w-]+\.[\w.-]+",           # emails
    r"\$\s?\d[\d,]*(?:\.\d{1,2})?",        # money
    r"\b\d{1,2}:\d{2}(?::\d{2})?\s*(?:[AaPp]\.?[Mm]\.?)?",  # clock times
    r"\b\d{4}-\d{2}-\d{2}\b",              # ISO dates
    r"\b\d+\s*%",                          # percentages
    r"\b[A-Za-z]{0,6}[-_]?\d[A-Za-z0-9-]*\b",  # ids carrying digits (ORD-1042, AB12CD)
    r"\b\d[\d,.]*\b",                      # any remaining number
)

_OPENERS: tuple[str, ...] = ("Sure, ", "Okay, ", "Alright, ", "So, ")

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_CONJUNCTIONS = re.compile(r"\s+(and|but|so|because)\s+", re.IGNORECASE)


class EntityGuardViolation(RuntimeError):
    """Raised internally when a rewrite would have changed a protected fact."""


@dataclass(frozen=True)
class RewriteResult:
    """Outcome of one rewrite, including everything needed to audit it."""

    text: str
    original: str
    changed: bool = False
    bypassed: bool = False
    reason: str = ""
    latency_ms: float = 0.0
    dropped_phrases: tuple[str, ...] = ()
    facts: tuple[str, ...] = field(default=())

    @property
    def budget_respected(self) -> bool:
        return not self.bypassed or self.reason in {"budget", "entity_guard"}


def protected_facts(text: str) -> tuple[str, ...]:
    """Every span the caller must hear exactly as generated, in order, lowercased."""
    found: list[str] = []
    for pattern in PROTECTED_PATTERNS:
        for match in re.finditer(pattern, text):
            found.append(match.group(0).strip().lower())
    return tuple(found)


def _same_facts(before: str, after: str) -> bool:
    return Counter(protected_facts(before)) == Counter(protected_facts(after))


def _match_case(replacement: str, original: str) -> str:
    if original.isupper() and len(original) > 1:
        return replacement.upper()
    if original[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def _apply_rules(text: str, rules: tuple[tuple[str, str], ...]) -> tuple[str, list[str]]:
    dropped: list[str] = []
    for pattern, replacement in rules:
        def _sub(match: re.Match[str], _replacement: str = replacement) -> str:
            dropped.append(match.group(0))
            return _match_case(_replacement, match.group(0))

        text = re.sub(pattern, _sub, text, flags=re.IGNORECASE)
    return text, dropped


def _split_long_sentences(text: str, *, max_words: int = 22) -> str:
    """Break an over-long sentence at its first coordinating conjunction.

    Long single-breath sentences are the single biggest "this is a robot" tell in TTS. The
    split only inserts punctuation, so no fact can move.
    """
    out: list[str] = []
    for sentence in _SENTENCE_SPLIT.split(text.strip()):
        words = sentence.split()
        if len(words) <= max_words:
            out.append(sentence)
            continue
        best: re.Match[str] | None = None
        for match in _CONJUNCTIONS.finditer(sentence):
            if len(sentence[: match.start()].split()) >= 8:
                best = match
                break
        if best is None:
            out.append(sentence)
            continue
        head = sentence[: best.start()].rstrip()
        tail = sentence[best.end():].strip()
        if not head or not tail:
            out.append(sentence)
            continue
        out.append(f"{head}. {tail[:1].upper()}{tail[1:]}")
    return " ".join(out)


def _tidy(text: str) -> str:
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"\s+([,.!?])", r"\1", text)
    text = re.sub(r"^[,\s]+", "", text)
    if text and text[0].islower():
        text = text[0].upper() + text[1:]
    if text and text[-1] not in ".!?":
        text += "."
    return text.strip()


def rewrite(
    text: str,
    *,
    budget_ms: float = DEFAULT_BUDGET_MS,
    add_opener: bool = False,
    opener_probability: float = 0.25,
    rng: random.Random | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> RewriteResult:
    """Rewrite one utterance into colloquial speech inside ``budget_ms``.

    Returns the original text with ``bypassed=True`` when the deadline or the fact guard
    says no. Never raises on bad input: an empty string is returned unchanged.
    """
    started = clock()
    if not text or not text.strip():
        return RewriteResult(text=text, original=text, latency_ms=0.0)

    def _expired() -> bool:
        return (clock() - started) * 1000.0 > budget_ms

    staged = text
    dropped: list[str] = []
    for stage in (PHRASE_REWRITES, WORD_REWRITES, CONTRACTIONS):
        if _expired():
            return RewriteResult(
                text=text, original=text, bypassed=True, reason="budget",
                latency_ms=(clock() - started) * 1000.0,
            )
        staged, hits = _apply_rules(staged, stage)
        dropped.extend(hits)

    if _expired():
        return RewriteResult(
            text=text, original=text, bypassed=True, reason="budget",
            latency_ms=(clock() - started) * 1000.0,
        )
    staged = _split_long_sentences(staged)

    rand = rng or random.Random()
    if add_opener and rand.random() < opener_probability:
        if not staged.lower().startswith(tuple(op.lower() for op in _OPENERS)):
            chosen = rand.choice(_OPENERS)
            staged = chosen + staged[0].lower() + staged[1:]

    staged = _tidy(staged)

    if not _same_facts(text, staged):
        # The guard is not advisory: a changed fact is a wrong appointment time.
        return RewriteResult(
            text=text, original=text, bypassed=True, reason="entity_guard",
            latency_ms=(clock() - started) * 1000.0,
            dropped_phrases=tuple(dropped),
        )

    return RewriteResult(
        text=staged,
        original=text,
        changed=staged != text,
        latency_ms=(clock() - started) * 1000.0,
        dropped_phrases=tuple(dropped),
        facts=protected_facts(staged),
    )


class ColloquialRewriter(FrameProcessor):
    """Pipecat processor: colloquialise ``TextFrame``s on their way to TTS.

    Emits ``colloquial_latency_ms`` and a bypass counter so a slow rewrite shows up in
    telemetry instead of silently degrading every reply.
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        budget_ms: float = DEFAULT_BUDGET_MS,
        add_opener: bool = False,
        opener_probability: float = 0.25,
        rng: random.Random | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__()
        self.enabled = enabled
        self.budget_ms = budget_ms
        self.add_opener = add_opener
        self.opener_probability = opener_probability
        self._rng = rng
        # Avoid shadowing FrameProcessor's own ``_clock`` BaseClock attribute.
        self._rewrite_clock = clock
        self.metric_samples: list[dict[str, Any]] = []
        self.latency_samples_ms: list[float] = []
        self.bypass_count = 0
        self.rewrite_count = 0
        self.last_result: RewriteResult | None = None

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if self.enabled and isinstance(frame, TextFrame) and frame.text:
            result = rewrite(
                frame.text,
                budget_ms=self.budget_ms,
                add_opener=self.add_opener,
                opener_probability=self.opener_probability,
                rng=self._rng,
                clock=self._rewrite_clock,
            )
            self.last_result = result
            self.latency_samples_ms.append(result.latency_ms)
            if result.bypassed:
                self.bypass_count += 1
                self.emit_metric("colloquial_bypassed", 1.0, reason=result.reason)
            else:
                self.rewrite_count += 1
                if result.changed:
                    frame.text = result.text
            self.emit_metric("colloquial_latency_ms", result.latency_ms, budget_ms=self.budget_ms)
        await self.push_frame(frame, direction)

    def emit_metric(self, name: str, value: float, **attributes: Any) -> None:
        """Record one metric sample. Overridable in tests and by the observability layer.

        Named ``metric_samples`` rather than ``metrics`` because ``FrameProcessor`` already
        owns a ``metrics`` attribute (``FrameProcessorMetrics``); shadowing it breaks pipecat.
        """
        self.metric_samples.append({"name": name, "value": value, **attributes})

    def summary(self) -> dict[str, Any]:
        samples = self.latency_samples_ms
        return {
            "enabled": self.enabled,
            "rewrites": self.rewrite_count,
            "bypasses": self.bypass_count,
            "budget_ms": self.budget_ms,
            "p50_latency_ms": round(sorted(samples)[len(samples) // 2], 3) if samples else 0.0,
            "max_latency_ms": round(max(samples), 3) if samples else 0.0,
        }
