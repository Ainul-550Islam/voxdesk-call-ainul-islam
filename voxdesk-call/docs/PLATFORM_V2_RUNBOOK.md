# Platform v2 Runbook — workforce, white-label, mobile

This is the operating document for the Batch 07 surface: the WFM engine
(`app/platform_v2/wfm`), the white-label domain/branding stack
(`app/platform_v2/whitelabel`), the mobile push stack (`app/platform_v2/mobile`),
the scheduled passes that keep all three honest, and the infrastructure those
passes are deployed with.

It is written for the person on call, so every section answers three questions:
*what should be true*, *how do I check it*, and *what do I do when it is not*.
Commands assume the repository root (`voxdesk-call/`) and `PYTHONPATH=.`.

Nothing in this document was applied to a cloud account. Terraform was formatted
and validated; Helm was linted and rendered; Caddy configs were validated. No
`terraform apply`, no Kubernetes rollout, no CA issuance outside the local fakes
in the test suite. See §9 for exactly what was and was not exercised.

---

## 1. What is new in this batch

| Area | Module root | API prefix | Permissions |
| --- | --- | --- | --- |
| Workforce management | `app/platform_v2/wfm/` | `/api/wfm` | `wfm:read`, `wfm:write`, `wfm:schedule`, `wfm:publish`, `wfm:approve` |
| White-label | `app/platform_v2/whitelabel/` | `/api/whitelabel`, `/api/platform/tls` | `brand:read`, `brand:write`, `domain:read`, `domain:write`, `domain:verify` |
| Mobile push | `app/platform_v2/mobile/` | `/api/mobile` | `mobile:read`, `mobile:write`, `mobile_push:send` |

Seventy-four platform paths are mounted (see `contracts/openapi.json`). The
permission bundles are policy, not a second authorisation model: they live in
`app/platform_v2/permissions.py` and are unioned into `app/auth/rbac.py` once.

Role → bundle:

| Role | Bundle | Consequence |
| --- | --- | --- |
| `AGENT` | `WFM_SELF_SERVICE` | reads own rota, requests leave, swaps shifts, registers own phone |
| `MANAGER` | `WFM_SUPERVISOR` | builds/adjusts the week, approves leave, swaps, adherence exceptions |
| `ADMIN` | `WFM_ADMIN` | publishes rota, rebrands, claims domains, sends push |
| `VIEWER` | `WFM_OBSERVER` | reads numbers only |
| `OWNER` | everything | derived from the enum, not listed in a bundle |

Publishing is deliberately *not* a manager permission: generating a rota changes
nothing until somebody publishes it, and the person who rearranges the week is
not always the person allowed to promise it to staff.

---

## 2. Schema

Revision `0068_platform_v2` (revises `0067_integrations_v2`) creates 26 tables:

* **Workforce (13)** — `wfm_interval_history`, `wfm_forecasts`,
  `wfm_forecast_accuracy`, `wfm_shift_templates`, `wfm_labor_rules`,
  `wfm_schedules`, `wfm_schedule_versions`, `wfm_shifts`,
  `wfm_time_off_requests`, `wfm_time_off_balances`, `wfm_swaps`,
  `wfm_open_shift_bids`, `wfm_adherence_exceptions`.
* **White-label (8)** — `wl_domains`, `wl_domain_checks`, `wl_certificates`,
  `wl_themes`, `wl_theme_versions`, `wl_assets`, `wl_email_sender_domains`,
  `wl_sender_domain_checks`.
* **Mobile (5)** — `mobile_devices`, `mobile_push_tokens`,
  `mobile_push_deliveries`, `mobile_auth_codes`, `mobile_sessions`.

```bash
alembic heads                 # exactly one head: 0068_platform_v2
alembic upgrade head
alembic downgrade 0067_integrations_v2
python3 -m pytest tests/test_alembic_models_in_sync.py tests/platform_v2/test_migration_0068.py -q
```

Two schema decisions that surface in operations:

* `wl_domains.domain` is unique across the whole platform, not per tenant. A
  hostname can only route to one tenant, so a second claim is refused rather than
  silently shadowed.
* `wfm_interval_history.queue_key` is `str(queue_id)` with `""` meaning
  "environment-wide". It is a string rather than a nullable FK because PostgreSQL
  and SQLite both treat NULLs as distinct in a unique index, which would let the
  same interval be written twice.

---

## 3. Configuration

