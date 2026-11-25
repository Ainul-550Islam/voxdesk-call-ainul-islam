#!/usr/bin/env python3
"""Measure the per-call CPU cost of the voice pipeline preamble.

PART 17B (P17B-06). Everything here runs on the call path *before* a single byte
of media moves: the tool filter, the language runtime, the STT decision and the
dynamic-variable block. It is small, it is pure Python, it runs once per call, and
until this script existed nobody had measured it — so "optimise the pipeline" was
folklore.

    python3 scripts/bench_pipeline_preamble.py            # default 2000 iterations
    python3 scripts/bench_pipeline_preamble.py --json     # machine-readable

Numbers are µs/call on the machine that runs it. The absolute value is not a
target; the *ratio* against the recorded baseline in docs/PERF_TUNING.md §6 is.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agent.functions import TOOL_SCHEMAS  # noqa: E402
from app.agent.language import build_language_policy, resolve_language_config  # noqa: E402
from app.agent.pipeline import (  # noqa: E402
    _dynamic_variables_instruction,
    build_language_runtime,
    filter_enabled_tools,
    requires_runtime_stt,
)

# A bilingual agent with an explicit allowlist: the common production shape, and
# the slowest one, because every branch of the language runtime is taken.
RUNTIME_CFG = SimpleNamespace(
    language="en-US",
    allowed_languages=["en-US", "es-ES"],
    language_reply_policy="follow_caller",
    language_fallback_phrase="",
    call_tooling={"ivr_navigation_enabled": True},
    expressive=True,
    expressive_emotions=("neutral", "warm", "calm"),
    structured_output_schema=None,
    stt_provider="deepgram",
    stt_fallback_providers=None,
    boosted_keywords=None,
    stt_language="en-US",
    stt_endpointing_mode="default",
    stt_medical_vocabulary=False,
    stt_medical_terms=None,
    stt_model="",
    custom_tools=[],
    mcp_tools=[],
    enabled_tools=["check_availability", "book_appointment", "escalate_to_human", "end_call"],
    ivr_navigation_enabled=True,
)
TENANT = SimpleNamespace(language="en-US")
VARIABLES = {"tenant": "acme", "caller": "+14155550100", "notes": "n" * 200}


def one_call() -> None:
    """Exactly the work `_run_cascaded_agent` does before the media path starts."""
    build_language_runtime(RUNTIME_CFG, fallback_language="en-US")
    filter_enabled_tools(
        list(TOOL_SCHEMAS),
        RUNTIME_CFG.enabled_tools,
        custom_names=set(),
        ivr_navigation_enabled=True,
    )
    _dynamic_variables_instruction(VARIABLES)
    requires_runtime_stt(RUNTIME_CFG, TENANT)


def measure(iterations: int, rounds: int) -> dict:
    samples: list[float] = []
    for _ in range(rounds):
        start = time.perf_counter()
        for _ in range(iterations):
            one_call()
        samples.append((time.perf_counter() - start) / iterations * 1e6)
    return {
        "iterations": iterations,
        "rounds": rounds,
        "us_per_call_median": round(statistics.median(samples), 2),
        "us_per_call_min": round(min(samples), 2),
        "us_per_call_max": round(max(samples), 2),
        "samples": [round(sample, 2) for sample in samples],
        "tool_schemas": len(TOOL_SCHEMAS),
        "python": sys.version.split()[0],
    }


def cache_report() -> dict:
    build_language_policy(languages=["en-US", "es-ES"], language="en-US")
    resolve_language_config("en-US")
    return {
        "resolve_language_config": dict(resolve_language_config.cache_info()._asdict()),
        "language_policy": dict(build_language_policy.__wrapped__.__globals__["_resolved_policy_fields"].cache_info()._asdict())
        if hasattr(build_language_policy, "__wrapped__")
        else {},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=2000)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--json", action="store_true", help="print JSON instead of a table")
    args = parser.parse_args(argv)

    result = measure(args.iterations, args.rounds)
    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    print(f"per-call preamble: {result['us_per_call_median']} µs/call "
          f"(median of {args.rounds} rounds × {args.iterations} calls, "
          f"min {result['us_per_call_min']}, max {result['us_per_call_max']})")
    print(f"tool schemas filtered per call: {result['tool_schemas']}, python {result['python']}")
    print("baseline recorded in docs/PERF_TUNING.md §6: 39.19 µs/call median "
          "(unmodified PART 18 tree, same harness)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
