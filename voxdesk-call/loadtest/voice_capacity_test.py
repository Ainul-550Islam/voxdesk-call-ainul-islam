#!/usr/bin/env python3
"""Single-worker voice capacity ramp test for VoxDesk (Part 8 / Gate G9).

Ramps concurrent calls ``10 -> N`` (default ``10, 20, 30, 40, 50, 60``) on a
single worker process against the REAL ``/telephony/ws`` path using recorded
caller audio (``loadtest/voice_ws_user.py``), recording per-step CPU time,
resident memory (RSS), per-call wall time, and ``LatencyObserver`` E2E latency
percentiles (p50, p95, p99), and determines the single-worker capacity knee
point.

Provider fakes are used for external STT/LLM/TTS network I/O to avoid live API
spend while exercising the real FastAPI WebSocket handler, stream-token HMAC
verification, ``TwilioFrameSerializer`` mu-law decode/encode, ``NoisereduceFilter``
spectral denoise, ``LatencyObserver``, and database persistence.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import resource
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from .safety import validate_target
    from .voice_ws_user import load_recorded_caller_pcm16, run_synthetic_ws_call
except ImportError:  # pragma: no cover - script execution path
    from safety import validate_target
    from voice_ws_user import load_recorded_caller_pcm16, run_synthetic_ws_call


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

        # Knee criterion: E2E p95 degrades > 25% above baseline, or wall p95 per
        # concurrent batch exceeds 3.0x baseline under single-worker GIL/CPU contention.
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
    seed: int = 42,
) -> dict[str, Any]:
    reason = validate_target(host)
    if reason:
        raise RuntimeError(reason)

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

    # Warm-up 1 call so one-time imports/allocations don't skew the C=10 step
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
        for step_idx, concurrency in enumerate(steps):
            rss_before = _current_rss_mb()
            u0, s0 = _cpu_times()
            t0 = time.perf_counter()

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
                    "single_core_saturation_pct": single_core_pct,
                    "rss_before_mb": rss_before,
                    "rss_after_mb": rss_after,
                    "rss_delta_mb": round(max(0.0, rss_after - baseline_rss_mb), 2),
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
        "provider_fakes": True,
        "provider_fakes_note": (
            "External STT/LLM/TTS network calls use deterministic local provider fakes for cost safety; "
            "TwilioFrameSerializer mu-law 8kHz decode/encode, NoisereduceFilter spectral denoise, "
            "stream token HMAC verification, LatencyObserver, and DB session persistence are real."
        ),
        "denoise_enabled": denoise_enabled,
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
    parser.add_argument("--turns", type=int, default=3, help="Turns per synthetic call")
    parser.add_argument("--chunks-per-turn", type=int, default=4, help="20ms audio chunks per turn")
    parser.add_argument(
        "--no-denoise",
        action="store_true",
        help="Disable NoisereduceFilter to compare raw transport vs DSP CPU",
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
    report = asyncio.run(
        run_capacity_ramp(
            host=args.host,
            concurrency_steps=step_list,
            turns_per_call=max(1, args.turns),
            chunks_per_turn=max(1, args.chunks_per_turn),
            denoise_enabled=not args.no_denoise,
        )
    )

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
