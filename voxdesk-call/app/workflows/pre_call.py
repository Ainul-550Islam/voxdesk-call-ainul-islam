"""Pre-call context fetch with a hard time budget (PART 14, P14-04).

The call is ringing. Everything fetched here is on the critical path between
"the phone is answered" and "the agent says hello", so the budget is not a
performance nicety -- it is the difference between a caller hearing a greeting
and a caller hearing silence.

The contract:

* each fetcher gets whatever is left of the budget, never a fresh timeout;
* a fetcher that times out or raises is recorded and its fallback value is used;
* the whole function returns, always, in <= budget + the time to record it;
* nothing here raises. A CRM outage degrades the greeting; it must not drop the
  call.

Results become dynamic variables, so one fetcher failing leaves the others'
values intact and the agent prompt sees `{{contact_name}}` missing rather than
the whole context block missing.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping

#: Total budget. 1.5 s is the most a caller will tolerate before the greeting.
DEFAULT_BUDGET_MS = 1500
#: A single fetcher may never consume the whole budget, or one slow CRM would
#: starve memory and calendar even when they were fast.
MAX_STEP_SHARE = 0.6


@dataclass(frozen=True)
class StepOutcome:
    name: str
    status: str  # ok | timeout | error | skipped
    elapsed_ms: float
    variables: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "variables": list(self.variables),
            "error": self.error,
        }


@dataclass(frozen=True)
class PreCallResult:
    variables: dict[str, Any]
    steps: tuple[StepOutcome, ...]
    timed_out: bool
    fallback_used: bool
    elapsed_ms: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "variables": dict(self.variables),
            "steps": [step.as_dict() for step in self.steps],
            "timed_out": self.timed_out,
            "fallback_used": self.fallback_used,
            "elapsed_ms": round(self.elapsed_ms, 1),
        }


Fetcher = Callable[[Mapping[str, Any]], Awaitable[Mapping[str, Any]]]


async def run_pre_call(
    context: Mapping[str, Any],
    fetchers: Mapping[str, Fetcher],
    *,
    budget_ms: int = DEFAULT_BUDGET_MS,
    fallbacks: Mapping[str, Mapping[str, Any]] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> PreCallResult:
    """Run every fetcher inside one shared budget."""
    if budget_ms <= 0:
        raise ValueError("budget_ms must be positive")
    started = clock()
    deadline = started + budget_ms / 1000.0
    variables: dict[str, Any] = {}
    steps: list[StepOutcome] = []
    timed_out = False
    fallback_used = False
    fallback_map = dict(fallbacks or {})

    for name, fetcher in fetchers.items():
        remaining = deadline - clock()
        if remaining <= 0:
            timed_out = True
            steps.append(StepOutcome(name, "skipped", 0.0, error="budget exhausted"))
            if name in fallback_map:
                variables.update(fallback_map[name])
                fallback_used = True
            continue
        step_budget = min(remaining, (budget_ms / 1000.0) * MAX_STEP_SHARE)
        step_start = clock()
        try:
            result = await asyncio.wait_for(fetcher(context), timeout=step_budget)
            elapsed = (clock() - step_start) * 1000.0
            payload = dict(result or {})
            variables.update(payload)
            steps.append(StepOutcome(name, "ok", elapsed, payload))
        except asyncio.TimeoutError:
            elapsed = (clock() - step_start) * 1000.0
            timed_out = True
            steps.append(StepOutcome(name, "timeout", elapsed, error="step budget exceeded"))
            if name in fallback_map:
                variables.update(fallback_map[name])
                fallback_used = True
        except Exception as exc:  # a CRM outage must not drop the call
            elapsed = (clock() - step_start) * 1000.0
            steps.append(StepOutcome(name, "error", elapsed, error=str(exc)[:200]))
            if name in fallback_map:
                variables.update(fallback_map[name])
                fallback_used = True

    return PreCallResult(
        variables=variables,
        steps=tuple(steps),
        timed_out=timed_out,
        fallback_used=fallback_used,
        elapsed_ms=(clock() - started) * 1000.0,
    )
