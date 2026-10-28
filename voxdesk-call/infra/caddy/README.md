# Edge configuration for tenant domains

Two files, because Caddy imposes one structure on us: `on_demand_tls` is a
**global** directive, and a file that contains a global block cannot be imported
*into* another global block.

| File | Imported from | Contains |
| --- | --- | --- |
| `edge-global.caddy` | inside the global `{ }` block | the `ask` hook, trusted proxies, certificate storage |
| `tenant-domains.caddy` | top level | the tenant site block (`tls { on_demand }`, widget caching, ask-path denial) |

The real edge Caddyfile:

```caddyfile
{
	admin off
	import /etc/caddy/edge-global.caddy
}

import /etc/caddy/tenant-domains.caddy

{$DOMAIN:http://localhost} {
	encode zstd gzip
	handle {
		reverse_proxy api:8000
	}
}
```

`make batch07-infra` validates a temporary complete Caddyfile that imports both
fragments in their intended contexts (the global file inside `{}` and the tenant
site file at top level). Validating either fragment alone is incorrect: Caddy's
`on_demand_tls` options are global, while `tenant-domains.caddy` contains a site
block. The same target records the real Caddy output in Batch 07 evidence. The
repository's root `Caddyfile` is the single-tenant compose stack and is deliberately
**not** changed by Batch 07: enabling on-demand TLS in a stack that does not run the
domain-verification service would let anyone who points DNS at it request
certificates.

## How a hostname gets a certificate

1. A tenant claims `help.acme.example.com` via `POST /api/whitelabel/domains`.
   The row is `pending_dns` and the ownership token is stored **hashed**.
2. The tenant adds `CNAME help.acme.example.com -> $CUSTOM_DOMAIN_CNAME_TARGET`
   (or the TXT ownership record). `POST /api/whitelabel/domains/{id}/verify`
   resolves both, records the evidence in `wl_domain_checks`, and moves the row
   to `active` (or `failed`).
3. The first HTTPS connection to the host reaches the edge, which calls
   `GET /api/platform/tls/ask?domain=help.acme.example.com`.
4. The API answers `200 {"allow": true}` **only** for an `active` row. `ask`
   never approves `pending_dns`, `verifying`, `issuing_cert`, `failed`,
   `disabled`, or an unknown host. A refusal is a 403, so Caddy never requests a
   certificate.
5. `GET /api/platform/tls/status?domain=…` lets the tenant UI show "certificate
   issued" without scraping the edge's logs.

## Why the ask hook is also token-gated

Without `X-Voxdesk-Ask-Token` the API answers 403 **before** it looks at the
domain, so the endpoint cannot be used to enumerate which hostnames exist. The
token is `TLS_ASK_TOKEN`; the edge sends it via
`header_up X-Voxdesk-Ask-Token {$TLS_ASK_TOKEN}` in the deployment config.

## Tests

`tests/platform_v2/test_caddy_tenant_domains.py` parses both files and asserts:

* `on_demand_tls` exists and its `ask` URL is the API's `/api/platform/tls/ask`;
* the ask path is **blocked** from tenant hosts (`respond @tls_ask … 404`);
* TLS verification is never disabled (no `insecure_skip_verify`, `skip_verify`,
  `tls_insecure_skip_verify`);
* there is no site block that serves an unknown Host from a default tenant;
* the widget cache rule exists and is short (60s), so a rebrand is visible fast.

It does not run Caddy; the behaviour it describes is tested against the API in
`test_live_custom_domain_tls.py` (marked `live`, skipped by default).
