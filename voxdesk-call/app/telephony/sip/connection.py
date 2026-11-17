"""
app/telephony/sip/connection.py
Durable SIP connection / trunk configuration, URI & transport validation,
secret-reference hashing (never storing plaintext passwords), and connection verification.

This module used to live at ``app/telephony/sip.py``. It became
``app/telephony/sip/connection.py`` when Batch 02 added the live SIP media
package next to it; the public names are re-exported from
``app.telephony.sip`` so every existing import keeps working.
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
import uuid
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# ``app.db.models`` is imported before ``app.db.telephony_models`` on
# purpose: the ORM base lives in ``app.db.models``, which imports the
# telephony tables at the end of its own module. Loading the models module
# first keeps that cycle closed no matter which module is imported first.
from app.db import models as _db_models  # noqa: F401
from app.db.telephony_models import TelephonyPhoneNumber, TelephonySipConnection
from app.telephony.enums import SipConnectionStatus
from app.telephony.exceptions import SipConfigurationError
from app.telephony.schemas import (
    SipConnectionCreateRequest,
    SipConnectionListResponse,
    SipConnectionResponse,
    SipConnectionTestRequest,
    validate_sip_uri,
)


_E164_SIP_USER = re.compile(r"\+[1-9]\d{7,14}\Z")


def _sip_header_uri_and_number(value: str, *, field_name: str) -> tuple[str, str]:
    """Return a validated SIP URI and its E.164 user part.

    SIP headers can include a display name, URI brackets, URI parameters, and
    header parameters such as ``tag``. Only the URI user is ever used for DID
    routing; a matching number in a hostname or an arbitrary parameter is not
    accepted as the called number.
    """
    raw = str(value or "").strip()
    if not raw:
        raise SipConfigurationError(
            f"SIP URI '{field_name}' cannot be empty",
            status_code=400,
            code="SIP_URI_INVALID",
        )
    if "<" in raw:
        left = raw.find("<")
        right = raw.find(">", left + 1)
        if right < 0:
            raise SipConfigurationError(
                f"SIP URI '{field_name}' is malformed",
                status_code=400,
                code="SIP_URI_INVALID",
            )
        raw = raw[left + 1 : right].strip()
    else:
        # Bare name-addr values may carry a header parameter after the URI.
        raw = raw.split(";", 1)[0].strip()
    cleaned = validate_sip_uri(raw, field_name=field_name)
    payload = cleaned.split(":", 1)[1].split(";", 1)[0].split("?", 1)[0]
    userinfo = payload.rsplit("@", 1)[0] if "@" in payload else payload
    user = userinfo.split(":", 1)[0]
    if not _E164_SIP_USER.fullmatch(user):
        raise SipConfigurationError(
            f"SIP URI '{field_name}' must use an E.164 user part",
            status_code=400,
            code="SIP_URI_NUMBER_INVALID",
        )
    return cleaned, user


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
        termination_uri = validate_sip_uri(payload.termination_uri, field_name="termination_uri")
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
        now = datetime.now(timezone.utc)
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
        stmt = select(TelephonySipConnection).where(TelephonySipConnection.tenant_id == tenant_id)
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
        row = await self._get_row(tenant_id=tenant_id, sip_connection_id=sip_connection_id)
        return _serialize_sip_connection(row)

    async def test_sip_connection(
        self,
        *,
        tenant_id: UUID,
        sip_connection_id: UUID,
        payload: SipConnectionTestRequest | None = None,
    ) -> SipConnectionResponse:
        row = await self._get_row(tenant_id=tenant_id, sip_connection_id=sip_connection_id)
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

        now = datetime.now(timezone.utc)
        row.last_test_at = now
        uri_lower = row.termination_uri.lower()

        if (
            req.simulate_unreachable
            or ".invalid" in uri_lower
            or "unreachable" in uri_lower
            or uri_lower.endswith(":0")
        ):
            row.status = SipConnectionStatus.FAILED.value
            row.last_error = f"SIP OPTIONS probe to '{row.termination_uri}' over {row.transport} failed: endpoint unreachable."
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

        from app.telephony.sip.sip_gateway_client import SipGatewayClient

        if not SipGatewayClient.supports_tenant_trunks:
            row.status = SipConnectionStatus.CONFIGURED.value
            row.last_error = (
                "The gateway does not provide verified tenant-scoped TLS/SRTP routing; "
                "SIP trunk readiness cannot be confirmed."
            )
            row.updated_at = now
            await self.session.flush()
            return _serialize_sip_connection(row)

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

    async def resolve_inbound_sip_route(
        self,
        *,
        to_uri: str,
        from_uri: str,
        sip_call_id: str,
        source_ip: str,
    ) -> dict[str, object]:
        """Resolve a call to exactly one ready tenant SIP connection and DID.

        This method is intentionally side-effect free: the gateway calls it
        before sending a successful SIP response, and the authenticated media
        socket repeats the lookup before creating the durable call row. Source
        authorization is by an explicit per-connection CIDR allow-list; an
        empty or malformed list never means "allow everyone" here.
        """
        from app.db.models import Environment, Tenant
        from app.telephony.sip.sip_gateway_client import SipGatewayClient

        call_id = str(sip_call_id or "").strip()
        if (
            not call_id
            or len(call_id) > 64
            or any(not 33 <= ord(character) <= 126 for character in call_id)
        ):
            raise SipConfigurationError(
                "SIP Call-ID is missing or malformed",
                status_code=400,
                code="SIP_CALL_ID_INVALID",
            )
        if not SipGatewayClient.supports_tenant_trunks:
            raise SipConfigurationError(
                "Inbound SIP trunk routing is disabled until tenant-scoped TLS/SRTP support is verified.",
                status_code=503,
                code="SIP_TENANT_TRUNK_UNSUPPORTED",
            )
        try:
            source = ipaddress.ip_address(str(source_ip or "").strip())
        except ValueError as exc:
            raise SipConfigurationError(
                "A valid SIP peer source address is required for inbound routing",
                status_code=403,
                code="SIP_IP_ACL_DENIED",
            ) from exc

        _, to_number = _sip_header_uri_and_number(to_uri, field_name="to_uri")
        try:
            _, from_number = _sip_header_uri_and_number(from_uri, field_name="from_uri")
        except SipConfigurationError:
            # Private/anonymous calling-party identities are valid SIP. Keep a
            # bounded, non-empty value for the existing call-history schema.
            from_number = "+10000000000"

        connections = (
            (
                await self.session.execute(
                    select(TelephonySipConnection).where(
                        TelephonySipConnection.phone_number_e164 == to_number
                    )
                )
            )
            .scalars()
            .all()
        )
        ready = [
            row
            for row in connections
            if str(row.provider).lower() == "sip"
            and str(row.status).upper() == SipConnectionStatus.READY.value
        ]
        if not ready:
            if connections:
                raise SipConfigurationError(
                    "The SIP connection for this destination is not ready",
                    status_code=503,
                    code="SIP_TRUNK_NOT_READY",
                )
            raise SipConfigurationError(
                "No SIP connection is bound to this destination",
                status_code=404,
                code="SIP_TRUNK_NOT_FOUND",
            )
        if len(ready) != 1:
            raise SipConfigurationError(
                "The destination has more than one ready SIP connection",
                status_code=409,
                code="SIP_ROUTE_AMBIGUOUS",
            )
        trunk = ready[0]

        phone_numbers = (
            (
                await self.session.execute(
                    select(TelephonyPhoneNumber).where(
                        TelephonyPhoneNumber.e164_number == to_number,
                        TelephonyPhoneNumber.provider == "sip",
                        TelephonyPhoneNumber.sip_enabled.is_(True),
                        TelephonyPhoneNumber.inbound_enabled.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )
        active_numbers = [
            row for row in phone_numbers if str(row.status).upper() in {"READY", "ACTIVE"}
        ]
        if len(active_numbers) != 1:
            if len(active_numbers) > 1:
                raise SipConfigurationError(
                    "The SIP destination is assigned to more than one active phone number",
                    status_code=409,
                    code="SIP_ROUTE_AMBIGUOUS",
                )
            raise SipConfigurationError(
                "The SIP destination is not enabled for inbound calls",
                status_code=404,
                code="SIP_PHONE_NUMBER_NOT_FOUND",
            )
        phone_number = active_numbers[0]
        if (
            phone_number.tenant_id != trunk.tenant_id
            or phone_number.sip_connection_id != trunk.id
            or (
                phone_number.environment_id is not None
                and trunk.environment_id is not None
                and phone_number.environment_id != trunk.environment_id
            )
        ):
            raise SipConfigurationError(
                "The SIP destination and connection do not share the same tenant scope",
                status_code=404,
                code="SIP_ROUTE_SCOPE_MISMATCH",
            )

        tenant = await self.session.get(Tenant, trunk.tenant_id)
        if tenant is None or not tenant.is_active:
            raise SipConfigurationError(
                "The tenant for this SIP destination is unavailable",
                status_code=404,
                code="SIP_TENANT_NOT_FOUND",
            )

        metadata = dict(trunk.metadata_json or {})
        configured_cidrs = metadata.get("allowed_cidrs")
        if not isinstance(configured_cidrs, (list, tuple)) or not configured_cidrs:
            raise SipConfigurationError(
                "An inbound SIP source allow-list is required",
                status_code=403,
                code="SIP_SOURCE_ACL_REQUIRED",
            )
        networks = []
        for raw_cidr in configured_cidrs:
            if not isinstance(raw_cidr, str) or not raw_cidr.strip():
                raise SipConfigurationError(
                    "The inbound SIP source allow-list is invalid",
                    status_code=503,
                    code="SIP_SOURCE_ACL_INVALID",
                )
            try:
                networks.append(ipaddress.ip_network(raw_cidr.strip(), strict=False))
            except ValueError as exc:
                raise SipConfigurationError(
                    "The inbound SIP source allow-list is invalid",
                    status_code=503,
                    code="SIP_SOURCE_ACL_INVALID",
                ) from exc
        if not any(source in network for network in networks):
            raise SipConfigurationError(
                "The inbound SIP source is not permitted by this connection",
                status_code=403,
                code="SIP_IP_ACL_DENIED",
            )

        environment_id = phone_number.environment_id or trunk.environment_id
        if environment_id is None:
            environment = (
                (
                    await self.session.execute(
                        select(Environment)
                        .where(
                            Environment.tenant_id == tenant.id,
                            Environment.kind == "production",
                        )
                        .order_by(Environment.is_default.desc())
                    )
                )
                .scalars()
                .first()
            )
        else:
            environment = await self.session.get(Environment, environment_id)
        if (
            environment is None
            or environment.tenant_id != tenant.id
            or str(environment.status).lower() in {"suspended", "archived"}
        ):
            raise SipConfigurationError(
                "The SIP route has no active environment in this tenant",
                status_code=503,
                code="SIP_ENVIRONMENT_UNAVAILABLE",
            )
        environment_id = environment.id

        return {
            "ok": True,
            "call_id": call_id,
            "sip_call_id": call_id,
            "tenant_id": str(tenant.id),
            "trunk_id": str(trunk.id),
            "environment_id": str(environment_id),
            "phone_number_id": str(phone_number.id),
            "from_number": from_number,
            "to_number": to_number,
        }

    async def route_inbound_sip_invite(
        self,
        *,
        to_uri: str,
        from_uri: str,
        sip_call_id: str,
        source_ip: str,
        digest_response: dict[str, str] | None = None,
        expected_password: str | None = None,
    ) -> dict[str, object]:
        """Resolve and bind a SIP INVITE to its tenant before starting media."""
        from app.db.models import Call, CallDirection, CallStatus, Tenant
        from app.runtime.agent_config_resolver import resolve_runtime_config

        route = await self.resolve_inbound_sip_route(
            to_uri=to_uri,
            from_uri=from_uri,
            sip_call_id=sip_call_id,
            source_ip=source_ip,
        )
        trunk_id = UUID(str(route["trunk_id"]))
        sip_row = await self.session.get(TelephonySipConnection, trunk_id)
        if sip_row is None or str(sip_row.tenant_id) != str(route["tenant_id"]):
            raise SipConfigurationError(
                "The authorized SIP connection is no longer available",
                status_code=404,
                code="SIP_TRUNK_NOT_FOUND",
            )

        if expected_password and sip_row.username:
            digest_uri = str((digest_response or {}).get("uri") or to_uri)
            if not digest_response or not verify_sip_digest_auth(
                username=sip_row.username,
                realm=str(digest_response.get("realm") or "voxdesk.sip"),
                password=expected_password,
                method=str(digest_response.get("method") or "INVITE"),
                uri=digest_uri,
                nonce=str(digest_response.get("nonce") or ""),
                response_hex=str(digest_response.get("response") or ""),
            ):
                raise SipConfigurationError(
                    "SIP Digest authentication failed.",
                    status_code=401,
                    code="SIP_DIGEST_AUTH_FAILED",
                )

        tenant = await self.session.get(Tenant, UUID(str(route["tenant_id"])))
        if tenant is None or not tenant.is_active:
            raise SipConfigurationError(
                "The tenant for this SIP destination is unavailable",
                status_code=404,
                code="SIP_TENANT_NOT_FOUND",
            )
        call_values = {
            "id": uuid.uuid4(),
            "tenant_id": tenant.id,
            "call_sid": str(route["call_id"]),
            "from_number": str(route["from_number"]),
            "to_number": str(route["to_number"]),
            "direction": CallDirection.INBOUND,
            "status": CallStatus.IN_PROGRESS,
            "environment_id": UUID(str(route["environment_id"])),
        }
        call = Call(**call_values)
        self.session.add(call)
        await self.session.flush()

        runtime_cfg = await resolve_runtime_config(self.session, call, tenant=tenant)
        call.language = runtime_cfg.language
        await self.session.flush()

        return {
            "ok": True,
            "sip_connection_id": str(sip_row.id),
            "trunk_id": str(sip_row.id),
            "call_id": str(call.id),
            "sip_call_id": str(route["call_id"]),
            "tenant_id": str(tenant.id),
            "environment_id": route.get("environment_id"),
            "agent_id": str(runtime_cfg.agent_id) if runtime_cfg.agent_id else None,
            "agent_version_id": str(runtime_cfg.agent_version_id)
            if runtime_cfg.agent_version_id
            else None,
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
    ha1 = hashlib.md5(
        f"{username}:{realm}:{password}".encode("utf-8"), usedforsecurity=False
    ).hexdigest()
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
