# VoxDesk — Real PSTN & Voice Pipeline Latency Benchmark

This document reports **only real external provider and PSTN caller-leg measurements** (`provider_fakes: false`). Synthetic in-process pipeline overhead tests are strictly excluded from this document and isolated in [`docs/LATENCY_BENCHMARK_MOCK.md`](LATENCY_BENCHMARK_MOCK.md).

---

## 1. Measurement Methodology (Caller-Leg Dual-Channel Recording + Server Telemetry)

Real conversational voice latency must be measured at the **caller leg** (from the last millisecond of caller speech to the first millisecond of audible agent reply arriving at the carrier bridge), joined with server-side stage timings:

1. **Dual-Channel Carrier Recording (`scripts/measure_pstn_latency.py`)**:
   - Outbound/inbound study calls are placed via Twilio Programmable Voice with `Record="true"` and `RecordingChannels="dual"` (`PCMU/8000` stereo WAV: `channel 0` = inbound caller leg, `channel 1` = outbound agent leg).
   - `analyze_dual_channel_wav()` computes short-time 10 ms RMS windows against a `-38.0 dBFS` speech threshold (`min_speech_ms = 80 ms`, `min_silence_ms = 150 ms`) to locate exact caller end-of-speech (`caller_speech_end_ms`) and the first sustained (`>= 20 ms`) agent speech onset (`agent_first_audio_ms`).
   - Caller-leg turn latency is `caller_leg_latency_ms = agent_first_audio_ms - caller_speech_end_ms`.
   - Every emitted JSON report records `provider`, `model`, `region`, `codec`, `provider_fakes: false`, per-turn timestamps, and `p50_ms` / `p95_ms` / `p99_ms`.
2. **N-Call Scripted PSTN Study (`scripts/run_real_call_study.py`)**:
   - Places `N` real calls via Twilio REST against a scripted callee (`TWILIO_TEST_TO_NUMBER`), downloads dual-channel WAVs, computes caller-leg latency, queries `/api/calls` for persisted `CallLatencyStat` per-stage breakdowns (`stt_final_ms`, `llm_ttft_ms`, `tts_ttfb_ms`, `pipeline_e2e_ms`) and barge-in interruption stop latency (`clean_stops_under_250ms`), and writes `evidence/latency/<date>/real_study.{json,csv}`.
3. **Per-Provider Streaming TTFB (`tests/real/test_stt_tts_llm_stream.py` & `tests/real/test_s2s_live.py`)**:
   - Measures direct wire TTFB/TTFT against Deepgram (`nova-2`), ElevenLabs (`eleven_turbo_v2_5`), Cartesia (`sonic-english`), OpenAI (`gpt-4o-mini` + `gpt-4o-realtime-preview`), Anthropic (`claude-3-5-haiku-latest`), and Gemini Live (`models/gemini-2.0-flash-exp`), writing `evidence/real-integrations/<date>/provider_latency.json`.

---

## 2. Current Real-Call Study Artifact (`evidence/latency/2026-11-07/real_study.json`)

| Field | Value |
| --- | --- |
| **Artifact Path** | `evidence/latency/2026-11-07/real_study.json`, `evidence/latency/2026-11-07/real_study.csv` |
| **Study Date (UTC)** | `2026-11-07` |
| **`provider_fakes`** | `false` |
| **Status in Offline CI / Sandbox** | `NOT_RUN_MISSING_CREDENTIALS` (`0` live carrier calls placed; zero credentials present in build sandbox) |
| **Configured Study Target Stack** | `twilio` (`PCMU/8000`, `us-east-1`) + `deepgram/nova-2` + `openai/gpt-4o-mini` + `elevenlabs/eleven_turbo_v2_5` |
| **Caller-Leg E2E (`p50 / p95 / p99`)** | `null / null / null` *(requires live `TWILIO_*`, `DEEPGRAM_API_KEY`, `OPENAI_API_KEY`, `ELEVENLABS_API_KEY`)* |
| **STT Final (`p50 / p95 / p99`)** | `null / null / null` |
| **LLM TTFT (`p50 / p95 / p99`)** | `null / null / null` |
| **TTS TTFB (`p50 / p95 / p99`)** | `null / null / null` |
| **Interruption Clean Stops (`<= 250 ms`)** | `0 / 0` (`NOT_RUN_MISSING_CREDENTIALS`) |

> **Honesty Policy**: Because live carrier and LLM API keys are not injected into the offline build environment, `real_study.json` records `null` percentiles rather than substituting synthetic numbers. See [`docs/EVIDENCE_STATUS.md`](EVIDENCE_STATUS.md) for the nightly real-provider integration status.

---

## 3. Reference Host Hardware (Recorded in `real_study.json`)

- **CPU**: `x86_64` container / VM (`Intel Xeon @ 2.20 GHz` in sandbox baseline; recommend `>= 4 vCPU` dedicated in `us-east-1` co-located near Twilio US-East media edge)
- **OS & Runtime**: `Linux 6.1`, Python `3.11+` / `3.13`, Pipecat `0.0.94`, Uvicorn (`uvloop`)
- **Telephony Codec**: `PCMU/8000` (`G.711 mu-law`, 20 ms RTP / WebSocket frames)

---

## 4. Reproducing a Live PSTN Study

```bash
export TWILIO_ACCOUNT_SID="AC..."
export TWILIO_AUTH_TOKEN="..."
export TWILIO_PHONE_NUMBER="+15550001111"
export TWILIO_TEST_TO_NUMBER="+15550002222"
export VOXDESK_PUBLIC_BASE_URL="https://voice.example.com"
export VOXDESK_REAL_API_KEY="vxd_live_..."
export DEEPGRAM_API_KEY="..."
export OPENAI_API_KEY="..."
export ELEVENLABS_API_KEY="..."

# 1. Run a 30-call dual-channel PSTN study and write real_study.{json,csv}
python scripts/run_real_call_study.py --calls 30 --date "$(date -u +%Y-%m-%d)"

# 2. Analyze an individual dual-channel carrier WAV directly
python scripts/measure_pstn_latency.py \
  --wav evidence/latency/$(date -u +%Y-%m-%d)/recordings/call_001_CAxxxx.wav \
  --provider twilio \
  --model gpt-4o-mini \
  --region us-east-1 \
  --codec PCMU/8000
```
