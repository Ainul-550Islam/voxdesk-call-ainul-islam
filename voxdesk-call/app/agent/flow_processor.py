# File: app/agent/flow_processor.py — Pipecat FrameProcessor adapter for FlowRunner: swaps system prompt & tools on node transitions (Part 5 / Gate G6)
"""Pipecat `FrameProcessor` adapter for `FlowRunner` (`pipecat-ai==0.0.94`).

Responsibilities:
  - Intercepts final caller `TranscriptionFrame`s in the live voice pipeline and
    forwards user turns to `FlowRunner.step(...)`.
  - When a node transition occurs:
      1. Updates the shared `OpenAILLMContext` system prompt and tool schemas.
      2. Emits `LLMMessagesUpdateFrame` and `LLMSetToolsFrame` (plus
         `LLMUpdateSettingsFrame` when the target node defines `model_override`
         and `TTSTextFrame` when the node defines `speak_text`).
      3. Appends structured `NodeTransitionEvent` entries to the call timeline
         (`call.transfer_context["flow_transitions"]`).
  - Bridges `FlowRunner` action nodes (`function`, `transfer`, `press_digit`,
    `send_sms`) to `FunctionHandlers` when running in a live call.
"""

from __future__ import annotations

import inspect
from typing import Any, Awaitable, Callable

