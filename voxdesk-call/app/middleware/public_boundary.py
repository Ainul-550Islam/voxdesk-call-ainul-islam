"""Prompt 5: Public / Private / Widget HTTP Security Boundary Middleware.

Enforces:
1. Public widget keys (`vdpk_*`) and widget session tokens (`vdws_*`) are rejected
   with HTTP 403 (`public_credential_forbidden_on_private_api`) if presented on ANY
   private/admin/auth route outside `/api/v1/public/widget/*`.
2. Explicit `X-VoxDesk-Boundary` classification header on every response (`public`,
   `auth`, `protected`, `public_widget`).
3. Strict `Cache-Control: no-store, private` on protected, auth, and widget session
   responses so private tenant data and widget tokens are never cached by proxies.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.errors import AppError
from app.domain.public_site_models import PublicRouteCategory
from app.services.public_auth_boundary_service import (
    classify_path,
    reject_public_credential_on_private_route,
)


def add_public_boundary_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def _public_boundary_middleware(request: Request, call_next):
        path = request.url.path or "/"
        auth_header = request.headers.get("authorization")
        pub_key_header = request.headers.get("x-voxdesk-public-key")
        widget_sess_header = request.headers.get("x-voxdesk-widget-session")

        category = classify_path(path)

        # Fail closed if a widget session token (`vdws_`) or public key (`vdpk_`) is
        # presented on any non-widget endpoint.
        try:
            reject_public_credential_on_private_route(
                path=path,
                authorization_header=auth_header,
                public_key_header=pub_key_header or widget_sess_header,
            )
        except AppError as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content={
                    "error": {
                        "code": exc.code,
                        "message": exc.message,
                    },
                    "detail": exc.message,
                },
                headers={
                    "X-VoxDesk-Boundary": category.value,
                    "Cache-Control": "no-store, private",
                },
            )

        response = await call_next(request)
        response.headers["X-VoxDesk-Boundary"] = category.value

        if category in {
            PublicRouteCategory.PROTECTED,
            PublicRouteCategory.AUTH,
            PublicRouteCategory.PUBLIC_WIDGET,
        }:
            response.headers["Cache-Control"] = "no-store, private"

        return response
