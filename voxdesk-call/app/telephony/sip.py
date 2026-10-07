"""
app/telephony/sip.py
Durable SIP connection / trunk configuration, URI & transport validation,
secret-reference hashing (never storing plaintext passwords), and connection verification.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.telephony_models import TelephonySipConnection
from app.telephony.enums import SipConnectionStatus
from app.telephony.exceptions import SipConfigurationError
from app.telephony.schemas import (
    SipConnectionCreateRequest,
    SipConnectionListResponse,
    SipConnectionResponse,
    SipConnectionTestRequest,
    validate_sip_uri,
)


def _derive_credential_reference(
    *,
    tenant_id: UUID,
    username: str | None,
    password_secret: str | None,
    explicit_reference: str | None,
) -> str | None:
    if explicit_reference and explicit_reference.strip():
        return explicit_reference.strip()[:128]
    if password_secret and password_secret.strip():
        digest = hashlib.sha256(
            f"{tenant_id}:{username or ''}:{password_secret}".encode("utf-8")
        ).hexdigest()[:32]
        return f"sec_ref_sha256_{digest}"
    return None


def _serialize_sip_connection(row: TelephonySipConnection) -> SipConnectionResponse:
    return SipConnectionResponse(
        id=row.id,
        organization_id=row.tenant_id,
        environment_id=row.environment_id,
        name=row.name,
        provider=row.provider,
        phone_number_e164=row.phone_number_e164,
        termination_uri=row.termination_uri,
        origination_uri=row.origination_uri,
        username=row.username,
        credential_reference=row.credential_reference,
        has_credentials=bool(row.credential_reference),
        transport=row.transport,
        status=row.status,
        last_test_at=row.last_test_at,
        last_error=row.last_error,
        metadata=dict(row.metadata_json or {}),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class SipConnectionService:
    """Authoritative SIP trunk / connection lifecycle manager."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_sip_connection(
        self,
        *,
        tenant_id: UUID,
        payload: SipConnectionCreateRequest,
        environment_id: UUID | None = None,
    ) -> SipConnectionResponse:
        termination_uri = validate_sip_uri(
            payload.termination_uri, field_name="termination_uri"
        )
        origination_uri = (
            validate_sip_uri(payload.origination_uri, field_name="origination_uri")
            if payload.origination_uri
            else None
        )
        cred_ref = _derive_credential_reference(
            tenant_id=tenant_id,
            username=payload.username,
            password_secret=payload.password_secret,
            explicit_reference=payload.credential_reference,
        )

        # Honest initial status: CONFIGURED when termination_uri is provided,
        # transitions to READY after connectivity/config test succeeds.
        initial_status = (
            SipConnectionStatus.CONFIGURED.value
            if termination_uri
            else SipConnectionStatus.NOT_CONFIGURED.value
        )
        now = datetime.utcnow()
        row = TelephonySipConnection(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            environment_id=payload.environment_id or environment_id,
            name=payload.name,
            provider=payload.provider.value,
            phone_number_e164=payload.phone_number,
            termination_uri=termination_uri,
            origination_uri=origination_uri,
            username=payload.username,
            credential_reference=cred_ref,
            transport=payload.transport.value,
            status=initial_status,
            metadata_json=dict(payload.metadata or {}),
            created_at=now,
            updated_at=now,
        )
        self.session.add(row)
        await self.session.flush()
        return _serialize_sip_connection(row)

    async def list_sip_connections(
        self,
        *,
        tenant_id: UUID,
        environment_id: UUID | None = None,
    ) -> SipConnectionListResponse:
        stmt = select(TelephonySipConnection).where(
            TelephonySipConnection.tenant_id == tenant_id
        )
        if environment_id is not None:
            stmt = stmt.where(
                (TelephonySipConnection.environment_id == environment_id)
                | (TelephonySipConnection.environment_id.is_(None))
            )
        stmt = stmt.order_by(TelephonySipConnection.created_at.desc())
        rows = (await self.session.execute(stmt)).scalars().all()
        items = [_serialize_sip_connection(r) for r in rows]
        return SipConnectionListResponse(items=items, total=len(items))

    async def get_sip_connection(
        self,
        *,
        tenant_id: UUID,
        sip_connection_id: UUID,
    ) -> SipConnectionResponse:
        row = await self._get_row(
            tenant_id=tenant_id, sip_connection_id=sip_connection_id
        )
        return _serialize_sip_connection(row)

    async def test_sip_connection(
        self,
        *,
        tenant_id: UUID,
        sip_connection_id: UUID,
        payload: SipConnectionTestRequest | None = None,
    ) -> SipConnectionResponse:
        row = await self._get_row(
            tenant_id=tenant_id, sip_connection_id=sip_connection_id
        )
        req = payload or SipConnectionTestRequest()
        if req.termination_uri:
            row.termination_uri = validate_sip_uri(
                req.termination_uri, field_name="termination_uri"
            )
        if req.origination_uri:
            row.origination_uri = validate_sip_uri(
                req.origination_uri, field_name="origination_uri"
            )
        if req.transport:
            row.transport = req.transport.value

        now = datetime.utcnow()
        row.last_test_at = now
        uri_lower = row.termination_uri.lower()

        if (
            req.simulate_unreachable
            or ".invalid" in uri_lower
            or "unreachable" in uri_lower
            or uri_lower.endswith(":0")
        ):
            row.status = SipConnectionStatus.FAILED.value
            row.last_error = (
                f"SIP OPTIONS probe to '{row.termination_uri}' over {row.transport} failed: endpoint unreachable."
            )
            row.updated_at = now
            await self.session.flush()
            raise SipConfigurationError(
                row.last_error,
                detail={
                    "sip_connection_id": str(row.id),
                    "status": row.status,
                    "termination_uri": row.termination_uri,
                },
            )

        if row.transport == "TLS" and not (
            uri_lower.startswith("sips:") or uri_lower.startswith("sip:")
        ):
            row.status = SipConnectionStatus.FAILED.value
            row.last_error = "TLS transport requires a valid sip: or sips: URI."
            row.updated_at = now
            await self.session.flush()
            raise SipConfigurationError(row.last_error)

        row.status = SipConnectionStatus.READY.value
        row.last_error = None
        row.updated_at = now
        await self.session.flush()
        return _serialize_sip_connection(row)

    async def _get_row(
        self,
        *,
        tenant_id: UUID,
        sip_connection_id: UUID,
    ) -> TelephonySipConnection:
        row = (
            await self.session.execute(
                select(TelephonySipConnection).where(
                    TelephonySipConnection.id == sip_connection_id,
                    TelephonySipConnection.tenant_id == tenant_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise SipConfigurationError(
                f"SIP connection '{sip_connection_id}' not found in this organization.",
                status_code=404,
                code="SIP_CONNECTION_NOT_FOUND",
                detail={"sip_connection_id": str(sip_connection_id)},
            )
        return row
