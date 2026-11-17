"""Adaptive pace and response eagerness (P12-03).

Two behaviours that share one measurement:

* **Pace** — the agent's TTS speed drifts towards the caller's own speaking rate, inside a
  hard ``[min_speed, max_speed]`` band. A fast talker gets ``1.15×``, a slow one ``0.9×``; the
  band means a mis-detected rate can never produce a chipmunk.
* **Eagerness** — the same rate feeds the turn-taking config, so a caller who has clearly
  finished speaking gets an answer sooner (lower ``stop_secs``).

Explicit requests win over inference: "can you slow down" pins the speed until the caller says
otherwise (or until the pin expires), because a caller who asked twice and got the same speed
twice will hang up.

Everything is a pure function of the observed turns, so it is unit-testable without audio.
"""

from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Iterable

from pipecat.frames.frames import Frame, InterimTranscriptionFrame, TranscriptionFrame, UserStartedSpeakingFrame, UserStoppedSpeakingFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

DEFAULT_MIN_SPEED = 0.8
DEFAULT_MAX_SPEED = 1.2
DEFAULT_WINDOW = 5
DEFAULT_TARGET_WPM = 150.0

#: Phrases that are unambiguously about the *agent's* delivery. Kept deliberately narrow: a
#: caller saying "slow down the elevator music" must not rescale the voice.
_SLOW_DOWN = re.compile(
    r"\b(?:can|could|would|will)?\s*(?:you\s+)?(?:please\s+)?"
    r"(?:slow(?:er)?\s*(?:down|it down|a bit|a little|please)?|speak\s+(?:more\s+)?slow(?:ly)?|"
    r"talk\s+(?:more\s+)?slow(?:ly)?|not\s+so\s+fast|too\s+fast)\b",
    re.IGNORECASE,
)
_SPEED_UP = re.compile(
    r"\b(?:can|could|would|will)?\s*(?:you\s+)?(?:please\s+)?"
    r"(?:speed\s+(?:it\s+)?up|talk\s+(?:more\s+)?fast(?:er)?|speak\s+(?:more\s+)?fast(?:er)?|"
    r"hurry\s+up|get\s+to\s+the\s+point|too\s+slow)\b",
    re.IGNORECASE,
)
_NORMAL_PACE = re.compile(
    r"\b(?:normal\s+(?:speed|pace)|(?:that'?s|this\s+is)\s+(?:better|fine|good)\s+now)\b",
    re.IGNORECASE,
)


#: A request that changes the agent's delivery is addressed to it: "can you…", "please…", or an
#: imperative first word. Everything else is a caller talking about a slow lane on the motorway.
_ADDRESSED = re.compile(r"\b(you|your|please)\b", re.IGNORECASE)
_IMPERATIVE_OPENERS = frozenset(
    {"slow", "slower", "faster", "speak", "talk", "speed", "hurry", "repeat", "louder", "quieter"}
)


@dataclass(frozen=True)
class PaceDecision:
    """What the pipeline should do with the agent's voice right now."""

    speed: float
    eagerness: float
    source: str  # "manual" | "adaptive" | "default"
    user_wpm: float = 0.0
    reason: str = ""

    def as_dict(self) -> dict[str, float | str]:
        return {
            "speed": round(self.speed, 3),
            "eagerness": round(self.eagerness, 3),
            "source": self.source,
            "user_wpm": round(self.user_wpm, 1),
            "reason": self.reason,
        }


