# Billing audit and regression record

This document records the billing defects that were identified before the
current billing implementation, the failure mode of each defect, and the code
and tests that prevent it from returning. It is an audit record, not a claim
that a live payment provider or production PostgreSQL database was tested.

## Scope and evidence

The reviewed surfaces are:

- `app/billing/hooks.py`
- `app/billing/metering.py`
- `app/billing/periods.py`
- `app/billing/entitlements.py`
- `app/billing/service.py`
- `app/billing/webhooks.py`
- `app/billing/reconciliation.py`
- `app/telephony/twilio_handler.py`
- `app/telephony/call_state.py`
- `app/db/models.py`
- migrations `0008_billing.py` and `0009_perf_policy.py`
- the billing API and the billing regression tests

The repository commit audited for this record is `1126370ce324097914be3642331024f37834ee57`.
Unit and contract tests use SQLite and scripted provider transports. They do
not prove Stripe interoperability, PostgreSQL migration success, or production
financial correctness by themselves.

## Finding summary

| ID | Finding | Risk before the fix | Current control | Regression coverage |
|---|---|---|---|---|
| F1 | Callback-order-dependent voice billing | One call could be billed for only part of its final duration | One immutable event keyed by the call id and built from final absolute duration | `tests/test_billing_voice.py::TestVoiceBillingRegression` |
| F2 | Lifetime counter compared with monthly allowance | A tenant could be permanently blocked after a cache drift, and usage could be filed against the wrong authority | `UsageEvent` is authoritative; counters are caches; periods are explicit | `tests/test_billing_metering.py`, `tests/test_billing_voice.py` |
| F3 | Incorrect period and time arithmetic | Usage could cross a billing boundary or be labelled differently from the invoice | UTC, subscription-anchored, half-open periods and clamped month arithmetic | `TestPeriodArithmetic`, `TestSubscriptionPeriods` |
| F4 | Duplicate callbacks and worker retries | A retry could create a second charge | Database uniqueness on `(tenant_id, idempotency_key)` plus savepoint-safe insertion | metering idempotency and concurrency tests |
| F5 | Provider state trusted without verification | A client or webhook could make an account appear paid | Provider webhook verification, receipt dedupe, ordering rules, and server-owned plans | billing subscription and webhook tests |
| F6 | Float and per-call rounding in the financial path | Invoice totals could disagree with line items | Integer smallest-unit quantities and period-level overage rounding | metering and overage tests |
| F7 | Reconciliation treated as the source of truth | A stale summary could change the amount billed | Immutable events are rebuilt into summaries; reconciliation reports drift | reconciliation tests |
| F8 | Billing failure could break the call path | A provider or database problem could cause Twilio retries and duplicate work | Call delivery is fail-safe; metering remains idempotent and emits bounded error logs | billing failure-path tests |
| F9 | Usage enforcement after the expensive action | A denied outbound call could still incur provider cost | Entitlements are checked before the outbound provider request | entitlement and outbound tests |
| F10 | Inbound callers were blocked by account usage | A business's customers could be hung up because the business owed money | Inbound voice never calls outbound entitlement enforcement; `tenant.is_active` remains the explicit kill switch | `TestInboundIsNeverBlocked` |

## F1 — voice-minute billing must not depend on callback order

The old pattern calculated a delta from the duration on the previous provider
callback. Twilio may send `in-progress`, `answered`, and `completed` callbacks
with different durations, and callbacks can be repeated or arrive out of
order. For a 120-second call, the observed old outcomes included 2.00, 1.00,
and 0.50 minutes depending on which intermediate value had already been
stored. The defect under-billed calls silently.

The current rule is:

1. `call_state.apply_provider_status()` is the only state transition path.
2. A terminal call is finalized from the final absolute duration.
3. `billing_hooks.on_call_finalized()` derives a stable key from the call id.
4. `metering.record_usage()` writes one immutable `UsageEvent`.
5. The unique constraint makes later finalization attempts no-ops.

A transferred call is one customer-visible call and therefore one voice usage
event. Provider legs are not charged independently.

