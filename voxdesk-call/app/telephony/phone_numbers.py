"""
app/telephony/phone_numbers.py
Durable E.164 phone number lifecycle management, inbound/outbound agent bindings,
tenant isolation, and health validation.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Agent, AgentVersion
from app.db.telephony_models import TelephonyPhoneNumber, TelephonySipConnection
from app.telephony.number_provisioning import PhoneNumber
from app.telephony.number_routing import RoutingError, build_routing
from app.telephony.enums import (
    PhoneNumberLifecycleStatus,
    SipConnectionStatus,
    TelephonyProviderName,
)
from app.telephony.exceptions import (
    PhoneNumberNotFoundError,
    PhoneNumberValidationError,
    TelephonyAuthorizationError,
)
from app.telephony.schemas import (
    PhoneNumberBindAgentRequest,
    PhoneNumberCreateRequest,
    PhoneNumberListResponse,
    PhoneNumberResponse,
    PhoneNumberRouting,
    PhoneNumberUpdateRequest,
    coerce_e164,
)


def _serialize_phone_number(row: TelephonyPhoneNumber) -> PhoneNumberResponse:
    return PhoneNumberResponse(
        id=row.id,
        organization_id=row.tenant_id,
        environment_id=row.environment_id,
        number=row.number,
        e164_number=row.e164_number,
        provider=row.provider,
        provider_number_id=row.provider_number_id,
        sip_connection_id=row.sip_connection_id,
        sip_enabled=bool(row.sip_enabled),
        inbound_enabled=bool(row.inbound_enabled),
        outbound_enabled=bool(row.outbound_enabled),
        inbound_agent_id=row.inbound_agent_id,
        inbound_agent_version=getattr(row, "inbound_agent_version", None),
        outbound_agent_id=row.outbound_agent_id,
        routing=_row_routing(row),
        status=row.status,
        metadata=dict(row.metadata_json or {}),
        last_health_check_at=row.last_health_check_at,
        last_health_error=row.last_health_error,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _serialize_legacy_phone_number(row: PhoneNumber) -> PhoneNumberResponse:
    now = datetime.now(timezone.utc)
    return PhoneNumberResponse(
        id=row.id,
        organization_id=row.tenant_id,
        environment_id=row.environment_id,
        number=row.e164,
        e164_number=row.e164,
        provider=str(row.provider or "twilio").upper(),
        provider_number_id=row.external_id,
        sip_connection_id=None,
        sip_enabled=False,
        inbound_enabled=True,
        outbound_enabled=True,
        inbound_agent_id=str(row.inbound_agent_id) if row.inbound_agent_id else None,
        inbound_agent_version=row.inbound_agent_version,
        outbound_agent_id=str(row.outbound_agent_id) if row.outbound_agent_id else None,
        routing=_row_routing(row),
        status=str(row.status or "ACTIVE").upper(),
        metadata=dict(row.capabilities or {}),
        last_health_check_at=None,
        last_health_error=None,
        created_at=row.created_at or now,
        updated_at=row.updated_at or now,
    )


async def _validate_agent_version(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    agent_id: str | None,
    version_number: int | None,
) -> int | None:
    if version_number is None:
        return None
    if not agent_id:
        raise PhoneNumberValidationError(
            "Cannot pin inbound_agent_version without inbound_agent_id.",
            detail={"field": "inbound_agent_version"},
        )
    try:
        agent_uuid = UUID(str(agent_id))
    except ValueError:
        return int(version_number)
    ver = (
        await session.execute(
            select(AgentVersion).where(
                AgentVersion.tenant_id == tenant_id,
                AgentVersion.agent_id == agent_uuid,
                AgentVersion.version_number == int(version_number),
            )
        )
    ).scalar_one_or_none()
    if ver is None:
        raise PhoneNumberValidationError(
            f"AgentVersion v{version_number} not found for agent '{agent_id}'.",
            detail={"field": "inbound_agent_version", "version_number": version_number},
        )
    return int(version_number)


async def _validate_agent_ownership(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    agent_id: str | None,
    field_name: str,
) -> str | None:
    if agent_id is None or not str(agent_id).strip():
        return None
    cleaned = str(agent_id).strip()
    try:
        agent_uuid = UUID(cleaned)
    except ValueError:
        # Allow deterministic string agent IDs if no UUID row exists,
        # but check if any Agent row matches UUID when valid UUID
        agent_uuid = None

    if agent_uuid is not None:
        row = (
            await session.execute(select(Agent).where(Agent.id == agent_uuid))
        ).scalar_one_or_none()
        if row is None:
            raise PhoneNumberValidationError(
                f"Agent '{cleaned}' for '{field_name}' does not exist.",
                detail={"field": field_name, "agent_id": cleaned},
            )
        if row.tenant_id != tenant_id:
            raise TelephonyAuthorizationError(
                f"Agent '{cleaned}' belongs to a different organization.",
                detail={"field": field_name, "agent_id": cleaned},
            )
        if getattr(row, "deleted_at", None) is not None or row.status == "archived":
            raise PhoneNumberValidationError(
                f"Agent '{cleaned}' is archived and cannot be bound to a phone number.",
                detail={"field": field_name, "agent_id": cleaned},
            )
    return cleaned


def _row_routing(row: TelephonyPhoneNumber | PhoneNumber) -> dict[str, list[dict[str, Any]]]:
    """Return the effective routing contract, including legacy single-agent aliases."""
    return build_routing(
        getattr(row, "routing", None),
        legacy_inbound_agent_id=(str(row.inbound_agent_id) if row.inbound_agent_id else ""),
        legacy_outbound_agent_id=(str(row.outbound_agent_id) if row.outbound_agent_id else ""),
    ).as_dict()


def _route_agent_id(
    routing: dict[str, list[dict[str, Any]]], channel: str
) -> str | None:
    entries = routing.get(channel) or []
    return str(entries[0].get("agent_id")) if entries else None


def _set_row_agent_alias(
    row: TelephonyPhoneNumber | PhoneNumber,
    *,
    channel: str,
    agent_id: str | None,
) -> None:
    field_name = f"{channel}_agent_id"
    if not hasattr(row, field_name):
        return
    if isinstance(row, PhoneNumber):
        try:
            setattr(row, field_name, UUID(agent_id) if agent_id else None)
        except (TypeError, ValueError):
            # The JSON route can use an external key; the UUID legacy alias cannot.
            setattr(row, field_name, None)
    else:
        setattr(row, field_name, agent_id)


def _sync_legacy_agent_edits(
    row: TelephonyPhoneNumber | PhoneNumber,
    routing: dict[str, list[dict[str, Any]]],
    fields_set: set[str],
) -> set[str]:
    """Make an explicitly edited legacy alias authoritative for its channel."""
    changed: set[str] = set()
    for channel in ("inbound", "outbound"):
        if f"{channel}_agent_id" not in fields_set:
            continue
        agent_id = getattr(row, f"{channel}_agent_id", None)
        routing[channel] = (
            [{"agent_id": str(agent_id), "weight": 1.0, "label": ""}]
            if agent_id
            else []
        )
        changed.add(channel)
    return changed


def _apply_routing_aliases(
    row: TelephonyPhoneNumber | PhoneNumber,
    routing: dict[str, list[dict[str, Any]]],
    channels: set[str],
) -> None:
    for channel in channels & {"inbound", "outbound"}:
        old_agent = getattr(row, f"{channel}_agent_id", None)
        old_agent = str(old_agent) if old_agent else None
        new_agent = _route_agent_id(routing, channel)
        _set_row_agent_alias(row, channel=channel, agent_id=new_agent)
        if channel == "inbound" and old_agent != new_agent:
            row.inbound_agent_version = None


def _routing_patch_channels(patch: PhoneNumberRouting | None) -> set[str]:
    if patch is None:
        return set()
    return {
        channel
        for channel in ("inbound", "outbound", "sms")
        if channel in patch.model_fields_set and getattr(patch, channel) is not None
    }


def _build_routing_patch_base(
    row: TelephonyPhoneNumber | PhoneNumber,
    *,
    legacy_fields_set: set[str] | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], set[str]]:
    routing = _row_routing(row)
    synced = _sync_legacy_agent_edits(row, routing, legacy_fields_set or set())
    return routing, synced


async def _merge_routing_patch(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    base: dict[str, list[dict[str, Any]]],
    patch: PhoneNumberRouting | None,
) -> dict[str, list[dict[str, Any]]]:
    """Validate newly submitted entries and merge only their supplied channels."""
    for channel in _routing_patch_channels(patch):
        entries = getattr(patch, channel) or []
        normalized: list[dict[str, Any]] = []
        for entry in entries:
            agent_id = await _validate_agent_ownership(
                session,
                tenant_id=tenant_id,
                agent_id=entry.agent_id,
                field_name=f"routing.{channel}",
            )
            if agent_id is None:
                raise PhoneNumberValidationError(
                    f"A routing.{channel} entry must include an agent id.",
                    detail={"field": f"routing.{channel}"},
                )
            normalized.append(
                {"agent_id": agent_id, "weight": float(entry.weight), "label": entry.label}
            )
        base[channel] = normalized
    try:
        return build_routing(base).as_dict()
    except (RoutingError, TypeError, ValueError) as exc:
        raise PhoneNumberValidationError(
            f"Invalid phone-number routing configuration: {exc}",
            detail={"field": "routing"},
        ) from exc


def _compute_lifecycle_status(
    *,
    provider: str,
    provider_number_id: str | None,
    inbound_agent_id: str | None,
    outbound_agent_id: str | None,
    sip_enabled: bool,
    sip_connection_id: UUID | None,
    sip_ready: bool,
) -> PhoneNumberLifecycleStatus:
    has_agent = bool(inbound_agent_id or outbound_agent_id)
    if sip_enabled and not sip_connection_id:
        return PhoneNumberLifecycleStatus.NOT_CONFIGURED
    if sip_enabled and not sip_ready:
        return PhoneNumberLifecycleStatus.CONFIGURED
    if has_agent or provider_number_id or provider == TelephonyProviderName.SIMULATED.value:
        return PhoneNumberLifecycleStatus.READY
    return PhoneNumberLifecycleStatus.CONFIGURED


class PhoneNumberService:
    """Authoritative service for E.164 phone number lifecycle and agent bindings."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_phone_number(
        self,
        *,
        tenant_id: UUID,
        payload: PhoneNumberCreateRequest,
        environment_id: UUID | None = None,
    ) -> PhoneNumberResponse:
        e164 = coerce_e164(payload.number, field_name="number")
        resolved_env = payload.environment_id or environment_id

        existing = (
            await self.session.execute(
                select(TelephonyPhoneNumber).where(
                    TelephonyPhoneNumber.tenant_id == tenant_id,
                    TelephonyPhoneNumber.e164_number == e164,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise PhoneNumberValidationError(
                f"Phone number {e164} is already registered in this organization.",
                status_code=409,
                code="PHONE_NUMBER_ALREADY_EXISTS",
                detail={"e164_number": e164, "phone_number_id": str(existing.id)},
            )

        inbound_agent = await _validate_agent_ownership(
            self.session,
            tenant_id=tenant_id,
            agent_id=payload.inbound_agent_id,
            field_name="inbound_agent_id",
        )
        outbound_agent = await _validate_agent_ownership(
            self.session,
            tenant_id=tenant_id,
            agent_id=payload.outbound_agent_id,
            field_name="outbound_agent_id",
        )
        routing_data: dict[str, list[dict[str, Any]]] | None = None
        if payload.routing is not None:
            base = build_routing(
                None,
                legacy_inbound_agent_id=inbound_agent or "",
                legacy_outbound_agent_id=outbound_agent or "",
            ).as_dict()
            routing_data = await _merge_routing_patch(
                self.session,
                tenant_id=tenant_id,
                base=base,
                patch=payload.routing,
            )
            route_channels = _routing_patch_channels(payload.routing)
            if "inbound" in route_channels:
                inbound_agent = _route_agent_id(routing_data, "inbound")
            if "outbound" in route_channels:
                outbound_agent = _route_agent_id(routing_data, "outbound")
        inbound_version = await _validate_agent_version(
            self.session,
            tenant_id=tenant_id,
            agent_id=inbound_agent,
            version_number=payload.inbound_agent_version,
        )

        sip_ready = False
        if payload.sip_connection_id is not None:
            sip_row = (
                await self.session.execute(
                    select(TelephonySipConnection).where(
                        TelephonySipConnection.id == payload.sip_connection_id,
                        TelephonySipConnection.tenant_id == tenant_id,
                    )
                )
            ).scalar_one_or_none()
            if sip_row is None:
                raise PhoneNumberValidationError(
                    "SIP connection not found in this organization.",
                    detail={"sip_connection_id": str(payload.sip_connection_id)},
                )
            sip_ready = sip_row.status == SipConnectionStatus.READY.value

        lifecycle = _compute_lifecycle_status(
            provider=payload.provider.value,
            provider_number_id=payload.provider_number_id,
            inbound_agent_id=inbound_agent,
            outbound_agent_id=outbound_agent,
            sip_enabled=payload.sip_enabled,
            sip_connection_id=payload.sip_connection_id,
            sip_ready=sip_ready,
        )

        now = datetime.now(timezone.utc)
        row = TelephonyPhoneNumber(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            environment_id=resolved_env,
            number=e164,
            e164_number=e164,
            provider=payload.provider.value,
            provider_number_id=payload.provider_number_id
            or f"pn_{payload.provider.value.lower()}_{uuid.uuid4().hex[:10]}",
            sip_connection_id=payload.sip_connection_id,
            sip_enabled=payload.sip_enabled,
            inbound_enabled=payload.inbound_enabled,
            outbound_enabled=payload.outbound_enabled,
            inbound_agent_id=inbound_agent,
            inbound_agent_version=inbound_version,
            outbound_agent_id=outbound_agent,
            routing=routing_data,
            status=lifecycle.value,
            metadata_json=dict(payload.metadata or {}),
            last_health_check_at=now,
            created_at=now,
            updated_at=now,
        )
        try:
            self.session.add(row)
            await self.session.flush()
        except IntegrityError as exc:
            raise PhoneNumberValidationError(
                f"Phone number {e164} already exists.",
                status_code=409,
                code="PHONE_NUMBER_ALREADY_EXISTS",
                detail={"e164_number": e164},
            ) from exc

        return _serialize_phone_number(row)

    async def list_phone_numbers(
        self,
        *,
        tenant_id: UUID,
        environment_id: UUID | None = None,
        status: str | None = None,
    ) -> PhoneNumberListResponse:
        stmt = select(TelephonyPhoneNumber).where(
            TelephonyPhoneNumber.tenant_id == tenant_id
        )
        if environment_id is not None:
            stmt = stmt.where(
                (TelephonyPhoneNumber.environment_id == environment_id)
                | (TelephonyPhoneNumber.environment_id.is_(None))
            )
        if status:
            stmt = stmt.where(TelephonyPhoneNumber.status == status.upper().strip())
        stmt = stmt.order_by(TelephonyPhoneNumber.created_at.desc())
        rows = (await self.session.execute(stmt)).scalars().all()
        items = [_serialize_phone_number(r) for r in rows]
        return PhoneNumberListResponse(items=items, total=len(items))

    async def get_phone_number(
        self,
        *,
        tenant_id: UUID,
        phone_number_id: UUID,
    ) -> PhoneNumberResponse:
        try:
            row = await self._get_row(tenant_id=tenant_id, phone_number_id=phone_number_id)
            return _serialize_phone_number(row)
        except PhoneNumberNotFoundError:
            legacy = await self._get_legacy_row(tenant_id=tenant_id, phone_number_id=phone_number_id)
            return _serialize_legacy_phone_number(legacy)

    async def find_by_e164(
        self,
        *,
        e164_number: str,
        tenant_id: UUID | None = None,
    ) -> TelephonyPhoneNumber | None:
        normalized = coerce_e164(e164_number, field_name="e164_number")
        stmt = select(TelephonyPhoneNumber).where(
            TelephonyPhoneNumber.e164_number == normalized
        )
        if tenant_id is not None:
            stmt = stmt.where(TelephonyPhoneNumber.tenant_id == tenant_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def update_phone_number(
        self,
        *,
        tenant_id: UUID,
        phone_number_id: UUID,
        payload: PhoneNumberUpdateRequest,
    ) -> PhoneNumberResponse:
        fields_set = payload.model_fields_set
        try:
            row = await self._get_row(tenant_id=tenant_id, phone_number_id=phone_number_id)
        except PhoneNumberNotFoundError:
            legacy = await self._get_legacy_row(tenant_id=tenant_id, phone_number_id=phone_number_id)
            previous_inbound = str(legacy.inbound_agent_id) if legacy.inbound_agent_id else None
            if "inbound_agent_id" in fields_set:
                val = await _validate_agent_ownership(
                    self.session,
                    tenant_id=tenant_id,
                    agent_id=payload.inbound_agent_id,
                    field_name="inbound_agent_id",
                )
                try:
                    legacy.inbound_agent_id = UUID(val) if val else None
                except (TypeError, ValueError):
                    legacy.inbound_agent_id = None
                if previous_inbound != val and "inbound_agent_version" not in fields_set:
                    legacy.inbound_agent_version = None
            if "outbound_agent_id" in fields_set:
                val = await _validate_agent_ownership(
                    self.session,
                    tenant_id=tenant_id,
                    agent_id=payload.outbound_agent_id,
                    field_name="outbound_agent_id",
                )
                try:
                    legacy.outbound_agent_id = UUID(val) if val else None
                except (TypeError, ValueError):
                    legacy.outbound_agent_id = None

            routing_data, legacy_route_channels = _build_routing_patch_base(
                legacy, legacy_fields_set=fields_set
            )
            route_channels = _routing_patch_channels(payload.routing)
            if payload.routing is not None:
                routing_data = await _merge_routing_patch(
                    self.session,
                    tenant_id=tenant_id,
                    base=routing_data,
                    patch=payload.routing,
                )
            changed_channels = legacy_route_channels | route_channels
            if changed_channels:
                _apply_routing_aliases(legacy, routing_data, changed_channels)
                legacy.routing = routing_data
            if "inbound_agent_version" in fields_set:
                legacy.inbound_agent_version = await _validate_agent_version(
                    self.session,
                    tenant_id=tenant_id,
                    agent_id=str(legacy.inbound_agent_id) if legacy.inbound_agent_id else None,
                    version_number=payload.inbound_agent_version,
                )
            await self.session.flush()
            return _serialize_legacy_phone_number(legacy)

        if "provider" in fields_set and payload.provider is not None:
            row.provider = payload.provider.value
        if "provider_number_id" in fields_set:
            row.provider_number_id = payload.provider_number_id
        if "sip_connection_id" in fields_set:
            row.sip_connection_id = payload.sip_connection_id
        if "sip_enabled" in fields_set and payload.sip_enabled is not None:
            row.sip_enabled = payload.sip_enabled
        if "inbound_enabled" in fields_set and payload.inbound_enabled is not None:
            row.inbound_enabled = payload.inbound_enabled
        if "outbound_enabled" in fields_set and payload.outbound_enabled is not None:
            row.outbound_enabled = payload.outbound_enabled
        previous_inbound = str(row.inbound_agent_id) if row.inbound_agent_id else None
        if "inbound_agent_id" in fields_set:
            row.inbound_agent_id = await _validate_agent_ownership(
                self.session,
                tenant_id=tenant_id,
                agent_id=payload.inbound_agent_id,
                field_name="inbound_agent_id",
            )
            if (
                previous_inbound != row.inbound_agent_id
                and "inbound_agent_version" not in fields_set
            ):
                row.inbound_agent_version = None
        if "outbound_agent_id" in fields_set:
            row.outbound_agent_id = await _validate_agent_ownership(
                self.session,
                tenant_id=tenant_id,
                agent_id=payload.outbound_agent_id,
                field_name="outbound_agent_id",
            )

        routing_data, legacy_route_channels = _build_routing_patch_base(
            row, legacy_fields_set=fields_set
        )
        route_channels = _routing_patch_channels(payload.routing)
        if payload.routing is not None:
            routing_data = await _merge_routing_patch(
                self.session,
                tenant_id=tenant_id,
                base=routing_data,
                patch=payload.routing,
            )
        changed_channels = legacy_route_channels | route_channels
        if changed_channels:
            _apply_routing_aliases(row, routing_data, changed_channels)
            row.routing = routing_data
        if "inbound_agent_version" in fields_set:
            row.inbound_agent_version = await _validate_agent_version(
                self.session,
                tenant_id=tenant_id,
                agent_id=row.inbound_agent_id,
                version_number=payload.inbound_agent_version,
            )
        if "metadata" in fields_set and payload.metadata is not None:
            merged = dict(row.metadata_json or {})
            merged.update(payload.metadata)
            row.metadata_json = merged

        if "status" in fields_set and payload.status is not None:
            row.status = payload.status.value
        else:
            row.status = _compute_lifecycle_status(
                provider=row.provider,
                provider_number_id=row.provider_number_id,
                inbound_agent_id=row.inbound_agent_id,
                outbound_agent_id=row.outbound_agent_id,
                sip_enabled=row.sip_enabled,
                sip_connection_id=row.sip_connection_id,
                sip_ready=True if row.sip_connection_id else False,
            ).value

        row.updated_at = datetime.now(timezone.utc)
        await self.session.flush()
        return _serialize_phone_number(row)

    async def bind_agent(
        self,
        *,
        tenant_id: UUID,
        phone_number_id: UUID,
        payload: PhoneNumberBindAgentRequest,
    ) -> PhoneNumberResponse:
        row = await self._get_row(tenant_id=tenant_id, phone_number_id=phone_number_id)
        fields_set = payload.model_fields_set

        previous_inbound = str(row.inbound_agent_id) if row.inbound_agent_id else None
        if "inbound_agent_id" in fields_set:
            row.inbound_agent_id = await _validate_agent_ownership(
                self.session,
                tenant_id=tenant_id,
                agent_id=payload.inbound_agent_id,
                field_name="inbound_agent_id",
            )
            if (
                previous_inbound != row.inbound_agent_id
                and "inbound_agent_version" not in fields_set
            ):
                row.inbound_agent_version = None
        if "outbound_agent_id" in fields_set:
            row.outbound_agent_id = await _validate_agent_ownership(
                self.session,
                tenant_id=tenant_id,
                agent_id=payload.outbound_agent_id,
                field_name="outbound_agent_id",
            )
        routing_data, legacy_route_channels = _build_routing_patch_base(
            row, legacy_fields_set=fields_set
        )
        route_channels = _routing_patch_channels(payload.routing)
        if payload.routing is not None:
            routing_data = await _merge_routing_patch(
                self.session,
                tenant_id=tenant_id,
                base=routing_data,
                patch=payload.routing,
            )
        changed_channels = legacy_route_channels | route_channels
        if changed_channels:
            _apply_routing_aliases(row, routing_data, changed_channels)
            row.routing = routing_data
        if "inbound_agent_version" in fields_set:
            row.inbound_agent_version = await _validate_agent_version(
                self.session,
                tenant_id=tenant_id,
                agent_id=row.inbound_agent_id,
                version_number=payload.inbound_agent_version,
            )
        if "inbound_enabled" in fields_set and payload.inbound_enabled is not None:
            row.inbound_enabled = payload.inbound_enabled
        if "outbound_enabled" in fields_set and payload.outbound_enabled is not None:
            row.outbound_enabled = payload.outbound_enabled

        row.status = _compute_lifecycle_status(
            provider=row.provider,
            provider_number_id=row.provider_number_id,
            inbound_agent_id=row.inbound_agent_id,
            outbound_agent_id=row.outbound_agent_id,
            sip_enabled=row.sip_enabled,
            sip_connection_id=row.sip_connection_id,
            sip_ready=True if row.sip_connection_id else False,
        ).value
        row.updated_at = datetime.now(timezone.utc)
        await self.session.flush()
        return _serialize_phone_number(row)

    async def delete_phone_number(
        self,
        *,
        tenant_id: UUID,
        phone_number_id: UUID,
    ) -> dict[str, Any]:
        row = await self._get_row(tenant_id=tenant_id, phone_number_id=phone_number_id)
        e164 = row.e164_number
        await self.session.delete(row)
        await self.session.flush()
        return {"deleted": True, "id": str(phone_number_id), "e164_number": e164}

    async def _get_row(
        self,
        *,
        tenant_id: UUID,
        phone_number_id: UUID,
    ) -> TelephonyPhoneNumber:
        row = (
            await self.session.execute(
                select(TelephonyPhoneNumber).where(
                    TelephonyPhoneNumber.id == phone_number_id,
                    TelephonyPhoneNumber.tenant_id == tenant_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise PhoneNumberNotFoundError(
                f"Phone number '{phone_number_id}' not found in this organization.",
                detail={"phone_number_id": str(phone_number_id)},
            )
        return row

    async def _get_legacy_row(
        self,
        *,
        tenant_id: UUID,
        phone_number_id: UUID,
    ) -> PhoneNumber:
        row = (
            await self.session.execute(
                select(PhoneNumber).where(
                    PhoneNumber.id == phone_number_id,
                    PhoneNumber.tenant_id == tenant_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise PhoneNumberNotFoundError(
                f"Phone number '{phone_number_id}' not found in this organization.",
                detail={"phone_number_id": str(phone_number_id)},
            )
        return row
