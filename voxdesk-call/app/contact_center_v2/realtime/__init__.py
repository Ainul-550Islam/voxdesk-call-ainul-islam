"""Authenticated live assist and supervisor projections."""
from .supervisor import supervisor_snapshot
from .wallboard import WallboardStreamState, without_stream_clock
from .websocket import (
    RealtimePrincipal,
    RealtimeTicketError,
    active_realtime_user,
    issue_realtime_ticket,
    verify_realtime_ticket,
)

__all__ = [
    "RealtimePrincipal",
    "RealtimeTicketError",
    "WallboardStreamState",
    "active_realtime_user",
    "issue_realtime_ticket",
    "supervisor_snapshot",
    "verify_realtime_ticket",
    "without_stream_clock",
]
