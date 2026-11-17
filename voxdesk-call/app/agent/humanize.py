"""মানুষের মতো শোনানোর লেয়ার।

রোবট আর মানুষের পার্থক্য প্রম্পটে না — এই ৫টা জিনিসে:

  1. TextNormalizer   "$1,250" -> "twelve fifty"   TTS সংখ্যা পড়তে পারে না
  2. FillerInjector   ক্যালেন্ডার চেক করার ২ সেকেন্ড চুপ না থেকে "let me check that"
  3. Backchannel      কাস্টমার লম্বা কথা বললে মাঝে "mm-hmm" — মানুষ এটাই করে
  4. Disfluency       মাঝেমধ্যে "so...", "okay, um" — নিখুঁত কথা রোবটিক শোনায়
  5. VariedGreeting   প্রতিবার একই greeting = রোবট ধরা পড়ে যায়
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from app.core.logging import log

# pipecat ছাড়াও যেন টেক্সট ফাংশনগুলো import করা যায় (টেস্ট/স্ক্রিপ্টের জন্য)
try:
    from pipecat.frames.frames import (
        BotStartedSpeakingFrame,
        BotStoppedSpeakingFrame,
        Frame,
        FunctionCallInProgressFrame,
        InterimTranscriptionFrame,
        TextFrame,
        TranscriptionFrame,
        TTSSpeakFrame,
        UserStartedSpeakingFrame,
        UserStoppedSpeakingFrame,
    )
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

    PIPECAT_AVAILABLE = True
except ImportError:                                   # pragma: no cover
    PIPECAT_AVAILABLE = False
    Frame = FunctionCallInProgressFrame = TextFrame = TTSSpeakFrame = object
    UserStartedSpeakingFrame = UserStoppedSpeakingFrame = object
    BotStartedSpeakingFrame = BotStoppedSpeakingFrame = object
    InterimTranscriptionFrame = TranscriptionFrame = object
    FrameDirection = None

    class FrameProcessor:                              # noqa: D401 - stub
        """pipecat ইনস্টল না থাকলে ব্যবহৃত ডামি।"""

        def __init__(self, *args, **kwargs):
            pass

# ============================================================== 1. NUMBERS ==

_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
         "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
         "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy",
         "eighty", "ninety"]


def num_to_words(n: int) -> str:
    """0-999999 -> ইংরেজি শব্দ। TTS-এর জন্য অপরিহার্য।"""
    if n < 0:
        return "minus " + num_to_words(-n)
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + ("" if n % 10 == 0 else " " + _ONES[n % 10])
    if n < 1000:
        rest = "" if n % 100 == 0 else " " + num_to_words(n % 100)
        return _ONES[n // 100] + " hundred" + rest
    if n < 1_000_000:
        rest = "" if n % 1000 == 0 else " " + num_to_words(n % 1000)
        return num_to_words(n // 1000) + " thousand" + rest
    return str(n)


def _money(match: re.Match) -> str:
    raw = match.group(1).replace(",", "")
    if "." in raw:
        dollars, cents = raw.split(".", 1)
        cents = (cents + "0")[:2]
        out = f"{num_to_words(int(dollars))} dollars"
        if int(cents):
            out += f" and {num_to_words(int(cents))} cents"
        return out
    return f"{num_to_words(int(raw))} dollars"


def _clock(match: re.Match) -> str:
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridiem = (match.group(3) or "").upper().replace(".", "")
    spoken = num_to_words(hour if hour else 12)
    if minute == 0:
        spoken += " o'clock" if not meridiem else ""
    elif minute < 10:
        spoken += " oh " + num_to_words(minute)
    else:
        spoken += " " + num_to_words(minute)
    if meridiem:
        spoken += " " + ("A M" if meridiem.startswith("A") else "P M")
    return spoken


def _phone(match: re.Match) -> str:
    digits = re.sub(r"\D", "", match.group(0))
    return " ".join(num_to_words(int(d)) for d in digits)


ABBREVIATIONS = {
    r"\bDr\.": "Doctor", r"\bMr\.": "Mister", r"\bMrs\.": "Missus",
    r"\bMs\.": "Miss", r"\bSt\.": "Street", r"\bAve\.": "Avenue",
    r"\bappt\b": "appointment", r"\bASAP\b": "as soon as possible",
    r"\be\.g\.": "for example", r"\bi\.e\.": "that is", r"\bvs\.": "versus",
    r"\bapprox\.": "approximately", r"&": " and ",
}


def format_transcript_text(text: str) -> str:
    """Clean markup and layout without changing the words or numeric values."""
    if not text:
        return text
    # Remove formatting that a call transcript or speech renderer should not expose.
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"[*_`#>|]+", " ", text)
    text = re.sub(r"^\s*[-•]\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", "", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text


def normalize_for_speech(text: str) -> str:
    """LLM-এর লেখা টেক্সটকে 'বলার মতো' টেক্সটে বদলায়।"""
    if not text:
        return text

    text = format_transcript_text(text)
    for pattern, replacement in ABBREVIATIONS.items():
        text = re.sub(pattern, replacement, text)

    # ক্রম গুরুত্বপূর্ণ: টাকা -> ফোন -> ঘড়ি -> বাকি সংখ্যা
    text = re.sub(r"\$\s?([\d,]+(?:\.\d{1,2})?)", _money, text)
    text = re.sub(r"\+?\d[\d\-\.\s\(\)]{8,}\d", _phone, text)
    text = re.sub(r"\b(\d{1,2}):(\d{2})\s*([APap]\.?[Mm]\.?)?", _clock, text)
    text = re.sub(
        r"\b(\d{1,2})\s*([APap])\.?[Mm]\.?",
        lambda m: f"{num_to_words(int(m.group(1)))} "
                  f"{'A M' if m.group(2).upper() == 'A' else 'P M'}",
        text,
    )
    text = re.sub(r"(\d+)%", lambda m: f"{num_to_words(int(m.group(1)))} percent", text)
    text = re.sub(r"\b(\d{1,4})\b", lambda m: num_to_words(int(m.group(1))), text)

    text = re.sub(r"\s{2,}", " ", text).strip()

    # "A.M." এর ডট খেয়ে ফেললে বাক্যের শেষ বিরামচিহ্ন হারায় -> TTS-এর সুর নষ্ট হয়
    if text and text[-1] not in ".?!,":
        text += "."
    return text


class TextNormalizer(FrameProcessor):
    """Apply one agent's selected speech/transcript presentation before TTS."""

    def __init__(
        self,
        *,
        normalize: bool = True,
        read_numbers_slowly: bool = False,
        format_transcript: bool = True,
    ) -> None:
        super().__init__()
        self.options = HumanizeOptions(
            normalize_for_speech=normalize,
            read_numbers_slowly=read_numbers_slowly,
            format_transcript=format_transcript,
        )

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TextFrame) and frame.text:
            frame.text = apply_humanize(frame.text, self.options)
        await self.push_frame(frame, direction)


