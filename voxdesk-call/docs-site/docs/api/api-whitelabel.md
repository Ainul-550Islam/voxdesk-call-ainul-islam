# `/api/whitelabel`

31 operations.

## GET `/api/whitelabel/assets`

**List Assets**

List uploaded brand assets (logos, favicons).

*Tags:* white-label

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | object | Successful Response |

*Auth:* HTTPBearer

## POST `/api/whitelabel/assets`

**Upload Asset**

Upload a logo or favicon. The bytes are validated and SVGs are sanitised.

*Tags:* white-label

### Request body — `multipart/form-data`

Schema: `Body_upload_asset_api_whitelabel_assets_post`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `file` | string | yes |  |
| `kind` | string | yes |  |

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 201 | object | Successful Response |
| 422 | HTTPValidationError | Validation Error |

*Auth:* HTTPBearer

## DELETE `/api/whitelabel/assets/{asset_id}`

**Delete Asset**

Delete a brand asset. A theme still referencing it falls back to no logo rather than a broken image.

*Tags:* white-label

### Parameters

| Name | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `asset_id` | path | string | yes |  |

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 204 | no body | Successful Response |
| 422 | HTTPValidationError | Validation Error |

*Auth:* HTTPBearer

## GET `/api/whitelabel/assets/{asset_id}`

**Read Asset**

Asset metadata by id.

*Tags:* white-label

### Parameters

| Name | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `asset_id` | path | string | yes |  |

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | object | Successful Response |
| 422 | HTTPValidationError | Validation Error |

*Auth:* HTTPBearer

## GET `/api/whitelabel/assets/{asset_id}/content`

**Read Asset Content**

Serve the stored bytes with the sniffed content type and a tight CSP.

*Tags:* white-label

### Parameters

| Name | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `asset_id` | path | string | yes |  |

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | object | Successful Response |
| 422 | HTTPValidationError | Validation Error |

*Auth:* HTTPBearer

## GET `/api/whitelabel/branding`

**Read Branding**

This tenant's branding, projected for one surface.

``GET /theme`` returns the raw payload an editor edits. This returns what a
*renderer* needs and nothing else: CSS custom properties, document metadata,
or the widget's config, plus a ``coverage`` block naming any field that still
carries the vendor's name. The distinction matters because a support engineer
answering "why does my dashboard still say VoxDesk" needs the projection and
its coverage report, not the theme JSON.

*Tags:* white-label

### Parameters

| Name | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `surface` | query | string | no |  |

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | object | Successful Response |
| 422 | HTTPValidationError | Validation Error |

*Auth:* HTTPBearer

## GET `/api/whitelabel/domains`

**List Domains**

List this workspace's claimed hostnames.

Includes their verification and certificate state, so an operator can see which are live.

*Tags:* white-label

### Parameters

| Name | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `limit` | query | integer | null | no |  |
| `offset` | query | integer | null | no |  |

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | object | Successful Response |
| 422 | HTTPValidationError | Validation Error |

*Auth:* HTTPBearer

## POST `/api/whitelabel/domains`

**Claim Domain**

Claim a hostname. The ownership token is returned **once**, in this response.

*Tags:* white-label

### Request body — `application/json`

Schema: `ClaimDomainIn`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `domain` | string | yes |  |
| `environment_id` | string \| null | no |  |

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 201 | object | Successful Response |
| 422 | HTTPValidationError | Validation Error |

*Auth:* HTTPBearer

## DELETE `/api/whitelabel/domains/{domain_id}`

**Delete Domain**

Release a hostname claim.

After this the hostname resolves to nothing and will receive no branded document.

*Tags:* white-label

### Parameters

| Name | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `domain_id` | path | string | yes |  |

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 204 | no body | Successful Response |
| 422 | HTTPValidationError | Validation Error |

*Auth:* HTTPBearer

## GET `/api/whitelabel/domains/{domain_id}`

**Read Domain**

Read one hostname's claim.

*Tags:* white-label

### Parameters

| Name | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `domain_id` | path | string | yes |  |

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | object | Successful Response |
| 422 | HTTPValidationError | Validation Error |

