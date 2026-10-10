"""Circuit-breaker failover wrapper for STT, TTS, and LLM Pipecat processors (Sub-Phase 2C).

Opens the circuit for the active provider after `failure_threshold=2` failures
(`ErrorFrame`, HTTP 5xx, timeout > `timeout_ms=2500`, or unhandled exception) within
`window_seconds=30.0`, switches to the next provider in `fallback_providers` for
`cooldown_seconds=60.0`, and increments `voxdesk_provider_failover_total{stage,from_provider,to_provider}`.
"""

from __future__ import annotations

import asyncio
import enum
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from pipecat.frames.frames import ErrorFrame, Frame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from app.core.logging import log
from app.core.metrics import record_provider_failover


class CircuitState(str, enum.Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class ProviderSlot:
    name: str
    processor: FrameProcessor | None = None
    factory: Callable[[], FrameProcessor] | None = None
    state: CircuitState = CircuitState.CLOSED
    failure_timestamps: list[float] = field(default_factory=list)
    opened_at: float | None = None

    def get_processor(self) -> FrameProcessor:
        if self.processor is None:
            if self.factory is None:
                raise RuntimeError(f"ProviderSlot {self.name!r} has neither processor nor factory")
            self.processor = self.factory()
        return self.processor


class FailoverServiceWrapper(FrameProcessor):
    """Wraps a primary Pipecat service and ordered fallbacks with a circuit breaker."""

    def __init__(
        self,
        *,
        stage: str,
        primary: FrameProcessor | tuple[str, FrameProcessor | Callable[[], FrameProcessor]],
        fallbacks: Sequence[
            FrameProcessor | tuple[str, FrameProcessor | Callable[[], FrameProcessor]]
        ] = (),
        failure_threshold: int = 2,
        window_seconds: float = 30.0,
        cooldown_seconds: float = 60.0,
        timeout_ms: float = 2500.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__()
        self.stage = str(stage).lower()
        self.failure_threshold = max(1, int(failure_threshold))
        self.window_seconds = float(window_seconds)
        self.cooldown_seconds = float(cooldown_seconds)
        self.timeout_ms = float(timeout_ms)
        self._clock = clock

        self.slots: list[ProviderSlot] = [self._coerce_slot(primary, default_name="primary")]
        for idx, fb in enumerate(fallbacks):
            self.slots.append(self._coerce_slot(fb, default_name=f"fallback_{idx + 1}"))

        self._active_index = 0
        self.failover_events: list[dict[str, str]] = []
        self._bind_slot_push(self.slots[0])

    @staticmethod
    def _coerce_slot(
        item: FrameProcessor | tuple[str, FrameProcessor | Callable[[], FrameProcessor]],
        *,
        default_name: str,
    ) -> ProviderSlot:
        if isinstance(item, tuple) and len(item) == 2:
            name, target = item
            if isinstance(target, FrameProcessor):
                return ProviderSlot(name=str(name), processor=target)
            if callable(target):
                return ProviderSlot(name=str(name), factory=target)
        if isinstance(item, FrameProcessor):
            prov_name = str(getattr(item, "provider", None) or default_name)
            return ProviderSlot(name=prov_name, processor=item)
        raise TypeError(f"Unsupported provider slot item: {item!r}")

    def _bind_slot_push(self, slot: ProviderSlot) -> None:
        proc = slot.get_processor()
        if getattr(proc, "_failover_bound", False):
            return

        async def _intercept_push(
            frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM
        ) -> None:
            if isinstance(frame, ErrorFrame):
                raise RuntimeError(f"Provider emitted ErrorFrame: {getattr(frame, 'error', frame)}")
            await self.push_frame(frame, direction)

        proc.push_frame = _intercept_push  # type: ignore[method-assign]
        setattr(proc, "_failover_bound", True)

    @property
    def active_slot(self) -> ProviderSlot:
        self._refresh_circuits()
        return self.slots[self._active_index]

    @property
    def active_provider(self) -> str:
        return self.active_slot.name

    def _refresh_circuits(self) -> None:
        now = self._clock()
        for slot in self.slots:
            if (
                slot.state == CircuitState.OPEN
                and slot.opened_at is not None
                and (now - slot.opened_at) >= self.cooldown_seconds
            ):
                slot.state = CircuitState.HALF_OPEN

    def record_failure(self, reason: str = "") -> bool:
        """Record a failure on the active slot. Returns True if failover occurred."""
        now = self._clock()
        slot = self.slots[self._active_index]
        cutoff = now - self.window_seconds
        slot.failure_timestamps = [t for t in slot.failure_timestamps if t >= cutoff]
        slot.failure_timestamps.append(now)

        if (
            len(slot.failure_timestamps) >= self.failure_threshold
            or slot.state == CircuitState.HALF_OPEN
        ):
            slot.state = CircuitState.OPEN
            slot.opened_at = now
            return self._switch_to_next_available(from_slot=slot, reason=reason)
        return False

    def _switch_to_next_available(self, *, from_slot: ProviderSlot, reason: str = "") -> bool:
        self._refresh_circuits()
        total = len(self.slots)
        for offset in range(1, total):
            candidate_idx = (self._active_index + offset) % total
            candidate = self.slots[candidate_idx]
            if candidate.state != CircuitState.OPEN:
                self._active_index = candidate_idx
                self._bind_slot_push(candidate)
                record_provider_failover(self.stage, from_slot.name, candidate.name)
                event = {
                    "stage": self.stage,
                    "from_provider": from_slot.name,
                    "to_provider": candidate.name,
                    "reason": reason,
                }
                self.failover_events.append(event)
                log.warning("provider.failover.switched", **event)
                return True
        return False

    def register_function(self, name: str | None, handler: Any, **kwargs: Any) -> None:
        for slot in self.slots:
            proc = slot.get_processor()
            if hasattr(proc, "register_function"):
                proc.register_function(name, handler, **kwargs)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        attempts = 0
        max_attempts = max(2, len(self.slots) * self.failure_threshold)
        last_exc: Exception | None = None

        while attempts < max_attempts:
            attempts += 1
            slot = self.active_slot
            proc = slot.get_processor()
            self._bind_slot_push(slot)
            try:
                await asyncio.wait_for(
                    proc.process_frame(frame, direction),
                    timeout=self.timeout_ms / 1000.0,
                )
                if slot.state == CircuitState.HALF_OPEN:
                    slot.state = CircuitState.CLOSED
                    slot.failure_timestamps.clear()
                return
            except Exception as exc:
                last_exc = exc
                switched = self.record_failure(reason=str(exc))
                if switched:
                    # Immediately retry the frame on the newly activated fallback provider
                    continue
                if len(self.slots) == 1:
                    break
                # Even if threshold not yet reached, if more attempts are needed on same or next slot:
                break

        if last_exc is not None:
            log.error(
                "provider.failover.call_failed",
                stage=self.stage,
                provider=self.active_provider,
                error=str(last_exc),
            )
            raise last_exc
