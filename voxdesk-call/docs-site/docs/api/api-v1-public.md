# `/api/v1/public`

34 operations.

## GET `/api/v1/public/analytics/summary`

**Get Public Analytics Summary**

Keep tenant-private metrics out of the public endpoint; no synthetic zeros.

*Tags:* public-home

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | AnalyticsSummaryResponse | Successful Response |

## GET `/api/v1/public/chat/budget`

**Budget**

Today's counters for this key. Read-only; consumes nothing.

*Tags:* public-chat

### Parameters

| Name | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `public_key` | query | string | yes |  |

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | object | Successful Response |
| 422 | HTTPValidationError | Validation Error |

## POST `/api/v1/public/chat/session`

**Create Chat Session**

Open a chat session for the embedded chat widget.

Gates run cheapest-first — key and origin, then toll-fraud heuristics, then captcha, then the daily budget — so a bot cannot make the server do expensive work before being refused.

*Tags:* public-chat

### Request body — `application/json`

Schema: `ChatSessionRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `public_key` | string | yes |  |
| `origin` | string \| null | no |  |
| `visitor_id` | string \| null | no |  |
| `captcha_token` | string \| null | no |  |
| `metadata` | object | no |  |

Example:

```json
{
  "captcha_token": "03AGdBq...",
  "origin": "https://acme.example.com",
  "public_key": "vd_pk_live_9f2c...",
  "visitor_id": "v-8f21"
}
```

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 201 | object | Successful Response |
| 422 | HTTPValidationError | Validation Error |

## POST `/api/v1/public/contact-sales`

**Submit Contact Sales**

Contact-sales submission, rate limited per IP.

*Tags:* public-site

### Request body — `application/json`

Schema: `PublicContactSalesCreate`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `full_name` | string | yes |  |
| `work_email` | string | yes |  |
| `company_name` | string | yes |  |
| `job_title` | string | no |  |
| `phone_number` | string \| null | no |  |
| `monthly_call_volume` | string | no |  |
| `primary_use_case` | string | no |  |
| `message` | string | no |  |
| `source_path` | string | no |  |

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 201 | PublicContactSalesRead | Successful Response |
| 422 | HTTPValidationError | Validation Error |

## GET `/api/v1/public/demo`

**Demo Status**

Everything the public page needs, and nothing about any tenant.

*Tags:* public-demo

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | object | Successful Response |

## POST `/api/v1/public/demo/call`

**Create Demo Call**

Place one demo call, or explain precisely why not.

*Tags:* public-demo

### Request body — `application/json`

Schema: `DemoCallBody`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `number` | string | yes |  |
| `consent` | boolean | yes | The visitor must tick this; the server re-checks it. |
| `consent_version` | string | yes |  |
| `captcha_token` | string \| null | no |  |

Example:

```json
{
  "captcha_token": "03AGdBq...",
  "consent": true,
  "consent_version": "2026-11-08",
  "number": "+15550100111"
}
```

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 201 | object | Successful Response |
| 422 | HTTPValidationError | Validation Error |

## POST `/api/v1/public/demo/verification`

**Request Verification**

Send a one-time code to a number the visitor claims to control.

Nothing is dialled until that code is confirmed, which is what stops the demo being pointed at somebody else's phone.

*Tags:* public-demo

### Request body — `application/json`

Schema: `VerificationRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `number` | string | yes | E.164, e.g. +15550100111 |

Example:

```json
{
  "number": "+15550100111"
}
```

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 202 | object | Successful Response |
| 422 | HTTPValidationError | Validation Error |

## POST `/api/v1/public/demo/verification/confirm`

**Confirm Verification**

Confirm the code and add the number to the demo allow-list.

Single use, expires in 15 minutes, and five wrong attempts lock the number until a new code is requested.

*Tags:* public-demo

### Request body — `application/json`

Schema: `VerificationConfirmRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `number` | string | yes |  |
| `code` | string | yes |  |

Example:

```json
{
  "code": "482913",
  "number": "+15550100111"
}
```

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | object | Successful Response |
| 422 | HTTPValidationError | Validation Error |

## GET `/api/v1/public/health`

**Public Health**

Report this endpoint's liveness without implying DB/cache/provider readiness.

*Tags:* public-home

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | object | Successful Response |

## GET `/api/v1/public/home`

**Get Public Home**

Return public labels and route-registration evidence, not runtime readiness.

*Tags:* public-home

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | HomeResponse | Successful Response |

## POST `/api/v1/public/share/session/verify`

**Verify**

Validate a session token minted by `/redeem`.

The voice and chat bootstraps call this instead of trusting the browser's
copy of the claims: an expired or tampered token has to be refused by the
server, not by a client that had already given up.

*Tags:* share-links

### Request body — `application/json`

Schema: `SessionVerify`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `token` | string | yes |  |

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | object | Successful Response |
| 422 | HTTPValidationError | Validation Error |

## GET `/api/v1/public/share/{token}`

**Describe**

What this link is, without consuming a use.

*Tags:* share-links

### Parameters

| Name | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `token` | path | string | yes |  |

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | object | Successful Response |
| 422 | HTTPValidationError | Validation Error |

## POST `/api/v1/public/share/{token}/redeem`

**Redeem**

Consume one use and return a short-lived session token.

*Tags:* share-links

### Parameters

| Name | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `token` | path | string | yes |  |

### Request body — `application/json`

Schema: `RedeemRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `passcode` | string \| null | no |  |

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | object | Successful Response |
| 422 | HTTPValidationError | Validation Error |

