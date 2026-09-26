"""QA and conversation intelligence.

Exports are lazy so importing the package does not pull the AI gateway or a
database session while ``app.db.models`` is still loading.
"""

from __future__ import annotations

__all__ = [
    "QaError",
    "calculate_review",
    "create_review",
    "request_auto_review",
]


def __getattr__(name: str):
    if name == "QaError":
        from app.qa.exceptions import QaError

        return QaError
    if name in {"calculate_review", "create_review", "request_auto_review"}:
        from app.qa import service

        return getattr(service, name)
    raise AttributeError(name)
