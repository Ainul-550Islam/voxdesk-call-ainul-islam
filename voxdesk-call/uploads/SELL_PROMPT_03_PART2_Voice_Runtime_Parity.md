# SELL PROMPT 3 of 10 — PART 2: Voice Runtime Parity

Copy the whole block below with one click and paste it into the coding agent. It contains everything for this part: mission, rules, the file tree with a `# comment` beside every file, and the acceptance commands.

```text
# ======================================================================================================================
# SELL PROMPT 3 of 10 — PART 2: VOICE RUNTIME PARITY   (Gate G3)
# Series: VoxDesk x Retell sell-ready gap closure (target sale band $30K-$60K) | repo root: voxdesk-call/ | master plan:
#   VOXDESK_SELL_READY_GAP_CLOSURE_30K_60K.md
# PREREQUISITE: SELL PROMPT 1 (PART 0) accepted. PART 1 is not required (its webhook work 1A makes the new tool events
#   visible).
# MISSION: Close the live voice-runtime gap against Retell: measured latency, smart turn-taking, provider choice with
#   failover and speech-to-speech, agent tools (end_call, DTMF, IVR navigation, voicemail, HTTP and MCP tools, warm
#   transfer with briefing), number-to-agent binding with an AgentVersion-driven runtime, multi-carrier media and voice
#   controls.
# ORDER OF WORK: 2A -> 2E -> 2B -> 2C -> 2D -> 2F -> 2G. One commit per sub-part, one report section per sub-part.
# SCOPE GUARD: change only what appears in the tree below. Other prompts own the rest. Do not start the next prompt in
#   this session.
# WHY: the live runtime is only ≈16.3K lines (measured in the audit). It is a clean Pipecat pipeline
#   (`app/agent/pipeline.py`: Twilio media stream → Silero VAD → Deepgram → LLM → ElevenLabs) but latency is unmeasured,
#   turn-taking is fixed-silence, providers are single-vendor, the agent cannot press digits/end calls/navigate IVRs, and
#   inbound calls read `Tenant` fields instead of a published `AgentVersion`.
# PIPECAT: Pipecat 0.0.94 APIs confirmed present (inspect the installed package before coding; use only what exists):
#   `pipecat.audio.turn.smart_turn.local_smart_turn_v3` (and v2/fal/http/coreml), `pipecat.frames.frames.{OutputDTMFFrame,
#   OutputDTMFUrgentFrame, InputDTMFFrame, LLMMessagesAppendFrame, LLMMessagesUpdateFrame, LLMRunFrame, TTSSpeakFrame,
#   EndTaskFrame, CancelTaskFrame, BotInterruptionFrame, STTMuteFrame, MixerEnableFrame, FilterEnableFrame,
#   MetricsFrame}`, `pipecat.metrics.metrics.{TTFBMetricsData, ProcessingMetricsData, LLMUsageMetricsData}`,
#   `pipecat.extensions.ivr.ivr_navigator.{IVRNavigator, IVRProcessor, IVRStatus}`,
#   `pipecat.extensions.voicemail.voicemail_detector.VoicemailDetector`,
#   `pipecat.processors.user_idle_processor.UserIdleProcessor`, `pipecat.processors.filters.stt_mute_filter`,
#   `pipecat.audio.mixers.soundfile_mixer`,
#   `pipecat.audio.filters.{noisereduce_filter,krisp_filter,koala_filter,aic_filter}`,
#   `pipecat.observers.{base_observer,turn_tracking_observer}` +
#   `pipecat.observers.loggers.{metrics_log_observer,user_bot_latency_log_observer,llm_log_observer,transcription_log_observer}`,
#   transports `pipecat.transports.network.{fastapi_websocket,small_webrtc}` + `services.{daily,livekit}`, serializers
#   `{twilio,telnyx,plivo,exotel,protobuf}`, services `openai_realtime`, `gemini_multimodal_live`, `aws_nova_sonic`,
#   `grok`, `ultravox`. Not present in 0.0.94: a Vonage serializer and the separate `pipecat-ai-flows` package.
# CLOSES facade rows: F-11 app/api/transfer_control_routes.py
# CLOSES unwired-table rows: W-12 AgentTool, WorkflowTrigger, KnowledgeCollection
# CLOSES Retell parity rows: #1 End-to-end latency | #2 Turn-taking & interruption controls | #3 LLMs and
#   speech-to-speech | #4 STT / TTS provider choice | #5 Voice cloning / voice library | #6 Voice controls (speed, volume,
#   pronunciation, boosted keywords, ambient sound, denoise) | #7 Multilingual / language switching | #9 Agent versions /
#   publish / rollback | #10 Agent bound to phone number | #11 Custom functions (HTTP tools) | #12 MCP tools | #13
#   Cold/warm transfer + AI briefing to human | #14 Agent-to-agent transfer | #15 IVR navigation (press digits on external
#   phone trees) | #16 DTMF | #17 Voicemail detection + message | #18 Numbers: buy / import / SIP trunk | #19
#   Multi-carrier live media | #24 Knowledge base (RAG) | #41 Contact memory & dynamic variables
# RULES (apply to every file below)
#   R1  Read every file fully before editing. Output the COMPLETE final content of every created/modified file.
#       Never write "...", "rest unchanged", "omitted for brevity".
#   R2  Extend REAL assets (tag [KEEP]); never build a parallel copy of something that already works.
#   R3  NO FAKE SUCCESS: do the real external effect or return NOT_CONFIGURED / UNSUPPORTED_CAPABILITY /
#       PENDING_PROVIDER (HTTP 501/409). Never record delivered/connected/success without proof.
#   R4  NO filler, padding, clones or line-count targets: no StructN, *_function_N, /endpoint-N, "Padding ... line
#       N" or "1050+ lines" banners.
#   R5  Durable multi-worker state only (Postgres / Redis / app/jobs / app/outbox). No process-local dict/list for
#       idempotency, rate limits, OAuth state, sessions, queues.
#   R6  Every outbound URL goes through app/core/ssrf.py. Secrets via app/security/secret_store.py or
#       app/integrations/crm/crypto.py (AES-GCM). No base64/reverse "encryption", no fallback secrets.
#   R7  Every query tenant-scoped (and environment-scoped where modelled). Cross-tenant = 404. Each new route ships
#       a two-tenant isolation test.
#   R8  Audit/outbox writes happen in the same transaction as the action. No `except Exception: pass` around
#       audit/outbox/webhook code.
#   R9  Every task ships unit + contract tests. External calls get a recording-fake test (asserts the real request)
#       AND an opt-in @pytest.mark.real_provider test (existing marker; runs only with VOXDESK_REAL_INTEGRATION=1). No
#       passing test = not LIVE.
#   R9b New tests/<dir>/ gets __init__.py; reuse the existing real_provider marker (never add a second `live`
#       marker); register only slow/docker in pytest.ini.
#   R10 Alembic: exactly ONE head (as shipped: 0045_request_idempotency_receipts). Use the next free revision, never
#       edit an applied migration, run `alembic heads` before and after.
#   R11 Docs contain only measured facts (scripts/repo_stats.py). Anything without an end-to-end/contract test is
#       documented as API_ONLY or PLANNED.
#   R12 One commit per task ID: "<TASK-ID>: <what>". No generated bulk. No null-byte files.
#   R13 After this part write reports/PART_<n>_REPORT.md: files changed (complete content), real command output,
#       test counts pass/fail/skip, residual gaps. Never claim "production ready".
#   R14 pipecat-ai is pinned at 0.0.94: inspect the installed package and use only classes that exist there.
#   R15 Register every new router/WebSocket in app/main.py, add an RBAC permission + isolation test, regenerate
#       contracts (make contracts-check). Never reuse an existing file name (ls app/api first).
# TAGS: [NEW] create | [MODIFY] read fully, change only what is described, keep other behaviour | [DELETE] remove +
#   every import/registration/compose/CI reference | [KEEP] REAL asset, extend only as stated | [VERIFY] inspect first,
#   record the decision in the report
# COMMENT FORMAT: # [TAG][sub-part] kind — what the file contains (classes / functions / behaviour / tests). Several
#   changes to one file are merged on one line as "sub-part: change || sub-part: change".
# ======================================================================================================================

voxdesk-call/
├── alembic/
│   └── versions/
│       ├── <next>_agent_turn_settings.py     # [NEW][2B] migration — backfill defaults into stored config snapshots
│       │                                     #   (idempotent)
│       ├── <next>_call_latency_stats.py      # [NEW][2A] migration — creates call_latency_stats
│       └── <next>_number_agent_binding.py    # [NEW][2E] migration — columns + backfill (existing numbers keep
│                                             #   tenant-compat behaviour)
├── app/
│   ├── agent/
│   │   ├── audio/
│   │   │   ├── __init__.py                   # [NEW][2G] package
│   │   │   ├── ambient.py                    # [NEW][2G] module — ambient sound via pipecat.audio.mixers.soundfile_mixer +
│   │   │   │                                 #   MixerEnableFrame at configurable volume; assets under assets/ambient/*.wav
│   │   │   │                                 #   (CC0, license file included)
│   │   │   └── denoise.py                    # [NEW][2G] module — optional noise suppression via
│   │   │                                     #   pipecat.audio.filters.noisereduce_filter (open source) or krisp/koala/aic
│   │   │                                     #   when licensed; capability flag; never advertise when disabled
│   │   ├── providers/
│   │   │   ├── __init__.py                   # [NEW][2C] package
│   │   │   ├── failover.py                   # [NEW][2C] module — failover policy reusing app/ai/circuit_breaker.py +
│   │   │   │                                 #   app/ai/fallback.py: STT/TTS/LLM errors or timeouts switch to the secondary
│   │   │   │                                 #   within the same call at a safe boundary (LLM between turns, TTS between
│   │   │   │                                 #   utterances); emits events + metrics
│   │   │   ├── llm_providers.py              # [NEW][2C] module — OpenAI, Anthropic, Google + one more (Groq/Azure OpenAI);
│   │   │   │                                 #   MODEL_CATALOG with unit-cost metadata feeding app/ai/costs.py; NO hard-coded
│   │   │   │                                 #   outdated model names in pipeline code
│   │   │   ├── registry.py                   # [NEW][2C] module — typed registries for STT/TTS/LLM/S2S: name → factory +
│   │   │   │                                 #   capabilities (languages, streaming, latency class, unit cost) + health;
│   │   │   │                                 #   selected per agent via config (stt.provider/model, tts.provider/voice_id,
│   │   │   │                                 #   llm.provider/model, mode: pipeline|s2s)
│   │   │   ├── s2s_providers.py              # [NEW][2C] module — OpenAI Realtime (pipecat.services.openai_realtime) and
│   │   │   │                                 #   Gemini Multimodal Live (pipecat.services.gemini_multimodal_live) behind a
│   │   │   │                                 #   feature flag; tool-calling parity with FunctionHandlers; usage metering into
│   │   │   │                                 #   the billing ledger
│   │   │   ├── stt_providers.py              # [NEW][2C] module — Deepgram (current model from config) + >=2 more STT
│   │   │   │                                 #   services available in pinned pipecat extras (e.g., AssemblyAI, Google,
│   │   │   │                                 #   Azure, Soniox — verify in site-packages)
│   │   │   └── tts_providers.py              # [NEW][2C] module — ElevenLabs + >=2 more (e.g., Cartesia, Deepgram Aura,
│   │   │                                     #   OpenAI TTS, Rime — verify); voice catalog listing per provider incl. cloned
│   │   │                                     #   voices
│   │   ├── tools/
│   │   │   ├── __init__.py                   # [NEW][2D] package — tool contracts, registry, dispatcher (split out of
│   │   │   │                                 #   functions.py responsibilities)
│   │   │   ├── builtin_calls.py              # [NEW][2D] module — tools end_call(reason) (speak goodbye then EndTaskFrame),
│   │   │   │                                 #   send_dtmf(digits) (push OutputDTMFUrgentFrame; validate [0-9*#wW], max
│   │   │   │                                 #   length, audit; verify the Twilio/Telnyx output transport renders tones —
│   │   │   │                                 #   otherwise synthesize dual-tone audio), transfer_call(destination,
│   │   │   │                                 #   mode=cold|warm, briefing), send_sms(to, body) guarded by consent + DNC
│   │   │   ├── http_tools.py                 # [NEW][2D] module — customer-defined HTTP function tools from AgentTool:
│   │   │   │                                 #   JSON-schema params, URL template, method, headers (secrets via
│   │   │   │                                 #   secret_store), timeout, retries, response mapping (JSONPath → dynamic
│   │   │   │                                 #   variables), speak_during_execution line, SSRF guard (app/core/ssrf.py),
│   │   │   │                                 #   PII-safe logging, counts toward the per-call tool ceiling
│   │   │   ├── ivr_navigation.py             # [NEW][2D] module — agent mode `ivr_navigation` for outbound calls to phone
│   │   │   │                                 #   trees using pipecat.extensions.ivr.ivr_navigator
│   │   │   │                                 #   (IVRNavigator/IVRProcessor/IVRStatus): classify IVR vs human, press digits,
│   │   │   │                                 #   speak goal; config: goal text + fallback to normal conversation when a human
│   │   │   │                                 #   answers; metric voxdesk_ivr_navigation_total{result}
│   │   │   ├── mcp_tools.py                  # [NEW][2D] module — MCP client tool source (Streamable HTTP/SSE) with
│   │   │   │                                 #   allowlist + timeouts; tools discovered at publish time and pinned in the
│   │   │   │                                 #   AgentVersion snapshot
│   │   │   └── voicemail.py                  # [NEW][2D] module — wraps
│   │   │                                     #   pipecat.extensions.voicemail.voicemail_detector.VoicemailDetector and
│   │   │                                     #   reconciles with Twilio AMD result; per-agent action: leave_message(template
│   │   │                                     #   with variables) | hangup | transfer; emits voicemail_detected event (1A)
│   │   ├── functions.py                      # [MODIFY][2D] module — keep FunctionHandlers + the 9 existing tools and
│   │   │                                     #   TOOL_CONTRACTS compatibility; delegate new tools to app/agent/tools/*; keep
│   │   │                                     #   the per-call tool ceiling
│   │   ├── humanize.py                       # [MODIFY][2B] module — backchannel/filler injection driven by agent settings;
│   │   │                                     #   language-aware word lists; never talk over the caller (respect VAD state)
│   │   ├── idle_reminders.py                 # [NEW][2B] module — wraps pipecat UserIdleProcessor: speaks configurable
│   │   │                                     #   reminder lines, hangs up after N reminders / silence limit via EndTaskFrame;
│   │   │                                     #   per-language lines
│   │   ├── language.py                       # [NEW][2G] module — allowed languages per agent, Deepgram multi-language mode
│   │   │                                     #   or `switch_language` tool, TTS voice switch map; uses app/core/i18n.py for
│   │   │                                     #   fixed strings
│   │   ├── latency.py                        # [NEW][2A] module — class LatencyObserver(BaseObserver) + helper processor:
│   │   │                                     #   records per turn STT/LLM/TTS TTFB (from MetricsFrame/TTFBMetricsData) and
│   │   │                                     #   end-to-end (UserStoppedSpeaking → first TTS audio frame out); fn
│   │   │                                     #   summarize(call) → p50/p95/max; exports Prometheus histograms
│   │   │                                     #   voxdesk_voice_e2e_latency_seconds,
│   │   │                                     #   voxdesk_voice_ttfb_seconds{stage=stt|llm|tts}, counter
│   │   │                                     #   voxdesk_voice_interruptions_total
│   │   ├── llm_factory.py                    # [MODIFY][2C] module — delegate to registry; keep `fast/natural` tiers as
│   │   │                                     #   aliases
│   │   ├── pipeline.py                       # [MODIFY][2A,2B,2E] module — 2A: pass LatencyObserver and
│   │   │                                     #   TurnTrackingObserver in PipelineTask(observers=[...]); keep
│   │   │                                     #   enable_metrics=True; remove the hard-coded "550-750 ms" docstring claim
│   │   │                                     #   (replace with a reference to docs/LATENCY_BENCHMARK.md) || 2B: use
│   │   │                                     #   build_turn_config + idle reminders; keep `tenant.vad_stop_secs` as the
│   │   │                                     #   backward-compatible default || 2E: accept RuntimeConfig instead of reading
│   │   │                                     #   tenant.* directly (keep a legacy adapter)
│   │   ├── stt.py                            # [MODIFY][2C] module — thin shim delegating to registry (keep public function
│   │   │                                     #   names so pipeline/tests keep working)
│   │   ├── stt_stream.py                     # [MODIFY][2C] module — same shim treatment
│   │   ├── tts.py                            # [MODIFY][2C] module — same shim treatment
│   │   ├── turn_taking.py                    # [NEW][2B] module — fn build_turn_config(agent_cfg) → Silero VAD params +
│   │   │                                     #   LocalSmartTurnAnalyzerV3
│   │   │                                     #   (pipecat.audio.turn.smart_turn.local_smart_turn_v3), falling back to fixed
│   │   │                                     #   stop_secs when the model/ONNX is unavailable; maps agent settings:
│   │   │                                     #   responsiveness (0–1), interruption_sensitivity (0–1), enable_backchannel,
│   │   │                                     #   reminder_trigger_ms + reminder_max_count (idle prompts),
│   │   │                                     #   end_call_after_silence_ms, max_call_duration_ms
│   │   └── voice_settings.py                 # [MODIFY][2G] module — typed settings: speed, volume, stability/style
│   │                                         #   (provider-mapped), pronunciation_dictionary (word→alias/IPA; provider-aware:
│   │                                         #   ElevenLabs dictionary locators or SSML alias), boosted_keywords (Deepgram
│   │                                         #   keyterms/keywords), ambient_sound + ambient_volume, denoise mode,
│   │                                         #   normalize_for_speech
│   ├── api/
│   │   ├── phone_number_lifecycle_routes.py  # [MODIFY][2E] route — bind/unbind endpoints; provider calls are real or
│   │   │                                     #   NOT_CONFIGURED
│   │   ├── tool_registry_routes.py           # [MODIFY][2D] route — thin routes over the service; `POST /{id}/test`
│   │   │                                     #   performs a REAL SSRF-guarded HTTP call and returns real status/latency;
│   │   │                                     #   delete simulated results
│   │   ├── transfer_control_routes.py        # [MODIFY][2D] route — whisper/briefing really delivered through the provider;
│   │   │                                     #   delete "In real implementation" paths
│   │   └── voice_catalog_routes.py           # [NEW][2C] route — GET /api/voices?provider=&language=, GET /api/models
│   │                                         #   (LLM/STT/TTS catalogs with capabilities/cost)
│   ├── core/
│   │   └── metrics.py                        # [MODIFY][2A] module — register the histograms with explicit buckets 0.2…3.0
│   │                                         #   s
│   ├── db/
│   │   └── telephony_models.py               # [MODIFY][2A,2E] model — 2A: CallLatencyStat(call_id, turn_idx, stt_ms,
│   │                                         #   llm_ttfb_ms, tts_ttfb_ms, e2e_ms, interrupted, created_at) + index
│   │                                         #   (tenant_id, call_id) || 2E: PhoneNumber.inbound_agent_id,
│   │                                         #   inbound_agent_version (null = latest published), outbound_agent_id; unique
│   │                                         #   per environment; Call.agent_id + Call.agent_version_id
│   ├── domain/
│   │   └── agent_models.py                   # [VERIFY][2B] schema — locate the AgentVersion config schema (`grep -rn
│   │                                         #   config_snapshot app`); add typed, validated settings with defaults for all
│   │                                         #   fields above
│   ├── knowledge/
│   │   └── context.py                        # [MODIFY][2E] module — retrieval scoped to the RuntimeConfig's knowledge
│   │                                         #   collections (wires KnowledgeCollection into live calls)
│   ├── runtime/
│   │   ├── __init__.py                       # [NEW][2E] package
│   │   └── agent_config_resolver.py          # [NEW][2E] module — async fn resolve_runtime_config(session, *, tenant,
│   │                                         #   direction, to_number, agent_id=None, version=None, environment) →
│   │                                         #   RuntimeConfig dataclass (prompt, voice, language, tools, knowledge
│   │                                         #   collections, turn settings, providers, dynamic variables, source flag) built
│   │                                         #   from the published AgentVersion snapshot; falls back to Tenant fields with
│   │                                         #   source="tenant_compat"; ONE code path for inbound, outbound, web calls and
│   │                                         #   simulations
│   ├── services/
│   │   └── tool_registry_service.py          # [NEW][2D] service — CRUD + validation + versioning with AgentVersion + real
│   │                                         #   test-invoke
│   ├── telephony/
│   │   ├── media/
│   │   │   ├── __init__.py                   # [NEW][2F] package
│   │   │   └── serializers.py                # [NEW][2F] module — provider → pipecat serializer factory:
│   │   │                                     #   TwilioFrameSerializer, TelnyxFrameSerializer (pipecat.serializers.telnyx),
│   │   │                                     #   PlivoFrameSerializer (optional); encoding/sample-rate per provider; Vonage =
│   │   │                                     #   capability flag media_supported=False until a custom serializer is written
│   │   ├── providers/
│   │   │   ├── base.py                       # [MODIFY][2D] module — adapter capability flags (supports_dtmf_send,
│   │   │   │                                 #   supports_warm_transfer, supports_takeover); callers must check them
│   │   │   ├── factory.py                    # [KEEP][2F] module — provider selection; extend only to expose media
│   │   │   │                                 #   capability
│   │   │   └── vonage.py                     # [MODIFY][2F] module — capabilities() reports media_supported=False; AI-call
│   │   │                                     #   attempts return UNSUPPORTED_CAPABILITY (no silent success)
│   │   ├── number_provisioning.py            # [MODIFY][2E] module — bind_inbound_agent / bind_outbound_agent with
│   │   │                                     #   validation (agent published, same environment); keep assign(use=…) semantics
│   │   ├── runtime.py                        # [MODIFY][2E] module — outbound path uses the same resolver (already requires
│   │   │                                     #   a published version)
│   │   ├── sip.py                            # [MODIFY][2F] module — provision trunk/credentials/IP-ACL on the chosen
│   │   │                                     #   gateway API; test_sip_connection performs a real OPTIONS/REGISTER probe;
│   │   │                                     #   NOT_CONFIGURED when no gateway is deployed
│   │   ├── telnyx_handler.py                 # [NEW][2F] module — Telnyx call-control/TeXML webhook +
│   │   │                                     #   `/telephony/telnyx/ws` handler reusing run_voice_agent with the Telnyx
│   │   │                                     #   serializer; signature verification via providers/telnyx.verify_webhook
│   │   ├── transcription.py                  # [MODIFY][2G] module — carry detected language into the transcript record
│   │   ├── transfer_service.py               # [MODIFY][2D] module — warm transfer with AI briefing: dial the human leg,
│   │   │                                     #   speak/play a summary to the human (provider say/play), wait for acceptance
│   │   │                                     #   (DTMF 1 or voice), then bridge; on failure return the caller to the agent;
│   │   │                                     #   keep the state machine + idempotency; emit transfer_* events
│   │   └── twilio_handler.py                 # [MODIFY][2E] module — /telephony/voice resolves dialed number → (tenant,
│   │                                         #   agent, version) via the resolver and passes ids in <Stream> parameters;
│   │                                         #   record on Call
│   ├── tts/
│   │   ├── provider.py                       # [VERIFY][2C] module — keep the provider-neutral contracts; adapters live in
│   │   │                                     #   the new package
│   │   └── registry.py                       # [VERIFY][2C] module — merge with app/voice/provider_registry.py and the new
│   │                                         #   registry; ONE source of truth
│   └── voice/
│       └── clone.py                          # [VERIFY][2C] module — confirm voice cloning really calls a provider
│                                             #   (VOICE_CLONE job); record result; NOT_CONFIGURED otherwise
├── assets/
│   └── ambient/
│       └── LICENSE.md                        # [NEW][2G] doc — source + license of each ambient sample
├── dashboard-next/
│   └── app/
│       └── dashboard/
│           └── phone-numbers/
│               └── page.tsx                  # [NEW][2E] ui-page — numbers list (search/buy/import/release), bind
│                                             #   inbound/outbound agent + version, capability flags, trust status badges
│                                             #   (PART 7)
├── docs/
│   ├── adr/
│   │   └── ADR-001-sip-ingress.md            # [NEW][2F] doc — decision record: gateway choice, trade-offs, capacity,
│   │                                         #   security model
│   ├── LATENCY_BENCHMARK.md                  # [NEW][2A] doc — methodology (region, providers, models, prompts, hardware),
│   │                                         #   results table (>=200 calls; p50/p95/p99 e2e + per-stage), raw data link.
│   │                                         #   Numbers only from real runs; Retell's ≈600 ms is quoted as a vendor claim,
│   │                                         #   never as a measured comparison
│   └── PROVIDERS.md                          # [NEW][2C] doc — supported providers, required env vars, capability matrix,
│                                             #   failover behaviour, known limits
├── observability/
│   ├── grafana/
│   │   └── voice-latency.json                # [NEW][2A] config — dashboard: p50/p95/p99 e2e, per-stage TTFB, by
│   │                                         #   provider/model/region/agent
│   └── alerts.yml                            # [MODIFY][2A] config — alert when p95 e2e > target for 10 min (target taken
│                                             #   from the benchmark doc, not guessed)
├── scripts/
│   ├── bench_latency.py                      # [NEW][2A] script — synthetic-caller harness: speaks Twilio media-stream JSON
│   │                                         #   frames (connected/start/media/stop) with recorded caller WAVs to the REAL
│   │                                         #   /telephony/ws path, N concurrent calls, collects CallLatencyStat + CPU/RAM;
│   │                                         #   writes JSON + CSV under evidence/latency/<date>/
│   └── measure_pstn_latency.py               # [NEW][2A] script — optional real-PSTN measurement: dual-channel call
│                                             #   recordings from test calls → gap between caller end-of-speech and agent
│                                             #   start of speech
├── services/
│   └── sip-gateway/                          # [NEW][2F] dir — OPTIONAL (choose ONE and document the ADR): LiveKit SIP,
│                                             #   jambonz or FreeSWITCH container + config terminating customer SIP trunks
│                                             #   (auth, PCMU/PCMA/Opus, RFC2833 DTMF) and bridging audio to /telephony/ws;
│                                             #   configuration only — no custom filler code
├── tests/
│   ├── agent/
│   │   ├── fixtures/
│   │   │   └── incomplete_utterances/
│   │   │       └── *.wav                     # [NEW][2B] test fixtures — "I'd like to book for…" style audio (marker
│   │   │                                     #   `slow`): smart-turn must not end the turn on trailing incomplete phrases
│   │   ├── test_ambient_denoise_flags.py     # [NEW][2G] test — mixer enabled/disabled frames; denoise capability gating
│   │   ├── test_failover.py                  # [NEW][2C] test — chaos (app/core/chaos.py) injects 5xx/timeouts per stage;
│   │   │                                     #   call continues on the secondary
│   │   ├── test_http_tools.py                # [NEW][2D] test — real local HTTP server:
│   │   │                                     #   params/headers/timeout/retry/response mapping; SSRF block; secret never
│   │   │                                     #   logged
│   │   ├── test_ivr_navigation.py            # [NEW][2D] test — scripted IVR transcript: agent selects menu digits, detects
│   │   │                                     #   human pickup
│   │   ├── test_latency_observer.py          # [NEW][2A] test — feeds synthetic frames, asserts per-turn math, interruption
│   │   │                                     #   handling, histogram export
│   │   ├── test_provider_registry.py         # [NEW][2C] test — selection by config, unknown provider → clear error,
│   │   │                                     #   capability filtering
│   │   ├── test_runtime_config_resolver.py   # [NEW][2E] test — snapshot precedence, tenant-compat fallback, environment
│   │   │                                     #   mismatch errors
│   │   ├── test_s2s_smoke.py                 # [NEW][2C] test — `@pytest.mark.real_provider` S2S round trip with tool call
│   │   ├── test_tools_dtmf_end_call.py       # [NEW][2D] test — DTMF validation + frame emission; end_call speaks then
│   │   │                                     #   terminates; tool ceiling respected
│   │   ├── test_turn_taking_config.py        # [NEW][2B] test — setting→parameter mapping, fallback when smart-turn model
│   │   │                                     #   missing, reminder/hangup logic with fake clock
│   │   ├── test_voice_settings_mapping.py    # [NEW][2G] test — settings→provider payload assertions (pronunciation,
│   │   │                                     #   keywords, speed/volume)
│   │   └── test_voicemail_flow.py            # [NEW][2D] test — detector verdict → configured action; AMD reconciliation
│   └── telephony/
│       ├── test_agent_binding.py             # [NEW][2E] test — inbound call uses the bound agent/version prompt+voice;
│       │                                     #   unbinding falls back; Call rows store agent/version; cross-tenant binding
│       │                                     #   rejected
│       ├── test_sip_connection.py            # [NEW][2F] test — gateway-API fake asserts provisioning calls; probe failure
│       │                                     #   surfaces a real error
│       ├── test_telnyx_media_path.py         # [NEW][2F] test — Telnyx websocket frames through the serializer into the
│       │                                     #   pipeline (provider fake) + `@pytest.mark.real_provider` real call
│       └── test_warm_transfer_briefing.py    # [NEW][2D] test — briefing delivered to the human leg; failure path returns
│                                             #   to agent; events emitted
└── requirements.txt                          # [MODIFY][2C] config — add only the pipecat extras actually used; re-pin; run
                                              #   pip-audit (scripts/audit_dependencies.sh)

# ======================================================================================================================
# ACCEPTANCE — run every command; paste the REAL output into reports/PART_2_REPORT.md
# ======================================================================================================================
make verify-truth                                   # truth guards must stay green
python -c "from app.main import app; print('routes:', len(app.routes))"   # new routers registered, no /endpoint-N routes
alembic upgrade head
alembic heads                                       # exactly ONE head
alembic downgrade -1 && alembic upgrade head           # migration is reversible
pytest -q tests/agent/test_latency_observer.py
pytest -q tests/agent/test_turn_taking_config.py
pytest -q tests/agent/test_provider_registry.py
pytest -q tests/agent/test_failover.py
pytest -q tests/agent/test_tools_dtmf_end_call.py
pytest -q tests/agent/test_ivr_navigation.py
pytest -q tests/agent/test_voicemail_flow.py
pytest -q tests/agent/test_http_tools.py
pytest -q tests/telephony/test_warm_transfer_briefing.py
pytest -q tests/telephony/test_agent_binding.py
pytest -q tests/agent/test_runtime_config_resolver.py
pytest -q tests/telephony/test_sip_connection.py
pytest -q tests/agent/test_voice_settings_mapping.py
pytest -q tests/agent/test_ambient_denoise_flags.py
VOXDESK_REAL_INTEGRATION=1 pytest -q -m real_provider tests/agent/test_s2s_smoke.py        # needs real credentials (skipped otherwise)
VOXDESK_REAL_INTEGRATION=1 pytest -q -m real_provider tests/telephony/test_telnyx_media_path.py        # needs real credentials (skipped otherwise)
(cd dashboard-next && npx tsc --noEmit && npx vitest run)

# ACCEPTANCE [2A]: `docs/LATENCY_BENCHMARK.md` generated from ≥200 real-provider calls; dashboard shows live p95; CI
#   runs the observer unit test.
# ACCEPTANCE [2G]: latency report (2A) published; agent bound to a number answers with its published version (2E);
#   DTMF/end_call/IVR/voicemail tools pass tests (2D); provider failover test passes (2C);
#   `scripts/fake_success_allowlist.txt` no longer lists `tool_registry_routes.py`, `transfer_control_routes.py`.
# ACCEPTANCE [2B]: setting-to-parameter mapping tested; fixed stop_secs fallback when the smart-turn model is missing;
#   incomplete-utterance fixtures do not end the turn; reminder/hang-up logic proven with a fake clock.
# ACCEPTANCE [2C]: provider chosen per agent config; failover test passes with injected 5xx/timeouts; S2S smoke test
#   (marker real_provider) completes a round trip with a tool call.
# ACCEPTANCE [2D]: end_call, send_dtmf, IVR navigation, voicemail action, HTTP tools and warm-transfer briefing tests
#   pass; tool-registry test-invoke performs a REAL SSRF-guarded call.
# ACCEPTANCE [2E]: an inbound call to a bound number uses that agent/version prompt and voice; unbinding falls back to
#   tenant-compat; Call rows store agent_id/agent_version_id; cross-tenant binding is rejected.
# ACCEPTANCE [2F]: Telnyx websocket frames flow through the serializer into the pipeline (provider fake) and a live test
#   connects a real call; SIP provisioning/probe tests pass; Vonage AI calls return UNSUPPORTED_CAPABILITY.

# NEXT: SELL PROMPT 4 of 10 — PART 3: WEB CALLS, WIDGET AND SDKs. Start it only after every acceptance command above
#   passes and reports/PART_2_REPORT.md exists.
```
