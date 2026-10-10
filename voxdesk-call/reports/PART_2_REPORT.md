# PART 2 REPORT — Voice Runtime Parity (Gate G3)

- **Repository**: `voxdesk-call-ainul-islam/voxdesk-call`
- **Date**: 2026-10-09
- **Alembic Head**: `0062_drop_pcap_artifacts (head)` (linear chain through `0057_experiment_assignment -> 0058_call_latency_stats -> 0059_number_agent_binding -> 0060_agent_turn_settings -> 0061_number_trust_profile -> 0062_drop_pcap_artifacts`)
- **Execution Order Completed**: `2A -> 2E -> 2B -> 2C -> 2D -> 2F -> 2G`

---

## 1. Sub-Phase Summary

### Sub-Phase 2A — Latency Measurement & Benchmark
- Added `CallLatencyStat` (`call_latency_stats` table, migration `0058_call_latency_stats.py`) and Prometheus histograms/counters (`voxdesk_voice_e2e_latency_seconds`, `voxdesk_voice_ttfb_seconds{stage,provider}`, `voxdesk_voice_interruptions_total`).
- Implemented `LatencyObserver(BaseObserver)` and `LatencyTrackingProcessor` in `app/agent/latency.py`, wired into `app/agent/pipeline.py` with automatic post-call persistence (`CallLatencyStat` + `Call.avg_response_ms`).
- Added `observability/grafana/voice-latency.json`, `observability/alerts.yml` (`VoxDeskVoiceLatencyHigh`, `VoxDeskVoiceLatencyCritical`, `VoxDeskSTTStageSlow`, `VoxDeskTTSStageSlow`), `scripts/bench_latency.py`, `scripts/measure_pstn_latency.py`, `docs/LATENCY_BENCHMARK.md`, and `tests/agent/test_latency_observer.py`.

### Sub-Phase 2E — Number-to-Agent Binding & RuntimeConfig Resolver
- Added migration `0059_number_agent_binding.py` adding `inbound_agent_id`, `inbound_agent_version`, and `outbound_agent_id` to `phone_numbers` (`app/telephony/number_provisioning.py`) and `agent_id`, `agent_version_id` to `calls` (`app/db/models.py`).
- Implemented `resolve_runtime_config(session, call, tenant)` in `app/runtime/agent_config_resolver.py` enforcing precedence:
  1. Active Experiment Variant (`1F`),
  2. Pinned `PhoneNumber.inbound_agent_version`,
  3. Bound `Agent.published_version_id`,
  4. Tenant default published `Agent`,
  5. Legacy `Tenant` columns fallback.
- Wired agent-scoped RAG (`document_ids` via `KnowledgeCollection` / `KnowledgeCollectionSource`) in `app/knowledge/context.py`, `PATCH /api/phone-numbers/{id}` in `app/api/phone_number_lifecycle_routes.py`, inbound/outbound routing in `app/telephony/twilio_handler.py` & `app/telephony/runtime.py`, and UI selectors in `dashboard-next/app/dashboard/phone-numbers/page.tsx`.

### Sub-Phase 2B — Smart Turn-Taking, Backchannel & Idle Reminders
- Added migration `0060_agent_turn_settings.py` and extended `app/domain/agent_models.py` with `responsiveness`, `interruption_sensitivity`, `enable_smart_turn`, `enable_backchannel`, `backchannel_frequency`, `backchannel_words`, `reminder_trigger_ms`, `reminder_max_count`, and `boosted_keywords`.
- Implemented `build_turn_config(cfg)` in `app/agent/turn_taking.py` (mapping `responsiveness` -> `SileroVADAnalyzer(VADParams(stop_secs=0.8 - 0.6*r))` + `LocalSmartTurnAnalyzerV3(SmartTurnParams(...))` and `interruption_sensitivity` -> `min_volume`, `allow_interruptions`), `build_idle_reminder_processor(cfg)` in `app/agent/idle_reminders.py` (wrapping Pipecat `UserIdleProcessor`), and `BackchannelProcessor` in `app/agent/humanize.py`.

