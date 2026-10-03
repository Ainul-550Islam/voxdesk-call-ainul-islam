"""Escalation facade.

``transfer`` wraps the authoritative telephony transfer service so callers have
one entry point for "hand this conversation to a human". The facade never
reports a handoff as complete on its own: it requires the persisted call, its
tenant, and a transaction/session, and it delegates the actual transfer to
``app.telephony``.
"""

__all__ = ["transfer"]