Every setting is read once through `app/platform_v2/settings.py`
(`get_plat2_settings()`, cached; `reset_plat2_settings_cache()` in tests). The
Helm CronJobs pass the same names with the same defaults — asserted by
`tests/platform_v2/test_helm_cronjobs.py`, so the two cannot drift.

### 3.1 Workforce

| Setting | Default | Meaning |
| --- | --- | --- |
| `WFM_INTERVAL_MINUTES` | `15` | rollup/forecast grid; must be 15 or 30 (a CHECK constraint enforces it) |
| `WFM_TIMEZONE` | `UTC` | zone the interval grid and the rota are built in |
| `WFM_FORECAST_HORIZON_DAYS` | `42` | maximum forecast horizon; the API caps at 14 unless asked |
| `WFM_MIN_HISTORY_DAYS` | `28` | days of history below which a forecast is refused |
| `WFM_WORKDAY_START_MINUTE` / `WFM_WORKDAY_MINUTES` | `540` / `480` | fallback working day when no shift template exists |
| `WFM_SOLVER_TIMEOUT_SECONDS` / `WFM_SOLVER_SEED` | `30` / `20260707` | CP-SAT budget and deterministic seed |
| `WFM_SERVICE_LEVEL`, `WFM_ANSWER_SECONDS`, `WFM_MAX_OCCUPANCY` | `0.8`, `20`, `0.85` | staffing target defaults |
| `WFM_SHRINKAGE`, `WFM_AHT_SECONDS` | `0.0`, `240` | productivity assumptions |
| `WFM_STAFFING_MODEL` | `erlang_c` | `erlang_c` (blocking) or `erlang_a` (abandonment-aware) |
| `WFM_PATIENCE_SECONDS` | `300` | mean patience, only used by `erlang_a` |

`WFM_TIMEZONE` is the *deployment* zone. It is stored on every history row and
every forecast, so changing it does not reinterpret last month's buckets.

### 3.2 White-label

| Setting | Default | Meaning |
| --- | --- | --- |
| `CUSTOM_DOMAIN_CNAME_TARGET` | `edge.voxdesk.app` | what tenants point their CNAME at |
| `ACME_DIRECTORY_URL` | Let's Encrypt production | ACME directory; use staging when testing |
| `ACME_CONTACT_EMAIL` | empty | account contact; empty means no contact (staging only) |
| `DNS_CHECK_RESOLVERS` | system defaults | comma-separated resolvers used to verify records |
| `CUSTOM_DOMAIN_EDGE_IPS` | empty | pins the A records the CNAME must reach |
| `DNS_CHECK_TIMEOUT_SECONDS` | `5` | per resolver |
| `DOMAIN_VERIFY_TTL_HOURS` | `72` | how long a verification is trusted before re-check |
| `DOMAIN_REVERIFY_HOURS` | `24` | how often the reverify pass revisits a verified domain |
| `TLS_ASK_ENABLED` | `true` | serve `GET /api/platform/tls/ask` for Caddy's on-demand TLS |
| `TLS_ASK_TOKEN` | empty | shared secret Caddy sends; required when on-demand TLS is in use |
| `BRANDING_MAX_ASSET_BYTES` / `BRANDING_MAX_THEME_BYTES` | `512000` / `32768` | upload caps |
| `BRANDING_ASSET_DIR` | `var/branding` | where sanitised assets are written |
| `EMAIL_DKIM_SELECTOR` / `EMAIL_DKIM_PUBLIC_KEY` | `voxdesk` / empty | DKIM records published to tenants |
| `EMAIL_FROM_DOMAIN`, `EMAIL_SPF_INCLUDE` | `mail.voxdesk.app`, empty | SPF defaults |

`TLS_ASK_ENABLED=true` with `TLS_ASK_TOKEN` empty is a startup-time warning
(`settings.py` validation), because an unauthenticated `ask` endpoint lets anyone
ask the edge to obtain certificates for hostnames they do not own. Fail closed:
set the token, or turn the endpoint off.

### 3.3 Mobile

