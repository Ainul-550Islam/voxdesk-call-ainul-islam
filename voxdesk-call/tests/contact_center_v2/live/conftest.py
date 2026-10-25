"""Live CC2 tests are strictly opt-in and never run in the normal gate."""
from __future__ import annotations

import os

import pytest
import pytest_asyncio
from httpx import AsyncClient


def pytest_runtest_setup(item: pytest.Item) -> None:
    if item.get_closest_marker("live") and os.environ.get("VOXDESK_LIVE_TESTS") != "1":
        pytest.skip("set VOXDESK_LIVE_TESTS=1 and CC2_LIVE_* values to run carrier tests")


@pytest_asyncio.fixture
async def live_api():
    required = (
        "CC2_LIVE_API_BASE_URL",
        "CC2_LIVE_BEARER_TOKEN",
        "CC2_LIVE_ENVIRONMENT_ID",
        "CC2_LIVE_DESTINATION",
        "CC2_LIVE_CALLER_ID",
    )
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        pytest.skip(f"live carrier configuration missing: {', '.join(missing)}")
    base = os.environ["CC2_LIVE_API_BASE_URL"].rstrip("/")
    if not base.startswith("https://") and os.environ.get("CC2_LIVE_ALLOW_HTTP") != "1":
        pytest.skip("live API must use HTTPS unless CC2_LIVE_ALLOW_HTTP=1 is explicit")
    async with AsyncClient(
        base_url=base,
        headers={"Authorization": f"Bearer {os.environ['CC2_LIVE_BEARER_TOKEN']}"},
        timeout=45,
    ) as client:
        yield client
