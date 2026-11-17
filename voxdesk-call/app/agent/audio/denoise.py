"""Inbound noise reduction: modes, a background-voice gate, and a CPU benchmark hook.

Two different questions live here and are deliberately kept apart:

* **Which backend runs** — ``off`` / ``noisereduce`` / a licensed vendor filter. Only what is
  genuinely licensed in this process is advertised.
* **What it is asked to remove (P12-05)** — :class:`DenoiseMode` ``off`` / ``noise`` /
  ``noise_and_speech``. ``noise`` is spectral gating of steady noise. ``noise_and_speech`` also
  tries to suppress *other people talking* in the background, which is a different problem: a
  denoiser cannot tell a colleague from the caller, so it needs :class:`BackgroundVoiceGate` and
  a speaker adapter. Without diarization the gate degrades to "keep everything and say so",
  which is the only honest behaviour when the alternative is cutting the caller off mid-word.
"""

from __future__ import annotations

import array
import asyncio
import math
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterable, Protocol

from pipecat.audio.filters.noisereduce_filter import NoisereduceFilter
from pipecat.frames.frames import (
    FilterEnableFrame,
    InputAudioRawFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


def available_denoise_backends() -> dict[str, dict[str, Any]]:
    """Return honest capability matrix of inbound noise-reduction backends.

    Licensed commercial filters (`krisp`, `koala`, `aic`) are never advertised
    unless their license key is present in the runtime environment.
    """
    krisp_licensed = bool(os.getenv("KRISP_MODEL_PATH") and os.getenv("KRISP_LICENSE_KEY"))
    koala_licensed = bool(os.getenv("PICOVOICE_KOALA_ACCESS_KEY"))
    aic_licensed = bool(os.getenv("AICOUSTICS_LICENSE_KEY"))
    return {
        "off": {"available": True, "open_source": True, "licensed": True},
        "noisereduce": {"available": True, "open_source": True, "licensed": True},
        "krisp": {"available": krisp_licensed, "open_source": False, "licensed": krisp_licensed},
        "koala": {"available": koala_licensed, "open_source": False, "licensed": koala_licensed},
        "aic": {"available": aic_licensed, "open_source": False, "licensed": aic_licensed},
    }


def advertised_denoise_modes() -> list[str]:
    """Return only the denoise modes genuinely available in this runtime."""
    return [mode for mode, info in available_denoise_backends().items() if info["available"]]


def build_denoise_enable_frame(enabled: bool) -> FilterEnableFrame:
    """Build a Pipecat `FilterEnableFrame` to dynamically toggle inbound noise reduction."""
    return FilterEnableFrame(enable=bool(enabled))


def build_denoise_filter(
    denoise_enabled: bool | str = True,
) -> NoisereduceFilter | None:
    """Return a Pipecat `NoisereduceFilter` when `denoise_enabled` is active, or `None` when disabled."""
    if isinstance(denoise_enabled, str):
        mode = denoise_enabled.strip().lower()
        if mode in {"", "off", "none", "disabled", "false", "0"}:
            return None
        backends = available_denoise_backends()
        if mode in {"krisp", "koala", "aic"} and not backends[mode]["available"]:
            raise ValueError(
                f"Denoise backend '{mode}' is not licensed in this environment. "
                f"Advertised modes: {advertised_denoise_modes()}"
            )
        if mode not in {
            "noisereduce", "on", "enabled", "true", "1", "krisp", "koala", "aic",
            # P12-05 modes: the spectral filter runs for both; `noise_and_speech` additionally
            # wants `build_denoise_stack()`, which also returns the background-voice gate.
            "noise", "noise_and_speech", "background_speech", "all",
        }:
            raise ValueError(f"Unsupported denoise_mode '{denoise_enabled}'")
    elif not denoise_enabled:
        return None
    return NoisereduceFilter()


# ============================================================== P12-05: MODES ===============


class DenoiseMode(str, Enum):
    """What the filter is asked to remove."""

    OFF = "off"
    NOISE = "noise"
    NOISE_AND_SPEECH = "noise_and_speech"

    @classmethod
    def parse(cls, value: Any) -> "DenoiseMode":
        """Accept the enum, the string, or a legacy boolean and return a mode."""
        if isinstance(value, cls):
            return value
        if isinstance(value, bool):
            return cls.NOISE if value else cls.OFF
        if value is None:
            return cls.OFF
        raw = str(value).strip().lower()
        aliases = {
            "": cls.OFF,
            "off": cls.OFF,
            "none": cls.OFF,
            "disabled": cls.OFF,
            "false": cls.OFF,
            "0": cls.OFF,
            "noise": cls.NOISE,
            "on": cls.NOISE,
            "enabled": cls.NOISE,
            "true": cls.NOISE,
            "1": cls.NOISE,
            "noisereduce": cls.NOISE,
            "noise_and_speech": cls.NOISE_AND_SPEECH,
            "background_speech": cls.NOISE_AND_SPEECH,
            "all": cls.NOISE_AND_SPEECH,
        }
        if raw not in aliases:
            raise ValueError(
                f"Unsupported denoise_mode {value!r}. Known modes: "
                f"{[mode.value for mode in cls]}"
            )
        return aliases[raw]


@dataclass(frozen=True)
class DenoisePlan:
    """Exactly what to build for a given mode, including what is *not* available."""

    mode: DenoiseMode
    filter_enabled: bool
    gate_enabled: bool
    notes: tuple[str, ...] = ()
    backend: str = "noisereduce"

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "filter_enabled": self.filter_enabled,
            "gate_enabled": self.gate_enabled,
            "backend": self.backend,
            "notes": list(self.notes),
        }


