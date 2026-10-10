"""Unit tests for 2-level IVR Menu Navigation (Sub-Phase 2D)."""

from __future__ import annotations

import pytest
from pipecat.frames.frames import Frame, OutputDTMFFrame

from app.agent.tools.ivr_navigation import IVRMenuRouter, execute_navigate_ivr
from app.telephony.providers.base import SimulatedTelephonyAdapter


@pytest.mark.asyncio
async def test_two_level_ivr_navigation_reaches_billing_extension():
    provider = SimulatedTelephonyAdapter()
    emitted: list[Frame] = []

    async def _push(frame: Frame) -> None:
        emitted.append(frame)

    router = IVRMenuRouter(goal="billing")

    # Level 1 IVR menu
    level1 = await router.step(
        "Thank you for calling Acme Health. Press 1 for appointments, press 2 for billing and insurance, or press 0 for the operator.",
        call_sid="CA_IVR_001",
        telephony_provider=provider,
        frame_pusher=_push,
    )
    assert level1["ok"] is True
    assert level1["digits"] == "2"
    assert level1["level"] == 1

    # Level 2 IVR submenu
    level2 = await router.step(
        "For new claims, press 1. To speak with a billing specialist about an existing invoice, press 4.",
        goal="existing invoice billing specialist",
        call_sid="CA_IVR_001",
        telephony_provider=provider,
        frame_pusher=_push,
    )
    assert level2["ok"] is True
    assert level2["digits"] == "4"
    assert level2["level"] == 2

    buttons = [f.button.value for f in emitted if isinstance(f, OutputDTMFFrame)]
    assert buttons == ["2", "4"]


@pytest.mark.asyncio
async def test_execute_navigate_ivr_tool_helper():
    res = await execute_navigate_ivr(
        ivr_prompt="For customer support, please dial 3.",
        goal="customer support",
        call_sid="CA_IVR_002",
        telephony_provider=SimulatedTelephonyAdapter(),
    )
    assert res["ok"] is True
    assert res["digits"] == "3"


@pytest.mark.asyncio
async def test_ivr_navigation_detects_human_pickup_and_falls_back():
    from app.agent.tools.ivr_navigation import classify_ivr_vs_human

    human_line = "Hi, this is Sarah speaking, how can I help you today?"
    assert classify_ivr_vs_human(human_line) == "human"
    res = await execute_navigate_ivr(
        ivr_prompt=human_line,
        goal="billing",
        call_sid="CA_IVR_HUMAN_003",
        telephony_provider=SimulatedTelephonyAdapter(),
    )
    assert res["ok"] is True
    assert res["classification"] == "human"
    assert res["fallback_to_conversation"] is True
    assert res["digits"] is None

