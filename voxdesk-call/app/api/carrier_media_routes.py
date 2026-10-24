"""
app/api/carrier_media_routes.py
Admin + tenant-scoped API for the Batch 02 carrier surfaces.

Endpoints:

* ``GET  /api/v1/carriers/capabilities``          -- what is really usable.
* ``POST /api/v1/carriers/{carrier}/test-connection`` -- a real credential check.
* ``GET  /api/v1/s2s/sessions``                    -- the tenant's S2S sessions.
* ``GET  /api/v1/s2s/sessions/{session_id}``        -- one S2S session.

Every route is tenant-scoped, RBAC-checked, audited, and the mutating route
is idempotent: the same ``Idempotency-Key`` never probes a carrier twice
and never records two checks.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import app.db.models  # noqa: F401 - ensure the ORM registry is initialized
from app.audit.service import record_event
from app.auth.dependencies import Permission, TenantContext, require_permission
from app.db.carrier_s2s_models import CarrierConnectionCheck, S2SSessionRecord
from app.db.session import get_session
from app.resilience.idempotency import (
    IdempotencyConflict,
    IdempotencyInProgress,
    IdempotencyPreviousFailure,
    claim_request,
    complete_request,
)
from app.core.logging import log

router = APIRouter(prefix="/api/v1", tags=["Carrier Media"])

#: The carriers this API knows about.
CARRIERS = ("vonage", "sip", "s2s")

MAX_IDEMPOTENCY_KEY_LENGTH = 128
MIN_IDEMPOTENCY_KEY_LENGTH = 8

_STATUS_OK = "ok"
_STATUS_FAILED = "failed"
_STATUS_NOT_CONFIGURED = "not_configured"


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class CarrierCapabilityRow(BaseModel):
    """One carrier's honest capability row."""

    carrier: str
    supported: bool = False
    status: str = _STATUS_NOT_CONFIGURED
    reason: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)


class CarrierCapabilitiesResponse(BaseModel):
    carriers: list[CarrierCapabilityRow]


class CarrierConnectionTestRequest(BaseModel):
    """Optional body of a connection test. Empty is a valid request."""

    metadata: dict[str, Any] = Field(default_factory=dict)


class CarrierConnectionCheckResponse(BaseModel):
    id: str
    carrier: str
    status: str
    ok: bool
    reason: str | None = None
    detail: str | None = None
    checked_at: datetime


class S2SSessionResponse(BaseModel):
    id: str
    provider: str
    model: str
    status: str
    call_id: str | None = None
    failover_reason: str | None = None
    error: str | None = None
    started_at: datetime
    ended_at: datetime | None = None
    duration_ms: int
    input_audio_s: float
    output_audio_s: float
    input_tokens: int
    output_tokens: int
    cost_micro_usd: int
    cost_known: bool


class S2SSessionListResponse(BaseModel):
    sessions: list[S2SSessionResponse]
    total: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _carrier_settings():
    from app.carriers_v2.settings import get_carrier_v2_settings

    return get_carrier_v2_settings()


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _http_get(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 8.0,
) -> tuple[int, dict[str, Any]]:
    """A GET that returns ``(status, body)``; never raises for HTTP errors."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(url, headers=headers or {})
    try:
        body = response.json()
    except ValueError:
        body = {"text": response.text[:200]}
    if not isinstance(body, dict):
        body = {"value": body}
    return response.status_code, body


def _digest(key: str) -> str:
    return hashlib.sha256(key.strip().encode("utf-8")).hexdigest()


async def _audit(
    session: AsyncSession,
    ctx: TenantContext,
    *,
    event_type: str,
    result: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
    detail: dict[str, Any] | None = None,
    environment_id: uuid.UUID | None = None,
) -> None:
    """Stage one audit row in the caller's transaction."""
    await record_event(
        session,
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.user_id,
        event_type=event_type,
        environment_id=environment_id,
        resource_type=resource_type,
        resource_id=resource_id,
        result=result,
        detail=detail or {},
    )


