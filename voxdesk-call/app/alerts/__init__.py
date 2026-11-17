# File: app/alerts/__init__.py — user-defined metric alerts
"""Alert rules, their evaluation and their delivery (PART 15, P15-04).

Three pieces, deliberately separate:

``models``     the two tables, re-exported from ``app.db.monitoring_models`` so
               there is exactly one definition of each
``evaluator``  metric sampling, comparison, cooldown and the exactly-once key
``notifier``   turning a fired event into deliveries per channel

The evaluator owns the decision; the notifier owns the side effect. A failed
Slack webhook must not make the evaluation look un-run, and a suppressed alert
must not look like a quiet metric.
"""

from app.alerts.evaluator import (
    COMPARATORS,
    METRICS,
    AlertRuleView,
    Evaluation,
    breaches,
    cooldown_active,
    evaluate_rule,
    evaluate_tenant,
    sample_metric,
    window_bucket,
)
from app.alerts.models import AlertEvent, AlertRule
from app.alerts.notifier import build_body, deliver

__all__ = [
    "AlertEvent",
    "AlertRule",
    "AlertRuleView",
    "COMPARATORS",
    "Evaluation",
    "METRICS",
    "breaches",
    "build_body",
    "cooldown_active",
    "deliver",
    "evaluate_rule",
    "evaluate_tenant",
    "sample_metric",
    "window_bucket",
]
