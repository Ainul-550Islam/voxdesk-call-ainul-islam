# VoxDesk — Real PSTN & Voice-Pipeline Latency Evidence

This document reports only measurements derived from real external provider
traffic or real dual-channel carrier recordings (`provider_fakes: false`). Mock
pipeline timings and synthetic WAV results are isolated in
[`docs/LATENCY_BENCHMARK_MOCK.md`](LATENCY_BENCHMARK_MOCK.md). **No live PSTN
latency value is currently available; no value is inferred from a mock run.**

## Measurement method

`scripts/measure_pstn_latency.py` analyzes a dual-channel PCM16 WAV. By default,
channel 0 is caller audio and channel 1 is agent audio. It uses short-time 10 ms
RMS windows with a `-38.0 dBFS` threshold, at least 80 ms speech, and 150 ms
silence to detect caller end-of-speech. It then finds the first sustained
(20 ms) agent-audio onset and computes
`caller_leg_latency_ms = agent_first_audio_ms - caller_speech_end_ms`.
The output includes the measurement leg, provider, model, region, codec, sample
rate, channel indices, detected turns, and p50/p95/p99 where observations exist.
The live runners default to channel 0 = caller and channel 1 = agent, with
`--caller-channel`/`--agent-channel` overrides; they record this map as
`verified_by_live_calibration: false`. A two-channel recording proves channel
count, not which side each channel contains. Until a controlled live calibration
confirms the direction for the deployed Twilio path, treat the resulting
caller-leg percentiles as provisional rather than validated product evidence.

The offline analysis is a signal-processing utility. Its synthetic stereo
fixture establishes that the detector returns a known injected offset; it does
not establish real PSTN, provider, media-stream, or model performance.

## N-call real PSTN study

`scripts/run_real_call_study.py` is the live-only runner. For each requested
call, it:

1. requires `VOXDESK_REAL_INTEGRATION=1`, a VoxDesk API key, Twilio credentials,
   provider test credentials, and an explicitly controlled destination;
2. creates the call through VoxDesk's authenticated `/api/calls` endpoint, so a
   durable `Call` row and signed outbound media URL exist;
3. waits for a completed Twilio call and a two-channel recording;
4. requires caller and assistant transcript turns through the authenticated
   replay endpoint, analyzes the recording, and writes per-call / aggregate
   caller-leg results to `real_study.json` and `real_study.csv`;
5. deletes the Twilio recording after analysis and does not retain raw WAV audio
   in the evidence bundle.

Per-stage STT/LLM/TTS TTFB and interruption-stop metrics are explicitly marked
`measured: false`/null in this `/api/calls`-based study. The call and replay
responses it consumes do not include per-call stage timings or interruption stop
latency; the separate operator live-proof flow is not joined to these call IDs.
No stage latency or interruption result is fabricated or inferred from an
aggregate dashboard response.

## Current evidence state

- `evidence/latency/` contains the instructions only; there is no `real_study.json`
  or `.csv` artifact.
- The repository's dated provider ledger at
  `evidence/real-integrations/2026-11-10/real_integrations.json` records 0 PASS
  and 12 `SKIPPED_NO_CREDENTIALS` results. A dated skip ledger is not provider
  evidence.
- No live provider call or N-call PSTN study was run to prepare this document.
- Caller-leg p50/p95/p99, per-stage TTFB, interruptions, and capacity under live
  provider conditions are therefore **not measured**.

The automated proof that exists is limited to offline behavior: the unit test
`tests/truth/test_part11_evidence_and_bench.py::test_measure_pstn_latency_dual_channel_caller_leg_onset`
checks a synthetic stereo fixture with a known 420 ms offset. This is not a
product-performance result.

## Running an authorized study

The scripts default to one call, but this N-call runner places paid calls. Use only
a consented test callee/IVR under your control. Increase `--calls` only when the
additional spend and side effects are authorized. The remote VoxDesk deployment
must itself be configured with the required carrier/STT/LLM/TTS providers.

```bash
export VOXDESK_REAL_INTEGRATION=1
export VOXDESK_REAL_API_KEY="vxd_test_..."
export VOXDESK_PUBLIC_BASE_URL="https://voice-test.example.com"
export TWILIO_ACCOUNT_SID="AC..."
export TWILIO_AUTH_TOKEN="..."
export TWILIO_PHONE_NUMBER="+15550001111"
export TWILIO_TEST_TO_NUMBER="+15550002222"  # controlled, consented test callee
export DEEPGRAM_API_KEY="..."
export OPENAI_API_KEY="..."
export ELEVENLABS_API_KEY="..."

python scripts/run_real_call_study.py \
  --calls 5 \
  --date "$(date -u +%Y-%m-%d)"
```

The manual `real-integrations` workflow also accepts a `pstn_study_calls`
input (default `0`). A positive count explicitly requests that additional paid
study and uploads its JSON/CSV artifacts. The nightly schedule does not run the
extra N-call study. See [`evidence/latency/README.md`](../evidence/latency/README.md)
for the artifact location and privacy notes.