The regression suite replays completed-only, intermediate-duration, repeated,
and mixed callback sequences and asserts the same number of seconds every time.

## F2 — event authority, cache separation, and inbound policy

`Tenant.minutes_used` and other legacy counters remain for compatibility, but
they are not the financial authority. The amount used for an entitlement or
invoice is derived from events in the relevant billing period. A cache sync may
fail without rewriting the financial record.

The old inbound guard compared a lifetime counter with a monthly allowance. It
therefore mixed two different units of time and eventually left an account
permanently blocked. It also put the consequence on callers who had no control
over the account's payment state.

Current enforcement is intentionally asymmetric:

- inbound calls are answered when the tenant is active;
- outbound calls are checked before the dialer spends provider money;
- account deactivation remains an explicit operator control;
- all calls that actually complete can still be metered afterward.

A billing lookup failure degrades the pre-action check open and emits a
secret-free error event. That policy prevents the bookkeeping service from
becoming an outage switch while preserving the usage event path.

## F3 — billing periods are explicit and UTC

A period is a half-open interval `[start, end)`. This prevents an instant at a
boundary from belonging to both adjacent periods. Subscription provider bounds
are preferred when present; otherwise the implementation projects from the
subscription anchor, and tenants with no subscription use a UTC calendar
period.

Month arithmetic clamps invalid days: January 31 plus one month is February
28 or 29, not a date in March. Labels are derived from the UTC period start,
not the server's local timezone or a browser timezone.

The same timezone discipline is required for reminders, calls, and provider
webhooks. Naive datetimes are only normalised at the documented SQLite boundary;
new billing code must use aware UTC values.

## F4–F7 — idempotency, money, provider truth, and reconciliation

Usage idempotency keys are derived from the business fact, for example:

```text
voice_minute:<call-id>
sms_segment:<message-id>
llm_token:<call-id>:turn:<turn-number>
tts_character:<call-id>:turn:<turn-number>
```

A random key per delivery would make a retry look like new usage and is not
acceptable. The insert is protected by a database uniqueness constraint, not
only by a pre-insert query. The insertion is performed inside a nested
savepoint so a concurrent loser can continue using its session and read the
winning row.

Durations and other quantities are stored as integers in their smallest
meaningful unit. Voice is stored in seconds, SMS in segments, tokens in tokens,
and TTS in characters. Overage is calculated from the plan catalogue and
rounded at the period total, not independently for every call.

Plans are server-owned. A tenant supplies a plan code, never a price. A
subscription is a provider mirror and is changed only through verified
provider state. Webhook receipts deduplicate provider event ids and preserve
out-of-order handling rules. A summary is a rebuildable cache; reconciliation
reports discrepancies instead of silently changing the event ledger.

## F8–F10 — failure behavior

Billing and cost observation must not make a live call or SMS provider return a
failure response. The call path records the operational error type without
including credentials, raw provider bodies, or payment data. A failed cost
observation is visible in logs but does not erase a successful usage event.

Entitlement decisions are made before outbound dialing. Inbound voice has no
quota hang-up branch. The deliberate exception is `tenant.is_active == false`,
which is an account lifecycle decision rather than a usage decision.

## What this audit does not claim

- No live Stripe API call was made with real credentials.
- No live provider webhook was received.
- The standard test suite uses SQLite; `alembic upgrade head` against
  PostgreSQL remains a deployment verification step.
- Provider prices, tax, proration, payment authentication, and settlement are
  not simulated by unit tests.
- A passing regression test is evidence for the stated code path, not a
  replacement for a production reconciliation process.

## Verification checklist

Before a billing change is merged, verify all of the following:

- the idempotency key still comes from the business event;
- a terminal call is billed from final absolute duration;
- the event uniqueness constraint is present in the migration;
- the plan and period are resolved server-side;
- inbound voice has no quota-denial branch;
- the cost observer cannot raise into the call or SMS path;
- webhook signatures are checked over the raw body and timestamps;
- tests cover duplicates, concurrency, out-of-order provider events, period
  boundaries, and tenant isolation;
- the full migration and provider checks are run in a PostgreSQL/staging
  environment before release.
