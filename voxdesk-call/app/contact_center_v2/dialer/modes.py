"""Dial-mode capabilities and hard guard matrix."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.contact_center_v2.errors import ContactCenterV2Error


class DialMode(StrEnum):
    PREVIEW = "preview"
    PROGRESSIVE = "progressive"
    PREDICTIVE = "predictive"
    AI_AGENT_BLAST = "ai_agent_blast"


@dataclass(frozen=True)
class ModeCapabilities:
    agent_click_required: bool
    reserves_human_agent: bool
    abandonment_cap_required: bool
    affirmative_voice_consent_required: bool
    safe_harbor_required: bool
    allows_predictive_pacing: bool


MODE_GUARDS: dict[DialMode, ModeCapabilities] = {
    DialMode.PREVIEW: ModeCapabilities(True, True, False, True, True, False),
    DialMode.PROGRESSIVE: ModeCapabilities(False, True, True, True, True, False),
    DialMode.PREDICTIVE: ModeCapabilities(False, False, True, True, True, True),
    DialMode.AI_AGENT_BLAST: ModeCapabilities(False, False, True, True, True, True),
}


def capabilities(mode: str | DialMode) -> ModeCapabilities:
    try:
        return MODE_GUARDS[DialMode(str(mode))]
    except (ValueError, KeyError) as exc:
        raise ContactCenterV2Error(
            "unsupported_dial_mode", "The requested dialing mode is not supported", status_code=422
        ) from exc


def assert_transition(current: str, target: str, *, running: bool) -> None:
    """Mode changes are immutable while any campaign is running."""
    target_mode = capabilities(target)
    del target_mode
    if running and current != target:
        raise ContactCenterV2Error(
            "policy_running", "Pause dialing before changing its mode", status_code=409
        )
    if current not in {"draft", "paused", "running", "completed", "stopped"}:
        raise ContactCenterV2Error("invalid_dialer_state", "Unknown campaign state", status_code=409)


__all__ = ["DialMode", "ModeCapabilities", "MODE_GUARDS", "capabilities", "assert_transition"]
