# Calendar integration audit and regression record

This document records the calendar failures that motivated the current
provider-neutral calendar layer. It complements `docs/CALENDAR-INTEGRATIONS.md`:
that document is the operating contract, while this one identifies the defects
and the controls that keep them visible.

## Scope and evidence

The reviewed surfaces are:

- `app/integrations/calendar/base.py`
- `app/integrations/calendar/models.py`
- `app/integrations/calendar/policy.py`
- `app/integrations/calendar/timezones.py`
- `app/integrations/calendar/service.py`
- `app/integrations/calendar/tools.py`
- all calendar provider adapters and the legacy Google service-account adapter
- `app/api/appointment_routes.py`
- `app/api/calendar_webhook_routes.py`
- migrations `0007_calendar_scheduling.py` and `0010_e2e_test_tenant.py`
- the calendar contract, provider, timezone, booking, API, and webhook tests

The repository commit audited for this record is
`1126370ce324097914be3642331024f37834ee57`.
No calendar provider was contacted with real credentials. Provider behavior is
exercised through scripted HTTP transports and deterministic internal fixtures.

## Finding summary

| ID | Finding | Risk before the fix | Current control | Regression coverage |
|---|---|---|---|---|
| F1 | Success reported before provider acceptance | A caller could be told an appointment was booked when the provider rejected it | `CONFIRMED` requires provider confirmation and an external event id | booking provider-rejection tests |
| F2 | Provider outage looked like free availability | A failed calendar could expose every slot and cause false bookings | Provider errors raise normalized errors; availability returns a degraded state | contract and availability tests |
| F3 | Naive/local time arithmetic | Reminders and bookings could move by a timezone offset or DST transition | UTC storage, tenant-zone reasoning, explicit DST handling | timezone and reminder tests |
| F4 | Double booking at the application layer | Two concurrent requests could both observe a free slot | Database uniqueness on the slot key plus provider idempotency | concurrency booking tests |
| F5 | Unsupported provider operations were guessed | A capability could be advertised without a real implementation | Registry capabilities are tested against provider overrides | `tests/test_calendar_contract.py` |
| F6 | Secrets and tenant routing were weak | Tokens or another tenant's appointment could be exposed | encrypted credentials, tenant-scoped lookups, signed webhooks, receipt dedupe | security and webhook tests |
| F7 | Provider success was trusted without an external id | A network response could produce a local “confirmed” row with nothing booked | confirmation requires an external provider id | booking lifecycle tests |
| F8 | LLM could invent a booking outcome | Voice text could claim a booking that did not happen | closed booking outcomes and server-authored messages | tool and service tests |

## F1 and F7 — provider acceptance is evidence

The service creates a local appointment in a provisional state, calls the
selected provider, and writes `CONFIRMED` only after the provider returns a
usable external event id. A provider rejection produces `FAILED` or `CONFLICT`
with a safe caller-facing message; it never produces a false “booked” result.

The internal provider is also deterministic. Its external id is derived from
the idempotency key, so it exercises the same confirmation contract without
requiring a vendor account.

A provisional mode exists only when the tenant explicitly disables the
provider-confirmation requirement. Even in that mode, the user-facing message
must say that the request is pending; it must not use confirmed language.

## F2 — an outage is not an empty calendar

The old legacy behavior returned an empty list for provider failures. Empty
means “there are no busy periods”; it must not also mean “the provider could
not be reached.” The current adapters normalize timeouts, connection errors,
HTTP failures, provider-level errors, and malformed success bodies into
`CalendarError` subclasses.

The availability service distinguishes a real empty result from a degraded
provider. The caller can then be offered a safe fallback, such as trying again,
choosing the internal calendar, or contacting the business, instead of being
told that every time is available.

The legacy Google service-account provider is retained for migration. Its
inherited exception-swallowing limitation is documented and its create path
still refuses to claim success without a returned event id. Tenants should
prefer the per-tenant OAuth Google provider.

## F3 — timezone and DST rules

The data model stores instants in UTC and stores the agreed IANA timezone with
the appointment. Natural-language interpretation happens in the tenant's
zone. Provider adapters receive aware datetimes and are never allowed to
silently reinterpret a naive local wall clock.

The implementation explicitly handles:

- nonexistent local times during a spring-forward transition;
- ambiguous local times during a fall-back transition;
- “same time tomorrow” as local calendar arithmetic, not 24 elapsed hours;
- half-hour and 45-minute zones;
- reminder windows calculated from aware UTC instants.

