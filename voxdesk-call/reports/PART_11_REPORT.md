# PART 11 Execution & Honest Measurement Report (`2026-11-07` UTC)

This report documents every deliverable implemented in **PART 11**, what was measured directly on the repository host on **`2026-11-07` UTC**, and which live external provider runs recorded explicit `SKIPPED_NO_CREDENTIALS` / `NOT_RUN_MISSING_CREDENTIALS` statuses (`provider_fakes: false`) because paid carrier/AI secrets are not injected into the offline sandbox.

---

## 1. Feature Matrix Dual-Column Governance & `<= 7` Day Freshness Gate

- **`tests/truth/feature_manifest.yaml`**: Upgraded to schema `version: 2`, preserving all **41** existing feature IDs and pytest `evidence_tests` node IDs while adding `evidence_tier` (`provider_gated`, `in_process_e2e`, `local_service_e2e`, `compliance_attestation`) and `provider_evidence` mappings (`twilio`, `telnyx`, `deepgram`, `elevenlabs`, `cartesia`, `openai`, `anthropic`, `openai_realtime`, `gemini_live`, `salesforce`, `hubspot`, `webhook_receiver`, `sip_sbc`).
- **Fresh JUnit Feature Verification (`2026-11-07` UTC)**: Executed all manifest test files against live `redis-server` (`489 passed, 7 skipped, 0 failures, 0 errors` in `59.97s`), updating `evidence/junit/feature_evidence.xml` to `2026-11-07T05:49:00Z` (`0` days old `<= 7` day limit).
- **`scripts/generate_feature_matrix.py`**:
  - Renders two independent columns in `docs/SALES/FEATURE_MATRIX_VERIFIED.md`: **`Test status`** and **`Provider evidence`**.
  - Enforces a **`<= 7` day** freshness rule on both JUnit execution timestamps and real-provider ledgers.
  - Adds `--check` (exits `1` on drift or stale JUnit XML) and `--require-fresh-real-runs` (used in `.github/workflows/real-integrations.yml` so nightly CI turns red when real-provider evidence is missing, failed, or `> 7` days old).
  - Generates [`docs/EVIDENCE_STATUS.md`](../docs/EVIDENCE_STATUS.md).

---

## 2. Real-Provider Integration Suite (`tests/real/`) & Nightly CI (`2026-11-07` UTC)

Implemented 7 real external provider suites (`12` test cases total, all marked `@pytest.mark.real_provider`) under `tests/real/`:

| Provider Key | Test Module | Target Service / API | Run Date (UTC) | Sandbox Result (`provider_fakes: false`) |
| --- | --- | --- | --- | --- |
| `twilio` | `tests/real/test_twilio_inbound_outbound.py` | Twilio Programmable Voice (`RecordingChannels="dual"`, `/telephony/voice`, `/telephony/status`, cleanup) | `2026-11-07` | `SKIPPED_NO_CREDENTIALS` |
| `telnyx` | `tests/real/test_telnyx_live.py` | Telnyx Call Control v2 + bidirectional media WS (`/telephony/telnyx/ws`) | `2026-11-07` | `SKIPPED_NO_CREDENTIALS` |
| `deepgram` | `tests/real/test_stt_tts_llm_stream.py` | Deepgram `nova-2` STT TTFB | `2026-11-07` | `SKIPPED_NO_CREDENTIALS` |
| `elevenlabs` | `tests/real/test_stt_tts_llm_stream.py` | ElevenLabs `eleven_turbo_v2_5` streaming PCM16 TTFB | `2026-11-07` | `SKIPPED_NO_CREDENTIALS` |
| `cartesia` | `tests/real/test_stt_tts_llm_stream.py` | Cartesia `sonic-english` streaming PCM16 TTFB | `2026-11-07` | `SKIPPED_NO_CREDENTIALS` |
| `openai` | `tests/real/test_stt_tts_llm_stream.py` | OpenAI `gpt-4o-mini` SSE TTFT | `2026-11-07` | `SKIPPED_NO_CREDENTIALS` |
| `anthropic` | `tests/real/test_stt_tts_llm_stream.py` | Anthropic `claude-3-5-haiku-latest` SSE TTFT | `2026-11-07` | `SKIPPED_NO_CREDENTIALS` |
| `openai_realtime` | `tests/real/test_s2s_live.py` | OpenAI `gpt-4o-realtime-preview` WebSocket S2S round-trip | `2026-11-07` | `SKIPPED_NO_CREDENTIALS` |
| `gemini_live` | `tests/real/test_s2s_live.py` | Google `models/gemini-2.0-flash-exp` `BidiGenerateContent` S2S round-trip | `2026-11-07` | `SKIPPED_NO_CREDENTIALS` |
| `salesforce` | `tests/real/test_salesforce_sandbox.py` | Salesforce OAuth refresh token -> Contact, Task, Case upsert + cleanup | `2026-11-07` | `SKIPPED_NO_CREDENTIALS` |
| `hubspot` | `tests/real/test_hubspot_sandbox.py` | HubSpot CRM v3 Contact, Task, Ticket upsert + cleanup | `2026-11-07` | `SKIPPED_NO_CREDENTIALS` |
| `webhook_receiver` | `tests/real/test_webhook_receiver.py` | Signed HMAC-SHA256 webhook delivery (`app.webhooks.delivery.deliver`) | `2026-11-07` | `SKIPPED_NO_CREDENTIALS` |

- **Ledger Artifacts**: `evidence/real-integrations/2026-11-07/real_integrations.json` and `evidence/real-integrations/2026-11-07/junit.xml`.
- **Nightly Workflow**: `.github/workflows/real-integrations.yml` runs nightly (`15 3 * * *` UTC) and via `workflow_dispatch`, uploads evidence artifacts, regenerates `docs/EVIDENCE_STATUS.md`, and fails red if `> 7` days stale.

