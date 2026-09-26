"""Contact-center ACD.

This package decides which queue and which agent should receive work. It does
not replace ``app.inbox`` assignment, ``app.telephony`` call state, or the
realtime transport. Exports are lazy so importing the package does not pull
FastAPI while ``app.db.models`` is still loading.
"""

from __future__ import annotations

__all__ = [
    "AcdError",
    "assign_next",
    "enqueue",
]


def __getattr__(name: str):
    if name == "AcdError":
        from app.contact_center.exceptions import AcdError

        return AcdError
    if name in {"assign_next", "enqueue"}:
        from app.contact_center import service

        return getattr(service, name)
    raise AttributeError(name)
