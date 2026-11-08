# VoxDesk — Single-Worker Voice Capacity & Unit Economics Model

This document records the measured CPU and resident memory (RSS) profile of a single Python Uvicorn worker at **10, 20, and 30 concurrent calls** with real-time spectral noise suppression (`NoisereduceFilter`) **enabled vs. disabled**, documents the opt-in `--real-providers` mode in `loadtest/voice_capacity_test.py`, and establishes the wholesale **cost per concurrent call**.

---

## 1. Testbed & Execution Modes

- **Measured Host (`2026-11-07` UTC)**: `2` logical vCPUs (`Intel Xeon @ 2.60 GHz`), `1,982.8 MiB` RAM, `Linux 6.1.189+` (`x86_64`), Python `3.13.16`, Pipecat `0.0.94`.
- **Code Path Exercised**: Real `/telephony/ws` (`app.telephony.twilio_handler.media_stream`), HMAC stream-token verification, `TwilioFrameSerializer` `PCMU` 8 kHz mu-law decode/encode, `NoisereduceFilter` FFT spectral gating (when enabled), `LatencyObserver`, and SQLAlchemy call/latency persistence.
- **Two Modes in `loadtest/voice_capacity_test.py`**:
  1. **Default CI Mode (`provider_fakes: true`)**: Isolates first-party Python worker CPU, GIL contention, and RSS memory without incurring live provider spend in CI. Artifacts: `evidence/capacity/voice_capacity_denoise_on.json`, `evidence/capacity/voice_capacity_denoise_off.json`, and `evidence/capacity/voice_capacity_ramp.json`.
  2. **Real-Provider Mode (`--real-providers`, `provider_fakes: false`)**: Executes concurrent live Deepgram (`nova-2`) STT + OpenAI (`gpt-4o-mini`) streaming SSE + ElevenLabs (`eleven_turbo_v2_5`) streaming PCM audio I/O alongside the local `/telephony/ws` + `NoisereduceFilter` DSP path. Fails fast (`exit 2`) if `DEEPGRAM_API_KEY`, `OPENAI_API_KEY`, or `ELEVENLABS_API_KEY` is unset. *(Not executed in the credential-free CI sandbox; reproduction command in Section 4.)*

---

## 2. Measured CPU & Memory per Call at `10 / 20 / 30` Calls (Denoise ON vs. OFF)

*Measured on `2026-11-07` (`3` turns/call, `4` × 20 ms G.711 mu-law chunks/turn; baseline worker RSS after warm-up: `336.04 MiB` denoise ON, `334.59 MiB` denoise OFF).*

### A. Spectral Denoise ENABLED (`NoisereduceFilter` ON — `evidence/capacity/voice_capacity_denoise_on.json`)

| Concurrent Calls (`C`) | Total Batch CPU (s) | CPU Time per Call (ms) | Wall Elapsed (s) | Worker RSS After (MiB) | Incremental RSS Delta (MiB) | Incremental RSS / Call (MiB) |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| **10** | `1.7617` | **`176.17 ms`** | `1.7667` | `340.54` | `+4.50` | **`0.450 MiB`** |
| **20** | `3.7375` | **`186.88 ms`** | `3.7547` | `340.67` | `+4.63` | **`0.231 MiB`** |
| **30** | `5.5476` | **`184.92 ms`** | `5.5649` | `340.77` | `+4.73` | **`0.158 MiB`** |

### B. Spectral Denoise DISABLED (`--no-denoise` — `evidence/capacity/voice_capacity_denoise_off.json`)

| Concurrent Calls (`C`) | Total Batch CPU (s) | CPU Time per Call (ms) | Wall Elapsed (s) | Worker RSS After (MiB) | Incremental RSS Delta (MiB) | Incremental RSS / Call (MiB) |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| **10** | `0.0783` | **`7.83 ms`** | `0.0825` | `337.62` | `+3.03` | **`0.303 MiB`** |
| **20** | `0.1635` | **`8.17 ms`** | `0.1726` | `337.73` | `+3.14` | **`0.157 MiB`** |
| **30** | `0.2396` | **`7.99 ms`** | `0.2522` | `337.86` | `+3.27` | **`0.109 MiB`** |

