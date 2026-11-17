"""Dynamic variable definition and resolution service (Retell parity).

Manages tenant-wide and chat-agent-scoped ``DynamicVariableDefinition`` rows and
resolves runtime variable payloads with type and constraint validation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import log
from app.db.models import Tenant
from app.db.retell_models import DynamicVariableDefinition
from app.domain.dynamic_variable_models import (
    DynamicVariableCreate,
    DynamicVariableUpdate,
    validate_runtime_value,
    validate_variable_name,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _tenant_id(tenant: Tenant | UUID) -> UUID:
    return tenant.id if isinstance(tenant, Tenant) else UUID(str(tenant))


async def create(
    session: AsyncSession,
    tenant: Tenant | UUID,
    payload: DynamicVariableCreate,
    *,
    created_by: UUID | None = None,
) -> DynamicVariableDefinition:
    tid = _tenant_id(tenant)
    name = validate_variable_name(payload.name)
    now = _now()
    row = DynamicVariableDefinition(
        tenant_id=tid,
        chat_agent_id=payload.chat_agent_id,
        name=name,
        var_type=payload.var_type.value if hasattr(payload.var_type, "value") else str(payload.var_type),
        description=payload.description.strip(),
        default_value=dict(payload.default_value or {}),
        constraints=dict(payload.constraints or {}),
        is_required=bool(payload.is_required),
        is_nullable=bool(payload.is_nullable),
        created_by=created_by,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    await session.flush()
    log.info("dynamic_variable.created", tenant_id=str(tid), variable_id=str(row.id), name=row.name)
    return row


async def get_by_id(
    session: AsyncSession,
    tenant: Tenant | UUID,
    variable_id: UUID | str,
) -> DynamicVariableDefinition | None:
    tid = _tenant_id(tenant)
    vid = UUID(str(variable_id))
    stmt = select(DynamicVariableDefinition).where(
        DynamicVariableDefinition.tenant_id == tid,
        DynamicVariableDefinition.id == vid,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_variables(
    session: AsyncSession,
    tenant: Tenant | UUID,
    *,
    chat_agent_id: UUID | None = None,
    include_agent_scoped_when_unfiltered: bool = True,
) -> list[DynamicVariableDefinition]:
    tid = _tenant_id(tenant)
    stmt = select(DynamicVariableDefinition).where(DynamicVariableDefinition.tenant_id == tid)
    if chat_agent_id is not None:
        stmt = stmt.where(
            (DynamicVariableDefinition.chat_agent_id == chat_agent_id)
            | (DynamicVariableDefinition.chat_agent_id.is_(None))
        )
    elif not include_agent_scoped_when_unfiltered:
        stmt = stmt.where(DynamicVariableDefinition.chat_agent_id.is_(None))
    stmt = stmt.order_by(DynamicVariableDefinition.name.asc())
    return list((await session.execute(stmt)).scalars().all())


async def update(
    session: AsyncSession,
    tenant: Tenant | UUID,
    variable_id: UUID | str,
    payload: DynamicVariableUpdate,
) -> DynamicVariableDefinition | None:
    row = await get_by_id(session, tenant, variable_id)
    if row is None:
        return None
    if payload.description is not None:
        row.description = payload.description.strip()
    if payload.default_value is not None:
        row.default_value = dict(payload.default_value)
    if payload.constraints is not None:
        row.constraints = dict(payload.constraints)
    if payload.is_required is not None:
        row.is_required = bool(payload.is_required)
    if payload.is_nullable is not None:
        row.is_nullable = bool(payload.is_nullable)
    row.updated_at = _now()
    await session.flush()
    return row


async def delete_variable(
    session: AsyncSession,
    tenant: Tenant | UUID,
    variable_id: UUID | str,
) -> bool:
    tid = _tenant_id(tenant)
    vid = UUID(str(variable_id))
    stmt = delete(DynamicVariableDefinition).where(
        DynamicVariableDefinition.tenant_id == tid,
        DynamicVariableDefinition.id == vid,
    )
    res = await session.execute(stmt)
    return bool(res.rowcount and res.rowcount > 0)


async def resolve_variables(
    session: AsyncSession,
    tenant: Tenant | UUID,
    provided: dict[str, Any] | None = None,
    *,
    chat_agent_id: UUID | None = None,
) -> dict[str, Any]:
    """Resolve and validate runtime dynamic variables against tenant definitions."""
    defs = await list_variables(
        session,
        tenant,
        chat_agent_id=chat_agent_id,
        include_agent_scoped_when_unfiltered=False,
    )
    runtime = dict(provided or {})
    resolved: dict[str, Any] = {}
    for d in defs:
        if d.name in runtime:
            val = runtime[d.name]
        elif "value" in (d.default_value or {}):
            val = d.default_value["value"]
        elif d.is_required:
            raise ValueError(f"missing required dynamic variable {d.name!r}")
        else:
            continue
        resolved[d.name] = validate_runtime_value(
            d.var_type,
            val,
            constraints=d.constraints,
            is_nullable=d.is_nullable,
        )
    return resolved

# ---------------------------------------------------------------------------
# PART 12 (P12-22) — system dynamic variables
# ---------------------------------------------------------------------------

#: Variables a create-call / update-call request may override.
OVERRIDABLE_SYSTEM_VARIABLES: frozenset[str] = frozenset(
    {
        "current_time",
        "current_time_zone",
        "call_type",
        "user_number",
        "agent_number",
    }
)

#: Variables the platform owns outright. Attempting to override these is an error, not a silent
#: drop: a caller that thinks it changed the direction should be told it did not.
PROTECTED_SYSTEM_VARIABLES: frozenset[str] = frozenset({"direction"})

CALL_TYPES: tuple[str, ...] = ("inbound", "outbound", "web", "sms")

DEFAULT_TZ = "UTC"

_TZ_VAR_RE = re.compile(r"^current_(time|hour)_([A-Za-z_]+(?:/[A-Za-z_+-]+)?)$")


class SystemVariableError(ValueError):
    """An unknown, unresolvable or protected system variable."""


def resolve_tz(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError) as exc:
        raise SystemVariableError(f"unknown time zone {name!r}") from exc


@dataclass(frozen=True)
class SystemVariableContext:
    """Everything the system variables need, all injected."""

    now: datetime
    direction: str = "inbound"
    call_type: str = "inbound"
    user_number: str = ""
    agent_number: str = ""
    default_tz: str = DEFAULT_TZ

    def __post_init__(self) -> None:
        if self.now.tzinfo is None:
            raise SystemVariableError("`now` must be timezone-aware")
        if self.direction not in {"inbound", "outbound"}:
            raise SystemVariableError("direction must be inbound or outbound")
        if self.call_type not in CALL_TYPES:
            raise SystemVariableError(f"call_type must be one of {list(CALL_TYPES)}")


def system_variables(context: SystemVariableContext) -> dict[str, Any]:
    """The base set, before any per-call override."""
    local = context.now.astimezone(resolve_tz(context.default_tz))
    return {
        "current_time": local.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "current_time_zone": context.default_tz,
        "current_hour": str(local.hour),
        "call_type": context.call_type,
        "direction": context.direction,
        "user_number": context.user_number,
        "agent_number": context.agent_number,
    }


def resolve_named(name: str, context: SystemVariableContext) -> Any:
    """Resolve one system variable, including the `<tz>` suffixed forms."""
    if name in {"current_time", "current_hour", "current_time_zone", "call_type", "direction",
                "user_number", "agent_number"}:
        return system_variables(context)[name]
    match = _TZ_VAR_RE.match(name)
    if not match:
        raise SystemVariableError(f"unknown system variable {name!r}")
    kind, tz_name = match.groups()
    zone = resolve_tz(tz_name.replace("_", "/", 1) if "/" not in tz_name else tz_name)
    local = context.now.astimezone(zone)
    if kind == "time":
        return local.strftime("%Y-%m-%dT%H:%M:%S%z")
    return str(local.hour)


def build_variables(
    context: SystemVariableContext,
    *,
    overrides: Mapping[str, Any] | None = None,
    defaults: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """System variables, then tenant defaults, then per-call overrides — in that order."""
    resolved = system_variables(context)
    system_keys = set(resolved)
    for key, value in (defaults or {}).items():
        if key in PROTECTED_SYSTEM_VARIABLES:
            continue
        resolved[str(key)] = value
    for key, value in (overrides or {}).items():
        key = str(key)
        if key in PROTECTED_SYSTEM_VARIABLES:
            raise SystemVariableError(f"system variable {key!r} cannot be overridden")
        # Only *system* variables are protected; a tenant default is by definition overridable.
        if key in system_keys and key not in OVERRIDABLE_SYSTEM_VARIABLES:
            raise SystemVariableError(f"system variable {key!r} cannot be overridden")
        resolved[key] = value
    return resolved
