#!/usr/bin/env python3
"""Run an N-call real PSTN latency study via Twilio REST against a scripted callee.

Collects per-call dual-channel recordings (caller leg vs agent leg), computes
caller-leg end-of-speech -> first agent audio onset via `measure_pstn_latency`,
joins internal `Call` + `CallLatencyStat` per-stage TTFB breakdowns and
interruption outcomes from VoxDesk `/api/calls`, and writes:
- `evidence/latency/<date>/real_study.json` (`provider_fakes: false`)
- `evidence/latency/<date>/real_study.csv`

Never substitutes mock providers. If credentials are absent and `--record-unconfigured`
is passed, writes an explicit `NOT_RUN_MISSING_CREDENTIALS` ledger (`calls_attempted: 0`,
`provider_fakes: false`) so `docs/LATENCY_BENCHMARK.md` can report exact status without
fabricating numbers.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import sys
import time
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agent.latency import percentile  # noqa: E402
from scripts.measure_pstn_latency import analyze_dual_channel_wav  # noqa: E402

REQUIRED_ENV = (
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
    "TWILIO_PHONE_NUMBER",
    "TWILIO_TEST_TO_NUMBER",
    "VOXDESK_PUBLIC_BASE_URL",
    "DEEPGRAM_API_KEY",
    "OPENAI_API_KEY",
    "ELEVENLABS_API_KEY",
)

CSV_COLUMNS = [
    "call_index",
    "call_sid",
    "status",
    "caller_leg_p50_ms",
    "caller_leg_p95_ms",
    "stt_final_ms",
    "llm_ttft_ms",
    "tts_ttfb_ms",
    "pipeline_e2e_ms",
    "matched_turns",
    "interruptions_detected",
    "interruption_stop_ms",
    "failure_reason",
]


def _hardware_metadata() -> dict[str, Any]:
    return {
        "os": f"{platform.system()} {platform.release()}",
        "arch": platform.machine(),
        "python_version": platform.python_version(),
        "cpu_count": os.cpu_count() or 1,
    }


def _write_study_files(out_dir: Path, payload: dict[str, Any], rows: list[dict[str, Any]]) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "real_study.json"
    csv_path = out_dir / "real_study.csv"
    json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for r in rows:
            writer.writerow({col: r.get(col) for col in CSV_COLUMNS})
    return json_path, csv_path


async def _execute_real_study(
    *,
    calls: int,
    out_dir: Path,
    provider: str,
    stt_model: str,
    llm_model: str,
    tts_model: str,
    region: str,
    codec: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    sid = os.environ["TWILIO_ACCOUNT_SID"].strip()
    tok = os.environ["TWILIO_AUTH_TOKEN"].strip()
    from_num = os.environ["TWILIO_PHONE_NUMBER"].strip()
    to_num = os.environ["TWILIO_TEST_TO_NUMBER"].strip()
    pub_base = os.environ["VOXDESK_PUBLIC_BASE_URL"].strip().rstrip("/")
    api_key = (os.environ.get("VOXDESK_REAL_API_KEY") or "").strip()

    api_base = f"https://api.twilio.com/2010-04-01/Accounts/{sid}"
    recordings_dir = out_dir / "recordings"
    recordings_dir.mkdir(parents=True, exist_ok=True)

    caller_leg_samples: list[float] = []
    stt_samples: list[float] = []
    llm_samples: list[float] = []
    tts_samples: list[float] = []
    e2e_samples: list[float] = []
    barge_in_stop_samples: list[float] = []
    failures: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    interruption_total = 0
    interruption_clean = 0

    async with httpx.AsyncClient(timeout=30.0, auth=(sid, tok)) as twilio_client:
        for idx in range(1, calls + 1):
            call_sid = ""
            try:
                dial = await twilio_client.post(
                    f"{api_base}/Calls.json",
                    data={
                        "From": from_num,
                        "To": to_num,
                        "Url": f"{pub_base}/telephony/voice",
                        "StatusCallback": f"{pub_base}/telephony/status",
                        "StatusCallbackEvent": ["initiated", "ringing", "answered", "completed"],
                        "Record": "true",
                        "RecordingChannels": "dual",
                        "Timeout": "30",
                    },
                )
                dial.raise_for_status()
                call_sid = str(dial.json()["sid"])

                final_status = "initiated"
                deadline = time.monotonic() + 150.0
                while time.monotonic() < deadline:
                    poll = await twilio_client.get(f"{api_base}/Calls/{call_sid}.json")
                    poll.raise_for_status()
                    final_status = str(poll.json().get("status") or "").lower()
                    if final_status in {"completed", "failed", "busy", "no-answer", "canceled"}:
                        break
                    await asyncio.sleep(2.5)

                if final_status != "completed":
                    reason = f"terminal_status={final_status}"
                    failures.append({"call_index": idx, "call_sid": call_sid, "reason": reason})
                    rows.append(
                        {
                            "call_index": idx,
                            "call_sid": call_sid,
                            "status": final_status,
                            "failure_reason": reason,
                        }
                    )
                    continue

                rec_resp = await twilio_client.get(f"{api_base}/Calls/{call_sid}/Recordings.json")
                rec_resp.raise_for_status()
                recs = rec_resp.json().get("recordings") or []
                if not recs:
                    reason = "missing_dual_channel_recording"
                    failures.append({"call_index": idx, "call_sid": call_sid, "reason": reason})
                    rows.append(
                        {
                            "call_index": idx,
                            "call_sid": call_sid,
                            "status": "missing_recording",
                            "failure_reason": reason,
                        }
                    )
                    continue

                rec_sid = str(recs[0]["sid"])
                wav_resp = await twilio_client.get(f"{api_base}/Recordings/{rec_sid}.wav?RequestedChannels=2")
                wav_resp.raise_for_status()
                wav_file = recordings_dir / f"call_{idx:03d}_{call_sid}.wav"
                wav_file.write_bytes(wav_resp.content)

                wav_metrics = analyze_dual_channel_wav(
                    wav_file,
                    provider=provider,
                    model=llm_model,
                    region=region,
                    codec=codec,
                )
                for turn in wav_metrics["turns"]:
                    caller_leg_samples.append(float(turn["caller_leg_latency_ms"]))

                stt_ms = None
                llm_ms = None
                tts_ms = None
                e2e_ms = None
                barge_count = 0
                barge_stop = None

                if api_key:
                    async with httpx.AsyncClient(timeout=15.0) as vox_client:
                        c_resp = await vox_client.get(
                            f"{pub_base}/api/calls",
                            params={"call_sid": call_sid},
                            headers={"Authorization": f"Bearer {api_key}"},
                        )
                        if c_resp.status_code == 200:
                            body = c_resp.json()
                            items = body.get("items", body) if isinstance(body, dict) else body
                            if isinstance(items, list) and items:
                                lat = items[0].get("latency") or {}
                                stt_ms = lat.get("stt_final_p50_ms")
                                llm_ms = lat.get("llm_ttft_p50_ms")
                                tts_ms = lat.get("tts_ttfb_p50_ms")
                                e2e_ms = lat.get("e2e_p50_ms")
                                barge_count = int(lat.get("interruptions_count") or 0)
                                barge_stop = lat.get("interruption_stop_p50_ms")

                for val, bucket in (
                    (stt_ms, stt_samples),
                    (llm_ms, llm_samples),
                    (tts_ms, tts_samples),
                    (e2e_ms, e2e_samples),
                    (barge_stop, barge_in_stop_samples),
                ):
                    if isinstance(val, (int, float)):
                        bucket.append(float(val))
                interruption_total += barge_count
                if barge_stop is not None and float(barge_stop) <= 250.0:
                    interruption_clean += barge_count

                rows.append(
                    {
                        "call_index": idx,
                        "call_sid": call_sid,
                        "status": "completed",
                        "caller_leg_p50_ms": wav_metrics["p50_ms"],
                        "caller_leg_p95_ms": wav_metrics["p95_ms"],
                        "stt_final_ms": stt_ms,
                        "llm_ttft_ms": llm_ms,
                        "tts_ttfb_ms": tts_ms,
                        "pipeline_e2e_ms": e2e_ms,
                        "matched_turns": wav_metrics["matched_turns"],
                        "interruptions_detected": barge_count,
                        "interruption_stop_ms": barge_stop,
                        "failure_reason": "",
                    }
                )
            except Exception as exc:
                failures.append({"call_index": idx, "call_sid": call_sid, "reason": str(exc)})
                rows.append(
                    {
                        "call_index": idx,
                        "call_sid": call_sid,
                        "status": "error",
                        "failure_reason": str(exc),
                    }
                )

    def _stats(vals: list[float]) -> dict[str, float | None]:
        if not vals:
            return {"p50_ms": None, "p95_ms": None, "p99_ms": None}
        return {
            "p50_ms": round(percentile(vals, 50), 2),
            "p95_ms": round(percentile(vals, 95), 2),
            "p99_ms": round(percentile(vals, 99), 2),
        }

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    completed_count = sum(1 for r in rows if r.get("status") == "completed")
    payload: dict[str, Any] = {
        "schema": "voxdesk.real_call_study/v1",
        "generated_at": now_iso,
        "study_date": out_dir.name,
        "provider_fakes": False,
        "status": "COMPLETED" if completed_count > 0 else "FAILED_ALL_CALLS",
        "providers": {
            "telephony": provider,
            "stt": f"deepgram/{stt_model}",
            "llm": f"openai/{llm_model}",
            "tts": f"elevenlabs/{tts_model}",
            "region": region,
            "codec": codec,
        },
        "hardware": _hardware_metadata(),
        "calls_requested": calls,
        "calls_attempted": len(rows),
        "calls_completed": completed_count,
        "calls_failed": len(failures),
        "turns_measured": len(caller_leg_samples),
        "caller_leg_e2e_ms": _stats(caller_leg_samples),
        "per_stage_ttfb_ms": {
            "stt_final": _stats(stt_samples),
            "llm_ttft": _stats(llm_samples),
            "tts_ttfb": _stats(tts_samples),
            "pipeline_e2e": _stats(e2e_samples),
        },
        "interruption_outcomes": {
            "interruptions_detected": interruption_total,
            "clean_stops_under_250ms": interruption_clean,
            "stop_latency": _stats(barge_in_stop_samples),
        },
        "failures": failures,
    }
    return payload, rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calls", type=int, default=30, help="Number of real PSTN calls to place.")
    parser.add_argument(
        "--date",
        default=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        help="Date directory under evidence/latency/<date>/.",
    )
    parser.add_argument("--provider", default="twilio")
    parser.add_argument("--stt-model", default=os.environ.get("DEEPGRAM_MODEL", "nova-2"))
    parser.add_argument("--llm-model", default=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"))
    parser.add_argument("--tts-model", default=os.environ.get("ELEVENLABS_MODEL_ID", "eleven_turbo_v2_5"))
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    parser.add_argument("--codec", default="PCMU/8000")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Override output directory (defaults to evidence/latency/<date>).",
    )
    parser.add_argument(
        "--record-unconfigured",
        action="store_true",
        help="When credentials are absent, write an honest NOT_RUN_MISSING_CREDENTIALS real_study.json and exit 0.",
    )
    args = parser.parse_args(argv)

    out_dir = args.output_dir or (ROOT / "evidence" / "latency" / args.date)
    missing = [name for name in REQUIRED_ENV if not (os.environ.get(name) or "").strip()]
    if missing:
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        unconfigured_payload: dict[str, Any] = {
            "schema": "voxdesk.real_call_study/v1",
            "generated_at": now_iso,
            "study_date": args.date,
            "provider_fakes": False,
            "status": "NOT_RUN_MISSING_CREDENTIALS",
            "missing_credentials": missing,
            "providers": {
                "telephony": args.provider,
                "stt": f"deepgram/{args.stt_model}",
                "llm": f"openai/{args.llm_model}",
                "tts": f"elevenlabs/{args.tts_model}",
                "region": args.region,
                "codec": args.codec,
            },
            "hardware": _hardware_metadata(),
            "calls_requested": args.calls,
            "calls_attempted": 0,
            "calls_completed": 0,
            "calls_failed": 0,
            "turns_measured": 0,
            "caller_leg_e2e_ms": {"p50_ms": None, "p95_ms": None, "p99_ms": None},
            "per_stage_ttfb_ms": {
                "stt_final": {"p50_ms": None, "p95_ms": None, "p99_ms": None},
                "llm_ttft": {"p50_ms": None, "p95_ms": None, "p99_ms": None},
                "tts_ttfb": {"p50_ms": None, "p95_ms": None, "p99_ms": None},
                "pipeline_e2e": {"p50_ms": None, "p95_ms": None, "p99_ms": None},
            },
            "interruption_outcomes": {
                "interruptions_detected": 0,
                "clean_stops_under_250ms": 0,
                "stop_latency": {"p50_ms": None, "p95_ms": None, "p99_ms": None},
            },
            "failures": [],
        }
        if args.record_unconfigured:
            json_p, csv_p = _write_study_files(out_dir, unconfigured_payload, [])
            print(json.dumps({"status": "NOT_RUN_MISSING_CREDENTIALS", "json": str(json_p), "csv": str(csv_p)}, indent=2))
            return 0
        print(
            "REFUSING to fabricate PSTN latency study; missing credentials: " + ", ".join(missing),
            file=sys.stderr,
        )
        return 2

    payload, rows = asyncio.run(
        _execute_real_study(
            calls=max(1, args.calls),
            out_dir=out_dir,
            provider=args.provider,
            stt_model=args.stt_model,
            llm_model=args.llm_model,
            tts_model=args.tts_model,
            region=args.region,
            codec=args.codec,
        )
    )
    json_p, csv_p = _write_study_files(out_dir, payload, rows)
    print(json.dumps({"status": payload["status"], "json": str(json_p), "csv": str(csv_p)}, indent=2))
    return 0 if payload["calls_completed"] > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
