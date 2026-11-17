# File: app/alerts/models.py — the alert tables, re-exported for the spec path
"""Alert tables.

The tables are declared once, in :mod:`app.db.monitoring_models`, next to the
live-scoring and batch-test tables they are created with. This module exists
because the spec names it: a second ``class AlertRule(Base)`` here would be a
second mapping for one table and would fail at import time with
``Table 'alert_rules' is already defined``.
"""

from app.db.monitoring_models import AlertEvent, AlertRule

__all__ = ["AlertEvent", "AlertRule"]
