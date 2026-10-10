"""LLM-played simulated caller for multi-turn agent evaluation (Part 6 / Gate G7).

Provides ``SimulatedCaller``, which drives multi-turn conversations against a
pinned ``AgentVersion`` using ``app/ai/gateway.py`` (``govern``) when a live
model or custom executor is attached, and supports deterministic seeded turn
generation and criteria judging for reproducible test runs.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import costs, gateway
from app.db.models import Tenant

ALLOWED_INTERRUPTION_STYLES = frozenset(
    {"none", "polite", "normal", "frequent", "impatient"}
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_live_llm_configured() -> bool:
    return bool(
        os.environ.get("OPENAI_API_KEY")
        or os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("GROQ_API_KEY")
    )


@dataclass(frozen=True)
class _SyntheticGatewayContext:
    """Minimal TenantContext-compatible wrapper for headless simulation runs."""

    tenant: Tenant
    tenant_id: uuid.UUID
    environment_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    is_superadmin: bool = False


def _seeded_digest(seed: int | None, *parts: object) -> bytes:
    effective_seed = 0 if seed is None else int(seed)
    raw = "|".join([str(effective_seed), *(str(p) for p in parts)]).encode("utf-8")
    return hashlib.sha256(raw).digest()


def _render_template_vars(text: str, variables: dict[str, Any]) -> str:
    out = text
    for key, val in sorted(variables.items()):
        out = out.replace(f"{{{{{key}}}}}", str(val)).replace(f"{{{key}}}", str(val))
    return out


@dataclass
class SimulatedCaller:
    """Autonomous caller persona that converses with an Agent to achieve a goal."""

    persona: str
    goal: str
    variables: dict[str, Any] = field(default_factory=dict)
    interruption_style: str = "normal"
    seed: int | None = None
    max_turns: int = 5
    success_criteria: list[str] = field(default_factory=list)
    transcript: list[dict[str, Any]] = field(default_factory=list)
    is_mock_provider: bool = False
    provider: str = "deterministic"
    model: str = "simulated-caller-v1"
    total_tokens: int = 0
    total_cost_usd: float = 0.0

    def __post_init__(self) -> None:
        self.persona = (self.persona or "Standard customer").strip()
        self.goal = (self.goal or "Complete the inquiry").strip()
        self.variables = dict(self.variables or {})
        style = (self.interruption_style or "normal").strip().lower()
        if style not in ALLOWED_INTERRUPTION_STYLES:
            raise ValueError(
                f"Unsupported interruption_style {self.interruption_style!r}; "
                f"expected one of {sorted(ALLOWED_INTERRUPTION_STYLES)}"
            )
        self.interruption_style = style
        self.max_turns = max(1, min(int(self.max_turns or 5), 25))
        self.success_criteria = [
            str(c).strip() for c in (self.success_criteria or []) if str(c).strip()
        ]

    def record_agent_turn(
        self,
        content: str,
        *,
        turn_index: int | None = None,
        intent: str | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        latency_ms: int = 0,
    ) -> dict[str, Any]:
        idx = len(self.transcript) if turn_index is None else int(turn_index)
        entry: dict[str, Any] = {
            "role": "assistant",
            "content": str(content or "").strip(),
            "turn_index": idx,
            "intent": intent or "general_response",
            "tool_calls": list(tool_calls or []),
            "latency_ms": int(latency_ms),
            "timestamp": _now_iso(),
        }
        self.transcript.append(entry)
        return entry

    def _should_interrupt(self, turn_index: int, agent_message: str) -> bool:
        if self.interruption_style in {"none", "polite"}:
            return False
        digest = _seeded_digest(
            self.seed, "interrupt", self.interruption_style, turn_index, agent_message
        )
        score = digest[0] / 255.0
        if self.interruption_style == "impatient":
            return score < 0.65 or len(agent_message) > 120
        if self.interruption_style == "frequent":
            return score < 0.50
        # "normal"
        return score < 0.20 and len(agent_message) > 160

    def _build_deterministic_utterance(
        self, turn_index: int, agent_message: str
    ) -> tuple[str, bool]:
        """Generate a deterministic, seed-stable caller utterance toward ``self.goal``."""
        rendered_goal = _render_template_vars(self.goal, self.variables)
        rendered_persona = _render_template_vars(self.persona, self.variables)
        digest = _seeded_digest(
            self.seed,
            "utterance",
            rendered_persona,
            rendered_goal,
            turn_index,
            json.dumps(self.variables, sort_keys=True, default=str),
        )
        variant_idx = digest[1] % 4
        var_summary = ", ".join(
            f"{k}={v}" for k, v in sorted(self.variables.items()) if v is not None
        )

        lower_agent = (agent_message or "").lower()
        goal_lower = rendered_goal.lower()

        # Detect if the agent already fulfilled the caller's goal
        goal_satisfied_in_reply = False
        if any(
            kw in lower_agent
            for kw in (
                "confirmed",
                "apt-2026",
                "transferring you",
                "in_transit",
                "starts at $",
            )
        ):
            if "book" in goal_lower or "appointment" in goal_lower:
                goal_satisfied_in_reply = "confirmed" in lower_agent or "apt-" in lower_agent
            elif "transfer" in goal_lower or "human" in goal_lower or "escalat" in goal_lower:
                goal_satisfied_in_reply = "transferring" in lower_agent
            elif "order" in goal_lower or "status" in goal_lower:
                goal_satisfied_in_reply = "in_transit" in lower_agent or "order" in lower_agent
            elif "pric" in goal_lower or "plan" in goal_lower or "cost" in goal_lower:
                goal_satisfied_in_reply = "starts at" in lower_agent or "pricing" in lower_agent
            else:
                goal_satisfied_in_reply = turn_index >= 1

        if goal_satisfied_in_reply or turn_index >= self.max_turns - 1:
            closings = (
                "That answers everything I needed, thank you! Goodbye.",
                "Perfect, thanks for taking care of that. Bye now.",
                "Great, I appreciate the help. Have a good day, goodbye.",
                "Thank you, that resolves my request. Goodbye!",
            )
            return closings[variant_idx], True

        openers = (
            f"Hi, as a {rendered_persona}, I need help to {rendered_goal}.",
            f"Hello! I'm calling because I want to {rendered_goal} ({rendered_persona}).",
            f"Good day — my goal today is to {rendered_goal}.",
            f"Hey there, could you help me {rendered_goal} right away?",
        )
        followups = (
            f"To clarify, I still need to {rendered_goal}.",
            f"Can we complete that now? Specifically: {rendered_goal}.",
            f"Here are my details so we can {rendered_goal}.",
            f"Please proceed to {rendered_goal}.",
        )
        base = openers[variant_idx] if turn_index == 0 else followups[variant_idx]
        if var_summary:
            base = f"{base} My details: {var_summary}."
        if self.interruption_style == "impatient":
            base = f"[Interrupting] Let's move quickly — {base}"
        return base, False

    def _build_gateway_prompt(self, turn_index: int, agent_message: str) -> str:
        payload = {
            "task": "simulated_caller_turn",
            "seed": self.seed,
            "turn_index": turn_index,
            "persona": _render_template_vars(self.persona, self.variables),
            "goal": _render_template_vars(self.goal, self.variables),
            "variables": self.variables,
            "interruption_style": self.interruption_style,
            "last_agent_message": agent_message,
            "history": [
                {"role": t.get("role"), "content": t.get("content")}
                for t in self.transcript[-8:]
            ],
        }
        return json.dumps(payload, sort_keys=True, default=str)

    async def generate_next_turn(
        self,
        session: AsyncSession | None = None,
        ctx: Any | None = None,
        *,
        tenant: Tenant | None = None,
        agent_message: str = "",
        turn_index: int = 0,
        executor: Any | None = None,
        allow_mock_fallback: bool = False,
    ) -> dict[str, Any]:
        """Produce the next caller utterance via ``app.ai.gateway.govern`` or deterministic seed."""
        interrupted = self._should_interrupt(turn_index, agent_message)
        prompt_text = self._build_gateway_prompt(turn_index, agent_message)

        gateway_ctx = ctx
        if gateway_ctx is None and tenant is not None:
            gateway_ctx = _SyntheticGatewayContext(tenant=tenant, tenant_id=tenant.id)

        if session is not None and gateway_ctx is not None and executor is not None:
            gw_result = await gateway.govern(
                session,
                gateway_ctx,
                text=prompt_text,
                channel="text",
                executor=executor,
                tokens_estimate=64,
                record_usage=False,
            )
            self.is_mock_provider = False
            self.provider = gw_result.provider
            self.model = gw_result.model
            tokens = int(gw_result.telemetry.get("tokens") or 24)
            self.total_tokens += tokens
            cost_est = costs.estimate(provider=gw_result.provider, tokens=tokens)
            if cost_est.get("known") and cost_est.get("provider_cost_usd"):
                self.total_cost_usd = round(
                    self.total_cost_usd + float(cost_est["provider_cost_usd"]), 6
                )
            raw_text = (gw_result.text or "").strip()
            goal_reached = False
            utterance = raw_text
            if raw_text.startswith("{"):
                try:
                    parsed = json.loads(raw_text)
                    utterance = str(
                        parsed.get("utterance")
                        or parsed.get("content")
                        or parsed.get("text")
                        or raw_text
                    ).strip()
                    goal_reached = bool(parsed.get("goal_reached", False))
                    if "interrupted" in parsed:
                        interrupted = bool(parsed["interrupted"])
                except ValueError:
                    utterance = raw_text
            if not utterance:
                utterance, goal_reached = self._build_deterministic_utterance(
                    turn_index, agent_message
                )
        else:
            if not _is_live_llm_configured() and not allow_mock_fallback:
                raise RuntimeError(
                    "PROVIDER_NOT_CONFIGURED: No live LLM provider is configured for SimulatedCaller "
                    "and allow_mock_fallback is False."
                )
            self.is_mock_provider = not _is_live_llm_configured()
            self.provider = "openai" if _is_live_llm_configured() else "deterministic_mock"
            self.model = "gpt-4o-mini" if _is_live_llm_configured() else "simulated-caller-seed-v1"
            utterance, goal_reached = self._build_deterministic_utterance(
                turn_index, agent_message
            )
            est_tokens = max(10, len(utterance.split()) * 2)
            self.total_tokens += est_tokens

        turn_entry: dict[str, Any] = {
            "role": "user",
            "content": utterance,
            "turn_index": len(self.transcript),
            "caller_step": turn_index,
            "interrupted": interrupted,
            "interruption_style": self.interruption_style,
            "goal_reached": goal_reached,
            "seed": self.seed,
            "is_mock_provider": self.is_mock_provider,
            "timestamp": _now_iso(),
        }
        self.transcript.append(turn_entry)
        return turn_entry

    async def judge_conversation(
        self,
        session: AsyncSession | None = None,
        ctx: Any | None = None,
        *,
        tenant: Tenant | None = None,
        transcript: list[dict[str, Any]] | None = None,
        final_output: dict[str, Any] | None = None,
        success_criteria: list[str] | None = None,
        executor: Any | None = None,
        allow_mock_fallback: bool = False,
    ) -> dict[str, Any]:
        """Judge whether the conversation achieved the caller's goal and all success criteria."""
        turns = list(transcript if transcript is not None else self.transcript)
        criteria = [
            str(c).strip()
            for c in (
                success_criteria
                if success_criteria is not None
                else self.success_criteria
            )
            if str(c).strip()
        ]
        if not criteria:
            criteria = [self.goal]

        gateway_ctx = ctx
        if gateway_ctx is None and tenant is not None:
            gateway_ctx = _SyntheticGatewayContext(tenant=tenant, tenant_id=tenant.id)

        if session is not None and gateway_ctx is not None and executor is not None:
            judge_prompt = json.dumps(
                {
                    "task": "judge_simulated_call",
                    "goal": self.goal,
                    "persona": self.persona,
                    "success_criteria": criteria,
                    "transcript": [
                        {"role": t.get("role"), "content": t.get("content")}
                        for t in turns
                    ],
                },
                sort_keys=True,
                default=str,
            )
            gw_res = await gateway.govern(
                session,
                gateway_ctx,
                text=judge_prompt,
                channel="text",
                executor=executor,
                tokens_estimate=96,
                record_usage=False,
            )
            try:
                parsed = json.loads(gw_res.text or "{}")
                if isinstance(parsed, dict) and "passed" in parsed:
                    verdicts = parsed.get("criteria_verdicts") or [
                        {
                            "criterion": c,
                            "passed": bool(parsed["passed"]),
                            "score": float(parsed.get("overall_score", 100.0 if parsed["passed"] else 0.0)),
                            "rationale": str(parsed.get("rationale") or "Evaluated by governed LLM judge."),
                        }
                        for c in criteria
                    ]
                    return {
                        "passed": bool(parsed["passed"]),
                        "verdict": "PASSED" if parsed["passed"] else "FAILED",
                        "overall_score": float(
                            parsed.get("overall_score", 100.0 if parsed["passed"] else 0.0)
                        ),
                        "rationale": str(
                            parsed.get("rationale") or "Evaluated by governed LLM judge."
                        ),
                        "criteria_verdicts": verdicts,
                        "judge_provider": gw_res.provider,
                        "judge_model": gw_res.model,
                        "is_mock_provider": False,
                    }
            except ValueError:
                pass

        if not _is_live_llm_configured() and not allow_mock_fallback and executor is None:
            raise RuntimeError(
                "PROVIDER_NOT_CONFIGURED: No live LLM provider is configured to judge success criteria."
            )

        assistant_text = " ".join(
            str(t.get("content") or "")
            for t in turns
            if t.get("role") == "assistant"
        ).lower()
        all_tools = [
            str(tc.get("name") or tc.get("tool_name") or "").lower()
            for t in turns
            if t.get("role") == "assistant"
            for tc in (t.get("tool_calls") or [])
            if isinstance(tc, dict)
        ]
        transferred = bool((final_output or {}).get("transferred"))

        criteria_verdicts: list[dict[str, Any]] = []
        for crit in criteria:
            c_low = crit.lower()
            passed_crit = False
            reason = ""
            if "book" in c_low or "appointment" in c_low or "schedule" in c_low:
                passed_crit = (
                    "confirmed" in assistant_text
                    or "appointment" in assistant_text
                    or any("book" in tl for tl in all_tools)
                )
                reason = (
                    "Agent confirmed appointment booking."
                    if passed_crit
                    else "Agent did not confirm an appointment or invoke a booking tool."
                )
            elif "transfer" in c_low or "human" in c_low or "escalat" in c_low:
                passed_crit = transferred or "transferring" in assistant_text
                reason = (
                    "Agent transferred the caller to a human queue."
                    if passed_crit
                    else "No transfer occurred during the conversation."
                )
            elif "order" in c_low or "status" in c_low:
                passed_crit = "order" in assistant_text or "in_transit" in assistant_text
                reason = (
                    "Agent provided order status details."
                    if passed_crit
                    else "Agent did not provide order status."
                )
            elif "pric" in c_low or "cost" in c_low or "plan" in c_low:
                passed_crit = "pricing" in assistant_text or "$" in assistant_text
                reason = (
                    "Agent answered pricing inquiry."
                    if passed_crit
                    else "Agent did not provide pricing information."
                )
            else:
                stop_words = {
                    "the", "and", "for", "that", "with", "must", "should",
                    "agent", "caller", "user", "call", "from", "into", "this",
                }
                keywords = [
                    w.strip(".,!?:;\"'()[]{}")
                    for w in c_low.split()
                    if len(w.strip(".,!?:;\"'()[]{}")) >= 4
                    and w.strip(".,!?:;\"'()[]{}") not in stop_words
                ]
                if keywords:
                    matched = [kw for kw in keywords if kw in assistant_text or kw in " ".join(all_tools)]
                    passed_crit = len(matched) >= max(1, (len(keywords) + 1) // 2)
                    reason = (
                        f"Matched criteria keywords {matched} in agent response."
                        if passed_crit
                        else f"Missing criteria keywords {keywords} in agent response."
                    )
                else:
                    passed_crit = bool(assistant_text.strip())
                    reason = "Agent responded to caller." if passed_crit else "No agent response."

            criteria_verdicts.append(
                {
                    "criterion": crit,
                    "passed": passed_crit,
                    "score": 100.0 if passed_crit else 0.0,
                    "rationale": reason,
                }
            )

        passed_count = sum(1 for v in criteria_verdicts if v["passed"])
        overall_score = round((passed_count / len(criteria_verdicts)) * 100.0, 2)
        all_passed = passed_count == len(criteria_verdicts)
        rationale = "; ".join(v["rationale"] for v in criteria_verdicts)

        return {
            "passed": all_passed,
            "verdict": "PASSED" if all_passed else "FAILED",
            "overall_score": overall_score,
            "rationale": rationale,
            "criteria_verdicts": criteria_verdicts,
            "judge_provider": "openai" if _is_live_llm_configured() else "deterministic_mock",
            "judge_model": "gpt-4o-mini" if _is_live_llm_configured() else "criteria-judge-v1",
            "is_mock_provider": not _is_live_llm_configured(),
        }
