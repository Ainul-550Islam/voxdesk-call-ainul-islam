"""HTTP boundary helpers shared by the five Batch 04 route modules."""
from __future__ import annotations

import functools
import uuid
from typing import Any

from fastapi import HTTPException

from app.auth.dependencies import TenantContext
from app.auth.permissions import Permission
from app.auth.service import record_audit
from app.contact_center.exceptions import AcdError
from app.contact_center_v2.errors import ContactCenterV2Error, to_http
from app.db.models import AuditAction
from app.resilience.idempotency import IdempotencyError
from app.tenancy.isolation import HierarchyError, to_http as hierarchy_http


def endpoint_errors(func):
    """Translate domain errors without exposing provider/secret text."""

    @functools.wraps(func)
    async def wrapped(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except ContactCenterV2Error as exc:
            raise to_http(exc) from None
        except HierarchyError as exc:
            raise hierarchy_http(exc) from None
        except IdempotencyError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": exc.code, "message": str(exc), "detail": {}},
            ) from None
        except AcdError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": type(exc).__name__, "message": str(exc), "detail": {}},
            ) from None
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "validation_error", "message": str(exc), "detail": {}},
            ) from None

    return wrapped


async def audit(
    session,
    ctx: TenantContext,
    *,
    event_type: str,
    resource_type: str,
    resource_id: uuid.UUID | str | None = None,
    environment_id: uuid.UUID | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    await record_audit(
        session,
        action=AuditAction.GOVERNANCE_EVENT,
        event_type=event_type,
        tenant_id=ctx.tenant_id,
        environment_id=environment_id,
        actor_user_id=ctx.user_id,
        actor_type=ctx.auth_method if ctx.is_machine else "human",
        actor_email=ctx.user.email,
        resource_type=resource_type,
        resource_id=resource_id,
        detail=detail or {},
        commit=False,
    )


def can_supervise(ctx: TenantContext) -> bool:
    return ctx.can(Permission.SUPERVISOR_WRITE)


__all__ = ["audit", "can_supervise", "endpoint_errors"]
