"""Pieces every compliance-v2 route module shares.

Three concerns, and nothing else:

* **tenant scope** — every handler takes ``tenant_id`` from the path and calls
  :func:`scoped_tenant`, which refuses a mismatch between the path and the
  caller's tenant before any query runs;
* **idempotency** — mutating handlers accept ``Idempotency-Key`` and claim it
  through the product's existing receipt store, so a retried POST cannot create
  a second campaign, a second export or a second erasure;
* **audit** — each mutation writes an audit row inside the same transaction as
  the business change, which is what puts it in the SIEM feed and the hash chain.

Error translation lives here too, so a route module raises a domain exception and
gets a stable HTTP body with a machine-readable ``code``.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_event
from app.auth.dependencies import TenantContext, get_context
from app.auth.permissions import Permission
from app.resilience.idempotency import (
    IdempotencyConflict,
    IdempotencyError,
    IdempotencyInProgress,
    IdempotencyPreviousFailure,
    RequestClaim,
    claim_request,
    complete_request,
    fail_request,
)
from app.db.session import get_session
from app.tenancy.isolation import HierarchyError, require_same_tenant, to_http

#: Header the client sends to make a POST retry-safe.
IDEMPOTENCY_HEADER = "Idempotency-Key"


def scoped_tenant(ctx: TenantContext, tenant_id: uuid.UUID) -> uuid.UUID:
    """Refuse a path tenant that is not the caller's tenant."""
    try:
        require_same_tenant(ctx.tenant, tenant_id)
    except HierarchyError as exc:
        raise to_http(exc) from None
    return tenant_id


def require_human(ctx: TenantContext) -> None:
    """Refuse a machine credential for actions that need a person's judgement."""
    if ctx.is_machine:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "human_session_required",
                "message": "This action must be performed by a signed-in person",
            },
        )


def error_body(code: str, message: str) -> dict[str, Any]:
    return {"detail": {"code": code, "message": message}}


def http_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def not_found(what: str = "resource") -> HTTPException:
    return http_error(status.HTTP_404_NOT_FOUND, f"{what}_not_found", f"{what} not found")


def body_status(status_code: int) -> int:
    return int(status_code)


def idempotency_key(request: Request) -> str:
    return (request.headers.get(IDEMPOTENCY_HEADER) or "").strip()


async def claim_idempotent(
    session: AsyncSession,
    *,
    ctx: TenantContext,
    request: Request,
    operation: str,
    payload: Any,
) -> RequestClaim | None:
    """Claim the request's idempotency key, or return ``None`` when absent.

    The claim is flushed into the caller's transaction. A duplicate that already
    succeeded is returned with ``created=False`` so the handler can replay the
    stored resource id instead of performing the side effect twice.
    """
    key = idempotency_key(request)
    if not key:
        return None
    try:
        return await claim_request(
            session,
            tenant_id=ctx.tenant_id,
            operation=operation,
            key=key,
            request_data=payload,
        )
    except IdempotencyConflict as exc:
        raise http_error(status.HTTP_409_CONFLICT, exc.code, str(exc)) from None
    except IdempotencyInProgress as exc:
        raise http_error(status.HTTP_409_CONFLICT, exc.code, str(exc)) from None
    except IdempotencyPreviousFailure as exc:
        raise http_error(status.HTTP_409_CONFLICT, exc.code, str(exc)) from None
    except IdempotencyError as exc:
        raise http_error(status.HTTP_400_BAD_REQUEST, exc.code, str(exc)) from None


def replay_reference(claim: RequestClaim | None, *, resource_type: str) -> str | None:
    """The stored resource id when this POST is a completed duplicate.

    A retried request must return the *first* resource, not create a second one.
    Returning ``None`` means "this is the first attempt, carry on". The receipt's
    own ``resource_type`` is compared so a key reused across two different
    operations can never be mistaken for a replay.
    """
    if claim is None or claim.created:
        return None
    if (claim.receipt.resource_type or "") != resource_type:
        return None
    return claim.receipt.resource_id or None


