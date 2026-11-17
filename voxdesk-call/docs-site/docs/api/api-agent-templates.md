# `/api/agent-templates`

3 operations.

## GET `/api/agent-templates`

**List Templates**

The gallery. Flows are omitted: they are large and the list is a menu.

*Tags:* agent-templates

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | object | Successful Response |

*Auth:* HTTPBearer

## GET `/api/agent-templates/{template_id}`

**Read Template**

One starter template in full: flow, tools, variables, analysis schema, simulation cases, compliance notes.

*Tags:* agent-templates

### Parameters

| Name | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `template_id` | path | string | yes |  |

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 200 | object | Successful Response |
| 422 | HTTPValidationError | Validation Error |

*Auth:* HTTPBearer

## POST `/api/agent-templates/{template_id}/instantiate`

**Instantiate Template**

Create a draft agent from a template.

The result is always a draft: a template that went straight to live would be a production agent nobody reviewed. 409 if the derived external key is already taken in this workspace.

*Tags:* agent-templates

### Parameters

| Name | In | Type | Required | Description |
| --- | --- | --- | --- | --- |
| `template_id` | path | string | yes |  |

### Request body — `application/json`

Schema: `InstantiateRequest`

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `name` | string \| null | no |  |
| `config_overrides` | object | no |  |

Example:

```json
{
  "config_overrides": {
    "voice": "vd-voice-sadia"
  },
  "name": "Front desk — Dhaka"
}
```

### Responses

| Status | Schema | Description |
| --- | --- | --- |
| 201 | InstantiatedAgent | Successful Response |
| 422 | HTTPValidationError | Validation Error |

*Auth:* HTTPBearer