## GET `/api/v1/public/site/manifest`

**Public Site Manifest**

Public route manifest for the marketing site.

*Tags:* public-site

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | PublicSiteManifest | Successful Response |

## GET `/api/v1/public/site/pricing`

**Public Site Pricing**

Public pricing data, generated from the plan catalogue so the page cannot quote a price the product will not charge.

*Tags:* public-site

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | array of PublicPricingTier | Successful Response |

## GET `/api/v1/public/site/route-boundary`

**Public Route Boundary Check**

Whether a path belongs to the marketing site or the application, so the edge never shows a marketing page to a signed-in user.

*Tags:* public-site

### Parameters

| Name | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `path` | query | string | no |  |
| `authenticated` | query | boolean | no |  |
| `next` | query | string | null | no |  |

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | PublicAuthBoundaryDecision | Successful Response |
| 422 | HTTPValidationError | Validation Error |

## GET `/api/v1/public/site/status`

**Public Site Status**

Public status summary. Component names and up/degraded only — never internals, never tenant data.

*Tags:* public-site

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | PublicSiteStatusSummary | Successful Response |

## GET `/api/v1/public/use-cases`

**List Use Cases**

GET /api/v1/public/use-cases — Public catalog with search, category filter, pagination.
Query params validated via UseCaseQuery schema, safe, no tenant data.

*Tags:* public-use-cases

### Parameters

| Name | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `q` | query | string | null | no | Search query |
| `category` | query | string | null | no | Category filter |
| `page` | query | integer | no |  |
| `page_size` | query | integer | no |  |

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | UseCaseListResponse | Successful Response |
| 422 | HTTPValidationError | Validation Error |

## GET `/api/v1/public/use-cases/categories`

**List Categories**

GET /api/v1/public/use-cases/categories — Backend-driven category list, no invented counts.

*Tags:* public-use-cases

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | UseCaseCategoriesResponse | Successful Response |

## GET `/api/v1/public/use-cases/health`

**Health**

Public health check

*Tags:* public-use-cases

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | object | Successful Response |

## GET `/api/v1/public/use-cases/{slug}`

**Get Use Case Detail**

GET /api/v1/public/use-cases/{slug} — Detail page, supports direct deep links.
Slug validated, safe, no tenant leakage.

*Tags:* public-use-cases

### Parameters

| Name | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `slug` | path | string | yes |  |

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | UseCaseDetailResponse | Successful Response |
| 422 | HTTPValidationError | Validation Error |

## POST `/api/v1/public/voice-demo/session`

**Create Voice Demo Session**

Create a durable DB-backed public voice demo session with an HMAC session token.

*Tags:* public-home

### Request body — `application/json`

Schema: `VoiceDemoSessionCreate`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `tenant_id` | string \| null | no |  |
| `metadata` | object | no |  |
| `ttl_seconds` | integer \| null | no |  |

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 201 | object | Successful Response |
| 422 | HTTPValidationError | Validation Error |

## GET `/api/v1/public/voice-demo/session/{session_id}`

**Get Voice Demo Session**

Return durable public demo session state (requires valid HMAC session token).

*Tags:* public-home

### Parameters

| Name | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `session_id` | path | string | yes |  |
| `token` | query | string | null | no |  |
| `X-Demo-Session-Token` | header | string | null | no |  |
| `Authorization` | header | string | null | no |  |

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | object | Successful Response |
| 422 | HTTPValidationError | Validation Error |

## POST `/api/v1/public/voice-demo/session/{session_id}/end`

**End Voice Demo Session**

Terminate an active public voice demo session.

*Tags:* public-home

### Parameters

| Name | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `session_id` | path | string | yes |  |
| `token` | query | string | null | no |  |
| `X-Demo-Session-Token` | header | string | null | no |  |
| `Authorization` | header | string | null | no |  |

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | object | Successful Response |
| 422 | HTTPValidationError | Validation Error |

## POST `/api/v1/public/voice-demo/session/{session_id}/event`

**Post Voice Demo Event**

Process a verified voice-demo lifecycle event (start/turn/interrupt/stop) against PostgreSQL/SQLite.

*Tags:* public-home

### Parameters

| Name | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `session_id` | path | string | yes |  |
| `token` | query | string | null | no |  |
| `X-Demo-Session-Token` | header | string | null | no |  |
| `Authorization` | header | string | null | no |  |

### Request body — `application/json`

Schema: `VoiceDemoEventIn`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `event` | string — one of `start`, `stop`, `end`, `interrupt`, `error`, `turn`, `text`, `utterance` | yes |  |
| `timestamp` | string \| null | no |  |
| `data` | object | no |  |
| `session_token` | string \| null | no |  |

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | object | Successful Response |
| 422 | HTTPValidationError | Validation Error |

