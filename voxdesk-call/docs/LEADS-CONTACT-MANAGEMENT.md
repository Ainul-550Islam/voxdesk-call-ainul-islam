# Leads and contact management

`Lead` in `app/db/models.py` is the canonical person record. This batch does not add `Contact`, `Customer`, `Prospect`, `CRMLead`, or `LeadRecord`. It does not add a second CRM, a second outbound dialer, or a second do-not-call list.

Lifecycle documentation lives in this file. There is no `docs/LEAD-LIFECYCLE.md`.

## What was reused

- `Lead` and `LeadStatus`: `new`, `queued`, `called`, `qualified`, `unqualified`, `failed`, `do_not_call`.
- Legacy collection routes: `POST` and `GET /api/tenants/{tenant_id}/leads`, and `POST .../do-not-call`. Those handlers were not rewritten, so their response shape is unchanged.
- `lead.created` and `lead.updated` via `app/integrations/crm/hooks.py`.
- CRM mapping allowlists in `app/integrations/crm/mapping.py`. `contact_from_lead` still builds a `NormalizedContact`. It does not copy `lead.custom_fields` or credential-shaped notes.
- Outbound window, max attempts, retry backoff, machine detection, and the compare-and-set in `_claim_attempt`.
- Phone normalization in `app/telephony/phone.py`. Country codes are not guessed.
- `DurableJob` for import idempotency. Delivery to an external CRM is still at-least-once, not exactly-once.

## Stored status and aliases

The `leads.status` column is not renamed and no new enum member is stored.

| Request alias | Stored value |
| --- | --- |
| contacted, engaged | called |
| appointment_set, converted | qualified |
| lost | unqualified |
| nurture | new |
| dnc | do_not_call |

`do_not_call` is terminal for calling. A later voice-consent grant does not clear it. Voice denial sets `do_not_call` through the same transition path; it does not write a parallel suppression list.

Every accepted change appends `lead_status_history`. That table is not updated to rewrite an earlier reason. Outbound claims append history after the existing claim update; they do not replace the claim.

## Identity, dedup, and merge

`lead_identities` holds normalized phone and email for one lead, plus the current owner and an optional `merged_into_id`. Matching is exact, and it is scoped to tenant and environment. The same E.164 number in another environment is not a duplicate. Formatting differences (`+1 (555) 111-0001` and `+15551110001`) are the same key. Email comparison is trimmed and lowercased.

Name similarity is token overlap. It is returned as informational and `mergeable: false`. It is never an automatic merge and never an authorization check.

Merge is explicit. A duplicate is not deleted. Its history remains. If the duplicate is do-not-call and the survivor is not, the survivor is moved to `do_not_call` so the number cannot be laundered back into the dialer. Merged leads are excluded from `next_callable_leads`.

## Score

Version `lead-score-v1` is a sum of fixed factors: phone, email, company, bounded attempts, qualified, called, and an appointment for the same phone in the same environment. The same stored state produces the same score. There is no clock, no random term, and no model call. A snapshot is appended to `lead_score_snapshots`. Scoring writes `Lead.score` only. It does not change status, so it cannot clear do-not-call.

Enrichment does not invent fields. With no wired provider the result is `NOT_CONFIGURED` and `fields` is empty.

## Segments

A segment definition is `{all: [...]}` or `{any: [...]}`. Each clause is `{field, op, value}` from a fixed field and operator list. The evaluator builds SQLAlchemy expressions from that allowlist. `sql`, `query`, `where`, and unknown fields are rejected. Membership is computed at read time against `Lead.tenant_id` and `Lead.environment_id`. There is no tenant-supplied SQL.

## Import and export

Imports are refused before parsing when the body is larger than 256 KiB, and refused when the row count exceeds 500. A row that fails validation is reported and does not abort the rows already accepted in that request. The idempotency key is the `Idempotency-Key` header or a hash of the body, stored on `DurableJob` as `lead_import`. A replay returns the stored result and does not insert again. That is idempotent for a committed import. It is not a claim of exactly-once external side effects.

Exports are capped at 1,000 rows. Custom-field keys containing `api_key`, `token`, `secret`, `password`, `authorization`, `credential`, or `payload` are omitted. Nested objects are omitted. Formula-leading cells are prefixed.

## Activities and tasks

The timeline references `Call`, `Appointment`, and `LeadTask` by id. It does not select `Turn.text` and does not copy a transcript onto `Lead`. Tasks move `open -> assigned -> in_progress -> completed|cancelled`. Completion sets `completed_at`. It does not delete the row.

## API

New routes are mounted beside the legacy collection. They use the authenticated tenant from `scoped_permission`. A path or body `tenant_id` that disagrees is a 404, same as a missing row.

- `POST /api/tenants/{tenant_id}/lead-records`
- `GET /api/tenants/{tenant_id}/lead-records`
- `GET|PATCH /api/tenants/{tenant_id}/leads/{lead_id}`
- `POST .../transition`, `.../owner`, `.../score`, `.../consent`, `.../enrich`, `.../duplicates`
- `POST /api/tenants/{tenant_id}/lead-merges`
- `GET|POST .../leads/{lead_id}/activities`
- `POST .../leads/{lead_id}/tasks` and `PATCH .../lead-tasks/{task_id}`
- `POST /api/tenants/{tenant_id}/lead-imports`
- `GET /api/tenants/{tenant_id}/lead-exports`
- `POST|GET /api/tenants/{tenant_id}/lead-segments` and `GET .../members`

Permissions are the existing `lead:read`, `lead:create`, and `lead:update`. No new role table was added.

## Persistence

`alembic/versions/0025_enterprise_leads.py` revises `0024_qa_conversation_intelligence`. It creates the supplemental tables above. It does not alter `LeadStatus`.

`app/db/models.py` imports `app.leads.models` so `create_all` and metadata see those tables. `app/main.py` includes the four routers. Both files are outside the 30 target paths and are listed as such in the batch report.

## Security

- Queries that read or write a lead filter by tenant and environment.
- A client tenant id cannot retarget another tenant.
- Do-not-call is checked in `place_call`, in the claim `UPDATE`, and in `next_callable_leads`. Voice-consent denial is the same status, with an additional consent lookup so a denial is not dialed if the status write was missed.
- The claim remains a conditional `UPDATE` on id, tenant, seen attempts, attempt cap, and status in `new|queued`. Two workers cannot both increment the same seen attempt.
- Segments cannot carry raw SQL.
- Exports and CRM mapping drop credential-shaped fields. Provider payloads are not stored on enrichment rows.

## Not claimed

PostgreSQL was not available for `alembic upgrade head` in this workspace (connection refused on port 5432). SQLite tests do not prove that upgrade. Live telephony was not placed. Production-ready is not claimed. External CRM delivery is not exactly-once.
