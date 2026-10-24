"""Live checks against the real speech-to-speech providers.

Every test here is marked ``live``. Without a provider credential the module
skips. The checks open a real provider session and wait for the provider to
answer; they never send audio, so the live suite spends no tokens.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from app.agent.s2s.s2s_session import ReconnectPolicy, S2SEventType

pytestmark = pytest.mark.live


def _core_setting(name: str) -> str:
    from app.core.config import settings as core_settings

    return (getattr(core_settings, name, "") or "").strip()


async def _first_error(session, timeout_seconds: float) -> str:
    """The first error the provider reports, or an empty string."""
    stream = session.events()
    deadline = time.monotonic() + timeout_seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return ""
        try:
            event = await asyncio.wait_for(stream.__anext__(), timeout=remaining)
        except (asyncio.TimeoutError, StopAsyncIteration):
            return ""
        if event is None:
            return ""
        if event.type == S2SEventType.ERROR:
            return event.error


async def test_the_openai_realtime_session_opens_against_the_provider() -> None:
    """The Realtime WebSocket must accept the credential and the model."""
    api_key = _core_setting("openai_api_key")
    if not api_key:
        pytest.skip("OPENAI_API_KEY is not configured")

    from app.agent.s2s.openai_realtime import OpenAIRealtimeSession
    from app.carriers_v2.settings import get_carrier_v2_settings

    model = get_carrier_v2_settings().openai_realtime_model
    session = OpenAIRealtimeSession(
        api_key=api_key,
        model=model,
        # One attempt only: a live check must report the first refusal
        # instead of retrying a rejected credential.
        reconnect_policy=ReconnectPolicy(max_attempts=0),
    )
    await session.start()
    try:
        error = await _first_error(session, timeout_seconds=20.0)
        assert not error, f"the live Realtime session failed: {error}"
    finally:
        await session.close()


async def test_the_gemini_live_session_opens_against_the_provider() -> None:
    """The Gemini Live WebSocket must accept the credential and the model."""
    api_key = _core_setting("google_api_key")
    if not api_key:
        pytest.skip("GOOGLE_API_KEY is not configured")

    from app.agent.s2s.gemini_live import GeminiLiveSession
    from app.carriers_v2.settings import get_carrier_v2_settings

    model = get_carrier_v2_settings().gemini_live_model
    session = GeminiLiveSession(
        api_key=api_key,
        model=model,
        reconnect_policy=ReconnectPolicy(max_attempts=0),
    )
    await session.start()
    try:
        error = await _first_error(session, timeout_seconds=20.0)
        assert not error, f"the live Gemini session failed: {error}"
    finally:
        await session.close()
