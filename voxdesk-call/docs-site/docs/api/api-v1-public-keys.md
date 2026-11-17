# `/api/v1/public-keys`

6 operations.

## GET `/api/v1/public-keys`

**List Widget Public Keys**

List this workspace's widget public keys.

Never returns the key itself — only its prefix, label, allowed origins and last-used timestamp.

*Tags:* public-widget-keys

### Parameters

| Name | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `agent_id` | query | string | null | no |  |

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | array of PublicWidgetKeyRead | Successful Response |
| 422 | HTTPValidationError | Validation Error |

*Auth:* HTTPBearer

## POST `/api/v1/public-keys`

**Create Widget Public Key**

Create a public key for the embedded widget.

The plaintext key is returned **once**. Only its hash is stored, so a database dump cannot be used to impersonate a widget.

*Tags:* public-widget-keys

### Request body — `application/json`

Schema: `PublicWidgetKeyCreate`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `agent_id` | string | yes |  |
| `agent_kind` | string | no |  |
| `name` | string | yes |  |
| `environment_name` | string | no |  |
| `allowed_origins` | array of string | no |  |
| `allowed_capabilities` | array of string | no |  |
| `rate_limit_per_minute` | integer | no |  |
| `session_ttl_seconds` | integer | no |  |
| `require_published_agent` | boolean | no |  |
| `expires_in_days` | integer \| null | no |  |
| `widget_config` | PublicWidgetAppearanceConfig | no |  |

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 201 | PublicWidgetKeyCreatedResponse | Successful Response |
| 422 | HTTPValidationError | Validation Error |

*Auth:* HTTPBearer

## GET `/api/v1/public-keys/{key_id}`

**Get Widget Public Key**

Read one widget public key by id.

Scoped to the caller's workspace; an id belonging to another workspace is a 404, not a 403, so the endpoint cannot be used to enumerate ids.

*Tags:* public-widget-keys

### Parameters

| Name | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `key_id` | path | string | yes |  |

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | PublicWidgetKeyRead | Successful Response |
| 422 | HTTPValidationError | Validation Error |

*Auth:* HTTPBearer

## PATCH `/api/v1/public-keys/{key_id}`

**Update Widget Public Key**

Update a key's label, allowed origins or agent binding.

*Tags:* public-widget-keys

### Parameters

| Name | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `key_id` | path | string | yes |  |

### Request body — `application/json`

Schema: `PublicWidgetKeyUpdate`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | string \| null | no |  |
| `allowed_origins` | array of string \| null | no |  |
| `allowed_capabilities` | array of string \| null | no |  |
| `rate_limit_per_minute` | integer \| null | no |  |
| `session_ttl_seconds` | integer \| null | no |  |
| `require_published_agent` | boolean \| null | no |  |
| `widget_config` | PublicWidgetAppearanceConfig \| null | no |  |

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | PublicWidgetKeyRead | Successful Response |
| 422 | HTTPValidationError | Validation Error |

*Auth:* HTTPBearer

## POST `/api/v1/public-keys/{key_id}/revoke`

**Revoke Widget Public Key**

Revoke a public key immediately.

Idempotent: revoking an already-revoked key succeeds and changes nothing.

*Tags:* public-widget-keys

### Parameters

| Name | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `key_id` | path | string | yes |  |

### Request body — `application/json`

Schema: `PublicWidgetKeyRevokeRequest | null`

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | PublicWidgetKeyRead | Successful Response |
| 422 | HTTPValidationError | Validation Error |

*Auth:* HTTPBearer

## POST `/api/v1/public-keys/{key_id}/rotate`

**Rotate Widget Public Key**

Rotate a public key, returning the new plaintext once.

The previous key keeps working for a grace window so a tenant's live site does not break mid-deploy; revoke it explicitly to end that window early.

*Tags:* public-widget-keys

### Parameters

| Name | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `key_id` | path | string | yes |  |

### Request body — `application/json`

Schema: `PublicWidgetKeyRotateRequest | null`

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 201 | PublicWidgetKeyCreatedResponse | Successful Response |
| 422 | HTTPValidationError | Validation Error |

*Auth:* HTTPBearer