| Setting | Default | Meaning |
| --- | --- | --- |
| `FCM_ENABLED`, `FCM_PROJECT_ID`, `FCM_SERVICE_ACCOUNT_JSON`, `FCM_ENDPOINT` | `false`, empty, empty, `https://fcm.googleapis.com` | Android delivery |
| `APNS_ENABLED`, `APNS_KEY_ID`, `APNS_TEAM_ID`, `APNS_PRIVATE_KEY_P8`, `APNS_BUNDLE_ID` | `false`, empty… | iOS delivery |
| `APNS_USE_SANDBOX`, `APNS_ENDPOINT` | `true`, empty | sandbox key pair vs production key pair |
| `PUSH_TOKEN_GC_DAYS` | `30` | a device unseen for this long loses its token |
| `PUSH_QUIET_HOURS_MAX_SPAN` | `12` | longest quiet window a device may request |
| `MOBILE_AUTH_PER_MINUTE` | `30` | per-IP rate limit on the OAuth entry points |

`FCM_ENABLED=true` with an empty project id is a validation error, not a warning:
the alternative is discovering it when a push fails silently three days later.
`APNS_ENDPOINT` is derived from `APNS_USE_SANDBOX` when left empty
(`api.sandbox.push.apple.com` vs `api.push.apple.com`).

---

## 4. Scheduled passes

Nine cycles, defined once in `app/platform_v2/jobs.py` (`LOOP_NAMES`, `CYCLES`,
`*_INTERVAL_SECONDS`). The long-running scheduler (`python -m scripts.scheduler`)
registers each as a locked tick; the Helm chart runs the same cycles as CronJobs
for deployments without a scheduler process. Both paths take a Redis lock named
`platform_v2:<cycle>`, so running both is safe — the lock decides, not the
topology.

| Cycle | Cadence | CronJob schedule | Job type | What it does |
| --- | --- | --- | --- | --- |
| `wfm_rollup` | 1 h | `5 * * * *` | `wfm.history.rollup` | rebuild interval history, per queue and environment |
| `wfm_forecast` | 24 h | `15 3 * * *` | `wfm.forecast.generate` | generate tomorrow's forecast per environment |
| `wfm_forecast_score` | 6 h | `40 */6 * * *` | `wfm.forecast.score` | score forecasts whose horizon has partly elapsed |
| `wfm_adherence` | 1 h | `20 * * * *` | `wfm.adherence.sweep` | evaluate yesterday's rota against the state ledger |
| `wl_domain_reverify` | 30 min | `*/30 * * * *` | `wl.domain.reverify` | re-check domains whose verification window elapsed |
| `wl_cert_refresh` | 15 min | `*/15 * * * *` | `wl.cert.refresh` | observe certificates; activate on issue |
| `wl_sender_domain` | 6 h | `25 */6 * * *` | `wl.sender_domain.check` | re-check SPF/DKIM/DMARC for pending senders |
| `mobile_push_retry` | 5 min | `*/5 * * * *` | `mobile.push.retry` | re-send deliveries that failed transiently |
| `mobile_token_gc` | 24 h | `0 4 * * *` | `mobile.token.gc` | deactivate tokens of devices unseen past the horizon |

All nine `JobType` members have registered handlers
(`app/platform_v2/jobs.py`, `register_handler_function`), asserted by
`tests/test_scheduler_wires_all_v2_jobs.py`.

Run one cycle by hand — same code path the scheduler and the CronJob use:

```bash
python3 scripts/platform_jobs.py --list
python3 scripts/platform_jobs.py wfm_rollup
python3 scripts/platform_jobs.py wfm_forecast --json /tmp/forecast.json
```

Exit codes: `0` ran or another worker held the lock, `1` the cycle failed, `2` bad
arguments. A `0` because the lock was held is normal during a deploy overlap; it
is only suspicious if it happens on every attempt.

### 4.1 The WFM chain

```
telephony rows ──wfm_rollup──▶ wfm_interval_history ──wfm_forecast──▶ wfm_forecasts
                                                   │                        │
                                                   │                        ├─▶ /api/wfm/staffing (Erlang B/C)
                                                   │                        ├─▶ /api/wfm/schedules/{id}/solve (CP-SAT)
                                                   │                        └─▶ /api/wfm/intraday
                                                   └──wfm_forecast_score──▶ wfm_forecast_accuracy
```

Invariants worth knowing before you debug a number:

* **A gap is not a zero.** An interval with no traffic *and* no agent seconds is
  written with `is_gap=true` and is excluded from forecasting and from intraday
  actuals. An interval with traffic but no coverage is a real zero and stays.
* **Handle time is credited to the interval the call ended in.** A call that
  started 08:59 and ended 09:04 belongs to 09:00.
