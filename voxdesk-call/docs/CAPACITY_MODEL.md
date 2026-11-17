# VoxDesk — Single-Worker Voice Capacity & Unit Economics

> **What these numbers are.** CPU time and resident memory measured on the real
> `/telephony/ws` media path (HMAC stream token, G.711 mu-law decode/encode,
> `NoisereduceFilter`, `LatencyObserver`, PostgreSQL persistence) while external
> STT/LLM/TTS calls were served by deterministic local fakes
> (`provider_fakes: true`). They are a **valid floor for worker compute cost** and a
> **valid comparison of denoise ON vs OFF**. They are **not** product latency: the
> end-to-end percentiles below exclude every provider network leg, so do not quote
> them as VoxDesk call latency. Real-provider latency lives in
> `docs/LATENCY_BENCHMARK.md` and requires owner provider keys (PART 11).

---

## 1. Testbed & Execution Modes

- **Measured host (`2026-11-10T04:23Z`)**: `2` logical vCPU (`Intel(R) Xeon(R) Processor @ 2.60GHz`),
  `1,982.8 MiB` RAM, Python `3.13.16`, Pipecat `0.0.94`, PostgreSQL `17` (local cluster, real
  migrations to head `0091_call_debug_pcap_metadata`), Uvicorn single worker, no `--reload`.
- **Mode 1 — default (CI) mode, `provider_fakes: true`**: exercises the real WebSocket media
  path and all first-party DSP with deterministic fakes for Deepgram STT, OpenAI LLM and
  ElevenLabs TTS. This is what the tables in §2 report. Artifacts:
  [`evidence/capacity/voice_capacity_denoise_on.json`](../evidence/capacity/voice_capacity_denoise_on.json) and
  [`evidence/capacity/voice_capacity_denoise_off.json`](../evidence/capacity/voice_capacity_denoise_off.json),
  both stamped `"not_product_performance": true` and `"evidence_tier": "LIVE_FAKE"`.
- **Mode 2 — `--real-providers`, `provider_fakes: false`**: same ramp with live Deepgram
  `nova-2`, OpenAI `gpt-4o-mini` and ElevenLabs `eleven_turbo_v2_5` I/O. The script refuses to
  start (`exit 2`) unless `DEEPGRAM_API_KEY`, `OPENAI_API_KEY` and `ELEVENLABS_API_KEY` are set
  — assertion `test_capacity_real_providers_gate_and_cost_estimator` pins that behaviour.
  **Mode 2 has not been run**: it needs paid keys. No real-provider capacity figure exists in
  this repository today, and none is claimed.
- **`evidence/capacity/voice_capacity_ramp*.json`** are an earlier run of the same script
  (`2026-10-08`, also `provider_fakes: true`) kept for trend comparison only.

---

## 2. Measured CPU & Memory per Call at 10 / 20 / 30 Concurrent Calls

Three turns per call, four 20 ms mu-law chunks per turn, one ramp per mode.

### A. Spectral denoise ENABLED — `voice_capacity_denoise_on.json`

Worker baseline RSS after warm-up: `337.00 MiB`.

| Concurrent calls | Batch CPU (s) | CPU per call | Wall (s) | RSS after (MiB) | RSS delta | RSS delta / call | e2e p50 | e2e p95 | e2e p99 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| **10** | `2.9713` | **`297.13 ms`** | `2.9788` | `341.31` | `+4.31 MiB` | `0.431 MiB` | `273.4 ms` | `298.8 ms` | `302.2 ms` |
| **20** | `5.9252` | **`296.26 ms`** | `5.9425` | `341.44` | `+4.44 MiB` | `0.222 MiB` | `283.3 ms` | `315.6 ms` | `317.3 ms` |
| **30** | `8.9863` | **`299.54 ms`** | `9.0139` | `341.54` | `+4.54 MiB` | `0.151 MiB` | `285.3 ms` | `314.6 ms` | `322.7 ms` |

### B. Spectral denoise DISABLED — `voice_capacity_denoise_off.json`

Worker baseline RSS after warm-up: `334.86 MiB`.

| Concurrent calls | Batch CPU (s) | CPU per call | Wall (s) | RSS after (MiB) | RSS delta | RSS delta / call | e2e p50 | e2e p95 | e2e p99 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| **10** | `0.1253` | **`12.53 ms`** | `0.1313` | `337.90` | `+3.04 MiB` | `0.304 MiB` | `273.4 ms` | `298.8 ms` | `302.2 ms` |
| **20** | `0.2475` | **`12.38 ms`** | `0.2606` | `338.01` | `+3.15 MiB` | `0.158 MiB` | `283.3 ms` | `315.6 ms` | `317.3 ms` |
| **30** | `0.3725` | **`12.42 ms`** | `0.3920` | `338.13` | `+3.27 MiB` | `0.109 MiB` | `285.3 ms` | `314.6 ms` | `322.7 ms` |

### What the two tables actually say

