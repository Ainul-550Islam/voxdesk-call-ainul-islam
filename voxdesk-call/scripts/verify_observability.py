#!/usr/bin/env python3
"""VoxDesk observability certification (Step 13 section H + static metric parity).

When ``--prometheus-url`` is provided, performs read-only live validation of the
Prometheus/Grafana stack (health, scrape targets, rule files, alert rules, SLO
rules, API metrics, scheduler metrics and Grafana health).

When ``--prometheus-url`` is omitted, performs deterministic offline static
verification of ``observability/prometheus.yml``, ``observability/alerts.yml``,
``observability/slos.yml`` and ``observability/grafana/**/*.json`` against the
metric definitions in ``app/`` and ``services/``.

Exit codes: 0 = PASS, 1 = FAIL, 2 = BLOCKED, 3 = invalid configuration.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.release import ops  # noqa: E402

_VOXDESK_METRIC_RE = re.compile(r"\bvoxdesk_[a-zA-Z0-9_:]+\b")


def collect_defined_metrics(repo_root: Path = REPO_ROOT) -> set[str]:
    """Collect every ``voxdesk_*`` metric defined across Python, Go, Rust and C++."""
    raw_defined: set[str] = set()
    for sub in ("app", "services", "scripts"):
        base_dir = repo_root / sub
        if not base_dir.exists():
            continue
        for path in base_dir.rglob("*"):
            if (
                not path.is_file()
                or "target" in path.parts
                or "node_modules" in path.parts
                or "__pycache__" in path.parts
                or path.suffix not in {".py", ".go", ".rs", ".cc", ".cpp", ".h", ".hpp"}
            ):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            raw_defined.update(_VOXDESK_METRIC_RE.findall(text))

    expanded = set(raw_defined)
    for name in raw_defined:
        for suffix in ("_total", "_bucket", "_count", "_sum", "_created"):
            expanded.add(f"{name}{suffix}")
    return expanded


def verify_static_observability(repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    """Verify Prometheus configs/rules and Grafana dashboards match code metrics."""
    obs_dir = repo_root / "observability"
    checks: dict[str, Any] = {"mode": "static_config_and_metric_parity"}
    missing_files: list[str] = []

    prom_cfg_path = obs_dir / "prometheus.yml"
    alerts_path = obs_dir / "alerts.yml"
    slos_path = obs_dir / "slos.yml"
    for required_file in (prom_cfg_path, alerts_path, slos_path):
        if not required_file.exists():
            missing_files.append(str(required_file.relative_to(repo_root)))

    if missing_files:
        checks["missing_files"] = missing_files
        return {
            "status": ops.STATUS_FAIL,
            "checks": checks,
            "evidence": f"missing observability files: {missing_files}",
        }

    prom_cfg = yaml.safe_load(prom_cfg_path.read_text(encoding="utf-8")) or {}
    scrape_jobs = [c.get("job_name") for c in prom_cfg.get("scrape_configs", []) if isinstance(c, dict)]
    checks["prometheus_scrape_jobs"] = scrape_jobs
    checks["prometheus_config_valid"] = bool(scrape_jobs)

    recorded_rules: set[str] = set()
    referenced_metrics: dict[str, list[str]] = {}
    rule_count = 0

    for rule_path in (alerts_path, slos_path):
        doc = yaml.safe_load(rule_path.read_text(encoding="utf-8")) or {}
        rel = str(rule_path.relative_to(repo_root))
        for group in doc.get("groups", []):
            for rule in group.get("rules", []):
                rule_count += 1
                if "record" in rule:
                    recorded_rules.add(str(rule["record"]))
                expr = str(rule.get("expr", ""))
                for metric in _VOXDESK_METRIC_RE.findall(expr):
                    referenced_metrics.setdefault(metric, []).append(rel)

    dashboard_files = sorted((obs_dir / "grafana").rglob("*.json"))
    panel_expr_count = 0
    for dash_path in dashboard_files:
        doc = json.loads(dash_path.read_text(encoding="utf-8"))
        rel = str(dash_path.relative_to(repo_root))

        def _walk(node: Any, rel_path: str = rel) -> None:
            nonlocal panel_expr_count
            if isinstance(node, dict):
                expr_val = node.get("expr")
                if isinstance(expr_val, str):
                    panel_expr_count += 1
                    for metric in _VOXDESK_METRIC_RE.findall(expr_val):
                        referenced_metrics.setdefault(metric, []).append(rel_path)
                for val in node.values():
                    _walk(val, rel_path)
            elif isinstance(node, list):
                for item in node:
                    _walk(item, rel_path)

        _walk(doc)

    defined = collect_defined_metrics(repo_root)
    undefined = sorted(set(referenced_metrics) - defined - recorded_rules)

    checks["rule_count"] = rule_count
    checks["recording_rules"] = len(recorded_rules)
    checks["grafana_json_files"] = len(dashboard_files)
    checks["grafana_panel_expressions"] = panel_expr_count
    checks["referenced_voxdesk_metrics"] = len(referenced_metrics)
    checks["undefined_metrics"] = undefined

    ok = bool(scrape_jobs) and rule_count > 0 and panel_expr_count > 0 and not undefined
    status = ops.STATUS_PASS if ok else ops.STATUS_FAIL
    return {
        "status": status,
        "checks": checks,
        "evidence": (
            f"static observability {status}: rules={rule_count}, "
            f"panel_exprs={panel_expr_count}, referenced_metrics={len(referenced_metrics)}, "
            f"undefined={undefined or 'none'}"
        ),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="verify_observability.py", description="VoxDesk observability certification"
    )
    parser.add_argument("--prometheus-url", default="", help="Prometheus base URL")
    parser.add_argument("--grafana-url", default="", help="Grafana base URL (optional)")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.prometheus_url:
        result = ops.verify_observability(args.prometheus_url, args.grafana_url)
    else:
        result = verify_static_observability(REPO_ROOT)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Observability verification: {result['status']}")
        print(f"  {result['evidence']}")
        for key, value in sorted(result.get("checks", {}).items()):
            print(f"  {key}: {value}")
    return ops.exit_code_for_status(result["status"])


if __name__ == "__main__":
    sys.exit(main())
