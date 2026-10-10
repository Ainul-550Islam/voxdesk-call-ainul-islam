# VoxDesk — SLOs and Alerts (Step 7 & Part 8 / Gate G9)

Two questions, one document: what service levels VoxDesk measures and targets
(SLOs), and what wakes an operator when we are about to breach them (alerts &
runbooks).

---

## 1. Service Level Objectives (SLOs)

Measured continuously by the Prometheus recording rules in
`observability/slos.yml`:

| SLO | Target | Recording Rules (`5m` live / `30d` rolling) | Underlying Metric | Runbook |
|---|---|---|---|---|
| **HTTP API Availability** | `>= 99.9%` monthly | `voxdesk:slo:availability:5m` / `voxdesk:slo:availability:30d` | `voxdesk_http_requests_total` | `docs/RUNBOOKS/db-failover.md` |
| **Voice E2E Turn Latency (p95)** | `<= 1.2s` (`1200 ms`) | `voxdesk:slo:voice_e2e_latency_p95:5m` / `voxdesk:slo:voice_e2e_latency_p95:30d` | `voxdesk_voice_e2e_latency_seconds_bucket` (from 2A `LatencyObserver`) | `docs/RUNBOOKS/high-latency.md` |
| **Webhook Delivery Success** | `>= 99.5%` monthly | `voxdesk:slo:webhook_delivery_success:5m` / `voxdesk:slo:webhook_delivery_success:30d` | `voxdesk_external_side_effects_total{kind="message_webhook"}` | `docs/RUNBOOKS/webhook-backlog.md` |
| **Post-Call Completion Time (p95)** | `<= 10.0s` | `voxdesk:slo:post_call_completion_p95:5m` / `voxdesk:slo:post_call_completion_p95:30d` | `voxdesk_runtime_event_duration_seconds_bucket{component="workflow",event="execution"}` | `docs/RUNBOOKS/high-latency.md` |

### Definitions & PromQL Queries

1. **HTTP API Availability (`>= 99.9%`)**:
   - **Formula**: `1 - (5xx responses / all HTTP responses)`. Client `4xx` responses (auth failure, validation error, 404) are normal protocol outcomes and are excluded from the error numerator.
   - **PromQL**:
     ```promql
     voxdesk:slo:availability:5m
     voxdesk:slo:availability:30d
     # Error budget remaining (fraction of traffic):
     voxdesk:slo:availability:30d - 0.999
     ```

2. **Voice E2E Turn Latency p95 (`<= 1.2s`, Sub-Phase 2A)**:
   - **Formula**: 95th percentile of `voxdesk_voice_e2e_latency_seconds` measured by `LatencyObserver` (`app/agent/latency.py`) from caller end-of-speech (`UserStoppedSpeakingFrame`) to first outbound bot audio frame (`BotStartedSpeakingFrame`). In the deterministic synthetic benchmark (`docs/LATENCY_BENCHMARK.md`, `N=200` calls), harness `e2e_p95_ms` is `323.328 ms`; the production SLO includes live STT/LLM/TTS network round-trips (`<= 1.2s` warning, `<= 2.0s` critical).
   - **PromQL**:
     ```promql
     voxdesk:slo:voice_e2e_latency_p95:5m
     voxdesk:slo:voice_e2e_latency_p95:30d
     ```

3. **Webhook Delivery Success (`>= 99.5%`)**:
   - **Formula**: `1 - (failed webhook deliveries / total completed webhook delivery attempts)`. Backed by the transactional outbox (`app/outbox/dispatcher.py`) and durable job worker (`app/jobs/worker.py`).
   - **PromQL**:
     ```promql
     voxdesk:slo:webhook_delivery_success:5m
     voxdesk:slo:webhook_delivery_success:30d
     ```

4. **Post-Call Completion Time p95 (`<= 10.0s`)**:
   - **Formula**: 95th percentile of post-call workflow and structured analysis execution duration (`voxdesk_runtime_event_duration_seconds_bucket{component="workflow",event="execution"}`) from call termination to persisted post-call artifacts and enqueued outbox events.
   - **PromQL**:
     ```promql
     voxdesk:slo:post_call_completion_p95:5m
     voxdesk:slo:post_call_completion_p95:30d
     ```

**Honesty about the 30d window.** A fresh Prometheus instance has no 30 days of retained samples, so `:30d` rules read optimistically high until the retention window fills. Use `:5m` for real-time operations and alerting, and `:30d` for monthly SLO reporting.

---

## 2. Alerts (`observability/alerts.yml`)

Every alert in `observability/alerts.yml` maps to a concrete operator action and runbook:

