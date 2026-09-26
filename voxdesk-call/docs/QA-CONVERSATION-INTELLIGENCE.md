# QA and conversation intelligence

QA scores an existing call. It does not create a second transcript system and
it does not copy recording bytes into another store. Evidence rows point at
`calls.id` and `turns.id`, plus a SHA-256 of the turn text and optional
offsets. The turn body stays on `turns`.

AI analysis goes through `app.ai.gateway.govern`. There is no second provider
registry and no API key in this package. Auto-review is a `jobs` row of type
`qa_auto_review`. It does not run on the media socket and it does not block
the live call path.

## Tenant boundary

Every query filters on the authenticated tenant. A path or body `tenant_id`
that does not match is a 404. A call, turn, review, scorecard, finding,
coaching signal, calibration session, metric or export from another tenant is
missing. A revoked or expired environment membership cannot act on that
environment. Absence of a membership row still inherits, matching the
existing environment model.

## Review lifecycle

States: `created`, `assigned`, `in_review`, `submitted`, `calculated`,
`finalized`, `reopened`, `cancelled`.

Legal moves are the ones in `app.qa.models.TRANSITIONS`. A repeated assign,
finalize or reopen of the same state is `duplicate`. A lost version is a
conflict, not a second owner. One open review per call and scorecard is
enforced by `open_key`. Finalize clears that key and stores
`calculation_snapshot`. Reopen appends that snapshot to `prior_snapshots`
before new scores can be saved. Calibration never writes the review row.

## Scorecards

A scorecard has a name and an integer version. Items and weights are not
rewritten after a finalized review uses that version. A later definition is a
new version. Pass threshold is an integer from 0 to 10000.

## Deterministic scoring

```
normalized = (value - min) * 10000 // (max - min)
section = sum(normalized * item_weight) // sum(item_weight)
overall = sum(section * section_weight) // sum(section_weight)
```

N/A items leave both sums. A missing required item fails the review even if
the average would pass. The same snapshot always reproduces the same
integers. There is no NaN path because scores are integers.

Human scores and AI scores are different columns. Accepting an AI suggestion
is an explicit `accepted_source=ai` write, and only when no human score
exists. A later human value keeps the AI value, stores an override reason,
an actor and a timestamp, and the final calculation uses the human value.

## Sampling

The bucket is `sha256(tenant|window|rule|call) % 10000`. Percentage sampling
keeps buckets below `percent * 100`. Count sampling keeps the first N after
sorting by bucket then call id. The same inputs select the same calls. A
unique `(tenant, rule, window, call)` row makes a repeat run a no-op.
Targeted rules can require a disposition, queue, agent, an existing
compliance finding, or a stored QoS index below a threshold. Missing QoS is
not treated as low quality.

## Auto-review

Enqueue writes a durable job and an `qa_auto_review_runs` row. Processing
sends only the call's existing turns to the governed gateway. The response
must be JSON. Guardrails and evidence checks run before any AI column is
written. A citation of a turn that is not on this call is a permanent
failure. Retryable provider errors stay retryable up to the job attempt cap.
Authentication and validation failures are permanent and are not retried.
The run records provider, model, prompt version, latency and token count
when the gateway returns them. It does not invent a cost. It does not set
the review to `finalized`.

## Sentiment, topics, compliance, coaching

Sentiment labels are `positive`, `neutral`, `negative`, `mixed` or
`unknown`, with confidence 0..100. That is a classification, not a clinical
claim. Topics require a caller-supplied taxonomy version. No enterprise
taxonomy is hard-coded.

Compliance policies are tenant rows with a code and version. Categories are
infrastructure labels: missing required phrase, phrase mismatch, sensitive
data handling, consent evidence, prohibited workflow. An AI finding starts
as `suggested`. A phrase rule starts as `open`. Only a human disposition
moves a finding to `confirmed` or `dismissed`.

Coaching records a category, recommendation, evidence reference and
acknowledgement. It does not apply employment discipline. Acknowledgement
is the assigned agent or a supervisor. A repeated acknowledgement is a
duplicate. A lost version is a conflict.

## APIs

`/api/qa/reviews`, scorecards, metrics, exports, sampling and calibration
use `qa:read`, `qa:write`, `qa:review` or `qa:finalize`. Conversation reads
live under `/api/conversations/{call_id}/...`. They return `recording_url:
null` and do not copy transcript text into the QA tables.

## Concurrency

Review assignment, scoring, finalization and coaching acknowledgement use a
compare-and-set on `version`. Sampling and auto-review enqueue use unique
idempotency keys. Two finalizers cannot both commit the first finalize.

## Exports and metrics

Exports are limited, tenant-filtered JSON or CSV of review metadata, item
scores, evidence references and provenance. They omit secrets and recording
URLs. Metrics are SQL aggregates for one tenant: counts, pass rate, score
buckets, coverage, finding counts, override count, calibration disagreement
and turnaround. They are not a warehouse.

## Failure handling and jobs

A missing executor is `executor_not_attached`, not a fabricated score.
Permanent validation stops the run. The job table remains the queue.
External delivery is at-least-once; the idempotency key stops a second
suggestion row.

## Observability

Service logs use the existing logger: `review.created`, `review.assigned`,
`review.submitted`, `review.finalized`, `review.reopened`,
`review.auto_review_requested`, `review.auto_review_completed`,
`review.auto_review_failed`, `coaching.created`. Audit rows reuse
`RESOURCE_BOUND` with an `operation` field so the PostgreSQL audit enum is
not extended by this batch.

## Migration and rollback

Revision `0024_qa_conversation_intelligence` revises
`0023_contact_center_acd`. It adds QA tables only. Downgrade drops those
tables and does not touch `calls` or `turns`. Applying it requires
PostgreSQL. A refused connection is not an applied migration.

Retention of QA rows follows the call they reference. This batch does not
add a second retention worker. Operators should delete QA rows with the
call, not keep a private transcript copy, because no private copy is stored.
