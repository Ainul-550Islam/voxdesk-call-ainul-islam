# Batch 04 contact-center operations

## Scope and ownership

Batch 04 extends the existing contact-center, telephony, knowledge, monitoring, identity, and durable-job primitives. It does not create a second durable queue or a parallel contact-center service.

- **A — outbound dialing:** preview, progressive, predictive, and AI-agent modes; compliance prechecks; single-claim attempts; pacing; agent availability; rolling abandonment accounting; and safe-harbor recovery.
- **B — human softphone:** browser WebRTC registration, short-lived tokens and WebSocket tickets, signed provider media, call controls, ICE/TURN, the TypeScript SDK, and the Next.js agent desktop.
- **C — agent assist:** redacted final transcript turns, cited knowledge retrieval, governed suggestions and next-best actions, compliance and sentiment alerts, audited feedback, and explicitly approved wrap-up/CRM writeback.
- **D — ACD and supervision:** skills/priority/longest-idle routing, queues/EWT/SLA/overflow, callbacks, wallboards, and realtime statistics.
- **E — accounting and feeding:** queued/scheduled callback recovery, durable agent-state intervals, occupancy/adherence reads, and idempotent dialer feeding.

## Install once

1. Apply Batches 01–03, back up PostgreSQL, and confirm `alembic current` is exactly `0065_compliance_v2`. From the extracted patch run `bash apply_batch04.sh /path/to/voxdesk-call`. The installer stops before changing files on a wrong database revision, applies anchored edits, upgrades to `0066_contact_center_v2`, and runs non-live gates. Do not run two installers concurrently.
2. Set `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `CC2_SOFTPHONE_TWILIO_API_KEY_SID`, `CC2_SOFTPHONE_TWILIO_API_KEY_SECRET`, and `CC2_SOFTPHONE_TWILIO_TWIML_APP_SID` through the runtime secret. Set `PUBLIC_BASE_URL` to the HTTPS API origin. Configure the TwiML App Voice URL as `https://API/telephony/voice?cc2=softphone-agent` (POST). Keep Twilio signature verification enabled; do not enable the development bypass in production.
3. Run the API, canonical durable scheduler/JobWorker, and Next.js dashboard together. The scheduler imports `app/contact_center_v2/jobs.py` for durable handlers and invokes `app/contact_center_v2/worker.py` for CC2 maintenance. The human desktop is `/agent-desktop`; permit the pinned `cdn.jsdelivr.net` script and Twilio Voice endpoints in CSP. Register with the active default production-environment UUID.

## Scheduler and recovery

The continuous maintenance loop runs every `min(CC2_PACING_TICK_MS / 1000, CC2_ACD_OVERFLOW_SWEEP_SECONDS)`. Its maintenance queries use bounded batches and report real row transitions. With `REDIS_URL` configured, named maintenance guards prevent overlapping passes across processes; without Redis, the local guard is valid only inside one process. Production must also retain the canonical durable JobWorker for carrier-facing jobs.

`app/contact_center_v2/jobs.py` exposes these scheduler entrypoints:

| Entrypoint | Operation |
| --- | --- |
| `pacing_tick_guard` | Admits the current durable tick for running campaigns using the campaign/bucket idempotency key. It never calls a carrier itself. |
| `abandon_rollup` | Reconciles durable attempt transitions into the 30-day abandonment ledger and verifies the rebuildable Redis projection against PostgreSQL. |
| `callback_dispatch_tick` | Recovers due scheduled callbacks whose durable dispatch row is absent. Running or ambiguous callbacks are not redialed. |
| `occupancy_rollup` | Closes contradictory open agent-state intervals using the last presence observation; it does not invent occupancy for active agents. |
| `stale_device_reaper` | Disables devices whose heartbeat expired, expires their active registrations, and releases active-device keys. |
| `preview_reservation_reaper` | Expires bounded batches of preview leases and returns only undialed assigned contacts to the eligible pool. |

The same worker also expires softphone sessions, enforces unanswered human-call agent-join deadlines, runs queue overflow sweeps, and reconciles provider-acknowledged CRM writebacks. Durable `cc2.dialer.*`, `cc2.assist.turn`, and callback jobs remain owned by the canonical `app.jobs.worker`.

Optional `contactCenterWorkers.enabled=true` moves this continuous maintenance loop to one `python -m app.contact_center_v2.worker` pod and sets `CC2_MAINTENANCE_ENABLED=false` on the scheduler; durable jobs remain singular and `scheduler.enabled` must stay true. Do not autoscale this maintenance pod from CPU and do not replace it with a periodic CronJob: neither increases durable dialing throughput, and a periodic-only loop cannot provide timely safe-harbor recovery. Scale the canonical durable JobWorker instead. The chart rejects those unsafe worker overrides.

