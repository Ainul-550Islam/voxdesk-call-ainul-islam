"""Fixtures shared by the pentest-fix regression tests.

A synchronous ``TestClient`` is used for the suites that assert on raw HTTP
responses (headers, CORS, rate limits) because those probes inspect status codes
and header maps, not parsed models. It is deliberately *not* entered as a context
manager: entering it runs the application lifespan, which would open the
deployment's real connection pool during a test.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient


@pytest.fixture
def sync_client(app):
    yield TestClient(app, base_url="http://test")
