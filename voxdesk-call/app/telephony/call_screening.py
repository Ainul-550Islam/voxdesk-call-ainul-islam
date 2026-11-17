"""iOS / Google call screening detection (PART 12, P12-12).

When a phone with call screening enabled receives our outbound call, the person never hears us
directly: their OS answers, transcribes our opening, and shows them a summary with a button. Two
things follow, and both are tested here:

1. We must recognise the screening prompt quickly, or the first thing the screening transcript
   captures is us talking over it — which reads as a robocall and gets declined.
2. After we answer, the OS goes quiet and the *person* picks up. "Quiet" is the signal to keep
   listening, not to hang up. Treating that silence as voicemail is the failure mode that makes
   agents leave a message into a screening assistant.

Nothing here guesses from a single word: the detector needs a phrase-level pattern, and the result
is a decision the pipeline can act on, with the phrase that triggered it kept for the transcript.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

#: Classifications a screener can produce.
SCREENING_STATES: tuple[str, ...] = ("screening", "live_pickup", "voicemail", "unknown")

#: Phrases the phone OS speaks to *us* when screening our call. English and Spanish fixtures.
_SCREENING_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "ios",
        # Deliberately *not* a bare "screening this call": Google says that exact phrase, and
        # attributing it to iOS sends the wrong platform into the transcript.
        re.compile(
            r"(the\s+person\s+you\s+are\s+calling\s+is\s+using\s+call\s+screening|"
            r"please\s+say\s+your\s+name\s+and\s+why\s+you\s+are\s+calling|"
            r"if\s+you\s+record\s+your\s+name.{0,40}the\s+person\s+you\s+are\s+calling)",
            re.IGNORECASE,
        ),
    ),
    (
        "google",
        re.compile(
            r"(call\s+screen(?:ing)?\s+(?:is|by)\s+google|"
            r"google\s+call\s+screen|"
            r"i\s+am\s+(?:a|the)\s+google\s+assistant|"
            r"this\s+call\s+is\s+being\s+screened|"
            r"tell\s+me\s+what\s+this\s+is\s+about)",
            re.IGNORECASE,
        ),
    ),
    (
        "es",
        re.compile(
            r"(filtro\s+de\s+llamadas|"
            r"di\s+tu\s+nombre\s+y\s+(?:el\s+)?motivo\s+de\s+tu\s+llamada|"
            r"la\s+persona\s+a\s+la\s+que\s+llamas\s+est[áa]\s+filt(?:rando|ro)|"
            r"asistente\s+de\s+google)",
            re.IGNORECASE,
        ),
    ),
)

_VOICEMAIL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(leave\s+(?:a\s+|your\s+)?message|after\s+the\s+(?:beep|tone)|"
        r"mailbox\s+is\s+full|not\s+available\s+right\s+now)",
        re.IGNORECASE,
    ),
)

#: The shortest thing worth saying back to a screener. Long messages get truncated by the OS.
DEFAULT_SCREENING_MESSAGE = "Hi, this is Ava calling from Northwind about your appointment."


@dataclass(frozen=True)
class ScreeningResult:
    state: str
    platform: str = ""
    phrase: str = ""
    confidence: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "platform": self.platform,
            "phrase": self.phrase,
            "confidence": round(self.confidence, 2),
        }

    @property
    def should_speak(self) -> bool:
        return self.state == "screening"

    @property
    def should_keep_listening(self) -> bool:
        # A screener always pauses for the human to press a button. Silence there is expected.
        return self.state in {"screening", "unknown"}


class ScreeningDetector:
    """Classify a short transcript chunk as screening, live pickup or voicemail."""

    def __init__(self, *, extra_phrases: Iterable[str] | None = None) -> None:
        self.extra = [
            re.compile(re.escape(phrase), re.IGNORECASE)
            for phrase in (extra_phrases or ())
            if str(phrase).strip()
        ]

    def classify(self, text: str) -> ScreeningResult:
        spoken = (text or "").strip()
        if not spoken:
            return ScreeningResult("unknown")

        for pattern in _VOICEMAIL_PATTERNS:
            match = pattern.search(spoken)
            if match:
                # Voicemail wins over screening: a machine that offers a beep is not a screener,
                # even if the OS phrase leaked into the same transcript.
                return ScreeningResult("voicemail", "voicemail", match.group(0), 0.8)

        # Google first: its prompt contains the generic phrase "screening this call", which iOS
        # would otherwise claim.
        for platform, pattern in sorted(_SCREENING_PATTERNS, key=lambda item: item[0] != "google"):
            match = pattern.search(spoken)
            if match:
                return ScreeningResult("screening", platform, match.group(0), 0.9)

        for pattern in self.extra:
            match = pattern.search(spoken)
            if match:
                return ScreeningResult("screening", "custom", match.group(0), 0.7)

        # "Who is this?" style pickup: a person, because a machine announces itself.
        if re.search(r"\b(hello|who\s+is\s+(?:this|calling)|speaking)\b", spoken, re.IGNORECASE):
            return ScreeningResult("live_pickup", "", spoken[:60], 0.6)

        return ScreeningResult("unknown")


@dataclass(frozen=True)
class ScreeningScript:
    """What we say to a screener, and how long we then wait for the human."""

    message: str = DEFAULT_SCREENING_MESSAGE
    wait_for_pickup_s: float = 20.0
    repeat_limit: int = 1

    def __post_init__(self) -> None:
        if not self.message.strip():
            raise ValueError("a screening message cannot be empty")
        if not (1.0 <= self.wait_for_pickup_s <= 60.0):
            raise ValueError("wait_for_pickup_s must be between 1 and 60 seconds")
        if self.repeat_limit < 0:
            raise ValueError("repeat_limit cannot be negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "message": self.message,
            "wait_for_pickup_s": self.wait_for_pickup_s,
            "repeat_limit": self.repeat_limit,
        }


def handle_screening(
    transcript: str,
    *,
    detector: ScreeningDetector | None = None,
    script: ScreeningScript | None = None,
    repeats_done: int = 0,
    voicemail_action: str = "hangup",
) -> dict[str, Any]:
    """Turn one transcript chunk into an action the media pipeline can execute.

    Returns `speak` (with the message), `wait` (keep the line open) or `voicemail` (hand over to the
    voicemail planner). A screener that never results in a pickup eventually becomes `wait` →
    `voicemail`, never a hangup in the middle of the OS's own prompt.
    """
    detector = detector or ScreeningDetector()
    script = script or ScreeningScript()
    result = detector.classify(transcript)

    if result.state == "voicemail":
        return {
            "action": "voicemail",
            "voicemail_action": voicemail_action,
            "classification": result.as_dict(),
        }
    if result.state == "live_pickup":
        return {"action": "resume_conversation", "classification": result.as_dict()}
    if result.state == "screening":
        if repeats_done < script.repeat_limit:
            return {
                "action": "speak",
                "message": script.message,
                "then": "wait_for_pickup",
                "wait_s": script.wait_for_pickup_s,
                "classification": result.as_dict(),
            }
        return {
            "action": "wait",
            "wait_s": script.wait_for_pickup_s,
            "reason": "already answered the screener",
            "classification": result.as_dict(),
        }
    return {
        "action": "wait",
        "wait_s": 5.0,
        "reason": "nothing recognisable yet",
        "classification": result.as_dict(),
    }
