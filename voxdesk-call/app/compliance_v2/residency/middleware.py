"""ASGI middleware that refuses a request naming a region it may not use.

The guard is deliberately narrow:

* it only inspects requests to the configured **protected path prefixes**
  (recordings, transcripts, exports) — the paths where object placement is the
  point of the request;
* it only acts when the request *names* a region, either through the
  ``X-VoxDesk-Region`` header or a ``region`` query parameter. A request that
  names no region is left alone, because there is nothing to compare;
* it resolves the calling tenant from the bearer token's ``tenant_id`` claim. A
  token that cannot be decoded is passed through to the normal authentication
  dependency, which owns the 401 — the middleware must not become a second,
  differently-worded authentication layer.

Outcome: an unknown region is a 400, a region that contradicts the tenant's pin
is a 403, and both write an audit event through ``enforce_region``. Nothing is
cached between requests, so a pin change takes effect immediately.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable, Iterable
from urllib.parse import parse_qs

from app.compliance_v2.residency.policy import (
    CrossRegionViolation,
    ResidencyDecision,
    check_region,
)
from app.compliance_v2.residency.regions import RegionError
from app.compliance_v2.settings import get_compliance_v2_settings

logger = logging.getLogger(__name__)

Scope = dict
Receive = Callable[[], Awaitable[dict]]
Send = Callable[[dict], Awaitable[None]]

REGION_HEADER = "x-voxdesk-region"


def _named_region(scope: Scope) -> str:
    """The region this request names, or ``""``."""
    for raw_name, raw_value in scope.get("headers") or []:
        if raw_name.decode("latin-1").lower() == REGION_HEADER:
            return raw_value.decode("latin-1").strip().lower()
    query = parse_qs((scope.get("query_string") or b"").decode("latin-1"))
    values = query.get("region") or []
    return values[0].strip().lower() if values else ""


def _tenant_claim(scope: Scope) -> str:
    """``tenant_id`` from the bearer token, without verifying the signature.

    Reading an unverified claim is safe *here* because the comparison is only
    ever used to *deny*: a forged claim can make a request be refused, never
    accepted. Authentication and signature verification remain the job of the
    route dependency.
    """
    from app.auth.jwt import decode_access_token

    for raw_name, raw_value in scope.get("headers") or []:
        if raw_name.decode("latin-1").lower() != "authorization":
            continue
        value = raw_value.decode("latin-1")
        if not value.lower().startswith("bearer "):
            continue
        token = value.split(" ", 1)[1].strip()
        try:
            claims = decode_access_token(token)
        except Exception:  # noqa: BLE001 - an unreadable token is not our decision
            return ""
        return str(getattr(claims, "tenant_id", "") or "")
    return ""


def _protected(path: str, prefixes: Iterable[str]) -> bool:
    return any(path.startswith(prefix) for prefix in prefixes)


class ResidencyGuardMiddleware:
    """Refuse cross-region requests on placement-bearing paths."""

    def __init__(
        self,
        app: Any,
        *,
        session_factory: Callable[[], Any] | None = None,
        protected_prefixes: tuple[str, ...] | None = None,
        enabled: bool = True,
    ) -> None:
        self.app = app
        self._session_factory = session_factory
        self._prefixes = protected_prefixes
        self._enabled = enabled

    def _prefix_list(self) -> tuple[str, ...]:
        if self._prefixes is not None:
            return self._prefixes
        return get_compliance_v2_settings().protected_prefixes

    async def _session(self):
        if self._session_factory is not None:
            return self._session_factory()
        from app.db.session import get_sessionmaker

        return get_sessionmaker()()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self._enabled or scope.get("type") != "http":
            return await self.app(scope, receive, send)
        if not _protected(str(scope.get("path") or ""), self._prefix_list()):
            return await self.app(scope, receive, send)

        requested = _named_region(scope)
        if not requested:
            return await self.app(scope, receive, send)

        from app.compliance_v2.residency.regions import build_registry

        active = build_registry()
        if not active.configured:
            return await self.app(scope, receive, send)
        if not active.is_known(requested):
            return await self._refuse(
                send,
                400,
                "unknown_region",
                f"region {requested!r} is not configured for this deployment",
            )

        tenant_raw = _tenant_claim(scope)
        if not tenant_raw:
            # No tenant could be read; the authentication dependency will decide
            # whether this request proceeds at all.
            return await self.app(scope, receive, send)

        identifier = None
        try:
            import uuid

            identifier = uuid.UUID(tenant_raw)
        except (ValueError, TypeError):
            return await self.app(scope, receive, send)

        session_cm = await self._session()
        try:
            async with session_cm as session:
                decision: ResidencyDecision = await check_region(
                    session,
                    tenant_id=identifier,
                    requested_region=requested,
                    registry=active,
                )
                if not decision.allowed:
                    from app.compliance_v2.residency.policy import enforce_region

                    try:
                        await enforce_region(
                            session,
                            tenant_id=identifier,
                            requested_region=requested,
                            operation=f"{scope.get('method', 'GET')} {scope.get('path', '')}",
                            registry=active,
                        )
                    except CrossRegionViolation:
                        await session.commit()
                else:
                    await session.rollback()
        except RegionError as exc:
            return await self._refuse(send, 400, "unknown_region", str(exc))
        except Exception as exc:  # noqa: BLE001 - fail closed with a generic refusal
            logger.warning(
                "residency.middleware_error", extra={"error_type": type(exc).__name__}
            )
            return await self._refuse(
                send, 503, "residency_unavailable", "region check unavailable"
            )

        if decision.allowed:
            return await self.app(scope, receive, send)
        return await self._refuse(
            send,
            403,
            "cross_region_blocked",
            decision.reason or "request names a region this tenant may not use",
        )

    @staticmethod
    async def _refuse(send: Send, status: int, code: str, message: str) -> None:
        body = json.dumps({"detail": {"code": code, "message": message}}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def add_residency_middleware(app: Any, **kwargs: Any) -> None:
    """Install the guard as outer middleware."""
    app.add_middleware(ResidencyGuardMiddleware, **kwargs)


__all__ = [
    "REGION_HEADER",
    "ResidencyGuardMiddleware",
    "add_residency_middleware",
]