class SpeakerAdapter(Protocol):
    """Diarization/voice-identity hook the gate needs to tell two talkers apart."""

    def identify(self, audio: bytes) -> str | None:  # pragma: no cover - protocol
        ...


class NullSpeakerAdapter:
    """Default adapter: no diarization, therefore no speaker identity, therefore no dropping."""

    def identify(self, audio: bytes) -> str | None:
        return None


@dataclass
class BackgroundVoiceGate:
    """Decide which speech frames belong to the caller (P12-05, ``noise_and_speech``).

    The gate never drops a frame it cannot positively attribute to somebody else. It keeps the
    caller's own voice by three signals, in order:

    1. **Speaker continuity** — if a ``SpeakerAdapter`` identifies the talker and it matches the
       caller's established identity, keep. A different identity plus a clearly quieter level is
       background speech, drop.
    2. **Energy** — a talker more than ``background_margin_db`` below the caller's running median
       is background.
    3. **Fallback** — no diarization: keep, and count it. The counter is the honest signal that
       ``noise_and_speech`` is only half-implemented on this deployment.
    """

    caller_speaker_id: str | None = None
    background_margin_db: float = 10.0
    adapter: SpeakerAdapter = field(default_factory=NullSpeakerAdapter)
    _caller_levels: list[float] = field(default_factory=list, repr=False)
    kept: int = 0
    dropped: int = 0
    kept_without_diarization: int = 0
    suspect_crosstalk: int = 0

    def _caller_median_db(self) -> float | None:
        if not self._caller_levels:
            return None
        ordered = sorted(self._caller_levels)
        return ordered[len(ordered) // 2]

    def observe_caller_level(self, level_db: float) -> None:
        self._caller_levels.append(float(level_db))
        if len(self._caller_levels) > 200:
            del self._caller_levels[:100]

    def evaluate(self, *, audio: bytes, level_db: float, is_speech: bool) -> bool:
        """Return ``True`` to keep the frame, ``False`` to drop it as background speech."""
        if not is_speech:
            self.kept += 1
            return True

        speaker = self.adapter.identify(audio)
        if speaker is not None and self.caller_speaker_id is None:
            # First identified speaker on the call establishes the caller.
            self.caller_speaker_id = speaker
        median = self._caller_median_db()

        if speaker is not None and self.caller_speaker_id is not None and speaker == self.caller_speaker_id:
            self.observe_caller_level(level_db)
            self.kept += 1
            return True

        at_background_level = median is not None and level_db <= median - self.background_margin_db
        if speaker is not None and speaker != self.caller_speaker_id:
            if at_background_level or median is None:
                self.dropped += 1
                return False
            self.suspect_crosstalk += 1
            self.kept += 1
            return True

        # No identity available: never guess.
        if median is None or not at_background_level:
            self.observe_caller_level(level_db)
        self.kept_without_diarization += 1
        self.kept += 1
        return True

    @property
    def summary(self) -> dict[str, Any]:
        return {
            "kept": self.kept,
            "dropped": self.dropped,
            "kept_without_diarization": self.kept_without_diarization,
            "suspect_crosstalk": self.suspect_crosstalk,
            "caller_speaker_id": self.caller_speaker_id,
            "diarization": not isinstance(self.adapter, NullSpeakerAdapter),
        }


class BackgroundVoiceGateProcessor(FrameProcessor):
    """Apply a configured background-speaker gate to inbound PCM frames.

    The transport VAD's user-speaking frames mark speech runs. When no diarizing
    ``SpeakerAdapter`` is configured, the underlying gate intentionally keeps all
    audio and records that limitation; it never guesses from volume alone.
    """

    def __init__(self, gate: BackgroundVoiceGate) -> None:
        super().__init__()
        self.gate = gate
        self._speech_active = False
        self.muted_frames = 0

    @staticmethod
    def _level_db(audio: bytes) -> float:
        usable = audio[: len(audio) - (len(audio) % 2)]
        if not usable:
            return -120.0
        samples = array.array("h")
        samples.frombytes(usable)
        if not samples:
            return -120.0
        rms = math.sqrt(sum(float(value) ** 2 for value in samples) / len(samples)) / 32768.0
        return 20.0 * math.log10(max(rms, 1e-6))

    def filter_audio(self, audio: bytes, *, is_speech: bool, level_db: float | None = None) -> bytes:
        """Return original audio or same-length silence; public for deterministic tests."""
        level = self._level_db(audio) if level_db is None else float(level_db)
        if self.gate.evaluate(audio=audio, level_db=level, is_speech=is_speech):
            return audio
        self.muted_frames += 1
        return bytes(len(audio))

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, UserStartedSpeakingFrame):
            self._speech_active = True
        elif isinstance(frame, UserStoppedSpeakingFrame):
            self._speech_active = False
        elif isinstance(frame, InputAudioRawFrame):
            frame.audio = self.filter_audio(frame.audio, is_speech=self._speech_active)
        await self.push_frame(frame, direction)


