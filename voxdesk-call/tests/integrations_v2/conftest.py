"""Shared fixtures and deterministic ``httpx.MockTransport`` protocol helpers for Integrations v2 tests."""

from __future__ import annotations

from typing import Callable

import httpx
import pytest

import app.db.models  # noqa: F401
import app.db.integrations_v2_models  # noqa: F401
from app.integrations_v2.agent_tools.executor import reset_conversation_tool_counts
from app.integrations_v2.collab.notification_rules import reset_notification_rule_windows
from app.integrations_v2.core.http_client import Int2HttpClient, reset_rate_limit_state
from app.integrations_v2.settings import reset_int2_settings_cache


@pytest.fixture(autouse=True)
def _reset_int2_runtime_caches():
    reset_rate_limit_state()
    reset_conversation_tool_counts()
    reset_notification_rule_windows()
    reset_int2_settings_cache()
    yield
    reset_rate_limit_state()
    reset_conversation_tool_counts()
    reset_notification_rule_windows()
    reset_int2_settings_cache()


async def _instant_sleep(_seconds: float) -> None:
    return None


def make_protocol_http_client(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    max_retries: int = 2,
) -> Int2HttpClient:
    """Build an ``Int2HttpClient`` wired to a real ``httpx.MockTransport`` protocol handler."""
    return Int2HttpClient(
        timeout_s=5.0,
        max_retries=max_retries,
        transport=httpx.MockTransport(handler),
        sleep_fn=_instant_sleep,
        resolve_dns=False,
    )
