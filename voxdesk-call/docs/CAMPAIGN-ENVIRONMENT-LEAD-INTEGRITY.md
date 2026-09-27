# Campaign ↔ Environment ↔ Lead Integrity (Batch 06)

Status: implemented. Scope: backend only (`voxdesk-call/app`, `alembic`,
`tests`). This document describes the binding rules, the enforcement layers,
the data migration, and the compatibility guarantees that Batch 06 added on
top of the existing campaign, lead and environment systems. Nothing here is
a new subsystem: every rule reuses machinery that already existed
(`app/environments/*`, `app/leads/lifecycle.py`, `app/leads/consent.py`,
`app/leads/repository.py`).

## 1. The gap this closes

Before Batch 06:

* `leads`, `calls`, `appointments` and the rest of the business resources
  carried `environment_id` (revision `0019`), but `campaigns` carried only
  `tenant_id`. A production campaign could sit beside staging leads inside
  one tenant with nothing binding them to the same environment.
* Several paths wrote `lead.status = ...` directly (workflow actions, the
  SMS webhook, an AI tool, the environment archive route, the Twilio status
  callback), bypassing `app/leads/lifecycle.py` — no transition validation,
  no history, and one path could silently reverse a do-not-call.
* The legacy bulk-lead route (`POST /api/tenants/{tenant_id}/leads`) created
  `Lead(...)` rows without the schema-required `environment_id`.

## 2. The model

A **campaign belongs to exactly one tenant + environment pair**, the same
convention leads already follow:

```
campaigns
├── environment_id  UUID NOT NULL
│     ├── FK → environments.id                     ON DELETE RESTRICT
│     └── (tenant_id, environment_id) FK → environments (tenant_id, id)
│            ← uq_environments_tenant_identity      (composite: no
│               cross-tenant environment pointer is representable)
├── ix_campaigns_environment_id
└── ix_campaigns_tenant_environment
```

`environment_id` is immutable after insert. A campaign may only ever target
leads and segments **from its own environment**; leads keep pointing at
campaigns through the existing `leads.campaign_id`, and every read path
filters both sides on the same pair.

### Environment resolution (existing stack, no new resolver)

Every campaign/lead entry point resolves the effective environment in the
platform's existing order:

1. an **explicit** `environment_id` (body/query) — it must exist and belong
   to the authenticated tenant; a cross-tenant id is the same safe not-found
   as a missing row, never a fallback and never a boundary disclosure;
2. the caller's **server-side selection** (`environment_selections`, via
   `app.environments.context_resolution.resolve_environment`);
3. the tenant's **active default production environment**
   (`app.leads.repository.production_environment_id`);
4. otherwise the request is **rejected** — an environment is never invented.

Headless callers (webhooks, the dialer) use rule 3 only, which is the same
rule leads already applied. Write operations additionally pass
`assert_environment_accepts_write`: **suspended and archived environments
refuse campaign creation, state changes and runs** (reads stay possible so
operators can inspect a frozen scope).

## 3. Enforcement layers (defense in depth)

| Layer | Where | What it guarantees |
|---|---|---|
| Schema | `campaigns.environment_id NOT NULL` + composite FK + indexes (model **and** migration 0026) | no environment-less campaign row can exist; no cross-tenant environment pointer is representable in PostgreSQL |
| ORM hooks | `app/db/models.py` `before_insert`/`before_update` on `Campaign`, delegating to the existing `app.environments.resource_binding._assign_scope`/`_freeze_scope` | legacy `Campaign(tenant_id=...)` inserts bind to the tenant's active production environment (identical rule to legacy `Lead(...)` inserts); closed environments refuse the insert; the environment can never move after insert |
| Service | `app/services/campaign_service.py` | resolves + write-gates the environment on every operation; `_validate_binding` requires tenant + environment + campaign identity on every persisted definition; audience leads/segments are validated one-by-one against the campaign's environment; metrics, plans and audience previews are environment-filtered |
| Repository | `app/leads/repository.py` (`require_environment`, `campaign_in_scope`, `find_lead_by_phone`, `get_lead`, `get_lead_for_update`) | one safe not-found for missing / foreign-tenant / cross-environment alike |
| Dialer | `app/telephony/outbound.py` | `next_callable_leads` filters `Lead.environment_id == campaign.environment_id` (a campaign with no environment selects nothing); `place_call` refuses `tenant_mismatch` / `environment_mismatch` **before** any claim, attempt or provider call |
| API | `app/api/campaign_routes.py`, `app/api/routes.py`, `app/api/environment_resource_routes.py` | explicit → selected → default production on every endpoint; client-supplied ids can never cross the authenticated tenant; legacy URLs and response fields preserved |

