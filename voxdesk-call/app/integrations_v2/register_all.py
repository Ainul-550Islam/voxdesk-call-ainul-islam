"""Registers all 11 Integrations v2 providers into the existing connector capability registry."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.integrations_v2_models import IntegrationConnection
from app.integrations_v2.collab.slack import SlackProvider
from app.integrations_v2.collab.teams import TeamsProvider
from app.integrations_v2.collab.zoom import ZoomProvider
from app.integrations_v2.core.connection_store import load_runtime_context
from app.integrations_v2.core.errors import IntegrationV2Error
from app.integrations_v2.core.provider_base import ConnectionRuntimeContext, ProviderV2
from app.integrations_v2.crm.dynamics365 import Dynamics365Provider
from app.integrations_v2.crm.freshsales import FreshsalesProvider
from app.integrations_v2.crm.pipedrive_v2 import PipedriveV2Provider
from app.integrations_v2.crm.zoho_crm import ZohoCrmProvider
from app.integrations_v2.helpdesk.freshdesk import FreshdeskProvider
from app.integrations_v2.helpdesk.servicenow import ServiceNowProvider
from app.integrations_v2.helpdesk.zendesk import ZendeskProvider
from app.integrations_v2.helpdesk.zoho_desk import ZohoDeskProvider
from app.integrations_v2.matrix import build_capability_matrix
from app.integrations_v2.settings import get_int2_settings

PROVIDERS_V2: dict[str, type[ProviderV2]] = {
    "zendesk": ZendeskProvider,
    "servicenow": ServiceNowProvider,
    "freshdesk": FreshdeskProvider,
    "zoho_desk": ZohoDeskProvider,
    "zoho_crm": ZohoCrmProvider,
    "freshsales": FreshsalesProvider,
    "dynamics365": Dynamics365Provider,
    "pipedrive": PipedriveV2Provider,
    "teams": TeamsProvider,
    "zoom": ZoomProvider,
    "slack": SlackProvider,
}


def get_provider_class_v2(slug: str) -> type[ProviderV2]:
    cls = PROVIDERS_V2.get((slug or "").strip().lower())
    if cls is None:
        raise IntegrationV2Error(
            f"Unsupported Integrations v2 provider {slug!r}. Expected one of: {', '.join(sorted(PROVIDERS_V2))}"
        )
    return cls


def is_provider_env_configured(slug: str) -> bool:
    cfg = get_int2_settings()
    if slug == "freshdesk":
        return bool(cfg.freshdesk_default_domain.strip())
    return cfg.is_oauth_app_configured(slug)


def instantiate_provider_v2(
    row: IntegrationConnection, *, http_client: Any = None
) -> ProviderV2:
    cls = get_provider_class_v2(row.provider)
    ctx = load_runtime_context(row)
    return cls(ctx, http_client=http_client)


async def build_honest_verification_matrix(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID | None = None,
    evidence_dir: Path | None = None,
) -> list[dict[str, Any]]:
    return await build_capability_matrix(
        session, tenant_id=tenant_id, evidence_dir=evidence_dir
    )


class _ConnectorV2BridgeAdapter:
    """Bridges ``app.integrations.connector.ConnectorAdapter`` to ``ProviderV2``."""

    def __init__(self, slug: str, context: Any) -> None:
        cls = get_provider_class_v2(slug)
        self.provider = slug
        self.capabilities = frozenset(
            {"health_check"} | {cap.value for cap in cls.capabilities}
        )
        self._cls = cls
        self._context = context

    def _has_creds(self) -> bool:
        creds = dict(self._context.credentials or {})
        return bool(creds.get("access_token") or creds.get("api_key") or creds.get("account_id"))

    def _build_v2(self) -> ProviderV2:
        cfg = dict(self._context.config or {})
        rt = ConnectionRuntimeContext(
            tenant_id=str(self._context.tenant_id),
            connection_id=str(self._context.connector_id),
            provider=self.provider,
            instance_url=str(cfg.get("instance_url") or cfg.get("resource_url") or ""),
            region=str(cfg.get("region") or "us"),
            credentials=dict(self._context.credentials or {}),
            config=cfg,
        )
        return self._cls(rt)

    async def validate_credentials(self, context: Any) -> None:
        if not self._has_creds():
            raise RuntimeError(f"NOT_CONFIGURED: {self.provider} credentials absent")
        res = await self._build_v2().health()
        if not res.ok:
            raise RuntimeError(f"{self.provider} health probe failed")

    async def health(self, context: Any) -> dict[str, Any]:
        if not self._has_creds():
            return {
                "connected": False,
                "provider": self.provider,
                "latency_ms": 0.0,
                "status": "NOT_CONFIGURED",
                "message": f"NOT_CONFIGURED: {self.provider} credentials are not configured",
            }
        res = await self._build_v2().health()
        return {
            "connected": res.ok,
            "provider": self.provider,
            "latency_ms": res.latency_ms,
            "status": "CONFIGURED" if res.ok else "ERROR",
            "message": res.detail,
        }

    async def dispatch(
        self, context: Any, operation: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        if operation == "health_check":
            return await self.health(context)
        if not self._has_creds():
            raise RuntimeError(f"NOT_CONFIGURED: {self.provider} credentials are not configured")
        raise RuntimeError(
            f"Operation {operation!r} must be invoked via typed integrations_v2 services"
        )


def register_v2_into_connector_registry() -> None:
    """Idempotently register/upgrade all 11 v2 providers in ``app.integrations.connector.registry``."""
    from app.integrations.connector import ConnectorRegistration, registry

    for slug, cls in PROVIDERS_V2.items():
        caps = frozenset({"health_check"} | {c.value for c in cls.capabilities})
        reg = ConnectorRegistration(
            provider=slug,
            kind=cls.category,
            capabilities=caps,
            factory=lambda ctx, s=slug: _ConnectorV2BridgeAdapter(s, ctx),
            required_credentials=frozenset(),
        )
        # Upgrade unconfigured placeholder if present, or register fresh
        registry._items[slug] = reg


register_v2_into_connector_registry()
