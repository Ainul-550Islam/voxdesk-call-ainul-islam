# `/api/billing`

19 operations.

## GET `/api/billing`

**Get Billing**

Read this workspace's billing status.

Returns the plan, subscription state, current period and the estimated overage for the period so far. Every field is an allowlist: no provider identifiers, no API keys, no payment instrument.

*Tags:* billing

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | BillingStatusOut | Successful Response |

*Auth:* HTTPBearer

## GET `/api/billing/`

**Get Billing**

Read this workspace's billing status.

Returns the plan, subscription state, current period and the estimated overage for the period so far. Every field is an allowlist: no provider identifiers, no API keys, no payment instrument.

*Tags:* billing

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | BillingStatusOut | Successful Response |

*Auth:* HTTPBearer

## POST `/api/billing/cancel`

**Cancel Subscription**

Cancel at the end of the current period.

Immediate cancellation is deliberately not offered: it would forfeit usage the customer has already been billed for.

*Tags:* billing

### Request body — `application/json`

Schema: `app__api__billing_routes__CancelIn | null`

Example:

```json
{
  "reason": "moving to a self-hosted deployment"
}
```

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | BillingStatusOut | Successful Response |
| 422 | HTTPValidationError | Validation Error |

*Auth:* HTTPBearer

## POST `/api/billing/change-plan`

**Change Plan**

Upgrade immediately, or schedule a downgrade for the period end.

The policy is implemented once, in `service.change_plan`, and documented
there and in `docs/BILLING.md`.

*Tags:* billing

### Request body — `application/json`

Schema: `ChangePlanIn`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `plan_code` | string | yes |  |
| `interval` | BillingInterval \| null | no |  |

Example:

```json
{
  "interval": "month",
  "plan_code": "growth"
}
```

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | BillingStatusOut | Successful Response |
| 422 | HTTPValidationError | Validation Error |

*Auth:* HTTPBearer

## POST `/api/billing/checkout`

**Start Checkout**

Begin a purchase.

Returns a URL and nothing else. **No subscription state is established
here** — a browser reaching the success URL has proved only that it
reached a URL. Only a verified webhook makes a subscription active.

*Tags:* billing

### Request body — `application/json`

Schema: `CheckoutIn`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `plan_code` | string | yes |  |
| `interval` | BillingInterval | no |  |

Example:

```json
{
  "interval": "month",
  "plan_code": "growth"
}
```

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | app__api__billing_routes__SessionOut | Successful Response |
| 422 | HTTPValidationError | Validation Error |

*Auth:* HTTPBearer

## GET `/api/billing/invoice/lines`

**Itemised Invoice Lines**

This period's usage, priced per component against the current book.

*Tags:* billing

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | object | Successful Response |

*Auth:* HTTPBearer

## GET `/api/billing/invoices`

**List Invoices**

Invoice metadata.

Served from the local mirror rather than by calling Stripe on every page
load: it is faster, it works when Stripe is down, and it keeps the API
key out of a read path that a dashboard polls.

*Tags:* billing

### Parameters

| Name | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `limit` | query | integer | no |  |

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | array of InvoiceOut | Successful Response |
| 422 | HTTPValidationError | Validation Error |

*Auth:* HTTPBearer

## POST `/api/billing/license/issue`

**Issue**

Mint a signed licence for a self-hosted installation.

Owner-only. The signature is HMAC over the licence claims, so the installation can verify itself offline.

*Tags:* billing-license

### Request body — `application/json`

Schema: `IssueLicenseIn`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `plan` | string | yes |  |
| `seats` | integer | yes |  |
| `expires_in_days` | integer | yes |  |

Example:

```json
{
  "expires_in_days": 365,
  "plan": "self_hosted_enterprise",
  "seats": 25
}
```

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | LicenseOut | Successful Response |
| 422 | HTTPValidationError | Validation Error |

*Auth:* HTTPBearer

## POST `/api/billing/license/verify`

**Verify**

Verify a licence signature and its claims.

Unauthenticated because a self-hosted installation has no account with us; the signature is the credential.

*Tags:* billing-license

### Request body — `application/json`

Schema: `VerifyLicenseIn`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `license` | string | yes |  |

Example:

```json
{
  "license": "eyJhbGciOiJIUzI1NiJ9..."
}
```

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | object | Successful Response |
| 422 | HTTPValidationError | Validation Error |

## GET `/api/billing/margin`

**Margin Report**

Cost against charge for this period — the reseller's view.

