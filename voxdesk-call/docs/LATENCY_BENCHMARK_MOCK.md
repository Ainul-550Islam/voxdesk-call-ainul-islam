# VoxDesk — In-Process Mock Pipeline Harness Numbers (NOT PRODUCT PERFORMANCE)

> **WARNING — NOT PRODUCT PERFORMANCE**:
> Every figure in this document comes from `scripts/bench_latency.py --mock` running **in-process with deterministic synthetic provider delays** (`provider_fakes: true`) and **zero real PSTN, STT, LLM, or TTS network traffic**.
> **Never cite these numbers in buyer decks, SLAs, marketplace listings, or product latency claims.** For real carrier-leg PSTN latency methodology and `provider_fakes: false` study artifacts, see [`docs/LATENCY_BENCHMARK.md`](LATENCY_BENCHMARK.md).

---

## 1. Relocated Mock Artifacts (`evidence/mock/`)

To prevent synthetic CI regression checks from being mistaken for live telephony performance, the October 8, 2026 200-call synthetic harness artifacts live exclusively under `evidence/mock/`:

- `evidence/mock/benchmark_200_calls.json` (`"provider_fakes": true`, `"not_product_performance": true`)
- `evidence/mock/benchmark_200_calls.csv`

---

## 2. What the Mock Harness Actually Exercises

- `TwilioFrameSerializer` G.711 `PCMU` (`audio/x-mulaw` 8 kHz) base64 decode & encode framing
- Pipecat `0.0.94` async `Pipeline` frame scheduling (`UserStartedSpeakingFrame` -> `UserStoppedSpeakingFrame` -> `TranscriptionFrame` -> `LLMFullResponseStartFrame` -> `TTSStartedFrame` -> `OutputAudioRawFrame`)
- `LatencyObserver` (`app/agent/latency.py`) stage timestamp capture and percentile math (`p50`, `p95`, `p99`)
- Barge-in cancellation scheduling overhead inside a single Python event loop

---

## 3. Historical Synthetic Harness Output (`200` Calls × `5` Turns = `1,000` Turns, `provider_fakes: true`)

*Host: 2 vCPU Intel Xeon @ 2.20 GHz, 2 GB RAM, Linux 6.1, Python 3.13, concurrency = 20, seed = 42.*

| Stage (Synthetic Delay + Local Framing Overhead) | `p50` (ms) | `p95` (ms) | `p99` (ms) |
| --- | ---: | ---: | ---: |
| Synthetic STT Finalization (`stt_ms`) | `69.4` | `78.5` | `81.3` |
| Synthetic LLM First Token (`llm_ttft_ms`) | `124.2` | `146.9` | `153.8` |
| Synthetic TTS First Audio (`tts_ttfb_ms`) | `91.1` | `108.4` | `113.7` |
| Total Synthetic Pipeline (`e2e_ms`) | `285.3` | `318.7` | `329.4` |
| Event-Loop Barge-In Cancellation (`interruption_stop_ms`) | `1.1` | `2.4` | `3.8` |

---

## 4. Reproducing the Local Regression Check

```bash
python scripts/bench_latency.py --calls 200 --turns 5 --concurrency 20 --mock --write-evidence
```

Artifacts are written to `evidence/mock/benchmark_200_calls.{json,csv}`.