### Sub-Phase 2C — Multi-Provider STT/TTS/LLM/S2S & Failover
- Pinned `soundfile==0.13.1` and `noisereduce==3.0.3` in `requirements.txt`.
- Implemented `app/agent/providers/{registry,stt_providers,tts_providers,llm_providers,s2s_providers,failover}.py`:
  - **STT**: Deepgram (`nova-3`, `keywords=word:boost`), AssemblyAI, OpenAI Whisper, Azure Speech, Google Cloud Speech v2, Local Whisper.
  - **TTS**: ElevenLabs (`eleven_flash_v2_5`, `eleven_multilingual_v2`), OpenAI TTS, Deepgram Aura, Cartesia Sonic-2, PlayHT, Azure Neural TTS, Google Journey/Neural2.
  - **LLM**: OpenAI, Anthropic, Google Gemini, Groq, Azure OpenAI, AWS Bedrock, Custom OpenAI.
  - **S2S**: OpenAI Realtime (`gpt-4o-realtime-preview`) and Google Gemini Live (`gemini-2.0-flash-exp`).
  - **Failover**: `FailoverServiceWrapper` with 2-failure / 30s window / 60s cooldown circuit breaker and `voxdesk_provider_failover_total` Prometheus counter.
- Added `app/api/voice_catalog_routes.py` (`GET /api/voices/catalog`, `GET /api/voices/providers`, `POST /api/voices/clone`), `app/voice/clone.py` (`clone_voice_elevenlabs`), and `docs/PROVIDERS.md`.

### Sub-Phase 2D — Agent Tools: `end_call`, DTMF, IVR, Voicemail, HTTP, MCP, Warm Transfer Briefing
- Implemented `app/agent/tools/{builtin_calls,ivr_navigation,voicemail,http_tools,mcp_tools}.py`, updated `app/agent/functions.py`, `app/services/tool_registry_service.py`, `app/api/tool_registry_routes.py`, `app/telephony/providers/base.py`, `app/telephony/transfer_service.py`, and `app/api/transfer_control_routes.py`.
- Added conference-backed Warm Transfer Briefing (`POST /api/calls/{id}/transfer/warm`, `/warm/bridge`, `/warm/abort`) that places the caller on conference hold, whispers the synthesized briefing to the human agent leg, and bridges or returns to the AI agent on timeout/decline.

### Sub-Phase 2F — Multi-Carrier Media Serializers, Telnyx, Vonage Capability & SIP Ingress
- Implemented `build_serializer(provider, ...)` in `app/telephony/media/serializers.py` supporting `TwilioFrameSerializer`, `TelnyxFrameSerializer`, `PlivoFrameSerializer`, `SIPBridgeFrameSerializer`, and honest `UnsupportedCapability` for `vonage` (`CapabilitySet.voice=False` in `app/telephony/providers/vonage.py`).
- Added `app/telephony/telnyx_handler.py` (`POST /telephony/telnyx/voice`, `WS /telephony/telnyx/stream/{call_id}`), extended `app/telephony/sip.py` (`verify_sip_digest_auth`, `check_sip_ip_acl`, `route_inbound_sip_invite`), and added `docs/adr/ADR-001-sip-ingress.md` + `services/sip-gateway/`.

### Sub-Phase 2G — Voice Controls, Ambient Audio, Denoise & Multilingual
- Added 5 loop-clean 8kHz mono ambient WAV soundbeds in `assets/ambient/` (`office.wav`, `call_center.wav`, `coffee_shop.wav`, `convention_hall.wav`, `summer_outdoor.wav`) + `assets/ambient/LICENSE.md`.
- Implemented `app/agent/audio/ambient.py` (`SoundfileMixer`), `app/agent/audio/denoise.py` (`NoisereduceFilter`), `app/agent/language.py` (`language="multi"` auto-detect), `app/agent/voice_settings.py` (`map_voice_settings`), and `app/telephony/transcription.py` (`resolve_transcription_options`).

---

