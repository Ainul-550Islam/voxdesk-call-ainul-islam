# Customer webhooks — 1A

Verified locally on 2026-10-07. This is not hosted LIVE certification.

## Canonical storage and API

`WebhookSubscription` and `WebhookDelivery` are authoritative. Migration
`0050_unify_webhooks` replaces duplicate enterprise endpoint/attempt tables.
The existing `/api/webhooks` router uses the canonical repository, transactional
outbox, delivery ledger and real signed HTTP transport, not a second dispatcher.

CRUD, enable/disable, bulk operations, rotate-secret, real test-send, delivery
history, retry, replay and DLQ redrive require the existing RBAC permissions.
Tenant and environment are explicit query constraints; cross-boundary access
returns 404. Configuration mutations and their audit records share a transaction.
Test-send records durable intent before HTTP and records the observed outcome
separately; database transactions cannot make an external HTTP effect atomic.
Health reports configuration and observed history, not a fabricated active probe.
Cache clearing is unsupported rather than reporting an invented operation.

Writes use a strict Redis-backed limit of 100 per tenant/environment per minute.
Missing configuration fails explicitly; Redis outages fail closed. Optional
Idempotency-Key receipts for create, rotate and test are durable database rows.
A crash after test intent commits can leave its receipt processing: a repeated
key can return 409 while the committed outbox event remains recoverable.

New signing secrets, custom headers and response diagnostics require configured
AES-GCM encryption. There is no new-write plaintext or fallback-key path.
Development-only legacy local-secret reading exists solely for migration/rotation.
Keep the configured keyring available across deployment and migration.
Rotation returns the newly generated secret to the authorized caller; stored
configuration does not expose plaintext secrets or encrypted legacy snapshots.

Delivery options persist per subscription: timeout 5 seconds by default (range
0.1–60), maximum attempts 8 (range 1–10), base backoff 30 seconds (range 1–3600),
maximum backoff 3600 seconds and not less than the base. Protected headers and
control-character injection are rejected. An empty event filter matches all.

## Transactional event producers

`call_event_catalog.schema_for` defines strict version-one JSON Schemas.
Call payloads carry references, status, direction and duration, not phone numbers,
transcripts, raw DTMF, provider error text or recording URLs. Recording events
include a recording ID. Defining a schema does not claim its producer is complete.

| Event | Runtime producer / status |
| --- | --- |
| call_started | Inbound creation, accepted outbound creation/retry and applied status transition |
| call_ended | Applied terminal status, media-error finalization, callback reconciliation and confirmed API end |
| transfer_started | Accepted provider redirect or applicable transfer callback; not proof of human pickup |
| transfer_completed | Accepted human-answer callback |
| transfer_failed | Confirmed failure; inferred termination alone is not published as provider evidence |
| voicemail_detected | Recognized machine verdict for an existing owned Call |
| dtmf_received | Nonempty validated IVR input; occurrence only, no raw digits |
| recording_ready | Provider recording state application and recording reconciliation |
| call_analyzed | PLANNED producer in 1C; schema only in 1A |
| batch_started / batch_completed | PLANNED producers in 1B; schemas only in 1A |
| agent_published | PLANNED producer in 1G; schema only in 1A |

Publication is awaited in the state-change transaction, before commit. Failures
propagate and rollback removes the event. Uniqueness is tenant-scoped with key
`call:{call_id}:{event}:v1`. Version one records at most one fact of each type per
call: it is not a per-digit, per-recording or per-transfer-attempt stream.
Consumers durably deduplicate the envelope UUID in X-VoxDesk-Event.

Legacy dotted filters remain supported where meaningful: call.answered requires
call_started with in_progress; call.completed and call.failed match the relevant
terminal statuses. Once call_started has been published at creation, a later
answer does not replace that immutable event. Prefer canonical event names.
Fanout uses currently enabled tenant/environment/filter matches at dispatch time;
no matches cancels the event, without claiming HTTP delivery. Closed history is
not retro-broadcast when a subscription is added later. Test events target only
the selected subscription. Disabled or deleted subscriptions cannot receive a
fresh fanout. An unmatched early voicemail callback is not queued for later SID
matching by that route; this is a documented provider reconciliation limitation.

## Signature verification

The request includes `X-VoxDesk-Event`, `X-VoxDesk-Signature` and
`X-VoxDesk-Timestamp`. The timestamp matches `t` inside the signature header.
The HMAC input is the ASCII timestamp, a period and the exact raw request body.
Parse JSON only after verifying the raw bytes. Reject stale timestamps, and store
processed event IDs durably. A repeated delivery must not repeat side effects.

