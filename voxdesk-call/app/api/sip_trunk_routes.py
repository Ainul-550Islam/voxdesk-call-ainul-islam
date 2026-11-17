"""SIP trunk API (PART 13, P13-01).

CRUD, a reachability test and a health roll-up, all tenant-scoped. The trunk
password is accepted on write and stored as a reference -- the API never returns
it, and `auth_secret_ref` is what leaves the process. A trunk that cannot be
dialled safely (plain RTP without the lab flag, no credential reference) is
rejected at write time rather than at 3 a.m. during a campaign.
"""

from __future__ import annotations

import hmac
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.carriers_v2.settings import get_carrier_v2_settings
from app.db.session import get_session
from app.db.telephony_models import SipTrunk
from app.security.secret_store import SecretStoreError, get_secret_store
from app.telephony.sip.connection import SipConnectionService
from app.telephony.sip.sip_gateway_client import build_gateway_client
from app.telephony.exceptions import SipConfigurationError
from app.telephony.sip_outbound import (
    TRANSPORTS,
    SipOriginator,
    TrunkConfig,
    TrunkConfigError,
)

router = APIRouter(tags=["telephony-trunks"])

# Purpose is part of the secret-store AAD, so a reference created for another
# integration or tenant cannot be reused as a SIP digest credential.
SIP_TRUNK_SECRET_PURPOSE = "sip_trunk_auth"


def _validate_secret_reference(value: str | None) -> str | None:
    if value is None:
        return None
    reference = value.strip()
    if not reference.startswith("secret://") or any(character.isspace() for character in reference):
        raise ValueError("auth_secret_ref must be an opaque secret:// reference")
    return reference


class TrunkCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=5061, ge=1, le=65535)
    transport: str = "tls"
    srtp_required: bool = True
    auth_username: str | None = None
    auth_secret_ref: str | None = None
    # Write-only over the authenticated HTTPS control plane. The API seals
    # this immediately and never stores or serializes the plaintext value.
    auth_password: SecretStr | None = Field(default=None, repr=False)
    caller_id: str | None = None
    egress_address: str | None = None
    allow_plain_rtp: bool = False

    @field_validator("auth_secret_ref")
    @classmethod
    def _auth_secret_ref_is_opaque(cls, value: str | None) -> str | None:
        return _validate_secret_reference(value)

    @field_validator("auth_password")
    @classmethod
    def _auth_password_is_bounded(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            raw = value.get_secret_value()
            if not raw or len(raw) > 4096:
                raise ValueError("auth_password must contain 1 to 4096 characters")
        return value

    @model_validator(mode="after")
    def _only_one_credential_input(self) -> "TrunkCreate":
        if self.auth_password is not None and self.auth_secret_ref is not None:
            raise ValueError("provide auth_password or auth_secret_ref, not both")
        return self


class TrunkUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    host: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    transport: str | None = None
    srtp_required: bool | None = None
    auth_username: str | None = None
    auth_secret_ref: str | None = None
    auth_password: SecretStr | None = Field(default=None, repr=False)
    caller_id: str | None = None
    egress_address: str | None = None

    @field_validator("auth_secret_ref")
    @classmethod
    def _auth_secret_ref_is_opaque(cls, value: str | None) -> str | None:
        return _validate_secret_reference(value)

    @field_validator("auth_password")
    @classmethod
    def _auth_password_is_bounded(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            raw = value.get_secret_value()
            if not raw or len(raw) > 4096:
                raise ValueError("auth_password must contain 1 to 4096 characters")
        return value

    @model_validator(mode="after")
    def _only_one_credential_input(self) -> "TrunkUpdate":
        if self.auth_password is not None and self.auth_secret_ref is not None:
            raise ValueError("provide auth_password or auth_secret_ref, not both")
        return self


def _serialize(trunk: SipTrunk) -> dict:
    return {
        "id": str(trunk.id),
        "name": trunk.name,
        "host": trunk.host,
        "port": trunk.port,
        "transport": trunk.transport,
        "srtp_required": trunk.srtp_required,
        "auth_username": trunk.auth_username,
        # The reference is safe to show; the secret behind it never leaves the
        # secret store.
        "auth_secret_ref": trunk.auth_secret_ref,
        "caller_id": trunk.caller_id,
        "egress_address": trunk.egress_address,
        "status": trunk.status,
        "last_test_at": trunk.last_test_at.isoformat() if trunk.last_test_at else None,
        "last_test_ms": trunk.last_test_ms,
        "last_error": trunk.last_error,
    }


def _config(
    payload: TrunkCreate | TrunkUpdate,
    trunk: SipTrunk | None = None,
    *,
    auth_secret_ref: str | None = None,
) -> TrunkConfig:
    allow_plain = bool(getattr(payload, "allow_plain_rtp", False))
    if trunk is None:
        return TrunkConfig(
            host=payload.host or "",
            port=int(payload.port or 5061),
            transport=(payload.transport or "tls").lower(),
            srtp_required=bool(payload.srtp_required),
            caller_id=payload.caller_id,
            auth_username=payload.auth_username,
            auth_secret_ref=auth_secret_ref or payload.auth_secret_ref,
            egress_address=payload.egress_address,
            allow_plain_rtp=allow_plain,
        )
    merged = {
        field: getattr(trunk, field)
        for field in (
            "host",
            "port",
            "transport",
            "srtp_required",
            "caller_id",
            "auth_username",
            "auth_secret_ref",
            "egress_address",
        )
    }
    for field in merged:
        value = getattr(payload, field, None)
        if value is not None:
            merged[field] = value
    if auth_secret_ref is not None:
        merged["auth_secret_ref"] = auth_secret_ref
    merged["transport"] = str(merged["transport"]).lower()
    return TrunkConfig(**merged, allow_plain_rtp=allow_plain)


async def _load(session: AsyncSession, tenant_id: uuid.UUID, trunk_id: uuid.UUID) -> SipTrunk:
    trunk = (
        await session.execute(
            select(SipTrunk).where(SipTrunk.id == trunk_id, SipTrunk.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if trunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="trunk not found")
    return trunk


def _auth_secret_reference(
    tenant_id: uuid.UUID,
    *,
    password: SecretStr | None = None,
    reference: str | None = None,
    existing_reference: str | None = None,
) -> str | None:
    """Seal a newly supplied password or verify an existing opaque reference."""
    store = get_secret_store()
    if password is not None:
        try:
            return store.put(str(tenant_id), SIP_TRUNK_SECRET_PURPOSE, password.get_secret_value())
        except SecretStoreError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="SIP trunk secret storage is unavailable",
            ) from exc
    if reference is not None:
        try:
            # Verify tenant/purpose binding at write time. The value is discarded
            # immediately and is never included in logs or public responses.
            store.get(str(tenant_id), SIP_TRUNK_SECRET_PURPOSE, reference)
        except SecretStoreError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "field": "auth_secret_ref",
                    "message": "secret reference is not valid for this trunk",
                },
            ) from exc
        return reference
    return existing_reference


def _require_gateway_auth(request: Request) -> None:
    expected = (get_carrier_v2_settings().sip_gateway_token or "").strip()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SIP gateway runtime configuration is not configured",
        )
    scheme, separator, presented = request.headers.get("Authorization", "").partition(" ")
    presented = presented.strip()
    if (
        not separator
        or scheme.lower() != "bearer"
        or not presented
        or not hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8"))
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        )