## POST `/api/v1/public/voice-demo/session/{session_id}/turn`

**Post Voice Demo Turn**

Process one conversational demo turn against the persisted session.

*Tags:* public-home

### Parameters

| Name | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `session_id` | path | string | yes |  |
| `token` | query | string | null | no |  |
| `X-Demo-Session-Token` | header | string | null | no |  |
| `Authorization` | header | string | null | no |  |

### Request body — `application/json`

Schema: `VoiceDemoTurnIn`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `text` | string | yes |  |
| `session_token` | string \| null | no |  |

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | object | Successful Response |
| 422 | HTTPValidationError | Validation Error |

## GET `/api/v1/public/widget/chat.js`

**Get Widget Chat Js**

Serve the text chat widget (`widget/chat.js`).

Same origin, same headers as `embed.js`: a customer embedding the chat
widget must not have to whitelist a second host, and the file must not be
sniffed into another type.

*Tags:* public-widget

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | object | Successful Response |

## GET `/api/v1/public/widget/config`

**Get Widget Config**

Configuration for an embedded widget, resolved from the request Origin.

Returns only what a web page may know: product name, colours, position and the session endpoint. A key presented from an origin not on the key's allowlist is refused.

*Tags:* public-widget

### Parameters

| Name | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `public_key` | query | string | null | no |  |
| `origin` | query | string | null | no |  |
| `X-VoxDesk-Public-Key` | header | string | null | no |  |

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | PublicWidgetBootstrapConfig | Successful Response |
| 422 | HTTPValidationError | Validation Error |

## GET `/api/v1/public/widget/embed.js`

**Get Widget Embed Js**

Serve the CSP-safe embeddable widget loader (`widget/embed.js`).

*Tags:* public-widget

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | object | Successful Response |

## POST `/api/v1/public/widget/sessions`

**Start Widget Session**

Open a widget session.

The only unauthenticated route that can spend money, so it passes the abuse gates — captcha, budget, toll-fraud heuristics — before a session row exists. Every refusal names the gate that fired.

*Tags:* public-widget

### Parameters

| Name | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `X-VoxDesk-Public-Key` | header | string | null | no |  |

### Request body — `application/json`

Schema: `PublicWidgetSessionCreateRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `public_key` | string | yes |  |
| `mode` | PublicWidgetSessionMode | no |  |
| `visitor_id` | string \| null | no |  |
| `origin` | string \| null | no |  |
| `metadata` | object | no |  |

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 201 | PublicWidgetSessionRead | Successful Response |
| 422 | HTTPValidationError | Validation Error |

## GET `/api/v1/public/widget/sessions/{session_id}`

**Read Widget Session**

Read a widget session's state and turns.

The session token is the credential; there is no other lookup path.

*Tags:* public-widget

### Parameters

| Name | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `session_id` | path | string | yes |  |
| `X-VoxDesk-Widget-Session` | header | string | null | no |  |
| `authorization` | header | string | null | no |  |

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | PublicWidgetSessionRead | Successful Response |
| 422 | HTTPValidationError | Validation Error |

## POST `/api/v1/public/widget/sessions/{session_id}/end`

**Terminate Widget Session**

End a widget session. Idempotent; ending an ended session returns its final state.

*Tags:* public-widget

### Parameters

| Name | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `session_id` | path | string | yes |  |
| `X-VoxDesk-Widget-Session` | header | string | null | no |  |
| `authorization` | header | string | null | no |  |

### Request body — `application/json`

Schema: `PublicWidgetEndSessionRequest | null`

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | PublicWidgetSessionRead | Successful Response |
| 422 | HTTPValidationError | Validation Error |

## POST `/api/v1/public/widget/sessions/{session_id}/messages`

**Post Widget Session Message**

Send one visitor message and return the agent's reply.

Blocking on purpose: a widget that returns a message id and polls is a widget that feels broken.

*Tags:* public-widget

### Parameters

| Name | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `session_id` | path | string | yes |  |
| `X-VoxDesk-Widget-Session` | header | string | null | no |  |
| `authorization` | header | string | null | no |  |

### Request body — `application/json`

Schema: `PublicWidgetMessageSendRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `content` | string | yes |  |
| `dynamic_variables` | object | no |  |

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | PublicWidgetMessageSendResponse | Successful Response |
| 422 | HTTPValidationError | Validation Error |

## POST `/api/v1/public/widget/voice-bootstrap`

**Create Widget Voice Bootstrap**

Return the real web-call bootstrap (public key + origin check) when configured, or NOT_CONFIGURED otherwise.

*Tags:* public-widget

### Parameters

| Name | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `X-VoxDesk-Public-Key` | header | string | null | no |  |

### Request body — `application/json`

Schema: `PublicWidgetVoiceBootstrapRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `public_key` | string \| null | no |  |
| `origin` | string \| null | no |  |
| `dynamic_vars` | object | no |  |
| `metadata` | object | no |  |

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | object | Successful Response |
| 422 | HTTPValidationError | Validation Error |
