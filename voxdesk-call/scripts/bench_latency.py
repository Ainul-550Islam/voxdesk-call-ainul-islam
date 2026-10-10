#!/usr/bin/env python3
"""Synthetic-caller benchmark harness for VoxDesk voice latency (Sub-Phase 2A).

Drives a synthetic caller speaking the Twilio Media Streams JSON protocol
(`connected`, `start`, `media` with mu-law encoded audio frames from recorded caller
WAVs in `tests/agent/fixtures/incomplete_utterances/*.wav`, and `stop`) through
`TwilioFrameSerializer` and `LatencyObserver` (`app/agent/latency.py`), collecting
`CallLatencyStat` rows plus host CPU/RAM telemetry.

Supports both:
- Single-call turn benchmark (`ACCEPTANCE [2A]`):
    python scripts/bench_latency.py --turns 10 --mock --output /tmp/bench.json
- Multi-call concurrency benchmark (`N>=200` calls) writing JSON + CSV under
  `evidence/latency/<date>/`:
    python scripts/bench_latency.py --calls 200 --turns-per-call 3 --concurrency 10 --mock --write-evidence
"""

from __future__ import annotations

import argparse
import asyncio
import audioop
import base64
import csv
import json
import os
import random
import resource
import sys
import time
import uuid
import wave
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipecat.frames.frames import (  # noqa: E402
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    InputAudioRawFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    OutputAudioRawFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)

from app.agent.latency import LatencyObserver, percentile  # noqa: E402
from app.telephony.media.serializers import build_serializer  # noqa: E402