The reminder scheduler must use one consistent `now` instant. A naive UTC
clock relabelled as a local time shifts the due window and can drop reminders;
this is covered by the reminder regression tests.

## F4 — concurrent booking and idempotency

Availability is advisory. The booking transaction owns the final decision. A
slot key is deterministic from the tenant, timezone-normalized start, and
booking window. `UNIQUE (tenant_id, slot_key)` is the final authority when two
requests race.

Provider requests also carry deterministic idempotency material where the
provider supports it:

- Google uses a client-supplied event id;
- Microsoft Graph uses `transactionId`;
- Cal.com carries the VoxDesk key in booking metadata;
- the internal provider derives its event id from the key.

An ambiguous provider timeout is not treated as a free slot. The service
reconciles when the provider supports lookup and otherwise reports a pending or
failed outcome according to the tenant policy.

## F5 — capability contracts

Every provider is registered through `app/integrations/calendar/registry.py`.
The registry is the source used by the API and service, rather than a second
hand-maintained list. Contract tests assert that:

- every enum provider has an adapter;
- every declared capability is actually overridden;
- every undeclared operation raises the normalized unsupported error;
- HTTP statuses and transport failures map to the same error taxonomy;
- configured timeouts reach the HTTP client;
- provider and tenant identifiers do not cross the adapter boundary.

A provider is allowed to be incomplete, but it must declare that fact. Silent
emulation of an unsupported operation is not a calendar integration.

## F6 — credentials, tenant isolation, and webhooks

OAuth credentials are encrypted using the shared key-ring implementation with
`(tenant_id, provider)` bound as associated data. The credential values are not
returned by API responses, JWTs, logs, or audit details. Production requires
`CRM_ENCRYPTION_KEYS`; plaintext fallback is not a supported mode.

Every integration lookup is tenant-scoped. Webhook routing uses a high-entropy
integration token, but the provider signature is the authentication boundary;
a path token alone is not sufficient. The raw request body is verified before
processing. A durable receipt keyed by tenant, provider, and provider event id
makes repeated delivery return a harmless duplicate response.

The current inbound mutation is deliberately narrow: a notification may mark
a locally known appointment cancelled when the provider unambiguously says the
event is gone. Bidirectional synchronization is not inferred from a partial
notification.

## Provider-specific audit notes

### Google Calendar

Event ids are derived from the idempotency key and filtered to Google's allowed
character set. A provider `409` for the same client id is reconciled as the
previous create having landed. Per-calendar errors inside an HTTP 200 response
are raised instead of being treated as an empty calendar. Refresh-token
rotation preserves the existing token when Google does not return a new one.

### Microsoft Graph

Requests use UTC instants with `timeZone: "UTC"`, avoiding a second IANA to
Windows timezone database. `transactionId` is sent for create-event. Schedule
responses use `scheduleItems` when available and decode `availabilityView`
when permissions omit item detail. Cancel attempts the provider cancel action
before falling back to deletion where the account is not the organizer.

### Cal.com

The adapter treats a non-success response envelope at HTTP 200 as a failure.
Cal.com slot conflicts reported as HTTP 400 are classified as conflicts rather
than permanent configuration errors. Booking, rescheduling, and cancellation
use the provider's dedicated endpoints and event type configuration.

### Internal and legacy Google service account

The internal provider makes the complete flow testable without an external
account and is not a placeholder. The service-account adapter is retained for
migration compatibility and carries its documented limitation: its legacy
client cannot distinguish an outage from an empty free/busy response.

## What this audit does not claim

- No provider API or OAuth authorization flow was live-tested with real
  credentials.
- No live webhook was received.
- The tests use SQLite and scripted transports; PostgreSQL migration execution
  remains a deployment verification step.
- Vendor behavior can change. The published API assumptions must be rechecked
  when a provider version or contract changes.

## Verification checklist

Before a calendar change is merged, verify that:

- provider rejection cannot produce `CONFIRMED`;
- no confirmation is stored without an external event id;
- outage and empty availability remain distinct;
- slot uniqueness and provider idempotency are preserved;
- DST, ambiguous time, and tenant-timezone tests still pass;
- credentials never enter responses or logs;
- webhook signatures are calculated over the raw body;
- tenant routing does not trust a body-supplied tenant id;
- declared capabilities and migration enums remain in parity.
