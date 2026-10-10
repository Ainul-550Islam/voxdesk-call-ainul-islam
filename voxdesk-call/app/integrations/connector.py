"""Canonical tenant-aware connector registry and lifecycle boundary (Part 1F / Gate G1).

Dispatches CRM providers (gohighlevel, hubspot, jobber, salesforce, webhook) to
``app.integrations.crm`` and Calendar providers (google, google_service_account,
microsoft, calcom, internal) to ``app.integrations.calendar``. Unconfigured
enterprise connectors fail closed with ``NOT_CONFIGURED`` and never fabricate
external IDs or fake health checks.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import log
from app.core.tracing import span
from app.db.models import AuditAction, Connector, CrmProviderType
from app.security.secret_store import SecretStoreError, get_secret_store


@dataclass(frozen=True)
class ConnectorContext:
    tenant_id: uuid.UUID
    connector_id: uuid.UUID
    provider: str
    config: dict[str, Any]
    credentials: dict[str, Any]


@dataclass(frozen=True)
class ConnectorResult:
    ok: bool
    status: str
    data: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    attempts: int = 1
    duration_ms: float = 0.0


class ConnectorAdapter(Protocol):
    provider: str
    capabilities: frozenset[str]

    async def validate_credentials(self, context: ConnectorContext) -> None: ...
    async def dispatch(
        self, context: ConnectorContext, operation: str, payload: dict[str, Any]
    ) -> dict[str, Any]: ...
    async def health(self, context: ConnectorContext) -> dict[str, Any]: ...


@dataclass(frozen=True)
class ConnectorRegistration:
    provider: str
    kind: str
    capabilities: frozenset[str]
    factory: Callable[[ConnectorContext], ConnectorAdapter]
    required_credentials: frozenset[str] = frozenset()


class ConnectorRegistry:
    def __init__(self) -> None:
        self._items: dict[str, ConnectorRegistration] = {}

    def register(self, registration: ConnectorRegistration) -> None:
        if registration.provider in self._items:
            raise ValueError(f"connector provider already registered: {registration.provider}")
        self._items[registration.provider] = registration

    def get(self, provider: str) -> ConnectorRegistration:
        try:
            return self._items[provider]
        except KeyError as exc:
            raise KeyError(f"connector provider is not registered: {provider}") from exc

    def providers(self) -> tuple[str, ...]:
        return tuple(sorted(self._items))


registry = ConnectorRegistry()


class _CrmAdapter:
    def __init__(self, provider: str, context: ConnectorContext):
        from app.integrations.crm.base import ProviderContext
        from app.integrations.crm.registry import build

        self.provider = provider
        crm_context = ProviderContext(
            tenant_id=str(context.tenant_id),
            credentials=context.credentials,
            config=context.config,
            field_mappings={},
            timeout_seconds=10.0,
        )
        self._adapter = build(CrmProviderType(provider), crm_context)
        self.capabilities = frozenset(item.value for item in self._adapter.capabilities)

    async def validate_credentials(self, context: ConnectorContext) -> None:
        result = await self._adapter.health_check()
        if not result.connected:
            raise RuntimeError(result.safe_message or "provider health check failed")

    async def health(self, context: ConnectorContext) -> dict[str, Any]:
        result = await self._adapter.health_check()
        return {
            "connected": result.connected,
            "provider": result.provider,
            "latency_ms": result.latency_ms,
            "message": result.safe_message,
        }

    async def dispatch(
        self, context: ConnectorContext, operation: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        from app.integrations.crm.models import NormalizedActivity, NormalizedContact

        if operation == "health_check":
            return await self.health(context)
        if operation in {"upsert_contact", "create_contact", "update_contact"}:
            fields = {
                key: value
                for key, value in payload.items()
                if key in NormalizedContact.__dataclass_fields__
            }
            contact = NormalizedContact(**fields)
            if operation == "upsert_contact":
                result = await self._adapter.upsert_contact(contact)
            elif operation == "create_contact":
                result = await self._adapter.create_contact(contact)
            else:
                result = await self._adapter.update_contact(str(payload["external_id"]), contact)
            return {
                "external_id": result.external_id,
                "already_existed": result.already_existed,
                "details": result.details,
            }
        if operation in {"create_note", "create_activity"}:
            activity = NormalizedActivity(
                title=str(payload.get("title", "")), body=str(payload.get("body", ""))
            )
            method = (
                self._adapter.create_note
                if operation == "create_note"
                else self._adapter.create_activity
            )
            result = await method(str(payload["external_contact_id"]), activity)
            return {"external_id": result.external_id, "details": result.details}
        raise RuntimeError("connector operation is not mapped")


class _CalendarAdapter:
    def __init__(self, provider: str, context: ConnectorContext):
        from app.db.models import CalendarProviderType
        from app.integrations.calendar.base import CalendarContext
        from app.integrations.calendar.registry import build

        self.provider = provider
        self._adapter = build(
            CalendarProviderType(provider),
            CalendarContext(
                tenant_id=str(context.tenant_id),
                credentials=context.credentials,
                config=context.config,
                timeout_seconds=10.0,
            ),
        )
        self.capabilities = frozenset({"health_check"})

    async def validate_credentials(self, context: ConnectorContext) -> None:
        result = await self._adapter.health_check()
        if not result.connected:
            raise RuntimeError(result.safe_message or "calendar health check failed")

    async def health(self, context: ConnectorContext) -> dict[str, Any]:
        result = await self._adapter.health_check()
        return {
            "connected": result.connected,
            "provider": result.provider,
            "latency_ms": result.latency_ms,
            "message": result.safe_message,
        }

    async def dispatch(
        self, context: ConnectorContext, operation: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        if operation != "health_check":
            raise RuntimeError("calendar operations use the existing calendar service contract")
        return await self.health(context)


class _UnconfiguredEnterpriseAdapter:
    """Fail-closed adapter for enterprise connectors without a live transport adapter."""

    provider: str
    capabilities: frozenset[str]

    def __init__(self, provider: str, context: ConnectorContext):
        self.provider = provider
        self._context = context
        self.capabilities = frozenset({"health_check"})

    async def validate_credentials(self, context: ConnectorContext) -> None:
        raise RuntimeError(
            f"NOT_CONFIGURED: {self.provider} transport adapter is not configured"
        )

    async def health(self, context: ConnectorContext) -> dict[str, Any]:
        return {
            "connected": False,
            "provider": self.provider,
            "latency_ms": 0.0,
            "status": "not_configured",
            "message": f"NOT_CONFIGURED: {self.provider} adapter is not configured for tenant {context.tenant_id}",
        }

    async def dispatch(
        self, context: ConnectorContext, operation: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        if operation == "health_check":
            return await self.health(context)
        raise RuntimeError(
            f"NOT_CONFIGURED: {self.provider} operation {operation} is not configured"
        )


def _make_enterprise_factory(provider: str):
    def factory(ctx: ConnectorContext) -> ConnectorAdapter:
        return _UnconfiguredEnterpriseAdapter(provider, ctx)

    return factory


def _register_existing() -> None:
    crm_caps = frozenset(
        {
            "health_check",
            "upsert_contact",
            "create_contact",
            "update_contact",
            "create_note",
            "create_activity",
        }
    )
    for provider in ("gohighlevel", "hubspot", "jobber", "salesforce", "webhook"):
        registry.register(
            ConnectorRegistration(
                provider, "crm", crm_caps, lambda ctx, p=provider: _CrmAdapter(p, ctx)
            )
        )
    for provider in ("google", "google_service_account", "microsoft", "calcom", "internal"):
        registry.register(
            ConnectorRegistration(
                provider,
                "calendar",
                frozenset({"health_check"}),
                lambda ctx, p=provider: _CalendarAdapter(p, ctx),
            )
        )
    enterprise_providers = (
        "dynamics",
        "servicenow",
        "sap",
        "sharepoint",
        "onedrive",
        "confluence",
        "jira",
        "zendesk",
        "freshdesk",
        "zoho",
        "shopify",
    )
    for provider in enterprise_providers:
        registry.register(
            ConnectorRegistration(
                provider=provider,
                kind="enterprise",
                capabilities=frozenset({"health_check"}),
                factory=_make_enterprise_factory(provider),
                required_credentials=frozenset(),
            )
        )


_register_existing()


async def _context(session: AsyncSession, row: Connector) -> ConnectorContext:
    credentials: dict[str, Any] = {}
    if row.credential_ref:
        try:
            value = get_secret_store().get(
                str(row.tenant_id), f"connector:{row.provider}", row.credential_ref
            )
        except SecretStoreError as exc:
            raise RuntimeError("connector credentials are unavailable") from exc
        try:
            parsed = json.loads(value)
            credentials = parsed if isinstance(parsed, dict) else {"access_token": value}
        except json.JSONDecodeError:
            credentials = {"access_token": value, "signing_secret": value}
    return ConnectorContext(
        row.tenant_id, row.id, row.provider, dict(row.config or {}), credentials
    )


def _audit():
    from app.auth.identity.events import emit

    return emit


async def validate_connector(session: AsyncSession, row: Connector) -> ConnectorResult:
    started = time.perf_counter()
    try:
        registration = registry.get(row.provider)
        context = await _context(session, row)
        if registration.required_credentials - context.credentials.keys():
            raise RuntimeError("required connector credentials are missing")
        with span(
            "voxdesk.connector.validate", tenant_id=str(row.tenant_id), provider=row.provider
        ):
            await registration.factory(context).validate_credentials(context)
        row.status, row.health_message, row.last_health_at = "healthy", None, datetime.utcnow()
        await _audit()(
            session,
            AuditAction.INTEGRATION_TESTED,
            tenant_id=row.tenant_id,
            detail={"provider": row.provider, "outcome": "healthy"},
        )
        return ConnectorResult(True, "healthy", duration_ms=(time.perf_counter() - started) * 1000)
    except Exception as exc:
        row.status, row.health_message, row.last_health_at = (
            "unhealthy",
            type(exc).__name__,
            datetime.utcnow(),
        )
        await _audit()(
            session,
            AuditAction.INTEGRATION_TESTED,
            tenant_id=row.tenant_id,
            detail={
                "provider": row.provider,
                "outcome": "unhealthy",
                "error_type": type(exc).__name__,
            },
        )
        return ConnectorResult(
            False,
            "unhealthy",
            error_code=type(exc).__name__,
            duration_ms=(time.perf_counter() - started) * 1000,
        )


async def dispatch(
    session: AsyncSession,
    row: Connector,
    operation: str,
    payload: dict[str, Any],
    *,
    max_attempts: int = 3,
) -> ConnectorResult:
    started = time.perf_counter()
    attempts = 0
    try:
        registration = registry.get(row.provider)
        if operation not in registration.capabilities:
            return ConnectorResult(False, "unsupported", error_code="unsupported_capability")
        context = await _context(session, row)
        adapter = registration.factory(context)
        for attempts in range(1, max(1, min(max_attempts, 5)) + 1):
            try:
                with span(
                    "voxdesk.connector.dispatch",
                    tenant_id=str(row.tenant_id),
                    provider=row.provider,
                    operation=operation,
                ):
                    data = await asyncio.wait_for(
                        adapter.dispatch(context, operation, payload), timeout=10.0
                    )
                await _audit()(
                    session,
                    AuditAction.INTEGRATION_TESTED,
                    tenant_id=row.tenant_id,
                    detail={"provider": row.provider, "operation": operation, "outcome": "success"},
                )
                return ConnectorResult(
                    True,
                    "success",
                    data=data,
                    attempts=attempts,
                    duration_ms=(time.perf_counter() - started) * 1000,
                )
            except (TimeoutError, asyncio.TimeoutError, OSError) as exc:
                if attempts >= max_attempts:
                    return ConnectorResult(
                        False,
                        "failed",
                        error_code=type(exc).__name__,
                        attempts=attempts,
                        duration_ms=(time.perf_counter() - started) * 1000,
                    )
                await asyncio.sleep(min(2 ** (attempts - 1), 4))
        return ConnectorResult(False, "failed", error_code="connector_error", attempts=attempts)
    except Exception as exc:
        log.warning(
            "connector.dispatch_failed", provider=row.provider, error_type=type(exc).__name__
        )
        return ConnectorResult(
            False,
            "failed",
            error_code=type(exc).__name__,
            attempts=max(1, attempts),
            duration_ms=(time.perf_counter() - started) * 1000,
        )
