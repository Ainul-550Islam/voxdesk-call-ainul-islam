#!/usr/bin/env python3
"""Run an explicitly authorized N-call real PSTN caller-leg latency study.

Creates calls through VoxDesk's authenticated `/api/calls` route so each call
has a durable database record and the signed outbound media-stream URL. It
analyzes Twilio dual-channel recordings and writes:
- `evidence/latency/<date>/real_study.json` (`provider_fakes: false`)
- `evidence/latency/<date>/real_study.csv`

Never substitutes mock providers. Caller/agent channel indices default to 0/1 and are
recorded in the output; their direction is marked unverified until a controlled live
calibration confirms the deployed Twilio recording layout. If credentials are absent
and `--record-unconfigured` is passed, writes an explicit
`NOT_RUN_MISSING_CREDENTIALS` ledger (`calls_attempted: 0`, `provider_fakes: false`)
without fabricating numbers.
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
import tempfile
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agent.latency import percentile  # noqa: E402
from scripts.measure_pstn_latency import analyze_dual_channel_wav  # noqa: E402
from scripts.real_pstn_support import (  # noqa: E402
    LIVE_PSTN_REQUIRED_ENV,
    create_voxdesk_outbound_call,
    missing_live_pstn_env,
    wait_for_dual_channel_recordings,
    wait_for_twilio_terminal,
    wait_for_voxdesk_transcript,
)

REQUIRED_ENV = (
    "VOXDESK_REAL_INTEGRATION",
    "VOXDESK_REAL_API_KEY",
    "VOXDESK_PUBLIC_BASE_URL",
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
    "TWILIO_PHONE_NUMBER",
    "TWILIO_TEST_TO_NUMBER",
    "DEEPGRAM_API_KEY",
    "OPENAI_API_KEY",
    "ELEVENLABS_API_KEY",
)

CSV_COLUMNS = [
    "call_index",
    "call_id",
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
    caller_channel: int,
    agent_channel: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    missing = missing_live_pstn_env()
    for name in ("DEEPGRAM_API_KEY", "OPENAI_API_KEY", "ELEVENLABS_API_KEY"):
        if not (os.environ.get(name) or "").strip():
            missing.append(name)
    if missing:
        raise RuntimeError("Live PSTN study is missing: " + ", ".join(missing))
    if provider.strip().lower() != "twilio":
        raise RuntimeError("N-call real PSTN study currently supports only Twilio")

    sid = os.environ["TWILIO_ACCOUNT_SID"].strip()
    auth = (sid, os.environ["TWILIO_AUTH_TOKEN"].strip())
    from_num = os.environ["TWILIO_PHONE_NUMBER"].strip()
    to_num = os.environ["TWILIO_TEST_TO_NUMBER"].strip()
    pub_base = os.environ["VOXDESK_PUBLIC_BASE_URL"].strip().rstrip("/")
    api_key = os.environ["VOXDESK_REAL_API_KEY"].strip()
    api_base = f"https://api.twilio.com/2010-04-01/Accounts/{sid}"

    caller_leg_samples: list[float] = []
    failures: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []

    async with (
        httpx.AsyncClient(timeout=30.0, auth=auth) as twilio_client,
        httpx.AsyncClient(timeout=45.0) as voxdesk_client,
    ):
        for idx in range(1, calls + 1):
            call_sid = ""
            call_id = ""
            terminal = False
            recording_sids: list[str] = []
            try:
                call = await create_voxdesk_outbound_call(
                    voxdesk_client,
                    public_base_url=pub_base,
                    api_key=api_key,
                    to_number=to_num,
                    from_number=from_num,
                    timeout_seconds=30,
                    idempotency_key=f"real-call-study-{idx}-{os.urandom(8).hex()}",
                )
                call_id = call["id"]
                call_sid = call["call_sid"]

                final_doc = await wait_for_twilio_terminal(
                    twilio_client,
                    api_base=api_base,
                    call_sid=call_sid,
                    timeout_seconds=150.0,
                    poll_seconds=2.5,
                )
                final_status = str(final_doc.get("status") or "").lower()
                terminal = True
                if final_status != "completed":
                    raise RuntimeError(f"PSTN call ended with status {final_status!r}")

                recordings = await wait_for_dual_channel_recordings(
                    twilio_client,
                    api_base=api_base,
                    call_sid=call_sid,
                )
                recording_sids = [str(recording["sid"]) for recording in recordings]
                recording_sid = recording_sids[0]
                wav_resp = await twilio_client.get(
                    f"{api_base}/Recordings/{recording_sid}.wav?RequestedChannels=2"
                )
                if wav_resp.status_code != 200:
                    raise RuntimeError(f"Twilio recording download returned HTTP {wav_resp.status_code}")

                # The raw recording contains personal speech. Analyze it from a
                # temporary file and never place it in the study artifact bundle.
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as wav_file:
                    wav_file.write(wav_resp.content)
                    wav_file.flush()
                    wav_metrics = analyze_dual_channel_wav(
                        wav_file.name,
                        provider=provider,
                        model=llm_model,
                        region=region,
                        codec=codec,
                        caller_channel=caller_channel,
                        agent_channel=agent_channel,
                    )
                if not wav_metrics["matched_turns"]:
                    raise RuntimeError("Dual-channel recording contains no measured caller-to-agent turn")

                replay = await wait_for_voxdesk_transcript(
                    voxdesk_client,
                    public_base_url=pub_base,
                    api_key=api_key,
                    call_id=call_id,
                )
                transcript_turns = [
                    event for event in replay.get("timeline") or []
                    if isinstance(event, dict) and event.get("event") == "transcript_turn"
                ]
                speakers = {str(event.get("speaker") or "").lower() for event in transcript_turns}
                if not {"user", "assistant"}.issubset(speakers):
                    raise RuntimeError("Authenticated call replay lacks caller and assistant transcript turns")

                for turn in wav_metrics["turns"]:
                    caller_leg_samples.append(float(turn["caller_leg_latency_ms"]))
                row = {
                    "call_index": idx,
                    "call_id": call_id,
                    "call_sid": call_sid,
                    "status": "completed",
                    "caller_leg_p50_ms": wav_metrics["p50_ms"],
                    "caller_leg_p95_ms": wav_metrics["p95_ms"],
                    "stt_final_ms": None,
                    "llm_ttft_ms": None,
                    "tts_ttfb_ms": None,
                    "pipeline_e2e_ms": None,
                    "matched_turns": wav_metrics["matched_turns"],
                    "transcript_turns": len(transcript_turns),
                    "interruptions_detected": None,
                    "interruption_stop_ms": None,
                    "failure_reason": "",
                }
                rows.append(row)
            except Exception as exc:
                reason = str(exc)[:300]
                failures.append(
                    {"call_index": idx, "call_id": call_id, "call_sid": call_sid, "reason": reason}
                )
                rows.append(
                    {
                        "call_index": idx,
                        "call_id": call_id,
                        "call_sid": call_sid,
                        "status": "error",
                        "failure_reason": reason,
                    }
                )
            finally:
                for recording_sid in recording_sids:
                    try:
                        await twilio_client.delete(f"{api_base}/Recordings/{recording_sid}.json")
                    except httpx.HTTPError:
                        pass
                if call_sid and not terminal:
                    try:
                        await twilio_client.post(
                            f"{api_base}/Calls/{call_sid}.json",
                            data={"Status": "completed"},
                        )
                    except httpx.HTTPError:
                        pass

    def _stats(vals: list[float]) -> dict[str, float | None]:
        if not vals:
            return {"p50_ms": None, "p95_ms": None, "p99_ms": None}
        return {
            "p50_ms": round(percentile(vals, 50), 2),
            "p95_ms": round(percentile(vals, 95), 2),
            "p99_ms": round(percentile(vals, 99), 2),
        }

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    completed_count = sum(1 for row in rows if row.get("status") == "completed")
    payload: dict[str, Any] = {
        "schema": "voxdesk.real_call_study/v1",
        "generated_at": now_iso,
        "study_date": out_dir.name,
        "provider_fakes": False,
        "status": (
            "COMPLETED" if completed_count == calls and not failures
            else "PARTIAL" if completed_count > 0
            else "FAILED_ALL_CALLS"
        ),
        "providers": {
            "telephony": provider,
            "stt": f"deepgram/{stt_model}",
            "llm": f"openai/{llm_model}",
            "tts": f"elevenlabs/{tts_model}",
            "region": region,
            "codec": codec,
        },
        "channel_mapping": {
            "caller_channel": caller_channel,
            "agent_channel": agent_channel,
            "verified_by_live_calibration": False,
        },
        "hardware": _hardware_metadata(),
        "calls_requested": calls,
        "calls_attempted": len(rows),
        "calls_completed": completed_count,
        "calls_failed": len(failures),
        "turns_measured": len(caller_leg_samples),
        "caller_leg_e2e_ms": _stats(caller_leg_samples),
        "per_stage_ttfb_ms": {
            "measured": False,
            "reason": "The /api/calls and replay responses used by this runner do not include per-call stage timing.",
            "stt_final": _stats([]),
            "llm_ttft": _stats([]),
            "tts_ttfb": _stats([]),
            "pipeline_e2e": _stats([]),
        },
        "interruption_outcomes": {
            "measured": False,
            "reason": "The replay response used by this runner contains no per-call interruption or stop-latency fields.",
            "interruptions_detected": None,
            "clean_stops_under_250ms": None,
            "stop_latency": _stats([]),
        },
        "failures": failures,
    }
    return payload, rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calls", type=int, default=1, help="Number of controlled, paid PSTN test calls to place (default: 1).")
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
    parser.add_argument("--caller-channel", type=int, default=0)
    parser.add_argument("--agent-channel", type=int, default=1)
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

    if args.calls < 1:
        parser.error("--calls must be at least 1")
    out_dir = args.output_dir or (ROOT / "evidence" / "latency" / args.date)
    missing = missing_live_pstn_env()
    missing.extend(
        name
        for name in REQUIRED_ENV
        if name not in LIVE_PSTN_REQUIRED_ENV and not (os.environ.get(name) or "").strip()
    )
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
            "channel_mapping": {
                "caller_channel": args.caller_channel,
                "agent_channel": args.agent_channel,
                "verified_by_live_calibration": False,
            },
            "hardware": _hardware_metadata(),
            "calls_requested": args.calls,
            "calls_attempted": 0,
            "calls_completed": 0,
            "calls_failed": 0,
            "turns_measured": 0,
            "caller_leg_e2e_ms": {"p50_ms": None, "p95_ms": None, "p99_ms": None},
            "per_stage_ttfb_ms": {
                "measured": False,
                "reason": "The /api/calls and replay responses used by this runner do not include per-call stage timing.",
                "stt_final": {"p50_ms": None, "p95_ms": None, "p99_ms": None},
                "llm_ttft": {"p50_ms": None, "p95_ms": None, "p99_ms": None},
                "tts_ttfb": {"p50_ms": None, "p95_ms": None, "p99_ms": None},
                "pipeline_e2e": {"p50_ms": None, "p95_ms": None, "p99_ms": None},
            },
            "interruption_outcomes": {
                "measured": False,
                "reason": "The replay response used by this runner contains no per-call interruption or stop-latency fields.",
                "interruptions_detected": None,
                "clean_stops_under_250ms": None,
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

    try:
        payload, rows = asyncio.run(
            _execute_real_study(
                calls=args.calls,
                out_dir=out_dir,
                provider=args.provider,
                stt_model=args.stt_model,
                llm_model=args.llm_model,
                tts_model=args.tts_model,
                region=args.region,
                codec=args.codec,
                caller_channel=args.caller_channel,
                agent_channel=args.agent_channel,
            )
        )
    except Exception as exc:
        print(f"REFUSED/FAILED: {type(exc).__name__}: {str(exc)[:300]}", file=sys.stderr)
        return 2
    json_p, csv_p = _write_study_files(out_dir, payload, rows)
    print(json.dumps({"status": payload["status"], "json": str(json_p), "csv": str(csv_p)}, indent=2))
    return 0 if payload["status"] == "COMPLETED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
