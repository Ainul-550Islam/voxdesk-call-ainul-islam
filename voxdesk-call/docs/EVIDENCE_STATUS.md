# VoxDesk — Real External Provider Evidence Status

Generated automatically by `scripts/generate_feature_matrix.py` from the
nightly real-provider integration ledger (`tests/real/`). Do not edit by hand.

- **Latest Run Date (UTC)**: `2026-11-07`
- **Freshness Status**: NO PROVIDER RAN (`14` entries, `0` PASS, all skipped for missing credentials; run date `2026-11-07`)
- **Reference Date Used**: `2026-11-18` (the age above is measured to this date, not to the day you are reading this file)
- **`provider_fakes`**: `false` (enforced across every `tests/real/` suite)
- **Evidence JSON**: [`evidence/real-integrations/2026-11-07/real_integrations.json`](../evidence/real-integrations/2026-11-07/real_integrations.json)
- **JUnit XML**: [`evidence/real-integrations/2026-11-07/junit.xml`](../evidence/real-integrations/2026-11-07/junit.xml)

## Evidence tier legend

| Tier | What it certifies |
| --- | --- |
| `LIVE_PROVIDER` | A real third-party provider served the traffic, and the ledger proving it is `<= 7` days old with `provider_fakes: false` and every provider `PASS`. |
| `LIVE_FAKE` | The real application code path ran end to end, but STT/LLM/TTS/carrier calls were served by deterministic fakes. Proves wiring and orchestration, **not** provider behaviour or speech quality. |
| `API_ONLY` | The capability exists behind an HTTP/WebSocket API and is covered by tests, but no live-provider proof is on file. |
| `NOT_CONFIGURED` | The code path exists but needs an operator-supplied external system (for example an external SIP SBC) before any claim can be made. |
| `PLANNED` | Not built. Listed so buyers can see the roadmap explicitly rather than inferring it. |

A manifest feature declares the highest tier it *could* reach; the matrix grants a tier only from artifacts that exist and are fresh, and never grants above the row's test status.

## Per-Provider Integration Status

| Provider | Capability Stage | Last Run Date | Freshness | Result | Latency (ms) | Model / Suite | Evidence Artifact |
| --- | --- | --- | --- | --- | ---: | --- | --- |
| `twilio` | Telephony (PSTN + Media Streams) | `2026-11-07` | `NO RUN` | `SKIPPED_NO_CREDENTIALS` | — | `tests/real/test_twilio_inbound_outbound.py` | [`real_integrations.json`](../evidence/real-integrations/2026-11-07/real_integrations.json) |
| `telnyx` | Call Control v2 only; signed media WS/audio unverified | `2026-11-07` | `NO RUN` | `SKIPPED_NO_CREDENTIALS` | — | `tests/real/test_telnyx_live.py` | [`real_integrations.json`](../evidence/real-integrations/2026-11-07/real_integrations.json) |
| `deepgram` | Streaming STT | `2026-11-07` | `NO RUN` | `SKIPPED_NO_CREDENTIALS` | — | `tests/real/test_stt_tts_llm_stream.py` | [`real_integrations.json`](../evidence/real-integrations/2026-11-07/real_integrations.json) |
| `elevenlabs` | Streaming TTS | `2026-11-07` | `NO RUN` | `SKIPPED_NO_CREDENTIALS` | — | `tests/real/test_stt_tts_llm_stream.py` | [`real_integrations.json`](../evidence/real-integrations/2026-11-07/real_integrations.json) |
| `cartesia` | Streaming TTS | `2026-11-07` | `NO RUN` | `SKIPPED_NO_CREDENTIALS` | — | `tests/real/test_stt_tts_llm_stream.py` | [`real_integrations.json`](../evidence/real-integrations/2026-11-07/real_integrations.json) |
| `openai` | Streaming LLM | `2026-11-07` | `NO RUN` | `SKIPPED_NO_CREDENTIALS` | — | `tests/real/test_stt_tts_llm_stream.py` | [`real_integrations.json`](../evidence/real-integrations/2026-11-07/real_integrations.json) |
| `anthropic` | Streaming LLM | `2026-11-07` | `NO RUN` | `SKIPPED_NO_CREDENTIALS` | — | `tests/real/test_stt_tts_llm_stream.py` | [`real_integrations.json`](../evidence/real-integrations/2026-11-07/real_integrations.json) |
| `openai_realtime` | Speech-to-Speech (S2S) | `2026-11-07` | `NO RUN` | `SKIPPED_NO_CREDENTIALS` | — | `tests/real/test_s2s_live.py` | [`real_integrations.json`](../evidence/real-integrations/2026-11-07/real_integrations.json) |
| `gemini_live` | Speech-to-Speech (S2S) | `2026-11-07` | `NO RUN` | `SKIPPED_NO_CREDENTIALS` | — | `tests/real/test_s2s_live.py` | [`real_integrations.json`](../evidence/real-integrations/2026-11-07/real_integrations.json) |
| `salesforce` | CRM Sandbox Writeback | `2026-11-07` | `NO RUN` | `SKIPPED_NO_CREDENTIALS` | — | `tests/real/test_salesforce_sandbox.py` | [`real_integrations.json`](../evidence/real-integrations/2026-11-07/real_integrations.json) |
| `hubspot` | CRM Sandbox Writeback | `2026-11-07` | `NO RUN` | `SKIPPED_NO_CREDENTIALS` | — | `tests/real/test_hubspot_sandbox.py` | [`real_integrations.json`](../evidence/real-integrations/2026-11-07/real_integrations.json) |
| `webhook_receiver` | Signed Outbound Webhook | `2026-11-07` | `NO RUN` | `SKIPPED_NO_CREDENTIALS` | — | `tests/real/test_webhook_receiver.py` | [`real_integrations.json`](../evidence/real-integrations/2026-11-07/real_integrations.json) |

## Reproduction Commands

```bash
VOXDESK_REAL_INTEGRATION=1 \
VOXDESK_REAL_EVIDENCE_DIR=evidence/real-integrations/$(date -u +%Y-%m-%d) \
pytest tests/real --junitxml=evidence/real-integrations/$(date -u +%Y-%m-%d)/junit.xml -rs
python scripts/generate_feature_matrix.py
python scripts/generate_feature_matrix.py --check
```
