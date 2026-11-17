"""PART 12 / P12-22 — system dynamic variables: time zones, overrides, protection."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services.dynamic_variable_service import (
    OVERRIDABLE_SYSTEM_VARIABLES,
    PROTECTED_SYSTEM_VARIABLES,
    SystemVariableContext,
    SystemVariableError,
    build_variables,
    resolve_named,
    system_variables,
)

CTX = SystemVariableContext(
    now=datetime(2026, 11, 10, 18, 30, tzinfo=timezone.utc),
    direction="inbound",
    call_type="inbound",
    user_number="+15550100",
    agent_number="+16660200",
    default_tz="Asia/Dhaka",
)


def test_the_clock_is_whatever_was_injected() -> None:
    variables = system_variables(CTX)
    assert variables["current_time"] == "2026-11-11T00:30:00+0600"
    assert variables["current_hour"] == "0"
    assert variables["current_time_zone"] == "Asia/Dhaka"


def test_every_documented_variable_is_present() -> None:
    variables = system_variables(CTX)
    assert set(variables) == {
        "current_time",
        "current_time_zone",
        "current_hour",
        "call_type",
        "direction",
        "user_number",
        "agent_number",
    }


def test_a_zone_suffixed_variable_resolves_in_that_zone() -> None:
    assert resolve_named("current_hour_Asia_Tokyo", CTX) == "3"
    assert resolve_named("current_hour_UTC", CTX) == "18"
    assert resolve_named("current_time_America_New_York", CTX).startswith("2026-11-10T13:30")


def test_an_unknown_zone_is_named_in_the_error() -> None:
    with pytest.raises(SystemVariableError, match="unknown time zone"):
        resolve_named("current_hour_Mars_Olympus", CTX)


def test_an_unknown_variable_is_refused() -> None:
    with pytest.raises(SystemVariableError, match="unknown system variable"):
        resolve_named("current_weather", CTX)


def test_a_naive_clock_is_refused() -> None:
    with pytest.raises(SystemVariableError, match="timezone-aware"):
        SystemVariableContext(now=datetime(2026, 11, 10, 18, 30))


def test_overrides_win_where_they_are_allowed() -> None:
    variables = build_variables(CTX, overrides={"call_type": "outbound", "user_number": "+15559999"})
    assert variables["call_type"] == "outbound"
    assert variables["user_number"] == "+15559999"
    assert variables["direction"] == "inbound"


def test_direction_cannot_be_overridden() -> None:
    with pytest.raises(SystemVariableError, match="cannot be overridden"):
        build_variables(CTX, overrides={"direction": "outbound"})
    assert "direction" in PROTECTED_SYSTEM_VARIABLES


def test_defaults_are_applied_before_overrides() -> None:
    variables = build_variables(CTX, defaults={"campaign": "spring", "call_type": "sms"}, overrides={})
    assert variables["campaign"] == "spring"
    assert variables["call_type"] == "sms", "a default may replace a system value it owns"


def test_a_default_cannot_smuggle_in_a_protected_value() -> None:
    variables = build_variables(CTX, defaults={"direction": "outbound"})
    assert variables["direction"] == "inbound"


def test_custom_variables_pass_through_untouched() -> None:
    variables = build_variables(CTX, defaults={"account_number": "A-1"}, overrides={"account_number": "A-2"})
    assert variables["account_number"] == "A-2"


def test_the_overridable_set_is_deliberately_small() -> None:
    assert {"call_type", "user_number", "agent_number"} <= OVERRIDABLE_SYSTEM_VARIABLES
    assert "direction" not in OVERRIDABLE_SYSTEM_VARIABLES