def plan_denoise(mode: Any, *, backend: str = "noisereduce") -> DenoisePlan:
    """Resolve a mode into a plan, refusing backends this runtime cannot legally run."""
    parsed = DenoiseMode.parse(mode)
    available = available_denoise_backends()
    if backend not in available:
        raise ValueError(f"Unknown denoise backend {backend!r}. Known: {sorted(available)}")
    if not available[backend]["available"]:
        raise ValueError(
            f"Denoise backend '{backend}' is not licensed in this environment. "
            f"Advertised backends: {[name for name, info in available.items() if info['available']]}"
        )
    if parsed is DenoiseMode.OFF:
        return DenoisePlan(mode=parsed, filter_enabled=False, gate_enabled=False, backend=backend,
                           notes=("no filtering",))
    if parsed is DenoiseMode.NOISE:
        return DenoisePlan(mode=parsed, filter_enabled=True, gate_enabled=False, backend=backend,
                           notes=("spectral gating of steady noise only",))
    return DenoisePlan(
        mode=parsed,
        filter_enabled=True,
        gate_enabled=True,
        backend=backend,
        notes=(
            "spectral gating plus a background-voice gate",
            "background speech can only be dropped when a SpeakerAdapter identifies talkers; "
            "without one the gate keeps every frame and counts it",
        ),
    )


def build_denoise_stack(
    mode: Any,
    *,
    backend: str = "noisereduce",
    speaker_adapter: SpeakerAdapter | None = None,
) -> tuple[NoisereduceFilter | None, BackgroundVoiceGate | None, DenoisePlan]:
    """Return ``(filter, gate, plan)`` for a mode. Gate is ``None`` unless the mode asks for it."""
    plan = plan_denoise(mode, backend=backend)
    denoise_filter = NoisereduceFilter() if plan.filter_enabled else None
    gate = None
    if plan.gate_enabled:
        gate = BackgroundVoiceGate(adapter=speaker_adapter or NullSpeakerAdapter())
    return denoise_filter, gate, plan


async def measure_denoise_cpu_ms_async(
    *,
    frames: Iterable[bytes] | None = None,
    mode: Any = DenoiseMode.NOISE,
    backend: str = "noisereduce",
    sample_rate: int = 16000,
    clock: Callable[[], float] = time.perf_counter,
) -> dict[str, Any]:
    """Async benchmark that awaits the exact filter used by the live audio path."""
    if frames is None:
        silent = array("h", [0] * 160).tobytes()  # 10 ms of 16 kHz silence
        frames = [silent] * 20
    frame_list = list(frames)
    if not frame_list:
        raise ValueError("need at least one frame to measure")

    parsed = DenoiseMode.parse(mode)
    denoise_filter = NoisereduceFilter() if parsed is not DenoiseMode.OFF else None
    if denoise_filter is not None:
        await denoise_filter.start(sample_rate)

    started = clock()
    try:
        for frame in frame_list:
            if denoise_filter is not None:
                await denoise_filter.filter(frame)
    finally:
        if denoise_filter is not None:
            await denoise_filter.stop()
    elapsed_ms = (clock() - started) * 1000.0
    return {
        "mode": parsed.value,
        "backend": backend,
        "frames": len(frame_list),
        "total_ms": round(elapsed_ms, 3),
        "cpu_ms_per_frame": round(elapsed_ms / len(frame_list), 3),
        "filter_declared": denoise_filter is not None,
    }


def measure_denoise_cpu_ms(
    *,
    frames: Iterable[bytes] | None = None,
    mode: Any = DenoiseMode.NOISE,
    backend: str = "noisereduce",
    sample_rate: int = 16000,
    clock: Callable[[], float] = time.perf_counter,
) -> dict[str, Any]:
    """Synchronous wrapper around :func:`measure_denoise_cpu_ms_async`.

    This benchmark is intended for CLI/report tooling. Async callers should await
    ``measure_denoise_cpu_ms_async`` directly rather than nesting event loops.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(
            measure_denoise_cpu_ms_async(
                frames=frames,
                mode=mode,
                backend=backend,
                sample_rate=sample_rate,
                clock=clock,
            )
        )
    raise RuntimeError(
        "measure_denoise_cpu_ms cannot run inside an active event loop; "
        "await measure_denoise_cpu_ms_async instead"
    )
