#!/usr/bin/env python3
"""Measure end-of-speech -> first agent audio at the CALLER leg from a dual-channel recording.

Supports two real-only modes (`provider_fakes: false`):
1. Offline WAV analysis (`--wav path/to/dual_channel.wav`): reads a 2-channel PCM WAV
   (default channel 0 = caller and channel 1 = agent; override with the channel flags),
   detects caller speech segments and trailing end-of-speech timestamps, locates the
   first subsequent agent audio onset on the caller-heard leg, and emits JSON with
   `provider`, `model`, `region`, `codec`, `turns`, and `p50_ms` / `p95_ms` / `p99_ms`.
2. Live PSTN capture (`--live`): creates real Twilio calls through the authenticated
   VoxDesk outbound API, downloads each dual-channel carrier recording, analyzes it,
   deletes the recording after analysis, and emits JSON. Live mode requires an explicit
   `VOXDESK_REAL_INTEGRATION=1` opt-in and a controlled test callee. Channel direction
   is an assumption until a controlled live calibration confirms it; reports mark the
   mapping unverified by default.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import struct
import sys
import tempfile
from typing import Any
import wave

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agent.latency import percentile  # noqa: E402
from scripts.real_pstn_support import (  # noqa: E402
    create_voxdesk_outbound_call,
    missing_live_pstn_env,
    wait_for_dual_channel_recordings,
    wait_for_twilio_terminal,
    wait_for_voxdesk_transcript,
)


def _rms_pcm16(samples: list[int]) -> float:
    if not samples:
        return 0.0
    acc = sum(s * s for s in samples)
    return math.sqrt(acc / len(samples))


def _dbfs_to_pcm16_rms(threshold_dbfs: float) -> float:
    clamped = max(-90.0, min(0.0, float(threshold_dbfs)))
    return 32768.0 * (10.0 ** (clamped / 20.0))


def _extract_channel_pcm16(
    raw_bytes: bytes,
    *,
    channels: int,
    sample_width: int,
    channel_index: int,
) -> list[int]:
    if sample_width != 2:
        raise ValueError(f"Expected 16-bit PCM WAV (sampwidth=2), got sampwidth={sample_width}")
    if channel_index < 0 or channel_index >= channels:
        raise ValueError(f"channel_index={channel_index} out of range for channels={channels}")

    frame_bytes = channels * 2
    total_frames = len(raw_bytes) // frame_bytes
    offset_bytes = channel_index * 2
    out: list[int] = [0] * total_frames
    for idx in range(total_frames):
        pos = idx * frame_bytes + offset_bytes
        (out[idx],) = struct.unpack_from("<h", raw_bytes, pos)
    return out


def _detect_speech_intervals_ms(
    samples: list[int],
    *,
    sample_rate: int,
    window_ms: float = 10.0,
    threshold_rms: float,
    min_speech_ms: float = 80.0,
    min_silence_ms: float = 150.0,
) -> list[tuple[float, float]]:
    """Return `(start_ms, end_ms)` speech intervals on one channel."""
    win_samples = max(1, int(round(sample_rate * (window_ms / 1000.0))))
    num_windows = len(samples) // win_samples
    if num_windows == 0:
        return []

    active = [
        _rms_pcm16(samples[w * win_samples : (w + 1) * win_samples]) >= threshold_rms
        for w in range(num_windows)
    ]

    min_silence_wins = max(1, int(round(min_silence_ms / window_ms)))
    min_speech_wins = max(1, int(round(min_speech_ms / window_ms)))

    intervals: list[tuple[float, float]] = []
    in_speech = False
    speech_start_w = 0
    last_voice_w = 0
    silence_run = 0

    for w, is_voice in enumerate(active):
        if is_voice:
            if not in_speech:
                in_speech = True
                speech_start_w = w
            last_voice_w = w
            silence_run = 0
        elif in_speech:
            silence_run += 1
            if silence_run >= min_silence_wins:
                if (last_voice_w - speech_start_w + 1) >= min_speech_wins:
                    intervals.append(
                        (
                            round(speech_start_w * window_ms, 2),
                            round((last_voice_w + 1) * window_ms, 2),
                        )
                    )
                in_speech = False
                silence_run = 0

    if in_speech and (last_voice_w - speech_start_w + 1) >= min_speech_wins:
        intervals.append(
            (
                round(speech_start_w * window_ms, 2),
                round((last_voice_w + 1) * window_ms, 2),
            )
        )
    return intervals


def analyze_dual_channel_wav(
    wav_path: str | Path,
    *,
    provider: str = "twilio",
    model: str = "gpt-4o-mini",
    region: str = "us-east-1",
    codec: str = "PCMU/8000",
    caller_channel: int = 0,
    agent_channel: int = 1,
    threshold_dbfs: float = -38.0,
    window_ms: float = 10.0,
    min_speech_ms: float = 80.0,
    min_silence_ms: float = 150.0,
    max_response_window_ms: float = 8000.0,
) -> dict[str, Any]:
    """Compute caller end-of-speech -> first agent audio onset at the CALLER leg."""
    path = Path(wav_path)
    with wave.open(str(path), "rb") as wf:
        channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        sample_rate = wf.getframerate()
        nframes = wf.getnframes()
        raw = wf.readframes(nframes)

    if channels < 2:
        raise ValueError(
            f"Dual-channel WAV required (channels >= 2) to separate caller and agent legs; got channels={channels}"
        )

    caller_pcm = _extract_channel_pcm16(
        raw, channels=channels, sample_width=sampwidth, channel_index=caller_channel
    )
    agent_pcm = _extract_channel_pcm16(
        raw, channels=channels, sample_width=sampwidth, channel_index=agent_channel
    )

    threshold_rms = _dbfs_to_pcm16_rms(threshold_dbfs)
    caller_utts = _detect_speech_intervals_ms(
        caller_pcm,
        sample_rate=sample_rate,
        window_ms=window_ms,
        threshold_rms=threshold_rms,
        min_speech_ms=min_speech_ms,
        min_silence_ms=min_silence_ms,
    )

    win_samples = max(1, int(round(sample_rate * (window_ms / 1000.0))))
    num_agent_wins = len(agent_pcm) // win_samples
    agent_active = [
        _rms_pcm16(agent_pcm[w * win_samples : (w + 1) * win_samples]) >= threshold_rms
        for w in range(num_agent_wins)
    ]

    turns: list[dict[str, Any]] = []
    latencies_ms: list[float] = []

    for idx, (c_start_ms, c_end_ms) in enumerate(caller_utts):
        next_caller_start_ms = (
            caller_utts[idx + 1][0]
            if (idx + 1) < len(caller_utts)
            else (c_end_ms + max_response_window_ms)
        )
        search_limit_ms = min(next_caller_start_ms, c_end_ms + max_response_window_ms)
        start_w = max(0, int(math.ceil(c_end_ms / window_ms)))
        end_w = min(num_agent_wins, int(math.floor(search_limit_ms / window_ms)))

        onset_ms: float | None = None
        # Require at least 2 consecutive active windows (20 ms) so a single click isn't scored as speech
        for w in range(start_w, max(start_w, end_w - 1)):
            if agent_active[w] and agent_active[w + 1]:
                onset_ms = round(w * window_ms, 2)
                break

        if onset_ms is not None:
            turn_lat = round(onset_ms - c_end_ms, 2)
            latencies_ms.append(turn_lat)
            turns.append(
                {
                    "turn_index": len(turns) + 1,
                    "caller_speech_start_ms": c_start_ms,
                    "caller_speech_end_ms": c_end_ms,
                    "agent_first_audio_ms": onset_ms,
                    "caller_leg_latency_ms": turn_lat,
                }
            )

    duration_ms = round((nframes / sample_rate) * 1000.0, 2) if sample_rate else 0.0
    return {
        "schema": "voxdesk.pstn_caller_leg_latency/v1",
        "measured_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "provider_fakes": False,
        "measurement_leg": "caller",
        "provider": provider,
        "model": model,
        "region": region,
        "codec": codec,
        "recording_file": path.name,
        "sample_rate_hz": sample_rate,
        "channels": channels,
        "caller_channel": caller_channel,
        "agent_channel": agent_channel,
        "duration_ms": duration_ms,
        "caller_utterances_detected": len(caller_utts),
        "matched_turns": len(turns),
        "p50_ms": round(percentile(latencies_ms, 50), 2) if latencies_ms else None,
        "p95_ms": round(percentile(latencies_ms, 95), 2) if latencies_ms else None,
        "p99_ms": round(percentile(latencies_ms, 99), 2) if latencies_ms else None,
        "turns": turns,
    }


async def run_live_dual_channel_capture(
    *,
    calls: int,
    provider: str,
    model: str,
    region: str,
    codec: str,
    caller_channel: int = 0,
    agent_channel: int = 1,
) -> dict[str, Any]:
    missing = missing_live_pstn_env()
    if missing:
        raise RuntimeError(
            "Live PSTN capture requires explicit opt-in, VoxDesk API access, and carrier credentials; missing: "
            + ", ".join(missing)
        )
    if provider.strip().lower() != "twilio":
        raise RuntimeError("Live PSTN dual-channel capture currently supports only Twilio")

    sid = os.environ["TWILIO_ACCOUNT_SID"].strip()
    auth = (sid, os.environ["TWILIO_AUTH_TOKEN"].strip())
    from_num = os.environ["TWILIO_PHONE_NUMBER"].strip()
    to_num = os.environ["TWILIO_TEST_TO_NUMBER"].strip()
    public_base = os.environ["VOXDESK_PUBLIC_BASE_URL"].strip().rstrip("/")
    api_key = os.environ["VOXDESK_REAL_API_KEY"].strip()
    api_base = f"https://api.twilio.com/2010-04-01/Accounts/{sid}"

    all_turn_latencies: list[float] = []
    call_reports: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    async with (
        httpx.AsyncClient(timeout=30.0, auth=auth) as twilio_client,
        httpx.AsyncClient(timeout=45.0) as voxdesk_client,
    ):
        for idx in range(calls):
            call_sid = ""
            call_id = ""
            terminal = False
            recording_sids: list[str] = []
            try:
                call = await create_voxdesk_outbound_call(
                    voxdesk_client,
                    public_base_url=public_base,
                    api_key=api_key,
                    to_number=to_num,
                    from_number=from_num,
                    timeout_seconds=25,
                    idempotency_key=f"real-pstn-measure-{idx + 1}-{os.urandom(8).hex()}",
                )
                call_sid = call["call_sid"]
                call_id = call["id"]
                final_doc = await wait_for_twilio_terminal(
                    twilio_client,
                    api_base=api_base,
                    call_sid=call_sid,
                    timeout_seconds=150.0,
                    poll_seconds=2.0,
                )
                status = str(final_doc.get("status") or "").lower()
                terminal = True
                if status != "completed":
                    raise RuntimeError(f"PSTN call ended with status {status!r}")

                recordings = await wait_for_dual_channel_recordings(
                    twilio_client,
                    api_base=api_base,
                    call_sid=call_sid,
                )
                recording_sids = [str(row["sid"]) for row in recordings]
                recording_sid = recording_sids[0]
                wav_resp = await twilio_client.get(
                    f"{api_base}/Recordings/{recording_sid}.wav?RequestedChannels=2"
                )
                if wav_resp.status_code != 200:
                    raise RuntimeError(f"Twilio recording download returned HTTP {wav_resp.status_code}")

                with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as tmp:
                    tmp.write(wav_resp.content)
                    tmp.flush()
                    analysis = analyze_dual_channel_wav(
                        tmp.name,
                        provider=provider,
                        model=model,
                        region=region,
                        codec=codec,
                        caller_channel=caller_channel,
                        agent_channel=agent_channel,
                    )
                if not analysis["matched_turns"]:
                    raise RuntimeError("Recording contained no matched caller-to-agent speech turn")
                replay = await wait_for_voxdesk_transcript(
                    voxdesk_client,
                    public_base_url=public_base,
                    api_key=api_key,
                    call_id=call_id,
                )
                transcript_turns = [
                    event
                    for event in replay.get("timeline") or []
                    if isinstance(event, dict) and event.get("event") == "transcript_turn"
                ]
                analysis.update(
                    {
                        "call_index": idx + 1,
                        "call_id": call_id,
                        "call_sid": call_sid,
                        "call_status": status,
                        "transcript_turns": len(transcript_turns),
                    }
                )
                call_reports.append(analysis)
                all_turn_latencies.extend(
                    float(turn["caller_leg_latency_ms"]) for turn in analysis["turns"]
                )
            except Exception as exc:
                failures.append(
                    {"call_index": idx + 1, "call_id": call_id, "call_sid": call_sid, "reason": str(exc)}
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

    return {
        "schema": "voxdesk.pstn_caller_leg_latency/v1",
        "measured_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "provider_fakes": False,
        "measurement_leg": "caller",
        "provider": provider,
        "model": model,
        "region": region,
        "codec": codec,
        "channel_mapping": {
            "caller_channel": caller_channel,
            "agent_channel": agent_channel,
            "verified_by_live_calibration": False,
        },
        "calls_requested": calls,
        "calls_attempted": len(call_reports) + len(failures),
        "calls_completed": len(call_reports),
        "calls_failed": len(failures),
        "total_turns": len(all_turn_latencies),
        "p50_ms": round(percentile(all_turn_latencies, 50), 2) if all_turn_latencies else None,
        "p95_ms": round(percentile(all_turn_latencies, 95), 2) if all_turn_latencies else None,
        "p99_ms": round(percentile(all_turn_latencies, 99), 2) if all_turn_latencies else None,
        "failures": failures,
        "calls": call_reports,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wav", type=Path, help="Dual-channel PCM16 WAV recording to analyze.")
    parser.add_argument("--live", action="store_true", help="Place paid live Twilio calls through VoxDesk (requires explicit opt-in).")
    parser.add_argument("--calls", type=int, default=1, help="Number of controlled test calls when --live is set.")
    parser.add_argument("--provider", default=os.environ.get("VOXDESK_TELEPHONY_PROVIDER", "twilio"))
    parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"))
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    parser.add_argument("--codec", default="PCMU/8000")
    parser.add_argument("--caller-channel", type=int, default=0)
    parser.add_argument("--agent-channel", type=int, default=1)
    parser.add_argument("--threshold-dbfs", type=float, default=-38.0)
    parser.add_argument("--min-silence-ms", type=float, default=150.0)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)

    if args.wav is not None:
        report = analyze_dual_channel_wav(
            args.wav,
            provider=args.provider,
            model=args.model,
            region=args.region,
            codec=args.codec,
            caller_channel=args.caller_channel,
            agent_channel=args.agent_channel,
            threshold_dbfs=args.threshold_dbfs,
            min_silence_ms=args.min_silence_ms,
        )
    elif args.live:
        if args.calls < 1:
            parser.error("--calls must be at least 1 for a live study")
        try:
            report = asyncio.run(
                run_live_dual_channel_capture(
                    calls=args.calls,
                    provider=args.provider,
                    model=args.model,
                    region=args.region,
                    codec=args.codec,
                    caller_channel=args.caller_channel,
                    agent_channel=args.agent_channel,
                )
            )
        except (RuntimeError, TimeoutError) as exc:
            print(f"REFUSED/FAILED: {exc}", file=sys.stderr)
            return 2
    else:
        parser.error("Pass --wav <dual_channel.wav> or --live")
        return 2

    serialized = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    if args.live and (not report.get("calls_completed") or report.get("calls_failed")):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