*Auth:* HTTPBearer

## GET `/api/whitelabel/domains/{domain_id}/certificate`

**Read Certificate**

The certificate state for one hostname.

A certificate is only issued after the domain is ACTIVE; before that this reports the reason rather than a placeholder.

*Tags:* white-label

### Parameters

| Name | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `domain_id` | path | string | yes |  |

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | object | Successful Response |
| 422 | HTTPValidationError | Validation Error |

*Auth:* HTTPBearer

## GET `/api/whitelabel/domains/{domain_id}/checks`

**Read Domain Checks**

The DNS checks for one hostname, with the resolver answers that produced them.

*Tags:* white-label

### Parameters

| Name | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `domain_id` | path | string | yes |  |
| `limit` | query | integer | null | no |  |

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | object | Successful Response |
| 422 | HTTPValidationError | Validation Error |

*Auth:* HTTPBearer

## POST `/api/whitelabel/domains/{domain_id}/disable`

**Disable Domain**

Stop serving one hostname immediately.

The reason is recorded in the audit trail; the claim itself is kept so re-enabling does not require DNS proof again.

*Tags:* white-label

### Parameters

| Name | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `domain_id` | path | string | yes |  |

### Request body — `application/json`

Schema: `DisableDomainIn | null`

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | object | Successful Response |
| 422 | HTTPValidationError | Validation Error |

*Auth:* HTTPBearer

## POST `/api/whitelabel/domains/{domain_id}/enable`

**Enable Domain**

Resume serving a disabled hostname.

*Tags:* white-label

### Parameters

| Name | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `domain_id` | path | string | yes |  |

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | object | Successful Response |
| 422 | HTTPValidationError | Validation Error |

*Auth:* HTTPBearer

## POST `/api/whitelabel/domains/{domain_id}/refresh-certificate`

**Refresh Certificate**

Request a renewed certificate for one hostname.

*Tags:* white-label

### Parameters

| Name | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `domain_id` | path | string | yes |  |

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | object | Successful Response |
| 422 | HTTPValidationError | Validation Error |

*Auth:* HTTPBearer

## POST `/api/whitelabel/domains/{domain_id}/verify`

**Verify Domain**

Re-run the DNS checks for one hostname.

Idempotent; a claim that already passes returns ACTIVE without changing anything.

*Tags:* white-label

### Parameters

| Name | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `domain_id` | path | string | yes |  |

### Request body — `application/json`

Schema: `VerifyDomainIn | null`

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | object | Successful Response |
| 422 | HTTPValidationError | Validation Error |

*Auth:* HTTPBearer

## GET `/api/whitelabel/email-domains`

**List Sender Domains**

List verified sending domains for outgoing email.

*Tags:* white-label

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | object | Successful Response |

*Auth:* HTTPBearer

## POST `/api/whitelabel/email-domains`

**Create Sender Domain**

Claim a sending domain.

Sending stays off until SPF, DKIM and DMARC all pass; this endpoint returns the records to publish.

*Tags:* white-label

### Request body — `application/json`

Schema: `SenderDomainIn`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `domain` | string | yes |  |
| `from_name` | string | no |  |
| `dkim_selector` | string | no |  |

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 201 | object | Successful Response |
| 422 | HTTPValidationError | Validation Error |

*Auth:* HTTPBearer

## DELETE `/api/whitelabel/email-domains/{sender_id}`

**Delete Sender Domain**

Remove a sending domain.

*Tags:* white-label

### Parameters

| Name | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sender_id` | path | string | yes |  |

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 204 | no body | Successful Response |
| 422 | HTTPValidationError | Validation Error |

*Auth:* HTTPBearer

## GET `/api/whitelabel/email-domains/{sender_id}/records`

**Read Sender Records**

The DNS records a sending domain must publish.

*Tags:* white-label

### Parameters

| Name | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sender_id` | path | string | yes |  |

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | object | Successful Response |
| 422 | HTTPValidationError | Validation Error |

*Auth:* HTTPBearer