# ---------------------------------------------------------------------------
# Capability + connection checks
# ---------------------------------------------------------------------------
async def _check_vonage() -> tuple[str, str, str]:
    """A real Vonage credential check. Returns ``(status, reason, detail)``."""
    carrier = _carrier_settings()
    if not carrier.vonage_media_ready():
        return (
            _STATUS_NOT_CONFIGURED,
            "vonage_media_credentials_missing",
            "VONAGE_API_KEY, VONAGE_API_SECRET, VONAGE_APPLICATION_ID, "
            "VONAGE_PRIVATE_KEY_PATH and VONAGE_SIGNATURE_SECRET are required.",
        )
    url = f"{carrier.vonage_api_base.rstrip('/')}/account/get-balance"
    auth = httpx.BasicAuth(
        carrier.vonage_api_key.strip(), carrier.vonage_api_secret.strip()
    )
    try:
        async with httpx.AsyncClient(timeout=carrier.vonage_http_timeout_seconds) as client:
            response = await client.get(url, auth=auth)
    except httpx.HTTPError as exc:
        return (
            _STATUS_FAILED,
            "vonage_unreachable",
            f"The Vonage account API could not be reached: {type(exc).__name__}.",
        )
    if response.status_code in (401, 403):
        return (
            _STATUS_FAILED,
            "vonage_credentials_rejected",
            "The Vonage account API rejected the API key and secret.",
        )
    if response.status_code >= 400:
        return (
            _STATUS_FAILED,
            "vonage_error_response",
            f"The Vonage account API answered with HTTP {response.status_code}.",
        )
    return (_STATUS_OK, "vonage_credentials_valid", "The Vonage credentials were accepted.")


async def _check_sip() -> tuple[str, str, str]:
    """A real SIP gateway readiness probe."""
    from app.telephony.sip.sip_capabilities import resolve_sip_capability

    capability = await resolve_sip_capability()
    if capability.ready:
        return (_STATUS_OK, capability.reason, "The SIP gateway reports itself ready.")
    if capability.reason == "sip_gateway_not_configured":
        return (
            _STATUS_NOT_CONFIGURED,
            capability.reason,
            "SIP_GATEWAY_URL and SIP_GATEWAY_TOKEN are required.",
        )
    return (_STATUS_FAILED, capability.reason, "The SIP gateway did not report itself ready.")


async def _check_s2s() -> tuple[str, str, str]:
    """A real speech-to-speech provider credential check."""
    from app.core.config import settings as core_settings

    carrier = _carrier_settings()
    openai_key = (getattr(core_settings, "openai_api_key", "") or "").strip()
    google_key = (getattr(core_settings, "google_api_key", "") or "").strip()
    if not carrier.s2s_enabled:
        return (
            _STATUS_NOT_CONFIGURED,
            "s2s_disabled",
            "ENABLE_S2S is not set, so the speech-to-speech runtime is off.",
        )
    if not openai_key and not google_key:
        return (
            _STATUS_NOT_CONFIGURED,
            "s2s_provider_credentials_missing",
            "Set OPENAI_API_KEY or GOOGLE_API_KEY to enable a speech-to-speech provider.",
        )
    if openai_key:
        status, body = await _http_get(
            "https://api.openai.com/v1/models?limit=1",
            headers={"Authorization": f"Bearer {openai_key}"},
            timeout=8.0,
        )
        if status in (401, 403):
            return (
                _STATUS_FAILED,
                "s2s_credentials_rejected",
                "The OpenAI API rejected the credential.",
            )
        if status >= 400:
            return (
                _STATUS_FAILED,
                "s2s_provider_error",
                f"The OpenAI API answered with HTTP {status}.",
            )
        return (
            _STATUS_OK,
            "s2s_credentials_valid",
            f"The OpenAI credential was accepted for model {carrier.openai_realtime_model}.",
        )
    status, body = await _http_get(
        "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1",
        headers={"x-goog-api-key": google_key},
        timeout=8.0,
    )
    if status in (401, 403):
        return (
            _STATUS_FAILED,
            "s2s_credentials_rejected",
            "The Google AI credential was rejected.",
        )
    if status >= 400:
        return (
            _STATUS_FAILED,
            "s2s_provider_error",
            f"The Google AI API answered with HTTP {status}.",
        )
    return (
        _STATUS_OK,
        "s2s_credentials_valid",
        f"The Google AI credential was accepted for model {carrier.gemini_live_model}.",
    )


