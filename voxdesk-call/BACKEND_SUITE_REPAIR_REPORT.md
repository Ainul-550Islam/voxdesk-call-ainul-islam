# Backend suite execution and repairs — 2026-10-06

## Result and remaining hard blocker

**4,145 current backend test IDs reconciled: 4,132 passed, 0 failed, 13 skipped because live-provider credentials are absent.** No current test ID is unaccounted for. The 13 live-provider checks were explicitly attempted again with `VOXDESK_REAL_INTEGRATION=1`; their missing-credential guards still prevented network validation. **The requested zero-skip acceptance criterion is NOT met.** These checks were not deleted, mocked into success, or reclassified as passes.

This is a node-ID reconciliation across sequential isolated runs and repair reruns, not a claim of one uninterrupted green full-suite command. The original collection contained 4,143 tests; two cancellation transition regression cases bring the current collection to 4,145. Changed calendar cases and the former unconditional job-recovery placeholder now execute meaningful assertions, with renamed IDs tracked by the fresh collection.

`PROMPT8_FINAL_REPORT.md` describes an earlier validation snapshot. This report supersedes that snapshot's backend execution counts and builder-preview review status; it does not upgrade live-provider or overall Retell parity claims.

## Execution evidence

| Lane | Result |
|---|---|
| Initial full-suite execution | 2,464 passed; 5 failed; 31 skipped; 50 unconfirmed; 1,593 later tests not run after an unstable randomized parameter ID caused collection failure |
| Remaining 1,643 IDs | 1,480 passed; 7 failed; 13 skipped; 60 unconfirmed; 83 later tests not run |
| Repair run, five IDs per child | 455 passed; 0 failed; 0 skipped; 5 unconfirmed after timeout; 83 later IDs not run |
| File-isolated repair tail | 88 passed; 0 failed; 0 skipped; 0 unconfirmed |
| Current node-ID reconciliation | **4,132 passed / 0 failed / 13 skipped / 0 unaccounted** |
| Dashboard | **559 passed across 48 files**; full suite rerun after builder changes |
| Dashboard production build | Passed; existing large-bundle advisory remains |
| Rust control-plane core | **53 passed / 0 failed / 0 ignored** |
| PostgreSQL recovery and enum probes | **3 passed**; real PostgreSQL 17.11, migrated through 0048 |
| Changed Python files | Ruff passed on all 15 changed Python source/test files; compileall passed |

Resource issues are retained in original evidence: one remaining-execution chunk was resource-terminated and later chunks timed out. Mixing heavy TTS imports with API/database tests caused memory pressure. All affected current IDs were subsequently rerun successfully in smaller, file-isolated processes. A setup invocation initially failed because Python dependencies were absent; the pinned requirements were then installed in `.venv`. No assertion failure has been relabeled as a resource limit.

The previously pending `tests/test_telephony_runtime_e2e.py` was rerun after fixture corrections and passed in the repair lane. These tests use explicitly simulated provider paths; they do not prove a real carrier call.

## Repairs

1. Calendar and CRM audit writers now retain validated field-name metadata under `provided_field_names`; credential values remain redacted. Existing secret-leak assertions remain and were strengthened for the metadata key.
2. Added the missing terminal CANCELLED state entry, aligned protobuf and Rust vocabulary, and added cancellation transition coverage. Existing immutable migration history was not rewritten.
3. Stabilized a randomized credential-test parameter ID so collection and child-process execution address the same test.
4. Replaced calendar contract skip branches with actual supported/unsupported-path assertions. HTTP transport, local provider behavior, legacy wrapper acknowledgment, and Cal.com inline datetime payloads are exercised.
5. Fixed the Google service-account free/busy path: unavailable configuration, provider errors, and per-calendar errors no longer masquerade as empty availability or a successful health check. Exception logs use error types rather than raw provider messages.
6. Replaced an unconditional skipped job-recovery placeholder with a real PostgreSQL multi-process SKIP LOCKED and persisted lease-expiry/reclaim test. Duplicate claiming and attempt history are asserted.
7. Exercised credentialless billing error handling instead of skipping it.
8. Anchored the analytics test's event timestamp inside the date range it queries, eliminating wall-clock-dependent failure without weakening the cost assertion.
9. Updated migration tests to recognize additive cancellation/context migrations and use Alembic's revision graph rather than quote-style-dependent regex parsing.
10. Corrected telephony E2E fixtures to supply a persisted environment binding and a required idempotency key. Fail-closed runtime checks remain intact.
11. Removed misleading public builder claims, fabricated indexed-document/voice status, inert buttons, stale API labels, and unused padding. The component now clearly describes workflow guidance, switches the selected step, and links to authenticated workspaces. It does not claim to be a saved agent or a live call.

## Credentials required to reach zero skipped live checks

Do not paste secret values into this report or chat. Configure them through the runtime's private environment/secret mechanism, then rerun the read-only checks.

```text
sssssssssssss                                                            [100%]
=========================== short test summary info ============================
SKIPPED [1] tests/test_real_providers.py:41: ANTHROPIC_API_KEY not configured
SKIPPED [1] tests/test_real_providers.py:41: not configured (missing: VOXDESK_REAL_CALCOM_API_KEY)
SKIPPED [1] tests/test_real_providers.py:41: DEEPGRAM_API_KEY not configured
SKIPPED [1] tests/test_real_providers.py:41: ELEVENLABS_API_KEY not configured
SKIPPED [1] tests/test_real_providers.py:41: not configured (missing: VOXDESK_REAL_GHL_ACCESS_TOKEN, VOXDESK_REAL_GHL_LOCATION_ID)
SKIPPED [1] tests/test_real_providers.py:41: not configured (missing: VOXDESK_REAL_GOOGLE_CALENDAR_REFRESH_TOKEN, VOXDESK_REAL_GOOGLE_CALENDAR_CLIENT_ID, VOXDESK_REAL_GOOGLE_CALENDAR_CLIENT_SECRET)
SKIPPED [1] tests/test_real_providers.py:41: GOOGLE_API_KEY not configured
SKIPPED [1] tests/test_real_providers.py:41: not configured (missing: VOXDESK_REAL_HUBSPOT_TOKEN)
SKIPPED [1] tests/test_real_providers.py:41: not configured (missing: VOXDESK_REAL_JOBBER_ACCESS_TOKEN)
SKIPPED [1] tests/test_real_providers.py:41: not configured (missing: VOXDESK_REAL_MICROSOFT_REFRESH_TOKEN, VOXDESK_REAL_MICROSOFT_CLIENT_ID, VOXDESK_REAL_MICROSOFT_CLIENT_SECRET)
SKIPPED [1] tests/test_real_providers.py:41: OPENAI_API_KEY not configured
SKIPPED [1] tests/test_real_providers.py:41: STRIPE_SECRET_KEY not configured
SKIPPED [1] tests/test_real_providers.py:41: TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN not configured
13 skipped in 0.08s
```

These checks are read-only. No customer phone call, SMS, charge, booking, or CRM mutation is needed. Valid provider accounts and credentials are still required; source-code changes cannot substitute for them.

## Complete resulting source files

All 19 modified repository source/test files and all three execution helper scripts are reproduced below without abridgment. The companion ZIP contains these files, every run log/manifest from the execution lanes, the fresh collection, and the per-node final outcome ledger. Existing prior implementation files not changed in this continuation remain in the repository and earlier report.

### File 01: `app/api/appointment_routes.py`

SHA-256: `0767895246e654f397a7ae505db6985d942d17587d8832024e33b00f3fd1be26`

````
"""
Appointment and calendar-integration API.

    GET    /api/appointments/availability          open slots
    GET    /api/appointments                       list (tenant-scoped)
    POST   /api/appointments                       book
    GET    /api/appointments/{id}                  one appointment
    PATCH  /api/appointments/{id}                  reschedule
    POST   /api/appointments/{id}/cancel           cancel
    POST   /api/appointments/{id}/no-show          mark absent

    GET    /api/calendar/providers                 catalogue + capabilities
    GET    /api/calendar/integrations              list connections
    PUT    /api/calendar/integrations/{provider}   connect or update
    DELETE /api/calendar/integrations/{provider}   disconnect
    POST   /api/calendar/integrations/{provider}/test    health check
    GET    /api/calendar/policy                    scheduling rules
    PUT    /api/calendar/policy                    update scheduling rules

Two rules run through the whole file, unchanged from STEP 5 because they were
right there:

**The tenant is never a parameter.** It comes from `ctx.tenant_id`, which comes
from the verified JWT. Requirement 14 asks for that explicitly, and a test
asserts a body containing `tenant_id` changes nothing.

**Responses are allowlists.** `AppointmentOut` and `CalendarIntegrationOut`
name every field that may reach a client. No OAuth token, no refresh token, no
ciphertext, no client secret.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, get_owned, record_audit, require_permission
from app.auth.permissions import Permission
from app.core.logging import log
from app.core.ssrf import OutboundUrlError, validate_outbound_url
from app.db.models import (
    Appointment,
    AppointmentStatus,
    AuditAction,
    CalendarIntegration,
    CalendarProviderType,
    SchedulingPolicy,
)
from app.db.session import get_session
from app.integrations.calendar import policy as policy_engine
from app.integrations.calendar import service
from app.integrations.calendar.models import BookingOutcome
from app.integrations.calendar.registry import capabilities_of
from app.integrations.calendar.timezones import (
    AmbiguousTimeError,
    NonexistentTimeError,
    TimezoneError,
    as_utc,
    is_valid_zone,
    now_utc,
    resolve_local,
)

router = APIRouter(prefix="/api/appointments", tags=["appointments"])
calendar_router = APIRouter(prefix="/api/calendar", tags=["calendar"])


#: Credential fields each provider accepts. An allowlist, so a tenant cannot
#: stuff arbitrary keys into the encrypted blob.
CREDENTIAL_FIELDS: dict[CalendarProviderType, tuple[str, ...]] = {
    CalendarProviderType.GOOGLE: (
        "access_token", "refresh_token", "client_id", "client_secret",
    ),
    CalendarProviderType.MICROSOFT: (
        "access_token", "refresh_token", "client_id", "client_secret", "scope",
    ),
    CalendarProviderType.CALCOM: ("api_key",),
    CalendarProviderType.GOOGLE_SERVICE_ACCOUNT: (),
    CalendarProviderType.INTERNAL: (),
}

#: Non-secret settings. Everything here is returned by the API, so nothing
#: credential-shaped may be added.
CONFIG_FIELDS: dict[CalendarProviderType, tuple[str, ...]] = {
    CalendarProviderType.GOOGLE: ("calendar_id", "base_url", "token_url"),
    CalendarProviderType.MICROSOFT: (
        "calendar_id", "mailbox", "schedule_id", "directory_tenant",
        "availability_interval", "base_url", "token_url",
    ),
    CalendarProviderType.CALCOM: (
        "event_type_id", "api_version", "language", "base_url",
    ),
    CalendarProviderType.GOOGLE_SERVICE_ACCOUNT: ("calendar_id",),
    CalendarProviderType.INTERNAL: (),
}


# ----------------------------------------------------------------- schemas ---

class AppointmentOut(BaseModel):
    id: uuid.UUID
    status: str
    customer_name: str
    customer_phone: str
    attendee_email: str | None = None
    reason: str = ""
    starts_at: str
    ends_at: str
    timezone: str
    provider: str | None = None
    #: The provider's event id. Safe: it is meaningless without the tenant's
    #: own credentials, and staff need it to find the event in their calendar.
    external_event_id: str | None = None
    meeting_url: str | None = None
    call_id: uuid.UUID | None = None
    lead_id: uuid.UUID | None = None
    cancelled_at: str | None = None
    cancellation_reason: str | None = None
    cancelled_by: str | None = None
    rescheduled_from: str | None = None
    confirmed_at: str | None = None
    #: Already scrubbed by `errors.safe_message` before it was stored.
    last_error: str | None = None
    created_at: str | None = None


class AppointmentListOut(BaseModel):
    appointments: list[AppointmentOut]
    total: int


class SlotOut(BaseModel):
    start: str
    end: str
    spoken: str


class AvailabilityOut(BaseModel):
    date: str
    timezone: str
    slots: list[SlotOut]
    #: Non-null when the provider could not be reached, so a UI can say
    #: "showing our own diary only" instead of implying certainty.
    degraded: str | None = None


class BookIn(BaseModel):
    customer_name: str = Field(min_length=1, max_length=200)
    customer_phone: str = Field(min_length=3, max_length=32)
    #: Local wall-clock in the tenant's timezone, e.g. "2026-09-08T15:00:00".
    #: Deliberately not UTC: a dashboard user picks a time on a clock, and
    #: making the client convert is where offsets get dropped.
    starts_at_local: str
    reason: str = ""
    customer_email: str | None = None
    lead_id: uuid.UUID | None = None
    #: Client-supplied idempotency. Without one the key is derived from
    #: (tenant, phone, start).
    request_id: str | None = None


class RescheduleIn(BaseModel):
    starts_at_local: str
    reason: str = ""


class CancelIn(BaseModel):
    reason: str = ""


class CalendarIntegrationOut(BaseModel):
    provider: str
    is_enabled: bool
    is_primary: bool
    connected: bool = Field(
        description="whether credentials are stored -- not whether they work"
    )
    capabilities: list[str] = Field(default_factory=list)
    config: dict = Field(default_factory=dict)
    #: Named `connected_at`, not `credentials_updated_at`: a response field
    #: whose name starts with "credentials" invites a sibling that holds them.
    connected_at: str | None = None
    token_expires_at: str | None = None
    last_health_check_at: str | None = None
    last_health_ok: bool | None = None
    last_error: str | None = None


class CalendarIntegrationIn(BaseModel):
    is_enabled: bool = True
    is_primary: bool = False
    credentials: dict[str, str] | None = None
    config: dict = Field(default_factory=dict)

    @field_validator("credentials")
    @classmethod
    def _reject_empty(cls, value):
        if value is None:
            return None
        for key, item in value.items():
            if not isinstance(item, str) or not item.strip():
                raise ValueError(f"credential {key!r} must be a non-empty string")
        return value


class ProviderInfo(BaseModel):
    provider: str
    capabilities: list[str]
    credential_fields: list[str]
    config_fields: list[str]


class ProviderCatalogueOut(BaseModel):
    providers: list[ProviderInfo]


class PolicyOut(BaseModel):
    timezone: str
    weekly_hours: dict = Field(default_factory=dict)
    holidays: list = Field(default_factory=list)
    blocked_periods: list = Field(default_factory=list)
    slot_minutes: int
    slot_interval_minutes: int
    buffer_before_minutes: int
    buffer_after_minutes: int
    minimum_notice_minutes: int
    booking_horizon_days: int
    max_slots_offered: int
    allow_outside_business_hours: bool
    require_provider_confirmation: bool
    #: False when the tenant has no policy row and the legacy Tenant columns
    #: are supplying the answer.
    configured: bool = True


class PolicyIn(BaseModel):
    weekly_hours: dict | None = None
    holidays: list | None = None
    blocked_periods: list | None = None
    slot_minutes: int = Field(default=30, ge=5, le=480)
    slot_interval_minutes: int = Field(default=30, ge=5, le=480)
    buffer_before_minutes: int = Field(default=0, ge=0, le=240)
    buffer_after_minutes: int = Field(default=0, ge=0, le=240)
    minimum_notice_minutes: int = Field(default=60, ge=0, le=40320)
    booking_horizon_days: int = Field(default=60, ge=1, le=730)
    max_slots_offered: int = Field(default=3, ge=1, le=20)
    allow_outside_business_hours: bool = False
    require_provider_confirmation: bool = True
    #: Changing the tenant's timezone is a scheduling decision, so it lives
    #: here rather than in the generic tenant settings.
    timezone: str | None = None


# ------------------------------------------------------------- serializers ---

def _iso(value: datetime | None) -> str | None:
    return as_utc(value).isoformat() if value else None


def to_out(appointment: Appointment) -> AppointmentOut:
    return AppointmentOut(
        id=appointment.id,
        status=appointment.status.value,
        customer_name=appointment.customer_name,
        customer_phone=appointment.customer_phone,
        attendee_email=appointment.attendee_email,
        reason=appointment.reason or "",
        starts_at=_iso(appointment.starts_at),
        ends_at=_iso(appointment.ends_at),
        timezone=appointment.timezone,
        provider=appointment.provider.value if appointment.provider else None,
        external_event_id=appointment.external_event_id,
        meeting_url=appointment.meeting_url,
        call_id=appointment.call_id,
        lead_id=appointment.lead_id,
        cancelled_at=_iso(appointment.cancelled_at),
        cancellation_reason=appointment.cancellation_reason,
        cancelled_by=appointment.cancelled_by,
        rescheduled_from=_iso(appointment.rescheduled_from),
        confirmed_at=_iso(appointment.confirmed_at),
        last_error=appointment.last_error,
        created_at=_iso(appointment.created_at),
    )


def integration_out(integration: CalendarIntegration) -> CalendarIntegrationOut:
    return CalendarIntegrationOut(
        provider=integration.provider.value,
        is_enabled=integration.is_enabled,
        is_primary=integration.is_primary,
        connected=bool(integration.credentials_encrypted),
        capabilities=sorted(c.value for c in capabilities_of(integration.provider)),
        config=dict(integration.config or {}),
        connected_at=_iso(integration.credentials_updated_at),
        token_expires_at=_iso(integration.token_expires_at),
        last_health_check_at=_iso(integration.last_health_check_at),
        last_health_ok=integration.last_health_ok,
        last_error=integration.last_error,
    )


def _parse_provider(provider: str) -> CalendarProviderType:
    try:
        return CalendarProviderType(provider.lower())
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Unknown provider. Supported: "
                f"{', '.join(p.value for p in CalendarProviderType)}"
            ),
        )


def _resolve_local_input(raw: str, timezone_name: str) -> datetime:
    """
    Parse a local wall-clock string into a UTC instant.

    The two DST cases become 422s with an explanation rather than a silent
    shift. Requirement 4: never silently move an appointment by an hour.
    """
    try:
        naive = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=422,
            detail=f"{raw!r} is not an ISO datetime like 2026-09-08T15:00:00",
        )
    if naive.tzinfo is not None:
        return naive.astimezone(as_utc(naive).tzinfo)

    try:
        return resolve_local(naive, timezone_name).utc
    except AmbiguousTimeError as exc:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{raw} happens twice in {timezone_name} (clocks go back). "
                f"Send an explicit offset: {exc.first.isoformat()} or "
                f"{exc.second.isoformat()}."
            ),
        )
    except NonexistentTimeError as exc:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{raw} does not exist in {timezone_name} (clocks go forward "
                f"from {exc.gap_start:%H:%M} to {exc.gap_end:%H:%M})."
            ),
        )
    except TimezoneError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


#: Outcome -> HTTP status. A conflict is a 409 and a policy refusal a 422, so
#: a client can branch on the status without parsing prose.
_OUTCOME_STATUS = {
    BookingOutcome.CONFLICT: 409,
    BookingOutcome.OUTSIDE_HOURS: 422,
    BookingOutcome.TOO_SOON: 422,
    BookingOutcome.TOO_FAR: 422,
    BookingOutcome.NEEDS_CLARIFICATION: 422,
    BookingOutcome.NOT_FOUND: 404,
    BookingOutcome.FAILED: 502,
}


def _raise_for_outcome(result) -> None:
    status = _OUTCOME_STATUS.get(result.outcome)
    if status is not None:
        raise HTTPException(
            status_code=status,
            detail={"outcome": result.outcome.value, "message": result.message},
        )


# -------------------------------------------------------- appointment routes ---

@router.get("/availability", response_model=AvailabilityOut)
async def get_availability(
    day: date = Query(..., description="local date, YYYY-MM-DD"),
    part_of_day: str = Query("any"),
    ctx: TenantContext = Depends(require_permission(Permission.APPOINTMENT_READ)),
    session: AsyncSession = Depends(get_session),
) -> AvailabilityOut:
    rules = await service.get_rules(session, ctx.tenant)
    slots, degraded = await service.find_slots(
        session, ctx.tenant, day=day, part_of_day=part_of_day,
        limit=50,   # an API client can render more than a voice agent can say
    )
    return AvailabilityOut(
        date=day.isoformat(),
        timezone=rules.timezone,
        degraded=degraded,
        slots=[
            SlotOut(
                start=slot.start.isoformat(),
                end=slot.end.isoformat(),
                spoken=slot.spoken(),
            )
            for slot in slots
        ],
    )


@router.get("", response_model=AppointmentListOut)
@router.get("/", response_model=AppointmentListOut)
async def list_appointments(
    status: str | None = Query(None),
    upcoming: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
    ctx: TenantContext = Depends(require_permission(Permission.APPOINTMENT_READ)),
    session: AsyncSession = Depends(get_session),
) -> AppointmentListOut:
    query = select(Appointment).where(Appointment.tenant_id == ctx.tenant_id)
    if status:
        try:
            query = query.where(Appointment.status == AppointmentStatus(status.lower()))
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Unknown status {status!r}")
    if upcoming:
        query = query.where(Appointment.starts_at >= now_utc() - timedelta(hours=1))

    rows = (
        (await session.execute(query.order_by(Appointment.starts_at).limit(limit)))
        .scalars()
        .all()
    )
    return AppointmentListOut(
        appointments=[to_out(row) for row in rows], total=len(rows)
    )


@router.post("", response_model=AppointmentOut, status_code=201)
@router.post("/", response_model=AppointmentOut, status_code=201)
async def create_appointment(
    body: BookIn,
    ctx: TenantContext = Depends(require_permission(Permission.APPOINTMENT_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> AppointmentOut:
    rules = await service.get_rules(session, ctx.tenant)
    start = _resolve_local_input(body.starts_at_local, rules.timezone)

    result = await service.book(
        session, ctx.tenant,
        service.BookingRequest(
            tenant_id=ctx.tenant_id,
            start=start,
            customer_name=body.customer_name,
            customer_phone=body.customer_phone,
            customer_email=body.customer_email,
            reason=body.reason,
            lead_id=body.lead_id,
            request_id=body.request_id,
        ),
    )
    _raise_for_outcome(result)

    appointment = await session.get(Appointment, uuid.UUID(result.appointment_id))
    return to_out(appointment)


@router.get("/{appointment_id}", response_model=AppointmentOut)
async def get_appointment(
    appointment_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.APPOINTMENT_READ)),
    session: AsyncSession = Depends(get_session),
) -> AppointmentOut:
    # 404s on another tenant's id rather than 403ing, so ids cannot be probed.
    return to_out(await get_owned(session, Appointment, appointment_id, ctx))


@router.patch("/{appointment_id}", response_model=AppointmentOut)
async def reschedule_appointment(
    appointment_id: uuid.UUID,
    body: RescheduleIn,
    ctx: TenantContext = Depends(require_permission(Permission.APPOINTMENT_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> AppointmentOut:
    appointment = await get_owned(session, Appointment, appointment_id, ctx)
    rules = await service.get_rules(session, ctx.tenant)
    start = _resolve_local_input(body.starts_at_local, rules.timezone)

    result = await service.reschedule(
        session, ctx.tenant, appointment, start, reason=body.reason
    )
    _raise_for_outcome(result)

    await record_audit(
        session, action=AuditAction.APPOINTMENT_RESCHEDULED, tenant_id=ctx.tenant_id,
        actor_user_id=ctx.user_id, actor_email=ctx.user.email,
        detail={"appointment_id": str(appointment_id)},
    )
    await session.commit()
    await session.refresh(appointment)
    return to_out(appointment)


@router.post("/{appointment_id}/cancel", response_model=AppointmentOut)
async def cancel_appointment(
    appointment_id: uuid.UUID,
    body: CancelIn | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.APPOINTMENT_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> AppointmentOut:
    appointment = await get_owned(session, Appointment, appointment_id, ctx)
    result = await service.cancel(
        session, ctx.tenant, appointment,
        reason=(body.reason if body else ""),
        cancelled_by=f"user:{ctx.user.email}",
    )
    _raise_for_outcome(result)

    await record_audit(
        session, action=AuditAction.APPOINTMENT_CANCELLED, tenant_id=ctx.tenant_id,
        actor_user_id=ctx.user_id, actor_email=ctx.user.email,
        detail={"appointment_id": str(appointment_id)},
    )
    await session.commit()
    await session.refresh(appointment)
    return to_out(appointment)


@router.post("/{appointment_id}/no-show", response_model=AppointmentOut)
async def mark_no_show(
    appointment_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.APPOINTMENT_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> AppointmentOut:
    appointment = await get_owned(session, Appointment, appointment_id, ctx)
    await service.mark_no_show(session, appointment)
    await session.refresh(appointment)
    return to_out(appointment)


# ----------------------------------------------------------- calendar routes ---

@calendar_router.get("/providers", response_model=ProviderCatalogueOut)
async def list_providers(
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_READ)),
) -> ProviderCatalogueOut:
    return ProviderCatalogueOut(providers=[
        ProviderInfo(
            provider=provider.value,
            capabilities=sorted(c.value for c in capabilities_of(provider)),
            credential_fields=list(CREDENTIAL_FIELDS[provider]),
            config_fields=list(CONFIG_FIELDS[provider]),
        )
        for provider in CalendarProviderType
    ])


@calendar_router.get("/integrations", response_model=list[CalendarIntegrationOut])
async def list_integrations(
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_READ)),
    session: AsyncSession = Depends(get_session),
) -> list[CalendarIntegrationOut]:
    rows = (
        (
            await session.execute(
                select(CalendarIntegration)
                .where(CalendarIntegration.tenant_id == ctx.tenant_id)
                .order_by(CalendarIntegration.provider)
            )
        )
        .scalars()
        .all()
    )
    return [integration_out(row) for row in rows]


@calendar_router.put(
    "/integrations/{provider}", response_model=CalendarIntegrationOut
)
async def upsert_integration(
    provider: str,
    body: CalendarIntegrationIn,
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> CalendarIntegrationOut:
    provider_type = _parse_provider(provider)
    config = _validated_config(provider_type, body.config)

    integration = await service.get_integration(
        session, tenant_id=ctx.tenant_id, provider=provider_type
    )
    creating = integration is None
    if creating:
        integration = CalendarIntegration(
            tenant_id=ctx.tenant_id, provider=provider_type, config={}
        )
        session.add(integration)

    integration.is_enabled = body.is_enabled
    integration.is_primary = body.is_primary
    integration.config = config
    integration.updated_at = now_utc()

    if body.credentials is not None:
        _store_credentials(integration, ctx, provider_type, body.credentials)

    if body.is_primary:
        await _demote_other_primaries(session, ctx.tenant_id, provider_type)

    await session.commit()
    await session.refresh(integration)

    await record_audit(
        session, action=AuditAction.CALENDAR_CONNECTED, tenant_id=ctx.tenant_id,
        actor_user_id=ctx.user_id, actor_email=ctx.user.email,
        # Provider and the *names* of the fields supplied -- never a value.
        detail={
            "provider": provider_type.value,
            "provided_field_names": sorted(body.credentials or {}),
            "config_keys": sorted(config),
            "created": creating,
        },
    )
    await session.commit()

    log.info(
        "calendar.integration_saved", tenant_id=str(ctx.tenant_id),
        provider=provider_type.value, outcome="created" if creating else "updated",
    )
    return integration_out(integration)


async def _demote_other_primaries(
    session: AsyncSession, tenant_id: uuid.UUID, keep: CalendarProviderType
) -> None:
    rows = (
        (
            await session.execute(
                select(CalendarIntegration).where(
                    CalendarIntegration.tenant_id == tenant_id,
                    CalendarIntegration.provider != keep,
                    CalendarIntegration.is_primary.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        row.is_primary = False


def _store_credentials(integration, ctx, provider_type, credentials) -> None:
    """
    Encrypt with STEP 5's cipher and key ring.

    One cipher for the whole product: same envelope, same rotation story, same
    `(tenant, provider)` associated data. There is no code path that stores a
    calendar token in any other form — if encryption is not configured this
    returns 503 rather than falling back to plaintext.
    """
    from app.integrations.crm import crypto

    allowed = set(CREDENTIAL_FIELDS[provider_type])
    unknown = sorted(set(credentials) - allowed)
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unknown credential field(s) for {provider_type.value}: "
                f"{', '.join(unknown)}. Allowed: {', '.join(sorted(allowed)) or 'none'}"
            ),
        )

    key_ring = crypto.key_ring_from_settings()
    if key_ring is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Calendar credential encryption is not configured on this "
                "instance. Set CRM_ENCRYPTION_KEYS before connecting a provider."
            ),
        )
    try:
        envelope, key_id = crypto.encrypt_credentials(
            dict(credentials), tenant_id=str(ctx.tenant_id),
            provider=provider_type.value, key_ring=key_ring,
        )
    except crypto.CredentialCryptoError as exc:
        raise HTTPException(status_code=503, detail=f"Cannot store credentials: {exc}")

    integration.credentials_encrypted = envelope
    integration.credentials_key_id = key_id
    integration.credentials_updated_at = now_utc()


def _validated_config(provider_type: CalendarProviderType, config: dict) -> dict:
    allowed = set(CONFIG_FIELDS[provider_type])
    unknown = sorted(set(config or {}) - allowed)
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unknown config field(s) for {provider_type.value}: "
                f"{', '.join(unknown)}. Allowed: {', '.join(sorted(allowed)) or 'none'}"
            ),
        )
    cleaned = {k: v for k, v in (config or {}).items() if v not in (None, "")}

    if provider_type is CalendarProviderType.CALCOM and "event_type_id" not in cleaned:
        raise HTTPException(
            status_code=422,
            detail="Cal.com requires config.event_type_id; every booking is "
                   "made against an event type",
        )

    # SSRF guard (Step 9): `base_url` receives a bearer access token and
    # `token_url` receives the tenant's client_secret + refresh_token, so both
    # must be https and must not point at loopback, link-local, RFC 1918, the
    # cloud metadata endpoint, or special-use hostnames.
    for field in ("base_url", "token_url"):
        value = cleaned.get(field)
        if value is not None:
            try:
                validate_outbound_url(str(value), require_https=True)
            except OutboundUrlError as exc:
                raise HTTPException(status_code=422, detail=str(exc))

    return cleaned


@calendar_router.post(
    "/integrations/{provider}/test", response_model=dict
)
async def test_integration(
    provider: str,
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """
    Live connection test.

    Always 200. A tenant pressing "Test" with a dead token gets a diagnosis,
    not a 500 — and never the provider's raw response.
    """
    provider_type = _parse_provider(provider)
    integration = await service.get_integration(
        session, tenant_id=ctx.tenant_id, provider=provider_type
    )
    if integration is None:
        raise HTTPException(status_code=404, detail="Integration is not configured")

    result = await service.check_health(session, ctx.tenant, integration)
    return {
        "connected": result.connected,
        "provider": result.provider,
        "latency_ms": result.latency_ms,
        "safe_message": result.safe_message,
    }


@calendar_router.delete("/integrations/{provider}", status_code=204)
async def delete_integration(
    provider: str,
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    provider_type = _parse_provider(provider)
    integration = await service.get_integration(
        session, tenant_id=ctx.tenant_id, provider=provider_type
    )
    if integration is None:
        raise HTTPException(status_code=404, detail="Integration is not configured")

    await session.delete(integration)
    await session.commit()

    await record_audit(
        session, action=AuditAction.CALENDAR_DISCONNECTED, tenant_id=ctx.tenant_id,
        actor_user_id=ctx.user_id, actor_email=ctx.user.email,
        detail={"provider": provider_type.value},
    )
    await session.commit()
    return None


# ------------------------------------------------------------- policy routes ---

@calendar_router.get("/policy", response_model=PolicyOut)
async def get_scheduling_policy(
    ctx: TenantContext = Depends(require_permission(Permission.APPOINTMENT_READ)),
    session: AsyncSession = Depends(get_session),
) -> PolicyOut:
    stored = await service.get_policy(session, ctx.tenant_id)
    rules = policy_engine.rules_from(ctx.tenant, stored)

    return PolicyOut(
        timezone=rules.timezone,
        weekly_hours=(stored.weekly_hours if stored else _legacy_hours(rules)),
        holidays=list(stored.holidays) if stored else [],
        blocked_periods=list(stored.blocked_periods) if stored else [],
        slot_minutes=rules.slot_minutes,
        slot_interval_minutes=rules.slot_interval_minutes,
        buffer_before_minutes=rules.buffer_before_minutes,
        buffer_after_minutes=rules.buffer_after_minutes,
        minimum_notice_minutes=rules.minimum_notice_minutes,
        booking_horizon_days=rules.booking_horizon_days,
        max_slots_offered=rules.max_slots_offered,
        allow_outside_business_hours=rules.allow_outside_business_hours,
        require_provider_confirmation=rules.require_provider_confirmation,
        configured=stored is not None,
    )


def _legacy_hours(rules) -> dict:
    """Render the Tenant-column fallback in the policy's own shape."""
    return {
        day: [[opens.strftime("%H:%M"), closes.strftime("%H:%M")]]
        for day, intervals in rules.weekly_hours.items()
        for opens, closes in intervals
    }


@calendar_router.put("/policy", response_model=PolicyOut)
async def update_scheduling_policy(
    body: PolicyIn,
    ctx: TenantContext = Depends(require_permission(Permission.APPOINTMENT_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> PolicyOut:
    if body.timezone is not None and not is_valid_zone(body.timezone):
        raise HTTPException(
            status_code=422,
            detail=(
                f"{body.timezone!r} is not a known IANA timezone. Use e.g. "
                f"America/New_York, Europe/London, Asia/Dhaka."
            ),
        )

    # Validate on write, so a bad schedule is a 422 now rather than a mystery
    # at three in the morning when a call comes in.
    try:
        policy_engine.parse_weekly_hours(body.weekly_hours)
        policy_engine.parse_holidays(body.holidays)
        policy_engine.parse_blocked(
            body.blocked_periods, body.timezone or ctx.tenant.timezone or "UTC"
        )
    except policy_engine.PolicyError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    if body.slot_interval_minutes > body.slot_minutes:
        raise HTTPException(
            status_code=422,
            detail=(
                "slot_interval_minutes may not exceed slot_minutes, or the "
                "grid would skip bookable time"
            ),
        )

    stored = await service.get_policy(session, ctx.tenant_id)
    if stored is None:
        stored = SchedulingPolicy(tenant_id=ctx.tenant_id)
        session.add(stored)

    stored.weekly_hours = body.weekly_hours or {}
    stored.holidays = body.holidays or []
    stored.blocked_periods = body.blocked_periods or []
    stored.slot_minutes = body.slot_minutes
    stored.slot_interval_minutes = body.slot_interval_minutes
    stored.buffer_before_minutes = body.buffer_before_minutes
    stored.buffer_after_minutes = body.buffer_after_minutes
    stored.minimum_notice_minutes = body.minimum_notice_minutes
    stored.booking_horizon_days = body.booking_horizon_days
    stored.max_slots_offered = body.max_slots_offered
    stored.allow_outside_business_hours = body.allow_outside_business_hours
    stored.require_provider_confirmation = body.require_provider_confirmation

    if body.timezone is not None:
        ctx.tenant.timezone = body.timezone

    await session.commit()
    return await get_scheduling_policy(ctx=ctx, session=session)
````

### File 02: `app/api/integration_routes.py`

SHA-256: `e88a14ecb33dc127c9b5379fd223f371d78ea0e511603f94ed72becdb68206ba`

````
"""
CRM integration configuration API.

    GET    /api/integrations/crm                     list this tenant's integrations
    GET    /api/integrations/crm/providers           catalogue + capabilities
    GET    /api/integrations/crm/{provider}          one integration
    PUT    /api/integrations/crm/{provider}          connect or update
    DELETE /api/integrations/crm/{provider}          delete outright
    POST   /api/integrations/crm/{provider}/test     health check
    POST   /api/integrations/crm/{provider}/disconnect  keep config, drop creds
    GET    /api/integrations/crm/syncs               sync status feed

Two rules run through the whole file.

**The tenant is never a parameter.** It comes from `ctx.tenant_id`, which
comes from the verified JWT. There is no path that reads a tenant id from a
body, a query string or a header — requirement 6 asks for that explicitly, and
a test asserts a body containing `tenant_id` changes nothing.

**Responses are allowlists.** `IntegrationOut` names every field that may
reach a client. `credentials_encrypted` is not among them, and neither is
anything derived from it. A field reaches a dashboard because someone wrote it
out here, not because it happened to be on the row.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, record_audit, require_permission
from app.auth.permissions import Permission
from app.core.logging import log
from app.core.ssrf import OutboundUrlError, validate_outbound_url
from app.db.models import (
    AuditAction,
    CrmEvent,
    CrmIntegration,
    CrmProviderType,
    CrmSync,
    CrmSyncStatus,
)
from app.db.session import get_session
from app.integrations.crm import crypto, service
from app.integrations.crm.mapping import MappingError, validate_field_mappings
from app.integrations.crm.providers.webhook import generate_signing_secret
from app.integrations.crm.registry import capabilities_of

router = APIRouter(prefix="/api/integrations/crm", tags=["integrations"])


# ----------------------------------------------------------------- schemas ---

#: Credential fields each provider accepts. An allowlist, so a tenant cannot
#: stuff arbitrary keys into the encrypted blob and so the API can tell them
#: exactly what is expected.
CREDENTIAL_FIELDS: dict[CrmProviderType, tuple[str, ...]] = {
    CrmProviderType.GOHIGHLEVEL: ("access_token",),
    CrmProviderType.HUBSPOT: ("access_token",),
    CrmProviderType.JOBBER: ("access_token", "refresh_token"),
    CrmProviderType.WEBHOOK: ("signing_secret",),
}

#: Non-secret settings each provider accepts. Everything here is returned by
#: the API, so nothing credential-shaped may be added to these tuples.
CONFIG_FIELDS: dict[CrmProviderType, tuple[str, ...]] = {
    CrmProviderType.GOHIGHLEVEL: ("location_id", "calendar_id", "base_url"),
    CrmProviderType.HUBSPOT: ("base_url",),
    CrmProviderType.JOBBER: ("api_version", "base_url"),
    CrmProviderType.WEBHOOK: ("url",),
}


class IntegrationOut(BaseModel):
    """
    The tenant-visible view.

    Requirement 15 lists what a dashboard may see and what it may not. The
    absent fields are the point of this class: no access token, no refresh
    token, no client secret, no encryption key, no ciphertext, and no key id
    beyond the boolean below.
    """

    provider: str
    is_enabled: bool
    connected: bool = Field(
        description="whether credentials are stored -- not whether they work"
    )
    capabilities: list[str] = Field(default_factory=list)
    config: dict = Field(default_factory=dict)
    field_mappings: dict = Field(default_factory=dict)
    subscribed_events: list[str] = Field(default_factory=list)
    share_transcripts: bool = False
    #: When credentials were last written. Named `connected_at` rather than
    #: `credentials_updated_at` on purpose: a response field whose name starts
    #: with "credentials" invites someone to add a sibling that holds the
    #: actual credentials. There is a test asserting no field in this model is
    #: named like a secret.
    connected_at: str | None = None
    last_health_check_at: str | None = None
    last_health_ok: bool | None = None
    last_error: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class IntegrationListOut(BaseModel):
    integrations: list[IntegrationOut]


class ProviderInfo(BaseModel):
    provider: str
    capabilities: list[str]
    credential_fields: list[str]
    config_fields: list[str]


class ProviderCatalogueOut(BaseModel):
    providers: list[ProviderInfo]


class IntegrationIn(BaseModel):
    """
    Connect or update payload.

    `credentials` is write-only: it goes in, it is encrypted, and no response
    model can echo it back. Omitting it on an update leaves the stored
    credentials untouched, so a tenant editing their location id does not have
    to re-paste a token.
    """

    is_enabled: bool = True
    credentials: dict[str, str] | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    field_mappings: dict[str, str] = Field(default_factory=dict)
    subscribed_events: list[str] = Field(default_factory=list)
    share_transcripts: bool = False

    @field_validator("credentials")
    @classmethod
    def _reject_empty_values(cls, value):
        if value is None:
            return None
        for key, item in value.items():
            if not isinstance(item, str) or not item.strip():
                raise ValueError(f"credential {key!r} must be a non-empty string")
        return value


class HealthOut(BaseModel):
    """Requirement 24's normalized shape."""

    connected: bool
    provider: str
    latency_ms: float
    safe_message: str


class SyncOut(BaseModel):
    id: uuid.UUID
    provider: str
    entity_type: str
    entity_id: uuid.UUID
    event_type: str | None = None
    status: str
    external_id: str | None = None
    attempt_count: int
    last_attempt_at: str | None = None
    next_attempt_at: str | None = None
    synced_at: str | None = None
    last_error: str | None = None
    last_error_code: str | None = None


class SyncListOut(BaseModel):
    syncs: list[SyncOut]
    total: int


# ------------------------------------------------------------- serializers ---

def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def to_out(integration: CrmIntegration) -> IntegrationOut:
    return IntegrationOut(
        provider=integration.provider.value,
        is_enabled=integration.is_enabled,
        connected=bool(integration.credentials_encrypted),
        capabilities=sorted(c.value for c in capabilities_of(integration.provider)),
        config=dict(integration.config or {}),
        field_mappings=dict(integration.field_mappings or {}),
        subscribed_events=list(integration.subscribed_events or []),
        share_transcripts=bool(integration.share_transcripts),
        connected_at=_iso(integration.credentials_updated_at),
        last_health_check_at=_iso(integration.last_health_check_at),
        last_health_ok=integration.last_health_ok,
        last_error=integration.last_error,
        created_at=_iso(integration.created_at),
        updated_at=_iso(integration.updated_at),
    )


def _parse_provider(provider: str) -> CrmProviderType:
    try:
        return CrmProviderType(provider.lower())
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Unknown provider. Supported: "
                f"{', '.join(p.value for p in CrmProviderType)}"
            ),
        )


async def _owned(
    session: AsyncSession, ctx: TenantContext, provider: CrmProviderType
) -> CrmIntegration:
    """
    Fetch this tenant's integration, or 404.

    404 rather than 403 on someone else's row, matching `get_owned()` from
    STEP 2: a different status code would confirm that another tenant has that
    provider connected.
    """
    integration = await service.get_integration(
        session, tenant_id=ctx.tenant_id, provider=provider
    )
    if integration is None:
        raise HTTPException(status_code=404, detail="Integration is not configured")
    return integration


# ------------------------------------------------------------------ routes ---

@router.get("/providers", response_model=ProviderCatalogueOut)
async def list_providers(
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_READ)),
) -> ProviderCatalogueOut:
    """
    What can be connected, and what each one can do.

    Serving capabilities means a dashboard can grey out "sync appointments"
    for Jobber instead of offering it and producing a permanent failure.
    """
    return ProviderCatalogueOut(providers=[
        ProviderInfo(
            provider=provider.value,
            capabilities=sorted(c.value for c in capabilities_of(provider)),
            credential_fields=list(CREDENTIAL_FIELDS[provider]),
            config_fields=list(CONFIG_FIELDS[provider]),
        )
        for provider in CrmProviderType
    ])


@router.get("", response_model=IntegrationListOut)
@router.get("/", response_model=IntegrationListOut)
async def list_integrations(
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_READ)),
    session: AsyncSession = Depends(get_session),
) -> IntegrationListOut:
    rows = (
        (
            await session.execute(
                select(CrmIntegration)
                .where(CrmIntegration.tenant_id == ctx.tenant_id)
                .order_by(CrmIntegration.provider)
            )
        )
        .scalars()
        .all()
    )
    return IntegrationListOut(integrations=[to_out(row) for row in rows])


@router.get("/syncs", response_model=SyncListOut)
async def list_syncs(
    status: str | None = Query(None, description="filter by sync status"),
    provider: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_READ)),
    session: AsyncSession = Depends(get_session),
) -> SyncListOut:
    """
    Sync status feed (requirement 15).

    `last_error` is included because an operator needs to know *why* a sync
    failed, and it is safe to include because `errors.safe_message` scrubbed
    it before it was ever written.
    """
    query = select(CrmSync).where(CrmSync.tenant_id == ctx.tenant_id)
    if status:
        try:
            query = query.where(CrmSync.status == CrmSyncStatus(status.lower()))
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Unknown status {status!r}")
    if provider:
        query = query.where(CrmSync.provider == _parse_provider(provider))

    rows = (
        (await session.execute(query.order_by(CrmSync.created_at.desc()).limit(limit)))
        .scalars()
        .all()
    )

    # One extra query rather than a join, to attach the event type without
    # widening the sync model. The id list is already tenant-scoped.
    event_types: dict[uuid.UUID, str] = {}
    if rows:
        events = (
            (
                await session.execute(
                    select(CrmEvent).where(
                        CrmEvent.tenant_id == ctx.tenant_id,
                        CrmEvent.id.in_([r.event_id for r in rows]),
                    )
                )
            )
            .scalars()
            .all()
        )
        event_types = {e.id: e.event_type.value for e in events}

    return SyncListOut(
        total=len(rows),
        syncs=[
            SyncOut(
                id=row.id, provider=row.provider.value,
                entity_type=row.entity_type.value, entity_id=row.entity_id,
                event_type=event_types.get(row.event_id),
                status=row.status.value, external_id=row.external_id,
                attempt_count=row.attempt_count,
                last_attempt_at=_iso(row.last_attempt_at),
                next_attempt_at=_iso(row.next_attempt_at),
                synced_at=_iso(row.synced_at),
                last_error=row.last_error, last_error_code=row.last_error_code,
            )
            for row in rows
        ],
    )


@router.get("/{provider}", response_model=IntegrationOut)
async def get_integration_route(
    provider: str,
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_READ)),
    session: AsyncSession = Depends(get_session),
) -> IntegrationOut:
    return to_out(await _owned(session, ctx, _parse_provider(provider)))


@router.put("/{provider}", response_model=IntegrationOut)
async def upsert_integration(
    provider: str,
    body: IntegrationIn,
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> IntegrationOut:
    """
    Connect a provider, or update an existing connection.

    Credentials are encrypted before the row is written. There is no code path
    that stores them in any other form: if encryption is not configured, this
    returns 503 rather than falling back to plaintext.
    """
    provider_type = _parse_provider(provider)

    config = _validated_config(provider_type, body.config)
    mappings = _validated_mappings(body.field_mappings)
    events = _validated_events(body.subscribed_events)

    integration = await service.get_integration(
        session, tenant_id=ctx.tenant_id, provider=provider_type
    )
    creating = integration is None
    if creating:
        integration = CrmIntegration(
            tenant_id=ctx.tenant_id, provider=provider_type, config={}
        )
        session.add(integration)

    integration.is_enabled = body.is_enabled
    integration.config = config
    integration.field_mappings = mappings
    integration.subscribed_events = events
    integration.share_transcripts = body.share_transcripts
    integration.updated_at = datetime.utcnow()

    credentials = _credentials_for(provider_type, body, creating)
    if credentials is not None:
        _store_credentials(integration, ctx, provider_type, credentials)

    await session.commit()
    await session.refresh(integration)

    await record_audit(
        session,
        action=(
            AuditAction.INTEGRATION_CONNECTED if creating
            else AuditAction.INTEGRATION_UPDATED
        ),
        tenant_id=ctx.tenant_id, actor_user_id=ctx.user_id,
        actor_email=ctx.user.email,
        # Provider and the *names* of the fields supplied -- never a value.
        # Requirement 22 asks for a test that no secret reaches an audit row.
        detail={
            "provider": provider_type.value,
            "provided_field_names": sorted(credentials or {}),
            "config_keys": sorted(config),
            "enabled": body.is_enabled,
        },
    )
    await session.commit()

    log.info(
        "crm.integration_saved", tenant_id=str(ctx.tenant_id),
        provider=provider_type.value, outcome="created" if creating else "updated",
    )
    return to_out(integration)


def _credentials_for(
    provider_type: CrmProviderType, body: IntegrationIn, creating: bool
) -> dict[str, str] | None:
    """
    Decide what credential bundle to store, if any.

    `None` means "leave what is already there" — an update that only changes
    a location id must not wipe the token.
    """
    supplied = body.credentials
    if supplied is not None:
        allowed = set(CREDENTIAL_FIELDS[provider_type])
        unknown = sorted(set(supplied) - allowed)
        if unknown:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Unknown credential field(s) for {provider_type.value}: "
                    f"{', '.join(unknown)}. Allowed: {', '.join(sorted(allowed))}"
                ),
            )
        return dict(supplied)

    if creating and provider_type is CrmProviderType.WEBHOOK:
        # A webhook's "credential" is a signing secret that we generate rather
        # than the tenant supplying it. Generating it here means an unsigned
        # webhook integration cannot exist, which is what makes the receiver's
        # signature check meaningful.
        return {"signing_secret": generate_signing_secret()}

    return None


def _store_credentials(
    integration: CrmIntegration, ctx: TenantContext,
    provider_type: CrmProviderType, credentials: dict[str, str],
) -> None:
    key_ring = crypto.key_ring_from_settings()
    if key_ring is None:
        # Never silently degrade to plaintext.
        raise HTTPException(
            status_code=503,
            detail=(
                "CRM credential encryption is not configured on this instance. "
                "Set CRM_ENCRYPTION_KEYS before connecting a provider."
            ),
        )
    try:
        envelope, key_id = crypto.encrypt_credentials(
            credentials, tenant_id=str(ctx.tenant_id),
            provider=provider_type.value, key_ring=key_ring,
        )
    except crypto.CredentialCryptoError as exc:
        raise HTTPException(status_code=503, detail=f"Cannot store credentials: {exc}")

    integration.credentials_encrypted = envelope
    integration.credentials_key_id = key_id
    integration.credentials_updated_at = datetime.utcnow()


def _validated_config(
    provider_type: CrmProviderType, config: dict[str, Any]
) -> dict[str, Any]:
    allowed = set(CONFIG_FIELDS[provider_type])
    unknown = sorted(set(config or {}) - allowed)
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unknown config field(s) for {provider_type.value}: "
                f"{', '.join(unknown)}. Allowed: {', '.join(sorted(allowed))}"
            ),
        )
    cleaned = {k: v for k, v in (config or {}).items() if v not in (None, "")}

    if provider_type is CrmProviderType.WEBHOOK:
        url = cleaned.get("url", "")
        if not url:
            raise HTTPException(
                status_code=422, detail="A webhook integration requires config.url"
            )
        if not str(url).startswith("https://"):
            raise HTTPException(
                status_code=422,
                detail=(
                    "config.url must be https -- webhook payloads carry customer "
                    "phone numbers and call summaries"
                ),
            )
        try:
            # SSRF guard: the webhook destination must not be loopback,
            # link-local, RFC 1918, the cloud metadata endpoint, or a
            # special-use hostname.
            validate_outbound_url(str(url), require_https=True)
        except OutboundUrlError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    # `base_url` carries a bearer access token in the Authorization header, so
    # it gets the same SSRF guard plus a mandatory https scheme. The official
    # provider hosts (api.hubapi.com, services.leadconnectorhq.com,
    # api.getjobber.com) are https by default, so this blocks only what a
    # tenant should never be able to set.
    base_url = cleaned.get("base_url")
    if base_url is not None:
        try:
            validate_outbound_url(str(base_url), require_https=True)
        except OutboundUrlError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    return cleaned


def _validated_mappings(mappings: dict[str, str]) -> dict[str, str]:
    try:
        return validate_field_mappings(mappings)
    except MappingError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


def _validated_events(events: list[str]) -> list[str]:
    from app.db.models import CrmEventType

    known = {e.value for e in CrmEventType}
    unknown = sorted(set(events or []) - known)
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unknown event type(s): {', '.join(unknown)}. "
                f"Known: {', '.join(sorted(known))}"
            ),
        )
    return list(events or [])


@router.post("/{provider}/test", response_model=HealthOut)
async def test_integration(
    provider: str,
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_READ)),
    session: AsyncSession = Depends(get_session),
) -> HealthOut:
    """
    Live connection test.

    Returns 200 with `connected=false` on a bad credential rather than an
    error status: this is a diagnostic, and the answer "your token is
    rejected" is a successful diagnosis.
    """
    provider_type = _parse_provider(provider)
    integration = await _owned(session, ctx, provider_type)
    result = await service.check_health(session, integration)

    await record_audit(
        session, action=AuditAction.INTEGRATION_TESTED, tenant_id=ctx.tenant_id,
        actor_user_id=ctx.user_id, actor_email=ctx.user.email,
        detail={"provider": provider_type.value, "connected": result.connected},
    )
    await session.commit()

    return HealthOut(
        connected=result.connected, provider=result.provider,
        latency_ms=result.latency_ms, safe_message=result.safe_message,
    )


@router.post("/{provider}/disconnect", response_model=IntegrationOut)
async def disconnect_integration(
    provider: str,
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> IntegrationOut:
    """
    Drop the credentials, keep the configuration.

    Distinct from DELETE on purpose: a tenant rotating a token, or pausing an
    integration during a CRM migration, should not have to re-enter their
    location id and field mappings afterwards.
    """
    provider_type = _parse_provider(provider)
    integration = await _owned(session, ctx, provider_type)

    integration.credentials_encrypted = None
    integration.credentials_key_id = None
    integration.credentials_updated_at = None
    integration.is_enabled = False
    integration.last_health_ok = None
    integration.last_error = None
    await session.commit()
    await session.refresh(integration)

    await record_audit(
        session, action=AuditAction.INTEGRATION_DISCONNECTED,
        tenant_id=ctx.tenant_id, actor_user_id=ctx.user_id,
        actor_email=ctx.user.email,
        detail={"provider": provider_type.value, "kept_config": True},
    )
    await session.commit()
    return to_out(integration)


@router.delete("/{provider}", status_code=204)
async def delete_integration(
    provider: str,
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """
    Remove the integration entirely.

    Sync history is left in place. Those rows are the record of what was sent
    to a customer's CRM, and deleting them because someone unplugged the
    connector would destroy the audit trail exactly when it matters.
    """
    provider_type = _parse_provider(provider)
    integration = await _owned(session, ctx, provider_type)

    await session.delete(integration)
    await session.commit()

    await record_audit(
        session, action=AuditAction.INTEGRATION_DISCONNECTED,
        tenant_id=ctx.tenant_id, actor_user_id=ctx.user_id,
        actor_email=ctx.user.email,
        detail={"provider": provider_type.value, "deleted": True},
    )
    await session.commit()
    return None
````

### File 03: `app/telephony/call_state.py`

SHA-256: `99f16312abc6a6bf2d7be66f9768e7d7382f67428b35d540e1efa66c7a19cd3d`

````
"""
The single source of truth for call lifecycle state.

Before this module, call status was written from four different places --
`incoming_call`, `media_stream`'s exception handler, `call_status`, and
`place_call` -- each with its own idea of what was legal. The status webhook in
particular did:

    call.status = _TERMINAL_STATUS.get(CallStatus_, CallStatus.COMPLETED)

which means a duplicate or late Twilio callback would happily rewrite a
TRANSFERRED call as COMPLETED, and an unrecognised status string would silently
be recorded as a successful completion. Twilio retries webhooks, and callbacks
from a parent call and a transfer leg can arrive out of order, so both of those
things happen in production rather than in theory.

Everything here is pure state-machine logic over a `Call` row. There are no
HTTP concerns and no provider SDK imports, so the rules can be tested directly.

Terminology
-----------
*Terminal* states are COMPLETED, FAILED, NO_ANSWER and CANCELLED. Once a call reaches one,
nothing may move it anywhere else.

TRANSFERRED is deliberately **not** terminal: a transferred call is still up,
just with a human on it, and it must still be allowed to reach COMPLETED (or
FAILED) when the human hangs up.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import structlog

from app.core import observability
from app.db.models import Call, CallStatus

log = structlog.get_logger()


# --------------------------------------------------------------- the graph ---

#: Every transition this application considers legal.
ALLOWED_TRANSITIONS: dict[CallStatus, frozenset[CallStatus]] = {
    CallStatus.RINGING: frozenset(
        {
            CallStatus.IN_PROGRESS,
            CallStatus.NO_ANSWER,
            CallStatus.FAILED,
            CallStatus.CANCELLED,
            # Twilio can report a call as completed straight from ringing when the
            # caller hangs up during ringback.
            CallStatus.COMPLETED,
        }
    ),
    CallStatus.IN_PROGRESS: frozenset(
        {
            CallStatus.TRANSFERRED,
            CallStatus.COMPLETED,
            CallStatus.FAILED,
        }
    ),
    CallStatus.TRANSFERRED: frozenset(
        {
            # The human hung up, or the transfer leg died after connecting.
            CallStatus.COMPLETED,
            CallStatus.FAILED,
        }
    ),
    # Terminal.
    CallStatus.COMPLETED: frozenset(),
    CallStatus.FAILED: frozenset(),
    CallStatus.NO_ANSWER: frozenset(),
    CallStatus.CANCELLED: frozenset(),
}

TERMINAL_STATUSES: frozenset[CallStatus] = frozenset(
    {
        CallStatus.COMPLETED,
        CallStatus.FAILED,
        CallStatus.NO_ANSWER,
        CallStatus.CANCELLED,
    }
)

#: Provider status string -> our enum. Twilio's vocabulary, lowercased.
PROVIDER_STATUS_MAP: dict[str, CallStatus] = {
    "queued": CallStatus.RINGING,
    "initiated": CallStatus.RINGING,
    "ringing": CallStatus.RINGING,
    "in-progress": CallStatus.IN_PROGRESS,
    "in_progress": CallStatus.IN_PROGRESS,
    "answered": CallStatus.IN_PROGRESS,
    "completed": CallStatus.COMPLETED,
    "no-answer": CallStatus.NO_ANSWER,
    "no_answer": CallStatus.NO_ANSWER,
    "busy": CallStatus.FAILED,
    "failed": CallStatus.FAILED,
    "canceled": CallStatus.CANCELLED,
    "cancelled": CallStatus.CANCELLED,
}

#: Provider statuses that explain *why* a call ended badly. Stored verbatim so
#: support can tell "nobody picked up" apart from "the number is disconnected".
FAILURE_REASONS = {"busy", "failed", "no-answer", "no_answer"}


def is_terminal(status: CallStatus) -> bool:
    return status in TERMINAL_STATUSES


def can_transition(current: CallStatus, target: CallStatus) -> bool:
    """A transition to the same state is always allowed (it is a no-op)."""
    if current == target:
        return True
    return target in ALLOWED_TRANSITIONS.get(current, frozenset())


def map_provider_status(raw: str | None) -> CallStatus | None:
    """
    Translate a provider status string. Returns None for anything unknown.

    Returning None rather than defaulting to COMPLETED is the whole point: an
    unrecognised string must not be able to terminate a live call.
    """
    if not raw:
        return None
    return PROVIDER_STATUS_MAP.get(raw.strip().lower())


# ------------------------------------------------------------------ result ---


@dataclass(frozen=True)
class TransitionResult:
    """What `apply_status` actually did. Never raises; always reports."""

    applied: bool
    previous: CallStatus
    current: CallStatus
    reason: str

    @property
    def ignored(self) -> bool:
        return not self.applied


# ------------------------------------------------------------------- apply ---


def apply_status(
    call: Call,
    target: CallStatus,
    *,
    reason: str = "",
    duration_seconds: float | None = None,
    ended_at: datetime | None = None,
    source: str = "unknown",
) -> TransitionResult:
    """
    Move `call` to `target` if that is legal. Idempotent and non-raising.

    The caller is responsible for committing the session. This function only
    mutates the in-memory row so it can be composed inside a larger unit of
    work (for example: transition + write a transcript event + bill minutes,
    all in one transaction).

    Outcomes:
        applied=True   the status changed
        applied=False  duplicate (same status), terminal, or illegal
    """
    previous = call.status

    # 1. Duplicate callback. Twilio retries; this is the common case, not an
    #    error. We still refresh duration/ended_at because a retry sometimes
    #    carries a more complete payload than the first delivery.
    if previous == target:
        _stamp(call, duration_seconds, ended_at, target)
        log.debug(
            "call_state.duplicate",
            call_id=str(call.id),
            call_sid=call.call_sid,
            status=target.value,
            source=source,
        )
        return TransitionResult(False, previous, previous, "duplicate")

    # 2. Already finished. A late webhook must not resurrect or rewrite it.
    if is_terminal(previous):
        log.warning(
            "call_state.terminal_protected",
            call_id=str(call.id),
            call_sid=call.call_sid,
            current=previous.value,
            rejected=target.value,
            source=source,
        )
        return TransitionResult(False, previous, previous, "terminal")

    # 3. Illegal edge, e.g. COMPLETED -> RINGING or FAILED -> IN_PROGRESS.
    if not can_transition(previous, target):
        log.warning(
            "call_state.illegal_transition",
            call_id=str(call.id),
            call_sid=call.call_sid,
            current=previous.value,
            rejected=target.value,
            source=source,
        )
        return TransitionResult(False, previous, previous, "illegal")

    call.status = target
    _stamp(call, duration_seconds, ended_at, target)

    # Step 7 observability: count the call-shaped signals once, at the exact
    # transition that produced them. Bounded labels only — the call id/SID
    # stay in the log line below, never in a Prometheus label.
    if target is CallStatus.IN_PROGRESS and previous is CallStatus.RINGING:
        observability.record_call_answered()
    if is_terminal(target):
        observability.record_call_outcome(target.value)
        observability.observe_call_duration(call.duration_seconds)

    if reason and target in (CallStatus.FAILED, CallStatus.NO_ANSWER):
        # Only record a failure reason where one is meaningful. `summary` is
        # the caller-visible outcome field and is left alone.
        call.failure_reason = reason[:120]

    log.info(
        "call_state.transition",
        call_id=str(call.id),
        call_sid=call.call_sid,
        previous=previous.value,
        current=target.value,
        reason=reason or None,
        source=source,
    )
    return TransitionResult(True, previous, target, "applied")


def _stamp(
    call: Call,
    duration_seconds: float | None,
    ended_at: datetime | None,
    target: CallStatus,
) -> None:
    """
    Update timestamps and duration without ever losing information.

    Duration only ever grows: a retry that reports 0 seconds must not erase a
    previously recorded 143 seconds. `ended_at` is written once and then left
    alone, so the first authoritative end time wins.
    """
    if duration_seconds is not None and duration_seconds > (call.duration_seconds or 0):
        call.duration_seconds = duration_seconds

    if is_terminal(target) and call.ended_at is None:
        call.ended_at = ended_at or datetime.utcnow()


def apply_provider_status(
    call: Call,
    raw_status: str | None,
    *,
    duration_seconds: float | None = None,
    source: str = "provider",
) -> TransitionResult:
    """
    Apply a raw provider status string.

    An unknown string is ignored rather than guessed at, because guessing here
    means terminating somebody's live call on a typo.
    """
    target = map_provider_status(raw_status)
    if target is None:
        log.warning(
            "call_state.unknown_provider_status",
            call_id=str(call.id),
            call_sid=call.call_sid,
            raw=raw_status,
            source=source,
        )
        return TransitionResult(False, call.status, call.status, "unknown_status")

    normalized = (raw_status or "").strip().lower()
    reason = normalized if normalized in FAILURE_REASONS else ""
    return apply_status(
        call,
        target,
        reason=reason,
        duration_seconds=duration_seconds,
        source=source,
    )
````

### File 04: `app/integrations/google_calendar.py`

SHA-256: `803099d84feb84dd4504d4ec007b5261786ecfa544704bc2fe3335d41a1750ed`

````
"""Google Calendar via a service account.

Sell tip: ask the client to share their calendar with your service-account email.
That is a 60-second setup on a demo call -- no OAuth screens, no Google review.
"""
from __future__ import annotations

import asyncio
from datetime import datetime

from app.core.config import settings
from app.core.logging import log
from app.integrations.calendar.errors import CalendarConfigurationError, CalendarTemporaryError

try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    _GOOGLE_AVAILABLE = True
except ImportError:      # keeps tests runnable without creds
    _GOOGLE_AVAILABLE = False

SCOPES = ["https://www.googleapis.com/auth/calendar"]


class CalendarClient:
    def __init__(self, calendar_id: str | None):
        self.calendar_id = calendar_id
        self._service = None

    def _get_service(self):
        if self._service is None:
            creds = service_account.Credentials.from_service_account_file(
                settings.google_credentials_json, scopes=SCOPES
            )
            self._service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        return self._service

    async def list_busy(self, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
        if not self.calendar_id or not _GOOGLE_AVAILABLE:
            raise CalendarConfigurationError("Google calendar is not configured", provider="google_service_account")

        def _call():
            body = {
                "timeMin": start.isoformat(),
                "timeMax": end.isoformat(),
                "items": [{"id": self.calendar_id}],
            }
            res = self._get_service().freebusy().query(body=body).execute()
            calendar = res["calendars"][self.calendar_id]
            if calendar.get("errors"):
                raise CalendarTemporaryError("Google calendar free/busy query failed", provider="google_service_account")
            slots = calendar.get("busy", [])
            return [
                (datetime.fromisoformat(s["start"]), datetime.fromisoformat(s["end"]))
                for s in slots
            ]

        try:
            return await asyncio.to_thread(_call)
        except Exception as exc:
            log.error("calendar.freebusy_failed", error_type=type(exc).__name__)
            raise CalendarTemporaryError("Google calendar free/busy query failed", provider="google_service_account") from exc

    async def create_event(
        self, summary: str, description: str, start: datetime, end: datetime
    ) -> str | None:
        if not self.calendar_id or not _GOOGLE_AVAILABLE:
            return None

        def _call():
            event = {
                "summary": summary,
                "description": description,
                "start": {"dateTime": start.isoformat()},
                "end": {"dateTime": end.isoformat()},
            }
            created = (
                self._get_service()
                .events()
                .insert(calendarId=self.calendar_id, body=event)
                .execute()
            )
            return created.get("id")

        try:
            return await asyncio.to_thread(_call)
        except Exception as exc:
            log.error("calendar.create_failed", error=str(exc))
            return None
````

### File 05: `app/integrations/calendar/providers/internal.py`

SHA-256: `e14a636f150ca238b3b6b1b1bce9820f615facccf8fa235e539284e4ac5d5353`

````
"""
Providers backed by what the repository already had.

Two adapters, and the split between them is the point.

**`InternalCalendarProvider`** — no external calendar at all. VoxDesk's own
`appointments` table is the entire source of truth. This is the correct default
for a small business that has not connected anything, and it is what makes the
booking flow work end to end without a vendor account. It declares every
capability *except* free/busy, because there is no third-party calendar to be
busy on: the service already checks VoxDesk's own appointments separately, and
declaring `FREE_BUSY` here would double-count them.

**`GoogleServiceAccountProvider`** — the pre-STEP-6 path, wrapping
`app/integrations/google_calendar.CalendarClient` unchanged.

That wrapper declares only `FREE_BUSY` and `CREATE_EVENT`, because those are
the only two methods the legacy client has. It cannot cancel, cannot
reschedule, and cannot read an event back. Declaring more would let the service
route work to methods that do not exist.

The legacy client now raises normalized calendar errors for failed free/busy
queries, including per-calendar errors, instead of reporting empty availability.
Event creation must still return an actual provider identifier before confirmation.
"""
from __future__ import annotations

from app.integrations.calendar.base import (
    CalendarCapability,
    CalendarProvider,
)
from app.integrations.calendar.errors import (
    CalendarConfigurationError,
    CalendarTemporaryError,
)
from app.integrations.calendar.models import (
    BusyPeriod,
    CalendarEvent,
    EventRequest,
    HealthResult,
    TimeWindow,
)
from app.integrations.calendar.timezones import UTC, as_utc


class InternalCalendarProvider(CalendarProvider):
    """
    VoxDesk is the calendar.

    Every operation succeeds locally and returns a deterministic event id
    derived from the booking's idempotency key — so a retry after a crash
    produces the *same* id, and the service's reconciliation logic behaves
    identically to a real provider's.
    """

    name = "internal"
    capabilities = frozenset({
        CalendarCapability.GET_AVAILABILITY,
        CalendarCapability.CREATE_EVENT,
        CalendarCapability.UPDATE_EVENT,
        CalendarCapability.CANCEL_EVENT,
        CalendarCapability.GET_EVENT,
        CalendarCapability.RESCHEDULE,
        CalendarCapability.HEALTH_CHECK,
    })

    # `get_busy` is deliberately NOT overridden.
    #
    # The first draft returned `[]` here "for explicitness", and the contract
    # test caught it: an undeclared capability that returns empty instead of
    # raising is the same shape as the audit's F2 -- a caller cannot tell
    # "nothing is busy" from "I do not answer this question". The base class
    # raises `CalendarUnsupportedError`, which is the honest answer, and the
    # service never asks because FREE_BUSY is not in `capabilities`.

    async def create_event(self, request: EventRequest) -> CalendarEvent:
        return CalendarEvent(
            external_id=self._event_id(request),
            start=request.start,
            end=request.end,
            title=request.title,
            status="confirmed",
            calendar_reference="internal",
        )

    async def update_event(
        self, external_id: str, request: EventRequest
    ) -> CalendarEvent:
        return CalendarEvent(
            external_id=external_id,
            start=request.start,
            end=request.end,
            title=request.title,
            status="confirmed",
            calendar_reference="internal",
        )

    async def cancel_event(
        self, external_id: str, *, reason: str = "", calendar_reference: str | None = None
    ) -> None:
        return None

    async def get_event(
        self, external_id: str, *, calendar_reference: str | None = None
    ) -> CalendarEvent | None:
        # There is no second store to consult: if VoxDesk has the row, the
        # event exists. The service already holds that row, so returning None
        # would be a lie and returning a fabricated one would be worse. This
        # provider is never in the ambiguous-timeout situation, because
        # nothing crosses a network.
        return None

    @staticmethod
    def _event_id(request: EventRequest) -> str:
        if request.idempotency_key:
            return f"internal-{request.idempotency_key[:40]}"
        return f"internal-{int(request.start.timestamp())}"

    async def health_check(self) -> HealthResult:
        return HealthResult(
            connected=True, provider=self.name, latency_ms=0.0,
            safe_message="internal calendar; no external provider configured",
        )


class GoogleServiceAccountProvider(CalendarProvider):
    """
    The pre-STEP-6 Google path, wrapped without modification.

    Kept so existing tenants keep working through the new service layer while
    they migrate to OAuth. Deliberately minimal capabilities.
    """

    name = "google_service_account"
    capabilities = frozenset({
        CalendarCapability.GET_AVAILABILITY,
        CalendarCapability.FREE_BUSY,
        CalendarCapability.CREATE_EVENT,
        CalendarCapability.HEALTH_CHECK,
    })
    # Absent, because `CalendarClient` has no such method: UPDATE_EVENT,
    # CANCEL_EVENT, GET_EVENT, RESCHEDULE. A tenant on this provider who tries
    # to cancel gets a clean "unsupported" and a human handles it, rather than
    # VoxDesk claiming to have cancelled something it never touched.

    def _client(self):
        calendar_id = (self.context.config or {}).get("calendar_id")
        if not calendar_id:
            raise CalendarConfigurationError(
                "the service-account provider needs a calendar_id",
                provider=self.name,
            )
        from app.integrations.google_calendar import CalendarClient

        return CalendarClient(calendar_id)

    async def get_busy(self, window: TimeWindow) -> list[BusyPeriod]:
        """
        Free/busy through the legacy client.

        Failed reads propagate normalized errors; an empty list means the
        provider successfully returned no busy periods.
        """
        periods = await self._client().list_busy(window.start, window.end)
        return [
            BusyPeriod(start=as_utc(start), end=as_utc(end), source=self.name)
            for start, end in periods
        ]

    async def create_event(self, request: EventRequest) -> CalendarEvent:
        event_id = await self._client().create_event(
            summary=request.title,
            description=request.description,
            start=request.start.astimezone(UTC),
            end=request.end.astimezone(UTC),
        )
        if not event_id:
            # The legacy client returns None on *any* failure. Turning that
            # into a raise is the single most important change in this file:
            # the old code treated None as success and told the caller the
            # appointment was booked.
            raise CalendarTemporaryError(
                "the Google service account did not confirm the event",
                provider=self.name,
            )
        return CalendarEvent(
            external_id=str(event_id),
            start=request.start,
            end=request.end,
            title=request.title,
            calendar_reference=(self.context.config or {}).get("calendar_id"),
        )

    async def health_check(self) -> HealthResult:
        async def probe():
            from datetime import timedelta

            from app.integrations.calendar.timezones import now_utc

            start = now_utc()
            await self._client().list_busy(start, start + timedelta(hours=1))

        return await self._timed_health_check(probe)
````

### File 06: `contracts/proto/voxdesk/contracts/v1/session.proto`

SHA-256: `bfc01cd20febb2388de723cbc92f9cca611ca59a796200425fe29dc300313560`

````
syntax = "proto3";

package voxdesk.contracts.v1;

import "google/protobuf/timestamp.proto";
import "voxdesk/contracts/v1/common.proto";

option go_package = "voxdesk/contracts/gen/go/contractsv1";

// ===========================================================================
// Enums
//
// The member names below are deliberately identical to the Python enum member
// names in app/db/models.py, because SQLAlchemy persists the enum member NAME
// (not the value) into PostgreSQL. scripts/verify_contracts.py cross-checks
// this vocabulary against app/db/models.py so the wire contract and the stored
// vocabulary can never drift apart silently.
// ===========================================================================

// Mirrors app.db.models.CallStatus. *_UNSPECIFIED is the proto3 zero value and
// is never persisted.
enum CallStatus {
  CALL_STATUS_UNSPECIFIED = 0;
  CALL_STATUS_RINGING = 1;
  CALL_STATUS_IN_PROGRESS = 2;
  CALL_STATUS_COMPLETED = 3;
  CALL_STATUS_FAILED = 4;
  CALL_STATUS_NO_ANSWER = 5;
  CALL_STATUS_TRANSFERRED = 6;
  CALL_STATUS_CANCELLED = 7;
}

// Mirrors app.db.models.TransferState. Transfer state is tracked separately
// from CallStatus because a call can be IN_PROGRESS while a transfer is
// mid-flight. Moving out of NONE is also the idempotency lock that stops a
// second tool call from dialing the human twice.
enum TransferState {
  TRANSFER_STATE_UNSPECIFIED = 0;
  TRANSFER_STATE_NONE = 1;
  TRANSFER_STATE_REQUESTED = 2;
  TRANSFER_STATE_DIALING = 3;
  TRANSFER_STATE_CONNECTED = 4;
  TRANSFER_STATE_FAILED = 5;
}

// Mirrors app.db.models.CallDirection.
enum CallDirection {
  CALL_DIRECTION_UNSPECIFIED = 0;
  CALL_DIRECTION_INBOUND = 1;
  CALL_DIRECTION_OUTBOUND = 2;
}

// ===========================================================================
// Messages
// ===========================================================================

// Emitted once when a call session is created. Carries the identifiers the
// rest of the platform already uses: call_sid is the provider (Twilio) SID;
// call_id is the internal UUID from app.db.models.Call.
message SessionStarted {
  TenantContext tenant = 1;
  Uuid call_id = 2;
  string call_sid = 3;
  CallDirection direction = 4;
  string from_number = 5;
  string to_number = 6;
  google.protobuf.Timestamp started_at = 7;
}

// Emitted whenever Call.status changes. `failure_reason` is only meaningful
// for FAILED / NO_ANSWER and uses the app.db.models.Call.failure_reason
// vocabulary ("busy", "no-answer", ...).
message SessionStatusChanged {
  TenantContext tenant = 1;
  Uuid call_id = 2;
  CallStatus status = 3;
  optional string failure_reason = 4;
  google.protobuf.Timestamp ended_at = 5;
  double duration_seconds = 6;
}

// A human-escalation request. `destination` and `reason` mirror
// app.db.models.Call.transfer_destination / transfer_reason; `attempts`
// mirrors transfer_attempts.
message TransferRequested {
  TenantContext tenant = 1;
  Uuid call_id = 2;
  string destination = 3;
  string reason = 4;
  int32 attempts = 5;
}

// Emitted whenever Call.transfer_state changes, carrying the failure detail
// when the escalation could not be completed and the wall-clock times that
// mirror the transfer_*_at columns on app.db.models.Call.
message TransferStateChanged {
  TenantContext tenant = 1;
  Uuid call_id = 2;
  TransferState state = 3;
  optional string error = 4;
  google.protobuf.Timestamp requested_at = 5;
  google.protobuf.Timestamp started_at = 6;
  google.protobuf.Timestamp completed_at = 7;
  google.protobuf.Timestamp failed_at = 8;
}

// Acknowledgment returned by the session control plane. Every response carries
// the tenant context so a receiver can assert it is being answered for the
// tenant it asked about.
message SessionAck {
  TenantContext tenant = 1;
  Uuid call_id = 2;
  bool accepted = 3;
  ErrorInfo error = 4;
}

// ===========================================================================
// Service
//
// The control-plane boundary. Consumed by the Rust session state machine and
// the Go ops tooling once they exist (roadmap Phases 2 and 5); the Python
// backend remains authoritative for business logic until shadow-mode parity is
// proven. Defined now so the boundary is stable before the first non-Python
// consumer exists.
// ===========================================================================
service SessionControl {
  rpc StartSession(SessionStarted) returns (SessionAck);
  rpc ReportStatus(SessionStatusChanged) returns (SessionAck);
  rpc RequestTransfer(TransferRequested) returns (SessionAck);
}
````

### File 07: `services/control-plane/crates/core/src/session.rs`

SHA-256: `39806361668f84222b6ae350f67c1a260707597e8608e3620207f18c88404d9c`

````
//! Call-session state machine, mirroring `app/telephony/call_state.py` and
//! `app/telephony/transfer_service.py` exactly.
//!
//! The Python modules are the single source of truth for call lifecycle, and
//! this file is their Rust shadow for the Phase 2 parity gate. Every rule
//! below is a verbatim re-statement of a Python rule, with the Python source
//! cited in the doc comment:
//!
//!   * the call-status graph (`ALLOWED_TRANSITIONS`), where TRANSFERRED is
//!     deliberately **not** terminal — a transferred call is still up, with a
//!     human on it, and must still reach COMPLETED/FAILED when the human hangs
//!     up;
//!   * the two-phase transfer (`request_transfer` → provider redirect →
//!     DIALING → callback → CONNECTED/FAILED), where `CallStatus.TRANSFERRED`
//!     is set at DIALING — once the provider has accepted the redirect — never
//!     from REQUESTED;
//!   * idempotency keyed on `transfer_state` (`TRANSFER_IN_FLIGHT`): a second
//!     request while one is in flight is a no-op, never a second dial;
//!   * the "inferred failure" correction rule: a provider-reported failure is
//!     authoritative, but a failure we *guessed* at (`"inferred: "` prefix)
//!     may be corrected by a later "connected" callback.

/// Mirrors `app.db.models.CallStatus`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum CallStatus {
    Ringing,
    InProgress,
    Completed,
    Failed,
    NoAnswer,
    Transferred,
    Cancelled,
}

/// Mirrors `app.db.models.TransferState`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum TransferState {
    None,
    Requested,
    Dialing,
    Connected,
    Failed,
}

/// Mirrors `call_state.ALLOWED_TRANSITIONS`. Every transition the application
/// considers legal, including the two people forget:
///
/// * RINGING -> COMPLETED — the provider reports completion straight from
///   ringing when the caller hangs up during ringback;
/// * TRANSFERRED -> COMPLETED | FAILED — the human hung up, or the transfer
///   leg died after connecting.
pub fn allowed_transitions(status: CallStatus) -> &'static [CallStatus] {
    match status {
        CallStatus::Ringing => &[
            CallStatus::InProgress,
            CallStatus::NoAnswer,
            CallStatus::Failed,
            CallStatus::Cancelled,
            CallStatus::Completed,
        ],
        CallStatus::InProgress => &[
            CallStatus::Transferred,
            CallStatus::Completed,
            CallStatus::Failed,
        ],
        CallStatus::Transferred => &[CallStatus::Completed, CallStatus::Failed],
        // Terminal.
        CallStatus::Completed
        | CallStatus::Failed
        | CallStatus::NoAnswer
        | CallStatus::Cancelled => &[],
    }
}

/// Mirrors `call_state.TERMINAL_STATUSES`. TRANSFERRED is deliberately absent.
pub fn is_terminal(status: CallStatus) -> bool {
    matches!(
        status,
        CallStatus::Completed
            | CallStatus::Failed
            | CallStatus::NoAnswer
            | CallStatus::Cancelled
    )
}

/// Mirrors `call_state.can_transition`: a transition to the same state is
/// always allowed (it is a no-op).
pub fn can_transition(current: CallStatus, target: CallStatus) -> bool {
    if current == target {
        return true;
    }
    allowed_transitions(current).contains(&target)
}

/// What `apply_status` did (mirrors `call_state.TransitionResult`). Never
/// raises; always reports.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct TransitionResult {
    pub applied: bool,
    pub previous: CallStatus,
    pub current: CallStatus,
}

/// Mirrors `transfer_service.TransferError`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TransferError {
    NoDestination,
    InvalidDestination,
    CallAlreadyEnded,
    CallNotTransferable,
    ProviderError,
    TenantMismatch,
}

/// Mirrors `transfer_service.TransferOutcome`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TransferOutcome {
    TransferStarted,
    TransferCompleted,
    TransferFailed,
    AlreadyTransferred,
}

/// Mirrors `transfer_service.TransferResult`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TransferResult {
    pub outcome: TransferOutcome,
    pub state: TransferState,
    pub error: Option<TransferError>,
    pub destination: Option<String>,
}

impl TransferResult {
    /// Mirrors `TransferResult.ok`.
    pub fn ok(&self) -> bool {
        matches!(
            self.outcome,
            TransferOutcome::TransferStarted
                | TransferOutcome::TransferCompleted
                | TransferOutcome::AlreadyTransferred
        )
    }
}

/// Mirrors `transfer_service.INFERRED_PREFIX`: a failure we guessed at rather
/// than one the provider reported. The provider always wins over the guess.
pub const INFERRED_PREFIX: &str = "inferred: ";

/// Mirrors `transfer_service`'s field caps (`.transfer_error` detail at 300,
/// `.transfer_reason` at 400).
pub const MAX_TRANSFER_ERROR_CHARS: usize = 300;
pub const MAX_TRANSFER_REASON_CHARS: usize = 400;

/// Mirrors `app.db.models.TRANSFER_IN_FLIGHT`. States in which a transfer is
/// already under way or finished; a second request in one of these is a no-op.
pub fn transfer_in_flight(state: TransferState) -> bool {
    matches!(
        state,
        TransferState::Requested | TransferState::Dialing | TransferState::Connected
    )
}

/// The outcome of `Session::request_transfer`: either validation passed and a
/// provider redirect must be performed now, or the request was refused with
/// the reason in the result.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RequestOutcome {
    Dial,
    Refused(TransferResult),
}

/// The mutable state of one call session. `call_id` is the internal UUID (as
/// text); the provider SID is carried separately at the transport layer.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Session {
    pub call_id: String,
    pub status: CallStatus,
    pub transfer: TransferState,
    pub transfer_destination: Option<String>,
    pub transfer_reason: Option<String>,
    pub transfer_attempts: u32,
    pub transfer_error: Option<String>,
    /// Mirrors `Call.escalated`: a transfer was attempted; kept visible even
    /// after a failure.
    pub escalated: bool,
}

fn truncate(s: &str, max: usize) -> Option<String> {
    if s.is_empty() {
        return None;
    }
    if s.chars().count() <= max {
        return Some(s.to_string());
    }
    Some(s.chars().take(max).collect())
}

impl Session {
    pub fn new(call_id: impl Into<String>) -> Self {
        Session {
            call_id: call_id.into(),
            status: CallStatus::Ringing,
            transfer: TransferState::None,
            transfer_destination: None,
            transfer_reason: None,
            transfer_attempts: 0,
            transfer_error: None,
            escalated: false,
        }
    }

    /// Mirrors `call_state.apply_status`: idempotent, never-raising. Returns
    /// whether the status actually changed.
    pub fn apply_status(&mut self, target: CallStatus) -> TransitionResult {
        let previous = self.status;
        if previous == target {
            return TransitionResult {
                applied: false,
                previous,
                current: target,
            };
        }
        if can_transition(previous, target) {
            self.status = target;
            return TransitionResult {
                applied: true,
                previous,
                current: target,
            };
        }
        TransitionResult {
            applied: false,
            previous,
            current: previous,
        }
    }

    /// Mirrors the validation ladder in `transfer_service.request_transfer`
    /// (steps 1–4: idempotency, terminal, transferable, destination, then
    /// record intent). `destination` is the tenant's already-resolved
    /// escalation number — in Python it always comes from the tenant row,
    /// never from a request body; `None` mirrors an unconfigured number.
    ///
    /// Returns `Dial` when a provider redirect must be performed now (intent
    /// recorded: REQUESTED, attempts + 1), or `Refused` with the reason.
    pub fn request_transfer(&mut self, destination: Option<&str>, reason: &str) -> RequestOutcome {
        // 1. Idempotency: in-flight means never a second dial.
        if transfer_in_flight(self.transfer) {
            let outcome = if self.transfer == TransferState::Connected {
                TransferOutcome::TransferCompleted
            } else {
                TransferOutcome::AlreadyTransferred
            };
            return RequestOutcome::Refused(TransferResult {
                outcome,
                state: self.transfer,
                error: None,
                destination: self.transfer_destination.clone(),
            });
        }

        // 2. The call must still be up, and must be able to reach TRANSFERRED.
        if is_terminal(self.status) {
            return RequestOutcome::Refused(TransferResult {
                outcome: TransferOutcome::TransferFailed,
                state: self.transfer,
                error: Some(TransferError::CallAlreadyEnded),
                destination: None,
            });
        }
        if !can_transition(self.status, CallStatus::Transferred) {
            return RequestOutcome::Refused(TransferResult {
                outcome: TransferOutcome::TransferFailed,
                state: self.transfer,
                error: Some(TransferError::CallNotTransferable),
                destination: None,
            });
        }

        // 3. Destination. A missing destination is recorded as a failure
        //    (transfer_state = FAILED) exactly like `_record_failure`.
        let dest = match destination {
            Some(d) => d,
            None => {
                self.record_failure(TransferError::NoDestination);
                return RequestOutcome::Refused(TransferResult {
                    outcome: TransferOutcome::TransferFailed,
                    state: self.transfer,
                    error: Some(TransferError::NoDestination),
                    destination: None,
                });
            }
        };

        // 4. Record intent (REQUESTED) before touching the provider, so that
        //    if the process dies mid-redirect we still know a transfer was
        //    attempted.
        self.escalated = true;
        self.transfer = TransferState::Requested;
        self.transfer_destination = Some(dest.to_string());
        self.transfer_reason = truncate(reason, MAX_TRANSFER_REASON_CHARS);
        self.transfer_attempts += 1;
        self.transfer_error = None;
        RequestOutcome::Dial
    }

    /// Mirrors the success branch of `transfer_service.request_transfer`
    /// (step 6): the provider accepted the redirect, so — now and only now —
    /// the transfer is DIALING and the call status becomes TRANSFERRED.
    pub fn on_redirect_ok(&mut self) -> TransferResult {
        self.transfer = TransferState::Dialing;
        self.apply_status(CallStatus::Transferred);
        TransferResult {
            outcome: TransferOutcome::TransferStarted,
            state: TransferState::Dialing,
            error: None,
            destination: self.transfer_destination.clone(),
        }
    }

    /// Mirrors the provider-failure branch of
    /// `transfer_service.request_transfer` (step 5): record the failure, keep
    /// the call alive.
    pub fn on_redirect_error(&mut self, detail: &str) -> TransferResult {
        self.record_failure_detail(detail);
        TransferResult {
            outcome: TransferOutcome::TransferFailed,
            state: TransferState::Failed,
            error: Some(TransferError::ProviderError),
            destination: self.transfer_destination.clone(),
        }
    }

    /// Mirrors `transfer_service.mark_transfer_connected`. Returns true when
    /// this changed anything, so a caller can tell a real event from a
    /// retried webhook. A failure we inferred (prefix `inferred: `) may be
    /// corrected here; a provider-reported failure may not. Does not touch
    /// the call status — the call stays TRANSFERRED.
    pub fn mark_transfer_connected(&mut self) -> bool {
        if self.transfer == TransferState::Connected {
            return false;
        }
        let recoverable = self.transfer == TransferState::Failed
            && self
                .transfer_error
                .as_deref()
                .is_some_and(|e| e.starts_with(INFERRED_PREFIX));
        if !matches!(
            self.transfer,
            TransferState::Requested | TransferState::Dialing
        ) && !recoverable
        {
            return false;
        }
        if recoverable {
            self.transfer_error = None;
        }
        self.transfer = TransferState::Connected;
        true
    }

    /// Mirrors `transfer_service.mark_transfer_failed`. Returns true when this
    /// changed anything. A late failure for an already-connected leg must not
    /// rewrite the successful transfer, and the call itself stays alive (the
    /// TwiML falls through to voicemail).
    pub fn mark_transfer_failed(&mut self, reason: &str) -> bool {
        if self.transfer == TransferState::Failed {
            return false;
        }
        if self.transfer == TransferState::Connected {
            return false;
        }
        self.record_failure_detail(reason);
        true
    }

    /// Mirrors `transfer_service._record_failure` (minus the transcript
    /// event): FAILED + truncated detail + keep `escalated` visible.
    fn record_failure_detail(&mut self, detail: &str) {
        self.transfer = TransferState::Failed;
        self.transfer_error = truncate(detail, MAX_TRANSFER_ERROR_CHARS);
        self.escalated = true;
    }

    /// Convenience over `record_failure_detail` for a `TransferError` code.
    fn record_failure(&mut self, error: TransferError) {
        let detail = match error {
            TransferError::NoDestination => "no_destination",
            TransferError::InvalidDestination => "invalid_destination",
            TransferError::CallAlreadyEnded => "call_already_ended",
            TransferError::CallNotTransferable => "call_not_transferable",
            TransferError::ProviderError => "provider_error",
            TransferError::TenantMismatch => "tenant_mismatch",
        };
        self.record_failure_detail(detail);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // ------------------------------------------------------------------
    // Differential tests: each one encodes a Python rule verbatim, so the
    // same inputs produce the same observable outcomes as the Python path.
    // ------------------------------------------------------------------

    #[test]
    fn differential_ringing_completed_is_legal() {
        // call_state.ALLOWED_TRANSITIONS[RINGING] contains COMPLETED (hang up
        // during ringback).
        let mut s = Session::new("c");
        let r = s.apply_status(CallStatus::Completed);
        assert!(r.applied);
        assert_eq!(s.status, CallStatus::Completed);
    }

    #[test]
    fn differential_transferred_is_not_terminal() {
        // TRANSFERRED -> COMPLETED and TRANSFERRED -> FAILED are legal (the
        // human hung up / the leg died); TRANSFERRED -> IN_PROGRESS is not.
        let mut s = Session::new("c");
        s.apply_status(CallStatus::InProgress);
        s.apply_status(CallStatus::Transferred);
        assert!(!is_terminal(CallStatus::Transferred));
        assert!(can_transition(
            CallStatus::Transferred,
            CallStatus::Completed
        ));
        assert!(can_transition(CallStatus::Transferred, CallStatus::Failed));
        assert!(!can_transition(
            CallStatus::Transferred,
            CallStatus::InProgress
        ));
        assert!(s.apply_status(CallStatus::Completed).applied);
    }

    #[test]
    fn differential_same_state_is_an_idempotent_noop() {
        // call_state.can_transition: same state is always allowed (no-op).
        let mut s = Session::new("c");
        s.apply_status(CallStatus::InProgress);
        let r = s.apply_status(CallStatus::InProgress);
        assert!(!r.applied);
        assert_eq!(r.previous, CallStatus::InProgress);
        assert_eq!(r.current, CallStatus::InProgress);
    }

    #[test]
    fn differential_terminal_never_moves() {
        for terminal in [
            CallStatus::Completed,
            CallStatus::Failed,
            CallStatus::NoAnswer,
            CallStatus::Cancelled,
        ] {
            assert!(is_terminal(terminal));
            assert!(allowed_transitions(terminal).is_empty());
        }
        let mut s = Session::new("c");
        s.apply_status(CallStatus::Completed);
        let r = s.apply_status(CallStatus::InProgress);
        assert!(!r.applied);
        assert_eq!(s.status, CallStatus::Completed);
    }

    #[test]
    fn differential_happy_path_sets_transferred_at_dialing() {
        // transfer_service: TRANSFERRED is set at DIALING, never REQUESTED.
        let mut s = Session::new("c");
        s.apply_status(CallStatus::InProgress);

        match s.request_transfer(Some("+15551234567"), "human please") {
            RequestOutcome::Dial => {}
            _ => panic!("expected a dial"),
        }
        assert_eq!(s.transfer, TransferState::Requested);
        assert_eq!(s.transfer_attempts, 1);
        assert!(s.escalated);
        assert_eq!(s.status, CallStatus::InProgress); // NOT transferred yet
        assert_eq!(s.transfer_error, None);

        let result = s.on_redirect_ok();
        assert_eq!(result.outcome, TransferOutcome::TransferStarted);
        assert!(result.ok());
        assert_eq!(s.transfer, TransferState::Dialing);
        assert_eq!(s.status, CallStatus::Transferred); // set at DIALING
    }

    #[test]
    fn differential_missing_destination_records_failure() {
        // resolve_destination -> NO_DESTINATION -> _record_failure (FAILED).
        let mut s = Session::new("c");
        s.apply_status(CallStatus::InProgress);
        match s.request_transfer(None, "human") {
            RequestOutcome::Refused(r) => {
                assert_eq!(r.outcome, TransferOutcome::TransferFailed);
                assert_eq!(r.error, Some(TransferError::NoDestination));
            }
            _ => panic!("expected refusal"),
        }
        assert_eq!(s.transfer, TransferState::Failed);
        assert_eq!(s.transfer_error.as_deref(), Some("no_destination"));
        assert!(s.escalated);
        assert_eq!(s.transfer_attempts, 0); // never dialed
    }

    #[test]
    fn differential_second_request_in_flight_is_a_noop() {
        let mut s = Session::new("c");
        s.apply_status(CallStatus::InProgress);
        assert!(matches!(
            s.request_transfer(Some("+1"), "r"),
            RequestOutcome::Dial
        ));
        assert_eq!(s.transfer_attempts, 1);
        // In-flight: refused, attempts unchanged, no second dial.
        match s.request_transfer(Some("+1"), "again") {
            RequestOutcome::Refused(r) => {
                assert_eq!(r.outcome, TransferOutcome::AlreadyTransferred);
                assert!(r.ok()); // not an error from the caller's perspective
            }
            _ => panic!("expected refusal"),
        }
        assert_eq!(s.transfer_attempts, 1);
    }

    #[test]
    fn differential_request_while_connected_reports_completed() {
        let mut s = Session::new("c");
        s.apply_status(CallStatus::InProgress);
        s.request_transfer(Some("+1"), "r");
        s.on_redirect_ok();
        assert!(s.mark_transfer_connected());
        match s.request_transfer(Some("+1"), "again") {
            RequestOutcome::Refused(r) => {
                assert_eq!(r.outcome, TransferOutcome::TransferCompleted);
            }
            _ => panic!("expected refusal"),
        }
    }

    #[test]
    fn differential_request_when_terminal_is_rejected() {
        let mut s = Session::new("c");
        s.apply_status(CallStatus::Completed);
        match s.request_transfer(Some("+1"), "r") {
            RequestOutcome::Refused(r) => {
                assert_eq!(r.error, Some(TransferError::CallAlreadyEnded));
                assert_eq!(r.outcome, TransferOutcome::TransferFailed);
            }
            _ => panic!("expected refusal"),
        }
        // No state change: still NONE, never escalated.
        assert_eq!(s.transfer, TransferState::None);
        assert!(!s.escalated);
    }

    #[test]
    fn differential_request_while_ringing_is_not_transferable() {
        // RINGING cannot transition to TRANSFERRED.
        let mut s = Session::new("c"); // starts RINGING
        match s.request_transfer(Some("+1"), "r") {
            RequestOutcome::Refused(r) => {
                assert_eq!(r.error, Some(TransferError::CallNotTransferable));
            }
            _ => panic!("expected refusal"),
        }
        assert_eq!(s.transfer, TransferState::None);
    }

    #[test]
    fn differential_provider_error_records_failure_keeps_call_alive() {
        let mut s = Session::new("c");
        s.apply_status(CallStatus::InProgress);
        s.request_transfer(Some("+1"), "r");
        let result = s.on_redirect_error("21212: busy");
        assert_eq!(result.outcome, TransferOutcome::TransferFailed);
        assert_eq!(result.error, Some(TransferError::ProviderError));
        assert_eq!(s.transfer, TransferState::Failed);
        assert_eq!(s.status, CallStatus::InProgress); // call still up
        assert_eq!(s.transfer_error.as_deref(), Some("21212: busy"));
    }

    #[test]
    fn differential_connected_from_dialing_keeps_status() {
        let mut s = Session::new("c");
        s.apply_status(CallStatus::InProgress);
        s.request_transfer(Some("+1"), "r");
        s.on_redirect_ok();
        assert!(s.mark_transfer_connected());
        assert_eq!(s.transfer, TransferState::Connected);
        assert_eq!(s.status, CallStatus::Transferred); // unchanged by connect
                                                       // Duplicate callback is a no-op.
        assert!(!s.mark_transfer_connected());
    }

    #[test]
    fn differential_inferred_failure_can_be_corrected() {
        let mut s = Session::new("c");
        s.apply_status(CallStatus::InProgress);
        s.request_transfer(Some("+1"), "r");
        s.on_redirect_ok();
        // The parent ended while the human's phone was ringing: we guess.
        assert!(s.mark_transfer_failed("inferred: parent call ended"));
        assert_eq!(s.transfer, TransferState::Failed);
        // The authoritative callback arrives: the guess is corrected.
        assert!(s.mark_transfer_connected());
        assert_eq!(s.transfer, TransferState::Connected);
        assert_eq!(s.transfer_error, None);
    }

    #[test]
    fn differential_reported_failure_is_never_corrected() {
        let mut s = Session::new("c");
        s.apply_status(CallStatus::InProgress);
        s.request_transfer(Some("+1"), "r");
        s.on_redirect_ok();
        // Provider-reported (no prefix) — authoritative.
        assert!(s.mark_transfer_failed("no-answer"));
        assert!(!s.mark_transfer_connected());
        assert_eq!(s.transfer, TransferState::Failed);
        assert_eq!(s.transfer_error.as_deref(), Some("no-answer"));
    }

    #[test]
    fn differential_late_failure_does_not_rewrite_connected() {
        let mut s = Session::new("c");
        s.apply_status(CallStatus::InProgress);
        s.request_transfer(Some("+1"), "r");
        s.on_redirect_ok();
        assert!(s.mark_transfer_connected());
        // A late failure callback for an earlier leg must not rewrite.
        assert!(!s.mark_transfer_failed("no-answer"));
        assert_eq!(s.transfer, TransferState::Connected);
    }

    #[test]
    fn differential_duplicate_failure_is_a_noop() {
        let mut s = Session::new("c");
        s.apply_status(CallStatus::InProgress);
        s.request_transfer(Some("+1"), "r");
        s.on_redirect_ok();
        assert!(s.mark_transfer_failed("busy"));
        assert!(!s.mark_transfer_failed("busy"));
    }

    #[test]
    fn differential_retry_after_failed_is_allowed() {
        // FAILED is not in TRANSFER_IN_FLIGHT, so a retry dials again.
        let mut s = Session::new("c");
        s.apply_status(CallStatus::InProgress);
        s.request_transfer(Some("+1"), "r");
        s.on_redirect_error("500");
        assert_eq!(s.transfer_attempts, 1);
        match s.request_transfer(Some("+1"), "retry") {
            RequestOutcome::Dial => {}
            _ => panic!("expected a retry dial"),
        }
        assert_eq!(s.transfer_attempts, 2);
        assert_eq!(s.transfer, TransferState::Requested);
        assert_eq!(s.transfer_error, None);
    }

    #[test]
    fn differential_reason_is_capped_at_400() {
        let mut s = Session::new("c");
        s.apply_status(CallStatus::InProgress);
        let long_reason = "x".repeat(500);
        s.request_transfer(Some("+1"), &long_reason);
        assert_eq!(s.transfer_reason.as_deref().unwrap().chars().count(), 400);
    }

    // ------------------------------------------------------------------
    // Property-based test: random operation sequences must never violate the
    // invariants the Python state machine guarantees.
    // ------------------------------------------------------------------

    struct Lcg(u64);
    impl Lcg {
        fn next(&mut self) -> u64 {
            self.0 = self
                .0
                .wrapping_mul(6364136223846793005)
                .wrapping_add(1442695040888963407);
            self.0 >> 33
        }
        fn below(&mut self, n: u64) -> usize {
            (self.next() % n) as usize
        }
    }

    /// `apply_status` models provider callbacks, which — per the
    /// `call_state.provider status map` — never report TRANSFERRED; the only
    /// way to reach TRANSFERRED is `on_redirect_ok`, mirroring the Python
    /// transfer service.
    const PROVIDER_STATUSES: [CallStatus; 6] = [
        CallStatus::Ringing,
        CallStatus::InProgress,
        CallStatus::Completed,
        CallStatus::Failed,
        CallStatus::NoAnswer,
        CallStatus::Cancelled,
    ];

    #[test]
    fn property_random_sequences_preserve_invariants() {
        let mut rng = Lcg(0x9e3779b97f4a7c15);
        for _case in 0..3000 {
            let mut s = Session::new("call");
            let mut terminal: Option<CallStatus> = None;
            for _step in 0..80 {
                match rng.below(7) {
                    0 | 1 => {
                        let target = PROVIDER_STATUSES[rng.below(PROVIDER_STATUSES.len() as u64)];
                        let before = s.status;
                        let r = s.apply_status(target);
                        if r.applied {
                            assert!(can_transition(before, target));
                            assert_eq!(r.previous, before);
                            assert_eq!(r.current, target);
                            if is_terminal(target) {
                                terminal = Some(target);
                            }
                        } else {
                            assert_eq!(r.current, before);
                        }
                    }
                    2 => {
                        let before = s.transfer_attempts;
                        let dest = if rng.below(2) == 0 {
                            Some("+15551234567")
                        } else {
                            None
                        };
                        match s.request_transfer(dest, "reason") {
                            RequestOutcome::Dial => {
                                assert_eq!(s.transfer, TransferState::Requested);
                                assert_eq!(s.transfer_attempts, before + 1);
                                assert!(s.escalated);
                            }
                            RequestOutcome::Refused(_) => {
                                assert_eq!(s.transfer_attempts, before);
                            }
                        }
                    }
                    3 => {
                        if s.transfer == TransferState::Requested {
                            s.on_redirect_ok();
                        }
                    }
                    4 => {
                        if s.transfer == TransferState::Requested {
                            s.on_redirect_error("provider: 500");
                        }
                    }
                    5 => {
                        s.mark_transfer_connected();
                    }
                    _ => {
                        s.mark_transfer_failed("inferred: parent ended");
                    }
                }

                // Invariants, checked after every step:
                if let Some(t) = terminal {
                    assert_eq!(s.status, t, "terminal status must never move");
                }
                if s.status == CallStatus::Transferred {
                    assert!(
                        s.transfer != TransferState::None,
                        "TRANSFERRED implies a transfer was attempted"
                    );
                }
                if matches!(
                    s.transfer,
                    TransferState::Requested | TransferState::Dialing | TransferState::Connected
                ) {
                    assert_eq!(
                        s.transfer_error, None,
                        "an in-flight or connected transfer has no failure recorded"
                    );
                }
                assert!(s.escalated || s.transfer == TransferState::None);
            }
        }
    }
}
````

### File 08: `tests/test_calendar_api.py`

SHA-256: `79b6d4d559fe391b08291f0f92d97cbe7830ec85748f662485f9d5aa8dc12376`

````
"""
API, tenant isolation, voice tools, webhooks and service-overhead timings.

Requirement 23's list is the core of this file: every cross-tenant test gives
Tenant B a genuine authenticated session and a correct id belonging to Tenant
A, and asserts that being right about the id changes nothing. **An id is not
authorization.**
"""
from __future__ import annotations

import asyncio
import json
import statistics
import time as _time
import uuid
from datetime import date, datetime, time, timedelta

import pytest
from sqlalchemy import func, select

from app.db.models import (
    Appointment,
    AppointmentStatus,
    AuditLog,
    CalendarIntegration,
    CalendarProviderType,
    CalendarWebhookReceipt,
)
from app.integrations.calendar import service
from app.integrations.calendar.models import BookingOutcome
from app.integrations.calendar.timezones import as_utc, resolve_local
from tests.conftest import (
    auth_headers,
    make_calendar_integration,
    make_scheduling_policy,
)

NY = "America/New_York"


def _next_tuesday() -> date:
    """
    A Tuesday roughly a fortnight out, computed from the real clock.

    Unlike `test_calendar_booking.py`, these tests go through the HTTP API,
    which has no way to inject a clock -- so the date has to be genuinely in
    the future or every booking is refused as TOO_SOON. Two weeks clears the
    default 60-minute minimum notice with room to spare and stays inside the
    60-day booking horizon.
    """
    today = date.today()
    ahead = (1 - today.weekday()) % 7 or 7      # 1 == Tuesday
    return today + timedelta(days=ahead + 7)


TUESDAY = _next_tuesday()


def ny(day: date, hour: int, minute: int = 0) -> datetime:
    return resolve_local(datetime.combine(day, time(hour, minute)), NY).utc


@pytest.fixture
async def tenant(db, tenant_a):
    tenant_a.timezone = NY
    await db.commit()
    await make_scheduling_policy(db, tenant_a)
    await make_calendar_integration(db, tenant_a, "internal")
    return tenant_a


@pytest.fixture
async def other(db, tenant_b):
    tenant_b.timezone = NY
    await db.commit()
    await make_scheduling_policy(db, tenant_b)
    await make_calendar_integration(db, tenant_b, "internal")
    return tenant_b


async def book(db, tenant, hour=10, **over):
    fields = dict(
        tenant_id=tenant.id, start=ny(TUESDAY, hour),
        customer_name="Jane Doe", customer_phone="+15551230000",
    )
    fields.update(over)
    result = await service.book(
        db, tenant, service.BookingRequest(**fields), now=ny(TUESDAY, 6)
    )
    assert result.outcome is BookingOutcome.BOOKED, result.message
    return await db.get(Appointment, uuid.UUID(result.appointment_id))


# ============================================================ booking API ===

class TestAppointmentApi:
    async def test_availability_endpoint(self, client, db, owner_a, tenant):
        headers = await auth_headers(client, owner_a)
        response = await client.get(
            f"/api/appointments/availability?day={TUESDAY}", headers=headers
        )
        assert response.status_code == 200

        body = response.json()
        assert body["timezone"] == NY
        assert body["slots"]
        assert body["degraded"] is None
        assert all("spoken" in slot for slot in body["slots"])

    async def test_booking_through_the_api(self, client, db, owner_a, tenant):
        headers = await auth_headers(client, owner_a)
        response = await client.post(
            "/api/appointments",
            headers=headers,
            json={
                "customer_name": "Jane Doe",
                "customer_phone": "+15551230000",
                "starts_at_local": f"{TUESDAY}T10:00:00",
                "reason": "Cleaning",
            },
        )
        assert response.status_code == 201, response.text

        body = response.json()
        assert body["status"] == "confirmed"
        assert body["timezone"] == NY
        assert body["external_event_id"]
        # The local wall clock was interpreted in the tenant's zone.
        assert body["starts_at"] == ny(TUESDAY, 10).isoformat()

    async def test_a_conflicting_booking_is_a_409(self, client, db, owner_a, tenant):
        await book(db, tenant, 10)
        headers = await auth_headers(client, owner_a)
        response = await client.post(
            "/api/appointments",
            headers=headers,
            json={
                "customer_name": "Bob", "customer_phone": "+15559990000",
                "starts_at_local": f"{TUESDAY}T10:00:00",
            },
        )
        assert response.status_code == 409
        assert response.json()["detail"]["outcome"] == "CONFLICT"

    async def test_an_out_of_hours_booking_is_a_422(self, client, db, owner_a, tenant):
        headers = await auth_headers(client, owner_a)
        response = await client.post(
            "/api/appointments",
            headers=headers,
            json={
                "customer_name": "Jane", "customer_phone": "+15551230000",
                "starts_at_local": f"{TUESDAY}T22:00:00",
            },
        )
        assert response.status_code == 422
        assert response.json()["detail"]["outcome"] == "OUTSIDE_HOURS"

    async def test_a_nonexistent_local_time_is_explained_not_shifted(
        self, client, db, owner_a, tenant
    ):
        """
        Requirement 4: never silently move an appointment by an hour. The API
        says what happened and offers the real boundary.
        """
        headers = await auth_headers(client, owner_a)
        response = await client.post(
            "/api/appointments",
            headers=headers,
            json={
                "customer_name": "Jane", "customer_phone": "+15551230000",
                "starts_at_local": "2026-03-08T02:30:00",
            },
        )
        assert response.status_code == 422
        assert "does not exist" in response.json()["detail"]

    async def test_an_ambiguous_local_time_asks_for_an_offset(
        self, client, db, owner_a, tenant
    ):
        headers = await auth_headers(client, owner_a)
        response = await client.post(
            "/api/appointments",
            headers=headers,
            json={
                "customer_name": "Jane", "customer_phone": "+15551230000",
                "starts_at_local": "2026-11-01T01:30:00",
            },
        )
        assert response.status_code == 422
        assert "twice" in response.json()["detail"]

    async def test_a_duplicate_request_id_returns_the_same_appointment(
        self, client, db, owner_a, tenant
    ):
        headers = await auth_headers(client, owner_a)
        payload = {
            "customer_name": "Jane", "customer_phone": "+15551230000",
            "starts_at_local": f"{TUESDAY}T10:00:00",
            "request_id": "client-req-1",
        }
        first = await client.post("/api/appointments", headers=headers, json=payload)
        second = await client.post("/api/appointments", headers=headers, json=payload)

        assert first.status_code == 201
        assert second.status_code == 201
        assert first.json()["id"] == second.json()["id"]

        total = (await db.execute(select(func.count(Appointment.id)))).scalar()
        assert total == 1

    async def test_reschedule_and_cancel_through_the_api(
        self, client, db, owner_a, tenant
    ):
        appointment = await book(db, tenant, 10)
        headers = await auth_headers(client, owner_a)

        moved = await client.patch(
            f"/api/appointments/{appointment.id}",
            headers=headers,
            json={"starts_at_local": f"{TUESDAY}T14:00:00"},
        )
        assert moved.status_code == 200
        assert moved.json()["starts_at"] == ny(TUESDAY, 14).isoformat()
        assert moved.json()["rescheduled_from"] == ny(TUESDAY, 10).isoformat()

        cancelled = await client.post(
            f"/api/appointments/{appointment.id}/cancel",
            headers=headers, json={"reason": "changed plans"},
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"
        assert cancelled.json()["cancellation_reason"] == "changed plans"

    async def test_listing_and_filtering(self, client, db, owner_a, tenant):
        await book(db, tenant, 9, customer_phone="+15550000001")
        await book(db, tenant, 10, customer_phone="+15550000002")
        headers = await auth_headers(client, owner_a)

        listed = await client.get("/api/appointments", headers=headers)
        assert listed.json()["total"] == 2

        filtered = await client.get(
            "/api/appointments?status=cancelled", headers=headers
        )
        assert filtered.json()["total"] == 0

    async def test_a_viewer_cannot_book(self, client, db, viewer_a, tenant):
        headers = await auth_headers(client, viewer_a)
        response = await client.post(
            "/api/appointments",
            headers=headers,
            json={
                "customer_name": "Jane", "customer_phone": "+15551230000",
                "starts_at_local": f"{TUESDAY}T10:00:00",
            },
        )
        assert response.status_code == 403

    async def test_unauthenticated_requests_are_rejected(self, client, tenant):
        for method, path, body in (
            ("get", "/api/appointments", None),
            ("get", f"/api/appointments/availability?day={TUESDAY}", None),
            ("post", "/api/appointments", {}),
        ):
            kwargs = {"json": body} if body is not None else {}
            response = await getattr(client, method)(path, **kwargs)
            assert response.status_code in (401, 403), path


# ========================================================== policy API ===

class TestPolicyApi:
    async def test_reading_the_policy(self, client, db, owner_a, tenant):
        headers = await auth_headers(client, owner_a)
        response = await client.get("/api/calendar/policy", headers=headers)

        assert response.status_code == 200
        body = response.json()
        assert body["configured"] is True
        assert body["timezone"] == NY
        assert body["weekly_hours"]["sat"] == []

    async def test_a_tenant_without_a_policy_sees_the_legacy_fallback(
        self, client, db, owner_a, tenant_a
    ):
        headers = await auth_headers(client, owner_a)
        body = (await client.get("/api/calendar/policy", headers=headers)).json()
        assert body["configured"] is False
        assert body["weekly_hours"]

    async def test_updating_the_policy(self, client, db, owner_a, tenant):
        headers = await auth_headers(client, owner_a)
        response = await client.put(
            "/api/calendar/policy",
            headers=headers,
            json={
                "weekly_hours": {"mon": [["10:00", "16:00"]], "tue": []},
                "holidays": ["2026-12-25"],
                "slot_minutes": 45,
                "slot_interval_minutes": 15,
                "buffer_before_minutes": 10,
                "minimum_notice_minutes": 120,
                "timezone": "Asia/Dhaka",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["slot_minutes"] == 45
        assert body["timezone"] == "Asia/Dhaka"

    async def test_an_invalid_timezone_is_rejected(self, client, db, owner_a, tenant):
        headers = await auth_headers(client, owner_a)
        response = await client.put(
            "/api/calendar/policy", headers=headers,
            json={"timezone": "Mars/Olympus"},
        )
        assert response.status_code == 422
        assert "IANA" in response.json()["detail"]

    async def test_invalid_hours_are_rejected_at_write_time(
        self, client, db, owner_a, tenant
    ):
        """A bad schedule must be a 422 now, not a mystery at 3 AM."""
        headers = await auth_headers(client, owner_a)
        response = await client.put(
            "/api/calendar/policy", headers=headers,
            json={"weekly_hours": {"mon": [["17:00", "09:00"]]}},
        )
        assert response.status_code == 422

    async def test_an_interval_longer_than_the_slot_is_rejected(
        self, client, db, owner_a, tenant
    ):
        headers = await auth_headers(client, owner_a)
        response = await client.put(
            "/api/calendar/policy", headers=headers,
            json={"slot_minutes": 15, "slot_interval_minutes": 60},
        )
        assert response.status_code == 422


# ================================================= calendar integration API ===

class TestCalendarIntegrationApi:
    async def test_connecting_a_provider(self, client, db, owner_a, tenant_a):
        headers = await auth_headers(client, owner_a)
        response = await client.put(
            "/api/calendar/integrations/google",
            headers=headers,
            json={
                "credentials": {
                    "access_token": "ya29-REAL-TOKEN", "refresh_token": "1//real",
                    "client_id": "cid", "client_secret": "csec",
                },
                "config": {"calendar_id": "primary"},
                "is_primary": True,
            },
        )
        assert response.status_code == 200, response.text

        body = response.json()
        assert body["connected"] is True
        assert body["config"]["calendar_id"] == "primary"
        # No token, anywhere.
        assert "ya29-REAL-TOKEN" not in response.text
        assert "credentials" not in body

    async def test_stored_credentials_are_ciphertext(self, db, tenant_a):
        integration = await make_calendar_integration(
            db, tenant_a, "google",
            credentials={"access_token": "ya29-PLAINTEXT-CHECK"},
        )
        assert integration.credentials_encrypted.startswith("v1.")
        assert "ya29-PLAINTEXT-CHECK" not in integration.credentials_encrypted

    async def test_credentials_are_bound_to_their_tenant_and_provider(
        self, db, tenant_a, tenant_b
    ):
        """
        Requirement 24: changing tenant or provider must not make another
        tenant's ciphertext decryptable. The AES-GCM associated data is
        `(tenant, provider)`, so a copied row fails to authenticate.
        """
        from app.core.config import settings
        from app.integrations.crm import crypto

        integration = await make_calendar_integration(db, tenant_a, "google")
        ring = crypto.parse_key_ring(settings.crm_encryption_keys)

        with pytest.raises(crypto.CredentialDecryptionError):
            crypto.decrypt_credentials(
                integration.credentials_encrypted,
                tenant_id=str(tenant_b.id), provider="google", key_ring=ring,
            )
        with pytest.raises(crypto.CredentialDecryptionError):
            crypto.decrypt_credentials(
                integration.credentials_encrypted,
                tenant_id=str(tenant_a.id), provider="microsoft", key_ring=ring,
            )

    async def test_an_unknown_credential_field_is_rejected(
        self, client, db, owner_a, tenant_a
    ):
        headers = await auth_headers(client, owner_a)
        response = await client.put(
            "/api/calendar/integrations/google",
            headers=headers,
            json={"credentials": {"access_token": "t", "admin_override": "x"}},
        )
        assert response.status_code == 422
        assert "admin_override" in response.text

    async def test_calcom_requires_an_event_type(self, client, db, owner_a, tenant_a):
        headers = await auth_headers(client, owner_a)
        response = await client.put(
            "/api/calendar/integrations/calcom",
            headers=headers, json={"credentials": {"api_key": "cal_live_x"}},
        )
        assert response.status_code == 422
        assert "event_type_id" in response.text

    async def test_the_provider_catalogue_reports_honest_capabilities(
        self, client, db, owner_a
    ):
        headers = await auth_headers(client, owner_a)
        response = await client.get("/api/calendar/providers", headers=headers)

        catalogue = {p["provider"]: p for p in response.json()["providers"]}
        assert set(catalogue) == {p.value for p in CalendarProviderType}
        # A dashboard can grey out what a provider cannot do.
        assert "free_busy" not in catalogue["calcom"]["capabilities"]
        assert "cancel_event" not in catalogue["google_service_account"]["capabilities"]
        assert "reschedule" in catalogue["calcom"]["capabilities"]

    async def test_setting_a_new_primary_demotes_the_old_one(
        self, client, db, owner_a, tenant_a
    ):
        await make_calendar_integration(db, tenant_a, "google", primary=True)
        headers = await auth_headers(client, owner_a)

        await client.put(
            "/api/calendar/integrations/calcom",
            headers=headers,
            json={
                "credentials": {"api_key": "cal_live_x"},
                "config": {"event_type_id": 1},
                "is_primary": True,
            },
        )
        rows = (
            (await db.execute(select(CalendarIntegration))).scalars().all()
        )
        for row in rows:
            await db.refresh(row)
        assert sum(1 for row in rows if row.is_primary) == 1

    async def test_the_health_check_never_leaks(
        self, client, db, owner_a, tenant_a, monkeypatch
    ):
        from tests.conftest import FakeTransport

        await make_calendar_integration(
            db, tenant_a, "google",
            credentials={"access_token": "ya29-SECRET-HEALTH"},
        )
        FakeTransport((401, {"token": "ya29-SECRET-HEALTH"})).install(monkeypatch)

        headers = await auth_headers(client, owner_a)
        response = await client.post(
            "/api/calendar/integrations/google/test", headers=headers
        )
        assert response.status_code == 200
        assert response.json()["connected"] is False
        assert "ya29-SECRET-HEALTH" not in response.text

    async def test_no_credential_reaches_any_response(
        self, client, db, owner_a, tenant_a
    ):
        await make_calendar_integration(
            db, tenant_a, "microsoft",
            credentials={"access_token": "eyJ-LEAK-CHECK", "refresh_token": "M.R-LEAK"},
        )
        headers = await auth_headers(client, owner_a)

        for path in (
            "/api/calendar/integrations",
            "/api/calendar/providers",
            "/api/calendar/policy",
        ):
            response = await client.get(path, headers=headers)
            assert response.status_code == 200, path
            assert "eyJ-LEAK-CHECK" not in response.text, path
            assert "M.R-LEAK" not in response.text, path

    async def test_connecting_writes_no_secret_to_the_audit_log(
        self, client, db, owner_a, tenant_a
    ):
        headers = await auth_headers(client, owner_a)
        await client.put(
            "/api/calendar/integrations/google",
            headers=headers,
            json={"credentials": {"access_token": "ya29-AUDIT-LEAK"}},
        )
        rows = (await db.execute(select(AuditLog))).scalars().all()
        everything = json.dumps([r.detail for r in rows])

        assert "ya29-AUDIT-LEAK" not in everything
        # The field *names* are recorded under a neutral metadata key so the
        # audit redactor does not suppress the safe list of submitted names.
        assert "provided_field_names" in everything
        assert "access_token" in everything


# =========================================================== isolation (23) ===

class TestTenantIsolation:
    async def test_tenant_b_cannot_read_tenant_a_appointments(
        self, client, db, owner_b, tenant, other
    ):
        appointment = await book(db, tenant, 10)
        headers = await auth_headers(client, owner_b)

        # 404, not 403 -- a 403 would confirm the id exists.
        one = await client.get(f"/api/appointments/{appointment.id}", headers=headers)
        assert one.status_code == 404

        listed = await client.get("/api/appointments", headers=headers)
        assert listed.json()["total"] == 0

    async def test_a_wrong_id_and_someone_elses_id_are_indistinguishable(
        self, client, db, owner_b, tenant, other
    ):
        appointment = await book(db, tenant, 10)
        headers = await auth_headers(client, owner_b)

        theirs = await client.get(f"/api/appointments/{appointment.id}", headers=headers)
        nobodys = await client.get(f"/api/appointments/{uuid.uuid4()}", headers=headers)
        assert theirs.status_code == nobodys.status_code == 404
        assert theirs.json() == nobodys.json()

    async def test_tenant_b_cannot_reschedule_tenant_a_appointment(
        self, client, db, owner_b, tenant, other
    ):
        appointment = await book(db, tenant, 10)
        headers = await auth_headers(client, owner_b)

        response = await client.patch(
            f"/api/appointments/{appointment.id}",
            headers=headers, json={"starts_at_local": f"{TUESDAY}T14:00:00"},
        )
        assert response.status_code == 404

        await db.refresh(appointment)
        assert as_utc(appointment.starts_at) == ny(TUESDAY, 10)

    async def test_tenant_b_cannot_cancel_tenant_a_appointment(
        self, client, db, owner_b, tenant, other
    ):
        appointment = await book(db, tenant, 10)
        headers = await auth_headers(client, owner_b)

        response = await client.post(
            f"/api/appointments/{appointment.id}/cancel", headers=headers, json={}
        )
        assert response.status_code == 404

        await db.refresh(appointment)
        assert appointment.status is AppointmentStatus.CONFIRMED

    async def test_tenant_b_sees_their_own_availability_not_tenant_a_s(
        self, client, db, owner_b, tenant, other
    ):
        """
        Tenant A booking 10 AM must not remove 10 AM from Tenant B's diary.
        Two businesses are not in competition for the same hour.
        """
        await book(db, tenant, 10)
        headers = await auth_headers(client, owner_b)

        response = await client.get(
            f"/api/appointments/availability?day={TUESDAY}&part_of_day=morning",
            headers=headers,
        )
        starts = {slot["start"] for slot in response.json()["slots"]}
        assert ny(TUESDAY, 10).isoformat() in starts

    async def test_tenant_b_cannot_read_tenant_a_calendar_integration(
        self, client, db, owner_b, tenant_a, tenant_b
    ):
        await make_calendar_integration(
            db, tenant_a, "google", config={"calendar_id": "TENANT-A-CAL"}
        )
        await make_calendar_integration(
            db, tenant_b, "google", config={"calendar_id": "TENANT-B-CAL"}
        )
        headers = await auth_headers(client, owner_b)

        response = await client.get("/api/calendar/integrations", headers=headers)
        assert "TENANT-A-CAL" not in response.text
        assert "TENANT-B-CAL" in response.text

    async def test_tenant_b_cannot_test_tenant_a_calendar_credentials(
        self, client, db, owner_b, tenant_a
    ):
        """Spending A's rate limit and probing whether their token is live."""
        await make_calendar_integration(db, tenant_a, "google")
        headers = await auth_headers(client, owner_b)
        response = await client.post(
            "/api/calendar/integrations/google/test", headers=headers
        )
        assert response.status_code == 404

    async def test_a_tenant_id_in_the_body_is_ignored(
        self, client, db, owner_b, tenant, other
    ):
        """Requirement 14: no client-controlled tenant_id authority."""
        headers = await auth_headers(client, owner_b)
        response = await client.post(
            "/api/appointments",
            headers=headers,
            json={
                "tenant_id": str(tenant.id),
                "customer_name": "Injected", "customer_phone": "+15557770000",
                "starts_at_local": f"{TUESDAY}T10:00:00",
            },
        )
        assert response.status_code == 201

        # It landed in B's diary, not A's.
        appointment = await db.get(Appointment, uuid.UUID(response.json()["id"]))
        assert appointment.tenant_id == other.id

    async def test_the_booking_service_never_crosses_tenants(
        self, db, tenant, other
    ):
        """
        Both tenants can hold the same slot. `slot_key` is salted with the
        tenant id, so their unique constraints cannot collide.
        """
        first = await book(db, tenant, 10)
        second = await book(db, other, 10, tenant_id=other.id)
        assert first.slot_key != second.slot_key
        assert first.tenant_id != second.tenant_id

    async def test_the_routes_use_the_permission_enum_not_role_strings(self):
        import pathlib

        for path in (
            "app/api/appointment_routes.py",
            "app/api/calendar_webhook_routes.py",
        ):
            source = pathlib.Path(path).read_text()
            assert '== "admin"' not in source, path
            assert ".role ==" not in source, path


# ============================================================= voice tools ===

class TestVoiceTools:
    @pytest.fixture
    def tools(self, db, tenant):
        from app.integrations.calendar.tools import SchedulingTools

        return SchedulingTools(db, tenant, None, now=ny(TUESDAY, 6))

    async def test_availability_returns_structured_slots(self, tools):
        result = await tools.check_availability("today")
        assert result["outcome"] == "AVAILABLE_SLOTS"
        assert result["ok"] is True
        assert result["slots"]
        assert all("spoken" in slot for slot in result["slots"])

    async def test_booking_returns_booked_only_after_a_provider_accepts(self, tools):
        result = await tools.book_appointment(
            customer_name="Jane Doe", customer_phone="+15551230000",
            when="today", at="10am", reason="Cleaning",
        )
        assert result["outcome"] == "BOOKED"
        assert "10 am" in result["message"]

    async def test_a_provider_failure_never_returns_booked(
        self, db, tenant_a, monkeypatch
    ):
        """
        **Requirement 15's headline.** The tool result is the source of truth,
        and the model can never see BOOKED unless a provider accepted.
        """
        from app.integrations.calendar.errors import CalendarTemporaryError
        from app.integrations.calendar.providers.google import GoogleCalendarProvider
        from app.integrations.calendar.tools import SchedulingTools

        tenant_a.timezone = NY
        await db.commit()
        await make_scheduling_policy(db, tenant_a)
        await make_calendar_integration(db, tenant_a, "google")

        async def boom(self, request):
            raise CalendarTemporaryError("down", provider="google")

        monkeypatch.setattr(GoogleCalendarProvider, "create_event", boom)

        tools = SchedulingTools(db, tenant_a, None, now=ny(TUESDAY, 6))
        result = await tools.book_appointment(
            customer_name="Jane", customer_phone="+15551230000",
            when="today", at="10am",
        )
        assert result["outcome"] == "FAILED"
        assert result["ok"] is False
        assert "booked" not in result["message"].lower()

    async def test_the_payload_never_carries_provider_detail(
        self, db, tenant_a, monkeypatch
    ):
        """The caller must never hear "Google returned 401"."""
        from app.integrations.calendar.errors import CalendarAuthError
        from app.integrations.calendar.providers.google import GoogleCalendarProvider
        from app.integrations.calendar.tools import SchedulingTools

        tenant_a.timezone = NY
        await db.commit()
        await make_scheduling_policy(db, tenant_a)
        await make_calendar_integration(db, tenant_a, "google")

        async def refuse(self, request):
            raise CalendarAuthError("HTTP 401 token revoked", provider="google")

        monkeypatch.setattr(GoogleCalendarProvider, "create_event", refuse)

        tools = SchedulingTools(db, tenant_a, None, now=ny(TUESDAY, 6))
        result = await tools.book_appointment(
            customer_name="Jane", customer_phone="+15551230000",
            when="today", at="10am",
        )
        blob = json.dumps(result).lower()
        assert "google" not in blob
        assert "401" not in blob
        assert "token" not in blob

    async def test_the_outcome_vocabulary_is_closed(self):
        """
        There is no value the model can receive that means "booked" unless the
        service wrote it after a provider acknowledgement.
        """
        from app.integrations.calendar.models import BookingOutcome

        assert BookingOutcome.BOOKED.value == "BOOKED"
        assert len(set(BookingOutcome)) == len(BookingOutcome)

    async def test_missing_arguments_produce_a_question_not_a_booking(self, tools):
        no_name = await tools.book_appointment(
            customer_name="", customer_phone="+15551230000", when="today", at="10am"
        )
        assert no_name["outcome"] == "NEEDS_CLARIFICATION"
        assert "name" in no_name["message"].lower()

        bad_phone = await tools.book_appointment(
            customer_name="Jane", customer_phone="12", when="today", at="10am"
        )
        assert bad_phone["outcome"] == "NEEDS_CLARIFICATION"
        assert "number" in bad_phone["message"].lower()

    async def test_an_ambiguous_day_asks_rather_than_guessing(self, tools):
        result = await tools.check_availability("Tuesday")
        assert result["outcome"] == "NEEDS_CLARIFICATION"
        assert result["ask"]

    async def test_an_ambiguous_hour_asks(self, tools):
        result = await tools.book_appointment(
            customer_name="Jane", customer_phone="+15551230000",
            when="tomorrow", at="seven",
        )
        assert result["outcome"] == "NEEDS_CLARIFICATION"

    async def test_a_nonexistent_local_time_asks_rather_than_shifting(
        self, db, tenant
    ):
        from app.integrations.calendar.tools import SchedulingTools

        tools = SchedulingTools(
            db, tenant, None, now=resolve_local(datetime(2026, 3, 1, 9, 0), NY).utc
        )
        result = await tools.book_appointment(
            customer_name="Jane", customer_phone="+15551230000",
            when="2026-03-08", at="2:30am",
        )
        assert result["outcome"] == "NEEDS_CLARIFICATION"
        assert "forward" in result["message"]

    async def test_reschedule_and_cancel_by_phone(self, db, tenant, tools):
        await book(db, tenant, 10)

        moved = await tools.reschedule_appointment(
            customer_phone="+1 (555) 123-0000", when="today", at="2pm"
        )
        assert moved["outcome"] == "RESCHEDULED"

        cancelled = await tools.cancel_appointment(customer_phone="+15551230000")
        assert cancelled["outcome"] == "CANCELLED"

    async def test_an_unknown_caller_gets_not_found(self, tools):
        result = await tools.cancel_appointment(customer_phone="+15559998888")
        assert result["outcome"] == "NOT_FOUND"

    async def test_confirm_is_honest_about_a_pending_booking(self, db, tenant):
        from app.integrations.calendar.tools import SchedulingTools

        appointment = await book(db, tenant, 10)
        appointment.status = AppointmentStatus.PENDING
        await db.commit()

        tools = SchedulingTools(db, tenant, None, now=ny(TUESDAY, 6))
        result = await tools.confirm_appointment(customer_phone="+15551230000")
        assert result["outcome"] == "CONFIRMED"
        assert "not fully confirmed" in result["message"]

    async def test_a_tool_cannot_reach_another_tenants_diary(
        self, db, tenant, other
    ):
        """
        The tenant comes from the bound call, never from an argument, so no
        tool call can address another business.
        """
        from app.integrations.calendar.tools import SchedulingTools

        await book(db, tenant, 10)
        tools_for_b = SchedulingTools(db, other, None, now=ny(TUESDAY, 6))
        result = await tools_for_b.cancel_appointment(customer_phone="+15551230000")
        assert result["outcome"] == "NOT_FOUND"

    async def test_the_voice_deadline_is_enforced(self, db, tenant, monkeypatch):
        """
        Requirement 30: an availability lookup must not hang. Three seconds of
        silence on a phone call is already a problem.
        """
        from app.core.config import settings
        from app.integrations.calendar.tools import SchedulingTools

        monkeypatch.setattr(settings, "calendar_voice_timeout_seconds", 0.1)

        async def never_returns(*args, **kwargs):
            await asyncio.sleep(30)

        monkeypatch.setattr(service, "find_slots", never_returns)

        tools = SchedulingTools(db, tenant, None, now=ny(TUESDAY, 6))
        started = _time.perf_counter()
        result = await tools.check_availability("today")
        elapsed = _time.perf_counter() - started

        assert result["outcome"] == "FAILED"
        assert elapsed < 2.0, f"the tool waited {elapsed:.1f}s"

    async def test_the_agent_dispatch_routes_to_the_new_layer(self, db, tenant):
        from app.agent.functions import SCHEDULING_TOOL_NAMES, FunctionHandlers
        from app.db.models import Call, CallDirection, CallStatus

        call = Call(
            tenant_id=tenant.id, direction=CallDirection.INBOUND,
            status=CallStatus.IN_PROGRESS, from_number="+15551230000",
            to_number=tenant.twilio_number, call_sid="CAtest",
        )
        db.add(call)
        await db.commit()

        handlers = FunctionHandlers(db, tenant, call)
        result = await handlers.dispatch("check_availability", {"when": "tomorrow"})

        assert "outcome" in result, "dispatch did not reach the STEP 6 layer"
        assert "check_availability" in SCHEDULING_TOOL_NAMES


# =============================================================== webhooks ===

class TestCalendarWebhooks:
    @pytest.fixture
    async def calcom_hook(self, db, tenant_a):
        from app.api.calendar_webhook_routes import routing_token

        integration = await make_calendar_integration(
            db, tenant_a, "calcom",
            credentials={"api_key": "cal_live_x", "webhook_secret": "hook-secret-123"},
            config={"event_type_id": 1},
        )
        return integration, routing_token(integration)

    @staticmethod
    def _sign(body: str, secret: str) -> dict:
        import hashlib
        import hmac

        return {
            "X-Cal-Signature-256": hmac.new(
                secret.encode(), body.encode(), hashlib.sha256
            ).hexdigest(),
            "Content-Type": "application/json",
        }

    async def test_a_valid_signed_notification_is_accepted(self, client, calcom_hook):
        _, token = calcom_hook
        body = json.dumps({"id": "evt-1", "triggerEvent": "BOOKING_CREATED"})

        response = await client.post(
            f"/api/calendar/webhooks/calcom/{token}",
            content=body, headers=self._sign(body, "hook-secret-123"),
        )
        assert response.status_code == 200
        assert response.json()["duplicate"] is False

    async def test_an_invalid_signature_is_rejected(self, client, calcom_hook):
        _, token = calcom_hook
        body = json.dumps({"id": "evt-2"})
        response = await client.post(
            f"/api/calendar/webhooks/calcom/{token}",
            content=body, headers=self._sign(body, "wrong-secret"),
        )
        assert response.status_code == 401

    async def test_a_forged_routing_token_is_rejected(self, client, calcom_hook):
        body = json.dumps({"id": "evt-3"})
        response = await client.post(
            "/api/calendar/webhooks/calcom/" + "f" * 40,
            content=body, headers=self._sign(body, "hook-secret-123"),
        )
        assert response.status_code == 401

    async def test_a_replayed_notification_is_ignored(
        self, client, db, calcom_hook, tenant_a
    ):
        _, token = calcom_hook
        body = json.dumps({"id": "evt-replay", "triggerEvent": "BOOKING_CREATED"})
        headers = self._sign(body, "hook-secret-123")

        first = await client.post(
            f"/api/calendar/webhooks/calcom/{token}", content=body, headers=headers
        )
        second = await client.post(
            f"/api/calendar/webhooks/calcom/{token}", content=body, headers=headers
        )
        assert first.json()["duplicate"] is False
        # 200, not 409: a provider that sees an error simply retries.
        assert second.status_code == 200
        assert second.json()["duplicate"] is True

        count = (
            await db.execute(select(func.count(CalendarWebhookReceipt.id)))
        ).scalar()
        assert count == 1

    async def test_a_tenant_id_in_the_body_does_not_route(
        self, client, db, calcom_hook, tenant_b
    ):
        """Requirement 21: the path token decides, the body is data."""
        integration, token = calcom_hook
        body = json.dumps({"id": "evt-inject", "tenant_id": str(tenant_b.id)})

        response = await client.post(
            f"/api/calendar/webhooks/calcom/{token}",
            content=body, headers=self._sign(body, "hook-secret-123"),
        )
        assert response.status_code == 200

        receipts = (
            (await db.execute(select(CalendarWebhookReceipt))).scalars().all()
        )
        assert len(receipts) == 1
        assert receipts[0].tenant_id == integration.tenant_id

    async def test_a_provider_cancellation_frees_the_slot(
        self, client, db, calcom_hook, tenant_a
    ):
        """
        The one mutation this endpoint performs, and the case where the
        provider is unambiguously authoritative: the event no longer exists on
        their calendar, so holding the slot would block a real booking.
        """
        integration, token = calcom_hook
        await make_scheduling_policy(db, tenant_a)
        tenant_a.timezone = NY
        await db.commit()

        appointment = Appointment(
            tenant_id=tenant_a.id, customer_name="Jane",
            customer_phone="+15551230000",
            starts_at=ny(TUESDAY, 10), ends_at=ny(TUESDAY, 10, 30),
            timezone=NY, status=AppointmentStatus.CONFIRMED,
            provider=CalendarProviderType.CALCOM,
            external_event_id="cal-booking-1", slot_key="slot-1",
        )
        db.add(appointment)
        await db.commit()

        body = json.dumps({
            "id": "evt-cancel", "triggerEvent": "BOOKING_CANCELLED",
            "payload": {"uid": "cal-booking-1"},
        })
        response = await client.post(
            f"/api/calendar/webhooks/calcom/{token}",
            content=body, headers=self._sign(body, "hook-secret-123"),
        )
        assert response.json()["applied"] is True

        await db.refresh(appointment)
        assert appointment.status is AppointmentStatus.CANCELLED
        assert appointment.cancelled_by == "provider:calcom"
        assert appointment.slot_key is None

    async def test_a_cancellation_cannot_reach_another_tenants_appointment(
        self, client, db, calcom_hook, tenant_b
    ):
        """
        Scoped by tenant *and* external id. The event id alone would be a
        cross-tenant write primitive.
        """
        appointment = Appointment(
            tenant_id=tenant_b.id, customer_name="Someone Else",
            customer_phone="+15559990000",
            starts_at=ny(TUESDAY, 10), ends_at=ny(TUESDAY, 10, 30),
            timezone=NY, status=AppointmentStatus.CONFIRMED,
            external_event_id="cal-booking-1", slot_key="slot-b",
        )
        db.add(appointment)
        await db.commit()

        _, token = calcom_hook
        body = json.dumps({
            "id": "evt-x", "triggerEvent": "BOOKING_CANCELLED",
            "payload": {"uid": "cal-booking-1"},
        })
        response = await client.post(
            f"/api/calendar/webhooks/calcom/{token}",
            content=body, headers=self._sign(body, "hook-secret-123"),
        )
        assert response.json()["applied"] is False

        await db.refresh(appointment)
        assert appointment.status is AppointmentStatus.CONFIRMED

    async def test_a_provider_with_no_stored_secret_fails_closed(
        self, client, db, tenant_a
    ):
        """
        An unverified inbound endpoint is worse than none: it is a public,
        tenant-addressable write path.
        """
        from app.api.calendar_webhook_routes import routing_token

        integration = await make_calendar_integration(
            db, tenant_a, "google", credentials={"access_token": "x"}
        )
        response = await client.post(
            f"/api/calendar/webhooks/google/{routing_token(integration)}",
            json={"id": "evt"},
        )
        assert response.status_code == 401

    async def test_a_google_channel_token_is_verified(self, client, db, tenant_a):
        from app.api.calendar_webhook_routes import routing_token

        integration = await make_calendar_integration(
            db, tenant_a, "google",
            credentials={"access_token": "x", "webhook_secret": "chan-secret"},
        )
        token = routing_token(integration)

        good = await client.post(
            f"/api/calendar/webhooks/google/{token}",
            json={},
            headers={
                "X-Goog-Channel-Token": "chan-secret",
                "X-Goog-Message-Number": "7",
                "X-Goog-Channel-ID": "chan-1",
            },
        )
        assert good.status_code == 200

        bad = await client.post(
            f"/api/calendar/webhooks/google/{token}",
            json={}, headers={"X-Goog-Channel-Token": "wrong"},
        )
        assert bad.status_code == 401

    async def test_a_graph_validation_token_is_echoed(self, client, db, tenant_a):
        """
        Graph validates a new subscription by POSTing a token that must come
        back as plain text within seconds -- before any clientState exists.
        """
        from app.api.calendar_webhook_routes import routing_token

        integration = await make_calendar_integration(db, tenant_a, "microsoft")
        response = await client.post(
            f"/api/calendar/webhooks/microsoft/{routing_token(integration)}"
            f"?validationToken=abc123",
            content=b"",
        )
        assert response.status_code == 200
        assert response.text == "abc123"

    async def test_every_rejection_looks_the_same(self, client, calcom_hook):
        """
        The endpoint must not become an oracle for which tokens or providers
        are live.
        """
        _, real = calcom_hook
        body = json.dumps({"id": "x"})

        responses = [
            await client.post(
                "/api/calendar/webhooks/calcom/" + "0" * 40,
                content=body, headers=self._sign(body, "hook-secret-123"),
            ),
            await client.post(
                "/api/calendar/webhooks/nosuchprovider/" + "0" * 40,
                content=body, headers=self._sign(body, "hook-secret-123"),
            ),
            await client.post(
                f"/api/calendar/webhooks/calcom/{real}",
                content=body, headers=self._sign(body, "wrong"),
            ),
        ]
        assert {r.status_code for r in responses} == {401}
        assert len({r.text for r in responses}) == 1

    async def test_receipts_can_be_pruned(self, db, tenant_a):
        from app.api.calendar_webhook_routes import prune_receipts

        db.add(CalendarWebhookReceipt(
            tenant_id=tenant_a.id, provider=CalendarProviderType.CALCOM,
            provider_event_id="old",
            received_at=datetime.utcnow() - timedelta(days=90),
        ))
        db.add(CalendarWebhookReceipt(
            tenant_id=tenant_a.id, provider=CalendarProviderType.CALCOM,
            provider_event_id="new",
        ))
        await db.commit()

        assert await prune_receipts(db, older_than_days=30) == 1


# ============================================================ performance ===

class TestServiceOverhead:
    """
    Requirement 30, honestly scoped.

    These measure **VoxDesk's own overhead** against the internal provider —
    policy evaluation, slot generation, the database round trips. They say
    nothing about Google or Microsoft latency, and no real provider was
    contacted, so no such claim is made.

    Thresholds are deliberately loose. What matters is catching a change of
    *complexity* — an O(n²) slot filter, a per-slot query — not a busy CI box.
    """

    @staticmethod
    async def _median_ms(coro_factory, runs: int = 5) -> float:
        samples = []
        for _ in range(runs):
            started = _time.perf_counter()
            await coro_factory()
            samples.append((_time.perf_counter() - started) * 1000)
        return statistics.median(samples)

    async def test_availability_lookup_overhead(self, db, tenant, capsys):
        async def run():
            await service.find_slots(db, tenant, day=TUESDAY, limit=50, now=ny(TUESDAY, 6))

        median = await self._median_ms(run)
        with capsys.disabled():
            print(f"\n  availability lookup: {median:.2f} ms")
        assert median < 500, f"{median:.1f} ms is far beyond service overhead"

    async def test_availability_scales_with_the_diary_not_quadratically(
        self, db, tenant, capsys
    ):
        """
        The guard that actually matters. A quadratic filter or a per-slot query
        would show up as a ratio far above the load increase.
        """
        async def run():
            await service.find_slots(db, tenant, day=TUESDAY, limit=50, now=ny(TUESDAY, 6))

        empty = await self._median_ms(run)

        for hour in (9, 10, 11, 13, 14, 15):
            await book(db, tenant, hour, customer_phone=f"+1555000{hour:04d}")
        loaded = await self._median_ms(run)

        ratio = loaded / max(empty, 0.01)
        with capsys.disabled():
            print(
                f"  availability empty {empty:.2f} ms -> 6 booked {loaded:.2f} ms "
                f"(x{ratio:.1f})"
            )
        assert ratio < 15, f"availability degraded {ratio:.1f}x under six bookings"

    async def test_booking_overhead(self, db, tenant, capsys):
        samples = []
        for index, hour in enumerate((9, 10, 11, 13, 14)):
            started = _time.perf_counter()
            await book(db, tenant, hour, customer_phone=f"+1555111{index:04d}")
            samples.append((_time.perf_counter() - started) * 1000)

        median = statistics.median(samples)
        with capsys.disabled():
            print(f"  booking: {median:.2f} ms")
        assert median < 500

    async def test_reschedule_and_cancel_overhead(self, db, tenant, capsys):
        appointment = await book(db, tenant, 9)

        started = _time.perf_counter()
        await service.reschedule(
            db, tenant, appointment, ny(TUESDAY, 15), now=ny(TUESDAY, 6)
        )
        reschedule_ms = (_time.perf_counter() - started) * 1000

        started = _time.perf_counter()
        await service.cancel(db, tenant, appointment)
        cancel_ms = (_time.perf_counter() - started) * 1000

        with capsys.disabled():
            print(
                f"  reschedule: {reschedule_ms:.2f} ms   cancel: {cancel_ms:.2f} ms"
            )
        assert reschedule_ms < 500 and cancel_ms < 500

    async def test_a_hung_provider_cannot_outlast_the_voice_deadline(
        self, db, tenant, monkeypatch, capsys
    ):
        """
        The one measurement that is a product requirement rather than a guard
        rail: with the lookup stubbed to hang forever, the tool still returns.
        """
        from app.core.config import settings
        from app.integrations.calendar.tools import SchedulingTools

        monkeypatch.setattr(settings, "calendar_voice_timeout_seconds", 0.2)

        async def never(*args, **kwargs):
            await asyncio.sleep(60)

        monkeypatch.setattr(service, "find_slots", never)

        tools = SchedulingTools(db, tenant, None, now=ny(TUESDAY, 6))
        started = _time.perf_counter()
        result = await tools.check_availability("today")
        elapsed = (_time.perf_counter() - started) * 1000

        with capsys.disabled():
            print(f"  hung provider, 200 ms budget -> returned at {elapsed:.2f} ms")
        assert result["outcome"] == "FAILED"
        assert elapsed < 1000
````

### File 09: `tests/test_crm_tenant_isolation.py`

SHA-256: `95da81cc8003e88f3da128086440c970e064fc35bdb815d5f7aeceaf91af22b8`

````
"""
Tenant isolation and API security for CRM integrations.

Requirement 22's list, one test each, plus the cases that list implies.

The premise throughout: **an id is not authorization.** Every test here gives
Tenant B a genuine, authenticated session and a correct id belonging to Tenant
A, and asserts that being right about the id changes nothing.
"""
from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import select

from app.db.models import (
    AuditLog,
    CrmEvent,
    CrmEventType,
    CrmIntegration,
    CrmProviderType,
    CrmSync,
)
from tests.conftest import auth_headers, make_integration

TOKEN_A = "pit-tenant-a-secret-token-1234567890"
TOKEN_B = "pit-tenant-b-secret-token-0987654321"


@pytest.fixture
async def integration_a(db, tenant_a):
    return await make_integration(
        db, tenant_a, "gohighlevel",
        credentials={"access_token": TOKEN_A},
        config={"location_id": "loc-A", "base_url": "https://ghl.test"},
    )


@pytest.fixture
async def integration_b(db, tenant_b):
    return await make_integration(
        db, tenant_b, "gohighlevel",
        credentials={"access_token": TOKEN_B},
        config={"location_id": "loc-B", "base_url": "https://ghl.test"},
    )


# ================================================== cross-tenant reads ===

async def test_tenant_b_cannot_read_tenant_a_integration(
    client, db, owner_b, integration_a, integration_b
):
    headers = await auth_headers(client, owner_b)
    response = await client.get("/api/integrations/crm/gohighlevel", headers=headers)

    assert response.status_code == 200
    body = response.json()
    # B sees B's own integration, never A's.
    assert body["config"]["location_id"] == "loc-B"
    assert "loc-A" not in json.dumps(body)


async def test_tenant_b_listing_never_includes_tenant_a(
    client, db, owner_b, integration_a, integration_b
):
    headers = await auth_headers(client, owner_b)
    response = await client.get("/api/integrations/crm", headers=headers)

    assert response.status_code == 200
    body = json.dumps(response.json())
    assert "loc-B" in body
    assert "loc-A" not in body


async def test_a_tenant_with_no_integration_gets_404_not_someone_elses(
    client, db, owner_b, integration_a
):
    """
    A 404 here, and a 404 for a provider nobody has connected, must be
    indistinguishable — otherwise the endpoint reports whether *another*
    tenant has GoHighLevel connected.
    """
    headers = await auth_headers(client, owner_b)

    theirs = await client.get("/api/integrations/crm/gohighlevel", headers=headers)
    nobodys = await client.get("/api/integrations/crm/jobber", headers=headers)

    assert theirs.status_code == nobodys.status_code == 404
    assert theirs.json() == nobodys.json()


async def test_no_credential_reaches_any_api_response(
    client, db, owner_a, integration_a
):
    headers = await auth_headers(client, owner_a)

    for path in (
        "/api/integrations/crm",
        "/api/integrations/crm/gohighlevel",
        "/api/integrations/crm/providers",
        "/api/integrations/crm/syncs",
    ):
        response = await client.get(path, headers=headers)
        assert response.status_code == 200, path
        body = response.text
        assert TOKEN_A not in body, path
        assert integration_a.credentials_encrypted not in body, path


# ================================================= cross-tenant writes ===

async def test_tenant_b_cannot_modify_tenant_a_integration(
    client, db, owner_b, integration_a, integration_b
):
    headers = await auth_headers(client, owner_b)
    response = await client.put(
        "/api/integrations/crm/gohighlevel",
        headers=headers,
        json={"config": {"location_id": "HIJACKED"}, "is_enabled": True},
    )
    assert response.status_code == 200

    await db.refresh(integration_a)
    await db.refresh(integration_b)
    assert integration_a.config["location_id"] == "loc-A"      # untouched
    assert integration_b.config["location_id"] == "HIJACKED"   # B changed B's


async def test_tenant_b_cannot_delete_tenant_a_integration(
    client, db, owner_b, integration_a
):
    headers = await auth_headers(client, owner_b)
    response = await client.delete(
        "/api/integrations/crm/gohighlevel", headers=headers
    )
    assert response.status_code == 404

    still_there = await db.get(CrmIntegration, integration_a.id)
    assert still_there is not None


async def test_tenant_b_cannot_disconnect_tenant_a_integration(
    client, db, owner_b, integration_a
):
    headers = await auth_headers(client, owner_b)
    response = await client.post(
        "/api/integrations/crm/gohighlevel/disconnect", headers=headers
    )
    assert response.status_code == 404

    await db.refresh(integration_a)
    assert integration_a.credentials_encrypted is not None


async def test_tenant_b_cannot_trigger_a_test_against_tenant_a_credentials(
    client, db, owner_b, integration_a
):
    """
    A connection test spends Tenant A's provider rate limit and would confirm
    whether their token is live. Both are A's business.
    """
    headers = await auth_headers(client, owner_b)
    response = await client.post(
        "/api/integrations/crm/gohighlevel/test", headers=headers
    )
    assert response.status_code == 404


# ================================================== tenant_id injection ===

async def test_a_tenant_id_in_the_body_is_ignored(
    client, db, owner_b, tenant_a, integration_b
):
    """
    Requirement 6: do not accept an arbitrary tenant_id and trust it.

    The model has no such field, so it is dropped. Asserting the *effect* --
    that A's row is untouched and B's changed -- rather than the mechanism,
    because the mechanism could change while the guarantee must not.
    """
    headers = await auth_headers(client, owner_b)
    response = await client.put(
        "/api/integrations/crm/gohighlevel",
        headers=headers,
        json={
            "tenant_id": str(tenant_a.id),
            "config": {"location_id": "injected"},
        },
    )
    assert response.status_code == 200

    rows = (
        (
            await db.execute(
                select(CrmIntegration).where(CrmIntegration.tenant_id == tenant_a.id)
            )
        )
        .scalars()
        .all()
    )
    assert all(r.config.get("location_id") != "injected" for r in rows)

    await db.refresh(integration_b)
    assert integration_b.config["location_id"] == "injected"


async def test_a_tenant_id_query_parameter_is_ignored(
    client, db, owner_b, tenant_a, integration_a, integration_b
):
    headers = await auth_headers(client, owner_b)
    response = await client.get(
        f"/api/integrations/crm/gohighlevel?tenant_id={tenant_a.id}", headers=headers
    )
    assert response.status_code == 200
    assert response.json()["config"]["location_id"] == "loc-B"


# ============================================== cross-tenant sync access ===

@pytest.fixture
async def sync_a(db, tenant_a, integration_a):
    from app.integrations.crm import events
    from tests.conftest import make_call

    call = await make_call(db, tenant_a)
    await events.emit(
        db, tenant_id=tenant_a.id, event_type=CrmEventType.CALL_COMPLETED,
        entity_id=call.id, payload={"call_id": str(call.id), "secret": "A-ONLY"},
    )
    await db.commit()
    return call


async def test_tenant_b_cannot_see_tenant_a_syncs(client, db, owner_b, sync_a):
    headers = await auth_headers(client, owner_b)
    response = await client.get("/api/integrations/crm/syncs", headers=headers)

    assert response.status_code == 200
    assert response.json()["total"] == 0


async def test_tenant_b_cannot_see_tenant_a_external_ids(
    client, db, tenant_a, owner_b, sync_a
):
    """Requirement 5: Tenant A must never inspect Tenant B's external IDs."""
    syncs = (
        (await db.execute(select(CrmSync).where(CrmSync.tenant_id == tenant_a.id)))
        .scalars()
        .all()
    )
    assert syncs
    syncs[0].external_id = "GHL-CONTACT-SECRET-99"
    await db.commit()

    headers = await auth_headers(client, owner_b)
    response = await client.get("/api/integrations/crm/syncs", headers=headers)
    assert "GHL-CONTACT-SECRET-99" not in response.text


async def test_an_event_is_only_queued_for_its_own_tenants_integrations(
    db, tenant_a, tenant_b, integration_a, integration_b
):
    """
    The fan-out itself must be tenant-scoped. If `_subscribed_integrations`
    ever dropped its tenant predicate, Tenant A's call would be pushed into
    Tenant B's CRM -- the worst outcome in this whole step, and it would look
    like a working feature.
    """
    from app.integrations.crm import events
    from tests.conftest import make_call

    call = await make_call(db, tenant_a)
    event = await events.emit(
        db, tenant_id=tenant_a.id, event_type=CrmEventType.CALL_COMPLETED,
        entity_id=call.id, payload={"call_id": str(call.id)},
    )
    await db.commit()

    syncs = (
        (await db.execute(select(CrmSync).where(CrmSync.event_id == event.id)))
        .scalars()
        .all()
    )
    assert len(syncs) == 1
    assert syncs[0].integration_id == integration_a.id
    assert syncs[0].tenant_id == tenant_a.id


async def test_a_sync_whose_integration_belongs_elsewhere_is_refused(
    db, tenant_a, tenant_b, integration_a, integration_b
):
    """
    Defensive depth. Even if a mismatched row were somehow written, the
    service must refuse rather than run Tenant A's event against Tenant B's
    credentials.
    """
    from app.db.models import CrmEntityType, CrmSyncStatus
    from app.integrations.crm import service
    from tests.conftest import make_call

    call = await make_call(db, tenant_a)
    event = CrmEvent(
        tenant_id=tenant_a.id, event_type=CrmEventType.CALL_COMPLETED,
        entity_type=CrmEntityType.CALL, entity_id=call.id,
        idempotency_key=f"manual:{uuid.uuid4()}", payload={},
    )
    db.add(event)
    await db.flush()

    rogue = CrmSync(
        tenant_id=tenant_a.id, event_id=event.id,
        integration_id=integration_b.id,          # <-- another tenant's
        provider=CrmProviderType.GOHIGHLEVEL,
        entity_type=CrmEntityType.CALL, entity_id=call.id,
        status=CrmSyncStatus.PENDING,
    )
    db.add(rogue)
    await db.commit()

    outcome = await service.process_sync(db, rogue)
    assert outcome.status is CrmSyncStatus.PERMANENT_FAILURE
    assert outcome.error_code == "misconfigured"


# =========================================================== permissions ===

async def test_a_viewer_cannot_write_an_integration(client, db, viewer_a):
    headers = await auth_headers(client, viewer_a)
    response = await client.put(
        "/api/integrations/crm/webhook",
        headers=headers,
        json={"config": {"url": "https://evil.example.com/steal"}},
    )
    assert response.status_code == 403


async def test_an_agent_cannot_read_integrations(client, db, agent_a, integration_a):
    """
    Integration config is a sensitive setting, not operational data. An agent
    handling calls has no reason to see which CRM is wired up or where its
    webhook points.
    """
    headers = await auth_headers(client, agent_a)
    response = await client.get("/api/integrations/crm", headers=headers)
    assert response.status_code == 403


async def test_the_routes_use_the_permission_enum_not_role_strings():
    """
    Requirement 6 forbids `if user.role == "admin"`. Asserted against the
    source, because this is the kind of shortcut that gets added under time
    pressure and reads as harmless.
    """
    import pathlib

    for path in ("app/api/integration_routes.py", "app/api/crm_webhook_routes.py"):
        source = pathlib.Path(path).read_text()
        assert '== "admin"' not in source, path
        assert "== 'admin'" not in source, path
        assert ".role ==" not in source, path


async def test_unauthenticated_requests_are_rejected(client, integration_a):
    for method, path in (
        ("get", "/api/integrations/crm"),
        ("get", "/api/integrations/crm/gohighlevel"),
        ("put", "/api/integrations/crm/gohighlevel"),
        ("delete", "/api/integrations/crm/gohighlevel"),
        ("post", "/api/integrations/crm/gohighlevel/test"),
    ):
        kwargs = {"json": {}} if method in ("put", "post") else {}
        response = await getattr(client, method)(path, **kwargs)
        assert response.status_code in (401, 403), f"{method} {path}"


# ============================================== no secrets in audit rows ===

async def test_connecting_an_integration_writes_no_secret_to_the_audit_log(
    client, db, owner_a
):
    """Requirement 22: CRM secret absent from audit records."""
    headers = await auth_headers(client, owner_a)
    response = await client.put(
        "/api/integrations/crm/hubspot",
        headers=headers,
        json={"credentials": {"access_token": "pat-na1-AUDIT-LEAK-TEST"}},
    )
    assert response.status_code == 200

    rows = (
        (await db.execute(select(AuditLog).where(AuditLog.tenant_id == owner_a.tenant_id)))
        .scalars()
        .all()
    )
    assert rows
    everything = json.dumps([r.detail for r in rows])
    assert "pat-na1-AUDIT-LEAK-TEST" not in everything
    # The supplied field names are recorded under a neutral metadata key so
    # redaction preserves safe schema information without persisting values.
    assert "provided_field_names" in everything
    assert "access_token" in everything


async def test_no_secret_reaches_the_structured_log(
    client, db, owner_a, monkeypatch
):
    """Requirement 22: CRM secret absent from logs."""
    captured: list[tuple] = []

    from app.core import logging as app_logging

    for level in ("info", "warning", "error"):
        original = getattr(app_logging.log, level)

        def spy(event, _level=level, _orig=original, **kw):
            captured.append((event, kw))
            return _orig(event, **kw)

        monkeypatch.setattr(app_logging.log, level, spy)

    headers = await auth_headers(client, owner_a)
    await client.put(
        "/api/integrations/crm/hubspot",
        headers=headers,
        json={"credentials": {"access_token": "pat-na1-LOG-LEAK-TEST"}},
    )

    assert captured, "nothing was logged, so the assertion would be vacuous"
    assert "pat-na1-LOG-LEAK-TEST" not in json.dumps(captured, default=str)
````

### File 10: `tests/test_call_state.py`

SHA-256: `c90db88b080733665eb846fbb32b03096679a8beed5b01eae14e89d5a255ae5f`

````
"""
Call lifecycle state machine.

These tests drive the real `Call` model through `app.telephony.call_state`.
Nothing is mocked: the transition rules are pure functions over a row, which is
exactly why they were extracted from the webhook handler.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest

from app.db.models import Call, CallStatus, TransferState
from app.telephony import call_state


def make_call(status: CallStatus = CallStatus.RINGING, **kw) -> Call:
    return Call(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        call_sid=f"CA{uuid.uuid4().hex[:12]}",
        from_number="+15550000001",
        to_number="+15550000002",
        status=status,
        started_at=datetime.utcnow(),
        duration_seconds=0.0,
        transfer_state=TransferState.NONE,
        transfer_attempts=0,
        **kw,
    )


# ------------------------------------------------------- the required graph ---

@pytest.mark.parametrize("start,target", [
    (CallStatus.RINGING, CallStatus.IN_PROGRESS),
    (CallStatus.RINGING, CallStatus.NO_ANSWER),
    (CallStatus.RINGING, CallStatus.FAILED),
    (CallStatus.RINGING, CallStatus.CANCELLED),
    (CallStatus.IN_PROGRESS, CallStatus.COMPLETED),
    (CallStatus.IN_PROGRESS, CallStatus.TRANSFERRED),
    (CallStatus.IN_PROGRESS, CallStatus.FAILED),
    (CallStatus.TRANSFERRED, CallStatus.COMPLETED),
    (CallStatus.TRANSFERRED, CallStatus.FAILED),
])
def test_legal_transitions_are_applied(start, target):
    call = make_call(start)
    result = call_state.apply_status(call, target)
    assert result.applied is True
    assert call.status is target
    assert result.previous is start


@pytest.mark.parametrize("start,target", [
    (CallStatus.COMPLETED, CallStatus.RINGING),
    (CallStatus.COMPLETED, CallStatus.IN_PROGRESS),
    (CallStatus.FAILED, CallStatus.IN_PROGRESS),
    (CallStatus.FAILED, CallStatus.COMPLETED),
    (CallStatus.NO_ANSWER, CallStatus.IN_PROGRESS),
    (CallStatus.CANCELLED, CallStatus.IN_PROGRESS),
    (CallStatus.IN_PROGRESS, CallStatus.RINGING),
    (CallStatus.TRANSFERRED, CallStatus.IN_PROGRESS),
    (CallStatus.TRANSFERRED, CallStatus.RINGING),
])
def test_illegal_transitions_are_rejected(start, target):
    call = make_call(start)
    result = call_state.apply_status(call, target)
    assert result.applied is False
    assert call.status is start, "the row must not have been mutated"


def test_terminal_states_are_terminal():
    assert call_state.is_terminal(CallStatus.COMPLETED)
    assert call_state.is_terminal(CallStatus.FAILED)
    assert call_state.is_terminal(CallStatus.NO_ANSWER)
    assert call_state.is_terminal(CallStatus.CANCELLED)
    # A transferred call is still live -- a human is on it.
    assert not call_state.is_terminal(CallStatus.TRANSFERRED)
    assert not call_state.is_terminal(CallStatus.IN_PROGRESS)
    assert not call_state.is_terminal(CallStatus.RINGING)


def test_every_status_has_a_transition_entry():
    assert set(call_state.ALLOWED_TRANSITIONS) == set(CallStatus)


# ------------------------------------------------------------- idempotency ---

def test_duplicate_status_is_a_no_op_but_not_an_error():
    call = make_call(CallStatus.COMPLETED)
    result = call_state.apply_status(call, CallStatus.COMPLETED)
    assert result.applied is False
    assert result.reason == "duplicate"
    assert call.status is CallStatus.COMPLETED


def test_repeated_completed_callbacks_keep_one_ended_at():
    call = make_call(CallStatus.IN_PROGRESS)
    call_state.apply_provider_status(call, "completed", duration_seconds=100)
    first_ended = call.ended_at
    assert first_ended is not None

    call_state.apply_provider_status(call, "completed", duration_seconds=100)
    call_state.apply_provider_status(call, "completed", duration_seconds=100)
    assert call.ended_at == first_ended, "ended_at must be written once"


def test_terminal_state_survives_a_stale_callback():
    """The exact bug: a late 'completed' used to overwrite TRANSFERRED."""
    call = make_call(CallStatus.IN_PROGRESS)
    call_state.apply_status(call, CallStatus.TRANSFERRED)
    call_state.apply_provider_status(call, "completed", duration_seconds=90)
    assert call.status is CallStatus.COMPLETED

    # ... and now a duplicate 'no-answer' arrives for the same SID.
    result = call_state.apply_provider_status(call, "no-answer")
    assert result.applied is False
    assert call.status is CallStatus.COMPLETED


def test_duration_never_shrinks():
    call = make_call(CallStatus.IN_PROGRESS)
    call_state.apply_provider_status(call, "completed", duration_seconds=143)
    call_state.apply_provider_status(call, "completed", duration_seconds=0)
    assert call.duration_seconds == 143


def test_ended_at_can_be_supplied_explicitly():
    call = make_call(CallStatus.IN_PROGRESS)
    when = datetime.utcnow() - timedelta(minutes=5)
    call_state.apply_status(call, CallStatus.COMPLETED, ended_at=when)
    assert call.ended_at == when


def test_non_terminal_transition_does_not_set_ended_at():
    call = make_call(CallStatus.RINGING)
    call_state.apply_status(call, CallStatus.IN_PROGRESS)
    assert call.ended_at is None


# --------------------------------------------------------- provider mapping ---

@pytest.mark.parametrize("raw,expected", [
    ("completed", CallStatus.COMPLETED),
    ("no-answer", CallStatus.NO_ANSWER),
    ("busy", CallStatus.FAILED),
    ("failed", CallStatus.FAILED),
    ("canceled", CallStatus.CANCELLED),
    ("cancelled", CallStatus.CANCELLED),
    ("in-progress", CallStatus.IN_PROGRESS),
    ("ringing", CallStatus.RINGING),
    ("queued", CallStatus.RINGING),
    ("initiated", CallStatus.RINGING),
    ("COMPLETED", CallStatus.COMPLETED),      # case-insensitive
    (" busy ", CallStatus.FAILED),            # whitespace tolerant
])
def test_provider_status_mapping(raw, expected):
    assert call_state.map_provider_status(raw) is expected


@pytest.mark.parametrize("raw", ["", None, "wat", "in-progresss", "hangup"])
def test_unknown_provider_status_maps_to_nothing(raw):
    assert call_state.map_provider_status(raw) is None


def test_unknown_provider_status_cannot_terminate_a_live_call():
    """Previously an unrecognised string defaulted to COMPLETED."""
    call = make_call(CallStatus.IN_PROGRESS)
    result = call_state.apply_provider_status(call, "something-new-from-twilio")
    assert result.applied is False
    assert result.reason == "unknown_status"
    assert call.status is CallStatus.IN_PROGRESS


def test_failure_reason_is_recorded_for_bad_endings():
    call = make_call(CallStatus.RINGING)
    call_state.apply_provider_status(call, "busy")
    assert call.status is CallStatus.FAILED
    assert call.failure_reason == "busy"


def test_failure_reason_is_not_set_on_a_clean_completion():
    call = make_call(CallStatus.IN_PROGRESS)
    call_state.apply_provider_status(call, "completed", duration_seconds=30)
    assert call.failure_reason is None


def test_can_transition_allows_self():
    assert call_state.can_transition(CallStatus.COMPLETED, CallStatus.COMPLETED)
````

### File 11: `tests/test_crm_credentials.py`

SHA-256: `f5854d157c498e5246ad532b56c41479bf75b99fd32b18e294c4aaf6dc7b393d`

````
"""
Credential storage and secret handling.

The single worst outcome in this whole step is a tenant's CRM token leaving
the system — in an API response, a log line, an audit row, or a database dump.
These tests are the ones that would have to fail before that could happen.
"""
from __future__ import annotations

import json
import uuid

import pytest

from app.integrations.crm import crypto
from app.integrations.crm.crypto import (
    CredentialCryptoError,
    CredentialDecryptionError,
    decrypt_credentials,
    encrypt_credentials,
    generate_key,
    parse_key_ring,
)

TENANT_A = str(uuid.uuid4())
TENANT_B = str(uuid.uuid4())
SECRET = {"access_token": "pit-super-secret-token-value-9876543210"}


@pytest.fixture
def ring():
    return parse_key_ring(f"k1:{generate_key()}")


# ============================================================ round trip ===

def test_credentials_survive_a_round_trip(ring):
    envelope, key_id = encrypt_credentials(
        SECRET, tenant_id=TENANT_A, provider="gohighlevel", key_ring=ring
    )
    recovered = decrypt_credentials(
        envelope, tenant_id=TENANT_A, provider="gohighlevel", key_ring=ring
    )
    assert recovered == SECRET
    assert key_id == "k1"


def test_the_plaintext_token_is_not_present_in_the_ciphertext(ring):
    """The obvious test, and the one that catches "encryption" that is base64."""
    envelope, _ = encrypt_credentials(
        SECRET, tenant_id=TENANT_A, provider="gohighlevel", key_ring=ring
    )
    assert SECRET["access_token"] not in envelope
    # ...and not merely base64-hidden either.
    import base64
    for part in envelope.split("."):
        try:
            decoded = base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))
        except Exception:
            continue
        assert SECRET["access_token"].encode() not in decoded


def test_every_encryption_uses_a_fresh_nonce(ring):
    """
    Identical plaintext must not produce identical ciphertext.

    Nonce reuse in GCM is catastrophic — it leaks the XOR of two plaintexts
    and breaks the authentication key. This is the test that catches a
    "deterministic for easier testing" change.
    """
    envelopes = {
        encrypt_credentials(
            SECRET, tenant_id=TENANT_A, provider="gohighlevel", key_ring=ring
        )[0]
        for _ in range(20)
    }
    assert len(envelopes) == 20


def test_the_envelope_records_which_key_wrote_it(ring):
    envelope, key_id = encrypt_credentials(
        SECRET, tenant_id=TENANT_A, provider="hubspot", key_ring=ring
    )
    assert envelope.startswith(f"v1.{key_id}.")


# ============================================================== tampering ===

def test_a_tampered_ciphertext_fails_to_decrypt(ring):
    """
    AES-GCM is authenticated, so this must raise rather than returning
    corrupted data that then gets sent to a provider.
    """
    envelope, _ = encrypt_credentials(
        SECRET, tenant_id=TENANT_A, provider="hubspot", key_ring=ring
    )
    prefix, key_id, nonce, ciphertext = envelope.split(".")
    flipped = ciphertext[:-4] + ("AAAA" if not ciphertext.endswith("AAAA") else "BBBB")

    with pytest.raises(CredentialDecryptionError):
        decrypt_credentials(
            f"{prefix}.{key_id}.{nonce}.{flipped}",
            tenant_id=TENANT_A, provider="hubspot", key_ring=ring,
        )


def test_a_malformed_envelope_is_rejected(ring):
    for bad in ("", "not-an-envelope", "v1.k1.onlythree", "v2.k1.a.b"):
        with pytest.raises(CredentialDecryptionError):
            decrypt_credentials(
                bad, tenant_id=TENANT_A, provider="hubspot", key_ring=ring
            )


def test_a_ciphertext_from_a_retired_key_reports_why(ring):
    envelope, _ = encrypt_credentials(
        SECRET, tenant_id=TENANT_A, provider="hubspot", key_ring=ring
    )
    other = parse_key_ring(f"k2:{generate_key()}")

    with pytest.raises(CredentialDecryptionError) as caught:
        decrypt_credentials(
            envelope, tenant_id=TENANT_A, provider="hubspot", key_ring=other
        )
    assert "k1" in str(caught.value)


# ================================================= AAD / tenant binding ===

def test_ciphertext_copied_to_another_tenant_will_not_decrypt(ring):
    """
    **The isolation guarantee reaching down to the cipher.**

    An attacker with database write access copies Tenant A's
    `credentials_encrypted` into Tenant B's row, hoping B's integration will
    then use A's token. The tenant id is in the AES-GCM associated data, so
    the copy fails authentication instead.
    """
    envelope, _ = encrypt_credentials(
        SECRET, tenant_id=TENANT_A, provider="gohighlevel", key_ring=ring
    )
    with pytest.raises(CredentialDecryptionError):
        decrypt_credentials(
            envelope, tenant_id=TENANT_B, provider="gohighlevel", key_ring=ring
        )


def test_ciphertext_moved_to_another_provider_will_not_decrypt(ring):
    """
    Same defence, other axis: a HubSpot token pasted into the same tenant's
    GoHighLevel row would otherwise be sent to the wrong vendor.
    """
    envelope, _ = encrypt_credentials(
        SECRET, tenant_id=TENANT_A, provider="hubspot", key_ring=ring
    )
    with pytest.raises(CredentialDecryptionError):
        decrypt_credentials(
            envelope, tenant_id=TENANT_A, provider="gohighlevel", key_ring=ring
        )


# ================================================================ key ring ===

def test_key_rotation_keeps_old_ciphertext_readable():
    """
    Rotation must not be a flag day. Old envelopes stay readable while new
    ones are written with the new key.
    """
    old_key, new_key = generate_key(), generate_key()

    old_ring = parse_key_ring(f"y2025:{old_key}")
    envelope, _ = encrypt_credentials(
        SECRET, tenant_id=TENANT_A, provider="jobber", key_ring=old_ring
    )

    rotated = parse_key_ring(f"y2026:{new_key},y2025:{old_key}")
    assert rotated.active_id == "y2026"
    assert decrypt_credentials(
        envelope, tenant_id=TENANT_A, provider="jobber", key_ring=rotated
    ) == SECRET

    fresh, key_id = encrypt_credentials(
        SECRET, tenant_id=TENANT_A, provider="jobber", key_ring=rotated
    )
    assert key_id == "y2026"


def test_a_bare_key_with_no_id_is_accepted():
    ring = parse_key_ring(generate_key())
    assert ring.active_id == "default"


@pytest.mark.parametrize("bad", [
    "",
    "   ",
    "k1:not-valid-base64!!!",
    "k1:" + "c2hvcnQ=",           # valid base64, but only 5 bytes
    pytest.param("bad id:" + generate_key(), id="space-in-key-id"),
])
def test_invalid_key_configuration_is_rejected_loudly(bad):
    with pytest.raises(CredentialCryptoError):
        parse_key_ring(bad)


def test_duplicate_key_ids_are_rejected():
    key = generate_key()
    with pytest.raises(CredentialCryptoError):
        parse_key_ring(f"k1:{key},k1:{generate_key()}")


def test_a_key_ring_never_prints_key_material():
    """A KeyRing in a traceback or a REPL must not reveal the keys."""
    ring = parse_key_ring(f"k1:{generate_key()},k2:{generate_key()}")
    printed = repr(ring)

    assert "k1" in printed and "k2" in printed
    for material in ring.keys.values():
        assert material.hex() not in printed
        assert str(material) not in printed


def test_generated_keys_are_the_right_size_and_random():
    keys = {generate_key() for _ in range(50)}
    assert len(keys) == 50
    for key in keys:
        ring = parse_key_ring(f"k:{key}")
        assert len(ring.keys["k"]) == 32


# ========================================================= configuration ===

def test_production_refuses_to_boot_without_an_encryption_key(monkeypatch):
    """
    Requirement 4: fail safely if encryption configuration is invalid.
    Storing a provider token with no key would mean plaintext in the database.
    """
    from app.core.config import Settings

    settings = Settings(
        app_env="production", crm_encryption_keys="",
        jwt_secret="x" * 40, secret_key="a-real-secret",
        public_base_url="https://api.example.com", twilio_auth_token="t",
        knowledge_embedding_provider="openai",
    )
    problems = settings.validate_security()
    assert any("CRM_ENCRYPTION_KEYS" in p for p in problems), problems


def test_a_malformed_encryption_key_is_a_boot_failure(monkeypatch):
    from app.core.config import Settings

    settings = Settings(
        app_env="development", crm_encryption_keys="k1:!!!not-base64!!!"
    )
    problems = settings.validate_security()
    assert any("CRM_ENCRYPTION_KEYS is invalid" in p for p in problems), problems


def test_development_without_a_key_is_allowed():
    """
    A developer with no key should still be able to run the app; they simply
    cannot connect an integration. Requiring one to boot locally is friction
    that gets worked around by disabling the check.
    """
    from app.core.config import Settings

    settings = Settings(app_env="development", crm_encryption_keys="")
    assert not any("CRM_ENCRYPTION_KEYS" in p for p in settings.validate_security())


def test_key_ring_from_settings_returns_none_when_unconfigured(monkeypatch):
    from app.core.config import settings as live

    monkeypatch.setattr(live, "crm_encryption_keys", "", raising=False)
    assert crypto.key_ring_from_settings() is None


# ============================================== no secrets in serialization ===

async def test_the_stored_row_contains_ciphertext_not_the_token(db, tenant_a):
    """End to end: what actually lands in the database column."""
    from tests.conftest import make_integration

    integration = await make_integration(
        db, tenant_a, "hubspot", credentials={"access_token": "pat-na1-REALTOKEN-42"}
    )
    assert integration.credentials_encrypted
    assert "pat-na1-REALTOKEN-42" not in integration.credentials_encrypted
    assert integration.credentials_encrypted.startswith("v1.")
    assert integration.credentials_key_id


async def test_no_secret_survives_json_serialization_of_the_api_model(db, tenant_a):
    from app.api.integration_routes import to_out
    from tests.conftest import make_integration

    integration = await make_integration(
        db, tenant_a, "jobber", credentials={"access_token": "jobber-REALTOKEN-42"}
    )
    body = json.dumps(to_out(integration).model_dump())

    assert "jobber-REALTOKEN-42" not in body
    assert "credentials_encrypted" not in body
    assert integration.credentials_encrypted not in body
    # But it must still tell the tenant that they *are* connected.
    assert '"connected": true' in body.replace(", ", ", ").lower()


async def test_the_api_model_has_no_field_that_could_carry_a_secret():
    """
    Structural. A future field named `credentials` or `token` added to the
    response model would be caught here rather than in production.
    """
    from app.api.integration_routes import IntegrationOut

    forbidden = (
        "credential", "token", "secret", "password", "key", "auth",
    )
    for name in IntegrationOut.model_fields:
        assert not any(word in name.lower() for word in forbidden), (
            f"IntegrationOut.{name} is named like a secret; if it is not one, "
            f"rename it, and if it is, remove it"
        )
````

### File 12: `tests/test_calendar_contract.py`

SHA-256: `958b51b92ca4b99ddf860a219cb2a18a2d96fe062199e70aa846d479ec24682f`

````
"""
Calendar provider contract tests.

Requirement 29: adding a new calendar provider must automatically inherit the
contract suite, and must not be able to bypass tenant scoping, error
normalization, timeout behaviour, idempotency expectations or secret handling.

Parameterized over the **registry**, not a hand-written list. A provider added
tomorrow is tested by this file the moment it is registered — which is the only
design that actually delivers the requirement. A checklist in a document does
not survive a deadline.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import httpx
import pytest

from app.db.models import CalendarProviderType
from app.integrations.calendar.base import (
    CalendarCapability,
    CalendarContext,
    CalendarProvider,
)
from app.integrations.calendar.errors import (
    CalendarAuthError,
    CalendarConflictError,
    CalendarError,
    CalendarNotFoundError,
    CalendarPermissionError,
    CalendarRateLimitError,
    CalendarTemporaryError,
    CalendarTimeout,
    CalendarUnsupportedError,
    CalendarValidationError,
)
from app.integrations.calendar.models import (
    Attendee,
    CalendarEvent,
    EventRequest,
    HealthResult,
    TimeWindow,
)
from app.integrations.calendar.registry import PROVIDERS, build, capabilities_of
from app.integrations.calendar.timezones import UTC
from tests.conftest import FakeTransport

ALL_PROVIDERS = list(CalendarProviderType)

#: Config that satisfies every adapter's required settings, so a contract test
#: exercises the operation rather than tripping over a missing calendar id.
_CONFIG = {
    CalendarProviderType.GOOGLE: {
        "calendar_id": "primary", "base_url": "https://gcal.test/v3",
    },
    CalendarProviderType.GOOGLE_SERVICE_ACCOUNT: {"calendar_id": "shared@example.com"},
    CalendarProviderType.MICROSOFT: {
        "mailbox": "diary@example.com", "base_url": "https://graph.test/v1.0",
    },
    CalendarProviderType.CALCOM: {
        "event_type_id": 4242, "base_url": "https://cal.test/v2",
    },
    CalendarProviderType.INTERNAL: {},
}
_CREDENTIALS = {
    CalendarProviderType.GOOGLE: {"access_token": "ya29-SECRET-google-token"},
    CalendarProviderType.GOOGLE_SERVICE_ACCOUNT: {},
    CalendarProviderType.MICROSOFT: {"access_token": "eyJ0-SECRET-graph-token"},
    CalendarProviderType.CALCOM: {"api_key": "cal_live_SECRET_key_123"},
    CalendarProviderType.INTERNAL: {},
}

START = datetime(2026, 6, 16, 14, 0, tzinfo=UTC)
WINDOW = TimeWindow(start=START, end=START + timedelta(minutes=30))
REQUEST = EventRequest(
    title="Cleaning",
    start=START,
    end=START + timedelta(minutes=30),
    timezone="America/New_York",
    attendee=Attendee(name="Jane Doe", phone="+15551230000", email="jane@example.com"),
    idempotency_key="abc123def456abc123def456",
)


def make_provider(provider: CalendarProviderType, *, timeout: float = 5.0):
    return build(provider, CalendarContext(
        tenant_id="11111111-1111-1111-1111-111111111111",
        credentials=dict(_CREDENTIALS[provider]),
        config=dict(_CONFIG[provider]),
        timezone="America/New_York",
        timeout_seconds=timeout,
    ))


def secrets_for(provider: CalendarProviderType) -> list[str]:
    return [v for v in _CREDENTIALS[provider].values() if v]


# ================================================================ registry ===

@pytest.mark.parametrize("provider", ALL_PROVIDERS)
def test_every_provider_type_has_an_adapter(provider):
    assert provider in PROVIDERS


@pytest.mark.parametrize("provider", ALL_PROVIDERS)
def test_adapter_name_matches_the_enum_value(provider):
    """
    A mismatch means log lines and adapters disagree about what a provider is
    called — the sort of thing that only bites while grepping during an
    incident.
    """
    assert PROVIDERS[provider].name == provider.value


@pytest.mark.parametrize("provider", ALL_PROVIDERS)
def test_adapter_subclasses_the_base(provider):
    assert issubclass(PROVIDERS[provider], CalendarProvider)


# ============================================================== capability ===

#: Capability -> the method that must exist. Several capabilities share a
#: method (RESCHEDULE is served by update_event or a dedicated reschedule).
_CAPABILITY_METHODS = {
    CalendarCapability.FREE_BUSY: "get_busy",
    CalendarCapability.CREATE_EVENT: "create_event",
    CalendarCapability.UPDATE_EVENT: "update_event",
    CalendarCapability.CANCEL_EVENT: "cancel_event",
    CalendarCapability.GET_EVENT: "get_event",
    CalendarCapability.HEALTH_CHECK: "health_check",
}


@pytest.mark.parametrize("provider", ALL_PROVIDERS)
def test_declared_capabilities_are_actually_implemented(provider):
    """
    **The core contract test.**

    Declaring a capability you have not written is worse than not declaring
    it: the service routes work to you and the tenant gets a permanent failure
    instead of a clean "unsupported".
    """
    adapter = PROVIDERS[provider]
    for capability, method_name in _CAPABILITY_METHODS.items():
        if capability not in capabilities_of(provider):
            continue
        assert hasattr(adapter, method_name), (
            f"{provider.value} declares {capability.value} but has no "
            f"{method_name}"
        )
        assert getattr(adapter, method_name) is not getattr(
            CalendarProvider, method_name
        ), (
            f"{provider.value} declares {capability.value} but does not "
            f"override {method_name}, so calling it would raise "
            f"CalendarUnsupportedError"
        )


@pytest.mark.parametrize("provider", ALL_PROVIDERS)
async def test_reschedule_is_served_by_something_real(provider):
    """
    RESCHEDULE may be an `update_event` or a dedicated `reschedule` method —
    Cal.com has a real endpoint for it. Either satisfies the capability;
    neither means the service would route a move into nothing.
    """
    adapter = make_provider(provider)
    if CalendarCapability.RESCHEDULE not in capabilities_of(provider):
        assert not hasattr(adapter, "reschedule")
        if CalendarCapability.UPDATE_EVENT not in capabilities_of(provider):
            with pytest.raises(CalendarUnsupportedError):
                await adapter.update_event("ext-1", REQUEST)
        else:
            assert type(adapter).update_event is not CalendarProvider.update_event
        return

    has_dedicated = hasattr(adapter, "reschedule")
    has_update = getattr(adapter, "update_event") is not CalendarProvider.update_event
    assert has_dedicated or has_update


@pytest.mark.parametrize("provider", ALL_PROVIDERS)
async def test_undeclared_capabilities_raise_unsupported_cleanly(provider):
    """
    An operation a provider does not claim must fail as
    `CalendarUnsupportedError`, not `AttributeError` or `NotImplementedError`.
    The service treats that class as "nothing to do here" rather than an
    outage.
    """
    adapter = make_provider(provider)
    declared = capabilities_of(provider)

    for capability, method_name in _CAPABILITY_METHODS.items():
        if capability in declared:
            continue
        if capability is CalendarCapability.HEALTH_CHECK:
            # health_check has a defined non-raising default: a diagnostic
            # must always answer.
            assert (await adapter.health_check()).connected is False
            continue
        with pytest.raises(CalendarUnsupportedError):
            await _call(adapter, method_name)


async def _call(adapter, method_name):
    method = getattr(adapter, method_name)
    return await {
        "get_busy": lambda: method(WINDOW),
        "create_event": lambda: method(REQUEST),
        "update_event": lambda: method("ext-1", REQUEST),
        "cancel_event": lambda: method("ext-1"),
        "get_event": lambda: method("ext-1"),
    }[method_name]()


# ====================================================== error normalization ===

#: Every adapter must map these identically. That is what lets the service
#: read one property instead of knowing four vendors' conventions.
STATUS_EXPECTATIONS = [
    (401, CalendarAuthError, False),
    (403, CalendarPermissionError, False),
    (404, CalendarNotFoundError, False),
    (409, CalendarConflictError, False),
    (410, CalendarNotFoundError, False),
    (422, CalendarValidationError, False),
    (429, CalendarRateLimitError, True),
    (500, CalendarTemporaryError, True),
    (503, CalendarTemporaryError, True),
]


@pytest.mark.parametrize("provider", ALL_PROVIDERS)
@pytest.mark.parametrize("status,expected,retryable", STATUS_EXPECTATIONS)
async def test_http_status_maps_identically_for_every_provider(
    provider, status, expected, retryable, monkeypatch
):
    FakeTransport((status, {"message": "nope"})).install(monkeypatch)
    adapter = make_provider(provider)

    with pytest.raises(CalendarError) as caught:
        await adapter.request("POST", "https://example.test/x", json_body={})

    assert isinstance(caught.value, expected), (
        f"{provider.value} mapped HTTP {status} to "
        f"{type(caught.value).__name__}, expected {expected.__name__}"
    )
    assert caught.value.retryable is retryable


@pytest.mark.parametrize("provider", ALL_PROVIDERS)
async def test_a_403_that_is_really_a_throttle_becomes_retryable(
    provider, monkeypatch
):
    """
    Google and Microsoft both signal throttling inside a 403 body. Treating
    those as permission errors would strand a tenant whose only problem was
    going too fast.
    """
    FakeTransport(
        (403, {"error": {"errors": [{"reason": "rateLimitExceeded"}]}})
    ).install(monkeypatch)
    adapter = make_provider(provider)

    with pytest.raises(CalendarError) as caught:
        await adapter.request("GET", "https://example.test/x")
    assert isinstance(caught.value, CalendarRateLimitError)
    assert caught.value.retryable is True


@pytest.mark.parametrize("provider", ALL_PROVIDERS)
async def test_timeouts_become_retryable_calendar_timeouts(provider, monkeypatch):
    FakeTransport(httpx.ReadTimeout("too slow")).install(monkeypatch)
    with pytest.raises(CalendarTimeout) as caught:
        await make_provider(provider).request("POST", "https://example.test/x")
    assert caught.value.retryable is True


@pytest.mark.parametrize("provider", ALL_PROVIDERS)
async def test_no_httpx_exception_ever_escapes(provider, monkeypatch):
    """
    The service catches `CalendarError`. A leaked `httpx` exception would skip
    classification entirely and land in the generic handler, turning a
    transient blip into a permanently failed booking.
    """
    for failure in (
        httpx.ConnectError("x"), httpx.ReadTimeout("x"),
        httpx.PoolTimeout("x"), httpx.RemoteProtocolError("x"),
    ):
        FakeTransport(failure).install(monkeypatch)
        try:
            await make_provider(provider).request("GET", "https://example.test/x")
        except CalendarError:
            pass
        except httpx.HTTPError as exc:
            pytest.fail(f"{provider.value} leaked {type(exc).__name__}")


@pytest.mark.parametrize("provider", ALL_PROVIDERS)
async def test_free_busy_behavior_is_explicit_on_failure_or_non_support(
    provider, monkeypatch
):
    """Providers either propagate read failures, or fail closed as unsupported.

    The service-account branch executes the legacy client's failure path
    against a local failing transport and requires failure rather than an
    empty calendar or a successful health result.
    """
    adapter = make_provider(provider)
    if CalendarCapability.FREE_BUSY not in capabilities_of(provider):
        with pytest.raises(CalendarUnsupportedError):
            await adapter.get_busy(WINDOW)
        return

    if provider is CalendarProviderType.GOOGLE_SERVICE_ACCOUNT:
        from app.integrations import google_calendar

        class FailingQuery:
            def execute(self):
                raise RuntimeError("local test provider failure")

        class FailingFreeBusy:
            def query(self, *, body):
                assert body["items"][0]["id"] == "shared@example.com"
                return FailingQuery()

        class FailingService:
            def freebusy(self):
                return FailingFreeBusy()

        client = google_calendar.CalendarClient("shared@example.com")
        monkeypatch.setattr(google_calendar, "_GOOGLE_AVAILABLE", True)
        monkeypatch.setattr(client, "_get_service", lambda: FailingService())
        monkeypatch.setattr(adapter, "_client", lambda: client)
        with pytest.raises(CalendarError):
            await adapter.get_busy(WINDOW)
        assert (await adapter.health_check()).connected is False
        return

    FakeTransport((500, {"error": "boom"})).install(monkeypatch)
    with pytest.raises(CalendarError):
        await adapter.get_busy(WINDOW)


# ================================================================= timeout ===

@pytest.mark.parametrize("provider", ALL_PROVIDERS)
async def test_the_configured_timeout_reaches_the_http_client(provider, monkeypatch):
    """
    Requirement 30: nothing may hang. Asserted by watching what the adapter
    passes to `AsyncClient`, because a timeout that is configured but never
    applied looks identical from the outside until a provider stops answering.
    """
    seen = {}
    original_init = httpx.AsyncClient.__init__

    def spy(self, *args, **kwargs):
        seen["timeout"] = kwargs.get("timeout")
        return original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", spy)
    FakeTransport((200, {})).install(monkeypatch)

    await make_provider(provider, timeout=2.5).request("GET", "https://example.test/x")
    assert seen["timeout"] == 2.5


# ========================================================== secret handling ===

@pytest.mark.parametrize("provider", ALL_PROVIDERS)
def test_the_context_repr_hides_credentials(provider):
    """
    A context in a traceback must not print an OAuth token. Tracebacks get
    pasted into issue trackers.
    """
    printed = repr(make_provider(provider).context)
    for secret in secrets_for(provider):
        assert secret not in printed
    assert "credential_keys" in printed


@pytest.mark.parametrize("provider", ALL_PROVIDERS)
async def test_raised_errors_do_not_expose_configured_credentials(provider, monkeypatch):
    """Credential-bearing errors are scrubbed; credentialless adapters stay empty."""
    adapter = make_provider(provider)
    secrets = secrets_for(provider)
    if not secrets:
        assert adapter.context.credentials == {}
        if provider is CalendarProviderType.INTERNAL:
            unsupported = adapter.get_busy(WINDOW)
        else:
            assert provider is CalendarProviderType.GOOGLE_SERVICE_ACCOUNT
            unsupported = adapter.update_event("ext-1", REQUEST)
        with pytest.raises(CalendarUnsupportedError) as caught:
            await unsupported
        assert "access_token" not in str(caught.value)
        return

    secret = secrets[0]
    FakeTransport(
        (400, {"error": "invalid", "access_token": secret, "echo": f"Bearer {secret}"})
    ).install(monkeypatch)

    with pytest.raises(CalendarError) as caught:
        await adapter.request("POST", "https://example.test/x")

    assert secret not in str(caught.value)
    assert secret not in caught.value.safe_message


@pytest.mark.parametrize("provider", ALL_PROVIDERS)
async def test_a_401_body_is_never_included_in_the_message(provider, monkeypatch):
    """
    A 401 body is the most likely place for a token to be reflected, so it is
    excluded entirely rather than merely scrubbed. The scrubber is a regex,
    and regexes miss things.
    """
    FakeTransport(
        (401, {"detail": "token abcdef0123456789 is invalid"})
    ).install(monkeypatch)

    with pytest.raises(CalendarAuthError) as caught:
        await make_provider(provider).request("GET", "https://example.test/x")
    assert "abcdef0123456789" not in str(caught.value)


@pytest.mark.parametrize("provider", ALL_PROVIDERS)
async def test_a_health_check_never_raises_and_never_leaks(provider, monkeypatch):
    secrets = secrets_for(provider)
    FakeTransport((401, {"token": secrets[0] if secrets else "x"})).install(monkeypatch)

    result = await make_provider(provider).health_check()

    assert isinstance(result, HealthResult)
    assert result.provider == provider.value
    assert result.latency_ms >= 0
    for secret in secrets:
        assert secret not in result.safe_message


@pytest.mark.parametrize("provider", ALL_PROVIDERS)
async def test_provider_failure_logging_does_not_expose_credentials(provider, monkeypatch):
    import json

    from app.core import logging as app_logging

    adapter = make_provider(provider)
    secrets = secrets_for(provider)
    captured: list = []
    for level in ("info", "warning", "error"):
        original = getattr(app_logging.log, level)

        def spy(event, _orig=original, **kw):
            captured.append((event, kw))
            return _orig(event, **kw)

        monkeypatch.setattr(app_logging.log, level, spy)

    if not secrets:
        assert adapter.context.credentials == {}
        result = await adapter.health_check()
        assert result.provider == provider.value
        if provider is CalendarProviderType.INTERNAL:
            assert result.connected is True
            assert captured == []
        else:
            assert provider is CalendarProviderType.GOOGLE_SERVICE_ACCOUNT
            assert not any(
                marker in json.dumps(captured, default=str).lower()
                for marker in ("access_token", "refresh_token", "private_key", "bearer ")
            )
        return

    FakeTransport((500, {"echo": secrets[0]})).install(monkeypatch)
    await adapter.health_check()

    assert secrets[0] not in json.dumps(captured, default=str)


# ============================================================ tenant scope ===

@pytest.mark.parametrize("provider", ALL_PROVIDERS)
def test_an_adapter_holds_no_handle_capable_of_widening_its_scope(provider):
    """
    Structural, and the strongest isolation guarantee in the layer.

    An adapter is constructed with a `CalendarContext` and nothing else: no
    database session, no `Tenant` row, no request. It therefore *cannot* reach
    another tenant, because it holds no object that could. This is a guarantee
    by construction, and this test is what stops someone "just passing the
    session in" for convenience.
    """
    adapter = make_provider(provider)
    assert set(vars(adapter)) == {"context"}
    for name, value in vars(adapter).items():
        assert not _looks_like_a_session(value), (
            f"{provider.value} holds {name}={type(value).__name__}"
        )


def _looks_like_a_session(value) -> bool:
    return any(
        hasattr(value, attr) for attr in ("execute", "get", "add", "commit")
    ) and not isinstance(value, (dict, list, str, bytes))


@pytest.mark.parametrize("provider", ALL_PROVIDERS)
def test_the_context_carries_exactly_one_tenant(provider):
    assert make_provider(provider).context.tenant_id == (
        "11111111-1111-1111-1111-111111111111"
    )


# ============================================================= idempotency ===

@pytest.mark.parametrize("provider", ALL_PROVIDERS)
def test_the_idempotency_key_reaches_the_provider_payload(provider):
    """
    Requirement 9. Every adapter must carry the key somewhere the provider can
    use it, or be able to find its own event afterwards — otherwise an
    ambiguous timeout has no safe resolution.
    """
    adapter = make_provider(provider)
    if not adapter.supports(CalendarCapability.CREATE_EVENT):
        assert type(adapter).create_event is CalendarProvider.create_event
        return

    if hasattr(adapter, "event_payload"):
        import json

        body = json.dumps(adapter.event_payload(REQUEST))
        assert REQUEST.idempotency_key[:20] in body, (
            f"{provider.value} drops the idempotency key from its payload"
        )
    else:
        # No payload of its own. Such a provider must either be able to
        # reconcile, or be one where an ambiguous timeout cannot happen or
        # cannot be resolved -- and in the latter case the service must fail
        # safe rather than retry. Both exceptions are documented limitations:
        #
        #   internal                -- no network, so no ambiguity exists
        #   google_service_account  -- the legacy client cannot read an event
        #                              back, so a timeout ends as FAILED
        can_reconcile = (
            getattr(type(adapter), "find_event_by_key")
            is not CalendarProvider.find_event_by_key
        )
        assert can_reconcile or provider in (
            CalendarProviderType.INTERNAL,
            CalendarProviderType.GOOGLE_SERVICE_ACCOUNT,
        ), f"{provider.value} can neither carry a key nor reconcile"


@pytest.mark.parametrize("provider", ALL_PROVIDERS)
async def test_reconciliation_defaults_to_unknown_not_absent(provider):
    """
    `find_event_by_key` returning `None` means "definitely not there", which
    licenses a retry. A provider that cannot check must not claim absence — the
    base default returns `None` only because the service also requires the
    original error to have been a timeout, and the internal provider never
    crosses a network.
    """
    adapter = make_provider(provider)
    if getattr(type(adapter), "find_event_by_key") is CalendarProvider.find_event_by_key:
        # Inheriting the default is only acceptable when there is no network
        # in the first place.
        assert provider in (
            CalendarProviderType.INTERNAL,
            CalendarProviderType.GOOGLE_SERVICE_ACCOUNT,
        ), (
            f"{provider.value} makes network calls but cannot reconcile an "
            f"ambiguous timeout"
        )


@pytest.mark.parametrize("provider", ALL_PROVIDERS)
async def test_a_created_event_always_carries_an_external_id(provider, monkeypatch):
    """
    The service refuses to write CONFIRMED without one, so an adapter that
    returns a `CalendarEvent` must have extracted an id from whatever shape
    its provider used.
    """
    success = {
        CalendarProviderType.GOOGLE: {
            "id": "gcal-1",
            "start": {"dateTime": "2026-06-16T14:00:00Z"},
            "end": {"dateTime": "2026-06-16T14:30:00Z"},
        },
        CalendarProviderType.MICROSOFT: {
            "id": "graph-1",
            "start": {"dateTime": "2026-06-16T14:00:00", "timeZone": "UTC"},
            "end": {"dateTime": "2026-06-16T14:30:00", "timeZone": "UTC"},
        },
        CalendarProviderType.CALCOM: {
            "status": "success",
            "data": {
                "uid": "cal-1", "status": "accepted",
                "start": "2026-06-16T14:00:00Z", "end": "2026-06-16T14:30:00Z",
            },
        },
    }.get(provider)

    adapter = make_provider(provider)
    if not adapter.supports(CalendarCapability.CREATE_EVENT):
        assert type(adapter).create_event is CalendarProvider.create_event
        return
    if success is None:
        # Internal creates a deterministic local reference; service-account
        # Google delegates to the legacy client and is verified with a stub.
        if provider is CalendarProviderType.GOOGLE_SERVICE_ACCOUNT:
            from app.integrations import google_calendar

            async def created_legacy_event(self, **kwargs):
                assert kwargs["start"].utcoffset().total_seconds() == 0
                assert kwargs["end"].utcoffset().total_seconds() == 0
                return "legacy-event-1"

            monkeypatch.setattr(
                google_calendar.CalendarClient, "create_event", created_legacy_event
            )
        event = await adapter.create_event(REQUEST)
        assert event.external_id
        return

    FakeTransport((200, success)).install(monkeypatch)
    event = await adapter.create_event(REQUEST)
    assert isinstance(event, CalendarEvent)
    assert event.external_id


@pytest.mark.parametrize("provider", ALL_PROVIDERS)
async def test_missing_remote_acknowledgement_never_confirms_an_event(provider, monkeypatch):
    """Remote adapters reject an empty success; local/legacy paths are explicit."""
    adapter = make_provider(provider)
    if not adapter.supports(CalendarCapability.CREATE_EVENT):
        assert type(adapter).create_event is CalendarProvider.create_event
        return

    if provider is CalendarProviderType.INTERNAL:
        event = await adapter.create_event(REQUEST)
        assert event.external_id == f"internal-{REQUEST.idempotency_key[:40]}"
        return

    if provider is CalendarProviderType.GOOGLE_SERVICE_ACCOUNT:
        from app.integrations import google_calendar

        async def legacy_created_without_id(self, **kwargs):
            return None

        monkeypatch.setattr(
            google_calendar.CalendarClient, "create_event", legacy_created_without_id
        )
        with pytest.raises(CalendarError):
            await adapter.create_event(REQUEST)
        return

    FakeTransport((200, {})).install(monkeypatch)
    with pytest.raises(CalendarError):
        await adapter.create_event(REQUEST)


@pytest.mark.parametrize("provider", ALL_PROVIDERS)
async def test_cancelling_a_missing_event_is_not_an_error(provider, monkeypatch):
    """
    Requirement 18: cancellation must be idempotent. A second cancel is a 404
    on Google; surfacing that would make a harmless double-click look broken.
    """
    adapter = make_provider(provider)
    if not adapter.supports(CalendarCapability.CANCEL_EVENT):
        with pytest.raises(CalendarUnsupportedError):
            await adapter.cancel_event("gone-1")
        return

    FakeTransport((404, {"error": "not found"})).install(monkeypatch)
    await adapter.cancel_event("gone-1")


# =============================================================== datetimes ===

@pytest.mark.parametrize("provider", ALL_PROVIDERS)
def test_naive_datetimes_are_refused_at_construction(provider):
    """
    A naive datetime reaching an adapter is how an appointment ends up five
    hours out. The cheapest place to catch it is the moment the value is
    constructed.
    """
    with pytest.raises(ValueError):
        EventRequest(
            title="x", start=datetime(2026, 6, 16, 14, 0),   # naive
            end=START + timedelta(minutes=30), timezone="UTC",
            attendee=Attendee(name="x"),
        )


@pytest.mark.parametrize("provider", ALL_PROVIDERS)
async def test_a_payload_transmits_an_unambiguous_instant(provider, monkeypatch):
    """Each outbound path pins an instant; local providers create no payload."""
    adapter = make_provider(provider)
    if hasattr(adapter, "event_payload"):
        import json

        body = json.dumps(adapter.event_payload(REQUEST))
        assert ("timeZone" in body) or ("+00:00" in body) or ("Z\"" in body), body
        return

    if provider is CalendarProviderType.CALCOM:
        response = {
            "status": "success",
            "data": {
                "uid": "cal-1",
                "status": "accepted",
                "start": "2026-06-16T14:00:00Z",
                "end": "2026-06-16T14:30:00Z",
            },
        }
        transport = FakeTransport((200, response)).install(monkeypatch)
        event = await adapter.create_event(REQUEST)
        assert event.external_id == "cal-1"
        assert transport.last()["json"]["start"] == REQUEST.start.astimezone(UTC).isoformat()
        assert "+00:00" in transport.last()["json"]["start"]
        return

    if provider is CalendarProviderType.INTERNAL:
        event = await adapter.create_event(REQUEST)
        assert event.start == REQUEST.start
        assert event.end == REQUEST.end
        assert event.start.utcoffset() is not None
        assert event.end.utcoffset() is not None
        return

    assert provider is CalendarProviderType.GOOGLE_SERVICE_ACCOUNT
    from app.integrations import google_calendar

    seen = {}

    async def capture_legacy_event(self, **kwargs):
        seen.update(kwargs)
        return "legacy-event-1"

    monkeypatch.setattr(
        google_calendar.CalendarClient, "create_event", capture_legacy_event
    )
    event = await adapter.create_event(REQUEST)
    assert event.external_id == "legacy-event-1"
    assert seen["start"].utcoffset().total_seconds() == 0
    assert seen["end"].utcoffset().total_seconds() == 0
````

### File 13: `tests/test_billing_subscriptions.py`

SHA-256: `e14c242ada4e806f224c34f3ee0630f9c58684b3d4f31d6562abcd747f50421d`

````
"""
Subscription lifecycle, Stripe adapter, webhooks and provider contract.

The theme: **provider state is the only thing that establishes billing
truth.** Creating a checkout session establishes nothing; a browser reaching
the success URL establishes nothing; only a verified webhook or a direct
provider read does.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import func, select

from app.billing import service, webhooks
from app.billing.base import (
    BillingCapability,
    BillingContextConfig,
    BillingProvider,
    RemoteSubscription,
)
from app.billing.errors import (
    BillingAuthError,
    BillingCardError,
    BillingConfigurationError,
    BillingError,
    BillingNotFoundError,
    BillingRateLimited,
    BillingTemporaryError,
    BillingTimeout,
    BillingValidationError,
    PlanNotFound,
)
from app.billing.plans import get_plan_by_code, resolve_price_id
from app.billing.providers.stripe import StripeProvider
from app.billing.registry import PROVIDERS, build, capabilities_of
from app.db.models import (
    BillingInterval,
    BillingProviderType,
    BillingWebhookReceipt,
    InvoiceStatus,
    Subscription,
    SubscriptionStatus,
)
from tests.conftest import FakeTransport, stripe_event, stripe_signature, subscribe

UTC = timezone.utc
ALL_PROVIDERS = list(BillingProviderType)

_CONFIG = {
    BillingProviderType.STRIPE: BillingContextConfig(
        secret_key="sk_test_SECRET_KEY_VALUE",
        webhook_secret="whsec_SECRET_HOOK_VALUE",
        base_url="https://stripe.test/v1",
        success_url="https://app.test/ok",
        cancel_url="https://app.test/no",
        return_url="https://app.test/back",
    ),
    BillingProviderType.MANUAL: BillingContextConfig(),
}


def make_provider(provider: BillingProviderType) -> BillingProvider:
    return build(provider, _CONFIG[provider])


def secrets_for(provider: BillingProviderType) -> list[str]:
    config = _CONFIG[provider]
    return [v for v in (config.secret_key, config.webhook_secret) if v]


def stripe_sub(**over) -> dict:
    body = {
        "id": "sub_FAKE123",
        "object": "subscription",
        "status": "active",
        "customer": "cus_FAKE123",
        "cancel_at_period_end": False,
        "current_period_start": 1_770_000_000,
        "current_period_end": 1_772_678_400,
        "items": {"data": [{
            "id": "si_FAKE",
            "price": {"id": "price_FAKE_pro_month", "recurring": {"interval": "month"}},
        }]},
    }
    body.update(over)
    return body


# ============================================================ plan catalogue ===

class TestPlanCatalogue:
    async def test_a_plan_resolves_by_code(self, db, billing_plans):
        plan = await get_plan_by_code(db, "pro")
        assert plan.name == "Pro"
        assert plan.included_voice_minutes == 2_000

    async def test_an_inactive_plan_is_refused_like_a_missing_one(
        self, db, billing_plans
    ):
        """
        Same error for both, so a grandfathered plan code cannot be
        discovered by probing the checkout endpoint.
        """
        from app.billing.plans import require_plan

        plan = await get_plan_by_code(db, "pro")
        plan.is_active = False
        await db.commit()

        with pytest.raises(PlanNotFound):
            await require_plan(db, "pro")
        with pytest.raises(PlanNotFound):
            await require_plan(db, "no-such-plan")

    async def test_the_price_id_comes_from_the_catalogue(self, db, billing_plans):
        """
        **The trust boundary.** A checkout request names a plan code; the
        price comes from here and never from a client.
        """
        plan = await get_plan_by_code(db, "pro")
        assert resolve_price_id(plan, BillingInterval.MONTH) == "price_FAKE_pro_month"
        assert resolve_price_id(plan, BillingInterval.YEAR) == "price_FAKE_pro_year"

    async def test_a_plan_with_no_price_id_fails_loudly(self, db, billing_plans):
        """
        Never defaults. Silently falling back to the monthly price when a
        customer asked for annual is a billing dispute.
        """
        plan = await get_plan_by_code(db, "pro")
        plan.provider_price_ids = {"month": "price_FAKE_pro_month"}
        await db.commit()

        with pytest.raises(BillingConfigurationError):
            resolve_price_id(plan, BillingInterval.YEAR)

    async def test_seeding_is_idempotent_and_never_clobbers_prices(
        self, db, billing_plans
    ):
        """
        An operator who repriced Pro in production must not have that undone
        by the next deploy -- that is an outage where every customer's next
        invoice is wrong.
        """
        from app.billing.plans import list_plans, sync_seed_plans

        plan = await get_plan_by_code(db, "pro")
        plan.monthly_price_cents = 59_900
        plan.provider_price_ids = {"month": "price_OPERATOR_EDITED"}
        await db.commit()

        await sync_seed_plans(db)
        await db.refresh(plan)

        assert plan.monthly_price_cents == 59_900
        assert plan.provider_price_ids["month"] == "price_OPERATOR_EDITED"
        assert len(await list_plans(db)) == 4

    def test_feature_entitlements_are_validated_on_write(self):
        """
        A typo like `team_memebers` would store cleanly and then grant the
        service default forever -- a limit that exists in the sales
        conversation and nowhere in the product.
        """
        from app.billing.plans import validate_feature_entitlements

        assert validate_feature_entitlements({"team_members": 5}) == {"team_members": 5}
        with pytest.raises(BillingConfigurationError):
            validate_feature_entitlements({"team_memebers": 5})
        with pytest.raises(BillingConfigurationError):
            validate_feature_entitlements({"team_members": "lots"})
        with pytest.raises(BillingConfigurationError):
            validate_feature_entitlements({"team_members": -7})

    async def test_production_requires_price_ids_for_active_paid_plans(
        self, db, billing_plans
    ):
        """Requirement 6, checked at boot rather than at checkout."""
        from app.billing.plans import configuration_problems, list_plans

        plans = await list_plans(db)
        assert configuration_problems(plans, is_production=True) == []

        pro = await get_plan_by_code(db, "pro")
        pro.provider_price_ids = {}
        await db.commit()

        problems = configuration_problems(await list_plans(db), is_production=True)
        assert any("pro" in p for p in problems)
        # ...but development is not blocked.
        assert configuration_problems(await list_plans(db), is_production=False) == []


# ================================================================= customer ===

class TestCustomer:
    async def test_a_customer_is_created_once(self, db, tenant_a, billing_plans):
        subscription, first = await service.ensure_customer(db, tenant_a)
        await db.commit()
        _, second = await service.ensure_customer(db, tenant_a)

        assert first == second
        count = (await db.execute(select(func.count(Subscription.id)))).scalar()
        assert count == 1

    async def test_an_existing_provider_customer_is_reused(
        self, db, tenant_a, billing_plans, monkeypatch
    ):
        """
        Covers a previous attempt that created a customer and then failed
        before we stored the id. Without this every retry makes another.
        """
        from app.billing.base import RemoteCustomer
        from app.billing.providers.manual import ManualBillingProvider

        calls = {"create": 0}

        async def counted(self, **kwargs):
            calls["create"] += 1
            return RemoteCustomer(external_id="should_not_happen")

        monkeypatch.setattr(ManualBillingProvider, "create_customer", counted)

        _, external = await service.ensure_customer(db, tenant_a)
        assert calls["create"] == 0
        assert external.startswith("manual_cus_")

    async def test_a_timeout_reconciles_rather_than_creating_twice(
        self, db, tenant_a, billing_plans, monkeypatch
    ):
        """
        A timeout on create means the provider may well have succeeded.
        Retrying blindly is how a tenant ends up with two customer records.
        """
        from app.billing.base import RemoteCustomer
        from app.billing.providers.manual import ManualBillingProvider

        calls = {"create": 0, "find": 0}

        async def timeout(self, **kwargs):
            calls["create"] += 1
            raise BillingTimeout("no response", provider="manual")

        async def found(self, tenant_id):
            calls["find"] += 1
            # First call (the pre-check) finds nothing; the recovery finds it.
            if calls["find"] == 1:
                return None
            return RemoteCustomer(external_id="cus_RECOVERED", already_existed=True)

        monkeypatch.setattr(ManualBillingProvider, "create_customer", timeout)
        monkeypatch.setattr(ManualBillingProvider, "find_customer_by_tenant", found)

        _, external = await service.ensure_customer(db, tenant_a)
        assert external == "cus_RECOVERED"
        assert calls["create"] == 1

    async def test_a_timeout_that_cannot_be_confirmed_raises(
        self, db, tenant_a, billing_plans, monkeypatch
    ):
        """
        "I could not check" is not evidence of absence. Creating anyway is the
        duplicate this path exists to prevent.
        """
        from app.billing.providers.manual import ManualBillingProvider

        async def timeout(self, **kwargs):
            raise BillingTimeout("no response", provider="manual")

        async def cannot_check(self, tenant_id):
            return None

        monkeypatch.setattr(ManualBillingProvider, "create_customer", timeout)
        monkeypatch.setattr(
            ManualBillingProvider, "find_customer_by_tenant", cannot_check
        )

        with pytest.raises(BillingTimeout):
            await service.ensure_customer(db, tenant_a)

    async def test_two_concurrent_checkouts_create_one_subscription_row(
        self, concurrent_sessionmaker
    ):
        """Requirement 31: no uncontrolled duplicate."""
        from app.billing.plans import SEED_PLANS, validate_feature_entitlements
        from app.db.models import BillingPlan, Tenant

        maker = concurrent_sessionmaker
        async with maker() as setup:
            tenant = Tenant(name="Race", twilio_number="+15550002222")
            setup.add(tenant)
            setup.add_all([
                BillingPlan(
                    code=s.code, name=s.name, monthly_price_cents=s.monthly_price_cents,
                    included_voice_minutes=s.included_voice_minutes,
                    feature_entitlements=validate_feature_entitlements(
                        s.feature_entitlements
                    ),
                    provider_price_ids={"month": f"price_{s.code}"},
                )
                for s in SEED_PLANS
            ])
            await setup.commit()
            await setup.refresh(tenant)
            tenant_id = tenant.id

        async def attempt():
            async with maker() as session:
                own = await session.get(Tenant, tenant_id)
                result = await service.ensure_customer(session, own)
                await session.commit()
                return result[1]

        results = await asyncio.gather(
            *(attempt() for _ in range(4)), return_exceptions=True
        )
        raised = [r for r in results if isinstance(r, BaseException)]
        assert not raised, f"a request saw an exception: {raised}"
        assert len(set(results)) == 1, "every request must get the same customer"

        async with maker() as session:
            count = (
                await session.execute(select(func.count(Subscription.id)))
            ).scalar()
        assert count == 1


# ============================================================= plan changes ===

class TestPlanChanges:
    async def test_an_upgrade_is_immediate(self, db, tenant_a, billing_plans):
        await subscribe(db, tenant_a, "starter")
        subscription = await service.change_plan(db, tenant_a, plan_code="pro")

        pro = await get_plan_by_code(db, "pro")
        assert subscription.plan_id == pro.id
        assert subscription.pending_plan_id is None

    async def test_a_downgrade_is_scheduled(self, db, tenant_a, billing_plans):
        """
        Applying it immediately would strand a customer who has already used
        more than the smaller plan includes, and refunding mid-period is an
        accounting system this step deliberately does not build.
        """
        await subscribe(db, tenant_a, "pro")
        subscription = await service.change_plan(db, tenant_a, plan_code="starter")

        pro = await get_plan_by_code(db, "pro")
        starter = await get_plan_by_code(db, "starter")

        assert subscription.plan_id == pro.id            # unchanged for now
        assert subscription.pending_plan_id == starter.id

    async def test_a_scheduled_downgrade_lands_at_renewal(
        self, db, tenant_a, billing_plans
    ):
        await subscribe(db, tenant_a, "pro")
        subscription = await service.change_plan(db, tenant_a, plan_code="starter")

        assert await service.apply_pending_downgrade(db, subscription)
        await db.commit()

        starter = await get_plan_by_code(db, "starter")
        assert subscription.plan_id == starter.id
        assert subscription.pending_plan_id is None

    async def test_a_downgrade_can_be_cancelled_by_reselecting(
        self, db, tenant_a, billing_plans
    ):
        await subscribe(db, tenant_a, "pro")
        await service.change_plan(db, tenant_a, plan_code="starter")

        subscription = await service.change_plan(db, tenant_a, plan_code="pro")
        assert subscription.pending_plan_id is None

    async def test_an_unknown_plan_is_refused(self, db, tenant_a, billing_plans):
        await subscribe(db, tenant_a, "pro")
        with pytest.raises(PlanNotFound):
            await service.change_plan(db, tenant_a, plan_code="platinum")

    async def test_a_plan_with_no_price_is_refused_before_the_provider_call(
        self, db, tenant_a, billing_plans
    ):
        await subscribe(db, tenant_a, "starter")
        pro = await get_plan_by_code(db, "pro")
        pro.provider_price_ids = {}
        await db.commit()

        with pytest.raises(BillingConfigurationError):
            await service.change_plan(db, tenant_a, plan_code="pro")


class TestCancellation:
    async def test_cancel_defaults_to_period_end(self, db, tenant_a, billing_plans):
        """
        The customer has paid for the month; cutting them off the moment they
        click cancel takes money for service not delivered.
        """
        await subscribe(db, tenant_a, "pro")
        subscription = await service.cancel(db, tenant_a)

        assert subscription.cancel_at_period_end
        assert subscription.status is SubscriptionStatus.CANCELING
        assert subscription.canceled_at is None

    async def test_immediate_cancel(self, db, tenant_a, billing_plans):
        await subscribe(db, tenant_a, "pro")
        subscription = await service.cancel(db, tenant_a, immediately=True)

        assert subscription.status is SubscriptionStatus.CANCELED
        assert subscription.canceled_at is not None

    async def test_cancelling_twice_is_idempotent(
        self, db, tenant_a, billing_plans, monkeypatch
    ):
        """
        A second click must not hit the provider again -- the audit trail
        would then show two cancellations for one decision.
        """
        from app.billing.providers.manual import ManualBillingProvider

        calls = {"cancel": 0}
        original = ManualBillingProvider.cancel_subscription

        async def counted(self, external_id, **kwargs):
            calls["cancel"] += 1
            return await original(self, external_id, **kwargs)

        monkeypatch.setattr(ManualBillingProvider, "cancel_subscription", counted)

        await subscribe(db, tenant_a, "pro")
        await service.cancel(db, tenant_a)
        await service.cancel(db, tenant_a)

        assert calls["cancel"] == 1

    async def test_a_provider_failure_still_records_the_intent(
        self, db, tenant_a, billing_plans, monkeypatch
    ):
        """
        The customer asked to cancel. Refusing to remember that because a
        vendor API was slow means they ask again and get charged again.
        """
        from app.billing.providers.manual import ManualBillingProvider

        async def fail(self, external_id, **kwargs):
            raise BillingTemporaryError("provider down", provider="manual")

        monkeypatch.setattr(ManualBillingProvider, "cancel_subscription", fail)

        await subscribe(db, tenant_a, "pro")
        subscription = await service.cancel(db, tenant_a)

        assert subscription.cancel_at_period_end
        assert subscription.last_error


class TestOrdering:
    async def test_a_stale_event_does_not_overwrite_newer_state(
        self, db, tenant_a, billing_plans
    ):
        """
        **Requirement 23.** Applying a stale event would downgrade a customer
        who just upgraded, or resurrect a cancelled subscription.
        """
        subscription = await subscribe(db, tenant_a, "pro")
        now = datetime.now(UTC)

        newer = RemoteSubscription(
            external_id="sub_1", status=SubscriptionStatus.ACTIVE, updated_at=now
        )
        assert service._apply_remote(subscription, newer)
        assert subscription.status is SubscriptionStatus.ACTIVE

        older = RemoteSubscription(
            external_id="sub_1", status=SubscriptionStatus.CANCELED,
            updated_at=now - timedelta(hours=1),
        )
        assert service._apply_remote(subscription, older) is False
        assert subscription.status is SubscriptionStatus.ACTIVE

    async def test_a_newer_event_is_applied(self, db, tenant_a, billing_plans):
        subscription = await subscribe(db, tenant_a, "pro")
        now = datetime.now(UTC)

        service._apply_remote(subscription, RemoteSubscription(
            external_id="sub_1", status=SubscriptionStatus.ACTIVE, updated_at=now
        ))
        service._apply_remote(subscription, RemoteSubscription(
            external_id="sub_1", status=SubscriptionStatus.PAST_DUE,
            updated_at=now + timedelta(minutes=5),
        ))
        assert subscription.status is SubscriptionStatus.PAST_DUE


class TestReconciliation:
    async def test_a_lost_subscription_is_adopted_not_recreated(
        self, db, tenant_a, billing_plans, monkeypatch
    ):
        """
        **Requirement 32.** A timeout after `create_subscription` leaves the
        provider holding a subscription we never recorded. Listing by customer
        finds it.
        """
        from app.billing.providers.manual import ManualBillingProvider

        subscription = await subscribe(db, tenant_a, "pro")
        subscription.external_subscription_id = None
        subscription.status = SubscriptionStatus.INCOMPLETE
        await db.commit()

        async def lists(self, external_customer_id):
            return [RemoteSubscription(
                external_id="sub_ORPHANED",
                status=SubscriptionStatus.ACTIVE,
                external_customer_id=external_customer_id,
                external_price_id="price_FAKE_pro_month",
                current_period_start=datetime.now(UTC),
                current_period_end=datetime.now(UTC) + timedelta(days=30),
            )]

        monkeypatch.setattr(ManualBillingProvider, "list_subscriptions", lists)
        monkeypatch.setattr(
            ManualBillingProvider, "capabilities",
            ManualBillingProvider.capabilities | {BillingCapability.LIST_SUBSCRIPTIONS},
        )

        recovered = await service.reconcile_subscription(db, tenant_a)
        assert recovered.external_subscription_id == "sub_ORPHANED"
        assert recovered.status is SubscriptionStatus.ACTIVE

        count = (await db.execute(select(func.count(Subscription.id)))).scalar()
        assert count == 1


# ============================================================ Stripe adapter ===

class TestStripeMapping:
    @pytest.mark.parametrize("raw,expected", [
        ("trialing", SubscriptionStatus.TRIALING),
        ("active", SubscriptionStatus.ACTIVE),
        ("past_due", SubscriptionStatus.PAST_DUE),
        ("unpaid", SubscriptionStatus.PAST_DUE),
        ("canceled", SubscriptionStatus.CANCELED),
        ("incomplete", SubscriptionStatus.INCOMPLETE),
        ("incomplete_expired", SubscriptionStatus.INCOMPLETE_EXPIRED),
        ("paused", SubscriptionStatus.PAUSED),
    ])
    def test_status_normalization(self, raw, expected):
        provider = make_provider(BillingProviderType.STRIPE)
        assert provider._to_subscription(stripe_sub(status=raw)).status is expected

    def test_cancel_at_period_end_becomes_a_state(self):
        """
        Stripe expresses "winding down" as `active` plus a boolean, which
        loses the distinction every UI needs.
        """
        provider = make_provider(BillingProviderType.STRIPE)
        remote = provider._to_subscription(
            stripe_sub(status="active", cancel_at_period_end=True)
        )
        assert remote.status is SubscriptionStatus.CANCELING
        assert remote.cancel_at_period_end

    def test_an_unmapped_status_is_read_safely(self):
        """
        A Stripe change we have not caught up with. INCOMPLETE grants no
        service and cancels nobody.
        """
        provider = make_provider(BillingProviderType.STRIPE)
        remote = provider._to_subscription(stripe_sub(status="something_new"))
        assert remote.status is SubscriptionStatus.INCOMPLETE

    def test_period_bounds_are_read_from_the_item_when_absent_on_the_root(self):
        """
        Stripe moved these onto items in a 2025 API version, and the version
        is set per-account -- we do not control which shape arrives.
        """
        provider = make_provider(BillingProviderType.STRIPE)
        body = stripe_sub()
        del body["current_period_start"]
        del body["current_period_end"]
        body["items"]["data"][0]["current_period_start"] = 1_770_000_000
        body["items"]["data"][0]["current_period_end"] = 1_772_678_400

        remote = provider._to_subscription(body)
        assert remote.current_period_start == datetime.fromtimestamp(
            1_770_000_000, tz=UTC
        )

    def test_a_related_object_is_accepted_as_an_id_or_an_object(self):
        provider = make_provider(BillingProviderType.STRIPE)
        assert provider._to_subscription(
            stripe_sub(customer="cus_A")
        ).external_customer_id == "cus_A"
        assert provider._to_subscription(
            stripe_sub(customer={"id": "cus_B", "object": "customer"})
        ).external_customer_id == "cus_B"

    def test_an_annual_price_is_detected(self):
        provider = make_provider(BillingProviderType.STRIPE)
        body = stripe_sub()
        body["items"]["data"][0]["price"]["recurring"]["interval"] = "year"
        assert provider._to_subscription(body).interval is BillingInterval.YEAR

    @pytest.mark.parametrize("raw,expected", [
        ("draft", InvoiceStatus.DRAFT), ("open", InvoiceStatus.OPEN),
        ("paid", InvoiceStatus.PAID), ("void", InvoiceStatus.VOID),
        ("uncollectible", InvoiceStatus.UNCOLLECTIBLE),
        ("something_new", InvoiceStatus.DRAFT),
    ])
    def test_invoice_status_normalization(self, raw, expected):
        """Requirement 20: do not invent invoice status."""
        provider = make_provider(BillingProviderType.STRIPE)
        invoice = provider._to_invoice({
            "id": "in_FAKE", "status": raw, "currency": "usd",
            "amount_due": 19900, "amount_paid": 19900,
        })
        assert invoice.status is expected


class TestStripeRequests:
    async def test_subscription_creation_payload(self, monkeypatch):
        transport = FakeTransport((200, stripe_sub())).install(monkeypatch)
        provider = make_provider(BillingProviderType.STRIPE)

        await provider.create_subscription(
            external_customer_id="cus_FAKE", price_id="price_FAKE_pro_month",
            trial_days=14, idempotency_key="sub:tenant-1",
            metadata={"voxdesk_tenant_id": "tenant-1"},
        )

        request = transport.last()
        # Stripe is form-encoded, so the payload is in `form`, not `json`.
        assert request["form"]["items[0][price]"] == "price_FAKE_pro_month"
        assert request["form"]["trial_period_days"] == 14
        assert request["form"]["metadata[voxdesk_tenant_id]"] == "tenant-1"
        assert request["headers"]["Idempotency-Key"] == "sub:tenant-1"
        assert request["headers"]["Authorization"].startswith("Bearer sk_test")

    async def test_the_idempotency_key_is_sent_on_every_mutating_call(
        self, monkeypatch
    ):
        """
        Stripe honours `Idempotency-Key`, and it is the difference between a
        timeout costing a retry and a timeout costing a second subscription.
        """
        transport = FakeTransport((200, {"id": "cus_FAKE"})).install(monkeypatch)
        provider = make_provider(BillingProviderType.STRIPE)

        await provider.create_customer(
            tenant_id="t-1", email=None, name="Acme", idempotency_key="customer:t-1",
        )
        assert transport.last()["headers"]["Idempotency-Key"] == "customer:t-1"

    async def test_updating_a_price_reads_the_item_id_first(self, monkeypatch):
        """
        Sending `items[0][price]` without an id *adds* a second item and bills
        the customer for both.
        """
        transport = FakeTransport(
            (200, stripe_sub()),      # the read
            (200, stripe_sub()),      # the update
        ).install(monkeypatch)
        provider = make_provider(BillingProviderType.STRIPE)

        await provider.update_subscription(
            "sub_FAKE123", price_id="price_NEW", idempotency_key="k"
        )
        assert transport.call_count == 2
        assert transport.requests[0]["method"] == "GET"

    async def test_cancelling_a_missing_subscription_succeeds(self, monkeypatch):
        FakeTransport((404, {"error": {"message": "No such subscription"}})).install(
            monkeypatch
        )
        provider = make_provider(BillingProviderType.STRIPE)

        remote = await provider.cancel_subscription("sub_GONE", at_period_end=False)
        assert remote.status is SubscriptionStatus.CANCELED

    async def test_checkout_requires_configured_urls(self, monkeypatch):
        provider = StripeProvider(BillingContextConfig(secret_key="sk_test_x"))
        with pytest.raises(BillingConfigurationError):
            await provider.create_checkout_session(
                external_customer_id="cus_x", price_id="price_x", tenant_id="t",
            )

    async def test_the_tenant_id_is_stamped_on_the_session_and_subscription(
        self, monkeypatch
    ):
        """
        So a webhook can be attributed to a tenant even when the local row is
        not written yet -- requirement 23's ordering problem.
        """
        transport = FakeTransport(
            (200, {"id": "cs_FAKE", "url": "https://checkout.test/x"})
        ).install(monkeypatch)
        provider = make_provider(BillingProviderType.STRIPE)

        await provider.create_checkout_session(
            external_customer_id="cus_x", price_id="price_x",
            tenant_id="tenant-42", trial_days=0,
        )
        form = transport.last()["form"]
        assert transport.last()["url"].endswith("/checkout/sessions")
        assert form["metadata[voxdesk_tenant_id]"] == "tenant-42"
        assert form["subscription_data[metadata][voxdesk_tenant_id]"] == "tenant-42"

    @pytest.mark.parametrize("status,expected,retryable", [
        (401, BillingAuthError, False),
        (402, BillingCardError, False),
        (404, BillingNotFoundError, False),
        (400, BillingValidationError, False),
        (429, BillingRateLimited, True),
        (500, BillingTemporaryError, True),
        (503, BillingTemporaryError, True),
    ])
    async def test_error_mapping(self, status, expected, retryable, monkeypatch):
        FakeTransport((status, {"error": {"message": "nope"}})).install(monkeypatch)
        provider = make_provider(BillingProviderType.STRIPE)

        with pytest.raises(BillingError) as caught:
            await provider.request("POST", "https://stripe.test/v1/x")
        assert isinstance(caught.value, expected)
        assert caught.value.retryable is retryable

    async def test_a_timeout_is_retryable(self, monkeypatch):
        FakeTransport(httpx.ReadTimeout("slow")).install(monkeypatch)
        provider = make_provider(BillingProviderType.STRIPE)

        with pytest.raises(BillingTimeout) as caught:
            await provider.request("POST", "https://stripe.test/v1/x")
        assert caught.value.retryable is True


# ================================================== webhook signature (21) ===

class TestWebhookSignature:
    SECRET = "whsec_SECRET_HOOK_VALUE"

    def provider(self):
        return make_provider(BillingProviderType.STRIPE)

    def test_a_valid_signature_is_accepted(self):
        body = stripe_event("invoice.paid", {"id": "in_1"})
        event = self.provider().verify_webhook(
            raw_body=body, signature_header=stripe_signature(body, self.SECRET)
        )
        assert event.event_type == "invoice.paid"

    def test_a_wrong_secret_is_rejected(self):
        body = stripe_event("invoice.paid", {"id": "in_1"})
        with pytest.raises(BillingAuthError):
            self.provider().verify_webhook(
                raw_body=body,
                signature_header=stripe_signature(body, "whsec_WRONG"),
            )

    def test_a_tampered_body_is_rejected(self):
        body = stripe_event("invoice.paid", {"id": "in_1", "amount_paid": 100})
        header = stripe_signature(body, self.SECRET)
        tampered = body.replace(b'"amount_paid": 100', b'"amount_paid": 999')

        with pytest.raises(BillingAuthError):
            self.provider().verify_webhook(
                raw_body=tampered, signature_header=header
            )

    def test_re_serialization_would_break_it(self):
        """
        Requirement 21: verification must use the exact raw body. This is why
        the route reads `await request.body()` and never `request.json()`.
        """
        body = stripe_event("invoice.paid", {"id": "in_1"})
        header = stripe_signature(body, self.SECRET)
        # A parser that round-trips with different separators changes bytes.
        reserialized = json.dumps(json.loads(body), indent=2).encode()

        assert reserialized != body
        with pytest.raises(BillingAuthError):
            self.provider().verify_webhook(
                raw_body=reserialized, signature_header=header
            )

    def test_a_stale_timestamp_is_rejected(self):
        """The replay defence. The timestamp is inside the signed payload."""
        body = stripe_event("invoice.paid", {"id": "in_1"})
        header = stripe_signature(
            body, self.SECRET, timestamp=int(time.time()) - 3600
        )
        with pytest.raises(BillingAuthError) as caught:
            self.provider().verify_webhook(raw_body=body, signature_header=header)
        assert "old" in str(caught.value)

    def test_a_future_timestamp_is_rejected(self):
        body = stripe_event("invoice.paid", {"id": "in_1"})
        header = stripe_signature(
            body, self.SECRET, timestamp=int(time.time()) + 3600
        )
        with pytest.raises(BillingAuthError):
            self.provider().verify_webhook(raw_body=body, signature_header=header)

    def test_several_v1_values_are_all_checked(self):
        """
        Stripe sends several during a signing-secret rotation. Accepting only
        the first would drop valid events for the whole rotation window.
        """
        body = stripe_event("invoice.paid", {"id": "in_1"})
        valid = stripe_signature(body, self.SECRET)
        header = valid + ",v1=" + "0" * 64

        assert self.provider().verify_webhook(
            raw_body=body, signature_header=header
        ).event_type == "invoice.paid"

    def test_an_unknown_scheme_is_ignored(self):
        body = stripe_event("invoice.paid", {"id": "in_1"})
        header = "v0=deadbeef," + stripe_signature(body, self.SECRET)
        assert self.provider().verify_webhook(
            raw_body=body, signature_header=header
        )

    @pytest.mark.parametrize("header", [
        "", "garbage", "t=notanumber,v1=abc", "v1=abc", "t=123",
    ])
    def test_a_malformed_header_is_rejected(self, header):
        body = stripe_event("invoice.paid", {"id": "in_1"})
        with pytest.raises(BillingAuthError):
            self.provider().verify_webhook(raw_body=body, signature_header=header)

    def test_a_malformed_body_is_rejected_after_verification(self):
        body = b"not json at all"
        with pytest.raises(BillingValidationError):
            self.provider().verify_webhook(
                raw_body=body, signature_header=stripe_signature(body, self.SECRET)
            )

    def test_an_event_with_no_id_is_rejected(self):
        body = json.dumps({"type": "invoice.paid"}).encode()
        with pytest.raises(BillingValidationError):
            self.provider().verify_webhook(
                raw_body=body, signature_header=stripe_signature(body, self.SECRET)
            )

    def test_verification_without_a_configured_secret_refuses(self):
        provider = StripeProvider(BillingContextConfig(secret_key="sk_test_x"))
        body = stripe_event("invoice.paid", {"id": "in_1"})
        with pytest.raises(BillingConfigurationError):
            provider.verify_webhook(
                raw_body=body, signature_header=stripe_signature(body, "x")
            )


# =================================================== webhook processing (22) ===

class TestWebhookProcessing:
    @pytest.fixture(autouse=True)
    def _stripe(self, stripe_settings):
        return stripe_settings

    async def _event(self, db, body: bytes):
        provider = make_provider(BillingProviderType.STRIPE)
        event = provider.verify_webhook(
            raw_body=body,
            signature_header=stripe_signature(body, "whsec_SECRET_HOOK_VALUE"),
        )
        return await webhooks.process_event(db, event)

    async def test_subscription_updated_applies_state(
        self, db, tenant_a, billing_plans
    ):
        await subscribe(
            db, tenant_a, "starter",
            provider=BillingProviderType.STRIPE,
            external_subscription_id="sub_FAKE123",
        )
        body = stripe_event("customer.subscription.updated", stripe_sub(
            status="past_due", metadata={"voxdesk_tenant_id": str(tenant_a.id)}
        ))

        outcome = await self._event(db, body)
        assert outcome.handled

        subscription = await service.get_subscription(db, tenant_a.id)
        assert subscription.status is SubscriptionStatus.PAST_DUE

    async def test_a_duplicate_event_is_processed_once(
        self, db, tenant_a, billing_plans
    ):
        await subscribe(
            db, tenant_a, "starter", provider=BillingProviderType.STRIPE,
            external_subscription_id="sub_FAKE123",
        )
        body = stripe_event(
            "customer.subscription.updated",
            stripe_sub(metadata={"voxdesk_tenant_id": str(tenant_a.id)}),
            event_id="evt_SAME",
        )

        first = await self._event(db, body)
        second = await self._event(db, body)

        assert first.handled
        assert second.duplicate and not second.handled

        count = (
            await db.execute(select(func.count(BillingWebhookReceipt.id)))
        ).scalar()
        assert count == 1

    async def test_an_out_of_order_event_does_not_regress_state(
        self, db, tenant_a, billing_plans
    ):
        """
        `customer.subscription.updated` routinely arrives before
        `checkout.session.completed`, and a retried old event can land after a
        newer one.
        """
        await subscribe(
            db, tenant_a, "starter", provider=BillingProviderType.STRIPE,
            external_subscription_id="sub_FAKE123",
        )
        now = int(time.time())
        meta = {"voxdesk_tenant_id": str(tenant_a.id)}

        await self._event(db, stripe_event(
            "customer.subscription.updated",
            stripe_sub(status="active", metadata=meta),
            event_id="evt_new", created=now,
        ))
        await self._event(db, stripe_event(
            "customer.subscription.updated",
            stripe_sub(status="canceled", metadata=meta),
            event_id="evt_old", created=now - 3600,
        ))

        subscription = await service.get_subscription(db, tenant_a.id)
        assert subscription.status is SubscriptionStatus.ACTIVE

    async def test_a_stale_delete_does_not_cancel_a_reactivated_subscription(
        self, db, tenant_a, billing_plans
    ):
        await subscribe(
            db, tenant_a, "starter", provider=BillingProviderType.STRIPE,
            external_subscription_id="sub_FAKE123",
        )
        now = int(time.time())
        meta = {"voxdesk_tenant_id": str(tenant_a.id)}

        await self._event(db, stripe_event(
            "customer.subscription.updated",
            stripe_sub(status="active", metadata=meta),
            event_id="evt_live", created=now,
        ))
        await self._event(db, stripe_event(
            "customer.subscription.deleted",
            stripe_sub(status="canceled", metadata=meta),
            event_id="evt_stale_delete", created=now - 7200,
        ))

        subscription = await service.get_subscription(db, tenant_a.id)
        assert subscription.status is SubscriptionStatus.ACTIVE

    async def test_a_payment_failure_marks_past_due_but_keeps_service(
        self, db, tenant_a, billing_plans
    ):
        await subscribe(
            db, tenant_a, "pro", provider=BillingProviderType.STRIPE,
            external_subscription_id="sub_FAKE123",
        )
        body = stripe_event("invoice.payment_failed", {
            "id": "in_FAILED", "object": "invoice", "status": "open",
            "currency": "usd", "amount_due": 49900, "amount_paid": 0,
            "subscription": "sub_FAKE123", "customer": "cus_FAKE123",
        })
        await self._event(db, body)

        subscription = await service.get_subscription(db, tenant_a.id)
        assert subscription.status is SubscriptionStatus.PAST_DUE
        assert subscription.last_payment_failed_at is not None

        # ...and the tenant is still entitled.
        from app.billing.entitlements import load_context

        assert (await load_context(db, tenant_a)).is_entitled

    async def test_a_paid_invoice_clears_past_due(self, db, tenant_a, billing_plans):
        await subscribe(
            db, tenant_a, "pro", provider=BillingProviderType.STRIPE,
            external_subscription_id="sub_FAKE123",
            status=SubscriptionStatus.PAST_DUE,
        )
        await self._event(db, stripe_event("invoice.paid", {
            "id": "in_PAID", "object": "invoice", "status": "paid",
            "currency": "usd", "amount_due": 49900, "amount_paid": 49900,
            "subscription": "sub_FAKE123", "customer": "cus_FAKE123",
        }))

        subscription = await service.get_subscription(db, tenant_a.id)
        assert subscription.status is SubscriptionStatus.ACTIVE
        assert subscription.last_invoice_status is InvoiceStatus.PAID

    async def test_a_paid_invoice_is_mirrored(self, db, tenant_a, billing_plans):
        from app.db.models import BillingInvoice

        await subscribe(
            db, tenant_a, "pro", provider=BillingProviderType.STRIPE,
            external_subscription_id="sub_FAKE123",
        )
        await self._event(db, stripe_event("invoice.paid", {
            "id": "in_MIRROR", "object": "invoice", "status": "paid",
            "currency": "usd", "amount_due": 49900, "amount_paid": 49900,
            "subscription": "sub_FAKE123", "customer": "cus_FAKE123",
            "hosted_invoice_url": "https://invoice.test/x",
        }))

        invoice = (await db.execute(select(BillingInvoice))).scalars().one()
        assert invoice.external_invoice_id == "in_MIRROR"
        assert invoice.amount_paid_cents == 49900

    async def test_an_unattributable_event_is_recorded_and_ignored(
        self, db, billing_plans
    ):
        """
        The receipt is kept so a replay is recognised, and the reason is
        recorded so it is investigable rather than silent.
        """
        body = stripe_event("customer.subscription.updated", stripe_sub(
            customer="cus_UNKNOWN", metadata={},
        ))
        outcome = await self._event(db, body)

        assert not outcome.handled
        assert outcome.ignored_reason == "no_tenant"

        receipt = (
            await db.execute(select(BillingWebhookReceipt))
        ).scalars().one()
        assert receipt.last_error == "no matching tenant"

    async def test_an_unhandled_event_type_is_recorded_and_ignored(
        self, db, tenant_a, billing_plans
    ):
        body = stripe_event("customer.created", {"id": "cus_FAKE123"})
        outcome = await self._event(db, body)
        assert outcome.ignored_reason == "unhandled_type"

    async def test_a_forged_tenant_id_that_does_not_exist_is_ignored(
        self, db, tenant_a, billing_plans
    ):
        """
        The metadata is ours, but the event body is not. A `tenant_id` that
        does not resolve to a real tenant must not select one.
        """
        body = stripe_event("customer.subscription.updated", stripe_sub(
            customer="cus_NOBODY",
            metadata={"voxdesk_tenant_id": str(uuid.uuid4())},
        ))
        outcome = await self._event(db, body)
        assert not outcome.handled

    async def test_checkout_completion_does_not_grant_service_by_itself(
        self, db, tenant_a, billing_plans, monkeypatch
    ):
        """
        **Requirement 8 and 10.** The session says a payment page was
        completed; the subscription object says what state it is in, and those
        can differ (3DS, a failed initial charge).
        """
        async def unavailable(self, external_id):
            return None      # the follow-up read fails

        monkeypatch.setattr(StripeProvider, "get_subscription", unavailable)

        await subscribe(
            db, tenant_a, "pro", provider=BillingProviderType.STRIPE,
            external_subscription_id=None,
            status=SubscriptionStatus.INCOMPLETE,
        )
        await self._event(db, stripe_event("checkout.session.completed", {
            "id": "cs_FAKE", "object": "checkout.session",
            "subscription": "sub_NEW", "customer": "cus_FAKE123",
            "metadata": {"voxdesk_tenant_id": str(tenant_a.id)},
        }))

        subscription = await service.get_subscription(db, tenant_a.id)
        assert subscription.external_subscription_id == "sub_NEW"
        assert subscription.status is SubscriptionStatus.INCOMPLETE


# ============================================================ contract (33) ===

class TestBillingProviderContract:
    """
    Requirement 33, parameterized over the **registry** rather than a
    hand-written list, so a future provider inherits the suite the moment it
    is registered.
    """

    @pytest.mark.parametrize("provider", ALL_PROVIDERS)
    def test_every_type_has_an_adapter(self, provider):
        assert provider in PROVIDERS

    @pytest.mark.parametrize("provider", ALL_PROVIDERS)
    def test_the_name_matches_the_enum_value(self, provider):
        assert PROVIDERS[provider].name == provider.value

    @pytest.mark.parametrize("provider", ALL_PROVIDERS)
    def test_declared_capabilities_are_implemented(self, provider):
        """
        Declaring a capability you have not written routes work to you and
        produces a failure instead of a clean "unsupported".
        """
        adapter = PROVIDERS[provider]
        methods = {
            BillingCapability.CREATE_CUSTOMER: "create_customer",
            BillingCapability.GET_CUSTOMER: "get_customer",
            BillingCapability.CREATE_SUBSCRIPTION: "create_subscription",
            BillingCapability.UPDATE_SUBSCRIPTION: "update_subscription",
            BillingCapability.CANCEL_SUBSCRIPTION: "cancel_subscription",
            BillingCapability.GET_SUBSCRIPTION: "get_subscription",
            BillingCapability.LIST_SUBSCRIPTIONS: "list_subscriptions",
            BillingCapability.LIST_INVOICES: "list_invoices",
            BillingCapability.GET_INVOICE: "get_invoice",
            BillingCapability.CHECKOUT_SESSION: "create_checkout_session",
            BillingCapability.PORTAL_SESSION: "create_portal_session",
            BillingCapability.VERIFY_WEBHOOK: "verify_webhook",
            BillingCapability.HEALTH_CHECK: "health_check",
        }
        for capability, name in methods.items():
            if capability not in capabilities_of(provider):
                continue
            assert getattr(adapter, name) is not getattr(BillingProvider, name), (
                f"{provider.value} declares {capability.value} but does not "
                f"override {name}"
            )

    @pytest.mark.parametrize("provider", ALL_PROVIDERS)
    async def test_undeclared_capabilities_raise_unsupported(self, provider):
        from app.billing.errors import BillingUnsupportedError

        adapter = make_provider(provider)
        if BillingCapability.CHECKOUT_SESSION not in capabilities_of(provider):
            with pytest.raises(BillingUnsupportedError):
                await adapter.create_checkout_session(
                    external_customer_id="x", price_id="y", tenant_id="z"
                )
        if BillingCapability.LIST_INVOICES not in capabilities_of(provider):
            with pytest.raises(BillingUnsupportedError):
                await adapter.list_invoices("cus_x")

    @pytest.mark.parametrize("provider", ALL_PROVIDERS)
    @pytest.mark.parametrize("status,expected,retryable", [
        (401, BillingAuthError, False),
        (404, BillingNotFoundError, False),
        (429, BillingRateLimited, True),
        (500, BillingTemporaryError, True),
    ])
    async def test_error_normalization_is_identical(
        self, provider, status, expected, retryable, monkeypatch
    ):
        FakeTransport((status, {"error": "x"})).install(monkeypatch)
        adapter = make_provider(provider)

        with pytest.raises(BillingError) as caught:
            await adapter.request("POST", "https://provider.test/x")
        assert isinstance(caught.value, expected)
        assert caught.value.retryable is retryable

    @pytest.mark.parametrize("provider", ALL_PROVIDERS)
    async def test_timeouts_are_normalized(self, provider, monkeypatch):
        FakeTransport(httpx.ReadTimeout("slow")).install(monkeypatch)
        with pytest.raises(BillingTimeout):
            await make_provider(provider).request("GET", "https://provider.test/x")

    @pytest.mark.parametrize("provider", ALL_PROVIDERS)
    async def test_no_httpx_exception_escapes(self, provider, monkeypatch):
        for failure in (
            httpx.ConnectError("x"), httpx.ReadTimeout("x"),
            httpx.PoolTimeout("x"), httpx.RemoteProtocolError("x"),
        ):
            FakeTransport(failure).install(monkeypatch)
            try:
                await make_provider(provider).request("GET", "https://provider.test/x")
            except BillingError:
                pass
            except httpx.HTTPError as exc:
                pytest.fail(f"{provider.value} leaked {type(exc).__name__}")

    @pytest.mark.parametrize("provider", ALL_PROVIDERS)
    def test_the_config_repr_hides_secrets(self, provider):
        """A config in a traceback must not print an API key."""
        printed = repr(make_provider(provider).config)
        for secret in secrets_for(provider):
            assert secret not in printed
        assert "has_secret" in printed

    @pytest.mark.parametrize("provider", ALL_PROVIDERS)
    async def test_secrets_never_appear_in_a_raised_error(self, provider, monkeypatch):
        secrets = secrets_for(provider)
        if not secrets:
            adapter = make_provider(provider)
            assert adapter.config.secret_key == ""
            assert adapter.config.webhook_secret == ""
            transport = FakeTransport((401, {"detail": "sk_live_LEAKED_KEY_12345"})).install(monkeypatch)
            with pytest.raises(BillingAuthError) as caught:
                await adapter.request("POST", "https://provider.test/x")
            assert transport.call_count == 1
            assert "sk_live_LEAKED_KEY_12345" not in str(caught.value)
            return

        secret = secrets[0]
        FakeTransport(
            (400, {"error": {"message": f"key {secret} rejected"}, "key": secret})
        ).install(monkeypatch)

        with pytest.raises(BillingError) as caught:
            await make_provider(provider).request("POST", "https://provider.test/x")
        assert secret not in str(caught.value)
        assert secret not in caught.value.safe_message

    @pytest.mark.parametrize("provider", ALL_PROVIDERS)
    async def test_a_401_body_is_never_echoed(self, provider, monkeypatch):
        FakeTransport((401, {"detail": "sk_live_LEAKED_KEY_12345 is invalid"})).install(
            monkeypatch
        )
        with pytest.raises(BillingAuthError) as caught:
            await make_provider(provider).request("GET", "https://provider.test/x")
        assert "sk_live_LEAKED_KEY_12345" not in str(caught.value)

    @pytest.mark.parametrize("provider", ALL_PROVIDERS)
    async def test_a_health_check_never_raises_and_never_leaks(
        self, provider, monkeypatch
    ):
        secrets = secrets_for(provider)
        FakeTransport((401, {"key": secrets[0] if secrets else "x"})).install(
            monkeypatch
        )
        result = await make_provider(provider).health_check()

        assert result.provider == provider.value
        assert result.latency_ms >= 0
        for secret in secrets:
            assert secret not in result.safe_message

    @pytest.mark.parametrize("provider", ALL_PROVIDERS)
    def test_an_adapter_holds_no_database_handle(self, provider):
        """
        An adapter is constructed with a config and nothing else, so it cannot
        reach another tenant -- a guarantee by construction rather than by
        discipline.
        """
        adapter = make_provider(provider)
        assert set(vars(adapter)) == {"config"}

    @pytest.mark.parametrize("provider", ALL_PROVIDERS)
    async def test_customer_creation_is_deterministic_for_reconciliation(
        self, provider, monkeypatch
    ):
        """
        Every adapter must let the service answer "did my earlier attempt
        land?" — either by carrying an idempotency key or by being able to
        look the customer up.
        """
        adapter = make_provider(provider)
        can_find = (
            getattr(type(adapter), "find_customer_by_tenant")
            is not BillingProvider.find_customer_by_tenant
        )
        assert can_find, (
            f"{provider.value} cannot reconcile an ambiguous customer creation"
        )
````

### File 14: `tests/jobs/test_recovery.py`

SHA-256: `293b019034be05eb819ee2106d50b6bfc418d7221ea560a2390258b4b3b6aec6`

````
"""Real PostgreSQL claim locking and durable worker-crash recovery.

Requires DATABASE_URL pointing at a disposable PostgreSQL database migrated to
head. Separate spawned processes exercise the real SKIP LOCKED claim path.
No SQLite substitution or unconditional skip is used.
"""
from __future__ import annotations

import asyncio
import multiprocessing
import os
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import DurableJob, JobAttempt, Organization, Tenant
from app.jobs.heartbeat import recover_abandoned
from app.jobs.repository import claim_next_job


def _claim_worker(url, tenant_id, ready, release, output):
    async def run():
        engine = create_async_engine(url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                job = await claim_next_job(
                    session, worker_id=str(os.getpid()), tenant_id=uuid.UUID(tenant_id)
                )
                output.put(str(job.id) if job else None)
                ready.set()
                if not await asyncio.to_thread(release.wait, 30):
                    raise TimeoutError("claim test coordinator did not release worker")
                await session.commit()
        finally:
            await engine.dispose()
    asyncio.run(run())


async def test_postgres_worker_crash_lease_reclaim_and_duplicate_prevention():
    url = os.environ.get("DATABASE_URL", "")
    assert url.startswith("postgresql+asyncpg://"), (
        "DATABASE_URL must target a disposable PostgreSQL database migrated to head"
    )
    engine = create_async_engine(url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    tenant_id = uuid.uuid4()
    job_id = uuid.uuid4()
    organization_id = None
    ctx = multiprocessing.get_context("spawn")
    processes = []
    releases = []
    try:
        async with maker() as session:
            tenant = Tenant(id=tenant_id, name="Recovery test", twilio_number=f"+{tenant_id.int % 10**14:014d}")
            session.add(tenant)
            await session.flush()
            organization_id = tenant.organization_id
            session.add(DurableJob(
                id=job_id, tenant_id=tenant_id, job_type="automation",
                idempotency_key=uuid.uuid4().hex, payload={},
                available_at=datetime.now(timezone.utc) - timedelta(seconds=1),
                created_at=datetime.now(timezone.utc),
            ))
            await session.commit()

        # Keep worker one's transaction open. Worker two must skip its locked
        # row instead of blocking or claiming the same job.
        for _ in range(2):
            ready, release, output = ctx.Event(), ctx.Event(), ctx.Queue()
            process = ctx.Process(target=_claim_worker, args=(url, str(tenant_id), ready, release, output))
            processes.append(process)
            releases.append(release)
            process.start()
            assert await asyncio.to_thread(ready.wait, 30), "worker failed to claim within timeout"
            claimed = await asyncio.to_thread(output.get, True, 5)
            if len(processes) == 1:
                assert claimed == str(job_id)
            else:
                assert claimed is None

        for release in releases:
            release.set()
        for process in processes:
            await asyncio.to_thread(process.join, 30)
            assert process.exitcode == 0

        # The process has exited but its committed lease survives. It is not
        # reclaimable until expiry; recovery then closes the first attempt.
        async with maker() as session:
            assert await claim_next_job(session, worker_id="too-early", tenant_id=tenant_id) is None
            job = await session.get(DurableJob, job_id)
            moment = job.leased_until + timedelta(seconds=1)
            result = await recover_abandoned(session, now=moment)
            assert result["requeued"] >= 1
            await session.commit()
        async with maker() as session:
            recovered = await claim_next_job(session, worker_id="replacement", tenant_id=tenant_id, now=moment)
            assert recovered.id == job_id
            assert recovered.attempt_count == 2
            await session.commit()
        async with maker() as session:
            attempts = (await session.execute(select(JobAttempt).where(JobAttempt.job_id == job_id).order_by(JobAttempt.attempt_number))).scalars().all()
            assert [attempt.status for attempt in attempts] == ["expired", "started"]
            assert await claim_next_job(session, worker_id="duplicate", tenant_id=tenant_id, now=moment) is None
    finally:
        for release in releases:
            release.set()
        for process in processes:
            await asyncio.to_thread(process.join, 5)
            if process.is_alive():
                process.terminate()
                await asyncio.to_thread(process.join, 5)
        async with maker() as session:
            await session.execute(delete(Tenant).where(Tenant.id == tenant_id))
            if organization_id:
                await session.execute(delete(Organization).where(Organization.id == organization_id))
            await session.commit()
        await engine.dispose()
````

### File 15: `tests/test_enterprise_batch01.py`

SHA-256: `a674152a052b56bd5d2826f83613c406fd1b0a654184d312e37eaf2086a38fa9`

````
"""Batch 01 enterprise expansion — full test suite.

Covers the eight domain layers, the eight services, the three new API route
files, and the cross-cutting security guarantees. Deterministic and offline:
no external provider is ever contacted. Where a feature has no table yet, the
test asserts the *documented* behaviour (ephemeral overlay + tenant isolation)
rather than pretending durability.

The API routes are not registered in ``app/main.py`` (that file is outside the
allowed set for this batch), so the API tests build a small FastAPI app that
includes the three new routers and overrides the session dependency — exactly
the wiring ``app/main.py`` will perform once the integration dependency is
resolved.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.auth.jwt import create_access_token
from app.core.errors import BadRequestError, NotFoundError
from app.db.models import CallStatus, LeadStatus, Speaker, UsageEvent, UsageEventType, UsageMetric
from app.db.session import get_session
from app.domain.agent_models import (
    AgentConfig,
    HandoffConfig,
    HandoffMode,
    LanguageConfig,
    ModelConfig,
    OperatingHours,
    SafetyPolicy,
    VoiceConfig,
    can_transition as agent_can_transition,
)
from app.domain.automation_models import (
    AutomationDefinition,
    AutomationSchedule,
    AutomationStatus,
    ExecutionPolicy,
    FilterRule,
    ScheduleKind,
    TriggerEvent,
    run_idempotency_key,
)
from app.domain.campaign_models import (
    Audience,
    CampaignChannel,
    CampaignDefinition,
    CampaignGoal,
    CampaignSchedule,
    CampaignState,
    ComplianceGate,
    Throttle,
)
from app.domain.conversation_models import (
    ConversationState,
    EscalationState,
    Sentiment,
    can_transition as conversation_can_transition,
)
from app.domain.inbox_models import (
    InboxChannel,
    MessageDirection,
    Thread,
    ThreadStatus,
    reopen_allowed,
)
from app.domain.notification_models import (
    DeliveryState,
    EventSource,
    NotificationChannel,
    NotificationTemplate,
    PreferenceSet,
    Recipient,
)
from app.domain.workflow_models import (
    CONDITION_OPERATORS,
    CONTROLLED_ACTIONS,
    Condition,
    ExecutionStatus,
    NodeType,
    WorkflowAction,
    WorkflowDefinition,
    WorkflowNode,
    evaluate_condition,
)
from app.services import (
    agent_service,
    analytics_service,
    automation_service,
    campaign_service,
    conversation_service,
    inbox_service,
    notification_service,
    workflow_service,
)
from tests.conftest import make_call, make_lead


# ============================================================ test fixtures ===


@pytest_asyncio.fixture
async def enterprise_app(sessionmaker_):
    """The three new routers wired into a test app with a real session override."""
    from app.api.agent_management_routes import router as agents_router
    from app.api.campaign_routes import router as campaigns_router
    from app.api.workflow_routes import router as workflows_router
    from app.core.errors import install_error_handling

    app = FastAPI()
    app.include_router(agents_router)
    app.include_router(workflows_router)
    app.include_router(campaigns_router)
    install_error_handling(app)

    async def _override():
        async with sessionmaker_() as session:
            yield session

    app.dependency_overrides[get_session] = _override
    yield app
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def enterprise_client(enterprise_app):
    async with AsyncClient(
        transport=ASGITransport(app=enterprise_app), base_url="http://test"
    ) as ac:
        yield ac


def _auth(user) -> dict:
    token, _ = create_access_token(
        user_id=user.id,
        tenant_id=user.tenant_id,
        role=user.role.value,
        token_version=user.token_version,
    )
    return {"Authorization": f"Bearer {token}"}


def _agent(name="Alex", tenant_id="t"):
    return AgentConfig(
        tenant_id=tenant_id,
        name=name,
        greeting="Thanks for calling.",
        language=LanguageConfig(primary="en-US"),
        model=ModelConfig(provider="anthropic", model="claude-haiku-4-5"),
    )


def _terminal_workflow(tenant_id, name="wf"):
    return WorkflowDefinition(
        id=f"wf-{tenant_id}-{name}",
        tenant_id=tenant_id,
        name=name,
        entry_node="end",
        nodes=(WorkflowNode(id="end", type=NodeType.TERMINAL),),
    )


def _campaign(tenant_id, name="Spring outreach"):
    return CampaignDefinition(
        id=f"campaign-{tenant_id}-{name}",
        tenant_id=tenant_id,
        name=name,
        goal=CampaignGoal.QUALIFY,
        channel=CampaignChannel.VOICE,
        audience=Audience(tenant_id=tenant_id),
        schedule=CampaignSchedule(daily_start=time(9, 0), daily_end=time(20, 0)),
        throttle=Throttle(),
        compliance=ComplianceGate(),
        state=CampaignState.DRAFT,
    )


def _bounded_range(days_back: int = 30):
    """A now-anchored, 400-day-safe range that brackets ``make_call``'s clock."""
    now = datetime.utcnow()
    return now - timedelta(days=days_back), now + timedelta(days=1)


def _open_window(tenant) -> None:
    """Open the tenant's outbound window for the whole day (tests only)."""
    tenant.outbound_window_open = time(0, 0)
    tenant.outbound_window_close = time(23, 59, 59)


# ==================================================================== AGENTS ===


class TestAgentDomain:
    def test_validation_rejects_unknown_provider(self):
        config = AgentConfig(tenant_id="t", name="A", model=ModelConfig(provider="mystery"))
        assert any("provider" in p for p in config.validate())

    def test_validation_rejects_out_of_range_speed(self):
        config = AgentConfig(tenant_id="t", name="A", voice=VoiceConfig(speech_speed=9.0))
        assert any("speech_speed" in p for p in config.validate())

    def test_id_stable_across_content_edits(self):
        a = _agent("Alex")
        b = AgentConfig(
            tenant_id="t",
            name="Alex",
            greeting="Different greeting",
            language=LanguageConfig(primary="en-US"),
            model=ModelConfig(provider="anthropic"),
        )
        assert a.id == b.id  # identity is (tenant, name), not content

    def test_publish_transition_rules(self):
        from app.domain.agent_models import AgentStatus

        assert agent_can_transition(AgentStatus.DRAFT, AgentStatus.PUBLISHED)
        assert agent_can_transition(AgentStatus.PUBLISHED, AgentStatus.PUBLISHED)
        assert agent_can_transition(AgentStatus.PUBLISHED, AgentStatus.RETIRED)
        assert not agent_can_transition(AgentStatus.RETIRED, AgentStatus.PUBLISHED)


class TestAgentService:
    async def test_draft_tenant_isolation(self, db, tenant_a, tenant_b):
        config_a = _agent("Alex", str(tenant_a.id))
        await agent_service.create_draft_async(db, tenant_a, config_a)
        assert await agent_service.list_agents_async(db, tenant_a, ensure_default=False)
        assert await agent_service.list_agents_async(db, tenant_b, ensure_default=False) == []

    async def test_publish_mints_immutable_version(self, db, tenant_a):
        config = _agent("Alex", str(tenant_a.id))
        await agent_service.create_draft_async(db, tenant_a, config)
        v1 = await agent_service.publish_async(db, tenant_a, config)
        v2 = await agent_service.publish_async(db, tenant_a, config, changelog="bump")
        assert v1.version == 1 and v2.version == 2
        history = await agent_service.version_history_async(db, tenant_a, config.id)
        assert history[0].version == 2
        assert history[0].config_hash == history[1].config_hash

    async def test_publish_persists_tenant_columns(self, db, tenant_a):
        config = AgentConfig(
            tenant_id=str(tenant_a.id),
            name="Rex",
            greeting="Hello there",
            language=LanguageConfig(primary="fr-FR"),
            model=ModelConfig(provider="google", model="gemini-2.0-flash", temperature=0.5),
            voice=VoiceConfig(voice_id="v1", speech_speed=1.1),
            operating_hours=OperatingHours(timezone="Europe/Paris"),
            handoff=HandoffConfig(mode=HandoffMode.NUMBER, destination="+15550001111"),
            safety=SafetyPolicy(record_calls=True),
        )
        await agent_service.create_draft_async(db, tenant_a, config)
        await agent_service.publish_async(db, tenant_a, config)
        assert tenant_a.agent_name == "Rex"
        assert tenant_a.language == "fr-FR"
        assert tenant_a.llm_provider == "google"
        assert tenant_a.escalation_number == "+15550001111"

    async def test_rollback_reapplies_history(self, db, tenant_a):
        v1 = _agent("Alex", str(tenant_a.id))
        await agent_service.create_draft_async(db, tenant_a, v1)
        await agent_service.publish_async(db, tenant_a, v1)
        v2 = AgentConfig(
            tenant_id=str(tenant_a.id),
            name="Alex",
            greeting="New greeting",
            language=LanguageConfig(primary="en-US"),
            model=ModelConfig(provider="anthropic"),
        )
        await agent_service.update_draft_async(db, tenant_a, v2)
        await agent_service.publish_async(db, tenant_a, v2)
        assert tenant_a.greeting == "New greeting"
        rolled = await agent_service.rollback_async(db, tenant_a, v1.id, 1)
        assert rolled.version == 3
        assert tenant_a.greeting == "Thanks for calling."

    async def test_configure_tools_validation(self, db, tenant_a):
        config = _agent("Alex", str(tenant_a.id))
        await agent_service.create_draft_async(db, tenant_a, config)
        with pytest.raises(ValueError):
            await agent_service.configure_tools_async(
                db, tenant_a, config.id, enabled=("not_a_tool",)
            )

    async def test_test_configuration_offline(self, tenant_a):
        config = _agent("Alex", str(tenant_a.id))
        result = agent_service.test_configuration(tenant_a, config)
        assert result["ok"] is True
        assert isinstance(result["checks"], dict)


# ============================================================== CONVERSATIONS ===


class TestConversationDomain:
    def test_transition_table(self):
        assert conversation_can_transition(ConversationState.ACTIVE, ConversationState.COMPLETED)
        assert conversation_can_transition(ConversationState.COMPLETED, ConversationState.ACTIVE)
        assert not conversation_can_transition(ConversationState.FAILED, ConversationState.ACTIVE)

    def test_invalid_transition_raises(self):
        from app.domain.conversation_models import Conversation, ConversationChannel

        conversation = Conversation(
            id="c1",
            tenant_id="t",
            channel=ConversationChannel.VOICE,
            state=ConversationState.FAILED,
        )
        with pytest.raises(ValueError):
            conversation.transition(ConversationState.COMPLETED)


class TestConversationService:
    async def test_start_and_project(self, db, tenant_a):
        call = await conversation_service.start_conversation(
            db,
            tenant_a,
            channel=conversation_service.ConversationChannel.VOICE,
            from_number="+15551234567",
            intent="booking",
        )
        view = conversation_service.project(tenant_a, call)
        assert view.intent == "booking"
        assert view.state is ConversationState.ACTIVE

    async def test_append_turn_and_classify(self, db, tenant_a):
        call = await conversation_service.start_conversation(
            db,
            tenant_a,
            channel=conversation_service.ConversationChannel.VOICE,
            from_number="+15551234567",
            intent="booking",
        )
        await conversation_service.append_turn(
            db, tenant_a, call, speaker=Speaker.USER, text="I need a cleaning"
        )
        await conversation_service.classify(
            db, tenant_a, call, intent="booking", topic="cleaning", sentiment=Sentiment.NEUTRAL
        )
        view = conversation_service.project(tenant_a, call)
        assert view.intent == "booking"
        assert view.sentiment is Sentiment.NEUTRAL

    async def test_escalate_records_intent_without_dialing(self, db, tenant_a):
        call = await conversation_service.start_conversation(
            db,
            tenant_a,
            channel=conversation_service.ConversationChannel.VOICE,
            from_number="+15551234567",
            intent="booking",
        )
        # ``escalate`` only records the intent; the transfer service owns the
        # provider dial, so no outbound call can be triggered from this module.
        view = await conversation_service.escalate(
            db, tenant_a, call, destination="+15550002222", reason="caller upset"
        )
        assert call.escalated is True
        assert call.transfer_reason == "caller upset"
        assert call.transfer_destination == "+15550002222"
        assert view.escalation is EscalationState.REQUESTED

    async def test_close_and_reopen_policy(self, db, tenant_a):
        call = await conversation_service.start_conversation(
            db,
            tenant_a,
            channel=conversation_service.ConversationChannel.VOICE,
            from_number="+15551234567",
            intent="booking",
        )
        await conversation_service.close(db, tenant_a, call)
        assert call.status is CallStatus.COMPLETED
        reopened = await conversation_service.reopen(db, tenant_a, call)
        assert reopened.state is ConversationState.ACTIVE

    async def test_reopen_outside_window_rejected(self, db, tenant_a):
        call = await conversation_service.start_conversation(
            db,
            tenant_a,
            channel=conversation_service.ConversationChannel.VOICE,
            from_number="+15551234567",
            intent="booking",
        )
        await conversation_service.close(db, tenant_a, call)
        call.ended_at = datetime.now(timezone.utc) - timedelta(days=3)
        await db.commit()
        with pytest.raises(BadRequestError):
            await conversation_service.reopen(db, tenant_a, call)

    async def test_summarize_persists(self, db, tenant_a):
        call = await conversation_service.start_conversation(
            db,
            tenant_a,
            channel=conversation_service.ConversationChannel.VOICE,
            from_number="+15551234567",
            intent="booking",
        )
        await conversation_service.summarize(
            db, tenant_a, call, short="Booked Tuesday", topics=("cleaning",)
        )
        assert call.summary == "Booked Tuesday"

    async def test_search_tenant_scoped(self, db, tenant_a, tenant_b):
        await conversation_service.start_conversation(
            db,
            tenant_a,
            channel=conversation_service.ConversationChannel.VOICE,
            from_number="+15551234567",
            intent="booking",
        )
        await conversation_service.start_conversation(
            db,
            tenant_b,
            channel=conversation_service.ConversationChannel.VOICE,
            from_number="+15559998888",
            intent="booking",
        )
        mine = await conversation_service.search(db, tenant_a, intent="booking")
        assert all(str(c.tenant_id) == str(tenant_a.id) for c in mine)
        assert len(mine) == 1


# ================================================================== WORKFLOWS ===


class TestWorkflowDomain:
    def test_controlled_actions_are_bounded(self):
        assert "update_lead_status" in CONTROLLED_ACTIONS
        for bad in ("eval", "exec", "import", "os.system"):
            assert bad not in CONTROLLED_ACTIONS
        for op in ("eq", "in", "contains", "gt"):
            assert op in CONDITION_OPERATORS

    def test_evaluate_condition_operators(self):
        assert evaluate_condition(5, "gt", 3) is True
        assert evaluate_condition("hello", "contains", "ell") is True
        assert evaluate_condition(None, "exists", True) is False
        assert evaluate_condition(3, "in", [1, 2, 3]) is True

    def test_validation_rejects_unknown_action(self):
        node = WorkflowNode(
            id="a", type=NodeType.ACTION, action=WorkflowAction(name="rm -rf /"), next="end"
        )
        definition = WorkflowDefinition(
            id="w",
            tenant_id="t",
            name="bad",
            entry_node="a",
            nodes=(node, WorkflowNode(id="end", type=NodeType.TERMINAL)),
        )
        assert any("not a controlled action" in p for p in definition.validate())

    def test_validation_rejects_code_markers(self):
        node = WorkflowNode(
            id="a", type=NodeType.ACTION, action=WorkflowAction(name="__import__"), next="end"
        )
        definition = WorkflowDefinition(
            id="w",
            tenant_id="t",
            name="bad",
            entry_node="a",
            nodes=(node, WorkflowNode(id="end", type=NodeType.TERMINAL)),
        )
        assert any("forbidden" in p for p in definition.validate())

    def test_validation_requires_terminal(self):
        node = WorkflowNode(id="a", type=NodeType.ACTION, action=WorkflowAction("mark_resolved"))
        definition = WorkflowDefinition(
            id="w", tenant_id="t", name="bad", entry_node="a", nodes=(node,)
        )
        assert any("terminal" in p for p in definition.validate())


class TestWorkflowService:
    async def test_publish_required_before_execute(self, tenant_a, db):
        definition = _terminal_workflow(str(tenant_a.id))
        await workflow_service.create_workflow(str(tenant_a.id), definition, session=db)
        with pytest.raises(BadRequestError):
            await workflow_service.execute_workflow(str(tenant_a.id), definition.id, {}, session=db)

    async def test_execution_deterministic_and_idempotent(self, tenant_a, db):
        definition = _terminal_workflow(str(tenant_a.id))
        await workflow_service.create_workflow(str(tenant_a.id), definition, session=db)
        await workflow_service.publish_workflow(str(tenant_a.id), definition.id, session=db)
        first = await workflow_service.execute_workflow(
            str(tenant_a.id), definition.id, {"x": 1}, session=db
        )
        second = await workflow_service.execute_workflow(
            str(tenant_a.id), definition.id, {"x": 1}, session=db
        )
        assert first.id == second.id  # idempotent replay
        assert first.status is ExecutionStatus.COMPLETED

    async def test_condition_branching(self, tenant_a, db):
        nodes = (
            WorkflowNode(
                id="gate",
                type=NodeType.CONDITION,
                condition=Condition(field="ok", operator="eq", value=True),
                next="end",
            ),
            WorkflowNode(id="end", type=NodeType.TERMINAL),
        )
        definition = WorkflowDefinition(
            id="wf-cond", tenant_id=str(tenant_a.id), name="cond", entry_node="gate", nodes=nodes
        )
        await workflow_service.create_workflow(str(tenant_a.id), definition, session=db)
        await workflow_service.publish_workflow(str(tenant_a.id), definition.id, session=db)
        ok = await workflow_service.execute_workflow(
            str(tenant_a.id), definition.id, {"ok": True}, session=db
        )
        bad = await workflow_service.execute_workflow(
            str(tenant_a.id), definition.id, {"ok": False}, session=db
        )
        assert ok.status is ExecutionStatus.COMPLETED
        assert bad.status is ExecutionStatus.FAILED

    async def test_approval_gate_waits_then_cancel(self, tenant_a, db):
        nodes = (
            WorkflowNode(id="gate", type=NodeType.APPROVAL, approver_role="manager", next="end"),
            WorkflowNode(id="end", type=NodeType.TERMINAL),
        )
        definition = WorkflowDefinition(
            id="wf-appr", tenant_id=str(tenant_a.id), name="appr", entry_node="gate", nodes=nodes
        )
        await workflow_service.create_workflow(str(tenant_a.id), definition, session=db)
        await workflow_service.publish_workflow(str(tenant_a.id), definition.id, session=db)
        execution = await workflow_service.execute_workflow(
            str(tenant_a.id), definition.id, {}, session=db
        )
        assert execution.status is ExecutionStatus.WAITING_APPROVAL
        cancelled = await workflow_service.cancel_execution(
            str(tenant_a.id), execution.id, session=db
        )
        assert cancelled.status is ExecutionStatus.CANCELLED

    async def test_retry_after_failure(self, tenant_a, db):
        nodes = (
            WorkflowNode(
                id="gate",
                type=NodeType.CONDITION,
                condition=Condition(field="ok", operator="eq", value=True),
                next="end",
            ),
            WorkflowNode(id="end", type=NodeType.TERMINAL),
        )
        definition = WorkflowDefinition(
            id="wf-retry", tenant_id=str(tenant_a.id), name="retry", entry_node="gate", nodes=nodes
        )
        await workflow_service.create_workflow(str(tenant_a.id), definition, session=db)
        await workflow_service.publish_workflow(str(tenant_a.id), definition.id, session=db)
        failed = await workflow_service.execute_workflow(
            str(tenant_a.id), definition.id, {"ok": False}, session=db
        )
        assert failed.status is ExecutionStatus.FAILED
        retried = await workflow_service.retry_execution(
            str(tenant_a.id), failed.id, {"ok": True}, session=db
        )
        assert retried.status is ExecutionStatus.COMPLETED

    async def test_tenant_isolation(self, tenant_a, tenant_b, db):
        definition = _terminal_workflow(str(tenant_a.id))
        await workflow_service.create_workflow(str(tenant_a.id), definition, session=db)
        with pytest.raises(NotFoundError):
            await workflow_service.get_workflow(str(tenant_b.id), definition.id, session=db)

    async def test_lead_status_action_persists(self, db, tenant_a):
        lead = await make_lead(db, tenant_a)
        nodes = (
            WorkflowNode(
                id="act",
                type=NodeType.ACTION,
                action=WorkflowAction("update_lead_status", {"status": "qualified"}),
                next="end",
            ),
            WorkflowNode(id="end", type=NodeType.TERMINAL),
        )
        definition = WorkflowDefinition(
            id="wf-lead", tenant_id=str(tenant_a.id), name="lead", entry_node="act", nodes=nodes
        )
        await workflow_service.create_workflow(str(tenant_a.id), definition, session=db)
        await workflow_service.publish_workflow(str(tenant_a.id), definition.id, session=db)
        await workflow_service.execute_workflow(
            str(tenant_a.id), definition.id, {"lead_id": str(lead.id)}, session=db
        )
        await db.refresh(lead)
        assert lead.status is LeadStatus.QUALIFIED


# ================================================================ AUTOMATIONS ===


class TestAutomationService:
    def _automation(self, tenant_id, *, filters=(), cooldown=0):
        return AutomationDefinition(
            id=f"auto-{tenant_id}",
            tenant_id=tenant_id,
            name="Negative sentiment alert",
            event=TriggerEvent.CALL_COMPLETED,
            filters=tuple(filters),
            actions=(WorkflowAction("record_escalation_intent", {"destination": "+15550009999"}),),
            schedule=AutomationSchedule(kind=ScheduleKind.ON_EVENT),
            policy=ExecutionPolicy(cooldown_seconds=cooldown, max_per_event=1),
            status=AutomationStatus.ENABLED,
        )

    async def test_filter_matching(self, tenant_a):
        automation = self._automation(
            str(tenant_a.id), filters=(FilterRule("sentiment", "eq", "negative"),)
        )
        automation_service.register_automation(str(tenant_a.id), automation)
        hits = automation_service.evaluate(
            str(tenant_a.id), TriggerEvent.CALL_COMPLETED, {"sentiment": "negative"}
        )
        assert len(hits) == 1
        misses = automation_service.evaluate(
            str(tenant_a.id), TriggerEvent.CALL_COMPLETED, {"sentiment": "positive"}
        )
        assert misses == []

    async def test_disabled_not_evaluated(self, tenant_a):
        automation = self._automation(str(tenant_a.id))
        automation_service.register_automation(str(tenant_a.id), automation)
        automation_service.set_enabled(str(tenant_a.id), automation.id, False)
        assert (
            automation_service.evaluate(
                str(tenant_a.id), TriggerEvent.CALL_COMPLETED, {"sentiment": "negative"}
            )
            == []
        )

    async def test_deduplication_idempotency(self, tenant_a):
        automation = self._automation(str(tenant_a.id))
        automation_service.register_automation(str(tenant_a.id), automation)
        event_id = "call-123"
        first = await automation_service.execute_automation(
            str(tenant_a.id), automation, event_id, {"sentiment": "negative"}
        )
        assert first.status == "completed"
        second = await automation_service.execute_automation(
            str(tenant_a.id), automation, event_id, {"sentiment": "negative"}
        )
        assert second.status == "cancelled"  # deduplicated, not double-executed
        assert second.last_error == "max_per_event budget exhausted"

    async def test_cooldown_suppresses(self, tenant_a):
        automation = self._automation(str(tenant_a.id), cooldown=3600)
        automation_service.register_automation(str(tenant_a.id), automation)
        first = await automation_service.execute_automation(
            str(tenant_a.id), automation, "call-1", {"sentiment": "negative"}
        )
        assert first.status == "completed"
        ok, reason = automation_service.should_run(str(tenant_a.id), automation, "call-2")
        assert ok is False and reason == "cooldown active"

    async def test_run_idempotency_key_deterministic(self):
        k1 = run_idempotency_key("t", "a", TriggerEvent.CALL_COMPLETED, "evt-1")
        k2 = run_idempotency_key("t", "a", TriggerEvent.CALL_COMPLETED, "evt-1")
        k3 = run_idempotency_key("t", "a", TriggerEvent.CALL_COMPLETED, "evt-2")
        assert k1 == k2 and k1 != k3


# ================================================================== CAMPAIGNS ===


class TestCampaignDomain:
    def test_state_transitions(self):
        assert campaign_service._can_transition(CampaignState.DRAFT, CampaignState.SCHEDULED)
        assert campaign_service._can_transition(CampaignState.SCHEDULED, CampaignState.RUNNING)
        assert campaign_service._can_transition(CampaignState.RUNNING, CampaignState.PAUSED)
        assert not campaign_service._can_transition(CampaignState.CANCELLED, CampaignState.RUNNING)

    def test_compliance_gate_cannot_be_weakened(self):
        weakened = ComplianceGate(require_dnc_check=False)
        assert any("may not be disabled" in p for p in weakened.validate())
        strict = ComplianceGate()
        assert strict.validate() == []
        assert strict.require_dnc_check is True and strict.require_call_window is True


class TestCampaignService:
    async def test_create_and_state_lifecycle(self, db, tenant_a):
        definition = _campaign(str(tenant_a.id))
        row = await campaign_service.create_campaign(db, tenant_a, definition)
        assert row.is_active is False
        await campaign_service.schedule_campaign(db, tenant_a, str(row.id))
        _open_window(tenant_a)
        await campaign_service.resume_campaign(db, tenant_a, str(row.id))
        current = await campaign_service.get_campaign(db, tenant_a, str(row.id))
        assert current.state is CampaignState.RUNNING
        await campaign_service.pause_campaign(db, tenant_a, str(row.id))
        current = await campaign_service.get_campaign(db, tenant_a, str(row.id))
        assert current.state is CampaignState.PAUSED

    async def test_dnc_lead_skipped(self, db, tenant_a):
        lead = await make_lead(db, tenant_a, status=LeadStatus.DNC)
        definition = _campaign(str(tenant_a.id))
        definition = CampaignDefinition(
            **{
                **definition.__dict__,
                "audience": Audience(tenant_id=str(tenant_a.id), lead_ids=(str(lead.id),)),
            }
        )
        ok, reason = await campaign_service.eligibility_check(db, tenant_a, lead, definition)
        assert ok is False and reason == "lead is on do-not-call"

    async def test_window_check_respects_tenant(self, db, tenant_a):
        lead = await make_lead(db, tenant_a)
        tenant_a.outbound_window_open = time(1, 0)
        tenant_a.outbound_window_close = time(2, 0)
        await db.commit()
        definition = _campaign(str(tenant_a.id))
        ok, reason = await campaign_service.eligibility_check(db, tenant_a, lead, definition)
        assert ok is False and reason == "outside the outbound call window"

    async def test_attempt_limit(self, db, tenant_a):
        lead = await make_lead(db, tenant_a, attempts=3)
        definition = _campaign(str(tenant_a.id))
        definition = CampaignDefinition(
            **{**definition.__dict__, "throttle": Throttle(max_attempts_per_lead=3)}
        )
        _open_window(tenant_a)
        ok, reason = await campaign_service.eligibility_check(db, tenant_a, lead, definition)
        assert ok is False and reason == "attempt limit reached"

    async def test_execution_plan_never_dials(self, db, tenant_a):
        lead = await make_lead(db, tenant_a)
        definition = _campaign(str(tenant_a.id))
        definition = CampaignDefinition(
            **{
                **definition.__dict__,
                "audience": Audience(tenant_id=str(tenant_a.id), lead_ids=(str(lead.id),)),
            }
        )
        row = await campaign_service.create_campaign(db, tenant_a, definition)
        fetched = await campaign_service.get_campaign(db, tenant_a, str(row.id))
        _open_window(tenant_a)
        # ``execution_plan`` only builds safe intents — it has no dial path at
        # all, so nothing can ever be dialled from here.
        intents = await campaign_service.execution_plan(db, tenant_a, fetched)
        assert len(intents) == 1 and not intents[0].skipped
        assert intents[0].lead_id == str(lead.id)
        assert intents[0].tenant_id == str(tenant_a.id)

    async def test_progress_counts_leads(self, db, tenant_a):
        row = await campaign_service.create_campaign(db, tenant_a, _campaign(str(tenant_a.id)))
        definition = await campaign_service.get_campaign(db, tenant_a, str(row.id))
        await make_lead(db, tenant_a, campaign_id=row.id, status=LeadStatus.QUALIFIED)
        await make_lead(db, tenant_a, campaign_id=row.id, status=LeadStatus.DNC)
        metrics = await campaign_service.progress(db, tenant_a, definition)
        assert metrics.total_leads == 2
        assert metrics.conversions == 1
        assert metrics.dnc_skipped == 1


# ================================================================= ANALYTICS ===


class TestAnalyticsService:
    async def test_aggregate_correctness(self, db, tenant_a):
        start, end = _bounded_range()
        await make_call(db, tenant_a, status=CallStatus.COMPLETED, duration_seconds=120)
        await make_call(db, tenant_a, status=CallStatus.COMPLETED, duration_seconds=60)
        await make_call(db, tenant_a, status=CallStatus.FAILED)
        kpi = await analytics_service.call_kpis(db, str(tenant_a.id), start=start, end=end)
        assert kpi.total == 3 and kpi.answered == 2 and kpi.failed == 1
        assert kpi.rates()["answer_rate"] == 66.7

    async def test_tenant_isolation(self, db, tenant_a, tenant_b):
        start, end = _bounded_range()
        await make_call(db, tenant_a, status=CallStatus.COMPLETED)
        kpi_a = await analytics_service.call_kpis(db, str(tenant_a.id), start=start, end=end)
        kpi_b = await analytics_service.call_kpis(db, str(tenant_b.id), start=start, end=end)
        assert kpi_a.total == 1 and kpi_b.total == 0

    async def test_range_validation(self, db, tenant_a):
        with pytest.raises(BadRequestError):
            await analytics_service.call_kpis(
                db, str(tenant_a.id), start=datetime(2025, 1, 2), end=datetime(2025, 1, 1)
            )

    async def test_empty_results(self, db, tenant_a):
        start, end = _bounded_range(days_back=5)
        snapshot = await analytics_service.snapshot(
            db, str(tenant_a.id), kind=analytics_service.KpiKind.CALL, start=start, end=end
        )
        assert snapshot.points[0].metrics["total"] == 0

    async def test_snapshot_has_no_pii(self, db, tenant_a):
        start, end = _bounded_range()
        await make_call(db, tenant_a)
        snapshot = await analytics_service.snapshot(
            db, str(tenant_a.id), kind=analytics_service.KpiKind.CALL, start=start, end=end
        )
        assert snapshot.assert_no_pii() == []

    async def test_cost_estimate_from_usage(self, db, tenant_a, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(
            settings.__class__,
            "cost_unit_prices",
            property(lambda self: {"voice_minute": 1300, "sms_segment": 790}),
        )
        event = UsageEvent(
            tenant_id=tenant_a.id,
            billing_period="2026-09",
            metric=UsageMetric.VOICE_MINUTE,
            event_type=UsageEventType.VOICE_MINUTE_USED,
            quantity=120,
            unit="seconds",
            idempotency_key="usage-test-1",
            created_at=datetime(2026, 9, 15),
            event_metadata={},
        )
        db.add(event)
        await db.commit()
        cost = await analytics_service.cost_kpis(
            db, str(tenant_a.id), start=datetime(2026, 9, 1), end=datetime(2026, 10, 1)
        )
        assert cost.minutes == 2.0
        assert cost.estimated_cost_millicents == 2600


# ============================================================== NOTIFICATIONS ===


class TestNotificationService:
    def _template(self, tenant_id="t"):
        return NotificationTemplate(
            id="tmpl-1",
            tenant_id=tenant_id,
            name="Appointment reminder",
            channel=NotificationChannel.SMS,
            body="Hi {customer_name}, see you at {appointment_time}.",
            variables=("customer_name", "appointment_time"),
        )

    def test_strict_rendering(self):
        template = self._template()
        body = template.render({"customer_name": "Jane", "appointment_time": "2pm"})
        assert body == "Hi Jane, see you at 2pm."
        body_unknown = template.render({"customer_name": "Jane"})
        assert "{appointment_time}" in body_unknown  # never swallowed, never evaluated

    def test_undeclared_variable_flagged(self):
        template = NotificationTemplate(
            id="t",
            tenant_id="t",
            name="x",
            channel=NotificationChannel.IN_APP,
            body="Hello {injected}",
            variables=(),
        )
        assert any("undeclared" in p for p in template.validate())

    def test_preference_quiet_hours(self):
        pref = PreferenceSet(
            channel=NotificationChannel.SMS, quiet_start=time(22, 0), quiet_end=time(8, 0)
        )
        moment = datetime(2026, 9, 14, 23, 0)
        ok, reason = notification_service.resolve_preferences(
            pref, NotificationChannel.SMS, now=moment
        )
        assert ok is False and reason == "recipient is in quiet hours"

    async def test_deduplication(self, tenant_a):
        template = self._template(str(tenant_a.id))
        notification_service.create_template(str(tenant_a.id), template)
        recipient = Recipient(kind="phone", target="+15550001111")
        n1 = notification_service.create_notification(
            str(tenant_a.id),
            template=template,
            recipient=recipient,
            event_source=EventSource.APPOINTMENT_REMINDER,
            business_key="appt-1",
        )
        n2 = notification_service.create_notification(
            str(tenant_a.id),
            template=template,
            recipient=recipient,
            event_source=EventSource.APPOINTMENT_REMINDER,
            business_key="appt-1",
        )
        assert n1.id == n2.id  # same business fact, one notification

    async def test_retry_backoff_advances(self, tenant_a):
        template = self._template(str(tenant_a.id))
        notification_service.create_template(str(tenant_a.id), template)
        notification = notification_service.create_notification(
            str(tenant_a.id),
            template=template,
            recipient=Recipient(kind="user", target="u1"),
            event_source=EventSource.SYSTEM,
            business_key="b1",
        )
        retried = notification_service.retry(str(tenant_a.id), notification.id)
        assert retried.attempts == 1
        assert retried.delivery_state is DeliveryState.RETRYING

    async def test_in_app_delivery(self, tenant_a):
        template = NotificationTemplate(
            id="tmpl-inapp",
            tenant_id=str(tenant_a.id),
            name="Alert",
            channel=NotificationChannel.IN_APP,
            body="You have a new message.",
            variables=(),
        )
        notification_service.create_template(str(tenant_a.id), template)
        notification = notification_service.create_notification(
            str(tenant_a.id),
            template=template,
            recipient=Recipient(kind="user", target="u1"),
            event_source=EventSource.SYSTEM,
            business_key="b2",
        )
        result = await notification_service.deliver(notification)
        assert result.delivered is True

    async def test_email_channel_suppressed_honestly(self, tenant_a):
        template = NotificationTemplate(
            id="tmpl-mail",
            tenant_id=str(tenant_a.id),
            name="Email",
            channel=NotificationChannel.EMAIL,
            body="Hello.",
            variables=(),
        )
        notification_service.create_template(str(tenant_a.id), template)
        notification = notification_service.create_notification(
            str(tenant_a.id),
            template=template,
            recipient=Recipient(kind="email", target="a@b.c"),
            event_source=EventSource.SYSTEM,
            business_key="b3",
        )
        result = await notification_service.deliver(notification)
        assert result.state is DeliveryState.SUPPRESSED


# ===================================================================== INBOX ===


class TestInboxService:
    async def test_thread_isolation(self, db, tenant_a, tenant_b):
        thread_a = await inbox_service.find_or_create_thread(
            db, tenant_a, channel=InboxChannel.WEB, customer="cust-a"
        )
        with pytest.raises(NotFoundError):
            await inbox_service.project(db, tenant_b, thread_a)

    async def test_message_ordering(self, db, tenant_a):
        thread = await inbox_service.find_or_create_thread(
            db, tenant_a, channel=InboxChannel.WEB, customer="cust-a"
        )
        m1 = await inbox_service.append_message(
            db, tenant_a, thread, direction=MessageDirection.INBOUND, body="hi"
        )
        m2 = await inbox_service.append_message(
            db, tenant_a, thread, direction=MessageDirection.OUTBOUND, body="hello"
        )
        assert m2.sequence > m1.sequence
        view = await inbox_service.project(db, tenant_a, thread)
        assert [m.sequence for m in view.messages] == sorted(m.sequence for m in view.messages)

    async def test_read_unread(self, db, tenant_a):
        thread = await inbox_service.find_or_create_thread(
            db, tenant_a, channel=InboxChannel.WEB, customer="cust-a"
        )
        await inbox_service.append_message(
            db, tenant_a, thread, direction=MessageDirection.INBOUND, body="hi"
        )
        inbox_service.mark_unread(tenant_a, thread)
        view = inbox_service.mark_read(tenant_a, thread)
        assert view.unread_count == 0

    async def test_assignment(self, db, tenant_a):
        thread = await inbox_service.find_or_create_thread(
            db, tenant_a, channel=InboxChannel.WEB, customer="cust-a"
        )
        view = inbox_service.assign(tenant_a, thread, "agent-1")
        assert view.assignee_id == "agent-1"

    async def test_escalation(self, db, tenant_a):
        thread = await inbox_service.find_or_create_thread(
            db, tenant_a, channel=InboxChannel.WEB, customer="cust-a"
        )
        await inbox_service.escalate(db, tenant_a, thread, reason="needs human")
        assert thread.escalated is True

    async def test_close_and_reopen_policy(self, db, tenant_a):
        thread = await inbox_service.find_or_create_thread(
            db, tenant_a, channel=InboxChannel.WEB, customer="cust-a"
        )
        await inbox_service.append_message(
            db, tenant_a, thread, direction=MessageDirection.INBOUND, body="hi"
        )
        await inbox_service.close(db, tenant_a, thread)
        view = await inbox_service.project(db, tenant_a, thread)
        assert view.status is ThreadStatus.CLOSED
        await inbox_service.reopen(db, tenant_a, thread)
        view = await inbox_service.project(db, tenant_a, thread)
        assert view.status is ThreadStatus.OPEN

    def test_reopen_window_rule(self):
        recent = Thread(
            id="t",
            tenant_id="x",
            channel=InboxChannel.WEB,
            status=ThreadStatus.CLOSED,
            last_message_at=datetime.now(timezone.utc).isoformat(),
        )
        assert reopen_allowed(recent) is True
        old = Thread(
            id="t2",
            tenant_id="x",
            channel=InboxChannel.WEB,
            status=ThreadStatus.CLOSED,
            last_message_at=(datetime.now(timezone.utc) - timedelta(days=30)).isoformat(),
        )
        assert reopen_allowed(old) is False


# ======================================================================= API ===


class TestAgentApi:
    async def test_owner_can_create_and_list(self, enterprise_client, owner_a):
        response = await enterprise_client.post(
            "/api/agents",
            json={"name": "Alex", "greeting": "Thanks for calling."},
            headers=_auth(owner_a),
        )
        assert response.status_code == 201
        body = response.json()
        assert body["name"] == "Alex"
        listing = await enterprise_client.get("/api/agents", headers=_auth(owner_a))
        assert listing.status_code == 200 and any(a["name"] == "Alex" for a in listing.json())

    async def test_viewer_forbidden(self, enterprise_client, viewer_a):
        response = await enterprise_client.post(
            "/api/agents", json={"name": "Alex"}, headers=_auth(viewer_a)
        )
        assert response.status_code == 403

    async def test_mass_assignment_protected(self, enterprise_client, owner_a):
        response = await enterprise_client.post(
            "/api/agents",
            json={"name": "Alex", "admin": True, "api_key": "sk-live-secret"},
            headers=_auth(owner_a),
        )
        assert response.status_code == 422  # extra fields rejected

    async def test_no_secret_leakage(self, enterprise_client, owner_a):
        await enterprise_client.post("/api/agents", json={"name": "Alex"}, headers=_auth(owner_a))
        listing = await enterprise_client.get("/api/agents", headers=_auth(owner_a))
        serialized = str(listing.json())
        assert "sk-live" not in serialized
        assert "api_key" not in listing.json()[0]

    async def test_publish_flow(self, enterprise_client, owner_a):
        created = await enterprise_client.post(
            "/api/agents", json={"name": "Rex", "greeting": "Hello"}, headers=_auth(owner_a)
        )
        agent_id = created.json()["id"]
        published = await enterprise_client.post(
            f"/api/agents/{agent_id}/publish", json={"changelog": "first"}, headers=_auth(owner_a)
        )
        assert published.status_code == 200
        assert published.json()["version"] == 1
        versions = await enterprise_client.get(
            f"/api/agents/{agent_id}/versions", headers=_auth(owner_a)
        )
        assert len(versions.json()) == 1


class TestWorkflowApi:
    async def test_create_and_execute(self, enterprise_client, owner_a):
        created = await enterprise_client.post(
            "/api/workflows",
            json={
                "name": "Onboarding",
                "entry_node": "end",
                "nodes": [{"id": "end", "type": "terminal"}],
            },
            headers=_auth(owner_a),
        )
        assert created.status_code == 201, created.text
        workflow_id = created.json()["id"]
        await enterprise_client.post(
            f"/api/workflows/{workflow_id}/publish", headers=_auth(owner_a)
        )
        execution = await enterprise_client.post(
            f"/api/workflows/{workflow_id}/execute",
            json={"payload": {"x": 1}},
            headers=_auth(owner_a),
        )
        assert execution.status_code == 200, execution.text
        assert execution.json()["status"] == "completed"

        first = await enterprise_client.post(
            f"/api/workflows/{workflow_id}/execute",
            json={"payload": {"x": 2}},
            headers={**_auth(owner_a), "Idempotency-Key": "api-request-1"},
        )
        replay = await enterprise_client.post(
            f"/api/workflows/{workflow_id}/execute",
            json={"payload": {"x": 2}},
            headers={**_auth(owner_a), "Idempotency-Key": "api-request-1"},
        )
        conflict = await enterprise_client.post(
            f"/api/workflows/{workflow_id}/execute",
            json={"payload": {"x": 3}},
            headers={**_auth(owner_a), "Idempotency-Key": "api-request-1"},
        )
        assert first.status_code == replay.status_code == 200
        assert first.json()["id"] == replay.json()["id"]
        assert conflict.status_code == 409

    async def test_approval_route_resumes_persisted_execution(self, enterprise_client, owner_a):
        created = await enterprise_client.post(
            "/api/workflows",
            json={
                "name": "Approval flow",
                "entry_node": "approval",
                "nodes": [
                    {
                        "id": "approval",
                        "type": "approval",
                        "approver_role": "manager",
                        "next": "end",
                    },
                    {"id": "end", "type": "terminal"},
                ],
            },
            headers=_auth(owner_a),
        )
        assert created.status_code == 201, created.text
        workflow_id = created.json()["id"]
        published = await enterprise_client.post(
            f"/api/workflows/{workflow_id}/publish", headers=_auth(owner_a)
        )
        assert published.status_code == 200
        waiting = await enterprise_client.post(
            f"/api/workflows/{workflow_id}/execute",
            json={"payload": {}},
            headers=_auth(owner_a),
        )
        assert waiting.status_code == 200
        assert waiting.json()["status"] == "waiting_approval"
        wrong_path = await enterprise_client.post(
            f"/api/workflows/not-the-owning-workflow/executions/{waiting.json()['id']}/approve",
            headers=_auth(owner_a),
        )
        assert wrong_path.status_code == 404
        still_waiting = await enterprise_client.get(
            f"/api/workflows/{workflow_id}/executions/{waiting.json()['id']}",
            headers=_auth(owner_a),
        )
        assert still_waiting.status_code == 200
        assert still_waiting.json()["status"] == "waiting_approval"
        approved = await enterprise_client.post(
            f"/api/workflows/{workflow_id}/executions/{waiting.json()['id']}/approve",
            headers=_auth(owner_a),
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["status"] == "completed"

    async def test_rejects_arbitrary_action(self, enterprise_client, owner_a):
        response = await enterprise_client.post(
            "/api/workflows",
            json={
                "name": "Bad",
                "entry_node": "a",
                "nodes": [
                    {
                        "id": "a",
                        "type": "action",
                        "action_name": "eval",
                        "action_params": {"code": "os.system('rm -rf /')"},
                        "next": "end",
                    },
                    {"id": "end", "type": "terminal"},
                ],
            },
            headers=_auth(owner_a),
        )
        assert response.status_code == 422

    async def test_tenant_isolation(self, enterprise_client, owner_a, owner_b):
        created = await enterprise_client.post(
            "/api/workflows",
            json={
                "name": "Secret workflow",
                "entry_node": "end",
                "nodes": [{"id": "end", "type": "terminal"}],
            },
            headers=_auth(owner_a),
        )
        workflow_id = created.json()["id"]
        probe = await enterprise_client.get(f"/api/workflows/{workflow_id}", headers=_auth(owner_b))
        assert probe.status_code == 404


class TestCampaignApi:
    async def test_create_requires_write_permission(self, enterprise_client, viewer_a):
        response = await enterprise_client.post(
            "/api/campaigns", json={"name": "Spring"}, headers=_auth(viewer_a)
        )
        assert response.status_code == 403

    async def test_create_and_plan(self, enterprise_client, owner_a):
        created = await enterprise_client.post(
            "/api/campaigns", json={"name": "Spring", "goal": "qualify"}, headers=_auth(owner_a)
        )
        assert created.status_code == 201, created.text
        campaign_id = created.json()["id"]
        plan = await enterprise_client.post(
            f"/api/campaigns/{campaign_id}/plan", headers=_auth(owner_a)
        )
        assert plan.status_code == 200
        assert plan.json() == []  # no leads yet, still safe

    async def test_mass_assignment_protected(self, enterprise_client, owner_a):
        response = await enterprise_client.post(
            "/api/campaigns",
            json={"name": "Spring", "bypass_dnc": True, "skip_safety": True},
            headers=_auth(owner_a),
        )
        assert response.status_code == 422


# =================================================================== SECURITY ===


class TestSecurityGuarantees:
    async def test_workflow_cannot_execute_arbitrary_code(self, tenant_a):
        for bad in ("eval", "__import__", "os.system", "exec", "lambda"):
            node = WorkflowNode(
                id="a", type=NodeType.ACTION, action=WorkflowAction(bad), next="end"
            )
            definition = WorkflowDefinition(
                id="w",
                tenant_id=str(tenant_a.id),
                name="x",
                entry_node="a",
                nodes=(node, WorkflowNode(id="end", type=NodeType.TERMINAL)),
            )
            assert definition.validate(), f"{bad} should be rejected"

    async def test_automation_never_duplicates_side_effects(self, tenant_a):
        automation = AutomationDefinition(
            id="auto-x",
            tenant_id=str(tenant_a.id),
            name="x",
            event=TriggerEvent.LEAD_CREATED,
            actions=(WorkflowAction("enqueue_notification", {"template_id": "nope"}),),
            policy=ExecutionPolicy(max_per_event=1),
            status=AutomationStatus.ENABLED,
        )
        automation_service.register_automation(str(tenant_a.id), automation)
        r1 = await automation_service.execute_automation(str(tenant_a.id), automation, "lead-1", {})
        r2 = await automation_service.execute_automation(str(tenant_a.id), automation, "lead-1", {})
        assert r1.status == "completed"
        assert r2.status == "cancelled"

    async def test_no_cross_tenant_conversation_visibility(self, db, tenant_a, tenant_b):
        call = await conversation_service.start_conversation(
            db,
            tenant_a,
            channel=conversation_service.ConversationChannel.VOICE,
            from_number="+15551234567",
            intent="booking",
        )
        with pytest.raises(NotFoundError):
            conversation_service.project(tenant_b, call)

    async def test_analytics_never_exposes_phone_numbers(self, db, tenant_a):
        start, end = _bounded_range()
        await make_call(db, tenant_a, from_number="+15551234567")
        snapshot = await analytics_service.snapshot(
            db, str(tenant_a.id), kind=analytics_service.KpiKind.CALL, start=start, end=end
        )
        serialized = str(snapshot.points[0].metrics)
        assert "+15551234567" not in serialized

    async def test_api_requires_authentication(self, enterprise_client):
        response = await enterprise_client.get("/api/agents")
        assert response.status_code == 401
````

### File 16: `tests/test_enum_consistency.py`

SHA-256: `73626de6fdeec9e0cd9bba9ef49bb3edf32a6436ccb0ce464291cf7956b9cafc`

````
"""
Enum consistency between the Python models and the PostgreSQL / Alembic types.

These enums drifted twice before, and both times the failure was invisible
until a real call tried to write a row:

  * `CallStatus.NO_ANSWER` was referenced by the Twilio status webhook but did
    not exist on the Python enum -> AttributeError at import time.
  * `Speaker.CALLER` / `Speaker.AGENT` were written by the voice pipeline while
    the database type only accepted USER / ASSISTANT / SYSTEM -> every
    transcript insert failed.

Two layers of checking:

  1. Source parity -- the Alembic migrations are parsed and their enum literals
     compared against the Python enums. This catches drift without needing a
     live PostgreSQL server, which is the case the previous bugs slipped
     through.
  2. Round-trip -- the real models are created on an in-memory SQLite database
     and rows are written and read back. SQLAlchemy renders Enum() as VARCHAR
     plus a CHECK constraint there, so an out-of-range member is still
     rejected.
"""
from __future__ import annotations

import ast
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import (
    Base,
    Call,
    CallDirection,
    CallStatus,
    Speaker,
    Tenant,
    Turn,
)

ALEMBIC_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"
BASELINE = ALEMBIC_DIR / "0001_baseline.py"
ENUM_FIX = ALEMBIC_DIR / "0002_enum_consistency.py"

EXPECTED_CALL_STATUS = {
    "RINGING", "IN_PROGRESS", "COMPLETED", "FAILED", "NO_ANSWER", "TRANSFERRED", "CANCELLED",
}
EXPECTED_SPEAKER = {"USER", "ASSISTANT", "SYSTEM"}


# ---------------------------------------------------------------- helpers ---

def _string_literals_in_assignment(path: Path, target: str) -> set[str]:
    """Pull the string literals out of `target = ...` in a migration file."""
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if target not in names:
            continue
        return {
            n.value for n in ast.walk(node.value)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        }
    raise AssertionError(f"{target} not found in {path.name}")


# =============================================================================
# 1. Python enum shape
# =============================================================================

def test_call_status_has_exactly_the_expected_members():
    assert {e.name for e in CallStatus} == EXPECTED_CALL_STATUS


def test_speaker_has_exactly_the_expected_members():
    assert {e.name for e in Speaker} == EXPECTED_SPEAKER


def test_no_answer_exists_on_the_python_enum():
    """Regression: the Twilio webhook referenced this before it existed."""
    assert CallStatus.NO_ANSWER.value == "no_answer"


def test_transferred_was_not_removed():
    """It predates this fix and is a real terminal state -- keep it."""
    assert CallStatus.TRANSFERRED.value == "transferred"


def test_speaker_has_no_legacy_aliases_left():
    """CALLER / AGENT must be gone, not shadowed, or drift can recur."""
    assert not hasattr(Speaker, "CALLER")
    assert not hasattr(Speaker, "AGENT")


def test_enum_names_are_unique_and_have_no_duplicate_values():
    for enum_cls in (CallStatus, Speaker):
        names = [e.name for e in enum_cls]
        values = [e.value for e in enum_cls]
        assert len(names) == len(set(names))
        assert len(values) == len(set(values))


# =============================================================================
# 2. Parity with the Alembic / PostgreSQL types
# =============================================================================

def test_migration_declares_the_same_call_status_values():
    declared = _string_literals_in_assignment(ENUM_FIX, "CALL_STATUS_VALUES")
    import re

    # Historical migrations remain immutable. Include additive enum migrations.
    for path in ALEMBIC_DIR.glob("*.py"):
        declared.update(re.findall(r"ALTER TYPE callstatus ADD VALUE IF NOT EXISTS '([A-Z_]+)'", path.read_text()))
    assert declared == {e.name for e in CallStatus}


def test_migration_declares_the_same_speaker_values():
    declared = _string_literals_in_assignment(ENUM_FIX, "SPEAKER_VALUES")
    assert declared == {e.name for e in Speaker}


def test_baseline_speaker_type_already_matches_python():
    """
    The baseline created USER/ASSISTANT/SYSTEM. Standardising on those names is
    what lets an Alembic-managed database keep its rows untouched.
    """
    baseline = _string_literals_in_assignment(BASELINE, "speaker")
    assert baseline - {"speaker"} == {e.name for e in Speaker}


def test_migration_adds_every_value_the_baseline_was_missing():
    baseline = _string_literals_in_assignment(BASELINE, "call_status") - {"callstatus"}
    added = _string_literals_in_assignment(ENUM_FIX, "CALL_STATUS_VALUES")
    assert baseline - added == set(), "migration must not drop a baseline value"
    assert "TRANSFERRED" in added - baseline


def test_legacy_speaker_map_only_targets_valid_members():
    """Every remap destination must be a real member, or the cast will fail."""
    mapping = ast.literal_eval(
        next(
            ast.unparse(node.value)
            for node in ast.walk(ast.parse(ENUM_FIX.read_text()))
            if isinstance(node, ast.Assign)
            and any(getattr(t, "id", None) == "SPEAKER_LEGACY_MAP"
                    for t in node.targets)
        )
    )
    assert set(mapping.values()) <= {e.name for e in Speaker}
    assert set(mapping) == {"CALLER", "AGENT"}


def test_migration_chain_is_linked_to_the_baseline():
    tree = ast.parse(ENUM_FIX.read_text())
    consts = {
        t.id: node.value.value
        for node in ast.walk(tree) if isinstance(node, ast.Assign)
        for t in node.targets
        if isinstance(t, ast.Name) and isinstance(node.value, ast.Constant)
    }
    assert consts["revision"] == "0002_enum_consistency"
    assert consts["down_revision"] == "0001_baseline"


# =============================================================================
# 3. Round-trip against a real database
# =============================================================================

@pytest_asyncio.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def _make_tenant(session: AsyncSession) -> Tenant:
    tenant = Tenant(name="Bright Smile Dental", twilio_number=f"+1555{uuid.uuid4().int % 10**7:07d}")
    session.add(tenant)
    await session.commit()
    await session.refresh(tenant)
    return tenant


async def _make_call(session: AsyncSession, tenant: Tenant, status: CallStatus) -> Call:
    call = Call(
        tenant_id=tenant.id,
        call_sid=f"CA{uuid.uuid4().hex}",
        from_number="+15551234567",
        to_number=tenant.twilio_number,
        status=status,
        direction=CallDirection.INBOUND,
    )
    session.add(call)
    await session.commit()
    await session.refresh(call)
    return call


@pytest.mark.asyncio
async def test_no_answer_can_be_stored_and_retrieved(session):
    """The exact case the Twilio status webhook produces on an unanswered call."""
    tenant = await _make_tenant(session)
    call = await _make_call(session, tenant, CallStatus.NO_ANSWER)

    session.expunge_all()
    loaded = (
        await session.execute(select(Call).where(Call.id == call.id))
    ).scalar_one()

    assert loaded.status is CallStatus.NO_ANSWER
    assert loaded.status.value == "no_answer"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", list(CallStatus))
async def test_every_call_status_round_trips(session, status):
    tenant = await _make_tenant(session)
    call = await _make_call(session, tenant, status)
    session.expunge_all()
    loaded = (
        await session.execute(select(Call).where(Call.id == call.id))
    ).scalar_one()
    assert loaded.status is status


@pytest.mark.asyncio
@pytest.mark.parametrize("speaker", list(Speaker))
async def test_every_speaker_round_trips(session, speaker):
    tenant = await _make_tenant(session)
    call = await _make_call(session, tenant, CallStatus.COMPLETED)
    session.add(Turn(call_id=call.id, speaker=speaker, text="hello"))
    await session.commit()

    session.expunge_all()
    loaded = (
        await session.execute(select(Turn).where(Turn.call_id == call.id))
    ).scalar_one()
    assert loaded.speaker is speaker


@pytest.mark.asyncio
async def test_full_transcript_with_caller_agent_and_system_round_trips(session):
    """
    A realistic transcript: the caller speaks, the assistant answers, and a
    system note records the transfer. This is the insert that used to fail.
    """
    tenant = await _make_tenant(session)
    call = await _make_call(session, tenant, CallStatus.TRANSFERRED)

    transcript = [
        (Speaker.USER, "Hi, do you have anything Tuesday morning?"),
        (Speaker.ASSISTANT, "I've got nine or ten thirty. Which works?"),
        (Speaker.USER, "Actually, can I speak to a person?"),
        (Speaker.SYSTEM, "Escalated to +15551110000"),
        (Speaker.ASSISTANT, "Sure, let me put you through."),
    ]
    for index, (speaker, text) in enumerate(transcript):
        session.add(Turn(
            call_id=call.id, speaker=speaker, text=text,
            created_at=datetime(2026, 6, 15, 10, 0, index, tzinfo=timezone.utc),
        ))
    await session.commit()
    session.expunge_all()

    rows = (
        await session.execute(
            select(Turn).where(Turn.call_id == call.id).order_by(Turn.created_at)
        )
    ).scalars().all()

    assert [(t.speaker, t.text) for t in rows] == transcript
    assert {t.speaker for t in rows} == set(Speaker)


@pytest.mark.asyncio
async def test_call_lifecycle_still_works_end_to_end(session):
    """Existing behaviour: ringing -> in progress -> a terminal status."""
    tenant = await _make_tenant(session)
    call = await _make_call(session, tenant, CallStatus.RINGING)

    call.status = CallStatus.IN_PROGRESS
    await session.commit()

    call.status = CallStatus.COMPLETED
    call.duration_seconds = 91.4
    call.booked = True
    await session.commit()

    session.expunge_all()
    loaded = (
        await session.execute(select(Call).where(Call.id == call.id))
    ).scalar_one()
    assert loaded.status is CallStatus.COMPLETED
    assert loaded.booked is True
    assert loaded.duration_seconds == pytest.approx(91.4)


@pytest.mark.asyncio
async def test_transcript_reader_maps_speakers_to_llm_roles(session):
    """
    app/channels/messaging.load_history() branches on Speaker.USER. Guard the
    mapping so a future rename cannot silently invert every stored role.
    """
    tenant = await _make_tenant(session)
    call = await _make_call(session, tenant, CallStatus.COMPLETED)
    session.add_all([
        Turn(call_id=call.id, speaker=Speaker.USER, text="q",
             created_at=datetime(2026, 6, 15, 10, 0, 0, tzinfo=timezone.utc)),
        Turn(call_id=call.id, speaker=Speaker.ASSISTANT, text="a",
             created_at=datetime(2026, 6, 15, 10, 0, 1, tzinfo=timezone.utc)),
    ])
    await session.commit()

    rows = (
        await session.execute(
            select(Turn).where(Turn.call_id == call.id).order_by(Turn.created_at)
        )
    ).scalars().all()
    roles = ["user" if t.speaker is Speaker.USER else "assistant" for t in rows]
    assert roles == ["user", "assistant"]


# ---------------------------------------------------------------------------
# STEP 3: the same parity guarantee for TransferState.
#
# SQLAlchemy's Enum() persists the member NAME. Migration 0004 must therefore
# declare NONE/REQUESTED/... exactly, and adding a member to the Python enum
# without a migration must fail loudly here rather than at 3am in production.
# ---------------------------------------------------------------------------

def test_transfer_state_migration_matches_the_model():
    import ast
    import pathlib

    from app.db.models import TransferState

    source = pathlib.Path(
        "alembic/versions/0004_call_transfer_lifecycle.py"
    ).read_text()
    tree = ast.parse(source)

    declared: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = getattr(func, "attr", getattr(func, "id", ""))
        if name != "Enum":
            continue
        kwargs = {k.arg: k.value for k in node.keywords}
        type_name = kwargs.get("name")
        if isinstance(type_name, ast.Constant) and type_name.value == "transferstate":
            declared = {
                a.value for a in node.args if isinstance(a, ast.Constant)
            }

    assert declared, "no transferstate Enum(...) found in migration 0004"
    assert declared == {m.name for m in TransferState}, (
        "migration 0004 and TransferState disagree; "
        f"migration={sorted(declared)} model={sorted(m.name for m in TransferState)}"
    )


def test_transfer_state_values_are_lowercase_names():
    """Keeps the value/name mapping predictable for API responses."""
    from app.db.models import TransferState

    for member in TransferState:
        assert member.value == member.name.lower()


def test_migration_0004_adds_every_new_call_column():
    """A model column with no migration is a production-only crash."""
    import pathlib

    from app.db.models import Call

    source = pathlib.Path(
        "alembic/versions/0004_call_transfer_lifecycle.py"
    ).read_text()

    new_columns = [
        c.name for c in Call.__table__.columns
        if c.name.startswith("transfer_") or c.name == "failure_reason"
    ]
    assert new_columns
    additive_source = pathlib.Path("alembic/versions/0047_call_transfer_context.py").read_text()
    for column in new_columns:
        migration_source = additive_source if column == "transfer_context" else source
        assert f'"{column}"' in migration_source, f"{column} is missing from its transfer migration"


def test_migration_chain_is_linear_and_unbroken():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    scripts = ScriptDirectory.from_config(Config("alembic.ini"))
    revisions = list(scripts.walk_revisions())
    assert len(scripts.get_heads()) == 1
    assert len(scripts.get_bases()) == 1
    assert len({item.revision for item in revisions}) == len(revisions)
    assert scripts.get_revision("0004_call_transfer_lifecycle").down_revision == "0003_auth_rbac"
    children = {}
    for item in revisions:
        parent = item.down_revision
        assert parent is None or isinstance(parent, str), "unexpected merge in linear history"
        if parent is not None:
            assert scripts.get_revision(parent) is not None
            children[parent] = children.get(parent, 0) + 1
    assert all(count == 1 for count in children.values())


# ---------------------------------------------------------------------------
# STEP 4: the same parity guarantee for the knowledge-base enums and tables.
#
# Same failure mode, same defence. DocumentStatus in particular gates
# retrieval -- a member the PostgreSQL type does not know about means every
# insert with that status raises, and the failure would first appear when a
# document happened to be archived in production.
# ---------------------------------------------------------------------------

MIGRATION_0005 = "alembic/versions/0005_knowledge_rag.py"
MIGRATION_0019 = "alembic/versions/0019_env_scope_business_res.py"


def _declared_enum(path: str, type_name: str) -> set[str]:
    """Pull the literal members of a named sa.Enum(...) out of a migration."""
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path(path).read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func_name = getattr(node.func, "attr", getattr(node.func, "id", ""))
        if func_name != "Enum":
            continue
        kwargs = {k.arg: k.value for k in node.keywords}
        declared_name = kwargs.get("name")
        if isinstance(declared_name, ast.Constant) and declared_name.value == type_name:
            return {a.value for a in node.args if isinstance(a, ast.Constant)}
    return set()


def test_document_status_migration_matches_the_model():
    from app.db.models import DocumentStatus

    declared = _declared_enum(MIGRATION_0005, "documentstatus")
    assert declared, "no documentstatus Enum(...) found in migration 0005"
    assert declared == {m.name for m in DocumentStatus}, (
        f"migration={sorted(declared)} "
        f"model={sorted(m.name for m in DocumentStatus)}"
    )


def test_document_source_type_migration_matches_the_model():
    from app.db.models import DocumentSourceType

    declared = _declared_enum(MIGRATION_0005, "documentsourcetype")
    assert declared, "no documentsourcetype Enum(...) found in migration 0005"
    assert declared == {m.name for m in DocumentSourceType}


def test_knowledge_enum_values_are_lowercase_names():
    from app.db.models import DocumentSourceType, DocumentStatus

    for enum_cls in (DocumentStatus, DocumentSourceType):
        for member in enum_cls:
            assert member.value == member.name.lower()


def test_only_ready_is_searchable():
    """
    The single gate the whole feature depends on.

    If this set ever grows, archived or half-processed documents start being
    answered from. It is asserted here rather than only in the retrieval tests
    because it is a policy decision, not an implementation detail.
    """
    from app.db.models import SEARCHABLE_DOCUMENT_STATUSES, DocumentStatus

    assert SEARCHABLE_DOCUMENT_STATUSES == frozenset({DocumentStatus.READY})


def test_migration_0005_creates_every_base_model_column():
    """Every base-schema model column must have a migration at its creation point.

    ``environment_id`` is intentionally added later by migration 0019 when
    environment scoping is introduced for existing business resources; it is
    not part of the original RAG migration.
    """
    import pathlib

    from app.db.models import KnowledgeChunk, KnowledgeDocument

    source = pathlib.Path(MIGRATION_0005).read_text()
    environment_source = pathlib.Path(MIGRATION_0019).read_text()
    for model in (KnowledgeDocument, KnowledgeChunk):
        for column in model.__table__.columns:
            if column.name == "environment_id":
                assert '"environment_id"' in environment_source, (
                    f"{model.__tablename__}.environment_id is missing from migration 0019"
                )
                continue
            assert f'"{column.name}"' in source, (
                f"{model.__tablename__}.{column.name} is missing from migration 0005"
            )


def test_migration_0005_declares_the_tenant_scoped_constraints():
    """
    Deduplication must be per tenant and chunk slots must be unique.

    Both are correctness guarantees rather than optimisations: the first stops
    dedup from crossing tenants, the second stops a retried reindex from
    writing a second set of embeddings.
    """
    import pathlib

    source = pathlib.Path(MIGRATION_0005).read_text()
    assert "uq_knowledge_doc_hash" in source
    assert '"tenant_id", "content_hash"' in source
    assert "uq_knowledge_chunk_slot" in source
    assert '"document_id", "version", "chunk_index"' in source
    # The indexes retrieval actually uses.
    assert "ix_knowledge_doc_tenant_status" in source
    assert "ix_knowledge_chunk_tenant_doc" in source


def test_migration_0005_follows_0004():
    import pathlib
    import re

    source = pathlib.Path(MIGRATION_0005).read_text()
    assert re.search(r'^revision = "0005_knowledge_rag"', source, re.M)
    assert re.search(
        r'^down_revision = "0004_call_transfer_lifecycle"', source, re.M
    )


def test_chunk_carries_its_own_tenant_id():
    """
    Denormalisation that exists purely for safety.

    Retrieval filters on KnowledgeChunk.tenant_id directly, so a wrong join
    cannot widen results past one tenant. If this column is ever removed the
    isolation guarantee quietly becomes join-dependent.
    """
    from app.db.models import KnowledgeChunk

    assert "tenant_id" in KnowledgeChunk.__table__.columns
    assert not KnowledgeChunk.__table__.columns["tenant_id"].nullable


# ---------------------------------------------------------------------------
# STEP 5: the same parity guarantee for the CRM enums and tables.
#
# The knowledge migration (0005) shipped with `UPLOAD/URL/MANUAL/SYNC` against
# a model declaring `UPLOAD/TEXT/URL`, and only this style of test caught it.
# CrmSyncStatus is the equivalent risk here: it gates the worker's query, so a
# member PostgreSQL does not know about means every state transition raises,
# and it would first show up as syncs silently never being attempted.
# ---------------------------------------------------------------------------

MIGRATION_0006 = "alembic/versions/0006_crm_integrations.py"


def test_crm_provider_type_migration_matches_the_model():
    from app.db.models import CrmProviderType

    declared = _declared_enum(MIGRATION_0006, "crmprovidertype")
    assert declared, "no crmprovidertype Enum(...) found in migration 0006"
    assert declared == {m.name for m in CrmProviderType}


def test_crm_event_type_migration_matches_the_model():
    from app.db.models import CrmEventType

    declared = _declared_enum(MIGRATION_0006, "crmeventtype")
    assert declared, "no crmeventtype Enum(...) found in migration 0006"
    assert declared == {m.name for m in CrmEventType}


def test_crm_entity_type_migration_matches_the_model():
    from app.db.models import CrmEntityType

    declared = _declared_enum(MIGRATION_0006, "crmentitytype")
    assert declared == {m.name for m in CrmEntityType}


def test_crm_sync_status_migration_matches_the_model():
    from app.db.models import CrmSyncStatus

    declared = _declared_enum(MIGRATION_0006, "crmsyncstatus")
    assert declared == {m.name for m in CrmSyncStatus}


def test_integration_audit_actions_are_added_by_migration_0006():
    """
    `auditaction` is an existing PostgreSQL type, so new members need an
    explicit `ALTER TYPE ... ADD VALUE`. Forgetting it means every
    integration change raises when it tries to write its audit row -- and
    audit writes happen after the change has already been committed.
    """
    import pathlib

    from app.db.models import AuditAction

    source = pathlib.Path(MIGRATION_0006).read_text()
    for member in (
        AuditAction.INTEGRATION_CONNECTED, AuditAction.INTEGRATION_UPDATED,
        AuditAction.INTEGRATION_DISCONNECTED, AuditAction.INTEGRATION_TESTED,
    ):
        assert member.name in source, f"{member.name} is not added by migration 0006"
    assert "ALTER TYPE auditaction ADD VALUE" in source


def test_crm_enum_values_are_stable_wire_strings():
    """
    Two different conventions, both deliberate.

    Status and provider values are the lowercase of their names, matching
    every other enum in the schema. `CrmEventType` values are dotted wire
    strings (`lead.created`) because they are *published* -- they appear in
    outbound webhook bodies and in tenant-facing filters, so they are part of
    the integration contract and cannot be renamed freely.
    """
    from app.db.models import (
        CrmEntityType, CrmEventType, CrmProviderType, CrmSyncStatus,
    )

    for enum_cls in (CrmProviderType, CrmEntityType, CrmSyncStatus):
        for member in enum_cls:
            assert member.value == member.name.lower(), member

    for member in CrmEventType:
        assert member.value == member.name.lower().replace("_", ".", 1), member


def test_migration_0006_creates_every_model_column():
    """A model column with no migration is a production-only crash."""
    import pathlib

    from app.db.models import (
        CrmContactLink, CrmEvent, CrmIntegration, CrmSync, CrmWebhookReceipt,
    )

    source = pathlib.Path(MIGRATION_0006).read_text()
    for model in (
        CrmIntegration, CrmEvent, CrmSync, CrmContactLink, CrmWebhookReceipt,
    ):
        for column in model.__table__.columns:
            assert f'"{column.name}"' in source, (
                f"{model.__tablename__}.{column.name} is missing from migration 0006"
            )


def test_migration_0006_declares_the_constraints_that_carry_the_guarantees():
    import pathlib

    source = pathlib.Path(MIGRATION_0006).read_text()

    # Isolation: (tenant_id, provider) is a key, not a filter.
    assert "uq_crm_integration_tenant_provider" in source
    # Idempotency: one event per business fact per tenant.
    assert "uq_crm_event_idempotency" in source
    # One delivery record per (event, integration).
    assert "uq_crm_sync_event_integration" in source
    # No duplicate contacts, and no cross-tenant contact matching.
    assert "uq_crm_contact_identity" in source
    # Inbound replay protection.
    assert "uq_crm_receipt_event" in source
    # The worker's query.
    assert "ix_crm_sync_due" in source


def test_migration_0006_follows_0005():
    import pathlib
    import re

    source = pathlib.Path(MIGRATION_0006).read_text()
    assert re.search(r'^revision = "0006_crm_integrations"', source, re.M)
    assert re.search(r'^down_revision = "0005_knowledge_rag"', source, re.M)


def test_migration_0006_does_not_touch_the_legacy_crm_columns():
    """
    The old `tenants.crm_*` columns hold configuration a customer entered.
    Dropping them in the same release that introduces their replacement leaves
    no way back, and the previous application version is still running during
    a rolling deploy.
    """
    import pathlib

    source = pathlib.Path(MIGRATION_0006).read_text()
    for legacy in ("crm_webhook_url", "crm_api_key", "crm_type"):
        assert f'drop_column("tenants", "{legacy}")' not in source
        assert f"drop_column('tenants', '{legacy}')" not in source


def test_the_retryable_and_terminal_status_sets_do_not_overlap():
    """
    A status in both sets would be picked up by the worker forever. This is a
    policy decision, asserted here rather than only in the service tests.
    """
    from app.db.models import (
        RETRYABLE_SYNC_STATUSES, TERMINAL_SYNC_STATUSES, CrmSyncStatus,
    )

    assert not (RETRYABLE_SYNC_STATUSES & TERMINAL_SYNC_STATUSES)
    # PROCESSING is in neither: it is held by a worker and recovered by the
    # reaper, not claimed by a second worker.
    assert CrmSyncStatus.PROCESSING not in RETRYABLE_SYNC_STATUSES
    assert CrmSyncStatus.PROCESSING not in TERMINAL_SYNC_STATUSES
    assert (
        RETRYABLE_SYNC_STATUSES | TERMINAL_SYNC_STATUSES | {CrmSyncStatus.PROCESSING}
    ) == set(CrmSyncStatus)


async def test_every_crm_enum_round_trips_through_the_database(db, tenant_a):
    """
    The second layer of the STEP 1 check: write every member and read it back.
    SQLAlchemy renders Enum() as VARCHAR + CHECK on SQLite, so an out-of-range
    member is still rejected.
    """
    from sqlalchemy import select as _select

    from app.db.models import (
        CrmEntityType, CrmEvent, CrmEventType, CrmIntegration, CrmProviderType,
    )

    for provider in CrmProviderType:
        db.add(CrmIntegration(tenant_id=tenant_a.id, provider=provider, config={}))
    for index, event_type in enumerate(CrmEventType):
        db.add(CrmEvent(
            tenant_id=tenant_a.id, event_type=event_type,
            entity_type=CrmEntityType.CALL, entity_id=uuid.uuid4(),
            idempotency_key=f"roundtrip-{index}", payload={},
        ))
    await db.commit()

    providers = (
        (await db.execute(_select(CrmIntegration.provider))).scalars().all()
    )
    assert set(providers) == set(CrmProviderType)

    types = (await db.execute(_select(CrmEvent.event_type))).scalars().all()
    assert set(types) == set(CrmEventType)


# ---------------------------------------------------------------------------
# STEP 6: the same parity guarantee for the calendar/scheduling enums.
#
# AppointmentStatus is the equivalent risk to DocumentStatus in STEP 4: it
# gates the conflict check, so a member PostgreSQL does not know about means
# every booking write raises -- and it would first surface as a caller being
# told the calendar is broken.
# ---------------------------------------------------------------------------

MIGRATION_0007 = "alembic/versions/0007_calendar_scheduling.py"


def test_appointment_status_migration_matches_the_model():
    from app.db.models import AppointmentStatus

    declared = _declared_enum(MIGRATION_0007, "appointmentstatus")
    assert declared, "no appointmentstatus Enum(...) found in migration 0007"
    assert declared == {m.name for m in AppointmentStatus}


def test_calendar_provider_type_migration_matches_the_model():
    from app.db.models import CalendarProviderType

    declared = _declared_enum(MIGRATION_0007, "calendarprovidertype")
    assert declared == {m.name for m in CalendarProviderType}


def test_calendar_audit_actions_are_added_by_migration_0007():
    import pathlib

    from app.db.models import AuditAction

    source = pathlib.Path(MIGRATION_0007).read_text()
    for member in (
        AuditAction.CALENDAR_CONNECTED, AuditAction.CALENDAR_DISCONNECTED,
        AuditAction.APPOINTMENT_CANCELLED, AuditAction.APPOINTMENT_RESCHEDULED,
    ):
        assert member.name in source, f"{member.name} is not added by 0007"
    assert "ALTER TYPE auditaction ADD VALUE" in source


def test_calendar_enum_values_are_lowercase_names():
    from app.db.models import AppointmentStatus, CalendarProviderType

    for enum_cls in (AppointmentStatus, CalendarProviderType):
        for member in enum_cls:
            assert member.value == member.name.lower(), member


def test_only_live_statuses_block_a_slot():
    """
    The single policy the conflict check depends on. If this set ever grows,
    cancelled appointments start blocking their time; if it shrinks, a
    confirmed one stops. Asserted here rather than only in the booking tests
    because it is a policy decision, not an implementation detail.
    """
    from app.db.models import (
        BLOCKING_APPOINTMENT_STATUSES,
        TERMINAL_APPOINTMENT_STATUSES,
        AppointmentStatus,
    )

    assert BLOCKING_APPOINTMENT_STATUSES == frozenset({
        AppointmentStatus.PENDING,
        AppointmentStatus.CONFIRMED,
        AppointmentStatus.RESCHEDULED,
    })
    assert not (BLOCKING_APPOINTMENT_STATUSES & TERMINAL_APPOINTMENT_STATUSES)
    assert (
        BLOCKING_APPOINTMENT_STATUSES | TERMINAL_APPOINTMENT_STATUSES
    ) == set(AppointmentStatus)


def test_migration_0007_creates_every_new_model_column():
    import pathlib

    from app.db.models import (
        Appointment,
        CalendarIntegration,
        CalendarWebhookReceipt,
        SchedulingPolicy,
    )

    source = pathlib.Path(MIGRATION_0007).read_text()
    environment_source = pathlib.Path(MIGRATION_0019).read_text()

    # Columns that existed before STEP 6 live in migration 0001.
    pre_existing = {
        "id", "tenant_id", "call_id", "customer_name", "customer_phone",
        "reason", "starts_at", "ends_at", "google_event_id", "created_at",
    }
    for column in Appointment.__table__.columns:
        if column.name in pre_existing:
            continue
        if column.name == "environment_id":
            assert '"environment_id"' in environment_source, (
                "appointments.environment_id is missing from migration 0019"
            )
            continue
        assert f'"{column.name}"' in source, (
            f"appointments.{column.name} is missing from migration 0007"
        )

    for model in (CalendarIntegration, SchedulingPolicy, CalendarWebhookReceipt):
        for column in model.__table__.columns:
            assert f'"{column.name}"' in source, (
                f"{model.__tablename__}.{column.name} is missing from 0007"
            )


def test_migration_0007_declares_the_constraints_that_carry_the_guarantees():
    import pathlib

    source = pathlib.Path(MIGRATION_0007).read_text()

    # The double-booking defence.
    assert "uq_appointment_slot" in source
    # A retried booking finds its own earlier attempt.
    assert "uq_appointment_idempotency" in source
    # (tenant, provider) is a key, not a convention.
    assert "uq_calendar_integration_tenant_provider" in source
    assert "uq_scheduling_policy_tenant" in source
    # Inbound webhook replay protection.
    assert "uq_calendar_receipt_event" in source


def test_migration_0007_backfills_rather_than_leaving_existing_rows_wrong():
    """
    Two backfills are load-bearing:

    * every pre-STEP-6 appointment becomes CONFIRMED, because the old code
      only ever wrote a row it believed in -- defaulting them to PENDING would
      make the entire existing book look unconfirmed;
    * `timezone` is copied from the owning tenant, because leaving the column
      default of 'UTC' would silently reinterpret every historical appointment
      by the tenant's offset.
    """
    import pathlib

    source = pathlib.Path(MIGRATION_0007).read_text()
    assert "UPDATE appointments SET status = 'CONFIRMED'" in source
    assert "SELECT t.timezone FROM tenants t" in source
    assert "GOOGLE_SERVICE_ACCOUNT" in source


def test_migration_0007_follows_0006():
    import pathlib
    import re

    source = pathlib.Path(MIGRATION_0007).read_text()
    assert re.search(r'^revision = "0007_calendar_scheduling"', source, re.M)
    assert re.search(r'^down_revision = "0006_crm_integrations"', source, re.M)


def test_migration_0007_does_not_drop_the_legacy_calendar_column():
    """
    `tenants.google_calendar_id` and `app/integrations/google_calendar.py` are
    still read by the previous application version during a rolling deploy,
    and by `tests/test_availability.py`.
    """
    import pathlib

    source = pathlib.Path(MIGRATION_0007).read_text()
    assert 'drop_column("tenants", "google_calendar_id")' not in source
    assert "drop_column(\"appointments\", \"google_event_id\")" not in source


async def test_every_appointment_status_round_trips_through_the_database(
    db, tenant_a
):
    from sqlalchemy import select as _select

    from app.db.models import Appointment, AppointmentStatus

    base = datetime(2026, 6, 16, 14, 0, tzinfo=timezone.utc)
    for index, status in enumerate(AppointmentStatus):
        db.add(Appointment(
            tenant_id=tenant_a.id, customer_name="X", customer_phone="+1555",
            starts_at=base, ends_at=base, timezone="UTC", status=status,
            slot_key=f"slot-{index}",
        ))
    await db.commit()

    stored = (await db.execute(_select(Appointment.status))).scalars().all()
    assert set(stored) == set(AppointmentStatus)


# ---------------------------------------------------------------------------
# STEP 7: the same parity guarantee for the billing enums.
#
# `SubscriptionStatus` gates entitlement and `UsageMetric` gates the rollup
# query, so a member PostgreSQL does not know about means every write raises
# -- and for billing that surfaces as a customer who cannot be charged or
# cannot use the product.
# ---------------------------------------------------------------------------

MIGRATION_0008 = "alembic/versions/0008_billing.py"


def test_billing_provider_type_migration_matches_the_model():
    from app.db.models import BillingProviderType

    declared = _declared_enum(MIGRATION_0008, "billingprovidertype")
    assert declared, "no billingprovidertype Enum(...) found in migration 0008"
    assert declared == {m.name for m in BillingProviderType}


def test_subscription_status_migration_matches_the_model():
    from app.db.models import SubscriptionStatus

    declared = _declared_enum(MIGRATION_0008, "subscriptionstatus")
    assert declared == {m.name for m in SubscriptionStatus}


def test_usage_metric_migration_matches_the_model():
    from app.db.models import UsageMetric

    declared = _declared_enum(MIGRATION_0008, "usagemetric")
    assert declared == {m.name for m in UsageMetric}


def test_usage_event_type_migration_matches_the_model():
    from app.db.models import UsageEventType

    declared = _declared_enum(MIGRATION_0008, "usageeventtype")
    assert declared == {m.name for m in UsageEventType}


def test_invoice_and_interval_migrations_match_the_model():
    from app.db.models import BillingInterval, InvoiceStatus

    assert _declared_enum(MIGRATION_0008, "invoicestatus") == {
        m.name for m in InvoiceStatus
    }
    assert _declared_enum(MIGRATION_0008, "billinginterval") == {
        m.name for m in BillingInterval
    }


def test_billing_enum_values_are_lowercase_names():
    from app.db.models import (
        BillingInterval, BillingProviderType, InvoiceStatus, SubscriptionStatus,
        UsageEventType, UsageMetric,
    )

    for enum_cls in (
        BillingProviderType, SubscriptionStatus, BillingInterval,
        UsageMetric, UsageEventType, InvoiceStatus,
    ):
        for member in enum_cls:
            assert member.value == member.name.lower(), member


def test_the_new_billing_audit_actions_are_added_by_the_migration():
    import pathlib

    from app.db.models import AuditAction

    source = pathlib.Path(MIGRATION_0008).read_text()
    billing_actions = [a for a in AuditAction if a.name.startswith("BILLING_")]
    assert len(billing_actions) == 9
    for member in billing_actions:
        assert member.name in source, f"{member.name} is not added by 0008"
    assert "ALTER TYPE auditaction ADD VALUE" in source


MIGRATION_0009 = "alembic/versions/0009_perf_policy.py"


def test_the_step9_audit_actions_are_added_by_the_migration():
    """
    GDPR export/erasure and license issuance write audit rows, and those rows
    must exist on the PostgreSQL `auditaction` type before the first write --
    the same drift class the 0006/0007 tests already guard.
    """
    import pathlib

    from app.db.models import AuditAction

    source = pathlib.Path(MIGRATION_0009).read_text()
    for member in (
        AuditAction.GDPR_EXPORT, AuditAction.GDPR_ERASURE,
        AuditAction.LICENSE_ISSUED,
    ):
        assert member.name in source, f"{member.name} is not added by 0009"
    assert "ALTER TYPE auditaction ADD VALUE" in source


def test_step9_migration_follows_0008():
    import pathlib
    import re

    source = pathlib.Path(MIGRATION_0009).read_text()
    assert re.search(r'^revision = "0009_perf_policy"', source, re.M)
    assert re.search(r'^down_revision = "0008_billing"', source, re.M)


def test_only_live_statuses_entitle_service():
    """
    The policy the whole entitlement layer rests on, asserted here rather than
    only in the billing tests because it is a business decision.

    PAST_DUE entitles deliberately: the provider is still retrying the card,
    and cutting a business off on the first failed charge costs far more
    goodwill than the few days of service it saves.
    """
    from app.db.models import (
        ENTITLED_SUBSCRIPTION_STATUSES,
        TERMINAL_SUBSCRIPTION_STATUSES,
        SubscriptionStatus,
    )

    assert ENTITLED_SUBSCRIPTION_STATUSES == frozenset({
        SubscriptionStatus.TRIALING,
        SubscriptionStatus.ACTIVE,
        SubscriptionStatus.CANCELING,
        SubscriptionStatus.PAST_DUE,
    })
    assert not (ENTITLED_SUBSCRIPTION_STATUSES & TERMINAL_SUBSCRIPTION_STATUSES)
    assert SubscriptionStatus.CANCELED in TERMINAL_SUBSCRIPTION_STATUSES


def test_migration_0008_creates_every_billing_column():
    import pathlib

    from app.db.models import (
        BillingInvoice, BillingPlan, BillingWebhookReceipt, Subscription,
        UsageEvent, UsageSummary,
    )

    source = pathlib.Path(MIGRATION_0008).read_text()
    environment_source = pathlib.Path(MIGRATION_0019).read_text()
    for model in (
        BillingPlan, Subscription, UsageEvent, UsageSummary, BillingInvoice,
        BillingWebhookReceipt,
    ):
        for column in model.__table__.columns:
            if column.name == "environment_id":
                assert '"environment_id"' in environment_source, (
                    f"{model.__tablename__}.environment_id is missing from migration 0019"
                )
                continue
            assert f'"{column.name}"' in source, (
                f"{model.__tablename__}.{column.name} is missing from 0008"
            )


def test_migration_0008_declares_the_constraints_that_carry_the_guarantees():
    import pathlib

    source = pathlib.Path(MIGRATION_0008).read_text()

    # The money constraint: no duplicate charge, whatever the interleaving.
    assert "uq_usage_event_idempotency" in source
    assert "uq_usage_summary_slot" in source
    # One subscription per tenant, and one tenant per provider subscription.
    assert "uq_subscription_tenant_provider" in source
    assert "uq_subscription_external_id" in source
    # Plan codes are the checkout trust boundary.
    assert "uq_billing_plan_code" in source
    # Webhook dedupe and idempotent invoice mirroring.
    assert "uq_billing_receipt_event" in source
    assert "uq_billing_invoice_external" in source


def test_migration_0008_does_not_touch_the_legacy_tenant_billing_columns():
    """
    `minutes_used` is demoted to a cache, not dropped: the dashboard reads it,
    and `tenants.plan` is the fallback that lets a pre-STEP-7 tenant keep the
    entitlements they were sold (requirement 25).
    """
    import pathlib

    source = pathlib.Path(MIGRATION_0008).read_text()
    for legacy in ("minutes_used", "included_minutes"):
        assert f'drop_column("tenants", "{legacy}")' not in source
    assert 'drop_column("tenants", "plan")' not in source


def test_migration_0008_seeds_no_plan_rows():
    """
    Prices are business data that changes. Repricing should be an operator
    action against a running system, not a schema change followed by a deploy.
    """
    import pathlib

    source = pathlib.Path(MIGRATION_0008).read_text()
    assert "INSERT INTO billing_plans" not in source
    assert "bulk_insert" not in source


def test_migration_0008_follows_0007():
    import pathlib
    import re

    source = pathlib.Path(MIGRATION_0008).read_text()
    assert re.search(r'^revision = "0008_billing"', source, re.M)
    assert re.search(r'^down_revision = "0007_calendar_scheduling"', source, re.M)


def test_money_is_stored_as_integers_never_floats():
    """
    A float 19.99 is not exactly 19.99, and accumulating fractional overage in
    binary floating point produces invoices that do not reconcile with
    themselves.
    """
    import sqlalchemy as sa

    from app.db.models import BillingInvoice, BillingPlan, UsageEvent, UsageSummary

    money_like = ("cents", "millicents", "quantity")
    for model in (BillingPlan, UsageEvent, UsageSummary, BillingInvoice):
        for column in model.__table__.columns:
            if any(word in column.name for word in money_like):
                assert isinstance(column.type, sa.Integer), (
                    f"{model.__tablename__}.{column.name} is "
                    f"{column.type}, not Integer"
                )


async def test_every_subscription_status_round_trips_through_the_database(
    db, tenant_a
):
    from sqlalchemy import select as _select

    from app.db.models import (
        BillingProviderType, Subscription, SubscriptionStatus, Tenant,
    )

    # One subscription per (tenant, provider), so each status needs its own
    # tenant -- which is itself a check that the constraint is real.
    for index, status in enumerate(SubscriptionStatus):
        tenant = Tenant(name=f"T{index}", twilio_number=f"+1555000{index:04d}")
        db.add(tenant)
        await db.flush()
        db.add(Subscription(
            tenant_id=tenant.id, provider=BillingProviderType.STRIPE, status=status
        ))
    await db.commit()

    stored = (await db.execute(_select(Subscription.status))).scalars().all()
    assert set(stored) == set(SubscriptionStatus)
````

### File 17: `tests/test_telephony_runtime_e2e.py`

SHA-256: `04dd97ef9225d169116f1c4a9bf5dbf8025f110e0d60abd949ca85e20714ae53`

````
"""
tests/test_telephony_runtime_e2e.py
Prompt 6 — Telephony / Voice Runtime End-to-End Integration Tests:
1. Phone number creation, E.164 normalization, invalid number rejection, duplicate rejection, and deletion.
2. Agent binding (`inbound_agent_id`, `outbound_agent_id`) and invalid/archived agent rejection.
3. SIP connection configuration, secret hashing (never storing plaintext passwords), and OPTIONS test/verify failure on unreachable URI.
4. Inbound webhook call routing to bound agent and deterministic failure when no inbound agent is bound.
5. Outbound call initiation, provider dispatch, state transitions, and honest `NOT_CONFIGURED` failure when live provider credentials are absent.
6. Explicit call state machine valid transitions and illegal state transition rejection.
7. Real-time media gateway session connect, audio frame ingestion, invalid frame rejection, utterance handling, and disconnect.
8. Barge-in / interruption flushing outbound speech queue.
9. DTMF digit validation, buffer accumulation, and IVR route matching.
10. Cold transfer, warm transfer (with whisper summary), agent-to-agent transfer (with context preservation), and deterministic fallback (`RETURN_TO_AGENT`, `HANGUP`).
"""

from __future__ import annotations

import base64
import json
import time

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Agent, AgentVersion, Environment, UserRole
from app.telephony.call_session import validate_call_state_transition
from app.telephony.enums import TelephonyCallState
from app.telephony.exceptions import CallStateTransitionError
from app.telephony.media_gateway import media_gateway_manager
from app.telephony.webhooks import DEFAULT_WEBHOOK_SECRET, compute_webhook_hmac_signature
from tests.conftest import auth_headers, make_tenant, make_user


def _signed_webhook_headers(
    body_dict: dict,
    *,
    org_id: str | None = None,
    secret: str = DEFAULT_WEBHOOK_SECRET,
) -> tuple[bytes, dict[str, str]]:
    raw = json.dumps(body_dict).encode("utf-8")
    ts = str(int(time.time()))
    sig = compute_webhook_hmac_signature(secret=secret, raw_body=raw, timestamp=ts)
    headers = {
        "Content-Type": "application/json",
        "X-Voxdesk-Timestamp": ts,
        "X-Voxdesk-Signature": f"sha256={sig}",
    }
    if org_id:
        headers["X-Voxdesk-Organization-Id"] = org_id
    return raw, headers


async def _seed_agent(
    db: AsyncSession,
    *,
    tenant_id,
    name: str = "Enterprise Receptionist Agent",
    greeting: str = "Hello, thank you for calling Acme Enterprise.",
) -> Agent:
    agent = Agent(
        tenant_id=tenant_id,
        external_key=f"ag_{name.lower().replace(' ', '_')}_{int(time.time() * 1000) % 100000}",
        name=name,
        description="Voice runtime E2E agent",
        status="published",
        published_version_number=1,
    )
    db.add(agent)
    await db.flush()
    ver = AgentVersion(
        agent_id=agent.id,
        tenant_id=tenant_id,
        version_number=1,
        status="published",
        config_hash="hash_v1_telephony_e2e",
        config_snapshot={
            "greeting": greeting,
            "system_prompt": f"You are {name}.",
            "voice_id": "alloy",
        },
        changelog="Initial published version",
    )
    db.add(ver)
    await db.flush()
    agent.published_version_id = ver.id
    await db.commit()
    return agent


@pytest.mark.asyncio
async def test_phone_number_lifecycle_e164_validation_and_agent_binding(
    client: AsyncClient,
    db: AsyncSession,
):
    tenant = await make_tenant(db, "Telephony E2E Org")
    admin = await make_user(db, tenant, role=UserRole.ADMIN)
    headers = await auth_headers(client, admin)
    agent = await _seed_agent(db, tenant_id=tenant.id, name="Inbound Support Agent")

    # 1. Invalid non-E.164 phone number is rejected with 422
    bad_resp = await client.post(
        "/api/v1/telephony/phone-numbers",
        json={"number": "5550101234", "provider": "TWILIO"},
        headers=headers,
    )
    assert bad_resp.status_code == 422

    # 2. Formatted E.164 phone number is normalized and created with status CONFIGURED when unbound
    create_resp = await client.post(
        "/api/v1/telephony/phone-numbers",
        json={
            "number": "+1 (415) 555-0142",
            "provider": "TWILIO",
            "metadata": {"department": "front_desk"},
        },
        headers=headers,
    )
    assert create_resp.status_code == 201, create_resp.text
    created = create_resp.json()
    phone_id = created["id"]
    assert created["e164_number"] == "+14155550142"
    assert created["organization_id"] == str(tenant.id)
    assert created["status"] in {"CONFIGURED", "READY"}
    assert created["inbound_agent_id"] is None

    # 3. Duplicate E.164 number in the same organization is rejected with 409
    dup_resp = await client.post(
        "/api/v1/telephony/phone-numbers",
        json={"number": "+14155550142", "provider": "TWILIO"},
        headers=headers,
    )
    assert dup_resp.status_code == 409

    # 4. Bind inbound and outbound agent transitions phone number to READY
    bind_resp = await client.post(
        f"/api/v1/telephony/phone-numbers/{phone_id}/bind-agent",
        json={
            "inbound_agent_id": str(agent.id),
            "outbound_agent_id": str(agent.id),
        },
        headers=headers,
    )
    assert bind_resp.status_code == 200, bind_resp.text
    bound = bind_resp.json()
    assert bound["inbound_agent_id"] == str(agent.id)
    assert bound["outbound_agent_id"] == str(agent.id)
    assert bound["status"] == "READY"

    # 5. Binding a non-existent UUID agent fails deterministically with 422
    nonexistent_resp = await client.post(
        f"/api/v1/telephony/phone-numbers/{phone_id}/bind-agent",
        json={"inbound_agent_id": "00000000-0000-0000-0000-000000000999"},
        headers=headers,
    )
    assert nonexistent_resp.status_code == 422

    # 6. List and get phone number
    list_resp = await client.get("/api/v1/telephony/phone-numbers", headers=headers)
    assert list_resp.status_code == 200
    assert list_resp.json()["total"] == 1

    # 7. Delete phone number
    del_resp = await client.delete(
        f"/api/v1/telephony/phone-numbers/{phone_id}", headers=headers
    )
    assert del_resp.status_code == 200
    assert del_resp.json()["deleted"] is True


@pytest.mark.asyncio
async def test_sip_connection_validation_secret_hashing_and_test_probe(
    client: AsyncClient,
    db: AsyncSession,
):
    tenant = await make_tenant(db, "SIP Trunking Org")
    admin = await make_user(db, tenant, role=UserRole.ADMIN)
    headers = await auth_headers(client, admin)

    # 1. Invalid SIP URI is rejected
    bad_sip = await client.post(
        "/api/v1/telephony/sip-connections",
        json={
            "name": "Broken SIP",
            "termination_uri": "sip:invalid host with spaces",
            "transport": "TLS",
        },
        headers=headers,
    )
    assert bad_sip.status_code == 422

    # 2. Valid SIP connection hashes secret into credential_reference, never exposing plaintext
    raw_secret = "SuperSecretSipPassword!2026"
    create_sip = await client.post(
        "/api/v1/telephony/sip-connections",
        json={
            "name": "Primary Carrier TLS Trunk",
            "termination_uri": "sip:pstn.carrier.example.com:5061",
            "origination_uri": "sip:ingress.voxdesk.example.com:5061",
            "phone_number": "+14155550155",
            "username": "trunk_auth_user",
            "password_secret": raw_secret,
            "transport": "TLS",
        },
        headers=headers,
    )
    assert create_sip.status_code == 201, create_sip.text
    sip_data = create_sip.json()
    sip_id = sip_data["id"]
    assert sip_data["status"] == "CONFIGURED"
    assert sip_data["has_credentials"] is True
    assert sip_data["credential_reference"].startswith("sec_ref_sha256_")
    assert raw_secret not in create_sip.text

    # 3. Testing reachable SIP connection transitions status to READY
    ok_test = await client.post(
        f"/api/v1/telephony/sip-connections/{sip_id}/test",
        json={},
        headers=headers,
    )
    assert ok_test.status_code == 200, ok_test.text
    assert ok_test.json()["status"] == "READY"

    # 4. Testing unreachable SIP endpoint fails honestly and marks status FAILED
    fail_test = await client.post(
        f"/api/v1/telephony/sip-connections/{sip_id}/test",
        json={"simulate_unreachable": True},
        headers=headers,
    )
    assert fail_test.status_code == 422
    sip_list = await client.get("/api/v1/telephony/sip-connections", headers=headers)
    assert sip_list.status_code == 200
    assert sip_list.json()["items"][0]["status"] == "FAILED"


@pytest.mark.asyncio
async def test_inbound_webhook_call_routing_and_unconfigured_agent_failure(
    client: AsyncClient,
    db: AsyncSession,
):
    tenant = await make_tenant(db, "Inbound Call Org")
    admin = await make_user(db, tenant, role=UserRole.ADMIN)
    headers = await auth_headers(client, admin)
    agent = await _seed_agent(db, tenant_id=tenant.id, name="Inbound Triage Agent")
    environment = await db.scalar(select(Environment).where(Environment.tenant_id == tenant.id, Environment.kind == "production"))
    assert environment is not None

    # 1. Create phone number WITHOUT inbound agent bound
    num_resp = await client.post(
        "/api/v1/telephony/phone-numbers",
        json={
            "number": "+14155550160",
            "provider": "SIMULATED",
            "environment_id": str(environment.id),
        },
        headers=headers,
    )
    assert num_resp.status_code == 201
    phone_id = num_resp.json()["id"]

    # 2. Inbound webhook to unbound number fails deterministically (never fabricates fake agent)
    unconfigured_payload = {
        "provider_event_id": "evt_inbound_unbound_001",
        "provider_call_id": "call_unbound_001",
        "event_type": "call.ringing",
        "direction": "inbound",
        "from_number": "+14155559999",
        "to_number": "+14155550160",
    }
    raw_body, wh_headers = _signed_webhook_headers(
        unconfigured_payload, org_id=str(tenant.id)
    )
    unbound_wh = await client.post(
        "/api/v1/telephony/webhooks/simulated/inbound",
        content=raw_body,
        headers=wh_headers,
    )
    assert unbound_wh.status_code == 422
    assert "no bound inbound agent" in unbound_wh.text.lower()

    # 3. Bind the durable agent to the phone number
    bind_resp = await client.post(
        f"/api/v1/telephony/phone-numbers/{phone_id}/bind-agent",
        json={"inbound_agent_id": str(agent.id)},
        headers=headers,
    )
    assert bind_resp.status_code == 200

    # 4. Inbound webhook now routes to bound agent and progresses RINGING -> ANSWERED -> IN_PROGRESS -> COMPLETED
    for idx, (evt_type, status_val) in enumerate(
        [
            ("call.ringing", "ringing"),
            ("call.answered", "answered"),
            ("call.in_progress", "in_progress"),
            ("call.completed", "completed"),
        ],
        start=1,
    ):
        payload = {
            "provider_event_id": f"evt_inbound_ok_{idx}",
            "provider_call_id": "call_inbound_live_002",
            "event_type": evt_type,
            "status": status_val,
            "direction": "inbound",
            "from_number": "+14155558888",
            "to_number": "+14155550160",
            "duration_seconds": 42 if status_val == "completed" else None,
        }
        raw_b, wh_h = _signed_webhook_headers(payload, org_id=str(tenant.id))
        endpoint = "inbound" if idx == 1 else "status"
        res = await client.post(
            f"/api/v1/telephony/webhooks/simulated/{endpoint}",
            content=raw_b,
            headers=wh_h,
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["accepted"] is True
        assert body["duplicate"] is False

    # Verify persisted call session state and transcript
    calls_resp = await client.get("/api/v1/telephony/calls", headers=headers)
    assert calls_resp.status_code == 200
    matched_calls = [
        c
        for c in calls_resp.json()["items"]
        if c["provider_call_id"] == "call_inbound_live_002"
    ]
    assert len(matched_calls) == 1
    call_record = matched_calls[0]
    assert call_record["status"] == "COMPLETED"
    assert call_record["agent_id"] == str(agent.id)
    assert call_record["usage_finalized"] is True
    assert len(call_record["transcript_turns"]) >= 1


@pytest.mark.asyncio
async def test_outbound_call_lifecycle_and_provider_readiness_honesty(
    client: AsyncClient,
    db: AsyncSession,
):
    tenant = await make_tenant(db, "Outbound Dialer Org")
    admin = await make_user(db, tenant, role=UserRole.ADMIN)
    headers = await auth_headers(client, admin)
    agent = await _seed_agent(db, tenant_id=tenant.id, name="Outbound Sales Agent")

    # 1. Requesting a live TWILIO outbound call when Twilio credentials are not configured
    # fails honestly with 422 TELEPHONY_NOT_CONFIGURED instead of faking a live carrier call
    unconf_resp = await client.post(
        "/api/v1/telephony/calls/outbound",
        json={
            "to_number": "+14155550199",
            "from_number": "+14155550101",
            "agent_id": str(agent.id),
            "provider": "TWILIO",
            "idempotency_key": "unconfigured-live-provider-check",
            "is_simulation": False,
        },
        headers=headers,
    )
    assert unconf_resp.status_code == 422
    assert "not configured" in unconf_resp.text.lower()

    # 2. Requesting an outbound call with SIMULATED provider succeeds and is idempotent on idempotency_key
    out_resp1 = await client.post(
        "/api/v1/telephony/calls/outbound",
        json={
            "to_number": "+14155550199",
            "from_number": "+14155550101",
            "agent_id": str(agent.id),
            "provider": "SIMULATED",
            "idempotency_key": "out_idem_key_001",
            "is_simulation": True,
        },
        headers=headers,
    )
    assert out_resp1.status_code == 201, out_resp1.text
    call1 = out_resp1.json()
    assert call1["status"] == "DIALING"
    assert call1["direction"] == "OUTBOUND"

    out_resp2 = await client.post(
        "/api/v1/telephony/calls/outbound",
        json={
            "to_number": "+14155550199",
            "from_number": "+14155550101",
            "agent_id": str(agent.id),
            "provider": "SIMULATED",
            "idempotency_key": "out_idem_key_001",
            "is_simulation": True,
        },
        headers=headers,
    )
    assert out_resp2.status_code == 201
    assert out_resp2.json()["id"] == call1["id"]


@pytest.mark.asyncio
async def test_call_state_machine_valid_and_illegal_transitions(
    db: AsyncSession,
):
    # Valid transitions succeed
    _, targ, is_noop = validate_call_state_transition(
        TelephonyCallState.CREATED, TelephonyCallState.DIALING
    )
    assert targ == TelephonyCallState.DIALING
    assert is_noop is False

    # Same-state transition is a safe no-op
    _, _, is_noop_same = validate_call_state_transition(
        TelephonyCallState.COMPLETED, TelephonyCallState.COMPLETED
    )
    assert is_noop_same is True

    # Illegal regression from terminal state raises CallStateTransitionError
    with pytest.raises(CallStateTransitionError):
        validate_call_state_transition(
            TelephonyCallState.COMPLETED, TelephonyCallState.IN_PROGRESS
        )

    with pytest.raises(CallStateTransitionError):
        validate_call_state_transition(
            TelephonyCallState.FAILED, TelephonyCallState.ANSWERED
        )


@pytest.mark.asyncio
async def test_realtime_media_gateway_barge_in_dtmf_and_transfers(
    client: AsyncClient,
    db: AsyncSession,
):
    tenant = await make_tenant(db, "Realtime Media & Transfer Org")
    admin = await make_user(db, tenant, role=UserRole.ADMIN)
    headers = await auth_headers(client, admin)
    agent1 = await _seed_agent(db, tenant_id=tenant.id, name="Primary Intake Agent")
    agent2 = await _seed_agent(db, tenant_id=tenant.id, name="Specialist Escalation Agent")

    # 1. Start an outbound call
    out_resp = await client.post(
        "/api/v1/telephony/calls/outbound",
        json={
            "to_number": "+14155550177",
            "from_number": "+14155550101",
            "agent_id": str(agent1.id),
            "provider": "SIMULATED",
            "is_simulation": True,
            "idempotency_key": "prompt8-realtime-outbound-20261006",
        },
        headers=headers,
    )
    assert out_resp.status_code == 201
    call_id = out_resp.json()["id"]

    # 2. Start real-time media stream (transitions DIALING -> ANSWERED -> IN_PROGRESS and enqueues greeting)
    start_media = await client.post(
        f"/api/v1/telephony/calls/{call_id}/media-event",
        json={"type": "media.start", "encoding": "mulaw", "sample_rate": 8000},
        headers=headers,
    )
    assert start_media.status_code == 200, start_media.text
    assert start_media.json()["status"] == "IN_PROGRESS"
    assert start_media.json()["media_state"] == "SPEAKING"

    # 3. Send inbound audio frame with speech_detected=True to trigger barge-in (flushes outbound queue)
    sample_pcm = base64.b64encode(b"\x7f" * 160).decode("ascii")
    audio_evt = await client.post(
        f"/api/v1/telephony/calls/{call_id}/media-event",
        json={
            "type": "media.audio",
            "payload": sample_pcm,
            "timestamp_ms": 20,
            "speech_detected": True,
        },
        headers=headers,
    )
    assert audio_evt.status_code == 200, audio_evt.text
    assert audio_evt.json()["barge_in_triggered"] is True
    assert audio_evt.json()["state"] == "INTERRUPTED"

    # 4. Reject invalid audio encoding
    bad_audio = await client.post(
        f"/api/v1/telephony/calls/{call_id}/media-event",
        json={
            "type": "media.audio",
            "payload": sample_pcm,
            "encoding": "invalid_codec_xyz",
        },
        headers=headers,
    )
    assert bad_audio.status_code == 422

    # 5. Send caller utterance and verify agent response turn
    utt_resp = await client.post(
        f"/api/v1/telephony/calls/{call_id}/media-event",
        json={"type": "media.utterance", "text": "I need help with my enterprise invoice."},
        headers=headers,
    )
    assert utt_resp.status_code == 200
    assert "Understood your request" in utt_resp.json()["agent_text"]

    # 6. DTMF validation, buffering, and IVR route matching
    bad_dtmf = await client.post(
        f"/api/v1/telephony/calls/{call_id}/dtmf",
        json={"digits": "INVALID_XYZ"},
        headers=headers,
    )
    assert bad_dtmf.status_code == 422

    ok_dtmf = await client.post(
        f"/api/v1/telephony/calls/{call_id}/dtmf",
        json={"digits": "3#", "source": "caller"},
        headers=headers,
    )
    assert ok_dtmf.status_code == 200, ok_dtmf.text
    dtmf_body = ok_dtmf.json()
    assert dtmf_body["dtmf_buffer"] == "3#"
    assert dtmf_body["matched_route"]["department"] == "billing"

    # 7. Warm transfer with simulated target failure and RETURN_TO_AGENT fallback
    fallback_xfer = await client.post(
        f"/api/v1/telephony/calls/{call_id}/transfer",
        json={
            "mode": "WARM",
            "target_destination": "+14155550000",
            "whisper_message": "Customer has an enterprise billing question.",
            "fallback_action": "RETURN_TO_AGENT",
            "simulate_target_failure": True,
        },
        headers=headers,
    )
    assert fallback_xfer.status_code == 200, fallback_xfer.text
    fb_data = fallback_xfer.json()
    assert fb_data["status"] == "FALLBACK_RETURNED"

    # Call remains IN_PROGRESS after RETURN_TO_AGENT fallback
    call_after_fb = await client.get(
        f"/api/v1/telephony/calls/{call_id}", headers=headers
    )
    assert call_after_fb.json()["status"] == "IN_PROGRESS"

    # 8. Agent-to-Agent transfer with full context preservation
    agent_xfer = await client.post(
        f"/api/v1/telephony/calls/{call_id}/transfer",
        json={
            "mode": "AGENT_TO_AGENT",
            "target_agent_id": str(agent2.id),
            "whisper_message": "Handing off billing dispute with full transcript context.",
            "reason": "specialist_escalation",
        },
        headers=headers,
    )
    assert agent_xfer.status_code == 200, agent_xfer.text
    ax_data = agent_xfer.json()
    assert ax_data["status"] == "COMPLETED"
    assert ax_data["target_agent_id"] == str(agent2.id)
    assert ax_data["context_snapshot"]["source_agent_id"] == str(agent1.id)
    assert ax_data["context_snapshot"]["dtmf_buffer"] == "3#"
    assert ax_data["context_snapshot"]["transcript_turns_count"] >= 3

    # 9. Hangup call and verify terminal state + finalized usage
    hangup_resp = await client.post(
        f"/api/v1/telephony/calls/{call_id}/hangup",
        json={"reason": "resolved_after_specialist_transfer"},
        headers=headers,
    )
    assert hangup_resp.status_code == 200
    final_call = hangup_resp.json()
    assert final_call["status"] == "COMPLETED"
    assert final_call["usage_finalized"] is True
    assert media_gateway_manager.get_session(final_call["id"]) is None
````

### File 18: `dashboard/src/pages/product/voice-agents/VoiceAgentsBuilderPreview.tsx`

SHA-256: `c36931a75967b664152ea9ba27701953b0a40d276f19bef5a61cb418ce0c8252`

````
/** Public workflow navigation, not a saved agent or a live provider session. */
import React, { useState } from 'react';
import { GlassCard } from '../../../components/ui/GlassCard';

interface BuilderStep {
  id: string;
  title: string;
  description: string;
  detail: string;
  href: string;
  linkLabel: string;
}

const BUILDER_STEPS: BuilderStep[] = [
  {
    id: 'voice', title: 'Voice & Language',
    description: 'Review voice and language configuration',
    detail: 'Select an existing agent in the authenticated workspace. Available voices depend on the provider configuration; this overview does not verify a voice or save a selection.',
    href: '/dashboard/agents', linkLabel: 'Open agent workspace',
  },
  {
    id: 'knowledge', title: 'Knowledge Base',
    description: 'Review documents and retrieval settings',
    detail: 'Inspect uploaded sources and their persisted indexing status in the workspace. No sample document shown here is represented as an indexed source.',
    href: '/dashboard/agents', linkLabel: 'Choose an agent for knowledge settings',
  },
  {
    id: 'tools', title: 'Tools & Integrations',
    description: 'Review tool schemas and provider connections',
    detail: 'Configure tools on an existing agent and verify each connection separately. A tool schema does not prove that a third-party action has succeeded.',
    href: '/dashboard/agents', linkLabel: 'Choose an agent for tool settings',
  },
  {
    id: 'routing', title: 'Call Routing & IVR',
    description: 'Review number and routing configuration',
    detail: 'Inspect the tenant-owned phone number, environment, and published agent version. Live inbound calls require a configured carrier and runtime.',
    href: '/dashboard/phone-numbers', linkLabel: 'Open phone-number console',
  },
  {
    id: 'test', title: 'Test & Validate',
    description: 'Inspect persisted simulation runs',
    detail: 'Run and inspect tests in the authenticated workspace. A deterministic simulation is not evidence of a successful live phone call.',
    href: '/dashboard/simulations', linkLabel: 'Open simulation workspace',
  },
];

export function VoiceAgentsBuilderPreview() {
  const [activeId, setActiveId] = useState('tools');
  const activeStep = BUILDER_STEPS.find((step) => step.id === activeId)!;

  return (
    <section className="mx-auto max-w-7xl px-4 sm:px-6 lg:px-8 py-16 sm:py-24">
      <div className="max-w-3xl">
        <h2 className="text-3xl font-bold text-white sm:text-4xl">Builder workflow overview</h2>
        <p className="mt-4 text-[15px] text-white/60">
          Explore configuration steps. This public overview does not create an agent, save changes, index documents, or initiate a call.
        </p>
      </div>
      <div className="mt-12 grid gap-8 lg:grid-cols-3">
        <div className="space-y-3" aria-label="Builder workflow steps">
          {BUILDER_STEPS.map((step, index) => (
            <button
              key={step.id}
              type="button"
              aria-pressed={activeId === step.id}
              aria-controls="builder-workflow-detail"
              onClick={() => setActiveId(step.id)}
              className={`w-full text-left rounded-[14px] border p-4 transition-colors ${activeId === step.id ? 'bg-white text-black border-white' : 'bg-white/[0.03] border-white/10 text-white/60 hover:bg-white/[0.05]'}`}
            >
              <div className="text-sm font-medium">{index + 1}. {step.title}</div>
              <div className="mt-1 text-[11px] opacity-70">{step.description}</div>
            </button>
          ))}
        </div>
        <div className="lg:col-span-2">
          <GlassCard className="p-6">
            <div id="builder-workflow-detail" aria-live="polite">
              <p className="text-xs text-white/50">Workflow guidance — no agent selected</p>
              <h3 className="mt-3 text-xl font-semibold text-white">{activeStep.title}</h3>
              <p className="mt-4 text-sm leading-relaxed text-white/60">{activeStep.detail}</p>
              <a className="mt-6 inline-block rounded-xl bg-white px-4 py-2 text-xs text-black" href={activeStep.href}>
                {activeStep.linkLabel}
              </a>
            </div>
          </GlassCard>
        </div>
      </div>
    </section>
  );
}

export default VoiceAgentsBuilderPreview;
````

### File 19: `dashboard/src/tests/voice-agents.test.tsx`

SHA-256: `9a55f92ac75fa3b2181cf2bd55f5a41a9000856325a6a400fa10ec0f470a39ba`

````
/**
 * dashboard/src/tests/voice-agents.test.tsx
 * Production tests for Voice AI / Phone Agents — real behavior, no fake data
 * Full structure: Voice AI explanation, build→test→deploy→monitor, call routing, IVR, transfers, outbound
 * No shortening, full code from start to end
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { VoiceAgentsHero } from '../pages/product/voice-agents/VoiceAgentsHero';
import { VoiceAgentsLifecycle } from '../pages/product/voice-agents/VoiceAgentsLifecycle';
import { VoiceAgentsCapabilities } from '../pages/product/voice-agents/VoiceAgentsCapabilities';
import { VoiceAgentsBuilderPreview } from '../pages/product/voice-agents/VoiceAgentsBuilderPreview';
import { VoiceAgentsUseCases } from '../pages/product/voice-agents/VoiceAgentsUseCases';
import { VoiceAgentsComparison } from '../pages/product/voice-agents/VoiceAgentsComparison';
import { VoiceAgentsDeveloper } from '../pages/product/voice-agents/VoiceAgentsDeveloper';
import { VoiceAgentsEnterprise } from '../pages/product/voice-agents/VoiceAgentsEnterprise';
import { VoiceAgentsSecurity } from '../pages/product/voice-agents/VoiceAgentsSecurity';
import { VoiceAgentsFAQ } from '../pages/product/voice-agents/VoiceAgentsFAQ';
import { VoiceAgentsCTA } from '../pages/product/voice-agents/VoiceAgentsCTA';
import { VoiceAgentsPage } from '../pages/product/voice-agents/VoiceAgentsPage';

vi.mock('../hooks/useHomeData', () => ({
  useHomeData: () => ({
    homeData: {
      capabilities: [{ id: 'voice', title: 'Voice', description: 'Natural voice', icon: '🎙️' }],
      developer_features: [{ id: 'api', title: 'API', description: 'REST API' }],
      security_items: [{ id: 'soc2', title: 'SOC 2', description: 'Compliant' }],
    },
    loading: false,
    error: null,
  }),
}));

const LIFECYCLE_STEPS = [
  { id: 'build', order: 1, title: 'Build — Create voice agents', description: 'Build description', shortTitle: 'BUILD', icon: '🛠️', color: 'from-blue-500 to-cyan-500', features: ['Voice', 'Knowledge'], cta: { label: 'Start Building', href: '/dashboard/agents/new' } },
  { id: 'test', order: 2, title: 'Test — Simulate calls', description: 'Test description', shortTitle: 'TEST', icon: '🧪', color: 'from-violet-500 to-purple-500', features: ['Simulation'], cta: { label: 'Test', href: '/dashboard/agents' } },
  { id: 'deploy', order: 3, title: 'Deploy — Go live', description: 'Deploy description', shortTitle: 'DEPLOY', icon: '🚀', color: 'from-emerald-500 to-teal-500', features: ['Phone numbers'], cta: { label: 'Deploy', href: '/dashboard/agents' } },
  { id: 'monitor', order: 4, title: 'Monitor — Real-time', description: 'Monitor description', shortTitle: 'MONITOR', icon: '📊', color: 'from-amber-500 to-orange-500', features: ['Analytics'], cta: { label: 'Monitor', href: '/dashboard/analytics' } },
  { id: 'improve', order: 5, title: 'Improve — Iterate', description: 'Improve description', shortTitle: 'IMPROVE', icon: '📈', color: 'from-pink-500 to-rose-500', features: ['A/B testing'], cta: { label: 'Improve', href: '/dashboard/agents' } },
];

describe('Voice Agents — AI Voice / Phone Agents', () => {
  it('renders hero with Voice AI explanation', () => {
    render(<VoiceAgentsHero onSeeHowItWorks={vi.fn()} />);
    expect(screen.getAllByText(/Build voice agents that/i).length).toBeGreaterThan(0);
  });

  it('shows build→test→deploy→monitor in hero', () => {
    render(<VoiceAgentsHero />);
    const body = document.body.textContent || '';
    expect(body).toContain('CREATE');
    expect(body).toContain('DEPLOY');
    expect(body).toContain('MONITOR');
  });

  it('renders lifecycle steps and truthful A/B API limits', () => {
    const activeStep = LIFECYCLE_STEPS[4];
    render(<VoiceAgentsLifecycle steps={LIFECYCLE_STEPS} activeId="improve" onChange={vi.fn()} activeStep={activeStep} />);
    expect(screen.getAllByText(/BUILD/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/TEST/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/DEPLOY/i).length).toBeGreaterThan(0);

    const content = document.body.textContent || '';
    expect(content).toContain('POST /api/experiments');
    expect(content).toContain('live call assignment is not connected');
    expect(content).not.toContain('/api/ab-testing');
    expect(content).not.toContain('Each phase verified with real provider integration');
  });

  it('handles lifecycle step change', () => {
    const onChange = vi.fn();
    const activeStep = LIFECYCLE_STEPS[0];
    render(<VoiceAgentsLifecycle steps={LIFECYCLE_STEPS} activeId="build" onChange={onChange} activeStep={activeStep} />);
    const testButton = screen.getAllByText('TEST')[0];
    fireEvent.click(testButton);
    expect(onChange).toHaveBeenCalled();
  });

  it('renders capabilities with IVR, transfers, outbound', () => {
    render(<VoiceAgentsCapabilities />);
    expect(screen.getAllByText(/IVR/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Transfer/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Outbound/i).length).toBeGreaterThan(0);
  });

  it('renders honest builder guidance and changes the selected workflow', () => {
    render(<VoiceAgentsBuilderPreview />);
    expect(screen.getByRole('heading', { name: 'Builder workflow overview' })).toBeInTheDocument();
    expect(screen.getByText(/does not create an agent, save changes/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Choose an agent for tool settings' })).toHaveAttribute('href', '/dashboard/agents');
    const routing = screen.getByRole('button', { name: /Call Routing & IVR/ });
    fireEvent.click(routing);
    expect(routing).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('link', { name: 'Open phone-number console' })).toHaveAttribute('href', '/dashboard/phone-numbers');
    fireEvent.click(screen.getByRole('button', { name: /Test & Validate/ }));
    expect(screen.getByRole('link', { name: 'Open simulation workspace' })).toHaveAttribute('href', '/dashboard/simulations');
    expect(document.body.textContent).not.toContain('Real Backend');
    expect(document.body.textContent).not.toContain('Indexed');
  });

  it('renders use cases section', () => {
    render(<VoiceAgentsUseCases />);
    expect(screen.getAllByText(/Use Cases/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/AI Receptionist/i).length).toBeGreaterThan(0);
  });

  it('renders comparison', () => {
    render(<VoiceAgentsComparison />);
    expect(screen.getAllByText(/Why VoxDesk/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Real Telephony/i).length).toBeGreaterThan(0);
  });

  it('renders developer section with API, SDK, Webhooks, Tools', () => {
    render(<VoiceAgentsDeveloper />);
    expect(screen.getAllByText(/Developer First/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/REST API/i).length).toBeGreaterThan(0);
  });

  it('shows API examples for outbound, transfer, DTMF', () => {
    render(<VoiceAgentsDeveloper />);
    const body = document.body.textContent || '';
    expect(body).toContain('/api/calls');
    expect(body).toContain('/transfer');
    expect(body).toContain('/dtmf');
  });

  it('renders enterprise section', () => {
    render(<VoiceAgentsEnterprise />);
    expect(screen.getAllByText(/Enterprise Ready/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/SOC 2/i).length).toBeGreaterThan(0);
  });

  it('renders security section', () => {
    render(<VoiceAgentsSecurity />);
    expect(screen.getAllByText(/Security/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Recording/i).length).toBeGreaterThan(0);
  });

  it('renders FAQ', () => {
    render(<VoiceAgentsFAQ />);
    expect(screen.getAllByText(/FAQ/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/What is Voice AI/i).length).toBeGreaterThan(0);
  });

  it('renders CTA', () => {
    render(<VoiceAgentsCTA />);
    expect(screen.getAllByText(/Build your first voice agent/i).length).toBeGreaterThan(0);
  });

  it('renders the public page with honest workflow boundaries and working workspace routes', () => {
    render(<VoiceAgentsPage />);
    expect(screen.getByRole('heading', { name: 'Build and inspect voice-agent workflows' })).toBeInTheDocument();
    expect(screen.getByText('Configure an agent')).toBeInTheDocument();
    expect(screen.getByText('Test before deployment')).toBeInTheDocument();
    expect(screen.getByText('Connect a phone number')).toBeInTheDocument();
    expect(screen.getByText('Review persisted calls')).toBeInTheDocument();
    expect(screen.getByText(/Route presence does not prove that a provider is configured/i)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Open agent workspace' })).toHaveAttribute('href', '/dashboard/agents');
  });

  it('distinguishes workflow examples from a live caller-to-agent call flow', () => {
    render(<VoiceAgentsPage />);
    const body = document.body.textContent || '';
    expect(body).toContain('Illustrative patterns');
    expect(body).toContain('They do not assert that a third-party provider is connected');
    expect(body).not.toContain('Caller → IVR → Voice Agent → Knowledge → Tools → Business System → Human');
  });

  it('never shows fake metrics', () => {
    render(<VoiceAgentsPage />);
    const body = document.body.textContent || '';
    expect(body).not.toContain('100% success rate');
    expect(body).not.toContain('10x ROI');
  });

  it('validates E.164 format', () => {
    const valid = ['+12345678901', '+441632960961'];
    const regex = /^\+[1-9]\d{7,14}$/;
    valid.forEach(n => expect(regex.test(n)).toBe(true));
  });

  it('validates DTMF digits', () => {
    const valid = ['123', '1#*', '0'];
    const regex = /^[0-9#*wW]+$/;
    valid.forEach(d => expect(regex.test(d)).toBe(true));
  });
});


````

### File 20: `.prompt8-validation-final/remaining-execution/run.py`

SHA-256: `15191e5a03662333f31f3bac9486f5d397913c77fb0049238e1e126f867b1df7`

````
import json
from pathlib import Path
from tests.test_memory_safe_final_validation import run_chunked_validation
root=Path.cwd()
folder=root/'.prompt8-validation-final/remaining-execution'
result=run_chunked_validation(root=root,targets=json.loads((folder/'targets.json').read_text()),max_tests=30,timeout_seconds=600,output_dir=folder)
print(json.dumps(result,indent=2))
````

### File 21: `.prompt8-validation-final/repair-execution/run.py`

SHA-256: `dc90ade5aee564f1a3452655ab4227ecfe719bd7c72b47525fcc9b0705f15d1a`

````
import json
from pathlib import Path
from tests.test_memory_safe_final_validation import run_chunked_validation
root=Path.cwd()
folder=root/'.prompt8-validation-final/repair-execution'
result=run_chunked_validation(root=root,targets=json.loads((folder/'targets.json').read_text()),max_tests=5,timeout_seconds=180,output_dir=folder)
print(json.dumps(result,indent=2))
````

### File 22: `.prompt8-validation-final/repair-tail/run.py`

SHA-256: `9908efb9370f2feca90fbb02fda89008804e404f9690c692e70901c51491a717`

````
import json
from collections import defaultdict
from pathlib import Path
from tests.test_memory_safe_final_validation import run_chunked_validation
root=Path.cwd()
base=root/'.prompt8-validation-final'
targets=json.loads((base/'repair-execution/targets.json').read_text())[455:]
groups=defaultdict(list)
for node in targets:
    groups[node.split('::')[0]].append(node)
for index,(module,nodes) in enumerate(groups.items(),1):
    folder=base/'repair-tail'/f'lane-{index:02}'
    result=run_chunked_validation(root=root,targets=nodes,max_tests=5,timeout_seconds=180,output_dir=folder)
    print(module,result['passed'],result['failed'],result['skipped'],result['unconfirmed'],flush=True)
````

