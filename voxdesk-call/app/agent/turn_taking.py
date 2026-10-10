"""Smart turn-taking configuration and semantic end-of-turn analyzer (Sub-Phase 2B).

Maps per-agent `RuntimeConfig` turn-taking parameters:
- `responsiveness` in [0.0, 1.0] -> VAD `stop_secs` in [1.2, 0.2] (linear interpolation)
- `interruption_sensitivity` in [0.0, 1.0] -> VAD `confidence` in [0.85, 0.45] and `min_volume` in [0.80, 0.30]
- `enable_smart_turn` -> Pipecat `LocalSmartTurnAnalyzerV3` (when ONNX weights are present) or
  `SmartTurnAnalyzer` (`BaseTurnAnalyzer`) for semantic + acoustic end-of-turn detection.
"""

from __future__ import annotations

import array
import math
import os
import re
from typing import Any, Optional, Tuple

from pipecat.audio.turn.base_turn_analyzer import (
    BaseTurnAnalyzer,
    BaseTurnParams,
    EndOfTurnState,
)
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.metrics.metrics import MetricsData

from app.core.logging import log

INCOMPLETE_UTTERANCE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(um+|uh+|er+|hmm+)\s*\.?$", re.IGNORECASE),
    re.compile(
        r"\b(so|and|but|or|because|like|well|actually|basically)\s*\.?$",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(hold on|let me|wait a|give me a|one second|just a|hang on|let's see|my number is|starts with|it is)\s*\.?$",
        re.IGNORECASE,
    ),
    re.compile(r"[,:\-\u2026]\s*$", re.IGNORECASE),
)


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(value)))


def responsiveness_to_stop_secs(responsiveness: float) -> float:
    """Linearly interpolate responsiveness [0.0, 1.0] -> stop_secs [1.2, 0.2]."""
    r = _clamp(responsiveness, 0.0, 1.0)
    return round(1.2 - 1.0 * r, 3)


def interruption_sensitivity_to_vad(sensitivity: float) -> tuple[float, float]:
    """Map interruption_sensitivity [0.0, 1.0] -> (confidence [0.85, 0.45], min_volume [0.80, 0.30])."""
    s = _clamp(sensitivity, 0.0, 1.0)
    confidence = round(0.85 - 0.40 * s, 3)
    min_volume = round(0.80 - 0.50 * s, 3)
    return confidence, min_volume


class SmartTurnParams(BaseTurnParams):
    """Parameters for semantic + acoustic turn analysis."""

    stop_secs: float = 0.5
    pre_speech_ms: float = 200.0
    max_duration_secs: float = 8.0


def is_utterance_text_incomplete(text: str | None) -> bool:
    """Return True when the partial transcript ends in a hesitation or unfinished clause."""
    if not text or not text.strip():
        return False
    cleaned = text.strip()
    for pattern in INCOMPLETE_UTTERANCE_PATTERNS:
        if pattern.search(cleaned):
            return True
    return False