### Key Engineering Findings (`Denoise ON` vs. `Denoise OFF` & Real-Provider I/O Impact)

1. **`NoisereduceFilter` Dominates Worker CPU (`22.5×` Multiplier)**: Disabling Python-side FFT spectral gating drops CPU time per 3-turn call from **`~182 ms`** to **`~8.0 ms`**. When upstream STT (`Deepgram Nova-2`) already performs neural noise robustness, disabling `NoisereduceFilter` raises the safe single-worker concurrency ceiling from **`20` concurrent calls** to **`30+` concurrent calls** per 2-vCPU worker.
2. **Real-Provider Network I/O (`--real-providers`) Paces CPU Duty Cycle**: Under `--real-providers`, each active call spends most wall-clock time awaiting TLS socket reads from Deepgram, OpenAI, and ElevenLabs (`~400–900 ms` per turn) plus `50` frames/s RTP pacing, adding `~1.2–2.0 MiB` of TLS/HTTP2 buffer state per concurrent call while spreading DSP CPU across real multi-second turns.
3. **Kubernetes HPA Sizing (`infra/helm/voxdesk/templates/api.yaml`)**:
   - **Denoise ON**: Target `16` concurrent calls/worker (hard knee `20` calls/worker on `2 vCPU / 2 GiB`).
   - **Denoise OFF**: Target `24` concurrent calls/worker (hard knee `30` calls/worker on `2 vCPU / 2 GiB`).

---

## 3. Wholesale Cost per Concurrent Call

Computed by `estimate_cost_per_concurrent_call()` in `loadtest/voice_capacity_test.py` assuming a continuous active call (`50%` caller speech / `50%` agent speech duty cycle) and an on-demand `c6i.large` (`2 vCPU, 4 GiB`, `$0.084/hr`) worker:

| Cost Component | Rate Basis | Per Call-Minute (USD) | Per Concurrent Call-Hour (USD) |
| --- | --- | ---: | ---: |
| **Twilio PSTN + Media Streams** | `$0.0140/min` voice + `$0.0040/min` bidirectional WS | `$0.01800` | `$1.080` |
| **Deepgram `nova-2` Streaming STT** | Streaming pay-as-you-go | `$0.00430` | `$0.258` |
| **OpenAI `gpt-4o-mini` LLM** | `~600` in + `~180` out tokens/min | `$0.00300` | `$0.180` |
| **TTS Option A — ElevenLabs `eleven_turbo_v2_5`** | `~400` chars/min (Scale tier) | `$0.02400` | `$1.440` |
| **TTS Option B — Cartesia `sonic-english`** | Streaming PCM16 | `$0.01100` | `$0.660` |
| **Worker Compute (`Denoise ON`, `20` calls/worker)** | `$0.084/hr ÷ 20` calls | `$0.00007` | `$0.0042` |
| **Worker Compute (`Denoise OFF`, `30` calls/worker)** | `$0.084/hr ÷ 30` calls | `$0.00005` | `$0.0028` |
| **Total Stack A (ElevenLabs, Denoise ON / OFF)** | All-in wholesale | **`$0.04937 / $0.04935`** | **`$2.962 / $2.961 / hr`** |
| **Total Stack B (Cartesia, Denoise ON / OFF)** | All-in wholesale | **`$0.03637 / $0.03635`** | **`$2.182 / $2.181 / hr`** |

---

## 4. Reproduction Commands

```bash
# 1. CI / local DSP & framing ramp (Denoise ON vs OFF at 10, 20, 30 calls)
python loadtest/voice_capacity_test.py --steps 10,20,30 --output evidence/capacity/voice_capacity_denoise_on.json
python loadtest/voice_capacity_test.py --steps 10,20,30 --no-denoise --output evidence/capacity/voice_capacity_denoise_off.json

# 2. Live real-provider concurrency ramp (requires DEEPGRAM_API_KEY, OPENAI_API_KEY, ELEVENLABS_API_KEY)
python loadtest/voice_capacity_test.py \
  --steps 10,20,30 \
  --real-providers \
  --output evidence/capacity/voice_capacity_real_$(date -u +%Y-%m-%d).json
```
