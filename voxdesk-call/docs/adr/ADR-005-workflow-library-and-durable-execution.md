# ADR-005 — The workflow library runs on the durable job queue, not on its own runner

**Status:** accepted · **Part:** 14 (P14-04) · **Date:** 2026-11-11

## Context

The library has to install third-party integrations (Slack, Drive, CRMs,
calendars, webhooks), let an agent call typed preset functions during a call, and
run post-call steps that post to those integrations after the caller has hung up.
The post-call half is the hard one: it runs when nobody is watching, it writes to
systems outside our control, and a duplicate write is a real business problem (a
customer sees two Slack messages about one call, or two CRM notes).

The repository already had a durable job system in `app/jobs/`: leases, retry
budgets, idempotency keys, a dead-letter queue and a worker. Building a second
retry mechanism for workflows would have meant two sets of bugs and two places
for an operator to look when something did not run.

## Decision

1. **One durable step = one durable job.** `app/workflows/executor.py` enqueues
   each step through `app.jobs.queue.enqueue` with `job_type="workflow.execution"`
   and an idempotency key of `workflow:<sha256(call, step, install, payload)>`.
   The queue's existing uniqueness on `(tenant_id, idempotency_key)` is what makes
   a retry safe, so the library does not re-implement it.
2. **The step record is written before the work is attempted.**
   `workflow_step_runs` carries status, attempt count, duration and the last
   error. A worker that dies mid-step leaves a `running` row, which an operator
   can see; it does not leave silence.
3. **Idempotency is declared per preset, not assumed.** `send_sms`, `post_slack`,
   `create_ticket` and `end_call` are `idempotent=False`: after a failure the
   executor re-queues only once the same call's reconcile step has confirmed the
   downstream state. `lookup_contact` and `book_appointment` may be retried
   blindly.
4. **Pre-call work is on a hard budget and never raises.** `run_pre_call` shares
   one deadline across fetchers (1.5 s by default, one step may take at most 60 %
   of it), records a timeout or an exception per step, and substitutes the
   declared fallback value. A CRM outage degrades a greeting; it does not drop a
   call.
5. **Installs are gated on a signed permission review.** `workflow_installs`
   stores the scopes the tenant was shown and who accepted them. An install
   cannot be enabled while that review is missing, and re-scoping bumps the
   version so the dashboard can show what changed.

## Consequences

- A workflow step inherits the queue's leases, retries, dead-letter and replay
  behaviour, and shows up wherever jobs are already monitored.
- Retrying is only safe because the idempotency key includes the payload: two
  different Slack messages for one call are two different steps.
- The pre-call path cannot return "nothing" for the agent: worst case it returns
  fallbacks and a `timed_out` flag, both of which are visible in the call record.
- The library cannot dial a vendor directly; adapters live under
  `app/integrations/` and are injected, which is why the whole library is tested
  without a network.

## Alternatives rejected

- **A Celery-style second worker.** Rejected: the repository already runs one
  worker with leases; a second would double the operational surface for no gain.
- **Synchronous post-call execution inside the call teardown.** Rejected: a Slack
  timeout would delay the call record, and any failure would be lost with the
  request.
- **Trusting `lastmod`/vendor webhooks for change detection.** Rejected for
  knowledge sync (see `app/knowledge/sitemap.py`): a content hash is the only
  thing that proves a page is unchanged.
