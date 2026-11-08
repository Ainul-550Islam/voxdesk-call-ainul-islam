# VoxDesk — Real External Provider Evidence Status

Generated automatically by `scripts/generate_feature_matrix.py` from the
nightly real-provider integration ledger (`tests/real/`). Do not edit by hand.

- **Latest Run Date (UTC)**: `2026-11-07`
- **Freshness Status**: FRESH (`0` day(s) old; window `<= 7` days)
- **`provider_fakes`**: `false` (enforced across every `tests/real/` suite)
- **Evidence JSON**: [`evidence/real-integrations/2026-11-07/real_integrations.json`](../evidence/real-integrations/2026-11-07/real_integrations.json)
- **JUnit XML**: [`evidence/real-integrations/2026-11-07/junit.xml`](../evidence/real-integrations/2026-11-07/junit.xml)

## Per-Provider Integration Status

| Provider | Capability Stage | Last Run Date | Freshness | Result | Latency (ms) | Model / Suite | Evidence Artifact |
| --- | --- | --- | --- | --- | ---: | --- | --- |
| `twilio` | Telephony (PSTN + Media Streams) | `2026-11-07` | `FRESH (0d)` | `SKIPPED_NO_CREDENTIALS` | — | `tests/real/test_twilio_inbound_outbound.py` | [`real_integrations.json`](../evidence/real-integrations/2026-11-07/real_integrations.json) |
| `telnyx` | Telephony (Call Control v2 + WS) | `2026-11-07` | `FRESH (0d)` | `SKIPPED_NO_CREDENTIALS` | — | `tests/real/test_telnyx_live.py` | [`real_integrations.json`](../evidence/real-integrations/2026-11-07/real_integrations.json) |
| `deepgram` | Streaming STT | `2026-11-07` | `FRESH (0d)` | `SKIPPED_NO_CREDENTIALS` | — | `tests/real/test_stt_tts_llm_stream.py` | [`real_integrations.json`](../evidence/real-integrations/2026-11-07/real_integrations.json) |
| `elevenlabs` | Streaming TTS | `2026-11-07` | `FRESH (0d)` | `SKIPPED_NO_CREDENTIALS` | — | `tests/real/test_stt_tts_llm_stream.py` | [`real_integrations.json`](../evidence/real-integrations/2026-11-07/real_integrations.json) |
| `cartesia` | Streaming TTS | `2026-11-07` | `FRESH (0d)` | `SKIPPED_NO_CREDENTIALS` | — | `tests/real/test_stt_tts_llm_stream.py` | [`real_integrations.json`](../evidence/real-integrations/2026-11-07/real_integrations.json) |
| `openai` | Streaming LLM | `2026-11-07` | `FRESH (0d)` | `SKIPPED_NO_CREDENTIALS` | — | `tests/real/test_stt_tts_llm_stream.py` | [`real_integrations.json`](../evidence/real-integrations/2026-11-07/real_integrations.json) |
| `anthropic` | Streaming LLM | `2026-11-07` | `FRESH (0d)` | `SKIPPED_NO_CREDENTIALS` | — | `tests/real/test_stt_tts_llm_stream.py` | [`real_integrations.json`](../evidence/real-integrations/2026-11-07/real_integrations.json) |
| `openai_realtime` | Speech-to-Speech (S2S) | `2026-11-07` | `FRESH (0d)` | `SKIPPED_NO_CREDENTIALS` | — | `tests/real/test_s2s_live.py` | [`real_integrations.json`](../evidence/real-integrations/2026-11-07/real_integrations.json) |
| `gemini_live` | Speech-to-Speech (S2S) | `2026-11-07` | `FRESH (0d)` | `SKIPPED_NO_CREDENTIALS` | — | `tests/real/test_s2s_live.py` | [`real_integrations.json`](../evidence/real-integrations/2026-11-07/real_integrations.json) |
| `salesforce` | CRM Sandbox Writeback | `2026-11-07` | `FRESH (0d)` | `SKIPPED_NO_CREDENTIALS` | — | `tests/real/test_salesforce_sandbox.py` | [`real_integrations.json`](../evidence/real-integrations/2026-11-07/real_integrations.json) |
| `hubspot` | CRM Sandbox Writeback | `2026-11-07` | `FRESH (0d)` | `SKIPPED_NO_CREDENTIALS` | — | `tests/real/test_hubspot_sandbox.py` | [`real_integrations.json`](../evidence/real-integrations/2026-11-07/real_integrations.json) |
| `webhook_receiver` | Signed Outbound Webhook | `2026-11-07` | `FRESH (0d)` | `SKIPPED_NO_CREDENTIALS` | — | `tests/real/test_webhook_receiver.py` | [`real_integrations.json`](../evidence/real-integrations/2026-11-07/real_integrations.json) |

## Reproduction Commands

```bash
VOXDESK_REAL_INTEGRATION=1 \
VOXDESK_REAL_EVIDENCE_DIR=evidence/real-integrations/$(date -u +%Y-%m-%d) \
pytest tests/real --junitxml=evidence/real-integrations/$(date -u +%Y-%m-%d)/junit.xml -rs
python scripts/generate_feature_matrix.py
python scripts/generate_feature_matrix.py --check
```
