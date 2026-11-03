"""UTC datetime helpers.

C2 moved application clocks to timezone-aware UTC. SQLite (the pytest
database) still round-trips ``DateTime(timezone=True)`` as naive values, and
a few call sites still mix the two. Every comparison and duration must go
through ``as_utc`` so production Postgres and local SQLite behave the same.
"""
from __future__ import annotations

from datetime import datetime, timezone

UTC = timezone.utc


def utc_now() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime | None) -> datetime | None:
    """Return ``value`` as an aware UTC datetime, treating naive as UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
