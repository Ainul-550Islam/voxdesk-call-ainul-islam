"""Allowlisted scope fields. No credentials, tokens, or other tenants' rows."""

from __future__ import annotations

from app.db.models import Environment


def scope_fields(resource, environment: Environment | None = None) -> dict:
    payload = {
        "id": str(resource.id),
        "tenant_id": str(resource.tenant_id),
        "environment_id": str(resource.environment_id),
        "resource_type": type(resource).__name__,
    }
    if environment is not None and str(environment.id) == str(resource.environment_id):
        payload["environment_kind"] = environment.kind
        payload["environment_slug"] = environment.slug
        payload["environment_status"] = environment.status
    return payload


def page_payload(*, resource_type: str, items: list[dict], limit: int, offset: int, total: int) -> dict:
    return {
        "resource_type": resource_type,
        "items": items,
        "limit": limit,
        "offset": offset,
        "total": total,
    }