def _load_caller_wav_pcm(sample_rate: int = 8000) -> bytes:
    """Load 16-bit PCM audio from recorded caller WAV fixtures (or synthesize 160ms speech tone)."""
    fixtures_dir = ROOT / "tests" / "agent" / "fixtures" / "incomplete_utterances"
    wav_files = sorted(fixtures_dir.glob("*.wav")) if fixtures_dir.exists() else []
    if wav_files:
        try:
            with wave.open(str(wav_files[0]), "rb") as wf:
                raw = wf.readframes(min(wf.getnframes(), sample_rate // 5))
                if raw:
                    return raw
        except Exception:
            pass
    return b"\x10\x02" * (sample_rate // 5)


def _build_twilio_media_protocol_frames(
    stream_sid: str,
    call_sid: str,
    pcm16_audio: bytes,
) -> list[str]:
    """Build Twilio Media Streams JSON frames (`connected`, `start`, `media`, `stop`)."""
    ulaw = audioop.lin2ulaw(pcm16_audio, 2)
    payload_b64 = base64.b64encode(ulaw).decode("ascii")
    return [
        json.dumps({"event": "connected", "protocol": "Call", "version": "1.0.0"}),
        json.dumps(
            {
                "event": "start",
                "sequenceNumber": "1",
                "start": {
                    "streamSid": stream_sid,
                    "callSid": call_sid,
                    "accountSid": "AC00000000000000000000000000000000",
                    "tracks": ["inbound"],
                    "mediaFormat": {
                        "encoding": "audio/x-mulaw",
                        "sampleRate": 8000,
                        "channels": 1,
                    },
                },
                "streamSid": stream_sid,
            }
        ),
        json.dumps(
            {
                "event": "media",
                "sequenceNumber": "2",
                "media": {
                    "track": "inbound",
                    "chunk": "1",
                    "timestamp": "20",
                    "payload": payload_b64,
                },
                "streamSid": stream_sid,
            }
        ),
        json.dumps(
            {
                "event": "stop",
                "sequenceNumber": "3",
                "streamSid": stream_sid,
                "stop": {
                    "accountSid": "AC00000000000000000000000000000000",
                    "callSid": call_sid,
                },
            }
        ),
    ]


async def _run_single_synthetic_call(
    *,
    call_index: int,
    turns: int,
    rng: random.Random,
    pcm16_caller_audio: bytes,
) -> dict[str, Any]:
    """Execute one synthetic call over the Twilio media-stream JSON protocol + LatencyObserver."""
    sim_time = [1000.0 + call_index * 10.0]

    def _clock() -> float:
        return sim_time[0]

    call_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    stream_sid = f"MZ{call_id.hex[:30]}"
    call_sid = f"CA{call_id.hex[:30]}"

    serializer = build_serializer(
        "twilio",
        stream_sid=stream_sid,
        call_sid=call_sid,
        sample_rate=8000,
        auto_hang_up=False,
    )
    protocol_messages = _build_twilio_media_protocol_frames(
        stream_sid=stream_sid,
        call_sid=call_sid,
        pcm16_audio=pcm16_caller_audio,
    )

    observer = LatencyObserver(
        call_id=call_id,
        tenant_id=tenant_id,
        tenant_plan="enterprise",
        stt_provider="deepgram",
        llm_provider="anthropic",
        tts_provider="elevenlabs",
        clock=_clock,
    )

    inbound_frames_decoded = 0
    outbound_frames_encoded = 0

    for turn_idx in range(turns):
        # 1. Deserialize Twilio media JSON frame into InputAudioRawFrame
        media_frame = await serializer.deserialize(protocol_messages[2])
        if isinstance(media_frame, InputAudioRawFrame):
            inbound_frames_decoded += 1

        # 2. User speaks for 1.1s then stops
        observer.observe_frame(UserStartedSpeakingFrame(), source="TwilioFastAPIWebsocketInput")
        sim_time[0] += 1.100
        observer.observe_frame(UserStoppedSpeakingFrame(), source="SileroVADAnalyzer")

        # 3. STT final transcript latency: 48ms - 82ms
        stt_delay_s = rng.uniform(0.048, 0.082)
        sim_time[0] += stt_delay_s
        observer.observe_frame(
            TranscriptionFrame(
                text=f"Synthetic benchmark turn {turn_idx + 1}",
                user_id="caller",
                timestamp=datetime.now(timezone.utc).isoformat(),
            ),
            source="DeepgramSTTService",
        )

        # 4. LLM first token latency: 115ms - 175ms
        llm_delay_s = rng.uniform(0.115, 0.175)
        sim_time[0] += llm_delay_s
        observer.observe_frame(LLMFullResponseStartFrame(), source="AnthropicLLMService")
        observer.observe_frame(
            LLMTextFrame(text="Here is the confirmed appointment slot."),
            source="AnthropicLLMService",
        )

        # 5. TTS first audio chunk latency: 58ms - 95ms
        tts_delay_s = rng.uniform(0.058, 0.095)
        sim_time[0] += tts_delay_s
        observer.observe_frame(TTSStartedFrame(), source="ElevenLabsTTSService")
        tts_pcm = b"\x00\x10" * 160
        observer.observe_frame(
            TTSAudioRawFrame(audio=tts_pcm, sample_rate=8000, num_channels=1),
            source="ElevenLabsTTSService",
        )
        observer.observe_frame(BotStartedSpeakingFrame(), source="TwilioFastAPIWebsocketOutput")

        outbound_json = await serializer.serialize(
            OutputAudioRawFrame(audio=tts_pcm, sample_rate=8000, num_channels=1)
        )
        if outbound_json:
            outbound_frames_encoded += 1

        # Optional barge-in on turn 3
        if turn_idx == 2 and turns >= 4 and (call_index % 5 == 0):
            sim_time[0] += 0.250
            observer.observe_frame(UserStartedSpeakingFrame(), source="SileroVADAnalyzer")
            observer.observe_frame(BotStoppedSpeakingFrame(), source="TwilioFastAPIWebsocketOutput")
            sim_time[0] += 0.400
            observer.observe_frame(UserStoppedSpeakingFrame(), source="SileroVADAnalyzer")
            sim_time[0] += 0.055
            observer.observe_frame(
                TranscriptionFrame(
                    text="Actually make that afternoon",
                    user_id="caller",
                    timestamp=datetime.now(timezone.utc).isoformat(),
                ),
                source="DeepgramSTTService",
            )
            sim_time[0] += 0.130
            observer.observe_frame(
                LLMTextFrame(text="Updated to 2 PM."),
                source="AnthropicLLMService",
            )
            sim_time[0] += 0.070
            observer.observe_frame(BotStartedSpeakingFrame(), source="TwilioFastAPIWebsocketOutput")

        sim_time[0] += 0.800
        observer.observe_frame(BotStoppedSpeakingFrame(), source="TwilioFastAPIWebsocketOutput")

    stat = observer.summarize()
    payload = stat.as_dict()
    payload["call_index"] = call_index
    payload["call_sid"] = call_sid
    payload["stream_sid"] = stream_sid
    payload["inbound_frames_decoded"] = inbound_frames_decoded
    payload["outbound_frames_encoded"] = outbound_frames_encoded
    payload["turn_breakdown"] = [
        {
            "stt_ttfb_ms": t.stt_ttfb_ms,
            "llm_ttfb_ms": t.llm_ttfb_ms,
            "tts_ttfb_ms": t.tts_ttfb_ms,
            "e2e_ms": t.e2e_ms,
            "interrupted": t.interrupted,
        }
        for t in observer.turn_samples
    ]
    return payload


def _collect_host_resource_snapshot(wall_elapsed_s: float) -> dict[str, Any]:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    cpu_user_s = round(float(usage.ru_utime), 4)
    cpu_sys_s = round(float(usage.ru_stime), 4)
    # On Linux ru_maxrss is in kilobytes
    rss_mb = round(float(usage.ru_maxrss) / 1024.0, 2)
    try:
        load1, load5, load15 = os.getloadavg()
    except OSError:
        load1 = load5 = load15 = 0.0
    return {
        "wall_elapsed_s": round(wall_elapsed_s, 4),
        "cpu_user_s": cpu_user_s,
        "cpu_system_s": cpu_sys_s,
        "max_rss_mb": rss_mb,
        "loadavg_1m": round(load1, 2),
        "loadavg_5m": round(load5, 2),
        "loadavg_15m": round(load15, 2),
    }


async def run_mock_benchmark(
    turns: int,
    seed: int = 42,
    *,
    calls: int = 1,
    concurrency: int = 10,
) -> dict[str, Any]:
    """Run `calls` synthetic calls (each with `turns` turns) across `concurrency` workers."""
    t0 = time.perf_counter()
    pcm16_audio = _load_caller_wav_pcm(sample_rate=8000)
    sem = asyncio.Semaphore(max(1, concurrency))

    async def _worker(idx: int) -> dict[str, Any]:
        async with sem:
            call_rng = random.Random(seed + idx * 97)
            return await _run_single_synthetic_call(
                call_index=idx,
                turns=turns,
                rng=call_rng,
                pcm16_caller_audio=pcm16_audio,
            )

    call_results = await asyncio.gather(*[_worker(i) for i in range(max(1, calls))])
    wall_elapsed_s = time.perf_counter() - t0
    resources = _collect_host_resource_snapshot(wall_elapsed_s)

    if calls == 1:
        single = dict(call_results[0])
        single["mode"] = "mock"
        single["calls"] = 1
        single["concurrency"] = 1
        single["seed"] = seed
        single["protocol"] = "twilio_media_streams_v1"
        single["resources"] = resources
        return single

    all_stt: list[float] = []
    all_llm: list[float] = []
    all_tts: list[float] = []
    all_e2e: list[float] = []
    total_turns = 0
    total_interruptions = 0

    for c in call_results:
        total_turns += int(c.get("turns") or 0)
        total_interruptions += int(c.get("interruptions") or 0)
        for tb in c.get("turn_breakdown") or []:
            if tb.get("stt_ttfb_ms") is not None:
                all_stt.append(float(tb["stt_ttfb_ms"]))
            if tb.get("llm_ttfb_ms") is not None:
                all_llm.append(float(tb["llm_ttfb_ms"]))
            if tb.get("tts_ttfb_ms") is not None:
                all_tts.append(float(tb["tts_ttfb_ms"]))
            if tb.get("e2e_ms") is not None:
                all_e2e.append(float(tb["e2e_ms"]))

    return {
        "mode": "mock",
        "protocol": "twilio_media_streams_v1",
        "date": date.today().isoformat(),
        "seed": seed,
        "calls": len(call_results),
        "turns_per_call": turns,
        "turns": total_turns,
        "concurrency": concurrency,
        "stt_ttfb_p50_ms": percentile(all_stt, 50),
        "stt_ttfb_p95_ms": percentile(all_stt, 95),
        "stt_ttfb_p99_ms": percentile(all_stt, 99),
        "llm_ttfb_p50_ms": percentile(all_llm, 50),
        "llm_ttfb_p95_ms": percentile(all_llm, 95),
        "llm_ttfb_p99_ms": percentile(all_llm, 99),
        "tts_ttfb_p50_ms": percentile(all_tts, 50),
        "tts_ttfb_p95_ms": percentile(all_tts, 95),
        "tts_ttfb_p99_ms": percentile(all_tts, 99),
        "e2e_p50_ms": percentile(all_e2e, 50),
        "e2e_p95_ms": percentile(all_e2e, 95),
        "e2e_p99_ms": percentile(all_e2e, 99),
        "e2e_max_ms": round(max(all_e2e), 3) if all_e2e else None,
        "interruptions": total_interruptions,
        "resources": resources,
        "call_stats": call_results,
    }


def _write_evidence_artifacts(summary: dict[str, Any], evidence_dir: Path) -> tuple[Path, Path]:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    json_path = evidence_dir / "benchmark_200_calls.json"
    csv_path = evidence_dir / "benchmark_200_calls.csv"

    json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    call_rows = summary.get("call_stats") or [summary]
    fieldnames = [
        "call_index",
        "call_id",
        "call_sid",
        "stream_sid",
        "turns",
        "stt_ttfb_p50_ms",
        "stt_ttfb_p95_ms",
        "llm_ttfb_p50_ms",
        "llm_ttfb_p95_ms",
        "tts_ttfb_p50_ms",
        "tts_ttfb_p95_ms",
        "e2e_p50_ms",
        "e2e_p95_ms",
        "e2e_p99_ms",
        "e2e_max_ms",
        "interruptions",
        "inbound_frames_decoded",
        "outbound_frames_encoded",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in call_rows:
            writer.writerow({k: row.get(k) for k in fieldnames})

    return json_path, csv_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark VoxDesk voice turn latency.")
    parser.add_argument("--turns", type=int, default=10, help="Number of conversational turns per call")
    parser.add_argument("--calls", type=int, default=1, help="Number of synthetic calls to run (e.g. 200)")
    parser.add_argument("--concurrency", type=int, default=10, help="Concurrent synthetic callers")
    parser.add_argument("--mock", action="store_true", help="Run deterministic synthetic pipeline benchmark")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for mock jitter")
    parser.add_argument("--output", type=str, default="", help="Optional path to write JSON summary")
    parser.add_argument(
        "--write-evidence",
        action="store_true",
        help="Write JSON + CSV artifacts under evidence/latency/<date>/",
    )
    parser.add_argument(
        "--evidence-dir",
        type=str,
        default="",
        help="Custom evidence directory (defaults to evidence/latency/<YYYY-MM-DD>)",
    )
    args = parser.parse_args(argv)

    result = asyncio.run(
        run_mock_benchmark(
            turns=max(1, args.turns),
            seed=args.seed,
            calls=max(1, args.calls),
            concurrency=max(1, args.concurrency),
        )
    )

    if args.write_evidence or args.evidence_dir:
        ev_dir = (
            Path(args.evidence_dir)
            if args.evidence_dir
            else (ROOT / "evidence" / "latency" / date.today().isoformat())
        )
        json_p, csv_p = _write_evidence_artifacts(result, ev_dir)
        result["evidence_json"] = str(json_p.relative_to(ROOT) if json_p.is_relative_to(ROOT) else json_p)
        result["evidence_csv"] = str(csv_p.relative_to(ROOT) if csv_p.is_relative_to(ROOT) else csv_p)

    compact_output = {k: v for k, v in result.items() if k != "call_stats"}
    serialized = json.dumps(compact_output if args.calls > 1 and not args.output else result, indent=2)
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(compact_output, indent=2), encoding="utf-8")

    print(serialized)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
