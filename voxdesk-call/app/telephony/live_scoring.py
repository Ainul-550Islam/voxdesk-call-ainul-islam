# File: app/telephony/live_scoring.py — explainable per-turn quality score for a live call
"""Explainable live call scoring (PART 15, P15-02).

The supervisor view needs one number per call -- and, when that number drops,
the reason. So the score is a weighted mean of five *named* components, and
every evaluation keeps both the component values and the weights that produced
them. Changing the tenant's weights tomorrow must not rewrite yesterday's
explanation.

Components (each 0-100, higher is better):

``sentiment``   mean caller sentiment over the window, mapped from -1..1
``trend``       newest half of the window versus the oldest half; a call that is
                sliding is worse than a call that is flat at the same mean
``latency``     p95 agent response latency against the agent's target; a caller
                waiting four seconds cannot be talked into a good score
``talk_ratio``  the agent's share of the words; both monologues and one-word
                replies score badly, the target band does not
``engagement``  repetition and dead-air: an agent repeating itself is looping,
                not helping

Nothing here touches the database, the clock or the network -- `LiveScorer` is a
deterministic function of the turns it is given, which is what makes the scoring
tests meaningful. Persistence and fan-out live in the route/service layer.
"""

from __future__ import annotations

import math
import statistics
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from app.core.logging import log

#: Band edges. `critical` is the band the auto-action engine reacts to.
BAND_OK = 70
BAND_WATCH = 45
BAND_CRITICAL = 0

#: Default weights. They are normalised before use, so a tenant may supply
#: weights that do not sum to one without skewing the scale.
DEFAULT_WEIGHTS: dict[str, float] = {
    "sentiment": 0.28,
    "trend": 0.12,
    "latency": 0.22,
    "talk_ratio": 0.16,
    "engagement": 0.22,
}

COMPONENTS: tuple[str, ...] = tuple(DEFAULT_WEIGHTS)

#: Turns kept per call. Six exchanges is enough for a trend and short enough to
#: forget a bad first minute.
DEFAULT_WINDOW = 12

#: Below this many turns a score would be noise, so we return ``None`` instead.
MIN_SAMPLES = 3

#: Agent share of words that scores full marks. Below it the agent is passive,
#: above it the caller cannot get a word in.
TALK_RATIO_LOW = 0.30
TALK_RATIO_HIGH = 0.60

#: Response latency that scores full marks, and the point where it scores zero.
LATENCY_TARGET_MS = 900.0
LATENCY_FLOOR_MS = 3600.0

#: A repeated agent utterance is penalised this much per occurrence.
REPEAT_PENALTY = 0.25


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    if math.isnan(value):
        return low
    return max(low, min(high, value))


def band_for(score: int) -> str:
    """Map a 0-100 score onto ``ok`` / ``watch`` / ``critical``."""
    if score >= BAND_OK:
        return "ok"
    if score >= BAND_WATCH:
        return "watch"
    return "critical"


def normalise_weights(weights: Mapping[str, float] | None) -> dict[str, float]:
    """Return a weight per component that sums to 1.0.

    Unknown keys are dropped (a typo must not silently become a component with
    weight) and missing keys get weight 0 rather than a guess. An all-zero or
    entirely unknown mapping falls back to the defaults instead of dividing by
    zero.
    """
    source = dict(weights or {})
    cleaned = {name: max(0.0, float(source.get(name, 0.0))) for name in COMPONENTS}
    total = sum(cleaned.values())
    if total <= 0:
        return dict(DEFAULT_WEIGHTS)
    return {name: value / total for name, value in cleaned.items()}


