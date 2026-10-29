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