_CHECKS = {
    "vonage": _check_vonage,
    "sip": _check_sip,
    "s2s": _check_s2s,
}


def _legacy_credentials_present(carrier: str) -> bool:
    """Whether the credentials of a carrier that predates Batch 02 exist."""
    from app.core.config import settings

    if carrier == "twilio":
        return bool(
            (settings.twilio_account_sid or "").strip()
            and (settings.twilio_auth_token or "").strip()
        )
    if carrier == "telnyx":
        return bool((settings.telnyx_api_key or "").strip())
    return False


@router.get("/carriers/capabilities", response_model=CarrierCapabilitiesResponse)
async def list_carrier_capabilities(
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_SYNC)),
    session: AsyncSession = Depends(get_session),
) -> CarrierCapabilitiesResponse:
    """What this deployment can really do, per carrier.

    The rows report whether the carrier credentials of this deployment are
    present and whether its media path is usable. That is the carrier
    connection of the tenant, so it sits with the role that owns the
    connection (owner/admin) rather than with every read-only role.
    """
    from app.telephony.media.serializers import supported_media_serializers
    from app.telephony.providers.vonage import vonage_media_capabilities
    from app.telephony.sip.sip_capabilities import sip_media_capabilities

    carrier = _carrier_settings()
    rows: list[CarrierCapabilityRow] = []

    # The carriers whose media path the deployment already carries. Their
    # rows come from the shared serializer truth table, so this endpoint
    # cannot drift away from what the pipeline can actually do.
    serializers = supported_media_serializers()
    for name in ("twilio", "telnyx"):
        media = serializers.get(name, {})
        configured = _legacy_credentials_present(name)
        if not media.get("supported"):
            reason = str(media.get("reason") or "")
        elif configured:
            reason = f"The {name} media path is available and configured."
        else:
            reason = (
                f"The {name} media path is available, but no credentials are configured."
            )
        rows.append(
            CarrierCapabilityRow(
                carrier=name,
                supported=bool(media.get("supported")),
                status=_STATUS_OK if media.get("supported") else _STATUS_NOT_CONFIGURED,
                reason=reason,
                detail={
                    "serializer": media.get("serializer"),
                    "encodings": media.get("encodings", []),
                    "sample_rates": media.get("sample_rates", []),
                    "credentials_configured": configured,
                },
            )
        )

    vonage = vonage_media_capabilities().get("vonage", {})
    rows.append(
        CarrierCapabilityRow(
            carrier="vonage",
            supported=bool(vonage.get("supported")),
            status=_STATUS_OK if vonage.get("supported") else _STATUS_NOT_CONFIGURED,
            reason=str(vonage.get("reason") or ""),
            detail={
                "serializer": vonage.get("serializer"),
                "encodings": vonage.get("encodings", []),
                "sample_rates": vonage.get("sample_rates", []),
            },
        )
    )

    sip = sip_media_capabilities().get("sip", {})
    rows.append(
        CarrierCapabilityRow(
            carrier="sip",
            supported=bool(sip.get("supported")),
            status=_STATUS_OK if sip.get("supported") else _STATUS_NOT_CONFIGURED,
            reason=str(sip.get("reason") or ""),
            detail={
                "serializer": sip.get("serializer"),
                "encodings": sip.get("encodings", []),
                "sample_rates": sip.get("sample_rates", []),
                "gateway_url": carrier.sip_gateway_url,
            },
        )
    )

    s2s_ready = carrier.s2s_enabled and carrier.s2s_provider_available()
    rows.append(
        CarrierCapabilityRow(
            carrier="s2s",
            supported=s2s_ready,
            status=_STATUS_OK if s2s_ready else _STATUS_NOT_CONFIGURED,
            reason=(
                "The speech-to-speech runtime is enabled."
                if s2s_ready
                else "The speech-to-speech runtime needs ENABLE_S2S and a provider credential."
            ),
            detail={
                "enabled": carrier.s2s_enabled,
                "openai_realtime_model": carrier.openai_realtime_model,
                "gemini_live_model": carrier.gemini_live_model,
                "max_session_seconds": carrier.s2s_max_session_seconds,
                "fallback_enabled": carrier.s2s_fallback_enabled,
            },
        )
    )

    await _audit(
        session,
        ctx,
        event_type="carrier.capabilities.read",
        result="success",
        resource_type="carrier_capabilities",
        detail={"carriers": [row.carrier for row in rows]},
    )
    await session.commit()
    return CarrierCapabilitiesResponse(carriers=rows)


