# Runbook: High Voice & API Latency (`high-latency.md`)

## 1. Trigger Conditions & Alerts

| Alert | Severity | Condition | Target SLO |
|---|---|---|---|
| `VoiceLatencyP95Degraded` | `warning` | `p95(voxdesk_voice_e2e_latency_seconds) > 1.2s` for `10m` | `p95 <= 1.2s` (`voxdesk:slo:voice_e2e_latency_p95:5m`) |
| `VoiceLatencyP95Critical` | `critical` | `p95(voxdesk_voice_e2e_latency_seconds) > 2.0s` for `2m` | `p95 <= 1.2s` |
| `VoxDeskSlowP95` | `warning` | `p95(voxdesk_http_request_duration_seconds) > 2.0s` for `5m` | HTTP p95 `< 2.0s` |

---

## 2. Dashboards & Metrics to Inspect First

1. **Grafana Voice Latency Dashboard (`observability/grafana/voice-latency.json`)**:
   - **End-to-End Turn Latency (`voxdesk_voice_e2e_latency_seconds_bucket`)**: measures elapsed time from `UserStoppedSpeakingFrame` (Silero VAD / SmartTurn V3 end-of-turn) to `BotStartedSpeakingFrame` (first outbound audio frame).
   - **Per-Stage TTFB Breakdown (`voxdesk_voice_ttfb_seconds_bucket`)**:
     ```promql
     histogram_quantile(0.95, sum(rate(voxdesk_voice_ttfb_seconds_bucket[5m])) by (le, stage, provider))
     ```
     Immediately isolates whether the regression is in:
     - `stage="stt"` (Deepgram / AssemblyAI / Whisper transcription latency)
     - `stage="llm"` (OpenAI / Anthropic / Gemini / Groq time-to-first-token)
     - `stage="tts"` (ElevenLabs / Cartesia / OpenAI / PlayHT first audio chunk)
2. **Grafana Overview Dashboard (`observability/grafana/dashboards/voxdesk.json`)**:
   - Check `voxdesk_active_calls` per pod against the single-worker capacity knee point documented in `docs/CAPACITY_MODEL.md`.
   - Check `voxdesk_provider_failover_total` and `voxdesk_provider_errors_total` to see if retries/timeouts (`timeout_ms=2500`) are inflating tail latency before circuit breakers open.
3. **Database Per-Call Latency Ledger (`call_latency_stats`)**:
   ```sql
   SELECT stt_provider, llm_provider, tts_provider,
           count(*) AS calls,
          round(avg(e2e_p50_ms)::numeric, 1) AS avg_p50_ms,
          round(avg(e2e_p95_ms)::numeric, 1) AS avg_p95_ms,
          round(max(e2e_max_ms)::numeric, 1) AS max_e2e_ms
   FROM call_latency_stats
   WHERE created_at >= now() - interval '15 minutes'
   GROUP BY 1, 2, 3
   ORDER BY avg_p95_ms DESC;
   ```

---

## 3. Common Root Causes & Mitigations

### Cause A: Upstream STT / LLM / TTS Provider Degradation
- **Symptom**: `voxdesk_voice_ttfb_seconds{stage="llm"}` or `{stage="tts"}` p95 spikes above `600ms` while host CPU is below 70%.
- **Automatic Mitigation**: `FailoverServiceWrapper` (`app/agent/providers/failover.py`) trips open after `2` failures or timeouts (`> 2500ms`) within `30s` and switches to the configured fallback provider for `60s`.
- **Manual Mitigation**: If a provider is slow (`800ms–2000ms`) without hard-failing or timing out at `2500ms`, promote the secondary provider to primary on affected agents via `PATCH /api/agents/{agent_id}` or `docs/RUNBOOKS/provider-outage.md`.

### Cause B: Worker CPU Contention (Audio Denoise / Silero VAD / SmartTurn V3)
- **Symptom**: `voxdesk_active_calls` per pod exceeds the knee point (`docs/CAPACITY_MODEL.md`), CPU utilization exceeds `80%`, and all pipeline stages show uniform queueing delay.
- **Mitigation**:
  1. Verify HorizontalPodAutoscaler (`infra/helm/voxdesk/templates/hpa.yaml`) is scaling out API pods on `voxdesk_active_calls` (`targetConcurrentCallsPerPod`).
  2. Temporarily scale out API replicas manually:
     ```bash
     kubectl scale deployment/voxdesk --replicas=6
     ```
  3. If CPU remains constrained during a traffic spike, disable spectral noise reduction (`denoise_enabled: false`) on high-volume agents (`noisereduce` FFT processing is the largest per-frame CPU consumer on the inbound audio path).

### Cause C: Database Pool Contention or Slow Turn Persistence
- **Symptom**: `VoxDeskSlowP95` fires alongside `VoiceLatencyP95Degraded`; `/telephony/voice` call setup takes `> 500ms`.
- **Mitigation**:
  1. Check `pg_stat_activity` for lock contention or slow queries (`docs/RUNBOOKS/db-failover.md`).
  2. Verify `DB_POOL_SIZE` and `DB_MAX_OVERFLOW` match the number of API workers (`WEB_CONCURRENCY`).
