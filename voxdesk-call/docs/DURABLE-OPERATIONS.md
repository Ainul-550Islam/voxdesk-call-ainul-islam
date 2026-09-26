# Durable operations

PostgreSQL is the source of truth for background jobs, automation action
receipts, inbox state, notification attempts, webhook deliveries and email
delivery facts. Process memory is a request cache. Redis, when present, may
coordinate a transient lock. It is not the queue and it is not the idempotency
record.

This is at-least-once worker processing with idempotent side-effect protection.
It is not exactly-once external delivery, and it is not an uptime claim.

## What is stored

`0020_durable_enterprise_operations` revises
`0019_environment_scope_business_resources`. It creates `jobs`, `job_attempts`,
`job_idempotency`, `automation_actions`, `notification_deliveries`,
`notification_preferences`, `webhook_subscriptions`, `webhook_deliveries` and
`email_deliveries`. It adds a version and SLA columns to the existing
`inbox_thread_states` table. It does not recreate `automations`,
`automation_runs`, `notifications`, `notification_templates` or
`inbox_thread_states`.

Existing inbox rows receive `version = 1` and empty SLA fields from the column
defaults. No row is copied onto another tenant. Jobs are not added to the
environment resource binder, because a job's `environment_id` may be null.

## Workers, leases and recovery

A worker claims one row. On PostgreSQL the claim is `SELECT … FOR UPDATE SKIP
LOCKED`. On other dialects, including the SQLite test database, the claim is a
compare-and-set update: `UPDATE jobs SET status = 'running' WHERE id = ? AND
status IN ('queued', 'retry_scheduled')`. Exactly one update matches. The loser
gets zero rows and does not run the handler.

The claim sets `leased_until`. A heartbeat extends that timestamp for the same
`worker_id`. A crash leaves the row `running` until the lease expires.
`recover_expired_jobs` returns those rows to `queued`. Work is not deleted
because a process died.

Shutdown can expire the current worker's lease so another worker may recover
the job. Inbound call handling does not wait on this loop.

## Retry and backoff

Retryable categories are connection failures, timeouts, HTTP 408, 429, 500,
502, 503, 504 and transient database errors. Delay is
`min(3600, 30 * 2^(attempt - 1))` seconds, reduced by up to 20 percent from a
hash of the idempotency key so retries do not align.

Permanent categories are validation failures, authorization failures, missing
records, environment or tenant mismatch, invalid configuration, and other HTTP
4xx responses. A permanent failure goes to the dead-letter state on the first
failure. A retryable failure goes there when `attempt_count` reaches
`max_attempts`. Nothing is retried forever.

## Dead letter and replay

A dead-letter job, notification or webhook delivery can be replayed by a role
that holds `campaign:run`. A viewer is rejected. Replay does not accept a new
organization, tenant, environment or subscription. A cross-tenant id is a
not-found boundary denial, not a distinct existence error. Each row has a
replay budget. Replay is another attempt of the same row, not a copy into
another tenant.

## Idempotency

Job enqueue is unique on `(tenant_id, idempotency_key)`. Automation side effects
are unique on `(tenant_id, automation_id, business_event_id, action_id)`.
Webhook delivery is unique on `(subscription_id, event_id)`. Notification
attempts are unique on `(notification_id, attempt_number)`. The application
inserts inside a savepoint and returns the existing row on conflict. The unique
constraint is the race protection. A check-then-insert is not.

## Isolation

Every environment-scoped read names `tenant_id` and `environment_id`. A staging
automation cannot resolve a production target, and a production automation
cannot resolve a staging target. A job whose payload names a different
environment is dead-lettered before the handler runs. Notification attribution
stays on the notification row's tenant and environment.

## Webhooks and email

Webhook bodies are signed as `t=<unix>,v1=<hmac-sha256>` over `timestamp.body`.
The signing secret is sealed with the existing identity cipher when a key ring
is configured. Production refuses to store a plaintext secret. Secrets are not
written to logs and are not placed in URLs.

The destination is checked with `app.core.ssrf.validate_outbound_url` before a
socket is opened. Redirects are not followed. A 3xx response is a permanent
rejection.

Email uses the configured SMTP transport. `EMAIL_TRANSPORT=log` is already
rejected in production, and the delivery adapter refuses that combination
again. The email delivery table stores a hash of the recipient, not the
address. A test double is not a production provider.

## What this does not do

It does not add environment-specific credentials, API keys, service accounts,
environment billing, regional routing, Terraform, Kubernetes, KMS, WAF, a
dashboard redesign, an SDK, or realtime regional routing. The next phase is the
enterprise AI control plane, not another pass on this queue.