* **Accuracy is measured, never asserted.** `wape`/`mape`/`bias` are fractions
  computed from stored actuals; there is no target column anywhere in the schema,
  and a forecast with nothing elapsed is skipped rather than scored as perfect.
* **Staffing needs agent-minutes.** `agents_available` on a history row is
  minutes, not head-count, and `erlang_a.service_level` is the probability of
  answering within `target_seconds`, not a percentage of calls.

### 4.2 The publish path

```
POST /api/wfm/schedules                        create the week (draft)
POST /api/wfm/schedules/{id}/solve             CP-SAT; records a draft version + shifts
GET  /api/wfm/schedules/{id}                   read versions, coverage, shifts
POST /api/wfm/schedules/{id}/publish           {version_id, min_coverage_ratio?}
GET  /api/wfm/schedules/{id}/diff?before_version_id=&after_version_id=
POST /api/wfm/schedules/{id}/archive
GET  /api/wfm/schedules/{id}                   versions + `shifts[]` in payroll shape
GET  /api/wfm/my-shifts                        the caller's own rota, same shape
```

`shifts[]` is already the export shape (`starts_at_utc` and `starts_at_local`
side by side, `paid_hours` with the unpaid break removed, `template_id` empty for a
manual shift), so a payroll extract is a field selection rather than a second
transform — which is why there is no separate export endpoint to keep in step.

`publish` returns the per-person change set (`diff`, `notifications`) so the push
job tells only the people whose week actually moved. A version is immutable once
published: changing it means a new version, and the old one is `superseded`.
Shifts belonging to a superseded version become `dropped` — except `swapped` and
`open`, which are human decisions about a specific shift and survive a re-publish.

A `409` from `publish` means one of: the version is already current, it has no
solver report (re-run the solver), or coverage is below the requested floor (the
error body carries `coverage_ratio`, `required`, `shortfall_agent_intervals`).

### 4.3 Leave, swaps and bids

```
GET  /api/wfm/time-off?statuses=requested&user_id=   list
POST /api/wfm/time-off                              {kind, starts_at, ends_at, reason, hours?}
POST /api/wfm/time-off/{id}/decide                  {approved, note?, enforce_balance?}
POST /api/wfm/time-off/{id}/cancel
POST /api/wfm/time-off/accruals                     {user_id, hours, note?}
GET  /api/wfm/time-off/balances[?user_id=]

GET  /api/wfm/swaps                                 POST {shift_id, target_user_id, note?}
POST /api/wfm/swaps/{id}/accept                     the target accepts
POST /api/wfm/swaps/{id}/decide                     {approved, note?}
POST /api/wfm/swaps/{id}/cancel
POST /api/wfm/shifts/{shift_id}/open                put a shift on the open board
GET|POST /api/wfm/shifts/{shift_id}/bids            {preference} → scored bid
POST /api/wfm/shifts/{shift_id}/award               lowest score wins, rest rejected
```

Approving leave deducts hours on **every** approval, but only refuses when
`enforce_balance` is true and the kind is not in `ALLOW_NEGATIVE`
(`sick`, `bereavement`). If the balance was never accrued, the approval returns
`409` with `requested_hours` in the body — accrue first, or pass
`enforce_balance=false` (the hours are still booked, and the balance may go
negative; that is the honest record of what happened).

A swap whose rule check fails is **recorded, not refused**: the manager sees
`rule_check.violations` and decides. Accepting an invalid swap auto-rejects it.

### 4.4 Adherence and intraday

* `/api/wfm/adherence?day=` — per-shift adherence/conformance, unplanned minutes,
  and the exceptions the sweep persisted. Worked time excludes breaks.
* `/api/wfm/intraday?queue_key=&as_of=` — plan vs actual per interval, plus a
  re-forecast of the rest of the day. The scale is shrunk toward 1.0 by sample
  size (`n / (n + 4)`) and clamped to `[0.33, 3.0]`: re-planning on two intervals
  is how a team ends up chasing its own tail. `alert` is `ok` / `watch` / `act`.

---

## 5. White-label

### 5.1 Domain lifecycle

```
POST   /api/whitelabel/domains                 {domain} → ownership token + instructions
POST   /api/whitelabel/domains/{id}/verify     publishes nothing; verifies DNS
GET    /api/whitelabel/domains/{id}/checks     per-resolver observations
GET    /api/whitelabel/domains/{id}/certificate
POST   /api/whitelabel/domains/{id}/refresh-certificate
POST   /api/whitelabel/domains/{id}/enable | /disable
DELETE /api/whitelabel/domains/{id}
```