def percentile(values: Sequence[float], q: float) -> float:
    """Linear-interpolated percentile; ``values`` may be unsorted."""
    if not values:
        return 0.0
    ordered = sorted(float(v) for v in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * _clamp(q, 0.0, 1.0)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[int(position)]
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


@dataclass(frozen=True, slots=True)
class Turn:
    """One scored turn of a live call."""

    speaker: str  # "caller" | "agent"
    sentiment: float = 0.0  # -1.0 .. 1.0
    latency_ms: float = 0.0  # agent response time preceding this turn
    text: str = ""

    def __post_init__(self) -> None:
        if self.speaker not in ("caller", "agent"):
            raise ValueError(f"unknown speaker {self.speaker!r}")

    @property
    def words(self) -> int:
        return len(self.text.split())


@dataclass(frozen=True, slots=True)
class LiveScore:
    """One evaluation: the number, the components and the reasons."""

    score: int
    band: str
    components: dict[str, float]
    weights: dict[str, float]
    sample_count: int
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "band": self.band,
            "components": {k: round(v, 2) for k, v in self.components.items()},
            "weights": {k: round(v, 4) for k, v in self.weights.items()},
            "sample_count": self.sample_count,
            "reasons": list(self.reasons),
        }


def _sentiment_component(turns: Sequence[Turn]) -> float:
    caller = [t.sentiment for t in turns if t.speaker == "caller"] or [
        t.sentiment for t in turns
    ]
    mean = statistics.fmean(caller)
    return _clamp((mean + 1.0) / 2.0 * 100.0)


def _trend_component(turns: Sequence[Turn]) -> float:
    caller = [t.sentiment for t in turns if t.speaker == "caller"] or [
        t.sentiment for t in turns
    ]
    if len(caller) < 2:
        return 50.0
    half = len(caller) // 2
    older = statistics.fmean(caller[:half]) if half else caller[0]
    newer = statistics.fmean(caller[len(caller) - half :])
    # A full swing from -1 to +1 (delta 2.0) is 50 points of component.
    return _clamp(50.0 + (newer - older) * 25.0)


def _latency_component(turns: Sequence[Turn]) -> float:
    latencies = [t.latency_ms for t in turns if t.speaker == "agent" and t.latency_ms > 0]
    if not latencies:
        return 50.0
    p95 = percentile(latencies, 0.95)
    if p95 <= LATENCY_TARGET_MS:
        return 100.0
    if p95 >= LATENCY_FLOOR_MS:
        return 0.0
    span = LATENCY_FLOOR_MS - LATENCY_TARGET_MS
    return _clamp(100.0 * (1.0 - (p95 - LATENCY_TARGET_MS) / span))


def _talk_ratio(turns: Sequence[Turn]) -> float | None:
    agent_words = sum(t.words for t in turns if t.speaker == "agent")
    caller_words = sum(t.words for t in turns if t.speaker == "caller")
    total = agent_words + caller_words
    if total == 0:
        return None
    return agent_words / total


def _talk_ratio_component(turns: Sequence[Turn]) -> float:
    ratio = _talk_ratio(turns)
    if ratio is None:
        return 50.0
    if TALK_RATIO_LOW <= ratio <= TALK_RATIO_HIGH:
        return 100.0
    if ratio < TALK_RATIO_LOW:
        # 0.0 share is a silent agent: 0 points; the low edge is 100.
        return _clamp(ratio / TALK_RATIO_LOW * 100.0)
    headroom = 1.0 - TALK_RATIO_HIGH
    if headroom <= 0:
        return 0.0
    return _clamp((1.0 - ratio) / headroom * 100.0)


def _engagement_component(turns: Sequence[Turn]) -> float:
    agent_turns = [t for t in turns if t.speaker == "agent"]
    if not agent_turns:
        return 50.0
    seen: set[str] = set()
    repeats = 0
    for turn in agent_turns:
        key = " ".join(turn.text.lower().split())
        if key and key in seen:
            repeats += 1
        if key:
            seen.add(key)
    score = 100.0 - (repeats * REPEAT_PENALTY * 100.0)
    # Empty agent turns are dead air, not engagement.
    empty = sum(1 for t in agent_turns if t.words == 0)
    score -= empty * 20.0
    return _clamp(score)


