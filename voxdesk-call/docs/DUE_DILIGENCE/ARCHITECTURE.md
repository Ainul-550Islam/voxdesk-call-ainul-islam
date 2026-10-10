# VoxDesk — System Architecture & Data-Flow Diagrams

This document provides the due-diligence architecture reference for the VoxDesk
Voice AI Platform (`voxdesk-call`), with five Mermaid diagrams covering every
runtime plane:
1. **Live PSTN Call Path** (`/telephony/voice` -> `/telephony/ws` -> Pipecat 0.0.94 pipeline)
2. **Multi-Tenant Control Plane & Identity Boundary**
3. **Durable Jobs & Transactional Outbox (`SELECT ... FOR UPDATE SKIP LOCKED`)**
4. **Polyglot Realtime Gateway, Rust Media Engine SFU & Signaling Hub**
5. **Browser Web-Call & Embeddable Widget Path**

---

## 1. Live PSTN Call Path (`/telephony/voice` & `/telephony/ws`)

```mermaid
sequenceDiagram
    autonumber
    participant Caller as PSTN Caller / Twilio
    participant API as FastAPI Telephony Handler<br/>(app/telephony/twilio_handler.py)
    participant DB as PostgreSQL 16<br/>(Call, Turn, CallLatencyStat)
    participant Pipe as Pipecat 0.0.94 Pipeline<br/>(app/agent/pipeline.py)
    participant STT as FailoverSTTService<br/>(Deepgram -> AssemblyAI/Whisper)
    participant LLM as FailoverLLMService + FlowProcessor<br/>(OpenAI/Anthropic/Gemini)
    participant TTS as FailoverTTSService + AmbientMixer<br/>(ElevenLabs -> OpenAI/Cartesia)
    participant Mon as MonitorBus<br/>(Listen / Whisper / Takeover)

    Caller->>API: POST /telephony/voice (X-Twilio-Signature verified)
    API->>DB: Resolve PhoneNumber -> AgentVersion / ExperimentVariant + Insert Call + call_started Outbox
    API-->>Caller: 200 OK TwiML (<Connect><Stream url="wss://.../telephony/ws?token=..."/></Connect>)
    Caller->>API: WebSocket Upgrade /telephony/ws (single-use HMAC stream token)
    API->>Pipe: Initialize Pipeline(FastAPIWebsocketTransport, Denoise, STT, TurnTaking, FlowProcessor, LLM, Pronunciation, TTS, AmbientMixer, MonitorTap, LatencyObserver)
    loop Every Conversational Turn (20 ms G.711 mu-law 8 kHz frames)
        Caller->>Pipe: Inbound media frames (mu-law 8 kHz -> PCM16)
        Pipe->>Mon: Zero-copy mirror frame (only when supervisor subscribed)
        Pipe->>STT: Stream audio frames -> interim/final TranscriptionFrame
        STT-->>Pipe: Final TranscriptionFrame + PII redaction
        Pipe->>LLM: SmartTurnDetector + FlowProcessor (node transitions, HTTP/MCP tools)
        LLM-->>Pipe: Streaming token deltas (TTFT recorded by LatencyObserver)
        Pipe->>TTS: Pronunciation Normalizer -> TTS synthesis + Ambient sound bed mix
        TTS-->>Caller: Outbound media frames (PCM16 -> mu-law 8 kHz) + Clear on barge-in
    end
    Caller->>API: WebSocket stop / POST /telephony/status
    API->>DB: Persist CallLatencyStat + Enqueue Post-Call Analysis & Auto-QA Jobs + call_ended Outbox
```

---

## 2. Multi-Tenant Control Plane & Identity Boundary

```mermaid
flowchart TB
    subgraph External["External Clients & Identity Providers"]
        AdminUI["React Dashboard (dashboard/) &<br/>Next.js Console (dashboard-next/)"]
        SDKs["SDK Clients<br/>(@voxdesk/node-sdk, @voxdesk/web-sdk, voxdesk-python)"]
        IdP["Enterprise IdP<br/>(Keycloak / Okta / Entra ID: OIDC+PKCE, SAML 2.0, SCIM 2.0)"]
    end

    subgraph Middleware["FastAPI Security & Tenancy Gate (app/main.py)"]
        Drain["Graceful Shutdown Drain Gate<br/>(app/core/graceful_shutdown.py)"]
        Auth["Auth & Session Validator<br/>(JWT / API Key / SCIM Bearer / MFA)"]
        RBAC["Fine-Grained RBAC & TenantContext<br/>(tenant_id + environment_id)"]
        Idem["Durable Idempotency Receipts<br/>(RequestIdempotencyReceipt)"]
    end

    subgraph Storage["Durable Storage & Secret Layer"]
        PG[("PostgreSQL 16<br/>239 Tables @ 0062_drop_pcap_artifacts")]
        Redis[("Redis 7<br/>Rate Limits, CPS Token Bucket, Circuit Breakers")]
        KMS["Secret Store & Envelope Crypto<br/>(AES-256-GCM: k1/k2 key ring)"]
    end

    AdminUI --> Drain
    SDKs --> Drain
    IdP --> Drain
    Drain --> Auth --> RBAC --> Idem
    RBAC --> PG
    RBAC --> Redis
    RBAC --> KMS
```