from pipecat.frames.frames import (
    InterimTranscriptionFrame,
    LLMMessagesUpdateFrame,
    LLMSetToolsFrame,
    LLMUpdateSettingsFrame,
    StartFrame,
    TTSTextFrame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from app.builder.flow_runner import (
    FlowRunner,
    FlowTurnResult,
    NodeTransitionEvent,
)
from app.core.logging import log


class FlowProcessor(FrameProcessor):
    """Pipecat `FrameProcessor` that drives `FlowRunner` and swaps prompt/tools on transitions."""

    def __init__(
        self,
        runner: FlowRunner,
        *,
        call: Any | None = None,
        context: Any | None = None,
        frame_pusher: Callable[[Any], Awaitable[None]] | None = None,
        on_transition: Callable[[NodeTransitionEvent], Awaitable[None] | None] | None = None,
        name: str | None = None,
    ) -> None:
        super().__init__(name=name or "FlowProcessor")
        self.runner = runner
        self.call = call
        self.context = context
        self._frame_pusher = frame_pusher
        self._on_transition = on_transition
        self.transition_timeline: list[dict[str, Any]] = []
        self.action_timeline: list[dict[str, Any]] = []
        self._initialized = False

    async def _emit_frame(
        self,
        frame: Any,
        direction: FrameDirection = FrameDirection.DOWNSTREAM,
    ) -> None:
        if self._frame_pusher is not None:
            await self._frame_pusher(frame)
        else:
            await self.push_frame(frame, direction)

    def _sync_context_and_build_messages(
        self, system_prompt: str, tools: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Update `self.context` (if attached) with the new system prompt & tools."""
        existing_messages: list[dict[str, Any]] = []
        if self.context is not None:
            if hasattr(self.context, "get_messages"):
                existing_messages = [dict(m) for m in self.context.get_messages()]
            elif hasattr(self.context, "messages") and isinstance(self.context.messages, list):
                existing_messages = [dict(m) for m in self.context.messages]

        non_system = [m for m in existing_messages if m.get("role") != "system"]
        updated_messages = [{"role": "system", "content": system_prompt}, *non_system]

        if self.context is not None:
            if hasattr(self.context, "set_messages"):
                self.context.set_messages(list(updated_messages))
            elif hasattr(self.context, "messages") and isinstance(self.context.messages, list):
                self.context.messages.clear()
                self.context.messages.extend(updated_messages)
            if hasattr(self.context, "set_tools"):
                try:
                    self.context.set_tools(list(tools))
                except Exception:
                    __import__("logging").getLogger(__name__).debug("suppressed_exception", exc_info=True)
                    pass

        return updated_messages

    async def _record_transitions_and_actions(self, result: FlowTurnResult) -> None:
        for tr in result.transitions:
            tr_dict = tr.to_dict()
            self.transition_timeline.append(tr_dict)
            if self._on_transition is not None:
                cb_res = self._on_transition(tr)
                if inspect.isawaitable(cb_res):
                    await cb_res

        for act in result.actions:
            self.action_timeline.append(act.to_dict())

        if self.call is not None:
            raw_ctx = getattr(self.call, "transfer_context", None)
            ctx = dict(raw_ctx) if isinstance(raw_ctx, dict) else {}
            ctx["flow_current_node_id"] = result.current_node_id
            ctx["flow_variables"] = dict(result.variables)
            ctx["flow_transitions"] = list(self.transition_timeline)
            ctx["flow_actions"] = list(self.action_timeline)
            try:
                self.call.transfer_context = ctx
            except Exception:
                __import__("logging").getLogger(__name__).debug("suppressed_exception", exc_info=True)
                pass

    async def _apply_turn_result(
        self,
        result: FlowTurnResult,
        direction: FrameDirection = FrameDirection.DOWNSTREAM,
    ) -> None:
        await self._record_transitions_and_actions(result)

        if result.transitioned or not self._initialized:
            updated_messages = self._sync_context_and_build_messages(
                result.system_prompt, result.tools
            )
            await self._emit_frame(
                LLMMessagesUpdateFrame(messages=updated_messages, run_llm=False),
                direction,
            )
            await self._emit_frame(
                LLMSetToolsFrame(tools=list(result.tools)),
                direction,
            )
            if result.model_override:
                settings_dict = {
                    k: v for k, v in result.model_override.items() if v is not None
                }
                if settings_dict:
                    await self._emit_frame(
                        LLMUpdateSettingsFrame(settings=settings_dict),
                        direction,
                    )

            log.info(
                "flow_processor.node_transition",
                call_id=str(getattr(self.call, "id", "")) if self.call else None,
                from_node=result.previous_node_id,
                to_node=result.current_node_id,
                to_type=result.current_node_type,
                tools_count=len(result.tools),
                ended=result.ended,
                transferred=result.transferred,
            )

        if result.speak_text:
            await self._emit_frame(TTSTextFrame(text=result.speak_text), direction)

    async def initialize(self) -> FlowTurnResult:
        """Start the underlying `FlowRunner` and emit initial prompt/tool frames."""
        result = await self.runner.start()
        await self._apply_turn_result(result, FrameDirection.DOWNSTREAM)
        self._initialized = True
        return result

    async def process_frame(
        self,
        frame: Any,
        direction: FrameDirection = FrameDirection.DOWNSTREAM,
    ) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, StartFrame) and not self._initialized:
            await self.push_frame(frame, direction)
            await self.initialize()
            return

        if isinstance(frame, TranscriptionFrame) and not isinstance(
            frame, InterimTranscriptionFrame
        ):
            if not self._initialized:
                await self.initialize()

            text = (getattr(frame, "text", "") or "").strip()
            if text:
                turn_result = await self.runner.step(text)
                await self._apply_turn_result(turn_result, direction)

        await self.push_frame(frame, direction)


def build_flow_processor_for_runtime(
    *,
    runtime_config: Any,
    handlers: Any | None = None,
    call: Any | None = None,
    context: Any | None = None,
    available_tools: list[dict[str, Any]] | None = None,
    judge: Callable[..., Awaitable[bool] | bool] | None = None,
) -> FlowProcessor | None:
    """Construct a `FlowProcessor` if `runtime_config` has a conversation flow enabled."""
    raw_cfg = getattr(runtime_config, "raw_config", None) or {}
    if not isinstance(raw_cfg, dict):
        return None
    flow_data = raw_cfg.get("flow")
    mode = str(raw_cfg.get("mode") or "single_prompt").strip().lower()
    if mode != "flow" and not (isinstance(flow_data, dict) and flow_data.get("nodes")):
        return None
    if not isinstance(flow_data, dict) or not flow_data.get("nodes"):
        return None

    async def _fn_handler(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        if handlers is not None and hasattr(handlers, "dispatch"):
            return await handlers.dispatch(tool_name, args)
        return {"ok": True, "tool_name": tool_name, "arguments": args}

    async def _transfer_handler(payload: dict[str, Any]) -> dict[str, Any]:
        if handlers is not None and hasattr(handlers, "dispatch"):
            dest = str(payload.get("destination") or "")
            t_mode = str(payload.get("transfer_mode") or "cold")
            tool_name = "warm_transfer" if t_mode == "warm" else "escalate_to_human"
            return await handlers.dispatch(
                tool_name,
                {
                    "destination": dest,
                    "reason": payload.get("whisper_text") or "flow_transfer",
                },
            )
        return {"ok": True, **payload}

    async def _dtmf_handler(digits: str, pause_ms: int) -> dict[str, Any]:
        if handlers is not None and hasattr(handlers, "dispatch"):
            return await handlers.dispatch(
                "send_dtmf", {"digits": digits, "pause_ms": pause_ms}
            )
        return {"ok": True, "digits": digits, "pause_ms": pause_ms}

    async def _sms_handler(message: str, to_number: str | None) -> dict[str, Any]:
        if handlers is not None and hasattr(handlers, "dispatch"):
            return await handlers.dispatch(
                "send_sms_confirmation",
                {"message": message, "to_number": to_number or ""},
            )
        return {"ok": True, "message": message, "to_number": to_number}

    init_vars: dict[str, Any] = {}
    if call is not None:
        init_vars["call_id"] = str(getattr(call, "id", ""))
        init_vars["call_sid"] = str(getattr(call, "call_sid", ""))
        init_vars["caller_number"] = str(getattr(call, "from_number", ""))
        init_vars["from_number"] = str(getattr(call, "from_number", ""))
        init_vars["to_number"] = str(getattr(call, "to_number", ""))

    runner = FlowRunner(
        flow_data,
        base_system_prompt=str(getattr(runtime_config, "system_prompt", "") or ""),
        available_tools=list(available_tools or getattr(runtime_config, "tools", []) or []),
        initial_variables=init_vars,
        judge=judge,
        function_handler=_fn_handler,
        transfer_handler=_transfer_handler,
        dtmf_handler=_dtmf_handler,
        sms_handler=_sms_handler,
    )
    return FlowProcessor(runner, call=call, context=context)
