"""Unit tests for Smart Turn-Taking, Backchannel, and Idle Reminders (Sub-Phase 2B)."""

from __future__ import annotations

import pathlib
import random
import uuid
import wave

import pytest
from pipecat.audio.turn.base_turn_analyzer import EndOfTurnState
from pipecat.frames.frames import (
    EndFrame,
    TTSSpeakFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from app.agent.humanize import BackchannelProcessor
from app.agent.idle_reminders import build_idle_reminder_processor
from app.agent.turn_taking import SmartTurnAnalyzer, build_turn_config
from app.domain.agent_models import AgentConfig
from app.runtime.agent_config_resolver import RuntimeConfig


def test_responsiveness_and_interruption_sensitivity_mapping():
    cfg_patient = RuntimeConfig(
        tenant_id=uuid.uuid4(),
        responsiveness=0.0,
        interruption_sensitivity=0.0,
        enable_smart_turn=False,
    )
    vad_patient, analyzer_patient = build_turn_config(cfg_patient)
    assert vad_patient.stop_secs == pytest.approx(1.2)
    assert vad_patient.confidence == pytest.approx(0.85)
    assert vad_patient.min_volume == pytest.approx(0.80)
    assert analyzer_patient is None

    cfg_snappy = RuntimeConfig(
        tenant_id=uuid.uuid4(),
        responsiveness=1.0,
        interruption_sensitivity=1.0,
        enable_smart_turn=True,
    )
    vad_snappy, analyzer_snappy = build_turn_config(cfg_snappy)
    assert vad_snappy.stop_secs == pytest.approx(0.2)
    assert vad_snappy.confidence == pytest.approx(0.45)
    assert vad_snappy.min_volume == pytest.approx(0.30)
    assert isinstance(analyzer_snappy, SmartTurnAnalyzer)


@pytest.mark.slow
@pytest.mark.asyncio
async def test_smart_turn_holds_on_incomplete_utterance_fixtures():
    fixtures_dir = (
        pathlib.Path(__file__).resolve().parent / "fixtures" / "incomplete_utterances"
    )
    wav_files = sorted(fixtures_dir.glob("*.wav"))
    assert len(wav_files) == 5

    transcripts = [
        "um so my number is",
        "hold on let me",
        "wait a",
        "let me",
        "the order number starts with",
    ]
    for wav_path, partial_text in zip(wav_files, transcripts):
        analyzer = SmartTurnAnalyzer(sample_rate=16000)
        analyzer.update_transcript(partial_text)
        with wave.open(str(wav_path), "rb") as wf:
            sr = wf.getframerate()
            chunk_samples = int(sr * 0.1)  # 100ms chunks
            raw = wf.readframes(wf.getnframes())

        bytes_per_chunk = chunk_samples * 2
        for offset in range(0, len(raw), bytes_per_chunk):
            chunk = raw[offset : offset + bytes_per_chunk]
            rms = analyzer._compute_rms(chunk)
            state = analyzer.append_audio(chunk, is_speech=(rms > 0.01))
            assert state == EndOfTurnState.INCOMPLETE

        eot_state, _ = await analyzer.analyze_end_of_turn()
        assert eot_state == EndOfTurnState.INCOMPLETE


@pytest.mark.asyncio
async def test_idle_reminder_fires_max_count_then_ends_call():
    cfg = RuntimeConfig(
        tenant_id=uuid.uuid4(),
        reminder_trigger_ms=5000,
        reminder_max_count=2,
    )
    processor, controller = build_idle_reminder_processor(cfg)
    assert controller.timeout_seconds == pytest.approx(5.0)

    pushed = []

    async def _capture_push(frame, direction=FrameDirection.DOWNSTREAM):
        pushed.append((frame, direction))

    processor.push_frame = _capture_push  # type: ignore[method-assign]

    # Retry 1 -> check-in prompt, returns True
    cont_1 = await controller.on_idle(processor, 1)
    assert cont_1 is True
    assert controller.fired_count == 1
    assert any(isinstance(f, TTSSpeakFrame) for f, _ in pushed)
    assert not any(isinstance(f, EndFrame) for f, _ in pushed)

    # Retry 2 -> second check-in prompt, returns True
    pushed.clear()
    cont_2 = await controller.on_idle(processor, 2)
    assert cont_2 is True
    assert controller.fired_count == 2
    assert any(isinstance(f, TTSSpeakFrame) for f, _ in pushed)
    assert not any(isinstance(f, EndFrame) for f, _ in pushed)

    # Retry 3 (> reminder_max_count=2) -> goodbye + EndFrame, returns False
    pushed.clear()
    cont_3 = await controller.on_idle(processor, 3)
    assert cont_3 is False
    assert controller.ended_call is True
    assert any(isinstance(f, TTSSpeakFrame) for f, _ in pushed)
    assert any(isinstance(f, EndFrame) for f, _ in pushed)


@pytest.mark.asyncio
async def test_backchannel_emits_cue_on_long_pause_when_enabled():
    bc = BackchannelProcessor(
        after_seconds=0.5,
        cooldown=1.0,
        enabled=True,
        frequency=1.0,
        words=["uh-huh", "got it"],
        rng=random.Random(42),
    )
    pushed = []

    async def _capture_push(frame, direction=FrameDirection.DOWNSTREAM):
        pushed.append(frame)

    bc.push_frame = _capture_push  # type: ignore[method-assign]

    await bc.process_frame(UserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    # Simulate 1.8s of speech before pause
    bc._speech_started_at = bc._speech_started_at - 1.8  # type: ignore[operator]
    await bc.process_frame(UserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)

    tts_frames = [f for f in pushed if isinstance(f, TTSSpeakFrame)]
    assert len(tts_frames) == 1
    assert tts_frames[0].text in {"uh-huh", "got it"}


def test_agent_config_roundtrips_turn_taking_settings():
    snap = {
        "name": "Turn Taking Agent",
        "turn_taking": {
            "responsiveness": 0.85,
            "interruption_sensitivity": 0.65,
            "enable_smart_turn": True,
            "enable_backchannel": True,
            "backchannel_frequency": 0.4,
            "backchannel_words": ["yeah", "mm-hmm"],
            "reminder_trigger_ms": 8000,
            "reminder_max_count": 3,
            "boosted_keywords": [["Invisalign", 3.5], [" emergency ", 2.0]],
        },
    }
    cfg = AgentConfig.from_snapshot_dict(snap, tenant_id=str(uuid.uuid4()))
    assert cfg.validate() == []
    assert cfg.turn_taking.responsiveness == pytest.approx(0.85)
    assert cfg.turn_taking.enable_backchannel is True
    assert cfg.turn_taking.boosted_keywords == (("Invisalign", 3.5), (" emergency ", 2.0))
    dumped = cfg.to_snapshot_dict()
    assert dumped["turn_taking"]["reminder_trigger_ms"] == 8000
    assert dumped["turn_taking"]["reminder_max_count"] == 3