async def complete_idempotent(
    session: AsyncSession,
    claim: RequestClaim | None,
    *,
    resource_type: str,
    resource_id: str | uuid.UUID,
) -> None:
    if claim is None:
        return
    await complete_request(
        session, claim.receipt, resource_type=resource_type, resource_id=resource_id
    )


async def fail_idempotent(
    session: AsyncSession, claim: RequestClaim | None, *, category: str
) -> None:
    if claim is None:
        return
    await fail_request(session, claim.receipt, category=category)


async def audit_action(
    session: AsyncSession,
    *,
    ctx: TenantContext,
    request: Request,
    event_type: str,
    resource_type: str,
    resource_id: str | uuid.UUID | None = None,
    detail: dict[str, Any] | None = None,
    result: str = "success",
) -> None:
    """Stage the audit row for a compliance mutation. Flushes."""
    await record_event(
        session,
        tenant_id=ctx.tenant_id,
        event_type=event_type,
        actor_user_id=ctx.user_id,
        actor_type="api_key" if ctx.is_machine else "human",
        actor_email=None if ctx.is_machine else ctx.user.email,
        resource_type=resource_type,
        resource_id=resource_id,
        request_id=getattr(request.state, "request_id", None),
        result=result,
        ip_address=(request.client.host if request.client else "")[:64],
        user_agent=(request.headers.get("user-agent") or "")[:300],
        detail=detail or {},
    )
    await session.flush()


def client_ip(request: Request) -> str:
    return (request.client.host if request.client else "")[:64]


def page_params(limit: int | None, offset: int | None) -> tuple[int, int]:
    size = 100 if limit is None else max(1, min(int(limit), 500))
    start = 0 if offset is None else max(0, int(offset))
    return size, start


def permission_names(*permissions: Permission) -> list[str]:
    return [permission.value for permission in permissions]


__all__ = [
    "IDEMPOTENCY_HEADER",
    "audit_action",
    "claim_idempotent",
    "client_ip",
    "complete_idempotent",
    "error_body",
    "fail_idempotent",
    "http_error",
    "idempotency_key",
    "not_found",
    "page_params",
    "permission_names",
    "replay_reference",
    "require_human",
    "scoped_tenant",
]


# ------------------------------------------------- privilege freshness ---
#
# The re-authentication window in ``app.compliance_v2.hipaa.session_policy`` is
# only a control if the irreversible actions actually consult it. These helpers
# attach it to a route as a dependency, so a stale session cannot erase a
# subject's data, approve a break-glass grant, or move a tenant's keys.


async def enforce_recent_auth(
    session: AsyncSession,
    *,
    ctx: TenantContext,
    action: str,
) -> None:
    """Refuse the request unless the caller authenticated inside the window.

    A refusal is itself audited: "someone tried to erase data with a stale
    session" is exactly the event an operator wants in the trail.
    """
    from app.compliance_v2.hipaa import session_policy

    try:
        await session_policy.require_recent_auth(
            session,
            user_id=ctx.user.id,
            session_id=ctx.session_id,
            action=action,
        )
    except session_policy.ReauthRequired as exc:
        try:
            await session_policy.audit_reauth_denied(
                session,
                tenant_id=ctx.tenant_id,
                user_id=ctx.user.id,
                action=action,
                error=exc,
            )
            await session.commit()
        except Exception:  # noqa: BLE001 - the refusal still stands if the audit fails
            await session.rollback()
        raise http_error(
            status.HTTP_403_FORBIDDEN, "reauth_required", str(exc)
        ) from None


def require_recent_auth_for(action: str):
    """A dependency for one sensitive action, e.g. ``dsar.erasure``."""

    async def dependency(
        ctx: TenantContext = Depends(get_context),
        session: AsyncSession = Depends(get_session),
    ) -> None:
        await enforce_recent_auth(session, ctx=ctx, action=action)

    return dependency