# ============================================================== 2. FILLERS ==

THINKING_FILLERS = [
    "Let me check that for you.",
    "One second, I'm looking at the calendar.",
    "Okay, let me see.",
    "Sure, give me just a moment.",
    "Alright, checking now.",
]


class FillerInjector(FrameProcessor):
    """Tool call চলাকালীন চুপ না থেকে কিছু বলে।

    এইটাই সবচেয়ে বড় 'মানুষ মনে হওয়ার' ট্রিক। ক্যালেন্ডার API-তে ৮০০ms লাগে;
    ওই ৮০০ms চুপ থাকলে কাস্টমার ভাবে লাইন কেটে গেছে।
    """

    def __init__(
        self,
        min_gap_seconds: float = 8.0,
        trigger_ms: int = 800,
        *,
        enabled: bool = True,
    ):
        super().__init__()
        self._last_filler_at = 0.0
        self._min_gap = min_gap_seconds
        self._trigger_ms = max(0, int(trigger_ms))
        self.enabled = bool(enabled)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if self.enabled and isinstance(frame, FunctionCallInProgressFrame):
            now = datetime.now().timestamp()
            if now - self._last_filler_at > self._min_gap:
                self._last_filler_at = now
                filler = random.choice(THINKING_FILLERS)
                log.info("humanize.filler", text=filler, trigger_ms=self._trigger_ms)
                await self.push_frame(TTSSpeakFrame(filler), FrameDirection.DOWNSTREAM)

        await self.push_frame(frame, direction)


