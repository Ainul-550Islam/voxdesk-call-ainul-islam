"""PART 12 / P12-14 — IVR navigation with an optional auto-hangup."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agent.functions import FunctionHandlers
from app.agent.tools.ivr_navigation import IVRMenuRouter, decide_ivr_action

MENU = (
    "For sales, press 1. For support, press 2. For billing, press 3. "
    "To speak to an operator, press 0."
)


def test_without_auto_hangup_we_keep_navigating() -> None:
    decision = decide_ivr_action(MENU, goal="billing", menus_seen=5, auto_hangup_on_ivr=False)
    assert decision.action == "dial"
    assert decision.digits == "3"


def test_with_auto_hangup_we_still_try_to_reach_a_person_first() -> None:
    decision = decide_ivr_action(MENU, goal="billing", menus_seen=5, auto_hangup_on_ivr=True)
    assert decision.action == "dial", "a matchable menu is not a reason to hang up"


def test_auto_hangup_fires_when_the_tree_keeps_looping() -> None:
    decision = decide_ivr_action(
        "Please listen carefully as our options have changed. Press 1 for sales.",
        goal="billing",
        menus_seen=4,
        auto_hangup_on_ivr=True,
        max_menus=3,
    )
    assert decision.action == "hangup"
    assert "auto-hangup" in decision.reason


def test_an_operator_option_is_preferred_over_the_first_option() -> None:
    decision = decide_ivr_action(MENU, goal="something nobody offers")
    assert decision.digits == "0"


def test_a_human_is_never_hung_up_on() -> None:
    decision = decide_ivr_action("This is Dana speaking, how can I help you today?", menus_seen=9, auto_hangup_on_ivr=True)
    assert decision.action == "wait"


def test_no_options_and_auto_hangup_hangs_up_after_the_first_menu() -> None:
    decision = decide_ivr_action("Thank you for calling.", menus_seen=1, auto_hangup_on_ivr=True)
    assert decision.action == "hangup"


def test_no_options_without_auto_hangup_waits() -> None:
    decision = decide_ivr_action("Thank you for calling.", menus_seen=1, auto_hangup_on_ivr=False)
    assert decision.action == "wait"
    assert decision.reason == "nothing actionable in the prompt"


def test_the_decision_is_serialisable_for_the_transcript() -> None:
    payload = decide_ivr_action(MENU, goal="support").as_dict()
    assert payload == {"action": "dial", "digits": "2", "reason": "goal matched an option", "menus_seen": 0}


def test_operator_option_is_still_tried_at_the_menu_limit() -> None:
    decision = decide_ivr_action(
        "Press 1 for sales. Press 0 to speak with an operator.",
        goal="billing",
        menus_seen=1,
        auto_hangup_on_ivr=True,
        max_menus=1,
    )
    assert decision.action == "dial"
    assert decision.digits == "0"


@pytest.mark.asyncio
async def test_router_carries_menu_history_and_hangs_up_at_the_configured_limit() -> None:
    class _Provider:
        def __init__(self):
            self.dials = []
            self.hangups = []

        async def send_dtmf(self, call_sid, digits):
            self.dials.append((call_sid, digits))
            return {"status": "sent"}

        async def hangup_call(self, call_sid):
            self.hangups.append(call_sid)
            return {"status": "hung_up"}

    provider = _Provider()
    call = type("Call", (), {"call_sid": "CA-ivr-limit", "end_reason": ""})()
    router = IVRMenuRouter(goal="billing")
    menu = "Press 1 for sales. Press 2 for support."

    first = await router.step(
        menu,
        call=call,
        call_sid=call.call_sid,
        telephony_provider=provider,
        auto_hangup_on_ivr=True,
        max_menus=2,
    )
    assert first["status"] == "completed"
    assert first["digits"] == "1"
    assert len(router.history) == 1

    second = await router.step(
        menu,
        call=call,
        call_sid=call.call_sid,
        telephony_provider=provider,
        auto_hangup_on_ivr=True,
        max_menus=2,
    )
    assert second["action"] == "hangup"
    assert second["menus_seen"] == 2
    assert provider.dials == [("CA-ivr-limit", "1")]
    assert provider.hangups == ["CA-ivr-limit"]
    assert call.end_reason == "ivr_unreachable"


@pytest.mark.asyncio
async def test_function_handlers_reuse_ivr_router_and_apply_runtime_limit() -> None:
    class _Provider:
        def __init__(self):
            self.dials = []
            self.hangups = []

        async def send_dtmf(self, call_sid, digits):
            self.dials.append((call_sid, digits))
            return {"status": "sent"}

        async def hangup_call(self, call_sid):
            self.hangups.append(call_sid)
            return {"status": "hung_up"}

    call = SimpleNamespace(call_sid="CA-handler-ivr", end_reason="")
    provider = _Provider()
    handlers = FunctionHandlers(
        session=None,
        tenant=SimpleNamespace(timezone="UTC", google_calendar_id=""),
        call=call,
        provider=provider,
        runtime_config=SimpleNamespace(
            call_tooling={"auto_hangup_on_ivr": True, "ivr_max_menus": 2}
        ),
    )
    menu = "Press 1 for sales. Press 2 for support."

    first = await handlers.navigate_ivr(menu, goal="billing")
    second = await handlers.navigate_ivr(menu, goal="billing")

    assert first["digits"] == "1"
    assert second["action"] == "hangup"
    assert provider.dials == [("CA-handler-ivr", "1")]
    assert provider.hangups == ["CA-handler-ivr"]
    assert len(handlers._ivr_router.history) == 1
