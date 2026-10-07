"""Session liveness must agree for PostgreSQL-aware and SQLite-naive UTC."""
from datetime import datetime, timedelta, timezone

import pytest

from app.auth.identity.models import UserSession


@pytest.mark.parametrize("stored_aware", [False, True])
@pytest.mark.parametrize("clock_aware", [False, True])
def test_session_liveness_normalizes_database_and_clock(stored_aware, clock_aware):
    now = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
    stored_now = now if stored_aware else now.replace(tzinfo=None)
    clock = now if clock_aware else now.replace(tzinfo=None)
    session = UserSession(expires_at=stored_now + timedelta(hours=1),
                          idle_expires_at=stored_now + timedelta(minutes=10), revoked_at=None)
    assert session.is_live(clock)
    assert not session.is_live(clock + timedelta(minutes=10))


@pytest.mark.parametrize("boundary", ["expires_at", "idle_expires_at"])
def test_non_utc_offsets_do_not_extend_expired_session(boundary):
    now = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
    session = UserSession(expires_at=now + timedelta(hours=1),
                          idle_expires_at=now + timedelta(minutes=10), revoked_at=None)
    setattr(session, boundary, now.astimezone(timezone(timedelta(hours=6))))
    assert not session.is_live(now.replace(tzinfo=None))


def test_revoked_session_stays_revoked():
    now = datetime.now(timezone.utc)
    session = UserSession(expires_at=now + timedelta(hours=1),
                          idle_expires_at=now + timedelta(minutes=10), revoked_at=now)
    assert not session.is_live(now)
