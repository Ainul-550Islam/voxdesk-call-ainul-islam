# Prompt 8B — validation and fail-closed contract corrections

Date: 2026-10-06. These changes do not establish production readiness or Retell parity.

## Workflow triggers

`POST /api/workflows/triggers` now resolves the supplied workflow UUID against the authoritative `Workflow` table and requesting tenant before inserting a trigger. Malformed, nonexistent, and foreign workflow identifiers all produce 404. Persisting a trigger does not establish a live event scheduler or external provider execution.

## Monitoring session termination

`POST /api/calls/{call_id}/monitor/{session_id}/end` retains its existing permission dependency and tenant/call lookups. It now actually enforces its previously documented owner-or-administrator rule. Another supervisor cannot terminate the session merely because they hold supervisor-write permission. Administrator overrides use the effective scoped context role, not an unscoped user role. Successful termination continues to persist the audit event.

## Outbound webhooks

The lifecycle dispatcher previously synthesized HTTP 200 and `delivered` from the destination URL without opening a connection. It now uses the existing signed HTTP delivery adapter. A transport-observed success is required before setting delivery timestamps. Redirects and permanent failures go to the dead-letter state; retryable failures retain a bounded backoff schedule. Sensitive remote response bodies are not stored.

Destination validation rejects literal private/metadata addresses and checks current DNS answers before connecting. Redirect following is disabled. Network egress restrictions remain necessary to close the DNS-rebinding window between validation and connection.

An injected HTTP client is supported only as the internal transport boundary for deterministic contract tests. Public API handlers do not expose that argument. Production calls use the real adapter. Endpoint custom headers are not supported by this adapter; those configurations explicitly fail closed rather than silently losing their authentication headers. This change does not implement an autonomous retry worker or exactly-once remote delivery.

## Salesforce

The legacy Salesforce route module generated OAuth tokens from hashes/random identifiers, treated token length as authentication, and returned fabricated API limits. Those are not implementations. Provider-dependent connect, OAuth, refresh, writeback, synchronization, describe and limit operations now explicitly return 501 with `SALESFORCE_RUNTIME_NOT_IMPLEMENTED`. Entity queries also fail closed instead of returning a fabricated empty remote dataset.

Existing tenant-scoped local connection metadata can still be read, updated and removed. Local connection health is `unverified` (or disabled/disconnected), never provider-verified health. No new pseudo-token is created. Legacy obfuscation helpers were removed; this does not retroactively migrate or certify any previously stored credentials.

## CRM and workflow outcome writeback

The generic CRM disposition/task/extracted-fields routes previously inserted `completed` logs without calling a CRM. Mapping creation returned an unpersisted generated ID, and backfill was not a durable provider job. Those operations now return 501 with `CRM_WRITEBACK_RUNTIME_NOT_IMPLEMENTED`, without manufacturing completion records. Workflow outcome writeback likewise returns 501 after its tenant-scoped call lookup.

Real local call-context reads are retained. Lead and appointment lookups remain tenant-scoped. Appointment query errors are no longer silently converted into an empty booking list.

These guards do not disable the independently implemented CRM adapter/service contracts. They prevent separate unsupported API surfaces from claiming those capabilities.

## Type contracts and exports

Lazy Deepgram exports have a type-checking-only SDK import, preserving import-light runtime behavior. Intentional module exports retain their names through explicit aliases. Application model-registration imports no longer collide statically with the FastAPI application variable. FastAPI documentation configuration uses explicit keyword arguments. JSON dictionary/list narrowing preserves identity and prior shape semantics while avoiding repeated optional `.get()` expressions. Streaming protocols describe methods returning asynchronous iterators rather than coroutines resolving to iterators.

Repository-wide mypy remains a required unresolved validation result until its complete log is green. These changes do not suppress errors or exclude modules from checking.

## Reproducible validation

Use Python 3.12, matching the Dockerfile and CI, install `requirements.txt`, and provision a disposable PostgreSQL instance migrated to head. The full-population runner is `scripts/validate_full_population.py`, with phase telemetry in `scripts/validation_evidence.py`. Every test ID is collected twice, scheduled, and represented in `FINAL_TEST_RESULTS.json`; nonzero child exits and timeouts are not success.

The measured eager application import consumes about 1.27 GiB by itself. A 2 GiB worker with no swap was insufficient for the full application's imports and instrumentation. The validation environment was given 3 GiB of swap and sequential fork-isolated test workers. Preloading occurs before any application lifespan, database connection, test fixture or event loop. Garbage-collector freezing keeps read-only inherited objects from unnecessary copy-on-write scanning. Each child executes pytest setup, call, teardown and session hooks normally. No test outcome is modified by the evidence plugin.

Run independent heavy build/test gates sequentially on memory-constrained workers. Configure live provider credentials only through the deployment secret store; never place their values in reports or chat. Missing live credentials remain explicit blockers, not local-contract passes.

## Real-browser and PostgreSQL regressions

The shared TypeScript client duplicated the default `/api` prefix, sending real browser requests to `/api/api/agents`; URL joining now includes the prefix once. Login now sends the backend schema's `next` field, preserving the sanitized return path rather than silently falling back to `/dashboard`.

PostgreSQL-backed browser login exposed `UserSession.is_live` comparing aware stored timestamps with naive UTC clocks. Both representations are now normalized to UTC, with equality still expired and revoked sessions still refused. Same-origin cookie refresh now trusts the operator-configured `PUBLIC_BASE_URL` as well as the explicit CORS list, never the incoming Host header. Cross-site Fetch Metadata remains unconditionally rejected.

Migration `0049_runtime_schema_alignment` adds six missing ORM columns across agents, agent_versions and mcp_tools, supplies defaults for retained legacy optional JSON columns, and aligns agent status column types with the persisted runtime models. An old active version is adopted only after exact tenant/agent/version matching. MCP discovery timestamps are backfilled from existing creation timestamps, with forced RLS restored transactionally. Downgrade refuses to relabel superseded immutable snapshots. The successful empty-database migration round trip is not proof of a lossless downgrade for every populated deployment.

The final real Chromium smoke uses a newly persisted test owner and actual login, creates an agent through the API, renders its builder and the operator/testing/analytics/phone/billing pages, and records desktop/mobile screenshots. It does not place live calls, settle payments, certify accessibility, or replace the separate provider gates.