@dataclass
class PaceAdapter:
    """Rolling estimate of the caller's speaking rate → TTS speed and eagerness."""

    min_speed: float = DEFAULT_MIN_SPEED
    max_speed: float = DEFAULT_MAX_SPEED
    target_wpm: float = DEFAULT_TARGET_WPM
    window: int = DEFAULT_WINDOW
    auto_mode: bool = True
    _samples: deque[float] = field(default_factory=deque, repr=False)
    _manual_speed: float | None = None
    _manual_turns_left: int = 0

    def __post_init__(self) -> None:
        if not (0.5 <= self.min_speed < self.max_speed <= 2.0):
            raise ValueError("require 0.5 <= min_speed < max_speed <= 2.0")
        if self.window < 1:
            raise ValueError("window must be >= 1")
        self._samples = deque(maxlen=self.window)

    # -- observation -------------------------------------------------------
    def observe_turn(self, *, words: int, duration_ms: float) -> None:
        """Record one caller turn. Turns under 300 ms carry no usable rate."""
        if duration_ms < 300 or words <= 0:
            return
        self._samples.append(words / (duration_ms / 1000.0) * 60.0)

    def observe_utterance(self, text: str, duration_ms: float) -> None:
        self.observe_turn(words=len(text.split()), duration_ms=duration_ms)

    @property
    def user_wpm(self) -> float:
        if not self._samples:
            return 0.0
        return round(sum(self._samples) / len(self._samples), 1)

    # -- explicit requests --------------------------------------------------
    def handle_intent(self, text: str, *, hold_turns: int = 6) -> str | None:
        """Detect "slow down" / "talk faster" / "normal speed"; returns the intent or ``None``.

        A request must plausibly be addressed to the agent: it has to say "you"/"please", start
        with an imperative verb, or be a short "that's better now" acknowledgement. Without that
        gate, "the slow down lane was closed" rescales the agent's voice mid-sentence.

        A manual request holds for ``hold_turns`` agent turns, then inference takes over again.
        """
        if not text:
            return None
        words = text.strip().lower().split()
        if len(words) > 14:
            return None
        addressed = bool(_ADDRESSED.search(text)) or (words and words[0] in _IMPERATIVE_OPENERS)
        if not addressed and not _NORMAL_PACE.search(text):
            return None
        if _NORMAL_PACE.search(text):
            self._manual_speed = None
            self._manual_turns_left = 0
            return "normal"
        if _SLOW_DOWN.search(text):
            base = self._manual_speed or self.recommended_speed()
            self._manual_speed = max(self.min_speed, round(base - 0.1, 3))
            self._manual_turns_left = hold_turns
            return "slow_down"
        if _SPEED_UP.search(text):
            base = self._manual_speed or self.recommended_speed()
            self._manual_speed = min(self.max_speed, round(base + 0.1, 3))
            self._manual_turns_left = hold_turns
            return "speed_up"
        return None

    def tick(self) -> None:
        """Call once per agent turn so a manual pin expires on its own."""
        if self._manual_turns_left > 0:
            self._manual_turns_left -= 1
            if self._manual_turns_left == 0:
                self._manual_speed = None

    # -- output -------------------------------------------------------------
    def recommended_speed(self) -> float:
        if not self._samples or self.target_wpm <= 0:
            return 1.0
        ratio = self.user_wpm / self.target_wpm
        # Compress the ratio: a caller 20 % faster should not make the agent 20 % faster.
        speed = 1.0 + (ratio - 1.0) * 0.5
        return max(self.min_speed, min(self.max_speed, round(speed, 3)))

    def eagerness(self) -> float:
        """0..1 hint for turn-taking: fast, low-pause callers want a quicker answer."""
        if not self._samples:
            return 0.7
        ratio = self.user_wpm / self.target_wpm if self.target_wpm else 1.0
        return max(0.0, min(1.0, round(0.5 + (ratio - 1.0) * 0.5, 3)))

    def decide(self) -> PaceDecision:
        if not self.auto_mode:
            return PaceDecision(speed=1.0, eagerness=0.7, source="default",
                                user_wpm=self.user_wpm, reason="auto pace disabled")
        if self._manual_speed is not None:
            return PaceDecision(
                speed=self._manual_speed,
                eagerness=self.eagerness(),
                source="manual",
                user_wpm=self.user_wpm,
                reason=f"caller asked for a change; {self._manual_turns_left} turn(s) left",
            )
        if not self._samples:
            return PaceDecision(speed=1.0, eagerness=0.7, source="default", reason="no turns observed")
        return PaceDecision(
            speed=self.recommended_speed(),
            eagerness=self.eagerness(),
            source="adaptive",
            user_wpm=self.user_wpm,
            reason=f"caller speaks about {self.user_wpm:.0f} wpm",
        )

    def to_context(self) -> str:
        decision = self.decide()
        if decision.source == "default":
            return ""
        if decision.source == "manual":
            return (
                "The caller has asked you to change your speed. Keep this pace unless they ask "
                "again, and do not mention the change."
            )
        if decision.user_wpm and decision.user_wpm >= self.target_wpm * 1.25:
            return "The caller talks fast. Match their energy but keep answers short."
        if decision.user_wpm and decision.user_wpm <= self.target_wpm * 0.75:
            return "The caller talks slowly. Give them room and speak at a relaxed pace."
        return ""


