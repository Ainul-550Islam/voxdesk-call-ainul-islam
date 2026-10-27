"""One aggregator for the whole Batch 05 integrations-v2 HTTP surface.

``app/main.py`` mounts this single router. Two very different kinds of endpoint
live behind it, and they are composed in one place so the public/private split is
visible in a single file:

**Authenticated product APIs** (per-tenant, permissioned per handler):

* ``/api/v1/integrations``   — provider catalogue, connections, OAuth, field
  mapping, incremental sync runs, health/refresh, plus the ``/v2/...`` aliases
  the dashboard calls.
* ``/api/v1/helpdesk``       — requester lookup, ticket search/create/update,
  comments, voice-call logging.
* ``/api/v1/collab``         — notification rules, meetings and Slack/Teams/Zoom
  notify + test.
* ``/api/v1/agent-tools`` and ``/api/v1/integrations/v2/agent-tools`` — MCP tool
  enablements, dry-run and execute.

**Public inbound endpoints** (no session; the provider proves itself):

* ``/webhooks/int2/{provider}/{connection_id}`` — HMAC/Ed25519-verified vendor
  webhooks and the GET challenge handshake (Zoom ``url_validation``, Microsoft
  Graph ``validationToken``, Zendesk/Slack signatures). Verification runs inside
  :func:`app.integrations_v2.core.webhook_inbound.receive_inbound_webhook`
  *before* the body is trusted.
* ``/webhooks/int2/slack/{connection_id}/slash`` and ``/interactivity``
  (:mod:`app.api.collab_routes`) — Slack request-signature verification.
* ``/webhooks/int2/slack/{connection_id}/commands`` and ``/interactions``
  (:mod:`app.api.integrations_v2_webhooks`) — the same verification through the
  slash-command helpers.

The last three families are on the reviewed public allowlist in
``security_harness/authz_matrix.PUBLIC_ROUTES``; nothing else in this aggregator
is reachable without a tenant context.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api import (
    agent_tools_v2_routes,
    collab_routes,
    helpdesk_routes,
    integrations_v2_routes,
    integrations_v2_webhooks,
)

router = APIRouter()

# BATCH06:int2_surface_composition
router.include_router(integrations_v2_routes.router)
router.include_router(helpdesk_routes.router)
router.include_router(collab_routes.router)
router.include_router(agent_tools_v2_routes.router)
router.include_router(integrations_v2_webhooks.router)

__all__ = ["router"]