# ========================================================= 3. BACKCHANNEL ==

BACKCHANNELS = ["Mm-hmm.", "Right.", "Okay.", "Got it.", "I see."]
DEFAULT_BACKCHANNEL_WORDS = ("yeah", "uh-huh", "got it", "mm-hmm", "okay")


class Backchannel(FrameProcessor):
    """কাস্টমার লম্বা কথা বললে মাঝে ছোট সাড়া দেয় — মানুষ ঠিক এটাই করে।

    Extended in Sub-Phase 2B:
    - Respects `enabled` (`enable_backchannel`), `frequency` (`0.0..1.0`), and `words` (`backchannel_words`).
    - Triggers on user pauses after `after_seconds` (default 1.5s when configured via RuntimeConfig,
      or 3.0s legacy default) without interrupting the user's turn or clearing LLM context.
    """

    def __init__(
        self,
        after_seconds: float = 1.5,
        cooldown: float = 6.0,
        *,
        enabled: bool = True,
        frequency: float = 0.5,
        words: Sequence[str] | None = None,
        rng: random.Random | None = None,
    ):
        super().__init__()
        self._after = max(0.5, float(after_seconds))
        self._cooldown = max(1.0, float(cooldown))
        self._enabled = bool(enabled)
        self._frequency = max(0.0, min(1.0, float(frequency)))
        cleaned_words = [str(w).strip() for w in (words or ()) if str(w).strip()]
        self._words = cleaned_words if cleaned_words else list(BACKCHANNELS)
        self._rng = rng or random.Random()
        self._speech_started_at: float | None = None
        self._last_ack_at = 0.0
        self._bot_speaking = False
        self.emitted_cues: list[str] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        now = datetime.now().timestamp()

        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
        elif isinstance(frame, UserStartedSpeakingFrame):
            self._speech_started_at = now
        elif isinstance(frame, UserStoppedSpeakingFrame) and self._speech_started_at is not None:
            spoke_for = now - self._speech_started_at
            self._speech_started_at = None
            if (
                self._enabled
                and not self._bot_speaking
                and self._frequency > 0.0
                and spoke_for >= self._after
                and (now - self._last_ack_at) >= self._cooldown
                and self._rng.random() <= self._frequency
            ):
                self._last_ack_at = now
                ack = self._rng.choice(self._words)
                self.emitted_cues.append(ack)
                log.info(
                    "humanize.backchannel",
                    text=ack,
                    after_s=round(spoke_for, 2),
                    frequency=self._frequency,
                )
                await self.push_frame(TTSSpeakFrame(ack), FrameDirection.DOWNSTREAM)

        await self.push_frame(frame, direction)


BackchannelProcessor = Backchannel


# ========================================================== 4. DISFLUENCY ==

SENTENCE_OPENERS = ["So, ", "Okay, ", "Alright, ", "Sure, ", "Well, ", ""]


