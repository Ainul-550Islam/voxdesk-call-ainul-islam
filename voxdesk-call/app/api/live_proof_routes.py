"""Operator surface for the live PSTN proof harness.

A live proof run dials a real telephone number and spends real provider money, so
this router is deliberately small, heavily guarded and honest about refusal:

* ``require_tenant_admin`` (owner/admin role **and** ``Permission.CALL_WRITE``)
  to start or abort a run -- it places a call and spends money;
  ``Permission.CALL_READ`` to inspect one;
* tenant scoping comes from ``TenantContext``: every query filters ``tenant_id``,
  and a run id belonging to another tenant is a plain 404;
* ``Idempotency-Key`` is mandatory on both POST routes, so a retried request can
  never dial the number twice;
* a rate limit per tenant stops a loop from burning the budget;
* every state change is audited inside the same transaction;
* the endpoint refuses before doing anything when the harness is disabled, when
  the configuration is invalid, or when a provider credential is missing -- with
  the exact missing names, never a generic error.

The run itself is executed by ``run_pstn_probe``. That call blocks for the length
of the real call (bounded by ``LIVE_PROOF_TIMEOUT_SECONDS``), because returning
before the call ended would mean reporting numbers that do not exist yet.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_event
from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.core.config import settings
from app.core.errors import AppError
from app.db.models import UserRole
from app.core.logging import log
from app.core.rate_limit import allow_identity_action
from app.db.live_proof_models import LiveProofRun
from app.db.session import get_session, get_sessionmaker
from app.resilience.idempotency import (
    IdempotencyConflict,
    IdempotencyInProgress,
    IdempotencyPreviousFailure,
    claim_request,
    complete_request,
    fail_request,
)
from app.telephony.live_proof.cost_guard import CostGuard
from app.telephony.live_proof.evidence_writer import (
    EvidenceIntegrityError,
    ProviderCallLedger,
)
from app.telephony.live_proof.pstn_probe import (
    LiveProofResult,
    missing_provider_settings,
    run_pstn_probe,
)
from app.telephony.provider_errors import TelephonyError

router = APIRouter(prefix="/api/live-proof", tags=["live-proof"])

#: The repository has no ``TENANT_ADMIN`` permission: administrative authority is
#: expressed as a role plus the specific capability. A live proof is an
#: operator action that spends the tenant's money, so it requires both — the
#: owner/admin role *and* ``CALL_WRITE`` — which is the same pairing the rest of
#: the control plane uses.
ADMIN_ROLES = frozenset({UserRole.OWNER, UserRole.ADMIN})

OPERATION_START = "live_proof.run.start"
OPERATION_ABORT = "live_proof.run.abort"
MIN_IDEMPOTENCY_KEY_LENGTH = 8
MAX_IDEMPOTENCY_KEY_LENGTH = 128
START_RATE_LIMIT_PER_MINUTE = 6
ABORT_RATE_LIMIT_PER_MINUTE = 12
MAX_LIST_LIMIT = 100
DEFAULT_LIST_LIMIT = 25
#: Extra wall-clock headroom over the probe's own deadline before the request is
#: abandoned, so the probe's own timeout always wins and reports a real reason.
REQUEST_TIMEOUT_HEADROOM_SECONDS = 60.0


class LiveProofUnavailable(AppError):
    """A 5xx refusal that must reach the caller with its real code.

    ``AppError`` is the repo's convention for application-level 5xx responses:
    the framework handler masks any bare ``HTTPException`` over 499 as a generic
    ``internal_error``, so a refusal that names what is missing has to be an
    ``AppError`` to survive.
    """

    status_code = 503
    code = "live_proof_unavailable"


class LiveProofFailed(AppError):
    """The run itself could not produce a result."""

    status_code = 504
    code = "live_proof_failed"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())


class StartRunRequest(_Strict):
    """What an operator may vary about one live run.

    Caps can only *lower* the configured ceiling, never raise it: the operator's
    environment is the budget authority.
    """

    to_number: str | None = Field(default=None, max_length=20)
    caller_id: str | None = Field(default=None, max_length=20)
    max_minutes: float | None = Field(default=None, gt=0, le=60)
    max_usd: float | None = Field(default=None, gt=0, le=100)
    script: list[str] | None = Field(default=None, max_length=12)
    note: str = Field(default="", max_length=280)

    @field_validator("to_number", "caller_id")
    @classmethod
    def _digits_only(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        allowed = set("0123456789+")
        if set(cleaned) - allowed or not cleaned.lstrip("+").isdigit():
            raise ValueError("phone numbers must be digits with an optional leading +")
        return cleaned

    @field_validator("script")
    @classmethod
    def _lines(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        lines = [line.strip() for line in value if line.strip()]
        if not lines:
            return None
        for line in lines:
            if len(line) > 300:
                raise ValueError("each scripted caller line must be at most 300 characters")
        return lines


class LiveProofRunOut(_Strict):
    id: str
    tenant_id: str
    status: str
    provider_set: list[str]
    requested_to_number: str
    call_sid: str
    call_id: str | None
    turns: int
    e2e_p50_ms: float | None
    e2e_p95_ms: float | None
    e2e_p99_ms: float | None
    stt_ttfb_p50_ms: float | None
    llm_ttfb_p50_ms: float | None
    tts_ttfb_p50_ms: float | None
    cost_usd: float
    provider_fakes: bool
    is_evidence: bool
    evidence_path: str
    evidence_sha256: str
    recording_sha256: str
    requested_by: str | None
    detail: dict[str, Any]
    error: str
    duration_seconds: float | None
    created_at: str | None
    started_at: str | None
    finished_at: str | None


class LiveProofRunListOut(_Strict):
    total: int
    limit: int
    offset: int
    runs: list[LiveProofRunOut]


class ReadinessOut(_Strict):
    enabled: bool
    ready: bool
    calls_are_real: bool
    problems: list[str]
    missing_settings: list[str]
    provider_set: list[str]
    max_minutes: float
    max_usd: float
    usd_per_minute: float
    router_prefix: str


class EvidenceOut(_Strict):
    run_id: str
    sha256_recorded: str
    sha256_actual: str
    sha256_matches: bool
    provider_fakes: bool
    evidence: dict[str, Any]


class AbortOut(_Strict):
    run: LiveProofRunOut
    changed: bool


def get_live_proof_runner() -> Callable[..., Awaitable[LiveProofResult]]:
    """The real probe. Overridden in tests that must not dial a number."""
    return run_pstn_probe


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


async def _audit(
    session: AsyncSession,
    ctx: TenantContext,
    *,
    event_type: str,
    run_id: uuid.UUID,
    result: str = "success",
    detail: dict[str, Any] | None = None,
) -> None:
    await record_event(
        session,
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.user_id,
        actor_type="human",
        actor_email=ctx.user.email,
        event_type=event_type,
        resource_type="live_proof_run",
        resource_id=run_id,
        result=result,
        detail=detail or {},
    )


async def _rate_limit(ctx: TenantContext, *, action: str, limit: int) -> None:
    allowed = await allow_identity_action(
        action=action,
        who=str(ctx.tenant_id),
        limit=limit,
        window=60.0,
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "rate_limited", "message": "Too many live proof operations"},
            headers={"Retry-After": "60"},
        )


def require_tenant_admin(
    ctx: TenantContext = Depends(require_permission(Permission.CALL_WRITE)),
) -> TenantContext:
    """Owner or admin of this tenant, holding ``CALL_WRITE`` as well."""
    role = ctx.role
    if not isinstance(role, UserRole):
        try:
            role = UserRole(str(role))
        except ValueError:
            role = None
    if role not in ADMIN_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "admin_only",
                "message": "A live proof spends real money; only an owner or admin may run one.",
            },
        )
    return ctx


def _require_idempotency_key(key: str | None) -> str:
    if key is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "idempotency_key_required",
                "message": "This route places a real call; an Idempotency-Key header is required.",
            },
        )
    cleaned = key.strip()
    if len(cleaned) < MIN_IDEMPOTENCY_KEY_LENGTH or len(cleaned) > MAX_IDEMPOTENCY_KEY_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "idempotency_key_invalid",
                "message": (
                    f"Idempotency-Key must be {MIN_IDEMPOTENCY_KEY_LENGTH}-"
                    f"{MAX_IDEMPOTENCY_KEY_LENGTH} printable characters."
                ),
            },
        )
    return cleaned


def _require_harness_ready() -> None:
    """Refuse before any provider call when the harness cannot be honest."""
    if not settings.live_proof_enabled:
        raise LiveProofUnavailable(
            "LIVE_PROOF_ENABLED is not set; a live proof dials a real number and is opt-in.",
            code="live_proof_disabled",
        )
    missing = missing_provider_settings()
    if missing:
        # Credential gaps come first: they are the failure an operator can fix,
        # and the message names each missing setting instead of a generic refusal.
        raise LiveProofUnavailable(
            "live proof needs real provider credentials; missing " + ", ".join(missing),
            code="provider_not_configured",
            detail={"missing": list(missing)},
        )
    problems = settings.live_proof_problems()
    if problems:
        raise LiveProofUnavailable("; ".join(problems), code="live_proof_misconfigured")


def _apply_result(row: LiveProofRun, result: LiveProofResult) -> None:
    """Copy the probe's measured outcome onto the durable row."""
    row.status = result.status
    row.provider_set = ",".join(result.provider_set)
    row.call_sid = result.call_sid or row.call_sid
    row.call_id = uuid.UUID(result.call_id) if result.call_id else row.call_id
    row.turns = int(result.turns)
    row.e2e_p50_ms = result.e2e_p50_ms
    row.e2e_p95_ms = result.e2e_p95_ms
    row.e2e_p99_ms = result.e2e_p99_ms
    row.stt_ttfb_p50_ms = result.stt_ttfb_p50_ms
    row.llm_ttfb_p50_ms = result.llm_ttfb_p50_ms
    row.tts_ttfb_p50_ms = result.tts_ttfb_p50_ms
    row.cost_usd = float(result.cost_usd)
    row.provider_fakes = bool(result.provider_fakes)
    row.evidence_path = result.evidence_path or row.evidence_path
    row.evidence_sha256 = result.evidence_sha256 or row.evidence_sha256
    row.recording_sha256 = result.recording_sha256 or row.recording_sha256
    row.error = result.error
    row.started_at = result.started_at or row.started_at
    row.finished_at = result.finished_at
    row.detail = {
        "notes": list(result.notes),
        "provider_set": list(result.provider_set),
    }


