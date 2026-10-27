"""Fixtures for the Batch 07 platform tests.

Everything here is built from the repository's real models and the real application:
the tenant, the environment, the users and the roster rows are rows in the same
in-memory database the API routes read. Nothing is mocked that the production code
would call, because a mocked tenant-scoping check proves nothing about tenant
isolation.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Iterable, Sequence

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Environment, Tenant, User, UserRole
from app.db.platform_v2_models import ShiftTemplate, WfmIntervalHistory, WfmShift
from app.platform_v2.wfm.history import interval_grid, tenant_zone
from tests.conftest import auth_headers, make_user


@dataclass
class PlatScope:
    tenant: Tenant
    environment: Environment


async def _ensure_production_environment(db: AsyncSession, tenant: Tenant) -> Environment:
    """Return the tenant's production environment, creating it once if absent.

    Reuse before insert: ``UNIQUE (tenant_id, kind)`` means a fixture that always
    inserts would collide with the row another fixture already made.
    """
    existing = (
        await db.execute(
            select(Environment).where(
                Environment.tenant_id == tenant.id, Environment.kind == "production"
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    environment = Environment(
        tenant_id=tenant.id,
        name="Production",
        slug="production",
        kind="production",
        status="active",
        # The guards are single-row uniqueness locks on the real production row;
        # this fixture does not exercise that invariant, so they stay NULL.
        production_guard=None,
    )
    db.add(environment)
    await db.commit()
    await db.refresh(environment)
    return environment


@pytest_asyncio.fixture
async def plat_scope(db: AsyncSession, tenant_a: Tenant) -> PlatScope:
    """A tenant with its production environment.

    The environment is created here rather than borrowed from another package's
    fixture so this suite does not depend on a conftest it does not own.
    """
    return PlatScope(
        tenant=tenant_a, environment=await _ensure_production_environment(db, tenant_a)
    )


@pytest_asyncio.fixture
async def plat_scope_b(db: AsyncSession, tenant_b: Tenant) -> PlatScope:
    return PlatScope(
        tenant=tenant_b, environment=await _ensure_production_environment(db, tenant_b)
    )


@pytest_asyncio.fixture
async def wfm_agent(db: AsyncSession, plat_scope: PlatScope) -> User:
    return await make_user(
        db, plat_scope.tenant, UserRole.AGENT, email=f"wfm-agent-{uuid.uuid4().hex[:8]}@example.com"
    )


@pytest_asyncio.fixture
async def wfm_manager(db: AsyncSession, plat_scope: PlatScope) -> User:
    return await make_user(
        db, plat_scope.tenant, UserRole.MANAGER, email=f"wfm-mgr-{uuid.uuid4().hex[:8]}@example.com"
    )


@pytest_asyncio.fixture
async def agent_b(db: AsyncSession, plat_scope: PlatScope) -> User:
    """A second agent in the same tenant, for swap and bidding tests."""
    return await make_user(
        db, plat_scope.tenant, UserRole.AGENT, email=f"wfm-agent-b-{uuid.uuid4().hex[:8]}@example.com"
    )


@pytest_asyncio.fixture
async def agent_headers_b(client, agent_b: User) -> dict[str, str]:
    return await auth_headers(client, agent_b)


@pytest_asyncio.fixture
async def owner_headers(client, owner_a: User) -> dict[str, str]:
    return await auth_headers(client, owner_a)


@pytest_asyncio.fixture
async def agent_headers(client, wfm_agent: User) -> dict[str, str]:
    return await auth_headers(client, wfm_agent)


def history_rows(
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    start_date: date,
    days: int,
    interval_minutes: int = 30,
    zone_name: str = "Asia/Dhaka",
    queue_key: str = "",
    seed: int = 7,
    weekday_profile: dict[int, float] | None = None,
    base_volume: float = 40.0,
    active_hours: Sequence[int] = tuple(range(8, 21)),
) -> list[dict[str, Any]]:
    """Deterministic interval history with a known shape.

    The generator is seeded so a failing assertion is reproducible, and the shape is
    built from an explicit weekday multiplier and a 09:00 spike, so a test can assert
    the model learned the pattern it was shown rather than that it produced a number.
    """
    rng = random.Random(seed)
    day_factor = weekday_profile or {0: 1.6, 5: 0.5, 6: 0.5}
    zone = tenant_zone(zone_name)
    grid = interval_grid(zone_name, start_date, start_date + timedelta(days=days - 1), interval_minutes)
    rows: list[dict[str, Any]] = []
    for slot in grid:
        local = slot.start.astimezone(zone)
        multiplier = day_factor.get(local.weekday(), 1.0)
        slot_multiplier = 1.0 + (0.8 if local.hour == 9 else 0.0)
        if local.hour not in active_hours:
            offered = 0
        else:
            offered = max(0, int(round(base_volume * multiplier * slot_multiplier + rng.gauss(0, 2))))
        handled = int(round(offered * 0.92))
        rows.append(
            {
                "tenant_id": tenant_id,
                "environment_id": environment_id,
                "queue_key": queue_key,
                "interval_start": slot.start,
                "interval_minutes": interval_minutes,
                "offered": offered,
                "handled": handled,
                "abandoned": offered - handled,
                "total_handle_seconds": round(handled * 210.0, 2),
                "total_speed_answer_seconds": round(handled * 18.0, 2),
                "agents_scheduled": 0,
                "agents_available": 0,
                "is_gap": False,
                "source": "rollup",
                "computed_at": datetime.now(tz=UTC),
            }
        )
    return rows


async def seed_history(
    db: AsyncSession,
    scope: PlatScope,
    *,
    start_date: date | None = None,
    days: int = 28,
    **kwargs: Any,
) -> list[WfmIntervalHistory]:
    """Insert history rows and return the ORM objects."""
    first = start_date or (datetime.now(tz=UTC).date() - timedelta(days=days + 1))
    rows = history_rows(
        tenant_id=scope.tenant.id,
        environment_id=scope.environment.id,
        start_date=first,
        days=days,
        **kwargs,
    )
    objects = [WfmIntervalHistory(**row) for row in rows]
    db.add_all(objects)
    await db.commit()
    return objects


async def seed_templates(
    db: AsyncSession, scope: PlatScope, *, count: int = 3
) -> list[ShiftTemplate]:
    shapes = [
        ("early", 7 * 60, 8 * 60),
        ("mid", 9 * 60, 8 * 60),
        ("late", 11 * 60, 8 * 60),
        ("night", 21 * 60, 8 * 60),
    ][:count]
    rows = [
        ShiftTemplate(
            tenant_id=scope.tenant.id,
            name=name,
            start_minute=start,
            length_minutes=length,
            paid_break_minutes=15,
            unpaid_break_minutes=30,
            break_rules=[{"after_minutes": 240, "minutes": 30, "paid": False}],
            required_skill_key="",
            is_active=True,
        )
        for name, start, length in shapes
    ]
    db.add_all(rows)
    await db.commit()
    return rows


async def seed_shift(
    db: AsyncSession,
    scope: PlatScope,
    user: User,
    *,
    starts_at: datetime | None = None,
    hours: int = 8,
    status: str = "published",
) -> WfmShift:
    start = starts_at or (datetime.now(tz=UTC).replace(minute=0, second=0, microsecond=0) + timedelta(hours=2))
    shift = WfmShift(
        tenant_id=scope.tenant.id,
        environment_id=scope.environment.id,
        user_id=user.id,
        starts_at=start,
        ends_at=start + timedelta(hours=hours),
        break_minutes=45,
        status=status,
        skill_key="",
    )
    db.add(shift)
    await db.commit()
    return shift


@pytest_asyncio.fixture
async def staffed_scope(db: AsyncSession, plat_scope: PlatScope) -> PlatScope:
    """A tenant with templates and four agents, ready for a solve."""
    await seed_templates(db, plat_scope)
    for index in range(4):
        await make_user(
            db,
            plat_scope.tenant,
            UserRole.AGENT,
            email=f"agent-{index}-{uuid.uuid4().hex[:6]}@example.com",
        )
    return plat_scope


def week_start_for(moment: datetime | None = None) -> date:
    """The Monday of the week containing ``moment`` (UTC)."""
    current = (moment or datetime.now(tz=UTC)).date()
    return current - timedelta(days=current.weekday())


def fixed_window(day: date, hour: int = 9, minutes: int = 60) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time(hour, 0), tzinfo=UTC)
    return start, start + timedelta(minutes=minutes)


def iso(moment: datetime) -> str:
    return moment.isoformat()


def as_list(payload: Iterable[Any]) -> list[Any]:
    return list(payload)
