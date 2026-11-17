"""Convert a Retell export into a `voxdesk.agent/1` bundle (PART 18, P18-04).

    # what an export usually looks like: one JSON object per agent
    python3 scripts/import_retell_agent.py retell-agent.json -o agent.bundle.json

    # a directory of exports, converted in place
    python3 scripts/import_retell_agent.py exports/ --out-dir bundles/

    # read the report and nothing else
    python3 scripts/import_retell_agent.py retell-agent.json --report-only

Exit codes
----------
0  converted, no unsupported fields
1  converted, but something was **not** imported — the report says what
2  could not convert at all (not a Retell payload, unreadable file)

Exit code 1 exists on purpose: a migration script that returns success while
having quietly dropped half an agent is how a customer ends up with an agent
that answers wrong and nobody knows why.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.services.retell_import_service import (  # noqa: E402
    convert_retell_payload,
    redact_for_report,
)


def _convert_file(path: Path) -> tuple[int, dict, dict]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"✗ {path.name}: {exc}", file=sys.stderr)
        return 2, {}, {}

    if not isinstance(payload, dict):
        print(f"✗ {path.name}: expected a JSON object at the top level", file=sys.stderr)
        return 2, {}, {}

    try:
        bundle, report = convert_retell_payload(payload)
    except ValueError as exc:
        print(f"✗ {path.name}: {exc}", file=sys.stderr)
        return 2, {}, {}

    return (0 if report.clean else 1), bundle, report.as_dict()


def _print_report(name: str, report: dict, verbose: bool) -> None:
    counts = report["counts"]
    print(
        f"{name}: converted {counts['converted']}, "
        f"unsupported {counts['unsupported']}, lossy {counts['lossy']}"
    )
    if verbose:
        for entry in report["converted"]:
            print(f"  ✓ {entry['source_path']} → {entry['target_path']}  ({entry['rule']})")
    for entry in report["lossy"]:
        print(f"  ~ {entry['source_path']}: {entry['reason']}")
        if entry.get("suggestion"):
            print(f"      → {entry['suggestion']}")
    for entry in report["unsupported"]:
        print(f"  ✗ {entry['source_path']} ({entry['value_summary']}): {entry['reason']}")
        if entry.get("suggestion"):
            print(f"      → {entry['suggestion']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Convert Retell agent / conversation-flow JSON to voxdesk.agent/1."
    )
    parser.add_argument("source", type=Path, help="a .json file or a directory of them")
    parser.add_argument("-o", "--output", type=Path, help="output file (single input only)")
    parser.add_argument("--out-dir", type=Path, help="output directory (batch)")
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="print what would be converted and write nothing",
    )
    parser.add_argument(
        "--report-json", type=Path, help="write the machine-readable report here"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="list every converted field")
    args = parser.parse_args(argv)

    inputs = (
        sorted(args.source.glob("*.json")) if args.source.is_dir() else [args.source]
    )
    if not inputs:
        print(f"✗ no .json files in {args.source}", file=sys.stderr)
        return 2

    worst = 0
    reports: dict[str, dict] = {}
    for path in inputs:
        code, bundle, report = _convert_file(path)
        worst = max(worst, code)
        if code == 2:
            continue
        _print_report(path.name, report, args.verbose)
        reports[path.name] = report

        if args.report_only:
            continue
        if args.output is not None and len(inputs) == 1:
            target = args.output
        elif args.out_dir is not None:
            args.out_dir.mkdir(parents=True, exist_ok=True)
            target = args.out_dir / f"{path.stem}.bundle.json"
        else:
            target = path.with_suffix(".bundle.json")

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(redact_for_report(bundle), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"  → wrote {target}")

    if args.report_json:
        args.report_json.parent.mkdir(parents=True, exist_ok=True)
        args.report_json.write_text(json.dumps(reports, indent=2) + "\n", encoding="utf-8")
        print(f"  → wrote report {args.report_json}")

    if worst == 1:
        print(
            "\nSome fields were not imported. Read the ✗ lines above: each one is a "
            "decision you have to make, not a warning to ignore.",
            file=sys.stderr,
        )
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