async def _load_run(
    session: AsyncSession, ctx: TenantContext, run_id: uuid.UUID
) -> LiveProofRun:
    row = await session.scalar(
        select(LiveProofRun).where(
            LiveProofRun.id == run_id,
            LiveProofRun.tenant_id == ctx.tenant_id,
        )
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "live_proof_run_not_found", "message": "No live proof run with that id."},
        )
    return row


def _evidence_payload(row: LiveProofRun) -> dict[str, Any]:
    """Read back the evidence file, refusing anything outside the evidence root."""
    import hashlib
    import json
    from pathlib import Path

    if not row.evidence_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "live_proof_evidence_missing",
                "message": "This run produced no evidence file.",
            },
        )
    root = Path(settings.live_proof_evidence_dir).resolve()
    path = Path(row.evidence_path).resolve()
    if root != path and root not in path.parents:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "live_proof_evidence_outside_root",
                "message": "The stored evidence path is outside LIVE_PROOF_EVIDENCE_DIR.",
            },
        )
    if not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail={
                "code": "live_proof_evidence_deleted",
                "message": "The evidence file recorded for this run is no longer on disk.",
            },
        )
    raw = path.read_bytes()
    return {
        "run_id": str(row.id),
        "sha256_recorded": row.evidence_sha256,
        "sha256_actual": hashlib.sha256(raw).hexdigest(),
        "sha256_matches": hashlib.sha256(raw).hexdigest() == row.evidence_sha256,
        "provider_fakes": bool(row.provider_fakes),
        "evidence": json.loads(raw.decode("utf-8")),
    }


