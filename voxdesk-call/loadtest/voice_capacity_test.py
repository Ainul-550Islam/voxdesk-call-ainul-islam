#!/usr/bin/env python3
"""Single-worker voice capacity ramp test for VoxDesk (Part 8 / Gate G9 & Part 11).

Ramps concurrent calls ``10 -> N`` (default ``10, 20, 30, 40, 50, 60``) on a
single worker process against the REAL ``/telephony/ws`` path using recorded
caller audio (``loadtest/voice_ws_user.py``), recording per-step CPU time,
resident memory (RSS), per-call wall time, and ``LatencyObserver`` E2E latency
percentiles (p50, p95, p99), and determines the single-worker capacity knee
point.

Modes:
- Default CI mode (``provider_fakes: true``): deterministic local STT/LLM/TTS
  fakes to prevent live API spend in CI while exercising real ``/telephony/ws``,
  HMAC stream tokens, ``TwilioFrameSerializer`` mu-law decode/encode,
  ``NoisereduceFilter`` spectral denoise, ``LatencyObserver``, and DB persistence.
- Opt-in live mode (``--real-providers``, ``provider_fakes: false``): executes
  real concurrent Deepgram STT + OpenAI streaming LLM + ElevenLabs streaming TTS
  network I/O per call turn alongside local G.711 / denoise DSP. Refuses to run
  without ``DEEPGRAM_API_KEY``, ``OPENAI_API_KEY``, and ``ELEVENLABS_API_KEY``.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import platform
import resource
import sys
import time
from typing import Any
import uuid
import wave

import httpx

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from .safety import validate_target
    from .voice_ws_user import load_recorded_caller_pcm16, run_synthetic_ws_call
except ImportError:  # pragma: no cover - script execution path
    from safety import validate_target
    from voice_ws_user import load_recorded_caller_pcm16, run_synthetic_ws_call

REAL_PROVIDER_ENV = ("DEEPGRAM_API_KEY", "OPENAI_API_KEY", "ELEVENLABS_API_KEY")


def _hardware_profile() -> dict[str, Any]:
    cpu_model = platform.processor() or "unknown"
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if line.lower().startswith("model name"):
                cpu_model = line.split(":", 1)[1].strip()
                break
    except Exception:
        pass

    mem_total_mb = 0.0
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                kb = int(line.split()[1])
                mem_total_mb = round(kb / 1024.0, 1)
                break
    except Exception:
        pass

    return {
        "cpu_model": cpu_model,
        "logical_vcpus": os.cpu_count() or 1,
        "mem_total_mb": mem_total_mb,
        "kernel": platform.release(),
        "python_version": platform.python_version(),
        "arch": platform.machine(),
    }


def _current_rss_mb() -> float:
    try:
        for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
            if line.startswith("VmRSS:"):
                kb = int(line.split()[1])
                return round(kb / 1024.0, 2)
    except Exception:
        pass
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return round(float(usage.ru_maxrss) / 1024.0, 2)


def _cpu_times() -> tuple[float, float]:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return float(usage.ru_utime), float(usage.ru_stime)


def estimate_cost_per_concurrent_call(*, denoise_enabled: bool) -> dict[str, Any]:
    """Compute wholesale provider + compute cost per concurrent active call."""
    # Wholesale list rates (USD per active call minute, assuming 50% caller / 50% agent duty cycle)
    twilio_pstn_media_per_min = 0.0180  # $0.014/min PSTN + $0.004/min Media Streams
    deepgram_nova2_per_min = 0.0043
    openai_gpt4o_mini_per_min = 0.0030  # ~600 input + ~180 output tokens/min
    elevenlabs_turbo_per_min = 0.0240   # ~400 chars/min on Scale/Business tier
    cartesia_sonic_per_min = 0.0110

    worker_hourly_usd = 0.084  # c6i.large (2 vCPU, 4 GB RAM) on-demand ~$0.085/hr
    safe_calls_per_worker = 20 if denoise_enabled else 30
    compute_per_call_hour = round(worker_hourly_usd / safe_calls_per_worker, 5)
    compute_per_call_min = round(compute_per_call_hour / 60.0, 6)

    elevenlabs_stack_min = round(
        twilio_pstn_media_per_min
        + deepgram_nova2_per_min
        + openai_gpt4o_mini_per_min
        + elevenlabs_turbo_per_min
        + compute_per_call_min,
        5,
    )
    cartesia_stack_min = round(
        twilio_pstn_media_per_min
        + deepgram_nova2_per_min
        + openai_gpt4o_mini_per_min
        + cartesia_sonic_per_min
        + compute_per_call_min,
        5,
    )
    return {
        "denoise_enabled": denoise_enabled,
        "safe_concurrent_calls_per_2vcpu_worker": safe_calls_per_worker,
        "compute_worker_hourly_usd": worker_hourly_usd,
        "compute_cost_per_concurrent_call_hour_usd": compute_per_call_hour,
        "wholesale_stacks": {
            "twilio_deepgram_gpt4omini_elevenlabs": {
                "cost_per_call_minute_usd": elevenlabs_stack_min,
                "cost_per_concurrent_call_hour_usd": round(elevenlabs_stack_min * 60.0, 3),
            },
            "twilio_deepgram_gpt4omini_cartesia": {
                "cost_per_call_minute_usd": cartesia_stack_min,
                "cost_per_concurrent_call_hour_usd": round(cartesia_stack_min * 60.0, 3),
            },
        },
    }


def _pcm16_to_wav_bytes(pcm16: bytes, sample_rate: int = 8000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm16)
    return buf.getvalue()


async def _run_real_provider_call(
    *,
    client: httpx.AsyncClient,
    host: str,
    turns: int,
    chunks_per_turn: int,
    seed: int,
    denoise_enabled: bool,
    session_factory: Any,
    tenant_id: uuid.UUID,
    pcm16_audio: bytes,
) -> dict[str, Any]:
    """Execute local `/telephony/ws` framing + denoise DSP AND real STT -> LLM -> TTS HTTP/SSE streams."""
    wav_bytes = _pcm16_to_wav_bytes(pcm16_audio, sample_rate=8000)
    dg_key = os.environ["DEEPGRAM_API_KEY"].strip()
    oa_key = os.environ["OPENAI_API_KEY"].strip()
    el_key = os.environ["ELEVENLABS_API_KEY"].strip()
    voice_id = os.environ.get("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM").strip()

    # Exercise real /telephony/ws serializer + NoisereduceFilter DSP locally first
    local_res = await run_synthetic_ws_call(
        host=host,
        turns=turns,
        chunks_per_turn=chunks_per_turn,
        seed=seed,
        denoise_enabled=denoise_enabled,
        session_factory=session_factory,
        tenant_id=tenant_id,
        pcm16_audio=pcm16_audio,
    )

    from app.agent.latency import percentile

    turn_e2e_ms: list[float] = []
    wall_start = time.perf_counter()
    for _ in range(turns):
        t0 = time.perf_counter()
        stt_resp = await client.post(
            "https://api.deepgram.com/v1/listen?model=nova-2",
            headers={"Authorization": f"Token {dg_key}", "Content-Type": "audio/wav"},
            content=wav_bytes,
        )
        stt_resp.raise_for_status()
        transcript = (
            (((stt_resp.json().get("results") or {}).get("channels") or [{}])[0].get("alternatives") or [{}])[0].get(
                "transcript"
            )
            or "Confirm appointment slot."
        )

        reply_parts: list[str] = []
        async with client.stream(
            "POST",
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {oa_key}", "Content-Type": "application/json"},
            json={
                "model": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
                "stream": True,
                "max_tokens": 24,
                "messages": [{"role": "user", "content": transcript}],
            },
        ) as llm_resp:
            llm_resp.raise_for_status()
            async for line in llm_resp.aiter_lines():
                if line.startswith("data: ") and line.strip() != "data: [DONE]":
                    choices = json.loads(line[6:]).get("choices") or []
                    if choices:
                        reply_parts.append((choices[0].get("delta") or {}).get("content") or "")

        reply_text = "".join(reply_parts).strip() or "Confirmed for Tuesday."
        first_tts_ms: float | None = None
        async with client.stream(
            "POST",
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream?output_format=pcm_16000",
            headers={"xi-api-key": el_key, "Content-Type": "application/json"},
            json={"text": reply_text, "model_id": "eleven_turbo_v2_5"},
        ) as tts_resp:
            tts_resp.raise_for_status()
            async for chunk in tts_resp.aiter_bytes():
                if chunk and first_tts_ms is None:
                    first_tts_ms = (time.perf_counter() - t0) * 1000.0

        turn_e2e_ms.append(first_tts_ms if first_tts_ms is not None else (time.perf_counter() - t0) * 1000.0)

    wall_ms = (time.perf_counter() - wall_start) * 1000.0
    return {
        **local_res,
        "e2e_p50_ms": percentile(turn_e2e_ms, 50),
        "e2e_p95_ms": percentile(turn_e2e_ms, 95),
        "e2e_p99_ms": percentile(turn_e2e_ms, 99),
        "wall_ms": round(wall_ms, 2),
    }


def _find_knee_point(steps: list[dict[str, Any]]) -> dict[str, Any]:
    """Identify the concurrency knee point before latency/CPU inflection."""
    if not steps:
        return {"knee_concurrent_calls": 0, "reason": "no_steps"}

    baseline = steps[0]
    base_e2e_p95 = float(baseline.get("e2e_p95_ms") or 300.0)
    base_wall_p95 = max(1.0, float(baseline.get("wall_call_p95_ms") or 50.0))

    knee_step = steps[-1]
    knee_reason = "completed_all_steps_within_slo"

    for idx, step in enumerate(steps):
        e2e_p95 = float(step.get("e2e_p95_ms") or 0.0)
        wall_p95 = float(step.get("wall_call_p95_ms") or 0.0)
        vcpu_pct = float(step.get("single_core_saturation_pct") or 0.0)

        if idx > 0 and (
            e2e_p95 > base_e2e_p95 * 1.25
            or e2e_p95 > 500.0
            or (wall_p95 > base_wall_p95 * 3.0 and vcpu_pct >= 80.0)
        ):
            knee_step = steps[idx - 1]
            knee_reason = (
                f"Inflection at C={step['concurrent_calls']} "
                f"(e2e_p95={e2e_p95:.1f}ms vs baseline {base_e2e_p95:.1f}ms, "
                f"wall_p95={wall_p95:.1f}ms vs baseline {base_wall_p95:.1f}ms, "
                f"single_core_cpu={vcpu_pct:.1f}%)"
            )
            break

    return {
        "knee_concurrent_calls": int(knee_step["concurrent_calls"]),
        "recommended_hpa_target_calls_per_worker": max(
            10, int(round(int(knee_step["concurrent_calls"]) * 0.8))
        ),
        "knee_e2e_p95_ms": knee_step["e2e_p95_ms"],
        "knee_cpu_ms_per_call": knee_step["cpu_ms_per_call"],
        "knee_rss_mb": knee_step["rss_after_mb"],
        "reason": knee_reason,
    }


async def run_capacity_ramp(
    *,
    host: str = "http://localhost:8000",
    concurrency_steps: list[int] | None = None,
    turns_per_call: int = 3,
    chunks_per_turn: int = 4,
    denoise_enabled: bool = True,
    real_providers: bool = False,
    seed: int = 42,
) -> dict[str, Any]:
    reason = validate_target(host)
    if reason:
        raise RuntimeError(reason)

    if real_providers:
        missing = [k for k in REAL_PROVIDER_ENV if not (os.environ.get(k) or "").strip()]
        if missing:
            raise RuntimeError(
                "--real-providers requires live STT/LLM/TTS credentials; missing: "
                + ", ".join(missing)
            )

    import app.db.models  # noqa: F401
    import app.db.telephony_models  # noqa: F401
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.agent.latency import percentile
    from app.db.models import Base, Tenant

    steps = concurrency_steps or [10, 20, 30, 40, 50, 60]
    pcm16_audio = load_recorded_caller_pcm16(sample_rate=8000, duration_ms=320)
    hw = _hardware_profile()

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    tenant_id = uuid.uuid4()
    async with maker() as session:
        session.add(
            Tenant(
                id=tenant_id,
                name="CapacityRampTenant",
                twilio_number="+15550199999",
                is_active=True,
            )
        )
        await session.commit()

    await run_synthetic_ws_call(
        host=host,
        turns=1,
        chunks_per_turn=2,
        seed=seed,
        denoise_enabled=denoise_enabled,
        session_factory=maker,
        tenant_id=tenant_id,
        pcm16_audio=pcm16_audio,
    )

    baseline_rss_mb = _current_rss_mb()
    step_records: list[dict[str, Any]] = []

    try:
        async with httpx.AsyncClient(timeout=30.0) as real_http:
            for step_idx, concurrency in enumerate(steps):
                rss_before = _current_rss_mb()
                u0, s0 = _cpu_times()
                t0 = time.perf_counter()

                if real_providers:
                    tasks = [
                        _run_real_provider_call(
                            client=real_http,
                            host=host,
                            turns=turns_per_call,
                            chunks_per_turn=chunks_per_turn,
                            seed=seed + step_idx * 1000 + call_i,
                            denoise_enabled=denoise_enabled,
                            session_factory=maker,
                            tenant_id=tenant_id,
                            pcm16_audio=pcm16_audio,
                        )
                        for call_i in range(concurrency)
                    ]
                else:
                    tasks = [
                        run_synthetic_ws_call(
                            host=host,
                            turns=turns_per_call,
                            chunks_per_turn=chunks_per_turn,
                            seed=seed + step_idx * 1000 + call_i,
                            denoise_enabled=denoise_enabled,
                            session_factory=maker,
                            tenant_id=tenant_id,
                            pcm16_audio=pcm16_audio,
                        )
                        for call_i in range(concurrency)
                    ]
                results = await asyncio.gather(*tasks)

                wall_s = max(0.0001, time.perf_counter() - t0)
                u1, s1 = _cpu_times()
                rss_after = _current_rss_mb()

                cpu_user_s = max(0.0, u1 - u0)
                cpu_sys_s = max(0.0, s1 - s0)
                cpu_total_s = cpu_user_s + cpu_sys_s

                e2e_p50s = [float(r["e2e_p50_ms"]) for r in results if r.get("e2e_p50_ms") is not None]
                e2e_p95s = [float(r["e2e_p95_ms"]) for r in results if r.get("e2e_p95_ms") is not None]
                e2e_p99s = [float(r["e2e_p99_ms"]) for r in results if r.get("e2e_p99_ms") is not None]
                wall_ms_list = [float(r["wall_ms"]) for r in results if r.get("wall_ms") is not None]

                single_core_pct = min(100.0, round((cpu_total_s / wall_s) * 100.0, 2))
                rss_delta_mb = round(max(0.0, rss_after - baseline_rss_mb), 2)
                step_records.append(
                    {
                        "concurrent_calls": concurrency,
                        "turns_per_call": turns_per_call,
                        "total_turns": sum(int(r.get("turns") or 0) for r in results),
                        "inbound_frames": sum(int(r.get("inbound_frames") or 0) for r in results),
                        "outbound_frames": sum(int(r.get("outbound_frames") or 0) for r in results),
                        "wall_elapsed_s": round(wall_s, 4),
                        "cpu_user_s": round(cpu_user_s, 4),
                        "cpu_sys_s": round(cpu_sys_s, 4),
                        "cpu_total_s": round(cpu_total_s, 4),
                        "cpu_ms_per_call": round((cpu_total_s * 1000.0) / concurrency, 3),
                        "cpu_pct_per_call": round(single_core_pct / concurrency, 3),
                        "single_core_saturation_pct": single_core_pct,
                        "rss_before_mb": rss_before,
                        "rss_after_mb": rss_after,
                        "rss_delta_mb": rss_delta_mb,
                        "rss_delta_mb_per_call": round(rss_delta_mb / concurrency, 3),
                        "e2e_p50_ms": percentile(e2e_p50s, 50),
                        "e2e_p95_ms": percentile(e2e_p95s, 95),
                        "e2e_p99_ms": percentile(e2e_p99s, 99),
                        "wall_call_p50_ms": percentile(wall_ms_list, 50),
                        "wall_call_p95_ms": percentile(wall_ms_list, 95),
                    }
                )
    finally:
        await engine.dispose()

    knee = _find_knee_point(step_records)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "path_under_test": "/telephony/ws (app.telephony.twilio_handler.media_stream)",
        "provider_fakes": not real_providers,
        "provider_fakes_note": (
            "Live Deepgram Nova-2 + OpenAI + ElevenLabs Turbo v2.5 HTTP/SSE streams executed concurrently."
            if real_providers
            else (
                "External STT/LLM/TTS network calls use deterministic local provider fakes for CI cost safety; "
                "TwilioFrameSerializer mu-law 8kHz decode/encode, NoisereduceFilter spectral denoise, "
                "stream token HMAC verification, LatencyObserver, and DB session persistence are real."
            )
        ),
        "denoise_enabled": denoise_enabled,
        "cost_model": estimate_cost_per_concurrent_call(denoise_enabled=denoise_enabled),
        "hardware": hw,
        "baseline_rss_mb": baseline_rss_mb,
        "steps": step_records,
        "knee_point": knee,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="http://localhost:8000", help="Target host URL")
    parser.add_argument(
        "--steps",
        default="10,20,30,40,50,60",
        help="Comma-separated concurrency ramp steps on 1 worker",
    )
    parser.add_argument("--turns", type=int, default=3, help="Turns per call")
    parser.add_argument("--chunks-per-turn", type=int, default=4, help="20ms audio chunks per turn")
    parser.add_argument(
        "--no-denoise",
        action="store_true",
        help="Disable NoisereduceFilter to compare raw transport vs DSP CPU",
    )
    parser.add_argument(
        "--real-providers",
        action="store_true",
        help="Run concurrent turns against real Deepgram + OpenAI + ElevenLabs APIs (default: fake mode for CI).",
    )
    parser.add_argument(
        "--output",
        default="evidence/capacity/voice_capacity_ramp.json",
        help="Output JSON path for the capacity model data",
    )
    args = parser.parse_args(argv)

    reason = validate_target(args.host)
    if reason:
        print(f"ERROR: {reason}", file=sys.stderr)
        return 2

    step_list = [int(x.strip()) for x in args.steps.split(",") if x.strip()]
    try:
        report = asyncio.run(
            run_capacity_ramp(
                host=args.host,
                concurrency_steps=step_list,
                turns_per_call=max(1, args.turns),
                chunks_per_turn=max(1, args.chunks_per_turn),
                denoise_enabled=not args.no_denoise,
                real_providers=args.real_providers,
            )
        )
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.output:
        out_path = Path(args.output)
        if not out_path.is_absolute():
            out_path = ROOT / out_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
