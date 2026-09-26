"""AI control plane.

``app.agent`` still executes voice and text. This package checks policy,
budget, prompts and guardrails around that execution. It does not replace
the provider clients or the billing meter.

Exports are lazy. ``app.db.models`` imports ``app.ai.models`` while the auth
package is still loading, so this module must not import FastAPI at import time.
"""

from __future__ import annotations

__all__ = [
    "GovernanceError",
    "PolicyDenied",
    "govern",
    "health_view",
    "load_policy",
    "providers_view",
]


def __getattr__(name: str):
    if name in {"govern", "health_view", "load_policy", "providers_view"}:
        from app.ai import gateway

        return getattr(gateway, name)
    if name in {"GovernanceError", "PolicyDenied"}:
        from app.ai import models

        return getattr(models, name)
    raise AttributeError(name)
