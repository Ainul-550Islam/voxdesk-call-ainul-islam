"""The TLS ask and white-label widget have public boundaries with explicit guards."""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Response
from httpx import ASGITransport, AsyncClient

from app.core.errors import AppError
from app.domain.public_site_models import PublicRouteCategory
from app.middleware.public_boundary import add_public_boundary_middleware
from app.platform_v2.router import AREAS
from app.services.public_auth_boundary_service import (
    classify_path,
    reject_public_credential_on_private_route,
)


def test_the_live_app_mounts_every_platform_v2_route_area():
    from app.main import app

    paths = {route.path for route in app.routes if hasattr(route, "path")}
    missing = {
        area: prefix
        for area, prefix in AREAS.items()
        if not any(path == prefix or path.startswith(prefix.rstrip("/") + "/") for path in paths)
    }
    assert missing == {}, f"platform-v2 router areas are not mounted: {missing}"


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/api/platform/tls/ask", PublicRouteCategory.PUBLIC),
        ("/api/platform/tls/ask/extra", PublicRouteCategory.PROTECTED),
        ("/api/platform/tls/ask-extra", PublicRouteCategory.PROTECTED),
        ("/api/whitelabel/public/widget/key/loader.js", PublicRouteCategory.PUBLIC_WIDGET),
        ("/api/whitelabel/publicity/widget/key/loader.js", PublicRouteCategory.PROTECTED),
        ("/api/v1/public/widget/key/config", PublicRouteCategory.PUBLIC_WIDGET),
        ("/api/v1/public/widget2/config", PublicRouteCategory.PROTECTED),
    ],
    ids=[
        "tls-ask-exact",
        "tls-ask-subpath-protected",
        "tls-ask-near-match-protected",
        "white-label-widget-public",
        "white-label-prefix-near-match-protected",
        "existing-widget-public",
        "existing-widget-prefix-near-match-protected",
    ],
)
def test_public_route_families_match_only_on_reviewed_path_boundaries(path, expected):
    assert classify_path(path) is expected


def test_widget_credentials_are_accepted_only_on_widget_paths():
    reject_public_credential_on_private_route(
        path="/api/whitelabel/public/widget/key/config",
        authorization_header="Bearer vdpk_public-key",
        public_key_header="vdpk_public-key",
    )

    for path in (
        "/api/whitelabel/theme",
        "/api/platform/tls/ask",
        "/pricing",
    ):
        with pytest.raises(AppError) as caught:
            reject_public_credential_on_private_route(
                path=path,
                public_key_header="vdpk_public-key",
            )
        assert caught.value.status_code == 403
        assert caught.value.code == "public_credential_forbidden_on_private_api"


@pytest.mark.asyncio
async def test_boundary_middleware_preserves_public_widget_cache_policy():
    app = FastAPI()
    add_public_boundary_middleware(app)

    @app.get("/api/whitelabel/public/widget/{public_key}/loader.js")
    async def widget_loader(public_key: str):
        return Response(
            content=f"// {public_key}",
            media_type="application/javascript",
            headers={"Cache-Control": "public, max-age=60"},
        )

    @app.get("/api/whitelabel/theme")
    async def protected_theme():
        return Response(content="{}", headers={"Cache-Control": "public, max-age=60"})

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        widget = await client.get("/api/whitelabel/public/widget/key/loader.js")
        protected = await client.get("/api/whitelabel/theme")

    assert widget.headers["X-VoxDesk-Boundary"] == "public_widget"
    assert widget.headers["Cache-Control"] == "public, max-age=60"
    assert protected.headers["X-VoxDesk-Boundary"] == "protected"
    assert protected.headers["Cache-Control"] == "no-store, private"
