"""Fixed predicates. There is no helper that filters an arbitrary column."""

from __future__ import annotations

from sqlalchemy import and_


def tenant_scope(model, tenant_id):
    return model.tenant_id == tenant_id


def environment_scope(model, environment_id):
    return model.environment_id == environment_id


def tenant_and_environment_scope(model, tenant_id, environment_id):
    return and_(tenant_scope(model, tenant_id), environment_scope(model, environment_id))


def active_environment_scope(environment_model):
    return environment_model.status == "active"
