"""A configured same-origin deployment can refresh; request Host is not trust."""
import pytest
from starlette.requests import Request

from app.core.config import settings
from app.security.csrf import csrf_origin_rejected


@pytest.mark.parametrize("origin,site,rejected", [
    ("https://app.example.com", "same-origin", False),
    ("https://app.example.com", "cross-site", True),
    ("https://attacker.example.com", "same-origin", True),
    ("null", "same-origin", True),
])
def test_canonical_public_origin_and_host_spoofing(monkeypatch, origin, site, rejected):
    monkeypatch.setattr(settings, "public_base_url", "https://app.example.com")
    monkeypatch.setattr(settings, "cors_origins", "https://separate-frontend.example.com")
    request = Request({"type": "http", "method": "POST", "path": "/auth/refresh",
                       "scheme": "https", "server": ("attacker.example.com", 443),
                       "headers": [(b"host", b"attacker.example.com"),
                                   (b"cookie", b"voxdesk_refresh=contract-only"),
                                   (b"origin", origin.encode()),
                                   (b"sec-fetch-site", site.encode())]})
    assert csrf_origin_rejected(request) is rejected
