"""Validate a resource's tenant and environment before it is stored or read.

The database composite foreign key is the backstop. These checks run first so
a cross-tenant id fails closed without confirming that the other tenant exists.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from app.db.models import Environment

from app.tenancy.isolation import BoundaryDenied, LifecycleDenied

_CLOSED = frozenset({"suspended", "archived"})
_ACTIVE_PRODUCTION: dict[str, str] = {}


def remember_production(tenant_id, environment_id) -> None:
    """Remember a tenant's active production id so a later insert need not re-query it."""
    if tenant_id is None or environment_id is None:
        return
    _ACTIVE_PRODUCTION[str(tenant_id)] = str(environment_id)


def forget_production(tenant_id, environment_id=None) -> None:
    key = str(tenant_id)
    if environment_id is None or _ACTIVE_PRODUCTION.get(key) == str(environment_id):
        _ACTIVE_PRODUCTION.pop(key, None)

def _models():
    from app.db.models import (
        Appointment,
        Automation,
        AutomationRun,
        Call,
        Environment,
        InboxThreadState,
        KnowledgeChunk,
        KnowledgeDocument,
        Lead,
        NotificationRow,
        UsageEvent,
    )

    return {
        "Appointment": Appointment,
        "Automation": Automation,
        "AutomationRun": AutomationRun,
        "Call": Call,
        "Environment": Environment,
        "InboxThreadState": InboxThreadState,
        "KnowledgeChunk": KnowledgeChunk,
        "KnowledgeDocument": KnowledgeDocument,
        "Lead": Lead,
        "NotificationRow": NotificationRow,
        "UsageEvent": UsageEvent,
    }


class ImmutableEnvironment(LifecycleDenied):
    def __init__(self) -> None:
        super().__init__("A resource cannot move between environments")


def _same(left, right) -> bool:
    if left is None or right is None:
        return False
    return str(left) == str(right)


def assert_environment_belongs_to_tenant(environment: Environment | None, tenant_id) -> Environment:
    if environment is None or not _same(environment.tenant_id, tenant_id):
        raise BoundaryDenied()
    return environment


def assert_resource_belongs_to_tenant(resource, tenant_id) -> None:
    if resource is None or not _same(getattr(resource, "tenant_id", None), tenant_id):
        raise BoundaryDenied()


def assert_resource_environment_match(resource, environment: Environment) -> None:
    if resource is None or not _same(getattr(resource, "environment_id", None), environment.id):
        raise BoundaryDenied()
    if not _same(getattr(resource, "tenant_id", None), environment.tenant_id):
        raise BoundaryDenied()


def assert_environment_accepts_write(environment: Environment) -> None:
    if environment.status in _CLOSED:
        raise LifecycleDenied("This environment is not accepting writes")


def assert_resource_scope(resource, environment: Environment) -> None:
    assert_environment_belongs_to_tenant(environment, environment.tenant_id)
    assert_resource_belongs_to_tenant(resource, environment.tenant_id)
    assert_resource_environment_match(resource, environment)


def _production_row(connection, tenant_id):
    environment = _models()["Environment"]
    return connection.execute(
        select(
            environment.id,
            environment.tenant_id,
            environment.status,
            environment.is_default,
        ).where(
            environment.tenant_id == tenant_id,
            environment.kind == "production",
        ).order_by(environment.is_default.desc())
    ).first()


def _environment_row(connection, environment_id):
    environment = _models()["Environment"]
    return connection.execute(
        select(
            environment.id,
            environment.tenant_id,
            environment.status,
        ).where(environment.id == environment_id)
    ).first()


def _copy_call_environment(connection, target) -> bool:
    models = _models()
    if not isinstance(target, models["InboxThreadState"]) or target.call_id is None:
        return False
    call = models["Call"]
    row = connection.execute(
        select(call.environment_id, call.tenant_id).where(call.id == target.call_id)
    ).first()
    if row is None or not _same(row.tenant_id, target.tenant_id) or row.environment_id is None:
        return False
    target.environment_id = row.environment_id
    return True


def _assign_scope(mapper, connection, target) -> None:
    # SQLite's default busy timeout is zero. A scope lookup inside the insert
    # transaction must wait for another connection instead of failing the race
    # the unique constraint is meant to arbitrate. PostgreSQL does not need this.
    if connection.dialect.name == "sqlite":
        connection.exec_driver_sql("PRAGMA busy_timeout=5000")
    tenant_id = getattr(target, "tenant_id", None)
    if tenant_id is None:
        raise BoundaryDenied()
    if getattr(target, "environment_id", None) is None and _copy_call_environment(connection, target):
        return
    environment_id = getattr(target, "environment_id", None)
    if environment_id is None:
        cached = _ACTIVE_PRODUCTION.get(str(tenant_id))
        if cached is not None:
            target.environment_id = uuid.UUID(cached)
            return
        row = _production_row(connection, tenant_id)
        if row is None or row.status in _CLOSED:
            raise LifecycleDenied("No active production environment is available")
        remember_production(tenant_id, row.id)
        target.environment_id = row.id
        return
    cached = _ACTIVE_PRODUCTION.get(str(tenant_id))
    if cached is not None and _same(cached, environment_id):
        return
    row = _environment_row(connection, environment_id)
    if row is None or not _same(row.tenant_id, tenant_id):
        raise BoundaryDenied()
    if row.status in _CLOSED:
        raise LifecycleDenied("This environment is not accepting writes")


def _freeze_scope(mapper, connection, target) -> None:
    history = inspect(target).attrs.environment_id.history
    if not history.has_changes():
        return
    previous = history.deleted[0] if history.deleted else None
    current = history.added[0] if history.added else getattr(target, "environment_id", None)
    if previous is not None and not _same(previous, current):
        raise ImmutableEnvironment()


def _mapper_has(model, identifier: str, fn) -> bool:
    return any(
        getattr(item, "__wrapped__", item) is fn or item is fn
        for item in getattr(model.__mapper__.dispatch, identifier)
    )


def _track_production(mapper, connection, target) -> None:
    kind = getattr(target, "kind", None)
    kind_value = getattr(kind, "value", kind)
    if kind_value != "production":
        return
    status = getattr(target, "status", None)
    status_value = getattr(status, "value", status)
    if status_value in _CLOSED or not getattr(target, "is_default", False):
        forget_production(target.tenant_id, target.id)
        return
    remember_production(target.tenant_id, target.id)


def register() -> None:
    # `event.contains` is true after a pre-configuration listen even when the
    # mapper dispatch that actually fires is still empty. Configure first.
    from sqlalchemy.orm import configure_mappers

    configure_mappers()
    environment = _models()["Environment"]
    if not _mapper_has(environment, "after_insert", _track_production):
        event.listen(environment, "after_insert", _track_production)
    if not _mapper_has(environment, "after_update", _track_production):
        event.listen(environment, "after_update", _track_production)
    for model in _models().values():
        if model.__name__ == "Environment":
            continue
        if not _mapper_has(model, "before_insert", _assign_scope):
            event.listen(model, "before_insert", _assign_scope)
        if not _mapper_has(model, "before_update", _freeze_scope):
            event.listen(model, "before_update", _freeze_scope)


def session_environment(session: Session, environment_id):
    return session.get(_models()["Environment"], environment_id)
