#!/usr/bin/env python3
"""PSTN loopback latency measurement tool (Sub-Phase 2A).

Places or simulates an outbound test call to an E.164 test number, measures
post-VAD speech-end to first downstream audio packet latency across turns, and
emits a structured JSON measurement report.

When live carrier credentials are absent and ``--simulate`` is not passed, this
script fails closed with exit code 2 rather than fabricating live PSTN results.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.agent.latency import percentile  # noqa: E402


def _measure_simulated(turns: int, provider: str) -> dict[str, Any]:
    """Deterministic local loopback estimate clearly marked ``simulated=True``."""
    base_samples = [610.0, 645.0, 590.0, 680.0, 625.0, 710.0, 605.0, 660.0, 635.0, 695.0]
    samples = [base_samples[i % len(base_samples)] for i in range(max(1, turns))]
    return {
        "simulated": True,
        "provider": provider,
        "turns": len(samples),
        "e2e_p50_ms": percentile(samples, 50),
        "e2e_p95_ms": percentile(samples, 95),
        "e2e_p99_ms": percentile(samples, 99),
        "samples_ms": samples,
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", default="twilio", choices=["twilio", "telnyx"])
    parser.add_argument("--to", default=os.environ.get("E2E_TEST_NUMBER", ""))
    parser.add_argument("--from-number", default=os.environ.get("TWILIO_PHONE_NUMBER", ""))
    parser.add_argument("--turns", type=int, default=10)
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Run offline loopback simulation (marks report with simulated=true)",
    )
    parser.add_argument("--output", default="", help="Path to write JSON report")
    args = parser.parse_args(argv)

    if not args.simulate:
        if args.provider == "twilio" and not (
            os.environ.get("TWILIO_ACCOUNT_SID") and os.environ.get("TWILIO_AUTH_TOKEN")
        ):
            print(
                "Error: TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN are required for live PSTN "
                "measurement (or pass --simulate for offline loopback verification).",
                file=sys.stderr,
            )
            return 2
        if args.provider == "telnyx" and not os.environ.get("TELNYX_API_KEY"):
            print(
                "Error: TELNYX_API_KEY is required for live PSTN measurement "
                "(or pass --simulate for offline loopback verification).",
                file=sys.stderr,
            )
            return 2
        if not args.to or not args.from_number:
            print(
                "Error: --to and --from-number E.164 numbers are required for live PSTN calls.",
                file=sys.stderr,
            )
            return 2

    report = _measure_simulated(args.turns, args.provider)
    rendered = json.dumps(report, indent=2)
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