def summarise_turns(turns: Iterable[tuple[int, float]]) -> dict[str, float]:
    """Convenience helper for logs/telemetry: ``(words, duration_ms)`` pairs → rate stats."""
    adapter = PaceAdapter()
    for words, duration_ms in turns:
        adapter.observe_turn(words=words, duration_ms=duration_ms)
    decision = adapter.decide()
    return {"user_wpm": adapter.user_wpm, "speed": decision.speed, "eagerness": decision.eagerness}


class AdaptivePaceProcessor(FrameProcessor):
    """Observe final caller turns, adjust supported TTS speed, and tune the next VAD pause."""

    def __init__(
        self,
        *,
        tts: Any,
        turn_config: Any,
        enabled: bool = True,
        min_speed: float = DEFAULT_MIN_SPEED,
        max_speed: float = DEFAULT_MAX_SPEED,
        base_speed: float = 1.0,
        clock=time.monotonic,
    ) -> None:
        super().__init__()
        self.adapter = PaceAdapter(
            min_speed=float(min_speed),
            max_speed=float(max_speed),
            auto_mode=bool(enabled),
        )
        self.tts = tts
        self.turn_config = turn_config
        self.base_speed = max(0.7, min(1.2, float(base_speed)))
        # FrameProcessor already owns ``_clock`` for Pipecat's BaseClock; keep the
        # policy's injectable monotonic callable under a distinct name.
        self._monotonic_now = clock
        self._turn_started_at: float | None = None
        self._turn_stopped_at: float | None = None
        self.decisions: list[PaceDecision] = []
        self.applied_speeds: list[float] = []
        self._base_stop_secs = float(getattr(getattr(turn_config, "vad_params", None), "stop_secs", 0.5))

    async def _apply_decision(self, decision: PaceDecision) -> None:
        self.decisions.append(decision)
        if decision.source == "default":
            return
        effective_speed = max(0.7, min(1.2, self.base_speed * decision.speed))
        update_speed = getattr(self.tts, "update_speed", None)
        if callable(update_speed):
            try:
                result = await update_speed(effective_speed)
                if result is not False:
                    self.applied_speeds.append(effective_speed)
            except Exception:
                __import__("logging").getLogger(__name__).debug(
                    "adaptive_pace_tts_update_failed", exc_info=True
                )

        # This value applies to the next turn: callers who answer quickly need
        # less silence before the assistant begins. Never compound on a prior turn.
        scale = 1.15 - 0.3 * max(0.0, min(1.0, decision.eagerness))
        stop_secs = max(0.15, min(1.5, self._base_stop_secs * scale))
        try:
            self.turn_config.vad_analyzer.params.stop_secs = stop_secs
        except Exception:
            pass
        analyzer = getattr(self.turn_config, "turn_analyzer", None)
        try:
            if analyzer is not None and hasattr(analyzer, "params"):
                analyzer.params.stop_secs = stop_secs
        except Exception:
            pass

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if direction == FrameDirection.DOWNSTREAM:
            now = self._monotonic_now()
            if isinstance(frame, UserStartedSpeakingFrame):
                self._turn_started_at = now
            elif isinstance(frame, UserStoppedSpeakingFrame):
                self._turn_stopped_at = now
            elif (
                isinstance(frame, TranscriptionFrame)
                and not isinstance(frame, InterimTranscriptionFrame)
                and str(getattr(frame, "text", "") or "").strip()
            ):
                text = str(frame.text).strip()
                end = self._turn_stopped_at or now
                if self._turn_started_at is not None:
                    duration_ms = max(300.0, (end - self._turn_started_at) * 1000.0)
                else:
                    # A provider may omit user-speaking events; use a conservative
                    # word-rate estimate rather than claiming an observed duration.
                    duration_ms = max(300.0, len(text.split()) / 2.5 * 1000.0)
                self.adapter.tick()
                self.adapter.observe_utterance(text, duration_ms)
                self.adapter.handle_intent(text)
                decision = self.adapter.decide()
                await self._apply_decision(decision)
                self._turn_started_at = None
                self._turn_stopped_at = None
        await self.push_frame(frame, direction)