def add_natural_opener(text: str, probability: float = 0.25) -> str:
    """মাঝেমধ্যে বাক্যের শুরুতে স্বাভাবিক শব্দ বসায়।

    নিখুঁত ব্যাকরণ = রোবট। সামান্য অগোছালো = মানুষ।
    ২৫% এর বেশি করবেন না, নাহলে ন্যাকা শোনাবে।
    """
    if not text or random.random() > probability:
        return text
    if text[:6].lower() in ("so, ", "okay,", "alrig", "sure,", "well,"):
        return text
    return random.choice(SENTENCE_OPENERS) + text[0].lower() + text[1:]


# ====================================================== 5. VARIED GREETING ==

def vary_greeting(base: str, business_name: str, agent_name: str) -> str:
    """প্রতিবার হুবহু একই greeting = রোবট ধরা পড়ে। সময় অনুযায়ী বদলায়।"""
    hour = datetime.now().hour
    part = "Good morning" if hour < 12 else ("Good afternoon" if hour < 17 else "Good evening")

    templates = [
        base,
        f"{part}, {business_name}, this is {agent_name}. How can I help?",
        f"Thanks for calling {business_name}. {agent_name} speaking, what can I do for you?",
        f"Hi, {business_name}, this is {agent_name}. What can I help you with today?",
    ]
    return random.choice(templates)


# ============================== P12-06: per-agent speech toggles =============================

_DIGIT_WORDS = (
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
)


def read_numbers_slowly(text: str) -> str:
    """Spell digits out one at a time so a serial number is heard, not rounded.

    ``"call 5550142"`` becomes ``"call five, five, five, zero, one, four, two"``. Applied before
    :func:`normalize_for_speech`, which leaves already-spelled numbers alone.
    """
    if not text:
        return text

    def _spell(match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        if len(digits) < 3:
            return match.group(0)
        return ", ".join(_DIGIT_WORDS[int(digit)] for digit in digits)

    # Percentages, money and clock times are handled by normalize_for_speech; spelling those out
    # digit by digit would say "two, five percent".
    protected: list[str] = []

    def _protect(match: re.Match[str]) -> str:
        protected.append(match.group(0))
        return f"\x00{len(protected) - 1}\x00"

    text = re.sub(r"\$\s?\d[\d,]*(?:\.\d{1,2})?|\b\d{1,2}:\d{2}\b|\b\d+\s*%", _protect, text)
    text = re.sub(r"\b\d{3,}\b", _spell, text)
    return re.sub(r"\x00(\d+)\x00", lambda m: protected[int(m.group(1))], text)


@dataclass(frozen=True)
class HumanizeOptions:
    """Per-agent speech presentation toggles (P12-06).

    Every flag defaults to today's behaviour, so an agent that sets nothing sounds exactly as
    it did before this task — a presentation change must be opted into, never inherited.
    """

    normalize_for_speech: bool = True
    read_numbers_slowly: bool = False
    format_transcript: bool = True

    def validate(self) -> list[str]:
        """No flag combination is dangerous enough to refuse.

        Turning ``normalize_for_speech`` off deliberately lets whatever the LLM wrote reach TTS,
        markdown included — that is the operator's call, and refusing it would make the toggle
        unusable. The combination is documented instead of policed.
        """
        return []

    def as_dict(self) -> dict[str, bool]:
        return {
            "normalize_for_speech": self.normalize_for_speech,
            "read_numbers_slowly": self.read_numbers_slowly,
            "format_transcript": self.format_transcript,
        }


def apply_humanize(text: str, options: HumanizeOptions | None = None) -> str:
    """Apply the enabled presentation transforms in the order that keeps numbers correct."""
    opts = options or HumanizeOptions()
    problems = opts.validate()
    if problems:
        raise ValueError("; ".join(problems))
    if not text:
        return text
    out = text
    if opts.read_numbers_slowly:
        out = read_numbers_slowly(out)
    if opts.normalize_for_speech:
        out = normalize_for_speech(out)
    elif opts.format_transcript:
        # Transcript formatting is deliberately distinct from speech normalization: when number
        # normalization is off, operators may still remove markdown and emoji from transcripts.
        out = format_transcript_text(out)
    return out
