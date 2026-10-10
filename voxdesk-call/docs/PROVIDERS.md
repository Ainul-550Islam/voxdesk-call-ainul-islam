# VoxDesk Multi-Provider Voice Runtime Matrix (Sub-Phase 2C)

## Overview

VoxDesk supports multi-vendor Speech-to-Text (STT), Text-to-Speech (TTS), Large Language Model (LLM), and Speech-to-Speech (S2S) providers with per-agent selection (`RuntimeConfig`) and automatic circuit-breaker failover (`FailoverServiceWrapper`).

---

## 1. Speech-to-Text (STT) Providers

| Provider (`stt_provider`) | Default Model | Supported Models | Required Env Vars | Typical TTFB | Capabilities |
|---|---|---|---|---|---|
| `deepgram` | `nova-3` | `nova-3`, `nova-2`, `nova-2-medical`, `nova-2-phonecall` | `DEEPGRAM_API_KEY` | ~95 ms | Streaming WebSocket, Interim results, Keyword Boosting (`keywords=word:boost`), Multilingual |
| `assemblyai` | `universal-2` | `universal-2`, `best`, `nano` | `ASSEMBLYAI_API_KEY` | ~140 ms | Streaming WebSocket, Built-in endpointing |
| `openai_whisper` | `gpt-4o-transcribe` | `gpt-4o-transcribe`, `gpt-4o-mini-transcribe`, `whisper-1` | `OPENAI_API_KEY` | ~220 ms | 50+ languages, High domain accuracy |
| `azure` | `default` | `default`, `conversation`, `dictation` | `AZURE_SPEECH_KEY`, `AZURE_SPEECH_REGION` | ~150 ms | Enterprise Azure Speech real-time STT |
| `google` | `chirp_2` | `chirp_2`, `latest_short`, `telephony` | `GOOGLE_API_KEY` | ~165 ms | Google Cloud Speech v2 / Chirp 2 |
| `whisper_local` | `base` | `tiny`, `base`, `small`, `medium`, `large-v3-turbo` | `WHISPER_LOCAL_ENABLED` | ~260 ms | Self-hosted / air-gapped Whisper |

---

## 2. Text-to-Speech (TTS) Providers

| Provider (`tts_provider`) | Default Model | Supported Models | Required Env Vars | Typical TTFB | Capabilities |
|---|---|---|---|---|---|
| `elevenlabs` | `eleven_flash_v2_5` | `eleven_flash_v2_5`, `eleven_turbo_v2_5`, `eleven_multilingual_v2` | `ELEVENLABS_API_KEY` | ~135 ms | Stability, Similarity Boost, Style, Speed (`0.7..1.2`), Instant Voice Cloning |
| `openai` | `gpt-4o-mini-tts` | `gpt-4o-mini-tts`, `tts-1`, `tts-1-hd` | `OPENAI_API_KEY` | ~180 ms | Speed (`0.25..4.0`), Built-in voices (`alloy`, `nova`, `shimmer`, etc.) |
| `deepgram` | `aura-asteria-en` | `aura-asteria-en`, `aura-luna-en`, `aura-stella-en`, `aura-orion-en` | `DEEPGRAM_API_KEY` | ~110 ms | Sub-120ms telephony streaming TTS |
| `cartesia` | `sonic-2` | `sonic-2`, `sonic-english`, `sonic-multilingual` | `CARTESIA_API_KEY` | ~90 ms | Sub-100ms SSM synthesis, Emotion & Speed controls, Voice Cloning |
| `playht` | `Play3.0-mini` | `Play3.0-mini`, `PlayDialog`, `PlayHT2.0-turbo` | `PLAYHT_API_KEY`, `PLAYHT_USER_ID` | ~160 ms | Speed, Temperature, Style guidance, Voice Cloning |
| `azure` | `en-US-AvaMultilingualNeural` | `en-US-AvaMultilingualNeural`, `en-US-AndrewMultilingualNeural`, `en-US-JennyNeural` | `AZURE_SPEECH_KEY`, `AZURE_SPEECH_REGION` | ~155 ms | SSML Rate (`%`), Pitch (`st`), Volume Gain (`dB`) |
| `google` | `en-US-Journey-F` | `en-US-Journey-F`, `en-US-Journey-D`, `en-US-Neural2-F` | `GOOGLE_API_KEY` | ~170 ms | Speaking rate, Pitch semitones, Volume gain dB |

---

## 3. Large Language Model (LLM) Providers

| Provider (`llm_provider`) | Default Model | Supported Models | Required Env Vars | Typical TTFB | Tool Calling |
|---|---|---|---|---|---|
| `openai` | `gpt-4o-mini` | `gpt-4o-mini`, `gpt-4o`, `gpt-4.1-mini`, `gpt-4.1` | `OPENAI_API_KEY` | ~210 ms | Yes |
| `anthropic` | `claude-haiku-4-5-20251001` | `claude-haiku-4-5-20251001`, `claude-3-5-haiku-latest`, `claude-3-5-sonnet-latest`, `claude-sonnet-4-5` | `ANTHROPIC_API_KEY` | ~240 ms | Yes |
| `google` | `gemini-2.0-flash` | `gemini-2.0-flash`, `gemini-2.0-flash-lite`, `gemini-1.5-pro` | `GOOGLE_API_KEY` | ~220 ms | Yes |
| `groq` | `llama-3.3-70b-versatile` | `llama-3.3-70b-versatile`, `llama-3.1-8b-instant`, `qwen-2.5-32b` | `GROQ_API_KEY` | ~110 ms | Yes |
| `azure_openai` | `gpt-4o-mini` | `gpt-4o-mini`, `gpt-4o` | `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT` | ~220 ms | Yes |
| `bedrock` | `anthropic.claude-3-5-haiku-20241022-v1:0` | `anthropic.claude-3-5-haiku-20241022-v1:0`, `anthropic.claude-3-5-sonnet-20241022-v2:0`, `amazon.nova-lite-v1:0` | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | ~260 ms | Yes |
| `custom_openai` | `default` | Any OpenAI-compatible model | `CUSTOM_OPENAI_API_KEY`, `CUSTOM_OPENAI_BASE_URL` | ~250 ms | Yes |

---

## 4. Speech-to-Speech (S2S) Realtime Providers

| Provider (`s2s_provider`) | Default Model | Supported Models | Required Env Vars | Typical E2E | Notes |
|---|---|---|---|---|---|
| `openai_realtime` | `gpt-4o-realtime-preview` | `gpt-4o-realtime-preview`, `gpt-4o-mini-realtime-preview` | `OPENAI_API_KEY` | ~320 ms | Direct audio-in/audio-out WebSocket; bypasses separate STT+LLM+TTS |
| `gemini_live` | `gemini-2.0-flash-exp` | `gemini-2.0-flash-exp` | `GOOGLE_API_KEY` | ~350 ms | Bidirectional multimodal live audio streaming |

---

## 5. Circuit-Breaker Failover (`FailoverServiceWrapper`)

Every stage (`stt`, `tts`, `llm`) supports an ordered `fallback_providers` list on the agent config:
- **Failure Threshold**: `2` failures (`ErrorFrame`, exception, HTTP 5xx, or timeout > `2500 ms`) within `30 s` opens the circuit for `60 s`.
- **Automatic Switchover**: Active call frames immediately re-route to the next configured fallback provider without dropping the call.
- **Prometheus Metric**: `voxdesk_provider_failover_total{stage="stt|tts|llm",from_provider="...",to_provider="..."}`.
