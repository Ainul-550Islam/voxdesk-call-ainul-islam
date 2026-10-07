"""Allowlisted segment evaluation. Tenant JSON never becomes SQL text."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Lead, LeadStatus
from app.leads.exceptions import InvalidSegment
from app.leads.models import LeadSegment

_FIELDS = {
    "status": Lead.status,
    "score": Lead.score,
    "attempts": Lead.attempts,
    "name": Lead.name,
    "company": Lead.company,
    "email": Lead.email,
    "phone": Lead.phone,
    "campaign_id": Lead.campaign_id,
    "created_at": Lead.created_at,
    "last_attempt_at": Lead.last_attempt_at,
}
_STRINGS = {"name", "company", "email", "phone"}
_NUMBERS = {"score", "attempts"}
_DATES = {"created_at", "last_attempt_at"}
_OPS = {
    "eq", "neq", "contains", "starts_with", "gt", "gte", "lt", "lte",
    "in", "between", "is_null", "is_not_null",
}
_MAX_CONDITIONS = 20


def validate_definition(definition: dict) -> dict:
    if not isinstance(definition, dict):
        raise InvalidSegment("A segment definition must be an object")
    if "sql" in definition or "query" in definition or "where" in definition:
        raise InvalidSegment("Raw SQL is not accepted")
    keys = set(definition) - {"all", "any"}
    if keys or ("all" in definition and "any" in definition):
        raise InvalidSegment("Use exactly one of all or any")
    combiner = "all" if "all" in definition else "any" if "any" in definition else None
    if combiner is None:
        raise InvalidSegment("A segment needs an all or any list")
    clauses = definition[combiner]
    if not isinstance(clauses, list) or not clauses:
        raise InvalidSegment("The condition list is empty")
    if len(clauses) > _MAX_CONDITIONS:
        raise InvalidSegment("Too many conditions")
    cleaned = []
    for clause in clauses:
        cleaned.append(_clause(clause))
    return {combiner: cleaned}


def _clause(clause: object) -> dict:
    if not isinstance(clause, dict):
        raise InvalidSegment("Each condition must be an object")
    field = clause.get("field")
    op = clause.get("op")
    if not isinstance(field, str) or field not in _FIELDS:
        raise InvalidSegment("Unknown segment field")
    if not isinstance(op, str) or op not in _OPS:
        raise InvalidSegment("Unknown segment operator")
    if any(token in field.lower() for token in (";", "--", "/*", "drop", "select")):
        raise InvalidSegment("Unknown segment field")
    value = clause.get("value")
    if op in {"contains", "starts_with"} and field not in _STRINGS:
        raise InvalidSegment("That operator is only valid for text fields")
    if op == "between":
        if not isinstance(value, list) or len(value) != 2:
            raise InvalidSegment("between expects two values")
    if op == "in":
        if not isinstance(value, list) or not value or len(value) > 50:
            raise InvalidSegment("in expects a short list of values")
    if op not in {"is_null", "is_not_null"} and "value" not in clause:
        raise InvalidSegment("A comparison needs a value")
    return {"field": field, "op": op, "value": value}


def _coerce(field: str, value):
    if field == "status":
        try:
            return LeadStatus(str(value))
        except ValueError as exc:
            raise InvalidSegment("Unknown status value") from exc
    if field in _NUMBERS:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise InvalidSegment("Expected a number")
        return int(value)
    if field in _DATES:
        if not isinstance(value, str):
            raise InvalidSegment("Expected an ISO timestamp")
        try:
            return datetime.fromisoformat(value)
        except ValueError as exc:
            raise InvalidSegment("Expected an ISO timestamp") from exc
    if field == "campaign_id":
        try:
            return uuid.UUID(str(value))
        except ValueError as exc:
            raise InvalidSegment("Expected a campaign id") from exc
    if not isinstance(value, str):
        raise InvalidSegment("Expected text")
    return value[:500]


def _expression(clause: dict):
    column = _FIELDS[clause["field"]]
    op = clause["op"]
    if op == "is_null":
        return column.is_(None)
    if op == "is_not_null":
        return column.is_not(None)
    value = clause["value"]
    if op == "in":
        return column.in_([_coerce(clause["field"], item) for item in value])
    if op == "between":
        low = _coerce(clause["field"], value[0])
        high = _coerce(clause["field"], value[1])
        return column.between(low, high)
    coerced = _coerce(clause["field"], value)
    if op == "eq":
        return column == coerced
    if op == "neq":
        return column != coerced
    if op == "contains":
        return column.contains(coerced)
    if op == "starts_with":
        return column.startswith(coerced)
    if op == "gt":
        return column > coerced
    if op == "gte":
        return column >= coerced
    if op == "lt":
        return column < coerced
    return column <= coerced


def compile_filter(definition: dict):
    body = validate_definition(definition)
    combiner, clauses = next(iter(body.items()))
    expressions = [_expression(clause) for clause in clauses]
    from sqlalchemy import and_, or_

    return and_(*expressions) if combiner == "all" else or_(*expressions)


async def members(
    session: AsyncSession,
    segment: LeadSegment,
    *,
    limit: int,
    offset: int,
) -> list[Lead]:
    predicate = compile_filter(segment.definition)
    rows = (
        await session.execute(
            select(Lead)
            .where(
                Lead.tenant_id == segment.tenant_id,
                Lead.environment_id == segment.environment_id,
                predicate,
            )
            .order_by(Lead.created_at)
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    return list(rows)