## Calling controls

- Prepare every dialer before starting it. `GET /api/cc2/dialer/policies/{id}/precheck` is read-only even if a caller supplies `refresh=true`; use audited `POST` with an `Idempotency-Key` to refresh evidence. Replayed prepare/refresh keys do not repeat mutations.
- Recipients need a valid IANA timezone, a current DNC scrub with a matching tamper-evident recipient fingerprint, an allowed local calling window, and current affirmative voice consent for predictive/progressive mode. Caller ID must be tenant-owned, voice-capable, recently provider-verified, and attested. These checks fail closed.
- Automated modes require a safe-harbor message that identifies the business, includes the verified E.164 callback number, and gives an explicit opt-out instruction. It is played only through the signed provider path when no agent can join. Human calls unanswered by an agent require handling within two seconds; machine answers are excluded.
- The rolling 30-day abandonment cap defaults to 3% per campaign; lower is allowed. A higher cap requires an unexpired audited jurisdiction override and never exceeds the configured hard limit. Predictive starts and ticks refuse when the measured cap is breached. Progressive reserves an agent; preview dials only after the assigned agent clicks.
- `provider_outcome_ambiguous`, `dial_failed`, or `post_provider_persistence_ambiguous` is **not** a retry instruction. Pause the affected work, reconcile the provider SID/webhooks, and involve engineering if a running callback remains blocked. Never place a second call to “test” an unknown first outcome.

## WebRTC and media

Coturn is disabled by default. To enable it, set the same high-entropy `CC2_TURN_STATIC_SECRET` in the API and coturn secrets, plus `coturn.externalIP`, realm, and optional TLS secret. Permit UDP/TCP 3478, TCP 5349 when TLS is enabled, and the configured relay range (default UDP 49152–49252) to the host-networked coturn nodes. Restrict relay peers by network policy and keep the relay range closed to untrusted inbound destinations. Browser ICE credentials are short-lived HMAC credentials; no TURN secret is sent to the browser. Coturn operators can start from `infra/coturn/turnserver.conf.example`.

Registration, signaling, media, and call-control APIs remain tenant-scoped and permission-checked. Identity revocation also revokes CC2 softphone credentials in the canonical identity transaction. Use the SDK and desktop connection state rather than inferring call success from a requested provider operation.

## Assist, ACD, and accounting

Assist streams contain redacted final turns, cited suggestions, and alerts only. A newer committed transcript turn supersedes stale generation before suggestions are persisted or published. Wrap-up submission is explicit human approval: CRM state is `not_requested` without a subscribed integration, `pending` until canonical provider sync acknowledges external IDs, then `written` or `failed` after reconciliation.

Configure ACD callback, queue, escalation, or voicemail overflow through the queue-policy API. Callback execution rechecks DNC and requires an available registered agent. A voicemail outcome marked `voicemail_outcome_unknown` stays out of automatic sweeps pending reconciliation. Supervisor wallboard and occupancy are read APIs, not simulated evidence; occupancy is clipped from durable agent-state intervals. `/supervisor` and `/agent-desktop` use short-lived tenant/channel/call-bound WebSocket tickets with REST polling fallback.

## Verify and operate

Normal gate: `make batch04-check`. Focused entry points are `make cc2-test`, `make cc2-dialer-sim`, `make agent-desktop-test`, and `make agent-desktop-build`. The 1,000-run pacing check is a tests-only stochastic simulation and is never operational or carrier evidence. Live tests are excluded unless `VOXDESK_LIVE_TESTS=1`; additionally set `CC2_LIVE_API_BASE_URL`, `CC2_LIVE_BEARER_TOKEN`, `CC2_LIVE_ENVIRONMENT_ID`, `CC2_LIVE_DESTINATION`, and `CC2_LIVE_CALLER_ID` for a dedicated test agent/number. The live smoke places a real call, requires signed events for connection state, and ends it; never use a customer number. For the separate real PostgreSQL locking proof, set `CC2_POSTGRES_TEST_URL` to a disposable PostgreSQL database as well. It creates and drops a uniquely named probe table and schema, runs two concurrent canonical `run_tick` workers against real ORM transactions, and asserts that distinct contacts are never dialed twice. Only its carrier boundary is an acknowledged test adapter; this is database-concurrency evidence, not carrier evidence, and SQLite is never used as a substitute.

For rollback, first stop dialers, end softphone calls, and drain CC2 jobs. Downgrade only with `python3 -m alembic downgrade 0065_compliance_v2`; this drops all `cc2_` tables and their history. Do not downgrade while Batch 04 code or jobs remain active.
