"""Pieces every platform-v2 route module shares.

Three concerns, matching how the rest of the product's route modules behave:

* **error translation** — a domain exception becomes the documented envelope
  ``{"detail": {"code", "message", "detail"}}`` with the status the exception
  declares. Routes therefore raise ``NotFound``/``Conflict`` and never build a
  ``HTTPException`` by hand;
* **environment resolution** — the environment-scoped tables in this batch need a
  real ``environments`` row. The helper prefers the credential's bound
  environment, then an explicit selector, then the tenant's default, and refuses
  with ``409 environment_required`` rather than silently writing rows nothing can
  read;
* **idempotency + audit** — mutations accept ``Idempotency-Key`` through the
  product's existing receipt store and write their audit row in the same
  transaction as the business change.
"""

from __future__ import annotations

import functools
import uuid
from typing import Any, Awaitable, Callable, TypeVar

from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_event
from app.auth.dependencies import TenantContext
from app.db.models import Environment
from app.platform_v2.errors import PlatformV2Error, to_http
from app.resilience.idempotency import (
    IdempotencyConflict,
    IdempotencyError,
    IdempotencyInProgress,
    IdempotencyPreviousFailure,
    RequestClaim,
    claim_request,
    complete_request,
)

IDEMPOTENCY_HEADER = "Idempotency-Key"
Handler = TypeVar("Handler", bound=Callable[..., Awaitable[Any]])


def endpoint_errors(handler: Handler) -> Handler:
    """Translate :class:`PlatformV2Error` into the API error envelope.

    Nothing else is caught: an unexpected exception must surface as a 500 and be
    visible in the logs, not be relabelled as a client mistake.
    """

    @functools.wraps(handler)
    async def wrapper(*args: Any, **kwargs: Any):
        try:
            return await handler(*args, **kwargs)
        except PlatformV2Error as exc:
            raise to_http(exc) from None

    return wrapper  # type: ignore[return-value]


def not_found(what: str = "resource") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": f"{what}_not_found", "message": f"{what} not found"},
    )


async def require_environment(
    session: AsyncSession,
    ctx: TenantContext,
    environment_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """The environment this request may operate in.

    A supplied id outside the tenant is refused as *not found*, not as forbidden:
    telling a caller "that id exists but is not yours" is a boundary leak.
    """
    if environment_id is not None:
        row = await session.get(Environment, environment_id)
        if row is None or row.tenant_id != ctx.tenant_id:
            raise not_found("environment")
        return row.id
    if ctx.environment_id is not None:
        row = await session.get(Environment, ctx.environment_id)
        if row is not None and row.tenant_id == ctx.tenant_id:
            return row.id
    default = (
        await session.execute(
            select(Environment).where(
                Environment.tenant_id == ctx.tenant_id,
                Environment.is_default.is_(True),
            )
        )
    ).scalar_one_or_none()
    if default is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "environment_required",
                "message": (
                    "This tenant has no default environment; pass environment_id or "
                    "select an environment before using environment-scoped features"
                ),
            },
        )
    return default.id


async def audit(
    session: AsyncSession,
    ctx: TenantContext,
    request: Request,
    *,
    event_type: str,
    resource_type: str,
    resource_id: str | uuid.UUID | None = None,
    detail: dict[str, Any] | None = None,
    result: str = "success",
) -> None:
    """Stage the audit row for a mutation; it commits with the business change."""
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


async def claim(
    session: AsyncSession,
    ctx: TenantContext,
    request: Request,
    *,
    operation: str,
    payload: Any,
) -> RequestClaim | None:
    """Claim ``Idempotency-Key`` for this request, or ``None`` when absent."""
    key = (request.headers.get(IDEMPOTENCY_HEADER) or "").strip()
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
    except (IdempotencyConflict, IdempotencyInProgress, IdempotencyPreviousFailure) as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code, "message": str(exc)},
        ) from None
    except IdempotencyError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": exc.code, "message": str(exc)},
        ) from None


async def complete_claim(
    session: AsyncSession,
    claim_row: RequestClaim | None,
    *,
    resource_type: str,
    resource_id: str | uuid.UUID,
) -> None:
    if claim_row is None:
        return
    await complete_request(
        session, claim_row.receipt, resource_type=resource_type, resource_id=resource_id
    )


def replay_id(claim_row: RequestClaim | None, *, resource_type: str) -> str | None:
    """The stored resource id when this request is a completed duplicate."""
    if claim_row is None or claim_row.created:
        return None
    if (claim_row.receipt.resource_type or "") != resource_type:
        return None
    return claim_row.receipt.resource_id or None


def replay_response(
    claim_row: RequestClaim | None, *, resource_type: str, resource_id_field: str = "id"
) -> dict[str, Any] | None:
    """The body for a request that already completed, or ``None`` for a fresh one.

    A retried mutation must not be re-executed (the domain would refuse it as a
    duplicate) and must not fail either — the caller asked once, the network lost the
    answer, and a stable reply is the honest response. ``replayed`` is explicit so a
    client can tell it apart from a fresh creation without guessing from the status.
    """
    stored = replay_id(claim_row, resource_type=resource_type)
    if stored is None:
        return None
    return {
        resource_id_field: stored,
        "resource_type": resource_type,
        "replayed": True,
    }


def page(limit: int | None, offset: int | None) -> tuple[int, int]:
    size = 100 if limit is None else max(1, min(int(limit), 500))
    start = 0 if offset is None else max(0, int(offset))
    return size, start


def actor_label(ctx: TenantContext) -> str:
    """Who did it, for a log line or a notification body."""
    return "api-key" if ctx.is_machine else (ctx.user.email or str(ctx.user_id))


__all__ = [
    "IDEMPOTENCY_HEADER",
    "actor_label",
    "audit",
    "claim",
    "complete_claim",
    "endpoint_errors",
    "not_found",
    "page",
    "replay_id",
    "replay_response",
    "require_environment",
]
