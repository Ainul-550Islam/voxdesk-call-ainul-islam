"""Durable tenant-scoped audit capabilities.

The canonical table is the existing ``audit_logs`` table. This package enriches
and hardens that store rather than creating a parallel event ledger. Exports
are loaded lazily to keep logging/redaction imports free of database side effects.
"""
from __future__ import annotations

__all__ = [
    "AuditEventType",
    "AuditScopeError",
    "REQUIRED_ENTERPRISE_EVENT_TYPES",
    "record_event",
]


def __getattr__(name: str):
    if name in {"AuditEventType", "REQUIRED_ENTERPRISE_EVENT_TYPES"}:
        from app.audit import events
        return getattr(events, name)
    if name in {"AuditScopeError", "record_event"}:
        from app.audit import service
        return getattr(service, name)
    raise AttributeError(name)
