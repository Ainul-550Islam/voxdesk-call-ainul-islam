# Dashboard audit and metric integrity record

This document records the dashboard defects that were found in the earlier
metric presentation and the controls now used by the API and both dashboard
implementations. `docs/DASHBOARD.md` defines the metric contract; this file
explains the audit findings and how to verify them.

## Scope and evidence

The reviewed surfaces are:

- `app/api/analytics_routes.py`
- `app/analytics/`
- `app/billing/metering.py` and `app/billing/plans.py`
- `dashboard/src/`
- `dashboard/tests/`
- `dashboard-next/app/`
- `dashboard-next/components/`
- `dashboard-next/lib/`

The repository commit audited for this record is
`1126370ce324097914be3642331024f37834ee57`.
The audit covers source and tests. It is not a claim that the metrics have been
reconciled against a production warehouse or a customer's accounting system.

## Finding summary

| ID | Finding | Risk before the fix | Current control | Regression coverage |
|---|---|---|---|---|
| F1 | Invented revenue/value tile | Operators could read a constant as real money | Estimated revenue is not a dashboard metric; real usage and billing data are used instead | analytics and no-fake-revenue tests |
| F2 | Browser-side aggregation | Large tenants paid the latency and clients could disagree | API performs tenant-scoped conditional aggregation | analytics API tests |
| F3 | Unnamed denominators | Percentages could not be explained or audited | Every rate has a named numerator and denominator in the response contract | formula tests |
| F4 | Short calls included as booking opportunities | Mis-dials and hang-ups distorted conversion | Eligible answered call threshold is centralized at 10 seconds | analytics eligibility tests |
| F5 | Funnel stages were silently derived as a single journey | Cross-window events made pages disagree | Stages are counted independently over the declared window | funnel tests |
| F6 | No tenant-timezone window | Two users could see different “days” for the same tenant | Server resolves date boundaries in the tenant's timezone and returns it | analytics timezone tests |
| F7 | No range ceiling | A single request could scan an unbounded history | `MAX_RANGE_DAYS` caps the range at 400 days | API range tests |
| F8 | Billing permission represented as zero usage | A hidden value looked like no usage | Restricted usage is omitted rather than zeroed | authorization tests |
| F9 | Provider failures folded into agent performance | An outage looked like an agent failure | CRM/calendar failures are separate integration metrics | analytics integration tests |
| F10 | `booking_rate = booked / total` | Calls that could never book lowered the rate | `booking_rate = booked / eligible` | `TestFormulas::test_booking_rate_excludes_calls_that_could_never_book` |
| F11 | Browser-local timestamp rendering | The same call displayed at different local times | Formatting receives the tenant timezone and renders explicitly | dashboard formatting tests |

## F1 — no invented revenue

The earlier Overview presentation used a fixed multiplier such as
`booked * $150`. That value was not connected to the tenant's prices, invoices,
collections, currency, refunds, or tax. It looked like money while being a
fictional estimate, so it was removed from the dashboard contract.

The legacy API may retain a compatibility field for old clients, but the
current dashboard does not display it and no replacement projection is implied.
A money tile must come from a billing source that can explain its amount and
currency, not from a UI constant.

## F3 and F10 — metric definitions

All rates expose defensible definitions:

| Rate | Numerator | Denominator |
|---|---|---|
| `answer_rate` | completed calls | all calls |
| `booking_rate` | booked calls | eligible calls |
| `transfer_rate` | transferred calls | answered calls |
| `failure_rate` | failed calls | all calls |
| `call_to_lead_rate` | leads | eligible calls |
| `lead_to_appointment_rate` | booked appointments | leads |
| `appointment_kept_rate` | kept appointments | booked appointments |

An eligible call is completed and at least `ELIGIBLE_CALL_SECONDS` (10
seconds). The threshold is one server-side constant used by the API and pinned
by tests. A rate with a zero denominator is represented as zero or the API's
explicit empty-state value; it is not produced by an unbounded browser
calculation.

The booking rate must not use all calls as its denominator. A three-second
misedial and a call that never connected are not comparable booking
opportunities, and including them makes a tenant's conversion look worse for
reasons unrelated to the agent.

## F2, F4, and F5 — aggregate once, define the window

Analytics routes perform tenant-scoped conditional counts and sums in the
backend. The browser receives already-defined totals and series data rather
than fetching all call rows and recomputing business logic.

A range is resolved using the tenant's IANA timezone and returned with its
start, end, and timezone. The server applies the 400-day maximum. Funnel stages
are counted independently in the same declared window: a call in the window
may produce a lead or appointment later, and the page must not silently mutate
its own definition by following records across windows.

The funnel intentionally includes booked appointments that later no-show. A
no-show was still booked; the kept stage measures the later outcome. Cancelled
and failed appointments are excluded from booked counts as defined by the API
contract.

## F6 and F11 — timezone-safe presentation

The dashboard is not authoritative for date boundaries. It receives the
server-resolved window and formats timestamps using the tenant timezone passed
by the API. Browser locale and browser timezone must not change the business
meaning of a day or call time.

Frontend formatters are presentation helpers only. They must not reimplement
eligibility, rate denominators, billing period arithmetic, or tenant filters.

## F7–F9 — permissions and operational separation

Billing usage requires the billing-read permission. When a user lacks that
permission, the response omits the usage object instead of returning zero.
Zero means “nothing used”; omission means “not authorized to see this.”

CRM and calendar provider failures appear in integration metrics and
operational surfaces. They are not counted as the agent failing to answer a
call. API responses remain tenant-scoped and do not permit query parameters to
override the verified tenant.

## Frontend coverage

The repository contains a Vite dashboard under `dashboard/` and a Next.js
surface under `dashboard-next/`. The two applications share the API contract
but are separate deliverables. Both must preserve the same rules:

- no fake revenue tile;
- no client-side replacement denominator;
- no secret or access token in rendered markup;
- no unsafe transcript or meeting URL interpolation;
- explicit loading, empty, and error states;
- timestamps formatted with the API-provided timezone.

The Vite test suite covers boot, routing, authentication headers, calls,
transcripts, billing, integrations, audit views, realtime behavior, meeting
URL safety, and team pages. The Next.js test suite covers formatting and
identity helpers; a production build remains a separate release check.

## Verification checklist

Before an analytics or dashboard change is merged, verify that:

- every displayed percentage has a named numerator and denominator;
- `booking_rate` still uses eligible calls;
- `ELIGIBLE_CALL_SECONDS` is not duplicated in the frontend;
- the API still caps the range and resolves it in the tenant timezone;
- no invented revenue or projection is rendered;
- usage is omitted when the caller lacks billing permission;
- provider failures remain separate from agent performance;
- browser timezone does not change displayed business dates;
- both dashboard implementations pass their test suites;
- a metric definition change updates `docs/DASHBOARD.md` and this audit in the
  same change.

## Audit boundary

The audit does not certify business decisions made from the metrics, payment
settlement, or data completeness outside the API's source tables. It certifies
the definitions and safeguards present in the reviewed code at the audited
commit. Production operators must still monitor query latency, row counts,
period boundaries, permissions, and reconciliation with billing and CRM data.