### Lead status: one state machine

`app/leads/lifecycle.py` is the only legitimate lead-status writer. Batch 06
removed every direct `lead.status = ...` write from the product code:

* `workflow_service._lead_status_change` → `lifecycle.transition`
  (source `workflow`);
* `channels/messaging.set_opt_out` → `consent.record_consent` →
  `lifecycle.transition` (source `consent`);
* `agent/functions.mark_do_not_call` → `consent.record_consent` (source
  `consent`, creation history source `agent_tool`);
* `environment_resource_routes` archive → `consent.record_consent`;
* `api/routes.py` legacy DNC route → `consent.record_consent` (source
  `api:legacy_do_not_call`); legacy bulk import → `lifecycle.record_created`
  (source `legacy_bulk_import`);
* `telephony/twilio_handler` call grading → `lifecycle.transition` with
  `expected=lead.status` (source `telephony`);
* `telephony/outbound` dial failure → `lifecycle.transition(FAILED,
  expected=QUEUED)`; the dial claim itself stays the pre-existing atomic
  conditional UPDATE (`_claim_attempt`), audited via
  `lifecycle.record_dial_claim`.

Transitions are validated against the stored matrix, applied as a
compare-and-set (`UPDATE ... WHERE status = believed`), and every accepted
change appends `lead_status_history` in the lead's own environment. A stale
or racing writer receives `ClaimConflict` and loses instead of overwriting.

### Do-not-call policy