def _reasons(components: Mapping[str, float], turns: Sequence[Turn]) -> tuple[str, ...]:
    """Plain-language reasons for the components that are dragging the score."""
    out: list[str] = []
    if components["sentiment"] < 40:
        out.append("caller sentiment is negative")
    if components["trend"] < 40:
        out.append("caller sentiment is falling")
    if components["latency"] < 40:
        p95 = percentile(
            [t.latency_ms for t in turns if t.speaker == "agent" and t.latency_ms > 0] or [0.0],
            0.95,
        )
        out.append(f"agent response p95 is {int(p95)} ms")
    if components["talk_ratio"] < 40:
        ratio = _talk_ratio(turns)
        if ratio is not None:
            if ratio > TALK_RATIO_HIGH:
                out.append(f"agent is talking {int(ratio * 100)}% of the time")
            else:
                out.append("agent is barely responding")
    if components["engagement"] < 40:
        out.append("agent is repeating itself")
    return tuple(out)


def score_turns(
    turns: Iterable[Turn],
    *,
    weights: Mapping[str, float] | None = None,
    min_samples: int = MIN_SAMPLES,
) -> LiveScore | None:
    """Score a sequence of turns, or ``None`` when there is not enough signal."""
    window = list(turns)
    if len(window) < min_samples:
        return None

    components = {
        "sentiment": _sentiment_component(window),
        "trend": _trend_component(window),
        "latency": _latency_component(window),
        "talk_ratio": _talk_ratio_component(window),
        "engagement": _engagement_component(window),
    }
    effective = normalise_weights(weights)
    weighted = sum(components[name] * effective[name] for name in COMPONENTS)
    score = int(round(_clamp(weighted)))
    return LiveScore(
        score=score,
        band=band_for(score),
        components={name: round(value, 4) for name, value in components.items()},
        weights=effective,
        sample_count=len(window),
        reasons=_reasons(components, window),
    )


class LiveScorer:
    """Per-call sliding window of turns.

    One instance per process is enough; state is keyed by call id and dropped
    on ``forget`` or when the window is exceeded. Not thread-safe by design --
    a live call is driven by one task in the voice pipeline.
    """

    def __init__(
        self,
        *,
        window: int = DEFAULT_WINDOW,
        weights: Mapping[str, float] | None = None,
    ) -> None:
        if window < MIN_SAMPLES:
            raise ValueError(f"window must be >= {MIN_SAMPLES}")
        self._window = window
        self._weights = normalise_weights(weights)
        self._turns: dict[str, deque[Turn]] = {}

    def observe(self, call_id: str, turn: Turn) -> LiveScore | None:
        key = str(call_id)
        bucket = self._turns.setdefault(key, deque(maxlen=self._window))
        bucket.append(turn)
        return score_turns(bucket, weights=self._weights)

    def add(
        self,
        call_id: str,
        *,
        speaker: str,
        sentiment: float = 0.0,
        latency_ms: float = 0.0,
        text: str = "",
    ) -> LiveScore | None:
        return self.observe(
            call_id, Turn(speaker=speaker, sentiment=sentiment, latency_ms=latency_ms, text=text)
        )

    def current(self, call_id: str) -> LiveScore | None:
        bucket = self._turns.get(str(call_id))
        if not bucket:
            return None
        return score_turns(bucket, weights=self._weights)

    def sample_count(self, call_id: str) -> int:
        return len(self._turns.get(str(call_id), ()))

    def forget(self, call_id: str) -> None:
        """Drop a call's window (called when the call ends)."""
        self._turns.pop(str(call_id), None)

    def active_calls(self) -> list[str]:
        return list(self._turns)


_default_scorer: LiveScorer | None = None


def get_live_scorer() -> LiveScorer:
    global _default_scorer
    if _default_scorer is None:
        _default_scorer = LiveScorer()
    return _default_scorer


async def publish_score(call_id: str, score: LiveScore) -> int:
    """Fan the score out to supervisor sockets. Never raises.

    A monitoring bus hiccup must not break the call it is monitoring, so the
    failure is logged and swallowed.
    """
    from app.telephony.monitor_bus import get_monitor_bus

    try:
        return await get_monitor_bus().publish_live_score(call_id, score.as_dict())
    except Exception as exc:  # pragma: no cover - defensive
        log.warning(
            "live_scoring.publish_failed",
            call_id=str(call_id),
            error_type=type(exc).__name__,
        )
        return 0
