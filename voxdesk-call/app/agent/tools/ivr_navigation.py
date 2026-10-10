"""IVR Menu Navigation tool and Pipecat `IVRNavigator` wrapper (Sub-Phase 2D).

Allows outbound/inbound agents to navigate multi-level IVR trees ("Press 1 for
sales, press 2 for support, press 3 for billing") to reach a target goal or
department, emitting `OutputDTMFFrame` and carrier DTMF tones at each level.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from pipecat.extensions.ivr.ivr_navigator import IVRNavigator, IVRStatus
from pipecat.frames.frames import Frame

from app.agent.tools.builtin_calls import execute_send_dtmf
from app.core.metrics import record_ivr_navigation

log = structlog.get_logger()

NAVIGATE_IVR_TOOL_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "navigate_ivr",
        "description": (
            "Analyze an automated IVR phone menu prompt and press the appropriate DTMF keypad "
            "digit(s) to reach the target department or goal."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "ivr_prompt": {
                    "type": "string",
                    "description": "The exact IVR menu prompt heard on the call (e.g. 'Press 1 for sales, press 2 for billing').",
                },
                "goal": {
                    "type": "string",
                    "description": "Target department or objective (e.g. 'billing', 'support', 'operator').",
                },
                "digits": {
                    "type": "string",
                    "description": "Optional explicit DTMF digit(s) to press if already known.",
                },
            },
            "required": ["ivr_prompt", "goal"],
        },
    },
}

_OPTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    # "Press 1 for sales", "Dial 2 for customer support", "Hit 0 to speak with an operator"
    re.compile(
        r"(?:press|dial|hit|enter|push)\s+([0-9*#]+)\s+(?:for|to\s+reach|to\s+speak\s+(?:to|with)|if\s+you\s+(?:want|need)|to)\s+([^.,;!\n]+)",
        re.IGNORECASE,
    ),
    # "For sales, press 1", "To reach billing, dial 3"
    re.compile(
        r"(?:for|to\s+reach|to\s+speak\s+(?:to|with))\s+([^.,;!\n]+?)[,\s]+(?:please\s+)?(?:press|dial|hit|enter)\s+([0-9*#]+)",
        re.IGNORECASE,
    ),
)

_HUMAN_PICKUP_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(this\s+is\s+[a-z]+|speaking[,.!\s]+how\s+(?:can|may)\s+i\s+help|my\s+name\s+is\s+[a-z]+|thanks\s+for\s+holding|how\s+can\s+i\s+help\s+you\s+today)\b",
        re.IGNORECASE,
    ),
)


def classify_ivr_vs_human(prompt_text: str) -> str:
    """Classify whether `prompt_text` is an automated `'ivr'` menu or a live `'human'`."""
    text = (prompt_text or "").strip()
    if IVRMenuRouter.parse_options(text):
        return "ivr"
    for pat in _HUMAN_PICKUP_PATTERNS:
        if pat.search(text):
            return "human"
    return "ivr"


class IVRMenuRouter:
    """Deterministic + LLM-assisted multi-level IVR menu parser and navigator."""

    def __init__(self, goal: str) -> None:
        self.goal = (goal or "").strip()
        self.history: list[dict[str, str]] = []

    @staticmethod
    def parse_options(prompt_text: str) -> list[tuple[str, str]]:
        """Extract `(digit, description)` pairs from an IVR prompt string."""
        text = prompt_text or ""
        options: list[tuple[str, str]] = []

        for match in _OPTION_PATTERNS[0].finditer(text):
            digit = match.group(1).strip()
            desc = match.group(2).strip().lower()
            options.append((digit, desc))

        for match in _OPTION_PATTERNS[1].finditer(text):
            desc = match.group(1).strip().lower()
            digit = match.group(2).strip()
            options.append((digit, desc))

        return options

    def select_digit(self, prompt_text: str, goal: str | None = None) -> str | None:
        """Select the best matching DTMF digit for `goal` from `prompt_text`."""
        target = (goal or self.goal or "").strip().lower()
        if not target:
            return None

        target_tokens = {
            tok for tok in re.split(r"[^a-z0-9]+", target) if len(tok) >= 2
        }
        options = self.parse_options(prompt_text)
        best_digit: str | None = None
        best_score = 0

        for digit, desc in options:
            score = 0
            if target in desc or desc in target:
                score += 10
            desc_tokens = {tok for tok in re.split(r"[^a-z0-9]+", desc) if len(tok) >= 2}
            score += len(target_tokens & desc_tokens) * 3
            if score > best_score:
                best_score = score
                best_digit = digit

        return best_digit

    async def step(
        self,
        prompt_text: str,
        *,
        goal: str | None = None,
        explicit_digits: str | None = None,
        call: Any | None = None,
        call_sid: str | None = None,
        telephony_provider: Any | None = None,
        frame_pusher: Callable[[Frame], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        """Process one IVR menu level and send the matching DTMF sequence."""
        if not explicit_digits and classify_ivr_vs_human(prompt_text) == "human":
            record_ivr_navigation("human_detected")
            return {
                "ok": True,
                "status": IVRStatus.COMPLETED.value,
                "classification": "human",
                "fallback_to_conversation": True,
                "goal": goal or self.goal,
                "ivr_prompt": prompt_text,
                "digits": None,
            }

        chosen = explicit_digits or self.select_digit(prompt_text, goal=goal)
        if not chosen:
            record_ivr_navigation("stuck")
            return {
                "ok": False,
                "status": IVRStatus.STUCK.value,
                "goal": goal or self.goal,
                "ivr_prompt": prompt_text,
                "digits": None,
            }

        dtmf_res = await execute_send_dtmf(
            digits=chosen,
            call=call,
            call_sid=call_sid,
            telephony_provider=telephony_provider,
            frame_pusher=frame_pusher,
        )
        record_ivr_navigation("digits_pressed")
        self.history.append(
            {
                "prompt": prompt_text,
                "digits": chosen,
                "goal": goal or self.goal,
            }
        )
        return {
            "ok": True,
            "status": IVRStatus.COMPLETED.value,
            "goal": goal or self.goal,
            "ivr_prompt": prompt_text,
            "digits": chosen,
            "level": len(self.history),
            "dtmf_result": dtmf_res,
        }


async def execute_navigate_ivr(
    *,
    ivr_prompt: str,
    goal: str,
    digits: str | None = None,
    call: Any | None = None,
    call_sid: str | None = None,
    telephony_provider: Any | None = None,
    frame_pusher: Callable[[Frame], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    router = IVRMenuRouter(goal=goal)
    return await router.step(
        ivr_prompt,
        goal=goal,
        explicit_digits=digits,
        call=call,
        call_sid=call_sid,
        telephony_provider=telephony_provider,
        frame_pusher=frame_pusher,
    )


def build_ivr_navigator(
    *,
    llm: Any,
    ivr_prompt: str,
    ivr_vad_params: Any | None = None,
) -> IVRNavigator:
    """Construct Pipecat's `IVRNavigator` processor for pipeline-level IVR traversal."""
    kwargs: dict[str, Any] = {"llm": llm, "ivr_prompt": ivr_prompt}
    if ivr_vad_params is not None:
        kwargs["ivr_vad_params"] = ivr_vad_params
    return IVRNavigator(**kwargs)