A tenant administrator sees their own margin (they are the reseller to
*their* customers). Nobody sees another tenant's: the query is scoped to
``ctx.tenant_id`` exactly like every other read here.

*Tags:* billing

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | object | Successful Response |

*Auth:* HTTPBearer

## GET `/api/billing/plans`

**List Plans**

List the plans a workspace can buy.

Server-owned catalogue. A browser can name a plan *code* but never a price; the code is resolved to a provider price id server-side.

*Tags:* billing

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | array of PlanOut | Successful Response |

*Auth:* HTTPBearer

## POST `/api/billing/portal`

**Open Portal**

Open the customer portal for **the authenticated tenant's** customer.

There is no request body. The external customer id is read from the
tenant's own row, so there is no parameter through which another tenant's
id could be supplied (requirement 9).

*Tags:* billing

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | app__api__billing_routes__SessionOut | Successful Response |

*Auth:* HTTPBearer

## GET `/api/billing/price-book`

**Get Price Book**

The price book in force now, or a specific published version.

*Tags:* billing

### Parameters

| Name | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `version` | query | string | null | no |  |

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | PriceBookOut | Successful Response |
| 422 | HTTPValidationError | Validation Error |

*Auth:* HTTPBearer

## POST `/api/billing/price-book`

**Publish Price Book**

Publish a new immutable version. Existing versions are never edited.

There is no PATCH and no DELETE on purpose: an invoice that cites version
``2026-01`` must still mean the same numbers a year later.

Guarded by ``get_platform_admin``, which currently denies every principal:
prices are deploy-scoped, so a tenant administrator must not move them for
everyone. Until a platform-operator principal exists, a price change is a
release — see ``docs/PRICE_BOOK.md``. Denying is the correct interim
behaviour; inventing a superuser flag to make the endpoint "work" would be a
privilege-escalation hole hidden behind a convenience.

*Tags:* billing

### Request body — `application/json`

Schema: `PriceBookChangeIn`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `version` | string | yes |  |
| `effective_from` | string | yes |  |
| `notes` | string | no |  |
| `changes` | object | no | component → {millicents, list_millicents}, e.g. {'telephony:US': {'millicents': 1400}} |

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 201 | PriceBookOut | Successful Response |
| 422 | HTTPValidationError | Validation Error |

*Auth:* HTTPBearer

## GET `/api/billing/price-book/versions`

**List Price Book Versions**

Every published price-book version, oldest first.

Versions are immutable. An invoice cites the version it was priced with, so re-deriving an old invoice reproduces the old numbers exactly.

*Tags:* billing

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | array of object | Successful Response |

*Auth:* HTTPBearer

## POST `/api/billing/reconcile`

**Reconcile**

Re-read the provider and rebuild the usage cache.

The "something looks wrong" button. Safe by construction: the provider is
always authoritative on subscription state, and rebuilding a summary only
touches a cache.

*Tags:* billing

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | BillingStatusOut | Successful Response |

*Auth:* HTTPBearer

## GET `/api/billing/usage`

**Get Usage**

Metered usage for the current billing period.

One entry per metric with included, used, remaining and overage. The counters come from append-only usage events, so the figure is reproducible after any dispute.

*Tags:* billing

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | UsageOut | Successful Response |

*Auth:* HTTPBearer

## GET `/api/billing/usage/by-provider`

**Usage By Provider**

Usage split by provider and by day, from the same rows.

One query, one pivot: a "by day" chart computed separately from the "by
provider" table is how the two disagree by a few minutes and the customer
notices before we do.

*Tags:* billing

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | object | Successful Response |

*Auth:* HTTPBearer

## POST `/api/billing/webhook/{provider}`

**Receive Webhook**

Provider events.

Unauthenticated by necessity — Stripe has no VoxDesk credential — and
therefore **signature-verified before anything else**.

`await request.body()` gives the exact bytes as received. Nothing parses
the JSON before verification: requirement 21 is explicit that a
re-serialized body invalidates the signature, and reading `request.json()`
here would silently make every signature check meaningless.

Always returns 2xx once the signature passes. A non-2xx makes Stripe
retry, and retrying an event we have already recorded, or one nobody will
ever handle, is pure noise. Genuine handler failures are the exception:
those return 500 so Stripe *does* retry.

*Tags:* billing

### Parameters

| Name | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `provider` | path | string | yes |  |

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | object | Successful Response |
| 422 | HTTPValidationError | Validation Error |
