"""
app/telephony/phone_numbers.py
Durable E.164 phone number lifecycle management, inbound/outbound agent bindings,
tenant isolation, and health validation.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Agent
from app.db.telephony_models import TelephonyPhoneNumber, TelephonySipConnection
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
        outbound_agent_id=row.outbound_agent_id,
        status=row.status,
        metadata=dict(row.metadata_json or {}),
        last_health_check_at=row.last_health_check_at,
        last_health_error=row.last_health_error,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


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

        now = datetime.utcnow()
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
            outbound_agent_id=outbound_agent,
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
        row = await self._get_row(tenant_id=tenant_id, phone_number_id=phone_number_id)
        return _serialize_phone_number(row)

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
        row = await self._get_row(tenant_id=tenant_id, phone_number_id=phone_number_id)
        fields_set = payload.model_fields_set

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
        if "inbound_agent_id" in fields_set:
            row.inbound_agent_id = await _validate_agent_ownership(
                self.session,
                tenant_id=tenant_id,
                agent_id=payload.inbound_agent_id,
                field_name="inbound_agent_id",
            )
        if "outbound_agent_id" in fields_set:
            row.outbound_agent_id = await _validate_agent_ownership(
                self.session,
                tenant_id=tenant_id,
                agent_id=payload.outbound_agent_id,
                field_name="outbound_agent_id",
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

        row.updated_at = datetime.utcnow()
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

        if "inbound_agent_id" in fields_set:
            row.inbound_agent_id = await _validate_agent_ownership(
                self.session,
                tenant_id=tenant_id,
                agent_id=payload.inbound_agent_id,
                field_name="inbound_agent_id",
            )
        if "outbound_agent_id" in fields_set:
            row.outbound_agent_id = await _validate_agent_ownership(
                self.session,
                tenant_id=tenant_id,
                agent_id=payload.outbound_agent_id,
                field_name="outbound_agent_id",
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
        row.updated_at = datetime.utcnow()
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
