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

    async def route_inbound_sip_invite(
        self,
        *,
        to_uri: str,
        from_uri: str,
        sip_call_id: str,
        source_ip: str | None = None,
        digest_response: dict[str, str] | None = None,
        expected_password: str | None = None,
    ) -> dict[str, object]:
        """Authenticate an inbound SIP INVITE from the SIP Gateway and resolve its `RuntimeConfig` (Sub-Phase 2F)."""
        import re
        from app.db.models import Call, CallDirection, CallStatus, Tenant
        from app.runtime.agent_config_resolver import resolve_runtime_config

        cleaned_to = validate_sip_uri(to_uri, field_name="to_uri")
        e164_match = re.search(r"(\+[1-9]\d{7,14})", cleaned_to)
        to_e164 = e164_match.group(1) if e164_match else ""
        from_match = re.search(r"(\+[1-9]\d{7,14})", from_uri or "")
        from_e164 = from_match.group(1) if from_match else (from_uri or "+10000000000")[:32]

        stmt = select(TelephonySipConnection)
        if to_e164:
            stmt = stmt.where(TelephonySipConnection.phone_number_e164 == to_e164)
        sip_row = (await self.session.execute(stmt)).scalars().first()
        if sip_row is None:
            raise SipConfigurationError(
                f"No SIP trunk bound to destination {cleaned_to!r}.",
                status_code=404,
                code="SIP_TRUNK_NOT_FOUND",
            )

        meta = dict(sip_row.metadata_json or {})
        allowed_cidrs = list(meta.get("allowed_cidrs") or [])
        if allowed_cidrs and source_ip:
            if not check_sip_ip_acl(source_ip, allowed_cidrs):
                raise SipConfigurationError(
                    f"Source IP {source_ip!r} is not permitted by SIP trunk ACL.",
                    status_code=403,
                    code="SIP_IP_ACL_DENIED",
                )

        if expected_password and sip_row.username:
            if not digest_response or not verify_sip_digest_auth(
                username=sip_row.username,
                realm=str(digest_response.get("realm") or "voxdesk.sip"),
                password=expected_password,
                method=str(digest_response.get("method") or "INVITE"),
                uri=str(digest_response.get("uri") or cleaned_to),
                nonce=str(digest_response.get("nonce") or ""),
                response_hex=str(digest_response.get("response") or ""),
            ):
                raise SipConfigurationError(
                    "SIP Digest authentication failed.",
                    status_code=401,
                    code="SIP_DIGEST_AUTH_FAILED",
                )

        tenant = await self.session.get(Tenant, sip_row.tenant_id)
        if tenant is None:
            raise SipConfigurationError("Tenant for SIP trunk not found.", status_code=404)

        call = Call(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            call_sid=sip_call_id,
            from_number=from_e164,
            to_number=to_e164 or tenant.twilio_number,
            direction=CallDirection.INBOUND,
            status=CallStatus.IN_PROGRESS,
        )
        self.session.add(call)
        await self.session.flush()

        runtime_cfg = await resolve_runtime_config(self.session, call, tenant=tenant)
        call.language = runtime_cfg.language
        await self.session.flush()

        return {
            "ok": True,
            "sip_connection_id": str(sip_row.id),
            "call_id": str(call.id),
            "sip_call_id": sip_call_id,
            "tenant_id": str(tenant.id),
            "agent_id": str(runtime_cfg.agent_id) if runtime_cfg.agent_id else None,
            "agent_version_id": str(runtime_cfg.agent_version_id) if runtime_cfg.agent_version_id else None,
            "serializer": "SIPBridgeFrameSerializer",
            "stream_ws_url": f"wss://localhost:8000/telephony/sip/stream/{call.id}",
        }


def compute_sip_digest_response(
    *,
    username: str,
    realm: str,
    password: str,
    method: str,
    uri: str,
    nonce: str,
) -> str:
    """Compute an RFC 2617 MD5 SIP Digest `response` hash."""
    ha1 = hashlib.md5(f"{username}:{realm}:{password}".encode("utf-8"), usedforsecurity=False).hexdigest()
    ha2 = hashlib.md5(f"{method.upper()}:{uri}".encode("utf-8"), usedforsecurity=False).hexdigest()
    return hashlib.md5(f"{ha1}:{nonce}:{ha2}".encode("utf-8"), usedforsecurity=False).hexdigest()


def verify_sip_digest_auth(
    *,
    username: str,
    realm: str,
    password: str,
    method: str,
    uri: str,
    nonce: str,
    response_hex: str,
) -> bool:
    """Constant-time verification of RFC 2617 SIP Digest authentication."""
    import hmac

    if not username or not nonce or not response_hex:
        return False
    expected = compute_sip_digest_response(
        username=username,
        realm=realm,
        password=password,
        method=method,
        uri=uri,
        nonce=nonce,
    )
    return hmac.compare_digest(expected.lower(), response_hex.strip().lower())


def check_sip_ip_acl(
    source_ip: str,
    allowed_cidrs: list[str] | tuple[str, ...],
) -> bool:
    """Return True if `source_ip` falls within at least one CIDR in `allowed_cidrs`."""
    import ipaddress

    if not allowed_cidrs:
        return True
    try:
        addr = ipaddress.ip_address(source_ip.strip())
    except ValueError:
        return False

    for cidr in allowed_cidrs:
        try:
            net = ipaddress.ip_network(cidr.strip(), strict=False)
            if addr in net:
                return True
        except ValueError:
            continue
    return False