Python, using the application verifier:

```python
from app.webhooks.signing import verify


def authenticate(secret: str, raw_body: bytes, headers: dict) -> bool:
    signature = headers.get("x-voxdesk-signature", "")
    return verify(secret, raw_body, signature, tolerance_seconds=300)
```

Node.js, using a Buffer containing the unmodified request body:

```javascript
const crypto = require('node:crypto');

function authenticate(secret, rawBody, signature) {
  const fields = Object.fromEntries(signature.split(',').map(x => x.trim().split('=')));
  if (!/^\d+$/.test(fields.t || '') || !/^[0-9a-f]{64}$/.test(fields.v1 || '')) return false;
  if (Math.abs(Math.floor(Date.now() / 1000) - Number(fields.t)) > 300) return false;
  const expected = crypto.createHmac('sha256', secret)
    .update(fields.t + '.').update(rawBody).digest();
  return crypto.timingSafeEqual(expected, Buffer.from(fields.v1, 'hex'));
}
```

## HTTP results, retry and redrive

Only an actual HTTP 2xx acknowledgement marks a delivery succeeded. 408, 429,
5xx and transport failures retry with bounded exponential backoff and jitter.
Other 4xx, redirects, invalid destinations or unusable secrets are permanent.
Permanent siblings do not strand other subscriptions with retryable failures.
Successful siblings are not resent on normal retries. Exhausted failures enter
DLQ; authenticated retry/replay queues existing durable work, not an invented
success. Manual replay is bounded to three cycles and preserves original event
body and monotonic attempt history. Succeeded rows cannot be manually retried.

History/test responses show actual HTTP status, measured latency and a snippet
bounded to the first 4096 source bytes. Diagnostics are sealed at rest and must
be treated as untrusted text, never HTML. The HTTP client currently buffers the
response before truncating the stored snippet; this is not a response-size cap.

Production delivery validates destinations through app/core/ssrf.py, including
DNS resolution, rejects private/metadata answers and disables redirects.
DNS-rebinding resistance and TLS/address pinning are not certified by this work.
The worker holds database locks to serialize overlapping deliveries, but an
HTTP acknowledgement followed by a process/database crash can still be delivered
again. This is at-least-once delivery, not exactly-once external side effects.

## Migration and rollback

0050 copies legacy IDs and configuration into canonical rows, seals the complete
legacy snapshot, and drops duplicate tables. Unverified old claimed successes
are quarantined as legacy_unverified dead letters with no successful completion
timestamp; redrive refuses that historical quarantine. They are not HTTP proof.
A missing encryption key aborts migration atomically without dropping old tables.

Downgrade intentionally recreates EMPTY legacy tables and removes the new archive
and options fields. A schema roundtrip is not a data-restoration guarantee.
Export/backup before a production downgrade; forensic archive fields are lost on
downgrade. Re-upgrade does not make historical quarantined successes replayable.

## Measured acceptance and operational limits

The actual local receiver contract validates a test Twilio HMAC, executes the
callback, dispatch_due and real JobWorker, then checks received HTTP bytes/HMAC.
A test-only transport relays to loopback after URL validation; production SSRF
is not relaxed. The receiver uses HTTP, not TLS or a hosted Twilio service.
A recording-fake API contract verifies 503 diagnostics, idempotency, rotation,
queued redrive and a subsequent real worker invocation observing 204.

Independent PostgreSQL sessions overlap delivery and rotate secrets concurrently;
one successful delivery produces one HTTP request/ledger under those conditions.
This is not a two-OS-process or crash-injection certification. Seeded PostgreSQL
upgrade, missing-key rollback, repeated head, downgrade/re-upgrade and one head
were checked independently. The regression suite reports 375 passed, 1 skipped;
truth gate 41 passed. Separate named webhook runs: bridge 45 passed, lifecycle
15 passed/1 skipped, isolation 35 passed. Counts overlap and must not be added.
Runtime inventory: 1157 registered routes, measured by scripts/repo_stats.py.

The opt-in live test requires VOXDESK_WEBHOOK_LIVE=1,
VOXDESK_WEBHOOK_LIVE_URL and VOXDESK_WEBHOOK_LIVE_SECRET. Its HTTPS receiver must
return received_event_id matching the request and signature_valid=true. Without
explicit configuration it skips. No hosted provider LIVE claim is made here.
API contract tests require redis-server; PostgreSQL verification uses an isolated
test database. See reports/PART_1_REPORT.md for commands and full source artifacts.
