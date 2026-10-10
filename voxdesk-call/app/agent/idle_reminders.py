"""Idle caller reminder and graceful auto-hangup processor (Sub-Phase 2B).

Wraps Pipecat's `UserIdleProcessor`:
- Fires every `reminder_trigger_ms / 1000.0` seconds of user silence after the bot finishes speaking.
- For `retry_count <= reminder_max_count`, speaks a polite check-in prompt via `TTSSpeakFrame`
  and returns `True` to keep monitoring.
- When `retry_count > reminder_max_count`, speaks a polite goodbye and pushes `EndFrame`,
  returning `False` to stop idle monitoring.
"""

from __future__ import annotations

from typing import Any, Sequence

from pipecat.frames.frames import EndFrame, TTSSpeakFrame
from pipecat.processors.frame_processor import FrameDirection
from pipecat.processors.user_idle_processor import UserIdleProcessor

from app.core.logging import log

DEFAULT_REMINDER_PROMPTS: tuple[str, ...] = (
    "Are you still there? Take your time, I'm here whenever you're ready.",
    "Just checking in—are you still on the line?",
    "I'm still here if you need anything.",
)

DEFAULT_GOODBYE_MESSAGE = (
    "I haven't heard from you in a moment, so I'll go ahead and disconnect now. "
    "Feel free to call back anytime. Goodbye!"
)


class IdleReminderController:
    """Stateful callback handler for `UserIdleProcessor`."""

    def __init__(
        self,
        *,
        reminder_trigger_ms: int = 10000,
        reminder_max_count: int = 2,
        prompts: Sequence[str] | None = None,
        goodbye_message: str = DEFAULT_GOODBYE_MESSAGE,
    ) -> None:
        self.reminder_trigger_ms = max(500, int(reminder_trigger_ms))
        self.reminder_max_count = max(0, int(reminder_max_count))
        self.prompts = tuple(prompts) if prompts else DEFAULT_REMINDER_PROMPTS
        self.goodbye_message = goodbye_message
        self.fired_count = 0
        self.ended_call = False

    @property
    def timeout_seconds(self) -> float:
        return round(self.reminder_trigger_ms / 1000.0, 3)

    async def on_idle(
        self,
        processor: UserIdleProcessor,
        retry_count: int,
    ) -> bool:
        """Callback invoked by `UserIdleProcessor` with 1-based `retry_count`."""
        if retry_count <= self.reminder_max_count:
            self.fired_count = retry_count
            idx = min(retry_count - 1, len(self.prompts) - 1)
            message = self.prompts[idx]
            log.info(
                "idle_reminder.checkin",
                retry_count=retry_count,
                max_count=self.reminder_max_count,
                message=message,
            )
            await processor.push_frame(
                TTSSpeakFrame(message), FrameDirection.DOWNSTREAM
            )
            return True

        self.ended_call = True
        log.info(
            "idle_reminder.max_exceeded_hangup",
            retry_count=retry_count,
            max_count=self.reminder_max_count,
        )
        if self.goodbye_message:
            await processor.push_frame(
                TTSSpeakFrame(self.goodbye_message), FrameDirection.DOWNSTREAM
            )
        await processor.push_frame(EndFrame(), FrameDirection.DOWNSTREAM)
        return False


def build_idle_reminder_processor(
    cfg: Any,
    *,
    prompts: Sequence[str] | None = None,
    goodbye_message: str = DEFAULT_GOODBYE_MESSAGE,
) -> tuple[UserIdleProcessor, IdleReminderController]:
    """Construct a `(UserIdleProcessor, IdleReminderController)` from `RuntimeConfig`."""
    trigger_ms = int(getattr(cfg, "reminder_trigger_ms", 10000))
    max_count = int(getattr(cfg, "reminder_max_count", 2))
    controller = IdleReminderController(
        reminder_trigger_ms=trigger_ms,
        reminder_max_count=max_count,
        prompts=prompts,
        goodbye_message=goodbye_message,
    )
    processor = UserIdleProcessor(
        callback=controller.on_idle,
        timeout=controller.timeout_seconds,
    )
    return processor, controller