States: `pending` → `verified` (ownership TXT present and the CNAME routes to the
edge) → `issuing_cert` → `active`. `failed` carries `failure_reason` and the failing
checks. Verification is *not* certificate issuance: a domain can be `verified` and
never reach `active` if the edge cannot complete the ACME challenge.

The ownership token is returned **once**, at creation (`ownership_token`,
`ownership_token_notice`). Re-issue means deleting and re-claiming the domain.

### 5.2 On-demand TLS at the edge

```
/api/platform/tls/ask?domain=<host>     Caddy calls this before issuing
/api/platform/tls/status?domain=<host>  what the platform thinks
```

`ask` answers 200 only for a domain that is `ACTIVE` for a live tenant and sends
`TLS_ASK_TOKEN` when one is configured; everything else is refused. This is the
control that stops the edge from becoming an open certificate authority for
arbitrary hostnames. `infra/caddy/tenant-domains.caddy` wires `on_demand_tls` to
it; `infra/caddy/edge-global.caddy` holds the shared global options.

### 5.3 Branding

```
GET|POST /api/whitelabel/theme                  read / publish a version
POST     /api/whitelabel/theme/validate         reports, never raises
GET      /api/whitelabel/theme/versions
POST     /api/whitelabel/theme/versions/{id}/activate
GET|POST /api/whitelabel/assets                 list / upload (multipart: kind + file)
GET      /api/whitelabel/assets/{id}/content
DELETE   /api/whitelabel/assets/{id}
GET      /api/whitelabel/templates
POST     /api/whitelabel/templates/{name}/preview
```

`theme/validate` always answers 200 with `{valid, errors, contrast}`. Contrast is
WCAG 2.1 against the configured pairs; `#777777` on white is 4.478:1 and fails the
4.5:1 threshold, which is the point of having the check. SVG uploads are
sanitised — script content is removed and reported in `removed_from_svg`; a
sanitised asset is still stored, because the operator needs to see what was
stripped.

### 5.4 Sender domains (SPF/DKIM/DMARC)

```
GET|POST /api/whitelabel/email-domains
GET      /api/whitelabel/email-domains/{id}/records
POST     /api/whitelabel/email-domains/{id}/verify
DELETE   /api/whitelabel/email-domains/{id}
```

`records` returns the three records the tenant must publish (`records`) and the
per-record checks the platform performed (`checks`). A sender is `verified` only
when all three pass; `wl_sender_domain` re-checks pending and failed senders every
six hours.

### 5.5 Widget isolation

The public widget is served from `/api/whitelabel/public/widget/{public_key}/…`
with a per-tenant CSP, `frame_ancestors` restricted to the tenant's allowed
origins, and no tenant session. An unknown `public_key` is a `404`; it never falls
back to a default tenant.

---

## 6. Mobile

### 6.1 Device lifecycle

```
POST   /api/mobile/devices                              {platform, device_id, app_version, os_version}
POST   /api/mobile/devices/{id}/token                   {token, provider}
POST   /api/mobile/devices/{id}/quiet-hours             {start, end, timezone, enabled}
GET    /api/mobile/devices
DELETE /api/mobile/devices/{id}                         → 204, revokes tokens
GET    /api/mobile/providers                            fcm/apns configured? sandbox?
GET    /api/mobile/deliveries?limit=                    ledger + summary
POST   /api/mobile/push/test                            {template, title, body, urgent}
```

Quiet hours accept `start`/`end`/`timezone`/`enabled`; the body is
`extra="forbid"`, so a typo is a `422` rather than a silently ignored field. A
span longer than `PUSH_QUIET_HOURS_MAX_SPAN` hours is refused.

Delivery outcomes are `queued` → `sent` / `failed` / `dropped`. `dropped` means a
policy decision (quiet hours, pushed disabled, revoked device) and is never
retried; `failed` with a transient reason is picked up by `mobile_push_retry`,
bounded by `MAX_ATTEMPTS`.

### 6.2 OAuth for native apps

```
POST /api/mobile/oauth/authorize    → code (PKCE)
POST /api/mobile/oauth/token        code + verifier → access + refresh
POST /api/mobile/oauth/refresh      refresh → new pair (rotation)
POST /api/mobile/oauth/revoke       RFC 7009
GET|DELETE /api/mobile/sessions[/{family_id}]
```