## 2. Real Command Outputs (`2026-10-09`)

### 2.1 `make verify-truth`

```text
python3 scripts/strip_generated_tails.py --check dashboard/src dashboard-next
[]
python3 scripts/verify_no_filler.py
[]
python3 scripts/verify_no_fake_success.py
[]
python3 scripts/verify_retired_references.py
[]
python3 scripts/verify_no_null_bytes.py
[]
python3 scripts/strip_padding_markers.py --check
{}
test ! -s scripts/fake_success_allowlist.txt
python3 -m pytest tests/truth -q
.................................................................        [100%]
65 passed in 32.65s
```

### 2.2 Route Count, Alembic Head & Reversibility

```text
$ python3 -c "from app.main import app; print('routes:', len(app.routes))"
routes: 1182

$ alembic upgrade head && alembic heads && alembic downgrade -1 && alembic upgrade head
0062_drop_pcap_artifacts (head)
INFO  [alembic.runtime.migration] Running downgrade 0062_drop_pcap_artifacts -> 0061_number_trust_profile
INFO  [alembic.runtime.migration] Running upgrade 0061_number_trust_profile -> 0062_drop_pcap_artifacts
```

### 2.3 PART 2 Acceptance Test Suite (`pytest`)

```text
$ pytest -q \
  tests/agent/test_latency_observer.py \
  tests/agent/test_turn_taking_config.py \
  tests/agent/test_provider_registry.py \
  tests/agent/test_failover.py \
  tests/agent/test_s2s_smoke.py \
  tests/agent/test_tools_dtmf_end_call.py \
  tests/agent/test_ivr_navigation.py \
  tests/agent/test_voicemail_flow.py \
  tests/agent/test_http_tools.py \
  tests/telephony/test_warm_transfer_briefing.py \
  tests/telephony/test_agent_binding.py \
  tests/agent/test_runtime_config_resolver.py \
  tests/telephony/test_telnyx_media_path.py \
  tests/telephony/test_sip_connection.py \
  tests/agent/test_voice_settings_mapping.py \
  tests/agent/test_ambient_denoise_flags.py
..............s....................s.......                              [100%]
41 passed, 2 skipped in 26.84s
```

*(Note: the 2 skipped tests are `@pytest.mark.live` external sandbox tests in `test_s2s_smoke.py` and `test_telnyx_media_path.py` that require live API keys).*

### 2.4 Dashboard TypeScript & Vitest Check

```text
$ (cd dashboard-next && npx tsc --noEmit && npx vitest run)
 RUN  v3.2.7 /home/user/voxdesk-call-ainul-islam/voxdesk-call/dashboard-next
 ✓ tests/flow-editor.test.tsx (4 tests) 315ms
 ✓ lib/identity.test.ts (14 tests) 7ms
 ✓ tests/enterprise-ui/tilt-surface.test.tsx (14 tests) 11ms
 ✓ tests/enterprise-ui/metric-card-3d.test.tsx (17 tests) 74ms
 ✓ tests/enterprise-ui/data-state.test.tsx (15 tests) 16ms
 ✓ tests/enterprise-ui/connection-truth.test.tsx (5 tests) 201ms
 ✓ lib/format.test.ts (16 tests) 28ms

 Test Files  7 passed (7)
      Tests  85 passed (85)
```

---

## 3. Residual Gaps

- Opt-in `@pytest.mark.live` tests (`test_s2s_smoke.py`, `test_telnyx_media_path.py`) skip cleanly when external sandbox credentials (`OPENAI_API_KEY` with `VOXDESK_S2S_LIVE=1`, `TELNYX_API_KEY` with `VOXDESK_TELNYX_LIVE=1`) are not set in the local environment.
- Commercial noise-reduction filters (`krisp`, `koala`, `aic`) require vendor SDK licenses (`KRISP_LICENSE_KEY`, `PICOVOICE_KOALA_ACCESS_KEY`, `AICOUSTICS_LICENSE_KEY`) and remain unadvertised unless configured; open-source `noisereduce` is active by default.
