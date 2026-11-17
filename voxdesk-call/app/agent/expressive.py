"""Expressive mode — emotion tags, per-provider mapping, and listening features (P12-02).

Three separate jobs, deliberately kept apart:

* :class:`EmotionTagParser` finds ``[sigh]`` / ``[long pause]`` style markers the LLM wrote and
  removes them from the text that gets spoken as words.
* :class:`EmotionMapper` turns a *semantic* emotion into whatever the selected TTS provider
  actually accepts. Providers that accept nothing get an honest empty mapping instead of a
  parameter that is silently dropped.
* :class:`ListeningFeatures` turns the caller's own delivery (pace, pauses, pitch spread) into a
  short context line for the LLM, so the agent can react to a hurried or hesitant caller.

Nothing here calls a provider: the payloads are request shapes that the provider adapters send.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from pipecat.frames.frames import Frame, TextFrame, TTSUpdateSettingsFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

#: Canonical tag → the ways an LLM tends to write it.
TAG_ALIASES: dict[str, tuple[str, ...]] = {
    "sigh": ("sigh", "sighs", "sighing", "deep sigh"),
    "laugh": ("laugh", "laughs", "laughter", "giggle"),
    "chuckle": ("chuckle", "chuckles", "smile"),
    "breath": ("breath", "breathe", "inhale", "exhale"),
    "whisper": ("whisper", "whispers", "quietly"),
    "short pause": ("short pause", "pause", "beat", "hesitate"),
    "long pause": ("long pause", "long silence", "silence", "think"),
}

ALIAS_TO_CANONICAL: dict[str, str] = {
    alias.lower(): canonical for canonical, aliases in TAG_ALIASES.items() for alias in aliases
}

_TAG_RE = re.compile(r"\[\s*([A-Za-z _-]{2,24})\s*\]|\*\s*([A-Za-z _-]{2,24})\s*\*")

#: Which semantic emotion each provider can express, and how.
PROVIDER_EMOTION_SUPPORT: dict[str, dict[str, Any]] = {
    "elevenlabs": {
        "supports_tags": True,
        "supports_style": True,
        "emotions": ("neutral", "warm", "calm", "serious", "excited"),
    },
    "elevenlabs_v3": {
        "supports_tags": True,
        "supports_style": True,
        "emotions": ("neutral", "warm", "calm", "serious", "excited", "sad", "angry"),
    },
    "cartesia": {
        "supports_tags": False,
        "supports_style": True,
        "emotions": ("neutral", "calm", "warm", "excited", "sad", "angry"),
    },
    "minimax": {
        "supports_tags": False,
        "supports_style": True,
        "emotions": ("neutral", "happy", "sad", "angry", "fearful", "surprised"),
    },
    "fish_audio": {
        "supports_tags": False,
        "supports_style": True,
        "emotions": ("neutral", "happy", "sad", "angry", "calm"),
    },
    "openai": {"supports_tags": False, "supports_style": False, "emotions": ("neutral",)},
    "deepgram": {"supports_tags": False, "supports_style": False, "emotions": ("neutral",)},
}

#: Semantic emotion → provider-native value. Missing entries fall back to the provider default.
EMOTION_MAP: dict[str, dict[str, str]] = {
    "neutral": {"elevenlabs": "neutral", "cartesia": "neutral", "minimax": "neutral", "fish_audio": "neutral"},
    "warm": {"elevenlabs": "warm", "cartesia": "warm", "minimax": "happy", "fish_audio": "happy"},
    "calm": {"elevenlabs": "calm", "cartesia": "calm", "minimax": "neutral", "fish_audio": "calm"},
    "serious": {"elevenlabs": "serious", "cartesia": "calm", "minimax": "neutral", "fish_audio": "calm"},
    "excited": {"elevenlabs": "excited", "cartesia": "excited", "minimax": "happy", "fish_audio": "happy"},
    "sad": {"elevenlabs": "sad", "cartesia": "sad", "minimax": "sad", "fish_audio": "sad"},
    "angry": {"elevenlabs": "angry", "cartesia": "angry", "minimax": "angry", "fish_audio": "angry"},
    "happy": {"elevenlabs": "excited", "cartesia": "excited", "minimax": "happy", "fish_audio": "happy"},
    "surprised": {"elevenlabs": "excited", "cartesia": "excited", "minimax": "surprised", "fish_audio": "happy"},
    "fearful": {"elevenlabs": "calm", "cartesia": "calm", "minimax": "fearful", "fish_audio": "calm"},
}

#: Tag → emotion a planner may attach to it.
TAG_EMOTION: dict[str, str] = {
    "sigh": "calm",
    "laugh": "warm",
    "chuckle": "warm",
    "breath": "calm",
    "whisper": "calm",
    "short pause": "neutral",
    "long pause": "neutral",
}

DEFAULT_ALLOWED_EMOTIONS: tuple[str, ...] = ("neutral", "warm", "calm", "serious", "excited")
DEFAULT_ALLOWED_TAGS: tuple[str, ...] = ("sigh", "chuckle", "short pause", "long pause")


@dataclass(frozen=True)
class EmotionSpan:
    """One tag found in the text, with where it was and what it means."""

    raw: str
    canonical: str
    index: int
    emotion: str


@dataclass
class ParsedEmotion:
    text: str
    spans: list[EmotionSpan] = field(default_factory=list)

    @property
    def emotions(self) -> list[str]:
        seen: list[str] = []
        for span in self.spans:
            if span.emotion not in seen:
                seen.append(span.emotion)
        return seen

    @property
    def has_tags(self) -> bool:
        return bool(self.spans)


class EmotionTagParser:
    """Extract and canonicalise ``[sigh]``-style markers; never leave them in spoken text."""

    def __init__(self, allowed_tags: Sequence[str] | None = None) -> None:
        allowed = tuple(allowed_tags) if allowed_tags is not None else tuple(TAG_ALIASES)
        unknown = [tag for tag in allowed if tag not in TAG_ALIASES]
        if unknown:
            raise ValueError(f"Unknown emotion tags: {unknown}. Known: {sorted(TAG_ALIASES)}")
        self.allowed_tags = allowed

    def parse(self, text: str) -> ParsedEmotion:
        """Split markers from spoken words; unknown markers are dropped, not spoken."""
        if not text:
            return ParsedEmotion(text=text or "")
        spans: list[EmotionSpan] = []
        for match in _TAG_RE.finditer(text):
            raw = (match.group(1) or match.group(2) or "").strip()
            canonical = ALIAS_TO_CANONICAL.get(raw.lower())
            if canonical is None or canonical not in self.allowed_tags:
                # Recorded so support can see the LLM tried, but never spoken aloud.
                spans.append(
                    EmotionSpan(raw=f"[{raw}]", canonical="unknown", index=match.start(), emotion="neutral")
                )
                continue
            spans.append(
                EmotionSpan(
                    raw=f"[{raw}]",
                    canonical=canonical,
                    index=match.start(),
                    emotion=TAG_EMOTION.get(canonical, "neutral"),
                )
            )
        spoken = _TAG_RE.sub("", text)
        spoken = re.sub(r"\s+([,.!?;:])", r"\1", spoken)
        spoken = re.sub(r"\s{2,}", " ", spoken).strip()
        return ParsedEmotion(text=spoken, spans=spans)

    def strip(self, text: str) -> str:
        return self.parse(text).text


def supports_emotion(provider: str, emotion: str) -> bool:
    spec = PROVIDER_EMOTION_SUPPORT.get(provider)
    if not spec:
        return False
    return emotion in spec["emotions"] or EMOTION_MAP.get(emotion, {}).get(provider) in spec["emotions"]


class EmotionMapper:
    """Map a semantic emotion onto the parameters the chosen provider actually accepts."""

    def __init__(self, provider: str) -> None:
        if provider not in PROVIDER_EMOTION_SUPPORT:
            raise ValueError(
                f"Unknown TTS provider {provider!r}. Known: {sorted(PROVIDER_EMOTION_SUPPORT)}"
            )
        self.provider = provider

    @property
    def capabilities(self) -> dict[str, Any]:
        return dict(PROVIDER_EMOTION_SUPPORT[self.provider])

    def map(self, emotion: str) -> dict[str, Any]:
        """Provider parameters for ``emotion``; empty when the provider cannot express it."""
        spec = PROVIDER_EMOTION_SUPPORT[self.provider]
        native = EMOTION_MAP.get(emotion, {}).get(self.provider)
        if not spec["supports_style"] or native is None or native not in spec["emotions"]:
            return {}
        if self.provider.startswith("elevenlabs"):
            # ElevenLabs: `style` amplifies the chosen voice; stability drops as style rises.
            style = {"neutral": 0.0, "warm": 0.35, "calm": 0.2, "serious": 0.15, "excited": 0.7,
                     "sad": 0.3, "angry": 0.8}.get(native, 0.0)
            return {"voice_settings": {"style": style, "stability": round(0.75 - style / 2, 3)}}
        if self.provider == "cartesia":
            return {"emotion": native}
        if self.provider == "minimax":
            return {"emotion": native}
        if self.provider == "fish_audio":
            return {"emotion": native}
        return {}

    def render(self, text: str, *, default_emotion: str = "neutral") -> dict[str, Any]:
        """Return ``{"text": ..., "params": {...}}`` ready for the provider adapter."""
        parsed = EmotionTagParser().parse(text)
        emotion = parsed.emotions[0] if parsed.emotions else default_emotion
        spec = PROVIDER_EMOTION_SUPPORT[self.provider]
        spoken = text
        if not spec["supports_tags"]:
            spoken = parsed.text
        else:
            spoken = _TAG_RE.sub(
                lambda m: f"[{ALIAS_TO_CANONICAL[(m.group(1) or m.group(2)).strip().lower()]}]"
                if ALIAS_TO_CANONICAL.get((m.group(1) or m.group(2) or "").strip().lower()) else "",
                text,
            )
        return {"text": spoken, "params": self.map(emotion), "emotion": emotion}


class AutoEmotionPlanner:
    """Place a small, bounded number of tags where they help and nowhere else.

    Deterministic on purpose: the same reply always gets the same tags, so a regression in tag
    placement shows up as a diff rather than as a mood swing.
    """

    def __init__(
        self,
        *,
        allowed_emotions: Sequence[str] = DEFAULT_ALLOWED_EMOTIONS,
        allowed_tags: Sequence[str] = DEFAULT_ALLOWED_TAGS,
        max_tags: int = 2,
    ) -> None:
        self.allowed_emotions = tuple(allowed_emotions)
        self.allowed_tags = tuple(allowed_tags)
        self.max_tags = max(0, int(max_tags))

    def plan(self, text: str) -> str:
        if not text or not self.max_tags:
            return text
        if EmotionTagParser().parse(text).has_tags:
            return text  # manual tags win; never stack automatic ones on top
        tagged = text
        inserted = 0
        lowered = text.lower()

        def _add(tag: str, prefix: str = "") -> None:
            nonlocal tagged, inserted
            if inserted >= self.max_tags or tag not in self.allowed_tags:
                return
            if TAG_EMOTION.get(tag, "neutral") not in self.allowed_emotions:
                return
            tagged = f"[{tag}] {tagged}" if not prefix else tagged.replace(prefix, f"{prefix}[{tag}] ", 1)
            inserted += 1

        if any(marker in lowered for marker in ("sorry", "apolog", "unfortunately")):
            _add("sigh")
        elif any(marker in lowered for marker in ("great news", "perfect", "you're all set", "all set")):
            _add("chuckle")
        elif any(marker in lowered for marker in ("take your time", "no rush", "whenever you're ready")):
            _add("short pause")

        word_count = len(text.split())
        boundary = re.search(r"[.!?]\s", tagged)
        if (
            inserted < self.max_tags
            and word_count >= 40
            and boundary is not None
            and "long pause" in self.allowed_tags
            and TAG_EMOTION.get("long pause", "neutral") in self.allowed_emotions
        ):
            # After the first sentence, never mid-clause: a pause inside a clause sounds like
            # a dropped line rather than a breath.
            tagged = f"{tagged[:boundary.end()]}[long pause] {tagged[boundary.end():]}"
            inserted += 1
        return tagged


@dataclass(frozen=True)
class ListeningFeatures:
    """The caller's delivery, as context for the LLM.

    Built from VAD-segmented speech runs, so it needs no extra model and no extra audio path.
    """

    pace_wpm: float = 0.0
    mean_pause_ms: float = 0.0
    longest_pause_ms: float = 0.0
    speech_ratio: float = 0.0
    pitch_spread_hz: float = 0.0

    @classmethod
    def from_segments(
        cls,
        segments: Iterable[tuple[bool, float]],
        *,
        words: int,
        pitch_samples_hz: Sequence[float] | None = None,
    ) -> "ListeningFeatures":
        runs = list(segments)
        if not runs:
            return cls()
        speech_ms = sum(ms for is_speech, ms in runs if is_speech)
        total_ms = sum(ms for _, ms in runs) or 1.0
        pauses = [ms for is_speech, ms in runs if not is_speech]
        pace = (words / (speech_ms / 1000.0)) * 60.0 if speech_ms > 0 else 0.0
        spread = 0.0
        if pitch_samples_hz and len(pitch_samples_hz) > 1:
            values = [float(v) for v in pitch_samples_hz]
            spread = max(values) - min(values)
        return cls(
            pace_wpm=round(pace, 1),
            mean_pause_ms=round(sum(pauses) / len(pauses), 1) if pauses else 0.0,
            longest_pause_ms=round(max(pauses), 1) if pauses else 0.0,
            speech_ratio=round(speech_ms / total_ms, 3),
            pitch_spread_hz=round(spread, 1),
        )

    @property
    def pace_label(self) -> str:
        if self.pace_wpm == 0:
            return "unknown"
        if self.pace_wpm >= 180:
            return "fast"
        if self.pace_wpm <= 110:
            return "slow"
        return "normal"

    def to_context(self) -> str:
        """One line for the system prompt. Empty when there is nothing worth saying."""
        if not self.pace_wpm and not self.mean_pause_ms:
            return ""
        parts: list[str] = []
        if self.pace_label == "fast":
            parts.append("The caller is speaking quickly, so keep replies short and get to the point.")
        elif self.pace_label == "slow":
            parts.append("The caller is speaking slowly; slow down slightly and give them time.")
        if self.longest_pause_ms >= 1500:
            parts.append("They are pausing for a long time; do not fill the silence with questions.")
        elif self.mean_pause_ms and self.mean_pause_ms <= 200 and self.pace_label == "fast":
            parts.append("They barely pause, so avoid interruptions.")
        return " ".join(parts)

    def as_dict(self) -> dict[str, Any]:
        return {
            "pace_wpm": self.pace_wpm,
            "pace_label": self.pace_label,
            "mean_pause_ms": self.mean_pause_ms,
            "longest_pause_ms": self.longest_pause_ms,
            "speech_ratio": self.speech_ratio,
            "pitch_spread_hz": self.pitch_spread_hz,
        }


class ExpressiveTextProcessor(FrameProcessor):
    """Apply opt-in emotion tags and provider-safe ElevenLabs style updates."""

    def __init__(
        self,
        *,
        provider: str,
        model: str = "",
        enabled: bool = False,
        auto: bool = False,
        allowed_emotions: Sequence[str] = DEFAULT_ALLOWED_EMOTIONS,
        apply_provider_settings: bool = True,
    ) -> None:
        super().__init__()
        self.provider = (provider or "").strip().lower()
        self.model = (model or "").strip().lower()
        self.enabled = bool(enabled)
        self.auto = bool(auto)
        self.allowed_emotions = tuple(str(value).strip().lower() for value in allowed_emotions)
        self.apply_provider_settings = bool(apply_provider_settings)
        allowed_tags = tuple(
            tag for tag, emotion in TAG_EMOTION.items() if emotion in self.allowed_emotions
        )
        self.parser = EmotionTagParser(allowed_tags=allowed_tags)
        self.planner = AutoEmotionPlanner(allowed_emotions=self.allowed_emotions)

    def _canonical_v3_text(self, text: str) -> str:
        allowed_tags = set(self.parser.allowed_tags)

        def replace(match: re.Match[str]) -> str:
            raw = (match.group(1) or match.group(2) or "").strip().lower()
            canonical = ALIAS_TO_CANONICAL.get(raw)
            return f"[{canonical}]" if canonical in allowed_tags else ""

        return _TAG_RE.sub(replace, text)

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if self.enabled and isinstance(frame, TextFrame) and frame.text:
            planned = self.planner.plan(frame.text) if self.auto else frame.text
            parsed = self.parser.parse(planned)
            is_elevenlabs = self.provider in {"elevenlabs", "elevenlabs_v3"}
            supports_v3_tags = is_elevenlabs and "v3" in self.model
            if supports_v3_tags:
                frame.text = self._canonical_v3_text(planned)
            else:
                frame.text = parsed.text
                if (
                    self.provider == "elevenlabs"
                    and self.apply_provider_settings
                ):
                    emotion = parsed.emotions[0] if parsed.emotions else "neutral"
                    voice_settings = EmotionMapper("elevenlabs").map(emotion).get("voice_settings")
                    if voice_settings:
                        await self.push_frame(
                            TTSUpdateSettingsFrame(settings=voice_settings), direction
                        )
        await self.push_frame(frame, direction)