# --------------------------------------------------------------------------- #
# read routes
# --------------------------------------------------------------------------- #


@router.get("/readiness", response_model=ReadinessOut)
async def live_proof_readiness(
    ctx: TenantContext = Depends(require_permission(Permission.CALL_READ)),
) -> ReadinessOut:
    """Whether this deployment could run an honest live proof right now."""
    problems = settings.live_proof_problems()
    missing = missing_provider_settings()
    return ReadinessOut(
        enabled=bool(settings.live_proof_enabled),
        ready=bool(settings.live_proof_enabled) and not problems and not missing,
        calls_are_real=True,
        problems=problems,
        missing_settings=list(missing),
        provider_set=[
            "twilio",
            settings.stt_provider,
            settings.tts_provider,
            "openai",
        ],
        max_minutes=float(settings.live_proof_max_minutes),
        max_usd=float(settings.live_proof_max_usd),
        usd_per_minute=float(settings.live_proof_usd_per_minute),
        router_prefix=router.prefix,
    )


@router.get("/runs", response_model=LiveProofRunListOut)
async def list_runs(
    limit: int = Query(default=DEFAULT_LIST_LIMIT, ge=1, le=MAX_LIST_LIMIT),
    offset: int = Query(default=0, ge=0),
    run_status: str | None = Query(default=None, alias="status", max_length=16),
    ctx: TenantContext = Depends(require_permission(Permission.CALL_READ)),
    session: AsyncSession = Depends(get_session),
) -> LiveProofRunListOut:
    filters = [LiveProofRun.tenant_id == ctx.tenant_id]
    if run_status:
        filters.append(LiveProofRun.status == run_status.strip().lower())
    total = await session.scalar(select(func.count()).select_from(LiveProofRun).where(*filters)) or 0
    rows = (
        await session.execute(
            select(LiveProofRun)
            .where(*filters)
            .order_by(LiveProofRun.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    return LiveProofRunListOut(
        total=int(total),
        limit=limit,
        offset=offset,
        runs=[LiveProofRunOut(**row.as_dict()) for row in rows],
    )


@router.get("/runs/{run_id}", response_model=LiveProofRunOut)
async def get_run(
    run_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.CALL_READ)),
    session: AsyncSession = Depends(get_session),
) -> LiveProofRunOut:
    row = await _load_run(session, ctx, run_id)
    return LiveProofRunOut(**row.as_dict())


@router.get("/runs/{run_id}/evidence", response_model=EvidenceOut)
async def get_evidence(
    run_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.CALL_READ)),
    session: AsyncSession = Depends(get_session),
) -> EvidenceOut:
    row = await _load_run(session, ctx, run_id)
    return EvidenceOut(**_evidence_payload(row))


