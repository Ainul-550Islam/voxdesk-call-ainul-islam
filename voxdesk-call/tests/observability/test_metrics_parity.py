"""Prometheus alert/recording rule & Grafana dashboard metric parity test (Step 5 R4)."""
from __future__ import annotations

from scripts.verify_observability import (
    collect_defined_metrics,
    verify_static_observability,
)


def test_every_alert_and_grafana_voxdesk_metric_is_emitted_or_recorded() -> None:
    defined = collect_defined_metrics()
    assert len(defined) >= 15

    report = verify_static_observability()
    assert report["status"] == "PASS", report
    checks = report["checks"]
    assert checks["rule_count"] > 0
    assert checks["grafana_panel_expressions"] > 0
    assert checks["undefined_metrics"] == [], f"Undefined metrics: {checks['undefined_metrics']}"