---

## 3. Durable Jobs & Transactional Outbox (`SELECT ... FOR UPDATE SKIP LOCKED`)

```mermaid
sequenceDiagram
    autonumber
    participant Route as API Route / Telephony Callback
    participant PG as PostgreSQL 16<br/>(Same DB Transaction)
    participant Sched as Scheduler Worker<br/>(scripts/scheduler.py)
    participant SSRF as SSRF & DNS Guard<br/>(app/core/ssrf.py)
    participant Ext as Customer Webhook Receiver / Salesforce / LLM

    Route->>PG: BEGIN; Mutate domain row (Call / DNC / Review / Batch)
    Route->>PG: INSERT AuditLog + OutboxEvent / Job (status='pending')
    Route->>PG: COMMIT (Atomic: audit/outbox failure rolls back domain mutation)

    loop Worker Poll Loop (Multi-Worker Safe)
        Sched->>PG: SELECT ... FROM outbox_events / jobs WHERE status='pending' FOR UPDATE SKIP LOCKED
        PG-->>Sched: Claimed batch (locked to worker instance)
        Sched->>SSRF: Validate target URL + resolved A/AAAA records (block RFC1918/loopback/169.254.169.254)
        SSRF-->>Sched: Validated public endpoint
        Sched->>Ext: Signed HTTP POST (X-VoxDesk-Signature: t=...,v1=<hmac_sha256>)
        alt HTTP 2xx Success
            Ext-->>Sched: 200 OK
            Sched->>PG: Mark status='delivered', record latency & HTTP status
        else Transient 5xx / Timeout
            Ext-->>Sched: 503 / Timeout
            Sched->>PG: Increment attempts, schedule exponential backoff retry (or move to 'dlq' at max_attempts)
        end
    end
```

---

## 4. Polyglot Realtime Gateway, Rust Media Engine SFU & Signaling Hub

```mermaid
flowchart LR
    subgraph Clients["WebRTC / Signaling Peers"]
        Browser["Browser WebRTC Peer"]
        Supervisor["Supervisor Console"]
    end

    subgraph GoPlane["Go Realtime & Signaling Services"]
        GW["services/realtime/gateway-go<br/>(WebSocket/WebRTC Gateway, Auth, Backpressure)"]
        SigGo["services/signal-go<br/>(Room Signaling Hub)"]
        OpsGo["services/ops (voxops)<br/>(Operational Health & Backup CLI)"]
    end

    subgraph RustPlane["Rust Control & Media Planes"]
        CtrlRs["services/control-plane<br/>(voxdesk-control + voxdesk-signal)"]
        MediaRs["services/realtime/media-engine-rs<br/>(DTLS-SRTP RFC 5764 over dimpl 0.7.3,<br/>AES-CM/GCM, RTP/RTCP, G.711 Mixer)"]
    end

    subgraph CppPlane["C++17 DSP Library"]
        MediaCpp["services/media-plane<br/>(Adaptive Jitter Buffer, FFT Spectral Denoise,<br/>VAD, Consistent-Hash Router)"]
    end

    Browser <-->|"WSS / ICE-Lite / DTLS-SRTP"| GW
    Supervisor <-->|"WSS Room Subscribe"| SigGo
    GW <-->|"Protobuf Control Wire (contracts/*.proto)"| MediaRs
    SigGo <-->|"Differential State Sync"| CtrlRs
    MediaRs --- MediaCpp
```

---

## 5. Browser Web-Call & Embeddable Widget Path

```mermaid
sequenceDiagram
    autonumber
    participant Page as Customer Webpage<br/>(<script src="/widget/v1/embed.js">)
    participant Widget as Shadow DOM Widget /<br/>@voxdesk/web-sdk
    participant API as FastAPI Web-Call API<br/>(app/api/web_call_routes.py)
    participant WS as Web-Call Media Transport<br/>(/api/v1/telephony/web-calls/{id}/ws)
    participant Pipe as Pipecat Voice Pipeline<br/>(Shared with PSTN Agent)

    Page->>Widget: Mount floating call button (data-public-key="pk_live_...")
    Widget->>API: POST /api/v1/telephony/web-calls/public (Origin header checked against allowed_origins)
    API->>API: Enforce per-key rate limit + Mint 60s single-use JWT (jti stored in DB/Redis)
    API-->>Widget: 201 Created {call_id, ws_url, client_token, expires_at}
    Widget->>WS: WSS Connect /api/v1/telephony/web-calls/{call_id}/ws?token=<client_token>
    WS->>WS: Consume single-use jti (replay rejected with 401/4003)
    loop Full-Duplex Browser Audio + Transcript Events
        Widget->>WS: PCM16 / Opus audio frames + control frames (mute / dtmf / end_call)
        WS->>Pipe: Feed caller audio into Pipecat pipeline
        Pipe-->>WS: Agent audio frames + live transcript & state events
        WS-->>Widget: Stream audio to WebAudio playback + update UI state
    end
    Widget->>WS: Disconnect / Hangup
    WS->>API: Record Call duration, billing usage event, and enqueue post-call analysis
```