@router.get("/internal/telephony/sip-trunk-runtime-config")
async def sip_trunk_runtime_config(
    request: Request,
    response: Response,
    tenant_id: uuid.UUID,
    trunk_id: uuid.UUID,
    environment_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Return one resolved trunk profile to the authenticated SIP gateway only."""
    _require_gateway_auth(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    trunk = await _load(session, tenant_id, trunk_id)
    if trunk.environment_id is not None and trunk.environment_id != environment_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="trunk not found")
    try:
        config = TrunkConfig.from_row(trunk)
        config.validate()
    except TrunkConfigError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SIP trunk runtime configuration is incomplete",
        ) from exc
    if not trunk.auth_username or not trunk.auth_secret_ref:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SIP trunk runtime credentials are incomplete",
        )
    try:
        password = get_secret_store().get(
            str(tenant_id), SIP_TRUNK_SECRET_PURPOSE, trunk.auth_secret_ref
        )
    except SecretStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SIP trunk runtime credentials are unavailable",
        ) from exc
    if not password:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SIP trunk runtime credentials are unavailable",
        )
    return {
        "tenant_id": str(tenant_id),
        "trunk_id": str(trunk_id),
        "environment_id": str(trunk.environment_id) if trunk.environment_id else None,
        "host": trunk.host,
        "port": int(trunk.port),
        "transport": str(trunk.transport).lower(),
        "srtp_required": bool(trunk.srtp_required),
        "username": trunk.auth_username,
        "password": password,
        "caller_id": trunk.caller_id,
        "egress_address": trunk.egress_address,
    }


class InboundSIPRouteRequest(BaseModel):
    """SIP identity supplied by the gateway for one inbound route lookup."""

    model_config = ConfigDict(extra="forbid")
    to_uri: str = Field(min_length=1, max_length=512)
    from_uri: str = Field(min_length=1, max_length=512)
    call_id: str = Field(min_length=1, max_length=64)
    source_ip: str = Field(min_length=1, max_length=64)

    @field_validator("call_id")
    @classmethod
    def _call_id_is_header_safe(cls, value: str) -> str:
        if any(ord(character) < 33 or ord(character) > 126 for character in value):
            raise ValueError("call_id must be a visible SIP Call-ID token")
        return value


@router.post("/internal/telephony/sip-inbound-route")
async def sip_inbound_route(
    payload: InboundSIPRouteRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Resolve and authorize one inbound INVITE for the SIP gateway.

    The bearer-authenticated lookup has no write side effects. The gateway
    uses the returned tenant/trunk scope before sending SIP 200; its media
    WebSocket repeats the same route validation before creating call history.
    """
    _require_gateway_auth(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    try:
        return await SipConnectionService(session).resolve_inbound_sip_route(
            to_uri=payload.to_uri,
            from_uri=payload.from_uri,
            sip_call_id=payload.call_id,
            source_ip=payload.source_ip,
        )
    except SipConfigurationError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc


@router.get("/api/telephony/trunks")
async def list_trunks(
    ctx: TenantContext = Depends(require_permission(Permission.TELEPHONY_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    rows = (
        (
            await session.execute(
                select(SipTrunk).where(SipTrunk.tenant_id == ctx.tenant_id).order_by(SipTrunk.name)
            )
        )
        .scalars()
        .all()
    )
    return {"trunks": [_serialize(row) for row in rows], "transports": list(TRANSPORTS)}


@router.post("/api/telephony/trunks", status_code=status.HTTP_201_CREATED)
async def create_trunk(
    payload: TrunkCreate,
    ctx: TenantContext = Depends(require_permission(Permission.TELEPHONY_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    if payload.transport.lower() not in TRANSPORTS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"transport must be one of {', '.join(TRANSPORTS)}",
        )
    validation_ref = payload.auth_secret_ref or (
        "secret://pending-write" if payload.auth_password is not None else None
    )
    try:
        _config(payload, auth_secret_ref=validation_ref).validate()
    except TrunkConfigError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"field": exc.field, "message": str(exc)},
        ) from exc

    exists = (
        await session.execute(
            select(SipTrunk).where(
                SipTrunk.tenant_id == ctx.tenant_id, SipTrunk.name == payload.name
            )
        )
    ).scalar_one_or_none()
    if exists is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="a trunk with that name exists"
        )

    secret_ref = _auth_secret_reference(
        ctx.tenant_id,
        password=payload.auth_password,
        reference=payload.auth_secret_ref,
    )
    if not secret_ref:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"field": "auth_password", "message": "a trunk credential is required"},
        )

    trunk = SipTrunk(
        tenant_id=ctx.tenant_id,
        name=payload.name,
        host=payload.host,
        port=payload.port,
        transport=payload.transport.lower(),
        srtp_required=payload.srtp_required,
        auth_username=payload.auth_username,
        auth_secret_ref=secret_ref,
        caller_id=payload.caller_id,
        egress_address=payload.egress_address,
        status="CONFIGURED",
        metadata_json={"allow_plain_rtp": payload.allow_plain_rtp},
    )
    session.add(trunk)
    await session.commit()
    return _serialize(trunk)


@router.patch("/api/telephony/trunks/{trunk_id}")
async def update_trunk(
    trunk_id: uuid.UUID,
    payload: TrunkUpdate,
    ctx: TenantContext = Depends(require_permission(Permission.TELEPHONY_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    trunk = await _load(session, ctx.tenant_id, trunk_id)
    validation_ref = payload.auth_secret_ref or (
        "secret://pending-write" if payload.auth_password is not None else trunk.auth_secret_ref
    )
    try:
        _config(payload, trunk, auth_secret_ref=validation_ref).validate()
    except TrunkConfigError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"field": exc.field, "message": str(exc)},
        ) from exc
    secret_ref = _auth_secret_reference(
        ctx.tenant_id,
        password=payload.auth_password,
        reference=payload.auth_secret_ref,
        existing_reference=trunk.auth_secret_ref,
    )
    if not secret_ref:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"field": "auth_password", "message": "a trunk credential is required"},
        )
    for field in (
        "name",
        "host",
        "port",
        "transport",
        "srtp_required",
        "auth_username",
        "caller_id",
        "egress_address",
    ):
        value = getattr(payload, field, None)
        if value is not None:
            setattr(trunk, field, value.lower() if field == "transport" else value)
    trunk.auth_secret_ref = secret_ref
    trunk.updated_at = datetime.now(timezone.utc)
    await session.commit()
    return _serialize(trunk)


@router.delete("/api/telephony/trunks/{trunk_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_trunk(
    trunk_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TELEPHONY_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> None:
    trunk = await _load(session, ctx.tenant_id, trunk_id)
    await session.delete(trunk)
    await session.commit()


@router.post("/api/telephony/trunks/{trunk_id}/test")
async def test_trunk(
    trunk_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TELEPHONY_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Check gateway health only; no SIP OPTIONS request reaches the carrier."""
    trunk = await _load(session, ctx.tenant_id, trunk_id)
    originator = SipOriginator(build_gateway_client())
    try:
        result = await originator.ping(TrunkConfig.from_row(trunk))
    except TrunkConfigError as exc:
        trunk.status = "NOT_CONFIGURED"
        trunk.last_error = str(exc)
        trunk.last_test_at = datetime.now(timezone.utc)
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"field": exc.field, "message": str(exc)},
        ) from exc
    trunk.last_test_at = datetime.now(timezone.utc)
    # This path probes only the machine-wide gateway health API. It cannot
    # mark a tenant trunk READY because it does not send SIP OPTIONS to that
    # trunk or exercise its credentials/media policy.
    trunk.status = "GATEWAY_REACHABLE" if result["ok"] else "UNREACHABLE"
    trunk.last_error = (
        None if result["ok"] else str(result.get("reason") or result.get("detail"))[:400]
    )
    await session.commit()
    return {
        "ok": result["ok"],
        "probe_scope": result.get("probe_scope", "gateway_health_only"),
        "trunk_reached": False,
        "trunk": _serialize(trunk),
    }


@router.get("/api/telephony/trunks/health")
async def trunks_health(
    ctx: TenantContext = Depends(require_permission(Permission.TELEPHONY_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    rows = (
        (await session.execute(select(SipTrunk).where(SipTrunk.tenant_id == ctx.tenant_id)))
        .scalars()
        .all()
    )
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1
    ready = counts.get("READY", 0)
    return {
        "total": len(rows),
        "ready": ready,
        "degraded": len(rows) - ready,
        "by_status": counts,
        "ok": bool(rows) and ready == len(rows),
    }