| Alert | Severity | Condition | Dedicated Runbook |
|---|---|---|---|
| `VoxDeskInstanceDown` | `critical` | API unscrapeable `2m` | `#voxdeskinstancedown` |
| `VoxDeskSchedulerDown` | `critical` | scheduler unscrapeable `2m` | `docs/RUNBOOKS/webhook-backlog.md` |
| `VoxDeskDatabaseDown` | `critical` | `voxdesk_db_up == 0` for `1m` | `docs/RUNBOOKS/db-failover.md` |
| `VoxDeskHighErrorRate` | `warning` | HTTP `5xx > 5%` over `5m` | `#voxdeskhigherrorrate--voxdeskslowp95` |
| `VoxDeskSlowP95` | `warning` | HTTP `p95 > 2s` over `5m` | `docs/RUNBOOKS/high-latency.md` |
| `VoxDeskCallFailureRate` | `warning` | `> 30%` of finished calls failed over `10m` | `docs/RUNBOOKS/provider-outage.md` |
| `VoxDeskProviderErrorBurst` | `warning` | `> 10` provider errors in `10m` | `docs/RUNBOOKS/provider-outage.md` |
| `VoxDeskStuckSideEffects` | `critical` | any stuck side effect for `10m` | `docs/RUNBOOKS/webhook-backlog.md` |
| `VoxDeskJobStale` | `warning` | frequent scheduler loop silent `> 1h` | `docs/RUNBOOKS/webhook-backlog.md` |
| `VoiceLatencyP95Degraded` | `warning` | Voice E2E `p95 > 1.2s` for `10m` | `docs/RUNBOOKS/high-latency.md` |
| `VoiceLatencyP95Critical` | `critical` | Voice E2E `p95 > 2.0s` for `2m` | `docs/RUNBOOKS/high-latency.md` |

### Alert Summaries & Immediate Actions

#### VoxDeskInstanceDown
* **What it means:** Prometheus cannot scrape `/metrics` from the API.
* **Check:** `docker compose -f docker-compose.prod.yml ps api` or `kubectl get pods -l app.kubernetes.io/name=voxdesk`; inspect container logs (`--tail 200`) for OOMKills or startup validation errors.
* **Act:** Restart the service; if a deploy introduced a boot failure, roll back (`scripts/rollback.sh` or `helm rollback voxdesk`).

#### VoxDeskSchedulerDown
* **What it means:** The background worker (`scripts/scheduler.py` — reminders, campaigns, CRM sync, billing reconciliation, retention, durable jobs, outbox dispatch) is unscrapeable.
* **Impact:** Live calls still answer; asynchronous side effects queue in PostgreSQL.
* **Act:** Follow [`docs/RUNBOOKS/webhook-backlog.md`](RUNBOOKS/webhook-backlog.md). Restarting the scheduler is safe because all loops and job claims are idempotent and lease-guarded.

#### VoxDeskDatabaseDown
* **What it means:** `/health/ready` is reporting the database unreachable (`voxdesk_db_up == 0`), and readiness gates are draining the node.
* **Act:** Follow [`docs/RUNBOOKS/db-failover.md`](RUNBOOKS/db-failover.md). Check `pg_isready`, connection pool limits, standby promotion, or restore using `scripts/dr_drill.sh` / `scripts/restore.sh`.

#### VoxDeskHighErrorRate / VoxDeskSlowP95
* **What it means:** Either a code regression, worker CPU saturation, or a degraded downstream dependency.
* **Act:** Follow [`docs/RUNBOOKS/high-latency.md`](RUNBOOKS/high-latency.md). Correlate the 5xx/latency spike with deploy timestamps and `request_id` structured logs.

#### VoxDeskCallFailureRate
* **What it means:** More than 30% of finished calls are `failed` (`no_answer` is excluded as normal caller behavior).
* **Act:** Follow [`docs/RUNBOOKS/provider-outage.md`](RUNBOOKS/provider-outage.md). Inspect `voxdesk_provider_errors_total` and `voxdesk_provider_failover_total` by stage (`stt`, `llm`, `tts`).

#### VoxDeskProviderErrorBurst
* **What it means:** A voice provider (Deepgram, ElevenLabs, OpenAI, Anthropic, Gemini) is failing at volume.
* **Act:** Follow [`docs/RUNBOOKS/provider-outage.md`](RUNBOOKS/provider-outage.md). Verify automatic failover via `FailoverServiceWrapper` (`app/agent/providers/failover.py`) and switch primary provider if an upstream outage is prolonged.

#### VoxDeskStuckSideEffects
* **What it means:** Reminders, CRM syncs, or document ingestions have remained in-flight past their lease recovery window.
* **Act:** Follow [`docs/RUNBOOKS/webhook-backlog.md`](RUNBOOKS/webhook-backlog.md). Inspect `outbox_events`, `jobs`, and `webhook_deliveries` and redrive dead-lettered items once the downstream endpoint is healthy.

#### VoxDeskJobStale
* **What it means:** One of the frequent scheduler loops (`reminders`, `campaigns`, `crm_sync`, `knowledge`) has not succeeded in over an hour.
* **Act:** Follow [`docs/RUNBOOKS/webhook-backlog.md`](RUNBOOKS/webhook-backlog.md). Inspect `scheduler.*_failed` logs.

---

## 3. Alerting Philosophy

No noisy informational rules. Every alert represents a customer-impacting condition or imminent SLO burn, carries an explicit severity, and links to an executable runbook in `docs/RUNBOOKS/`.