A device mismatch or a spent/expired code is `409`; an unknown code or user is
`404`. A wrong verifier leaves `consumed_at` NULL so the legitimate client can
still complete — the code is only burned by a successful exchange. Using an
access token from a revoked device is `409`.

### 6.3 SDKs

| SDK | Path | What was verified |
| --- | --- | --- |
| React Native / TypeScript | `sdk/react-native/` | `npm ci`, `tsc --noEmit`, `npm run build`, 39 vitest tests |
| Android (Kotlin) | `sdk/android/` | structural: Gradle manifest, 10 sources, 2 JUnit sources |
| iOS (Swift) | `sdk/ios/` | structural: `Package.swift`, 5 sources, 2 test files (19 cases) |

The native SDKs are **not compiled here**: there is no Gradle/Kotlin/Xcode
toolchain in this environment, and no store submission has been made. Structural
checks run through `scripts/verify_mobile_sdks.py`, which also cross-checks every
`(method, path)` the SDKs call against the OpenAPI document (33 pairs, 0 unknown):

```bash
python3 scripts/verify_mobile_sdks.py --json
python3 scripts/verify_mobile_sdks.py --skip-endpoints   # structural only
```

---

## 7. Platform-admin console

`dashboard-next/app/platform-admin/` — a Next.js App Router section with seven
pages (overview, workforce, schedule, domains, branding, email, mobile), a typed
client in `lib/api.ts`, and 137 vitest tests across 9 files.

```bash
cd dashboard-next
npx tsc --noEmit
npx vitest run
npm run build
```

Client conventions that are not optional: writes send an `Idempotency-Key`
(the caller holds the key across retries, otherwise a retried publish runs twice),
and a `204` is never parsed as JSON.

The overview page gates on `GET /api/wfm/history/counts`: when `enough` is false
the forecast, staffing and schedule actions are disabled with the missing-day
count on screen. An empty forecast is never rendered as a real one.

---

## 8. Infrastructure

```
infra/terraform/
  versions.tf                 provider + version pins, no backend
  modules/{network,eks,rds,redis,alb,assets,failover,observability}/
  envs/{dev,staging,prod}/    one root per environment, modules wired
  README.md                   layout, and why the failover module exists
infra/helm/voxdesk/
  values.yaml
  templates/wfm-and-platform-cronjobs.yaml   the nine passes
infra/caddy/{edge-global,tenant-domains}.caddy
.github/workflows/{terraform,mobile}.yml
```

Each environment wires: multi-AZ network, EKS with managed node groups, an RDS
PostgreSQL instance, an ElastiCache Redis cluster, an ALB with ACM certs, an S3
bucket for branding assets, observability alarms, and a **failover** module
(Route 53 health checks + a warm secondary region) so a region loss removes a DNS
record rather than the product.

```bash
export PATH="$HOME/.local/bin:$PATH"
terraform fmt -check -recursive infra/terraform
for d in infra/terraform/modules/* infra/terraform/envs/*; do (cd $d && terraform init -backend=false -input=false && terraform validate); done
helm lint infra/helm/voxdesk
helm template voxdesk infra/helm/voxdesk >/dev/null
caddy validate --config infra/caddy/edge-global.caddy --adapter caddyfile
caddy validate --config infra/caddy/tenant-domains.caddy --adapter caddyfile
```

All of the above were run: `terraform fmt` clean, `terraform validate` clean for
all 11 directories, `helm lint` 0 failures (14 CronJobs render, 9 of them
Batch 07), both Caddy configs valid. **Nothing was planned against real state and
nothing was applied.**

`platform.enabled=false` removes the nine CronJobs and leaves the rest of the
chart alone; boolean values are read with `dig` because Helm's `default` treats
`false` as empty, which would make the block impossible to switch off.

---

## 9. Testing: what is proven, and what is not

```bash
# the batch's own suite (~682 tests, 10 opt-in skips)
python3 -m pytest tests/platform_v2 -q -m "not live"

# the gates
python3 scripts/verify_wiring.py
python3 scripts/verify_no_dead_routers.py
bash scripts/build_api_docs.sh          # then tests/test_openapi_contract_matches_live_app.py
python3 scripts/generate_feature_matrix.py --output /tmp/matrix.md
diff -u docs/SALES/FEATURE_MATRIX_VERIFIED.md /tmp/matrix.md
```

Live checks are opt-in and skip cleanly without credentials — they name what is
missing rather than passing quietly:

