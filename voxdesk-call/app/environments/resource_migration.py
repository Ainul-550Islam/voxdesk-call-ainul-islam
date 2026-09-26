"""Verify an environment-scope backfill. This module does not delete rows."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

SCOPED_TABLES = (
    "calls",
    "leads",
    "appointments",
    "knowledge_documents",
    "knowledge_chunks",
    "automations",
    "automation_runs",
    "notifications",
    "inbox_thread_states",
    "usage_events",
)


def backfill_sql(table: str) -> str:
    return (
        f"UPDATE {table} SET environment_id = ("
        "SELECT e.id FROM environments e "
        f"WHERE e.tenant_id = {table}.tenant_id AND e.kind = 'production' "
        "ORDER BY e.is_default DESC LIMIT 1) "
        "WHERE environment_id IS NULL"
    )


async def count_unbound(session: AsyncSession, table: str) -> int:
    if table not in SCOPED_TABLES:
        raise ValueError("unsupported table")
    result = await session.execute(text(f"SELECT COUNT(*) FROM {table} WHERE environment_id IS NULL"))
    return int(result.scalar_one())


async def count_mismatched(session: AsyncSession, table: str) -> int:
    if table not in SCOPED_TABLES:
        raise ValueError("unsupported table")
    result = await session.execute(text(
        f"SELECT COUNT(*) FROM {table} r "
        "LEFT JOIN environments e ON e.id = r.environment_id "
        "WHERE r.environment_id IS NOT NULL AND "
        "(e.id IS NULL OR e.tenant_id != r.tenant_id)"
    ))
    return int(result.scalar_one())


async def verify(session: AsyncSession) -> dict:
    unbound = {}
    mismatched = {}
    for table in SCOPED_TABLES:
        unbound[table] = await count_unbound(session, table)
        mismatched[table] = await count_mismatched(session, table)
    missing_production = int((await session.execute(text(
        "SELECT COUNT(*) FROM tenants t WHERE NOT EXISTS ("
        "SELECT 1 FROM environments e WHERE e.tenant_id = t.id AND e.kind = 'production')"
    ))).scalar_one())
    return {
        "unbound": unbound,
        "mismatched": mismatched,
        "tenants_missing_production": missing_production,
        "ready": missing_production == 0 and not any(unbound.values()) and not any(mismatched.values()),
    }
