# Integrations v2 Runbook

## 1. Environment Variables & OAuth App Registration

Required encryption key ring: `CRM_ENCRYPTION_KEYS` (`k1:<urlsafe-b64-32-bytes>`).

| Provider | Env Vars | Redirect URI | Required Scopes |
|---|---|---|---|
| `zendesk` | `ZENDESK_CLIENT_ID`, `ZENDESK_CLIENT_SECRET`, `ZENDESK_REDIRECT_URI`, `ZENDESK_WEBHOOK_SECRET` | `https://<host>/api/v1/integrations/zendesk/callback` | `read write tickets:read tickets:write users:read users:write` |
| `servicenow` | `SERVICENOW_CLIENT_ID`, `SERVICENOW_CLIENT_SECRET`, `SERVICENOW_REDIRECT_URI` | `https://<host>/api/v1/integrations/servicenow/callback` | `useraccount` |
| `freshdesk` | `FRESHDESK_DOMAIN` (per-tenant API key via `/api/v1/integrations/freshdesk/connect`) | N/A (Basic API Key) | Agent Ticket + Contact read/write |
| `zoho_desk` | `ZOHO_CLIENT_ID`, `ZOHO_CLIENT_SECRET`, `ZOHO_REDIRECT_URI`, `ZOHO_REGION` (`us`/`eu`/`in`/`au`/`jp`/`ca`) | `https://<host>/api/v1/integrations/zoho_desk/callback` | `Desk.tickets.ALL,Desk.contacts.ALL,Desk.tasks.ALL,Desk.search.READ,Desk.settings.READ` |
| `zoho_crm` | `ZOHO_CLIENT_ID`, `ZOHO_CLIENT_SECRET`, `ZOHO_REDIRECT_URI`, `ZOHO_REGION` | `https://<host>/api/v1/integrations/zoho_crm/callback` | `ZohoCRM.modules.ALL,ZohoCRM.users.READ,ZohoCRM.settings.fields.READ,ZohoCRM.coql.READ` |
| `freshsales` | `FRESHSALES_CLIENT_ID`, `FRESHSALES_CLIENT_SECRET`, `FRESHSALES_REDIRECT_URI` | `https://<host>/api/v1/integrations/freshsales/callback` | `freshsales.contacts.view freshsales.contacts.edit freshsales.deals.view freshsales.deals.edit freshsales.notes.create` |
| `dynamics365` | `DYNAMICS_CLIENT_ID`, `DYNAMICS_CLIENT_SECRET`, `DYNAMICS_TENANT_ID`, `DYNAMICS_REDIRECT_URI` | `https://<host>/api/v1/integrations/dynamics365/callback` | `<org_url>/user_impersonation offline_access` |
| `pipedrive` | `PIPEDRIVE_CLIENT_ID`, `PIPEDRIVE_CLIENT_SECRET`, `PIPEDRIVE_REDIRECT_URI` | `https://<host>/api/v1/integrations/pipedrive/callback` | `contacts:full deals:full activities:full users:read` |
| `teams` | `TEAMS_CLIENT_ID`, `TEAMS_CLIENT_SECRET`, `TEAMS_TENANT_ID`, `TEAMS_REDIRECT_URI` | `https://<host>/api/v1/integrations/teams/callback` | `offline_access User.Read User.ReadBasic.All Team.ReadBasic.All Channel.ReadBasic.All ChannelMessage.Send OnlineMeetings.ReadWrite Calendars.ReadWrite Presence.Read.All` |
| `zoom` | `ZOOM_CLIENT_ID`, `ZOOM_CLIENT_SECRET`, `ZOOM_ACCOUNT_ID`, `ZOOM_REDIRECT_URI`, `ZOOM_WEBHOOK_SECRET_TOKEN` | `https://<host>/api/v1/integrations/zoom/callback` | `user:read:user meeting:write:meeting meeting:read:meeting phone:read:list_call_logs team_chat:write:user_message` |
| `slack` | `SLACK_CLIENT_ID`, `SLACK_CLIENT_SECRET`, `SLACK_SIGNING_SECRET`, `SLACK_REDIRECT_URI` | `https://<host>/api/v1/integrations/slack/callback` | `chat:write,chat:write.public,channels:read,groups:read,commands,users:read,users:read.email` |

Inbound webhook receiver: `POST /webhooks/int2/{provider}/{connection_id}` (`GET` supported for Graph `validationToken`).

## 2. Commands

```bash
# Apply overlay (requires alembic/versions/0066_*.py present)
bash apply_batch05.sh

# Offline protocol & contract suite
make int2-test

# Opt-in live sandbox suite (writes JUnit evidence to evidence/integrations_v2_live/junit_live.xml)
ZENDESK_LIVE_SUBDOMAIN=<subd> ZENDESK_LIVE_ACCESS_TOKEN=<tok> \
SERVICENOW_LIVE_INSTANCE_URL=https://<inst>.service-now.com SERVICENOW_LIVE_ACCESS_TOKEN=<tok> \
FRESHDESK_LIVE_DOMAIN=<dom> FRESHDESK_LIVE_API_KEY=<key> \
ZOHO_LIVE_ACCESS_TOKEN=<tok> ZOHO_LIVE_REGION=us ZOHO_DESK_LIVE_ORG_ID=<org> \
FRESHSALES_LIVE_DOMAIN=<dom> FRESHSALES_LIVE_API_KEY=<key> \
DYNAMICS_LIVE_INSTANCE_URL=https://<org>.crm.dynamics.com DYNAMICS_LIVE_ACCESS_TOKEN=<tok> \
TEAMS_LIVE_ACCESS_TOKEN=<tok> ZOOM_LIVE_ACCESS_TOKEN=<tok> SLACK_LIVE_BOT_TOKEN=xoxb-... \
make int2-live-test

# Print capability & verification matrix JSON
make int2-matrix

# UI typecheck, tests, and production build
make int2-ui-test
make int2-ui-build
```