class SmartTurnAnalyzer(BaseTurnAnalyzer):
    """Pipecat `BaseTurnAnalyzer` combining acoustic trailing contour and semantic cues.

    If `SMART_TURN_ONNX_PATH` is configured and `LocalSmartTurnAnalyzerV3` is available,
    delegates to Pipecat's ONNX SmartTurn v3 model; otherwise runs deterministic
    acoustic + transcript end-of-turn classification suitable for real-time CPU execution.
    """

    def __init__(
        self,
        *,
        sample_rate: Optional[int] = 16000,
        params: Optional[SmartTurnParams] = None,
        onnx_model_path: Optional[str] = None,
    ) -> None:
        super().__init__(sample_rate=sample_rate)
        self._params = params or SmartTurnParams()
        self._speech_triggered = False
        self._silence_secs = 0.0
        self._speech_secs = 0.0
        self._audio_chunks: list[bytes] = []
        self._speech_rms_history: list[float] = []
        self._last_transcript: str = ""
        self._delegate: Optional[BaseTurnAnalyzer] = None

        model_path = onnx_model_path or os.environ.get("SMART_TURN_ONNX_PATH")
        if model_path and os.path.exists(model_path):
            try:
                from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import (
                    LocalSmartTurnAnalyzerV3,
                )

                self._delegate = LocalSmartTurnAnalyzerV3(
                    sample_rate=sample_rate,
                    smart_turn_model_path=model_path,
                )
            except Exception as exc:  # pragma: no cover
                log.warning("smart_turn.onnx_fallback", error=str(exc))

    @property
    def speech_triggered(self) -> bool:
        if self._delegate is not None:
            return self._delegate.speech_triggered
        return self._speech_triggered

    @property
    def params(self) -> SmartTurnParams:
        return self._params

    def update_transcript(self, text: str) -> None:
        """Provide the latest interim/partial transcript to aid semantic end-of-turn detection."""
        self._last_transcript = text or ""

    @staticmethod
    def _compute_rms(buffer: bytes) -> float:
        if len(buffer) < 2:
            return 0.0
        usable = buffer[: len(buffer) - (len(buffer) % 2)]
        samples = array.array("h")
        samples.frombytes(usable)
        if not samples:
            return 0.0
        mean_sq = sum(float(s) * float(s) for s in samples) / len(samples)
        return math.sqrt(mean_sq) / 32768.0

    def append_audio(self, buffer: bytes, is_speech: bool) -> EndOfTurnState:
        if self._delegate is not None:
            return self._delegate.append_audio(buffer, is_speech)

        sr = self.sample_rate or 16000
        duration_secs = (len(buffer) / 2.0) / float(sr) if buffer else 0.0
        rms = self._compute_rms(buffer)

        if is_speech:
            self._speech_triggered = True
            self._silence_secs = 0.0
            self._speech_secs += duration_secs
            self._audio_chunks.append(buffer)
            self._speech_rms_history.append(rms)
            return EndOfTurnState.INCOMPLETE

        if not self._speech_triggered:
            return EndOfTurnState.INCOMPLETE

        self._silence_secs += duration_secs
        self._audio_chunks.append(buffer)

        required_stop = self._params.stop_secs
        if is_utterance_text_incomplete(self._last_transcript):
            required_stop = max(required_stop * 2.0, required_stop + 0.6)

        if self._silence_secs >= required_stop:
            return EndOfTurnState.COMPLETE
        return EndOfTurnState.INCOMPLETE

    async def analyze_end_of_turn(self) -> Tuple[EndOfTurnState, Optional[MetricsData]]:
        if self._delegate is not None:
            return await self._delegate.analyze_end_of_turn()

        if not self._speech_triggered:
            return EndOfTurnState.INCOMPLETE, None

        # Semantic check on partial transcript if available
        if is_utterance_text_incomplete(self._last_transcript):
            if self._silence_secs < max(self._params.stop_secs * 2.0, 1.2):
                return EndOfTurnState.INCOMPLETE, None

        # Acoustic check: if speech ends abruptly with sustained/rising energy (typical of
        # "um..." or mid-clause hesitation) and silence is still brief, classify INCOMPLETE.
        if self._speech_rms_history:
            tail = self._speech_rms_history[-3:]
            tail_avg = sum(tail) / len(tail)
            overall_avg = sum(self._speech_rms_history) / len(self._speech_rms_history)
            # Sustained plateau at the end of speech indicates a held vowel / filler ("ummm...")
            if (
                len(self._speech_rms_history) >= 3
                and overall_avg > 0.01
                and tail_avg >= overall_avg * 0.92
                and self._silence_secs < max(self._params.stop_secs * 1.8, 0.9)
            ):
                return EndOfTurnState.INCOMPLETE, None

        self.clear()
        return EndOfTurnState.COMPLETE, None

    def clear(self) -> None:
        if self._delegate is not None:
            self._delegate.clear()
        self._speech_triggered = False
        self._silence_secs = 0.0
        self._speech_secs = 0.0
        self._audio_chunks.clear()
        self._speech_rms_history.clear()


class TurnConfig:
    """Turn-taking result supporting both `(vad_params, turn_analyzer)` 2-tuple unpacking and `.vad_analyzer` / `.allow_interruptions` attribute access."""

    def __init__(
        self,
        vad_params: VADParams,
        turn_analyzer: Optional[BaseTurnAnalyzer],
        *,
        allow_interruptions: bool = True,
        end_call_after_silence_ms: int = 30000,
        max_call_duration_ms: int = 1800000,
    ) -> None:
        from pipecat.audio.vad.silero import SileroVADAnalyzer

        self.vad_params = vad_params
        self.turn_analyzer = turn_analyzer
        self.vad_analyzer = SileroVADAnalyzer(params=vad_params)
        self.allow_interruptions = allow_interruptions
        self.end_call_after_silence_ms = end_call_after_silence_ms
        self.max_call_duration_ms = max_call_duration_ms

    def __iter__(self):
        yield self.vad_params
        yield self.turn_analyzer

    def __len__(self) -> int:
        return 2

    def __getitem__(self, index: int):
        return (self.vad_params, self.turn_analyzer)[index]


def build_turn_config(
    cfg: Any,
) -> TurnConfig:
    """Build Pipecat `TurnConfig` (unpackable as `(VADParams, turn_analyzer)`) from a `RuntimeConfig` or agent config object."""
    raw_resp = getattr(cfg, "responsiveness", None)
    responsiveness = float(0.7 if raw_resp is None else raw_resp)
    sensitivity = float(getattr(cfg, "interruption_sensitivity", 0.7))
    enable_smart_turn = bool(getattr(cfg, "enable_smart_turn", True))

    if raw_resp is None and getattr(cfg, "vad_stop_secs", None) is not None:
        stop_secs = float(getattr(cfg, "vad_stop_secs"))
    else:
        stop_secs = responsiveness_to_stop_secs(responsiveness)
    confidence, min_volume = interruption_sensitivity_to_vad(sensitivity)

    vad_params = VADParams(
        confidence=confidence,
        start_secs=0.2,
        stop_secs=stop_secs,
        min_volume=min_volume,
    )

    turn_analyzer: Optional[BaseTurnAnalyzer] = None
    if enable_smart_turn:
        turn_analyzer = SmartTurnAnalyzer(
            sample_rate=16000,
            params=SmartTurnParams(stop_secs=stop_secs),
        )

    return TurnConfig(
        vad_params=vad_params,
        turn_analyzer=turn_analyzer,
        allow_interruptions=bool(getattr(cfg, "barge_in_enabled", True)) and sensitivity > 0.0,
        end_call_after_silence_ms=int(getattr(cfg, "end_call_after_silence_ms", 30000) or 30000),
        max_call_duration_ms=int(getattr(cfg, "max_call_duration_ms", 1800000) or 1800000),
    )