# --------------------------------------------------------------------------- #
# write routes
# --------------------------------------------------------------------------- #


@router.post("/runs", response_model=LiveProofRunOut, status_code=status.HTTP_201_CREATED)
async def start_run(
    payload: StartRunRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ctx: TenantContext = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_session),
    runner: Callable[..., Awaitable[LiveProofResult]] = Depends(get_live_proof_runner),
) -> LiveProofRunOut:
    """Place one real PSTN call against this tenant's agent and record the proof."""
    key = _require_idempotency_key(idempotency_key)
    await _rate_limit(ctx, action="live_proof:start", limit=START_RATE_LIMIT_PER_MINUTE)
    _require_harness_ready()

    if payload.max_minutes is not None and payload.max_minutes > float(settings.live_proof_max_minutes):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "live_proof_cap_too_high",
                "message": (
                    "max_minutes may lower but never raise the configured ceiling "
                    f"LIVE_PROOF_MAX_MINUTES={settings.live_proof_max_minutes}"
                ),
            },
        )
    if payload.max_usd is not None and payload.max_usd > float(settings.live_proof_max_usd):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "live_proof_cap_too_high",
                "message": (
                    "max_usd may lower but never raise the configured ceiling "
                    f"LIVE_PROOF_MAX_USD={settings.live_proof_max_usd}"
                ),
            },
        )

    request_data = {
        "to_number": payload.to_number,
        "caller_id": payload.caller_id,
        "max_minutes": payload.max_minutes,
        "max_usd": payload.max_usd,
        "script": payload.script,
        "note": payload.note,
    }
    try:
        claim = await claim_request(
            session,
            tenant_id=ctx.tenant_id,
            operation=OPERATION_START,
            key=key,
            request_data=request_data,
        )
    except IdempotencyConflict:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "idempotency_conflict",
                "message": "Idempotency-Key was already used for a different live proof request.",
            },
        ) from None
    except IdempotencyInProgress:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "idempotency_in_progress",
                "message": "An identical live proof request is still running.",
            },
        ) from None
    except IdempotencyPreviousFailure:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "idempotency_previous_failure",
                "message": "That Idempotency-Key already failed; retry with a new key.",
            },
        ) from None

    if claim.replayed and claim.receipt.resource_id:
        existing = await _load_run(session, ctx, uuid.UUID(claim.receipt.resource_id))
        return LiveProofRunOut(**existing.as_dict())

    in_flight = await session.scalar(
        select(LiveProofRun.id).where(
            LiveProofRun.tenant_id == ctx.tenant_id,
            LiveProofRun.status == "running",
        )
    )
    if in_flight is not None:
        await fail_request(session, claim.receipt, category="live_proof_already_running")
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "live_proof_already_running",
                "message": (
                    "A live proof run is already in flight for this tenant. One real dial at a "
                    "time, so the measurements stay attributable."
                ),
                "run_id": str(in_flight),
            },
        )

    row = LiveProofRun(
        id=uuid.uuid4(),
        tenant_id=ctx.tenant_id,
        status="running",
        provider_set=",".join(["twilio", settings.stt_provider, settings.tts_provider, "openai"]),
        requested_to_number=(payload.to_number or settings.live_proof_test_number or ""),
        requested_by=ctx.user_id,
        provider_fakes=True,
        detail={"note": payload.note},
    )
    session.add(row)
    await _audit(
        session,
        ctx,
        event_type="live_proof.run.start",
        run_id=row.id,
        detail={"to_number": row.requested_to_number, "note": payload.note},
    )
    # The receipt and the pending run row must exist before the dial starts.
    await session.commit()

    guard = CostGuard(
        max_minutes=float(payload.max_minutes or settings.live_proof_max_minutes),
        max_usd=float(payload.max_usd or settings.live_proof_max_usd),
        usd_per_minute=float(settings.live_proof_usd_per_minute),
    )
    ledger = ProviderCallLedger()

    try:
        result = await asyncio.wait_for(
            runner(
                tenant_id=str(ctx.tenant_id),
                run_id=str(row.id),
                to_number=payload.to_number or "",
                from_number=payload.caller_id or "",
                session_factory=get_sessionmaker(),
                cost_guard=guard,
                script=payload.script,
                ledger=ledger,
            ),
            timeout=float(settings.live_proof_timeout_seconds) + REQUEST_TIMEOUT_HEADROOM_SECONDS,
        )
    except asyncio.TimeoutError:
        row.status = "failed"
        row.error = "live_proof_timeout"
        await _audit(
            session,
            ctx,
            event_type="live_proof.run.timeout",
            run_id=row.id,
            result="failure",
            detail={"timeout_seconds": float(settings.live_proof_timeout_seconds)},
        )
        await fail_request(session, claim.receipt, category="live_proof_timeout")
        await session.commit()
        raise LiveProofFailed(
            "The live call did not finish inside the configured deadline.",
            code="live_proof_timeout",
            detail={"run_id": str(row.id)},
        ) from None
    except TelephonyError as exc:
        row.status = "failed"
        row.error = exc.code
        await _audit(
            session,
            ctx,
            event_type="live_proof.run.failed",
            run_id=row.id,
            result="failure",
            detail={"error": exc.code},
        )
        await fail_request(session, claim.receipt, category=exc.code)
        await session.commit()
        raise LiveProofFailed(exc.message, code=exc.code, detail=exc.as_dict()) from None
    except EvidenceIntegrityError as exc:
        row.status = "failed"
        row.error = exc.code
        await _audit(
            session,
            ctx,
            event_type="live_proof.run.failed",
            run_id=row.id,
            result="failure",
            detail={"error": exc.code},
        )
        await fail_request(session, claim.receipt, category=exc.code)
        await session.commit()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.as_dict()) from None

    _apply_result(row, result)
    await complete_request(
        session,
        claim.receipt,
        resource_type="live_proof_run",
        resource_id=row.id,
    )
    await _audit(
        session,
        ctx,
        event_type="live_proof.run.finished",
        run_id=row.id,
        result="success" if result.status == "succeeded" else "failure",
        detail={
            "status": result.status,
            "provider_fakes": result.provider_fakes,
            "e2e_p50_ms": result.e2e_p50_ms,
            "cost_usd": result.cost_usd,
            "error": result.error,
        },
    )
    await session.commit()
    await session.refresh(row)
    log.info(
        "live_proof.api.run_finished",
        tenant_id=str(ctx.tenant_id),
        run_id=str(row.id),
        status=row.status,
        provider_fakes=row.provider_fakes,
    )
    return LiveProofRunOut(**row.as_dict())


