# Getting started

## Authentication

Use a tenant-scoped bearer token for authenticated API operations. Keep the
credential on a server; do not embed it in browser JavaScript. The API derives
the tenant from the authenticated principal rather than accepting a tenant id
from the request.

```http
Authorization: Bearer <access-token>
```

## Public widget

1. Create a widget public key in the workspace settings.
2. Add the exact HTTPS origins that are allowed to use it.
3. Store the plaintext key securely; it is returned only when created or
   rotated.
4. Embed the widget on an allowlisted origin and use the published widget
   endpoints for configuration and sessions.

[Download the generated Postman collection](postman/voxdesk-published-api.postman_collection.json)
with all 93 published operations.

A public key is not a user credential. It is origin-scoped and must not be used
to call tenant administration endpoints. See [Widget public keys](api/api-v1-public-keys.md)
and the [public API reference](api/api-v1-public.md).

## Billing

Checkout accepts a plan code and billing interval. Prices and provider price
identifiers are resolved server-side; callers cannot submit an amount. See the
[Billing API](api/api-billing.md).

## Contract and versioning

The generated reference is built from the running application's OpenAPI
schema. A CI guard requires every published operation to have a summary and a
description. Internal route families are not a promise of compatibility.
