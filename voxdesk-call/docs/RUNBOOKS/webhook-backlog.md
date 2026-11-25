# Runbook: Webhook Outbox & DLQ Backlog (`webhook-backlog.md`)

## 1. Trigger Conditions & Alerts

| Alert / Signal | Condition | Source |
|---|---|---|
| `VoxDeskStuckSideEffects` | `sum(voxdesk_stuck_side_effects) > 0` for `10m` | `observability/alerts.yml` |
| `VoxDeskSchedulerDown` | `up{job="voxdesk-scheduler"} == 0` for `2m` | `observability/alerts.yml` |
| `VoxDeskJobStale` | `time() - voxdesk_job_last_success_timestamp_seconds > 3600` for `10m` | `observability/alerts.yml` |
| Outbox / Webhook SLO burn | `voxdesk:slo:webhook_delivery_success:5m < 0.995` | `observability/slos.yml` |

---

## 2. Architecture Overview (`app/outbox` + `app/jobs`)

VoxDesk uses a transactional outbox (`outbox_events`) paired with the durable job platform (`jobs` + `webhook_deliveries`):
1. Domain actions write `OutboxEvent` rows (`status = "pending"`) in the **same database transaction** as the business state change.
2. `outbox_dispatch_loop()` in `scripts/scheduler.py` runs `dispatch_due()`, CAS-claiming due events and enqueuing `outbox.delivery` jobs in `jobs`.
3. `durable_jobs_loop()` claims `outbox.delivery` jobs under a lease (`leased_until`), renews heartbeats while running, signs the canonical JSON payload (`HMAC-SHA256`), validates the target URL through `app/core/ssrf.py`, and records per-subscription attempts in `webhook_deliveries`.
4. Events that exhaust `max_attempts` or fail with permanent errors (e.g., SSRF violation, missing secret, 4xx non-retryable response) transition to `status = "dead_letter"`.

---

## 3. Diagnosis Steps

### 3.1 Check Scheduler Liveness
```bash
kubectl get pods -l app.kubernetes.io/name=voxdesk-scheduler
# Or in Docker Compose:
docker compose -f docker-compose.prod.yml ps scheduler
```
If the scheduler pod is down, restart or scale it up immediately. Multiple scheduler instances are safe because `dispatch_due()` and `queue.claim_next()` use conditional compare-and-set updates.

### 3.2 Inspect Outbox Backlog & Error Categories in PostgreSQL
```sql
-- Outbox events by status and error category:
SELECT status, last_error_category, count(*), min(created_at) AS oldest_event
FROM outbox_events
GROUP BY 1, 2
ORDER BY count(*) DESC;

-- Webhook deliveries in dead_letter or queued retry:
SELECT status, last_error_category, http_status, count(*)
FROM webhook_deliveries
WHERE created_at >= now() - interval '24 hours'
GROUP BY 1, 2, 3
ORDER BY count(*) DESC;
```

### 3.3 Common Failure Categories
- `ssrf_blocked` / `invalid_url`: Tenant configured a private IP, loopback, or metadata URL; blocked by `app/core/ssrf.py`. Fix or disable the subscription (`PATCH /api/webhooks/{subscription_id}`).
- `secret_unavailable`: Encryption key ID missing from `CRM_ENCRYPTION_KEYS`; verify key ring and run `python3 scripts/rotate_secrets.py --dry-run`.
- `http_5xx` / `timeout`: Tenant's receiving webhook server is down or timing out (`timeout_seconds = 5`).
- `attempts_exhausted`: Transient failures persisted across all retry backoff rounds (`30s -> 60s -> ... -> 3600s`).

---

## 4. Redrive Procedure

### 4.1 Redrive via Operator API (Tenant-Scoped & Audited)
1. **List dead-lettered webhook deliveries / outbox events**:
   ```bash
   curl -fsS -H "Authorization: Bearer ${TOKEN}" \
     "${API_URL}/api/webhooks/dlq?limit=100"
   ```
2. **Replay a specific dead-lettered webhook delivery**:
   ```bash
   curl -fsS -X POST -H "Authorization: Bearer ${TOKEN}" \
     "${API_URL}/api/webhooks/dlq/${DELIVERY_ID}/redrive"
   ```
3. **Replay a dead-lettered outbox event** via `/api/outbox`:
   ```bash
   curl -fsS -X POST -H "Authorization: Bearer ${TOKEN}" \
     "${API_URL}/api/outbox/events/${EVENT_ID}/replay"
   ```

### 4.2 Bulk SQL Redrive After Downstream Recovery
When a tenant's webhook receiver recovers from a prolonged outage and all `attempts_exhausted` events for that tenant should be redriven:

```sql
BEGIN;

-- Reset queued/dead_letter delivery ledger entries for the affected events:
UPDATE webhook_deliveries
SET status = 'queued',
    attempt = 0,
    next_attempt_at = now(),
    completed_at = NULL
WHERE tenant_id = '<TENANT_UUID>'
  AND status = 'dead_letter'
  AND last_error_category IN ('http_5xx', 'timeout', 'delivery_retryable');

-- Re-admit dead-lettered outbox events back to pending:
UPDATE outbox_events
SET status = 'pending',
    attempt_count = 0,
    available_at = now(),
    last_error = '',
    last_error_category = ''
WHERE tenant_id = '<TENANT_UUID>'
  AND status = 'dead_letter'
  AND last_error_category IN ('attempts_exhausted', 'delivery_retryable', 'http_5xx', 'timeout');

COMMIT;
```

The next `outbox_dispatch_loop` tick (within 15 seconds) will claim the reset events and enqueue fresh `outbox.delivery` jobs. Consumers that already returned `2xx` for an `event_id` will not receive duplicate deliveries because `already_delivered(delivery)` checks the per-subscription ledger first.