## POST `/api/whitelabel/email-domains/{sender_id}/verify`

**Verify Sender Domain**

Re-check a sending domain's DNS records.

*Tags:* white-label

### Parameters

| Name | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `sender_id` | path | string | yes |  |

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | object | Successful Response |
| 422 | HTTPValidationError | Validation Error |

*Auth:* HTTPBearer

## GET `/api/whitelabel/public/widget/{public_key}/config`

**Public Widget Config**

Widget configuration for an authorised embedding origin.

*Tags:* white-label-widget

### Parameters

| Name | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `public_key` | path | string | yes |  |
| `Origin` | header | string | null | no |  |

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | object | Successful Response |
| 422 | HTTPValidationError | Validation Error |

## GET `/api/whitelabel/public/widget/{public_key}/loader.js`

**Public Widget Loader**

The script a tenant's site loads.

Resolved from ``Host`` **and** the public key: the host decides the tenant, the
key is checked against that tenant so a key copied from one tenant's page
cannot be used to pull another tenant's theme onto a domain that is not theirs.
A host we do not serve is a 404 — never the default theme.

*Tags:* white-label-widget

### Parameters

| Name | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `public_key` | path | string | yes |  |

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | object | Successful Response |
| 422 | HTTPValidationError | Validation Error |

## GET `/api/whitelabel/templates`

**List Templates**

The branded email templates this deployment can render.

*Tags:* white-label

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | object | Successful Response |

*Auth:* HTTPBearer

## POST `/api/whitelabel/templates/{name}/preview`

**Preview Template**

Render a branded email with the active theme, for the console's preview pane.

*Tags:* white-label

### Parameters

| Name | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `name` | path | string | yes |  |

### Request body — `application/json`

Schema: `TemplatePreviewIn`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `variables` | object | no |  |

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | object | Successful Response |
| 422 | HTTPValidationError | Validation Error |

*Auth:* HTTPBearer

## GET `/api/whitelabel/theme`

**Read Theme**

The theme payload an editor edits, plus its published version and the allowed fonts.

*Tags:* white-label

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | object | Successful Response |

*Auth:* HTTPBearer

## POST `/api/whitelabel/theme`

**Publish Theme**

Publish a new immutable theme version, optionally activating it.

Contrast is validated first; a theme that fails is refused with the failing pairs listed.

*Tags:* white-label

### Request body — `application/json`

Schema: `ThemeIn`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `payload` | object | yes |  |
| `activate` | boolean | no |  |
| `name` | string | no |  |

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | object | Successful Response |
| 422 | HTTPValidationError | Validation Error |

*Auth:* HTTPBearer

## POST `/api/whitelabel/theme/validate`

**Validate Theme Endpoint**

Dry-run validation, so the editor can show contrast failures before saving.

This endpoint *reports*; it never raises. A theme editor that has to parse a 422
to colour a field red is one that will show the failure in the wrong place, so
the errors come back in the body keyed by the field they belong to.

*Tags:* white-label

### Request body — `application/json`

Schema: `ThemeValidateIn`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `payload` | object | yes |  |

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | object | Successful Response |
| 422 | HTTPValidationError | Validation Error |

*Auth:* HTTPBearer

## GET `/api/whitelabel/theme/versions`

**List Theme Versions**

Theme version history, newest first.

*Tags:* white-label

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | object | Successful Response |

*Auth:* HTTPBearer

## POST `/api/whitelabel/theme/versions/{version_id}/activate`

**Activate Theme Version**

Repoint the active theme at an earlier version.

A rollback is a pointer move: no row is rewritten, so the history stays intact.

*Tags:* white-label

### Parameters

| Name | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `version_id` | path | string | yes |  |

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | object | Successful Response |
| 422 | HTTPValidationError | Validation Error |

*Auth:* HTTPBearer

## GET `/api/whitelabel/widget`

**Read Widget Admin**

The widget configuration as this workspace configured it.

Distinct from the public endpoint, which resolves the same theme by request Host.

*Tags:* white-label

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | object | Successful Response |

*Auth:* HTTPBearer
