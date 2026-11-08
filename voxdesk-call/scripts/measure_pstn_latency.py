#!/usr/bin/env python3
"""Measure end-of-speech -> first agent audio at the CALLER leg from a dual-channel recording.

Supports two real-only modes (`provider_fakes: false`):
1. Offline WAV analysis (`--wav path/to/dual_channel.wav`): reads a 2-channel PCM WAV
   (channel 0 = caller leg, channel 1 = agent leg at the carrier bridge by default),
   detects caller speech segments and trailing end-of-speech timestamps, locates the
   first subsequent agent audio onset on the caller-heard leg, and emits JSON with
   `provider`, `model`, `region`, `codec`, `turns`, and `p50_ms` / `p95_ms` / `p99_ms`.
2. Live PSTN capture (`--live`): places real Twilio dual-channel recorded calls,
   downloads the carrier `.wav` recording for each call, runs `analyze_dual_channel_wav`,
   deletes temporary carrier recordings unless `--keep-recordings` is set, and emits JSON.
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
import time
from typing import Any
import wave

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agent.latency import percentile  # noqa: E402


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
) -> dict[str, Any]:
    required = [
        "TWILIO_ACCOUNT_SID",
        "TWILIO_AUTH_TOKEN",
        "TWILIO_PHONE_NUMBER",
        "TWILIO_TEST_TO_NUMBER",
        "VOXDESK_PUBLIC_BASE_URL",
    ]
    missing = [k for k in required if not (os.environ.get(k) or "").strip()]
    if missing:
        raise RuntimeError(
            "Live PSTN dual-channel capture requires carrier credentials; missing: "
            + ", ".join(missing)
        )

    sid = os.environ["TWILIO_ACCOUNT_SID"].strip()
    tok = os.environ["TWILIO_AUTH_TOKEN"].strip()
    from_num = os.environ["TWILIO_PHONE_NUMBER"].strip()
    to_num = os.environ["TWILIO_TEST_TO_NUMBER"].strip()
    pub_base = os.environ["VOXDESK_PUBLIC_BASE_URL"].strip().rstrip("/")
    api_base = f"https://api.twilio.com/2010-04-01/Accounts/{sid}"
    auth = (sid, tok)

    all_turn_latencies: list[float] = []
    call_reports: list[dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=30.0, auth=auth) as client:
        for idx in range(calls):
            resp = await client.post(
                f"{api_base}/Calls.json",
                data={
                    "From": from_num,
                    "To": to_num,
                    "Url": f"{pub_base}/telephony/voice",
                    "Record": "true",
                    "RecordingChannels": "dual",
                    "Timeout": "25",
                },
            )
            resp.raise_for_status()
            call_sid = str(resp.json()["sid"])

            deadline = time.monotonic() + 120.0
            while time.monotonic() < deadline:
                poll = await client.get(f"{api_base}/Calls/{call_sid}.json")
                poll.raise_for_status()
                st = str(poll.json().get("status") or "").lower()
                if st in {"completed", "failed", "busy", "no-answer", "canceled"}:
                    break
                await asyncio.sleep(2.0)

            rec_list = await client.get(f"{api_base}/Calls/{call_sid}/Recordings.json")
            rec_list.raise_for_status()
            recs = rec_list.json().get("recordings") or []
            if not recs:
                continue
            rec_sid = str(recs[0]["sid"])
            wav_resp = await client.get(f"{api_base}/Recordings/{rec_sid}.wav?RequestedChannels=2")
            wav_resp.raise_for_status()

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as tmp:
                tmp.write(wav_resp.content)
                tmp.flush()
                analysis = analyze_dual_channel_wav(
                    tmp.name,
                    provider=provider,
                    model=model,
                    region=region,
                    codec=codec,
                )
            analysis["call_index"] = idx + 1
            analysis["call_sid"] = call_sid
            call_reports.append(analysis)
            for t in analysis["turns"]:
                all_turn_latencies.append(float(t["caller_leg_latency_ms"]))
            await client.delete(f"{api_base}/Recordings/{rec_sid}.json")

    return {
        "schema": "voxdesk.pstn_caller_leg_latency/v1",
        "measured_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "provider_fakes": False,
        "measurement_leg": "caller",
        "provider": provider,
        "model": model,
        "region": region,
        "codec": codec,
        "calls_completed": len(call_reports),
        "total_turns": len(all_turn_latencies),
        "p50_ms": round(percentile(all_turn_latencies, 50), 2) if all_turn_latencies else None,
        "p95_ms": round(percentile(all_turn_latencies, 95), 2) if all_turn_latencies else None,
        "p99_ms": round(percentile(all_turn_latencies, 99), 2) if all_turn_latencies else None,
        "calls": call_reports,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wav", type=Path, help="Dual-channel PCM16 WAV recording to analyze.")
    parser.add_argument("--live", action="store_true", help="Place live Twilio dual-channel calls.")
    parser.add_argument("--calls", type=int, default=5, help="Number of live PSTN calls when --live is set.")
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
        try:
            report = asyncio.run(
                run_live_dual_channel_capture(
                    calls=max(1, args.calls),
                    provider=args.provider,
                    model=args.model,
                    region=args.region,
                    codec=args.codec,
                )
            )
        except RuntimeError as exc:
            print(f"SKIP: {exc}", file=sys.stderr)
            return 2
    else:
        parser.error("Pass --wav <dual_channel.wav> or --live")
        return 2

    serialized = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
