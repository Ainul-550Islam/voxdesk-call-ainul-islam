"""Compatibility access to the canonical workflow condition evaluator.

The durable workflow domain owns the operator vocabulary and implementation.
This module keeps the historical orchestration imports working, but delegates
``evaluate`` and ``CONDITION_OPERATORS`` instead of maintaining a second
condition implementation. ``compare`` remains as a small compatibility helper
because older callers import it directly.
"""

from __future__ import annotations

from typing import Any

from app.domain.workflow_models import (
    CONDITION_OPERATORS as _CONDITION_OPERATORS,
    evaluate_condition,
)

# Historical names retained for ``app.orchestration.workflow`` and callers.
CONDITION_OPERATORS = _CONDITION_OPERATORS
evaluate = evaluate_condition


def compare(a: Any, b: Any) -> int:
    """Total, type-aware comparison used by the ordering operators.

    Numerics compare numerically, strings lexicographically; anything else
    raises ``ValueError`` because an ordering on mixed types would be a guess.
    """
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return (a > b) - (a < b)
    if isinstance(a, str) and isinstance(b, str):
        return (a > b) - (a < b)
    raise ValueError(f"cannot order-compare {type(a).__name__} with {type(b).__name__}")
