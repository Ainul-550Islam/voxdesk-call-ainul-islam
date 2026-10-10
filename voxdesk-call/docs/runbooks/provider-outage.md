# Runbook: Voice Provider & Carrier Outage (`provider-outage.md`)

## 1. Trigger Conditions & Alerts

| Alert / Metric | Condition | Meaning |
|---|---|---|
| `VoxDeskProviderErrorBurst` | `increase(voxdesk_provider_errors_total[10m]) > 10` for `5m` | STT, LLM, or TTS provider is returning errors or timing out at volume |
| `VoxDeskCallFailureRate` | `failed / total > 0.30` over `10m` | More than 30% of completed calls ended in `CallStatus.FAILED` |
| `voxdesk_provider_failover_total` | `increase(voxdesk_provider_failover_total[5m]) > 0` | Circuit breaker tripped and switched traffic from `from_provider` to `to_provider` |

---

## 2. How Automatic Circuit-Breaker Failover Works (`app/agent/providers/failover.py`)

Every STT, LLM, and TTS stage in the Pipecat voice pipeline is wrapped by `FailoverServiceWrapper`:

- **Failure Detection**: Any `ErrorFrame`, unhandled provider exception, HTTP 5xx, or frame processing timeout exceeding `timeout_ms = 2500.0` ms is recorded on the active `ProviderSlot`.
- **Circuit Trip (`CLOSED -> OPEN`)**: After `failure_threshold = 2` failures within `window_seconds = 30.0` s, the active slot's circuit transitions to `OPEN`, `voxdesk_provider_failover_total{stage,from_provider,to_provider}` is incremented, `provider.failover.switched` is logged, and the wrapper immediately retries the frame on the next healthy fallback provider in `fallback_providers`.
- **Cooldown & Recovery (`OPEN -> HALF_OPEN -> CLOSED`)**: After `cooldown_seconds = 60.0` s, the primary slot transitions to `HALF_OPEN`. A single successful frame closes the circuit (`CLOSED`); a failure re-opens it for another `60.0` s.
- **Process-Wide LLM Circuit (`app/agent/llm_factory.py`)**: In addition to per-call pipeline failover, `llm_factory` maintains a provider circuit breaker across calls (`openai -> anthropic -> google`), skipping providers that have tripped until their cooldown expires.

---

## 3. Triage by Provider Layer

### 3.1 Identify the Failing Provider and Category
Run the following PromQL queries in Grafana / Prometheus:

```promql
# Errors by provider and category (auth, rate_limit, quota, transient, timeout, server_error):
sum by (provider, category) (increase(voxdesk_provider_errors_total[10m]))

# Active failovers by stage and provider pair:
sum by (stage, from_provider, to_provider) (increase(voxdesk_provider_failover_total[10m]))
```

Check structured logs correlated by `call_sid`:
```bash
grep -E '"event":\s*"(provider\.failover\.switched|provider\.failover\.call_failed|call\.crashed)"' /var/log/voxdesk/api.log | tail -n 50
```

### 3.2 STT Outage (Deepgram / AssemblyAI / OpenAI Whisper)
1. **Verify fallback list**: Ensure agents specify `stt_fallback_providers` (e.g., `["assemblyai", "openai"]` when `stt_provider="deepgram"`).
2. **Manual primary switch**: If Deepgram has a prolonged regional outage, update the default STT provider via agent configuration (`PATCH /api/agents/{id}`) so new calls start directly on the healthy provider without waiting for the 2-failure circuit trip.

### 3.3 LLM Outage (OpenAI / Anthropic / Google Gemini / Groq / Bedrock)
1. **Verify API key & quota vs. upstream outage**:
   - `category="auth"` or `category="quota"`: rotate or replenish the provider key via `scripts/rotate_secrets.py` or update the secret in Kubernetes (`voxdesk-runtime`).
   - `category="rate_limit"` or `category="server_error"`: automatic failover in `FailoverServiceWrapper` and `app/agent/llm_factory.py` routes around the degraded provider.
2. **Inspect AI governance circuit status**: Check `/health/dependencies` to confirm at least one LLM provider is configured and installed.

### 3.4 TTS Outage (ElevenLabs / Cartesia / OpenAI / PlayHT / AWS Polly)
1. **Verify fallback list**: Ensure agents configure `tts_fallback_providers` (e.g., `["cartesia", "openai"]`).
2. **Manual override**: Patch affected agents' `tts_provider` to `"cartesia"` or `"openai"` until ElevenLabs status recovers.

### 3.5 Telephony Carrier Outage (Twilio / Telnyx)
1. **Inbound calls**:
   - Twilio inbound webhooks hit `POST /telephony/voice` and open `WSS /telephony/ws`.
   - Telnyx inbound webhooks hit `POST /telephony/telnyx/events` and open `WSS /telephony/telnyx/ws`.
   - If the primary carrier experiences a SIP/PSTN outage, activate carrier failover routing at the SIP trunk / DID forwarding layer to route inbound calls to the secondary carrier's numbers bound to the same tenant.
2. **Outbound calls & campaigns**:
   - Pause active outbound campaigns (`POST /api/batch-calls/{batch_id}/pause`) if carrier dial errors spike, switch the tenant's outbound carrier configuration, and resume (`POST /api/batch-calls/{batch_id}/resume`).

---

## 4. Recovery Verification

1. Confirm `increase(voxdesk_provider_errors_total[5m]) == 0`.
2. Place a synthetic verification call against the staging/local stack:
   ```bash
   python3 loadtest/voice_ws_user.py --self-test
   ```
3. Confirm `VoxDeskProviderErrorBurst` and `VoxDeskCallFailureRate` alerts resolve.