| Test module | Environment |
| --- | --- |
| `test_live_custom_domain_tls.py` | `VOXDESK_LIVE_DOMAIN`, `VOXDESK_LIVE_EDGE_URL`, `VOXDESK_LIVE_OWNERSHIP_TOKEN` |
| `test_live_push_delivery.py` | `VOXDESK_LIVE_FCM_TOKEN`, `VOXDESK_LIVE_APNS_TOKEN` (plus real provider credentials) |

Honest limits, stated once: Terraform was validated, never applied; the native
SDKs were checked structurally, never compiled; ACME/DNS and FCM/APNs behaviour is
tested against local fakes, and the real protocol only runs in the opt-in modules
above; forecast accuracy is always computed from stored data at runtime.

---

## 10. Troubleshooting

| Symptom | Likely cause | Action |
| --- | --- | --- |
| `422` from `/forecasts` with `insufficient_history` | fewer than `WFM_MIN_HISTORY_DAYS` days of history | check `GET /history/counts`; keep the rollup running, then retry |
| `/history/counts` says `enough: false` but rows exist | the days are gap-only, or the queue key differs | `GET /history?include_gaps=true`, then compare `queue_key` |
| `409` from `/schedules/{id}/solve` | no forecast covers the week | generate a forecast whose horizon covers `week_start` |
| `422` `InfeasibleSchedule` from solve | the rules and the demand cannot both hold (e.g. 448 h needed, 5 employees) | raise `max_hours_per_week`, add templates/staff, or allow shortfall |
| `409` from publish | already current / no solver report / below coverage floor | read `detail.coverage_ratio` and `shortfall_agent_intervals` |
| Leave approval `409` with `requested_hours` | balance was never accrued | `POST /time-off/accruals`, or decide with `enforce_balance=false` |
| Domain stuck in `pending` | ownership TXT missing or CNAME not on the edge | `GET /domains/{id}/checks` — each check names the resolver and what it saw |
| Domain stuck in `issuing_cert` | ACME challenge cannot reach the edge | check `GET /domains/{id}/certificate` `error`; verify the Caddy `ask` endpoint and `TLS_ASK_TOKEN` |
| `ask` returns 403 for a live domain | domain is not `ACTIVE`, or the token is missing/mismatched | `GET /api/platform/tls/status?domain=` shows the state the edge will see |
| Sender domain `failed` | one of the three records is missing | `GET /email-domains/{id}/records` → `checks` names the record |
| Push deliveries all `dropped` | quiet hours, push disabled, or revoked device | `GET /api/mobile/providers`, then the device row |
| Push deliveries `failed` repeatedly | provider credential or token invalid | check `deliveries[].error_code`; expired tokens are deactivated by `mobile_token_gc` |
| CronJob runs but does nothing | another worker holds `platform_v2:<cycle>` | expected during overlap; investigate only if it never runs |
| Console disables every action | `history/counts.enough` is false | this is the gate, not a bug; it prints the missing days |

---

## 11. Change checklist for the next pass

1. Add the setting to `app/platform_v2/settings.py` **with an explicit alias**, not
   an `env_prefix`, and pass it in the Helm template — `test_helm_cronjobs.py`
   compares the two.
2. Add the job type to `app/jobs/types.py`, the handler to `app/platform_v2/jobs.py`,
   the cycle to `LOOP_NAMES`/`CYCLES`, the tick to `scripts/scheduler.py`, and the
   CronJob to the chart. `tests/test_scheduler_wires_all_v2_jobs.py` and
   `test_helm_cronjobs.py` fail if any of those is missing.
3. Add the permission to `app/auth/permissions.py` and the bundle in
   `app/platform_v2/permissions.py`; do not mint a permission nothing references.
4. Mount the router in `app/main.py`, then run `verify_wiring.py` and
   `verify_no_dead_routers.py`.
5. Regenerate `contracts/openapi.json` with `scripts/build_api_docs.sh`; never edit it.
6. Public routes go on `security_harness/authz_matrix.PUBLIC_ROUTES` with a reason —
   an unlisted public route fails `tests/test_boot_and_mount.py`.
7. If the schema changes, add a revision and import the model in `alembic/env.py`,
   then confirm `alembic heads` is a single head and the autogenerate diff is empty.
8. Update `docs/SALES/FEATURE_MATRIX_VERIFIED.md` only through
   `scripts/generate_feature_matrix.py`.