---

## 3. PSTN Dual-Channel Latency, Mock Relocation & Demo Release Assets

- **Caller-Leg Dual-Channel Analyzer (`scripts/measure_pstn_latency.py`)**: Computes caller end-of-speech -> first agent audio onset on `channel 0` vs `channel 1` of a stereo PCM16 WAV (`-38 dBFS` RMS windowing, `150 ms` trailing silence hold) and emits JSON with `provider`, `model`, `region`, `codec`, and `provider_fakes: false`. Verified against synthetic stereo ground-truth audio in `tests/truth/test_part11_evidence_and_bench.py`.
- **N-Call Study Runner (`scripts/run_real_call_study.py`)**: Places `N` Twilio dual-channel calls, joins `/api/calls` `CallLatencyStat` stage breakdowns and barge-in interruption outcomes, and writes `evidence/latency/2026-11-07/real_study.{json,csv}` (`status: NOT_RUN_MISSING_CREDENTIALS`, `provider_fakes: false` on `2026-11-07`).
- **Mock Isolation (`evidence/mock/` & `docs/LATENCY_BENCHMARK_MOCK.md`)**: Relocated `evidence/latency/2026-10-08/benchmark_200_calls.{json,csv}` to `evidence/mock/` (`provider_fakes: true`, `not_product_performance: true`), referenced solely by `docs/LATENCY_BENCHMARK_MOCK.md` under a **"NOT PRODUCT PERFORMANCE"** banner. `docs/LATENCY_BENCHMARK.md` contains zero mock numbers.
- **Demo Artifacts (`evidence/demo/`)**: Removed binary `.wav` files from git tracking (`01_inbound_call_audio.wav`, `03_web_call_audio.wav`), added full turn transcripts + GitHub Release asset metadata (`v1.0.0-demo-audio`) across all 8 JSON traces, and generated `evidence/demo/manifest.json` (`v2`) + `evidence/demo/SHA256SUMS`.

---

## 4. Multi-Vertical Workflow Benchmark (`bench/` — `2026-11-07` UTC)

Executed `python bench/run_bench.py --seed 20261107 --output bench/results/2026-11-07.json` (`self_reported: true`, `evaluation_mode: "deterministic_seeded_offline"`, `provider_fakes: true`, `noisy_channel_error_rate: 0.08`):

- **Total Scenarios**: `100` across `3` verticals (`healthcare_scheduling`: `34`, `ecommerce_support`: `33`, `financial_services`: `33`).
- **Overall Workflow Accuracy**: **`85.0%`** (`85 / 100`), **95% Wilson CI: `[76.72%, 90.69%]`**.
- **Exact Tool-Call & Argument Match**: **`96.0%`** (`96 / 100`); **Rubric Judge Pass**: **`89.0%`**.
- **By Vertical**:
  - `ecommerce_support`: `100.0%` (`33 / 33`, 95% CI `[89.57%, 100.0%]`)
  - `financial_services`: `87.88%` (`29 / 33`, 95% CI `[72.67%, 95.18%]`)
  - `healthcare_scheduling`: `67.65%` (`23 / 34`, 95% CI `[50.84%, 80.87%]`)

---

## 5. Capacity Model, Toxiproxy Chaos Stack & P11-10 WebRTC Decision

- **Measured Worker CPU & RSS (`2026-11-07` UTC on `2 vCPU Intel Xeon @ 2.60 GHz`, `1,982.8 MiB` RAM)**:
  - **Denoise ON (`NoisereduceFilter`)**: `176.17 ms` (`C=10`), `186.88 ms` (`C=20`), `184.92 ms` (`C=30`) CPU/call; incremental RSS `0.450 / 0.231 / 0.158 MiB/call`; safe knee **`20` calls/worker**.
  - **Denoise OFF (`--no-denoise`)**: `7.83 ms` (`C=10`), `8.17 ms` (`C=20`), `7.99 ms` (`C=30`) CPU/call (**`22.5×` lower CPU**); incremental RSS `0.303 / 0.157 / 0.109 MiB/call`; safe knee **`30` calls/worker**.
  - **Wholesale Cost per Concurrent Call**: `$2.962/hr` (`$0.04937/min`) with `Twilio + Deepgram Nova-2 + GPT-4o-mini + ElevenLabs Turbo v2.5`, or `$2.182/hr` (`$0.03637/min`) with `Cartesia Sonic`; worker compute is `$0.0042/hr` (denoise ON) vs `$0.0028/hr` (denoise OFF).
  - **`--real-providers`**: Added to `loadtest/voice_capacity_test.py` (`exit 2` when live keys absent; fake mode remains default for CI).
- **Toxiproxy Chaos Stack**: Added `infra/chaos/docker-compose.toxiproxy.yml` (`stt_deepgram`, `llm_openai`, `tts_elevenlabs`) and opt-in `@pytest.mark.docker` `test_toxiproxy_stack_stage_stt_latency_and_llm_reset_failover` in `tests/resilience/test_chaos_calls.py`.
- **P11-10 WebRTC Decision (`app/telephony/web_transport.py`)**: Confirmed `ws-protobuf` (`/telephony/web/ws`) as the canonical browser transport in the Python API image (with UDP DTLS-SRTP SFU handled by `services/realtime/media-engine-rs`) and aligned `/telephony/web/offer` docstrings with its `503 UNSUPPORTED_CAPABILITY` (`fallback_transport: "ws-protobuf"`) response when optional `aiortc` is absent.