@router.post(
    "/carriers/{carrier}/test-connection",
    response_model=CarrierConnectionCheckResponse,
    status_code=200,
)
async def test_carrier_connection(
    carrier: str,
    payload: CarrierConnectionTestRequest | None = None,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_SYNC)),
    session: AsyncSession = Depends(get_session),
) -> CarrierConnectionCheckResponse:
    """Probe one carrier for real. Idempotent per ``Idempotency-Key``."""
    name = (carrier or "").strip().lower()
    if name not in CARRIERS:
        raise HTTPException(
            status_code=404,
            detail={"code": "unknown_carrier", "message": f"Unknown carrier {name!r}."},
        )
    key = (idempotency_key or "").strip()
    if not (MIN_IDEMPOTENCY_KEY_LENGTH <= len(key) <= MAX_IDEMPOTENCY_KEY_LENGTH):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "idempotency_key_required",
                "message": (
                    "a printable Idempotency-Key of 8 to 128 characters is required"
                ),
            },
        )

    body = (payload or CarrierConnectionTestRequest()).model_dump(mode="json")
    try:
        claim = await claim_request(
            session,
            tenant_id=ctx.tenant_id,
            operation=f"carrier.test_connection.{name}",
            key=key,
            request_data=body,
        )
    except IdempotencyConflict:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "idempotency_conflict",
                "message": "Idempotency-Key was already used for a different request.",
            },
        ) from None
    except IdempotencyInProgress:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "idempotency_in_progress",
                "message": "The probe is still in progress.",
            },
        ) from None
    except IdempotencyPreviousFailure:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "idempotency_previous_failure",
                "message": "The prior probe failed; use a new key for a new attempt.",
            },
        ) from None
    except ValueError:
        raise HTTPException(status_code=400, detail="Idempotency-Key is invalid") from None

    if claim.replayed:
        existing = await session.scalar(
            select(CarrierConnectionCheck).where(
                CarrierConnectionCheck.tenant_id == ctx.tenant_id,
                CarrierConnectionCheck.carrier == name,
                CarrierConnectionCheck.idempotency_key_digest == _digest(key),
            )
        )
        if existing is None:
            raise HTTPException(
                status_code=500,
                detail="the stored idempotency result no longer exists",
            )
        return _check_response(existing)

    # Commit the receipt before the external side effect: an ambiguous crash
    # then rejects a retry instead of repeating the probe.
    await session.commit()

    try:
        status, reason, detail = await _CHECKS[name]()
    except Exception as exc:
        log.warning("carrier_media.test_connection_failed", carrier=name, error=str(exc)[:160])
        status, reason, detail = (
            _STATUS_FAILED,
            "carrier_probe_error",
            f"The probe failed: {type(exc).__name__}.",
        )

    row = CarrierConnectionCheck(
        id=uuid.uuid4(),
        tenant_id=ctx.tenant_id,
        carrier=name,
        status=status,
        ok=status == _STATUS_OK,
        reason=reason,
        detail=detail,
        idempotency_key_digest=_digest(key),
        checked_by_user_id=ctx.user_id,
        checked_at=_now(),
    )
    session.add(row)
    await session.flush()

    await _audit(
        session,
        ctx,
        event_type="carrier.test_connection",
        result="success" if status == _STATUS_OK else "failure",
        resource_type="carrier_connection_check",
        resource_id=str(row.id),
        detail={"carrier": name, "status": status, "reason": reason},
    )
    await complete_request(
        session, claim.receipt, resource_type="carrier_connection_check", resource_id=row.id
    )
    await session.commit()
    return _check_response(row)


