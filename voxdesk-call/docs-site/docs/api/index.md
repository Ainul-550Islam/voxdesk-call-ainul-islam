# API reference

This reference is generated from the application's OpenAPI schema: **93 published operations** across 5 documented surfaces. Run `python scripts/build_api_reference.py` to regenerate it. The build fails if a published operation lacks a summary or description.

## Published surfaces

| Surface | Operations |
| --- | ---: |
| [`/api/v1/public`](api-v1-public.md) | 34 |
| [`/api/v1/public-keys`](api-v1-public-keys.md) | 6 |
| [`/api/billing`](api-billing.md) | 19 |
| [`/api/whitelabel`](api-whitelabel.md) | 31 |
| [`/api/agent-templates`](api-agent-templates.md) | 3 |

## Internal API families (not published)

These operation counts are not a promise of compatibility. Internal routes are intentionally outside the buyer-facing reference.

| Prefix | Operations | Classification |
| --- | ---: | --- |
| `/api/v1/testing` | 28 | test harness, not shipped to buyers |
| `/api/v1/conductor` | 29 | internal reasoning surface, unstable by design |
| `/api/v1/evaluations` | 7 | internal evaluation runner |
| `/api/v1/integrations` | 35 | provider plumbing, documented per integration |
| `/api/v1/telephony` | 24 | superseded by /api/telephony; kept for compatibility |
| `/api/v1/agents` | 19 | superseded by /api/agents |
| `/api/v1/collab` | 11 | internal collaboration fan-out |
| `/api/v1/helpdesk` | 7 | pre-GA |
| `/api/v1/analytics` | 14 | internal dashboards API |
| *(other)* | 1361 | not part of the published contract |