@router.post("/runs/{run_id}/abort", response_model=AbortOut)
async def abort_run(
    run_id: uuid.UUID,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ctx: TenantContext = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_session),
) -> AbortOut:
    """Mark a stuck run aborted so the next run is not blocked by it.

    The carrier leg is ended by the probe's own cost guard; this route only
    reconciles the durable row when a process died mid-run. It cannot make a run
    into evidence: the status becomes ``aborted``.
    """
    key = _require_idempotency_key(idempotency_key)
    await _rate_limit(ctx, action="live_proof:abort", limit=ABORT_RATE_LIMIT_PER_MINUTE)

    row = await _load_run(session, ctx, run_id)
    try:
        claim = await claim_request(
            session,
            tenant_id=ctx.tenant_id,
            operation=OPERATION_ABORT,
            key=key,
            request_data={"run_id": str(run_id)},
        )
    except IdempotencyConflict:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "idempotency_conflict",
                "message": "Idempotency-Key was already used to abort a different run.",
            },
        ) from None
    except IdempotencyInProgress:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "idempotency_in_progress", "message": "An identical abort is in flight."},
        ) from None
    except IdempotencyPreviousFailure:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "idempotency_previous_failure",
                "message": "That Idempotency-Key already failed; retry with a new key.",
            },
        ) from None

    changed = False
    if row.status in {"running", "queued"}:
        row.status = "aborted"
        row.error = row.error or "aborted_by_operator"
        changed = True
        await _audit(
            session,
            ctx,
            event_type="live_proof.run.aborted",
            run_id=row.id,
            detail={"previous_status": "running"},
        )

    await complete_request(
        session,
        claim.receipt,
        resource_type="live_proof_run",
        resource_id=row.id,
    )
    await session.commit()
    await session.refresh(row)
    return AbortOut(run=LiveProofRunOut(**row.as_dict()), changed=changed)