def _check_response(row: CarrierConnectionCheck) -> CarrierConnectionCheckResponse:
    return CarrierConnectionCheckResponse(
        id=str(row.id),
        carrier=row.carrier,
        status=row.status,
        ok=bool(row.ok),
        reason=row.reason,
        detail=row.detail,
        checked_at=row.checked_at,
    )


# ---------------------------------------------------------------------------
# Speech-to-speech sessions
# ---------------------------------------------------------------------------
def _session_response(row: S2SSessionRecord) -> S2SSessionResponse:
    return S2SSessionResponse(
        id=str(row.id),
        provider=row.provider,
        model=row.model,
        status=row.status,
        call_id=str(row.call_id) if row.call_id else None,
        failover_reason=row.failover_reason,
        error=row.error,
        started_at=row.started_at,
        ended_at=row.ended_at,
        duration_ms=row.duration_ms,
        input_audio_s=row.input_audio_s,
        output_audio_s=row.output_audio_s,
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        cost_micro_usd=row.cost_micro_usd,
        cost_known=row.cost_known,
    )


@router.get("/s2s/sessions", response_model=S2SSessionListResponse)
async def list_s2s_sessions(
    provider: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    ctx: TenantContext = Depends(require_permission(Permission.CALL_READ)),
    session: AsyncSession = Depends(get_session),
) -> S2SSessionListResponse:
    """The tenant's speech-to-speech sessions, newest first."""
    filters = [S2SSessionRecord.tenant_id == ctx.tenant_id]
    if provider:
        filters.append(S2SSessionRecord.provider == provider.strip().lower())
    if status_filter:
        filters.append(S2SSessionRecord.status == status_filter.strip().lower())

    total = int(
        (await session.execute(select(func.count()).select_from(S2SSessionRecord).where(*filters))).scalar_one()
        or 0
    )
    rows = (
        (
            await session.execute(
                select(S2SSessionRecord)
                .where(*filters)
                .order_by(S2SSessionRecord.started_at.desc(), S2SSessionRecord.id.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    await _audit(
        session,
        ctx,
        event_type="s2s.sessions.read",
        result="success",
        resource_type="s2s_session_list",
        detail={"total": total, "limit": limit, "offset": offset},
    )
    await session.commit()
    return S2SSessionListResponse(
        sessions=[_session_response(row) for row in rows], total=total
    )


@router.get("/s2s/sessions/{session_id}", response_model=S2SSessionResponse)
async def get_s2s_session(
    session_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.CALL_READ)),
    session: AsyncSession = Depends(get_session),
) -> S2SSessionResponse:
    """One speech-to-speech session of this tenant."""
    row = await session.scalar(
        select(S2SSessionRecord).where(
            S2SSessionRecord.id == session_id,
            S2SSessionRecord.tenant_id == ctx.tenant_id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="speech-to-speech session not found")
    await _audit(
        session,
        ctx,
        event_type="s2s.session.read",
        result="success",
        resource_type="s2s_session",
        resource_id=str(row.id),
        detail={"provider": row.provider, "status": row.status},
    )
    await session.commit()
    return _session_response(row)


__all__ = ["router"]
