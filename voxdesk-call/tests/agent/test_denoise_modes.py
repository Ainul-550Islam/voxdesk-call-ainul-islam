"""P12-05 — denoise modes, the background-voice gate, and the CPU benchmark hook."""

from __future__ import annotations

import pytest

from app.agent.audio.denoise import (
    BackgroundVoiceGate,
    DenoiseMode,
    NullSpeakerAdapter,
    build_denoise_filter,
    build_denoise_stack,
    measure_denoise_cpu_ms,
    measure_denoise_cpu_ms_async,
    plan_denoise,
)


class _FixedSpeaker:
    """Tiny speaker adapter: the "audio" bytes are the speaker label."""

    def identify(self, audio: bytes) -> str | None:
        label = audio.decode("utf-8", "ignore").strip()
        return label or None


def test_modes_parse_from_strings_booleans_and_none() -> None:
    assert DenoiseMode.parse("off") is DenoiseMode.OFF
    assert DenoiseMode.parse("noise") is DenoiseMode.NOISE
    assert DenoiseMode.parse("noise_and_speech") is DenoiseMode.NOISE_AND_SPEECH
    assert DenoiseMode.parse("background_speech") is DenoiseMode.NOISE_AND_SPEECH
    assert DenoiseMode.parse(True) is DenoiseMode.NOISE
    assert DenoiseMode.parse(False) is DenoiseMode.OFF
    assert DenoiseMode.parse(None) is DenoiseMode.OFF
    with pytest.raises(ValueError):
        DenoiseMode.parse("magic")


def test_plan_says_exactly_what_will_run() -> None:
    off = plan_denoise("off")
    assert (off.filter_enabled, off.gate_enabled) == (False, False)

    noise = plan_denoise("noise")
    assert (noise.filter_enabled, noise.gate_enabled) == (True, False)

    both = plan_denoise("noise_and_speech")
    assert (both.filter_enabled, both.gate_enabled) == (True, True)
    assert any("without one the gate keeps every frame" in note for note in both.notes)


def test_stack_builds_a_gate_only_when_the_mode_needs_it() -> None:
    denoise_filter, gate, _ = build_denoise_stack("noise")
    assert denoise_filter is not None and gate is None

    denoise_filter, gate, plan = build_denoise_stack("noise_and_speech")
    assert denoise_filter is not None and gate is not None
    assert plan.mode is DenoiseMode.NOISE_AND_SPEECH

    denoise_filter, gate, _ = build_denoise_stack("off")
    assert denoise_filter is None and gate is None


def test_unlicensed_backend_is_refused_rather_than_silently_skipped() -> None:
    from app.agent.audio.denoise import available_denoise_backends

    if not available_denoise_backends()["krisp"]["available"]:
        with pytest.raises(ValueError, match="not licensed"):
            plan_denoise("noise", backend="krisp")


def test_legacy_helper_still_accepts_the_new_modes() -> None:
    assert build_denoise_filter("off") is None
    assert build_denoise_filter("noise") is not None
    assert build_denoise_filter("noise_and_speech") is not None
    assert build_denoise_filter(False) is None


def test_gate_without_diarization_keeps_everything_and_says_so() -> None:
    gate = BackgroundVoiceGate()
    for level in (40.0, 39.0, 41.0, 12.0):
        assert gate.evaluate(audio=b"", level_db=level, is_speech=True) is True
    summary = gate.summary
    assert summary["diarization"] is False
    assert summary["dropped"] == 0
    assert summary["kept_without_diarization"] == 4


def test_gate_drops_a_different_speaker_at_background_level() -> None:
    gate = BackgroundVoiceGate(adapter=_FixedSpeaker(), background_margin_db=10.0)
    # Caller establishes identity and level.
    assert gate.evaluate(audio=b"caller", level_db=42.0, is_speech=True) is True
    assert gate.evaluate(audio=b"caller", level_db=44.0, is_speech=True) is True
    # A colleague 20 dB down is background speech.
    assert gate.evaluate(audio=b"colleague", level_db=20.0, is_speech=True) is False
    assert gate.summary["dropped"] == 1
    assert gate.summary["diarization"] is True


def test_gate_keeps_a_second_speaker_at_full_level_instead_of_cutting_them_off() -> None:
    gate = BackgroundVoiceGate(adapter=_FixedSpeaker(), background_margin_db=10.0)
    assert gate.evaluate(audio=b"caller", level_db=42.0, is_speech=True) is True
    # Someone else at the caller's own level: keep, but flag it. Dropping here would mute a
    # partner who picked up the second handset.
    assert gate.evaluate(audio=b"partner", level_db=43.0, is_speech=True) is True
    assert gate.summary["suspect_crosstalk"] == 1
    assert gate.summary["dropped"] == 0


def test_silence_always_passes_and_caller_levels_are_tracked() -> None:
    gate = BackgroundVoiceGate(adapter=_FixedSpeaker())
    assert gate.evaluate(audio=b"caller", level_db=40.0, is_speech=False) is True
    assert gate.summary["kept"] == 1
    for level in range(30, 240):
        gate.observe_caller_level(float(level))
    assert len(gate._caller_levels) <= 200


def test_default_adapter_is_the_null_one() -> None:
    assert isinstance(BackgroundVoiceGate().adapter, NullSpeakerAdapter)


def test_cpu_benchmark_measures_the_real_filter_and_the_off_baseline() -> None:
    from array import array

    frames = [array("h", [0] * 160).tobytes() for _ in range(10)]
    on = measure_denoise_cpu_ms(frames=frames, mode="noise")
    off = measure_denoise_cpu_ms(frames=frames, mode="off")

    assert on["frames"] == 10 and on["filter_declared"] is True
    assert off["filter_declared"] is False and off["cpu_ms_per_frame"] == 0.0
    assert on["cpu_ms_per_frame"] > 0.0
    assert on["mode"] == "noise"


def test_cpu_benchmark_uses_the_injected_clock() -> None:
    # The off baseline calls the clock exactly twice: once before the loop, once after.
    ticks = iter([0.0, 0.5, 1.0])

    def clock() -> float:
        return next(ticks)

    result = measure_denoise_cpu_ms(frames=[b"\x00\x00" * 160] * 2, mode="off", clock=clock)
    assert result["total_ms"] == pytest.approx(500.0)
    assert result["cpu_ms_per_frame"] == pytest.approx(250.0)


@pytest.mark.asyncio
async def test_async_benchmark_is_available_inside_an_event_loop() -> None:
    result = await measure_denoise_cpu_ms_async(frames=[b"\x00\x00" * 160], mode="off")
    assert result["frames"] == 1
    assert result["filter_declared"] is False
    with pytest.raises(RuntimeError, match="await measure_denoise_cpu_ms_async"):
        measure_denoise_cpu_ms(frames=[b"\x00\x00" * 160], mode="off")