DNC is **authoritative and terminal**. `consent.record_consent` refuses to
clear a voice DNC ("Do-not-call cannot be cleared by granting voice
consent"), SMS `START` records an SMS grant only and never touches the voice
DNC, the lifecycle matrix gives DNC no outgoing edges, and the dialer,
planner and webhook conversation gate all re-check it. No path in the
product can silently reverse a DNC.

## 4. Migration `0026_campaign_environment_scope`

* `down_revision = "0025_enterprise_leads"`; single head.
* Adds `campaigns.environment_id` nullable, then **backfills** every
  existing campaign with its own tenant's *active* production environment,
  preferring the default row (`kind = 'production' AND status = 'active'
  ORDER BY is_default DESC LIMIT 1`) — the deterministic shape `0019`
  established, narrowed to active environments because a campaign is an
  operational resource.
* Then creates the two indexes, the `ON DELETE RESTRICT` FK and (on
  PostgreSQL) the composite FK.
* `NOT NULL` is enforced **only if the backfill bound every row**. A
  campaign whose tenant has no active production environment stays unbound
  and visible: a loud, inspectable state instead of an invented binding
  (`0019`'s "fail visible, never fabricate" rule). Operators restore or
  designate a production environment, then re-run the tightening.
* `downgrade` reverses everything (composite FK first on PostgreSQL).
* PostgreSQL-compatible by design; the test suite never claims migration
  behaviour from SQLite.

## 5. Compatibility guarantees

* **URLs unchanged**: `/api/campaigns` (+ `/{id}`, `/schedule`, `/pause`,
  `/resume`, `/cancel`, `/audience`, `/eligibility`, `/execution-status`,
  `/plan`, `/results`, `/kpis`), `/api/tenants/{tid}/campaigns`
  (+ `/{cid}/run`), `/api/tenants/{tid}/leads` (+ `/{lid}/do-not-call`),
  `/telephony/status`, `/channels/message`.
* **Requests unchanged**: every new field (`environment_id`) is optional;
  omitting it reproduces the pre-Batch-06 behaviour (default production).
* **Responses additive**: legacy fields keep their names, types and
  meanings; `environment_id` is added.
* **Legacy data**: campaigns created before 0026 are bound by the backfill;
  campaigns created via the legacy route are visible in the modern API and
  vice versa (one `campaigns` table, one service).
* **Cross-environment association is refused, not repaired**: leads are
  never silently moved between environments to make a binding fit.

## 6. Concurrency

* Dial claims: the pre-existing atomic conditional UPDATE arbitrates; two
  overlapping ticks claim each lead exactly once (`claimed_by_another`).
* Lead transitions: compare-and-set on `(id, tenant_id, environment_id,
  believed status)`; the loser gets `ClaimConflict`.
* Repeated provider callbacks: `call_state.apply_provider_status` gates on
  `result.applied`, and grading passes `expected=lead.status`, so a redelivered
  callback never double-grades.
* Campaign state changes keep the existing stored machine
  (`draft → scheduled → running ⇄ paused → completed/cancelled`); illegal
  moves stay `422`.

## 7. Where the rules live (file map)

| Path | Role in this contract |
|---|---|
| `app/db/models.py` | `Campaign.environment_id`, composite FK, indexes, insert/update hooks |
| `app/domain/campaign_models.py` | `environment_id` on `CampaignDefinition`/`Audience`/`Segment`, binding validation |
| `app/services/campaign_service.py` | resolution, write gates, audience/metrics/plan scoping |
| `app/api/campaign_routes.py`, `app/api/routes.py` | HTTP resolution + legacy compatibility |
| `app/api/environment_resource_routes.py` | archive uses consent, not a direct DNC write |
| `app/leads/{repository,service,lifecycle,activities}.py` | scoping primitives, the state machine, history |
| `app/services/workflow_service.py` | workflow status changes via lifecycle |
| `app/channels/messaging.py`, `app/agent/functions.py` | STOP/START + AI tool via consent/lifecycle |
| `app/telephony/{outbound,twilio_handler}.py` | dialer scope gate + callback grading via lifecycle |
| `app/orchestration/campaign.py` | planner environment gate (`environment_mismatch` skip reason) |
| `alembic/versions/0026_campaign_environment_scope.py` | schema + backfill |

Tests: `tests/campaign/` (scope, legacy compatibility, concurrency),
`tests/leads/test_lifecycle.py` + `tests/leads/test_environment_scope.py`,
`tests/environments/test_campaign_resource_binding.py`,
`tests/telephony/test_outbound_environment_scope.py` +
`tests/telephony/test_lead_lifecycle_callbacks.py`,
`tests/test_legacy_lead_routes.py`,
`tests/test_messaging_lead_environment.py`,
`tests/test_workflow_lead_lifecycle.py`,
`tests/test_campaign_release_compatibility.py`.

## 8. Operational notes

* A tenant with **no active production environment** refuses default-context
  campaign creation, default-context lead writes and webhook lead lookups
  (safe not-found / lifecycle-denied). Restore or designate a production
  environment; nothing is invented in the meantime.
* Suspending or archiving an environment **freezes campaign writes** there
  (create/schedule/resume/run/lead mutation) while reads keep working.
* If migration 0026 reports unbound campaigns after the backfill, the
  column stays nullable on purpose: bind those tenants to an active
  production environment, then re-run the migration's tightening step.
* The release gate sees `0026_campaign_environment_scope` as the single
  migration head; a frozen artifact recording an older head is flagged as
  drift (see `tests/test_campaign_release_compatibility.py`).