1. **Denoise costs about 24× CPU.** `297.13 ms` vs `12.53 ms` per 3-turn call at 10 calls is a
   `23.7×` multiplier; the ratio is stable at 20 (`23.9×`) and 30 (`24.1×`). `NoisereduceFilter`
   is, by a wide margin, the largest per-call CPU consumer in the worker.
2. **Memory is flat, not linear.** RSS delta per call *falls* as concurrency rises
   (`0.431 → 0.222 → 0.151 MiB` with denoise on) because the audio pipeline's buffers are
   allocated per connection but the interpreter and model-free DSP state dominate the baseline.
   Roughly `0.1–0.45 MiB` of marginal RSS per concurrent call is the honest planning number.
3. **The e2e percentiles in both tables are identical, and that is a red flag worth stating
   clearly.** With deterministic fakes the observed latency is dominated by a fixed fake
   provider delay, so the percentiles measure the harness, not the product. They are recorded
   because the script emits them, not because they say anything about VoxDesk latency. Only
   mode 2 (§1) can produce a meaningful figure, and mode 2 has not run.
4. **The ramp completed all three steps inside the SLO**, so the script's knee detector reports
   `knee_concurrent_calls: 30` and recommends `24` calls per 2-vCPU worker for HPA
   (`knee_point` in both JSONs). That is a *lower* bound: the fake path is cheaper than the
   real one, so real-provider capacity is likely lower, not higher.
5. **Not measured**, and therefore not claimed anywhere in this repository: per-call TLS/HTTP2
   buffer growth, per-turn wall-clock time under real provider I/O, and any denoise-vs-STT
   accuracy trade-off.

---

## 3. Wholesale Cost per Concurrent Call (model, not measurement)

Computed by `estimate_cost_per_concurrent_call()` in `loadtest/voice_capacity_test.py` from
published list prices, assuming a continuous call (50 % caller speech / 50 % agent speech) on an
on-demand `c6i.large` (`2 vCPU, 4 GiB`, `$0.084/hr`). Asserted by
`test_capacity_real_providers_gate_and_cost_estimator`; the safe-concurrency inputs are the
`20` (denoise on) and `30` (denoise off) figures above, i.e. **mock-path** values, so treat the
compute line as a floor rather than a promise.

| Cost component | Rate basis | Per call-minute (USD) | Per concurrent call-hour (USD) |
| --- | --- | ---: | ---: |
| **Twilio PSTN + Media Streams** | `$0.0140/min` voice + `$0.0040/min` bidirectional WS | `$0.01800` | `$1.080` |
| **Deepgram `nova-2` streaming STT** | streaming pay-as-you-go | `$0.00430` | `$0.258` |
| **OpenAI `gpt-4o-mini` LLM** | `~600` in + `~180` out tokens/min | `$0.00300` | `$0.180` |
| **TTS option A — ElevenLabs `eleven_turbo_v2_5`** | `~400` chars/min (Scale tier) | `$0.02400` | `$1.440` |
| **TTS option B — Cartesia `sonic-english`** | streaming PCM16 | `$0.01100` | `$0.660` |
| **Worker compute (`denoise ON`, `20` calls/worker)** | `$0.084/hr ÷ 20` | `$0.00007` | `$0.0042` |
| **Worker compute (`denoise OFF`, `30` calls/worker)** | `$0.084/hr ÷ 30` | `$0.00005` | `$0.0028` |
| **Total stack A (ElevenLabs)** | all-in wholesale | **`$0.04937` / `$0.04935`** | **`$2.962` / `$2.961`** |
| **Total stack B (Cartesia)** | all-in wholesale | **`$0.03637` / `$0.03635`** | **`$2.182` / `$2.181`** |

Provider prices are vendor list prices at the time of writing and change without notice; the
worker compute line is `0.14 %`–`0.22 %` of the total, so this model is a **provider-pricing**
model, not a capacity model.

---

## 4. Reproduction Commands

```bash
# 0. Boot the real stack the ramp talks to (SQLite cannot run these migrations)
export DATABASE_URL="postgresql+asyncpg://USER@/DBNAME?host=/tmp&port=5433"
alembic upgrade head
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 &

# 1. Worker DSP & framing ramp (denoise ON then OFF at 10, 20, 30 calls)
python loadtest/voice_capacity_test.py --host http://127.0.0.1:8000 --steps 10,20,30 \
  --output evidence/capacity/voice_capacity_denoise_on.json
python loadtest/voice_capacity_test.py --host http://127.0.0.1:8000 --steps 10,20,30 --no-denoise \
  --output evidence/capacity/voice_capacity_denoise_off.json

# 2. Live real-provider ramp (needs DEEPGRAM_API_KEY, OPENAI_API_KEY, ELEVENLABS_API_KEY;
#    exits 2 without them — this is the command that would make the latency columns real)
python loadtest/voice_capacity_test.py --host http://127.0.0.1:8000 --steps 10,20,30 \
  --real-providers --output evidence/capacity/voice_capacity_real_$(date -u +%Y-%m-%d).json
```
