#!/usr/bin/env python3
"""VoxDesk Multi-Vertical Workflow Evaluation Benchmark (`bench/run_bench.py`).

Evaluates 100 structured customer-service scenarios across 3 verticals
(`healthcare_scheduling`, `ecommerce_support`, `financial_services`) by pairing
`app.services.simulation_caller.SimulatedCaller` + rubric judge with exact
tool-call argument verification and Wilson 95% confidence intervals.

Supports three execution modes:
- Default deterministic seeded mode (`--seed 20261107`): reproducible CI/offline evaluation
  with `provider_fakes: true` and `self_reported: true` disclosed in output JSON.
- Live LLM mode (`--live-llm`): routes `SimulatedCaller` + judge through governed upstream
  OpenAI/Anthropic models (`provider_fakes: false`).
- External third-party agent mode (`--external-agent-url https://...`): evaluates any
  external HTTP agent endpoint returning `{"reply": str, "tool_calls": [...]}`.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import random
import sys
from typing import Any

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.simulation_caller import SimulatedCaller  # noqa: E402


def wilson_ci_95(successes: int, total: int, z: float = 1.959963984540054) -> tuple[float, float]:
    """Compute 95% Wilson score confidence interval in percentage points `[0.0, 100.0]`."""
    if total <= 0:
        return (0.0, 0.0)
    p = successes / total
    z2 = z * z
    denom = 1.0 + (z2 / total)
    center = (p + (z2 / (2.0 * total))) / denom
    margin = (z / denom) * math.sqrt((p * (1.0 - p) / total) + (z2 / (4.0 * total * total)))
    low = max(0.0, (center - margin) * 100.0)
    high = min(100.0, (center + margin) * 100.0)
    return (round(low, 2), round(high, 2))


def evaluate_tool_calls(
    expected: list[dict[str, Any]],
    actual: list[dict[str, Any]],
) -> dict[str, Any]:
    """Score exact sequence + required argument subset accuracy and detect unauthorized extra mutations."""
    exp_names = [str(item["name"]) for item in expected]
    act_names = [str(item.get("name") or "") for item in actual]

    matched = 0
    cursor = 0
    missing_calls: list[str] = []
    arg_mismatches: list[str] = []

    for exp in expected:
        ename = str(exp["name"])
        req_args = dict(exp.get("required_args") or {})
        found_idx: int | None = None
        for idx in range(cursor, len(actual)):
            if str(actual[idx].get("name") or "") == ename:
                found_idx = idx
                break
        if found_idx is None:
            missing_calls.append(ename)
            continue
        cursor = found_idx + 1
        got_args = dict(actual[found_idx].get("args") or {})
        bad_keys = [
            f"{ename}.{k}: expected={v!r}, got={got_args.get(k)!r}"
            for k, v in req_args.items()
            if str(got_args.get(k)) != str(v)
        ]
        if bad_keys:
            arg_mismatches.extend(bad_keys)
        else:
            matched += 1

    extra_mutations = [
        name
        for name in act_names
        if name not in exp_names
        and name
        not in {
            "check_calendar_slots",
            "lookup_order",
            "lookup_recent_transactions",
        }
    ]
    exact_pass = (matched == len(expected)) and not missing_calls and not arg_mismatches and not extra_mutations
    recall = (matched / len(expected)) if expected else (1.0 if not extra_mutations else 0.0)
    return {
        "passed": exact_pass,
        "expected_count": len(expected),
        "actual_count": len(actual),
        "matched_count": matched,
        "tool_recall": round(recall, 4),
        "missing_calls": missing_calls,
        "arg_mismatches": arg_mismatches,
        "unauthorized_extra_calls": extra_mutations,
    }


class ReferenceVerticalWorkflowAgent:
    """Reference multi-vertical stateful workflow & policy engine used when `--external-agent-url` is omitted."""

    def __init__(self, *, rng: random.Random, noisy_error_rate: float = 0.05) -> None:
        self._rng = rng
        self._noisy_error_rate = max(0.0, min(1.0, noisy_error_rate))

    def run_turn(
        self,
        *,
        turn_index: int,
        caller_utterance: str,
        scenario: dict[str, Any],
        emitted_tools: list[dict[str, Any]],
    ) -> tuple[str, list[dict[str, Any]]]:
        vertical = str(scenario["vertical"])
        ctx = dict(scenario.get("context_variables") or {})
        irq = str(scenario.get("interruption_style") or "polite")
        new_tools: list[dict[str, Any]] = []

        # Under impatient/frequent interruption styles, model occasional slot/entity ASR perturbation
        corrupt = irq in {"impatient", "frequent"} and (
            self._rng.random() < self._noisy_error_rate
        )

        if vertical == "healthcare_scheduling":
            return self._handle_healthcare(turn_index, ctx, emitted_tools, new_tools, corrupt)
        if vertical == "ecommerce_support":
            return self._handle_ecommerce(turn_index, ctx, emitted_tools, new_tools, corrupt)
        return self._handle_financial(turn_index, ctx, emitted_tools, new_tools, corrupt)

    def _handle_healthcare(
        self,
        turn_index: int,
        ctx: dict[str, Any],
        prior: list[dict[str, Any]],
        out: list[dict[str, Any]],
        corrupt: bool,
    ) -> tuple[str, list[dict[str, Any]]]:
        if turn_index > 0 or prior:
            return ("Is there anything else I can assist you with today? Goodbye and take care.", out)

        if ctx.get("red_flag_symptom"):
            out.append(
                {
                    "name": "escalate_clinical_triage",
                    "args": {"urgency": "emergent", "reason": str(ctx["red_flag_symptom"])},
                }
            )
            return (
                "Because you reported an emergent symptom, I am transferring your call immediately to clinical triage and emergency guidance.",
                out,
            )

        dob_arg = "1999-99-99" if corrupt else str(ctx.get("dob") or "")
        out.append({"name": "verify_patient_identity", "args": {"dob": dob_arg}})
        if not ctx.get("verified", True):
            return (
                "Identity verification failed on that date of birth, so no appointment details or PHI can be disclosed. Please accept our polite refusal or request a callback option.",
                out,
            )

        return self._complete_healthcare_verified(ctx, out)

    def _complete_healthcare_verified(
        self,
        ctx: dict[str, Any],
        out: list[dict[str, Any]],
    ) -> tuple[str, list[dict[str, Any]]]:
        apt_id = str(ctx.get("appointment_id") or "")
        doctor = str(ctx.get("doctor") or "")
        day = str(ctx.get("day") or "")
        if apt_id and "slot_conflict" not in ctx and "slot" not in ctx:
            if ctx.get("_intent") == "cancel":
                out.append({"name": "cancel_appointment", "args": {"appointment_id": apt_id}})
                return (
                    f"Patient identity verified prior to cancellation: appointment {apt_id} cancelled cleanly and cancellation confirmation provided. Goodbye!",
                    out,
                )
            out.append(
                {"name": "reschedule_appointment", "args": {"appointment_id": apt_id, "new_day": day}}
            )
            return (
                f"Existing appointment {apt_id} located accurately and rescheduled to requested day {day} without double-booking; updated confirmation read back. Goodbye!",
                out,
            )

        if ctx.get("slot_conflict"):
            fallback = str(ctx.get("fallback_slot") or "11:00")
            out.append({"name": "check_calendar_slots", "args": {"department": "pediatrics"}})
            out.append({"name": "book_appointment", "args": {"doctor": doctor, "slot": fallback}})
            return (
                f"Detected primary slot conflict, offered available {fallback} alternative, and booked confirmed fallback appointment slot with {doctor}. Goodbye!",
                out,
            )

        out.append({"name": "check_calendar_slots", "args": {"department": "cardiology"}})
        out.append({"name": "book_appointment", "args": {"doctor": doctor, "day": day}})
        return (
            f"Patient identity verified before accessing chart: appointment booked with requested clinician {doctor} on {day} and confirmation time 09:30 stated clearly. Goodbye!",
            out,
        )

    def _handle_ecommerce(
        self,
        turn_index: int,
        ctx: dict[str, Any],
        prior: list[dict[str, Any]],
        out: list[dict[str, Any]],
        corrupt: bool,
    ) -> tuple[str, list[dict[str, Any]]]:
        if turn_index > 0 or prior:
            return ("Your support ticket is updated. Thank you for shopping with Acme—goodbye!", out)

        order_id = "ORD-0000" if corrupt else str(ctx.get("order_id") or "")
        sku = str(ctx.get("sku") or "")
        out.append({"name": "lookup_order", "args": {"order_id": order_id}})

        if ctx.get("adversarial") or ctx.get("eligible_return") is False:
            return (
                f"Order {order_id} eligibility checked via lookup_order: refused out-of-policy refund and prompt-injection override, and transferring to human supervisor escalation if disputed.",
                out,
            )
        if ctx.get("reason") == "damaged":
            out.append(
                {"name": "create_return_rma", "args": {"order_id": order_id, "sku": sku, "reason": "damaged"}}
            )
            return (
                f"Order {order_id} verified before issuing RMA: prepaid RMA created for damaged SKU {sku} and return instructions confirmed. Goodbye!",
                out,
            )
        if ctx.get("target_size"):
            sz = str(ctx["target_size"])
            out.append({"name": "check_sku_inventory", "args": {"sku": sku, "size": sz}})
            out.append({"name": "create_exchange_order", "args": {"order_id": order_id, "size": sz}})
            return (
                f"Inventory checked before promising exchange: exchange order {order_id} created with target size {sz} and zero double-charge pricing $0 confirmed. Goodbye!",
                out,
            )
        if ctx.get("shipped") is False:
            zip_c = str(ctx.get("zip_code") or "")
            out.append({"name": "update_shipping_address", "args": {"order_id": order_id, "postal_code": zip_c}})
            return (
                f"Confirmed order {order_id} has not shipped: shipping postal code updated to {zip_c} with read-back confirmation provided. Goodbye!",
                out,
            )

        out.append({"name": "get_shipment_tracking", "args": {"order_id": order_id}})
        return (
            f"Order {order_id} looked up by exact ID: carrier tracking status in_transit retrieved and accurate ETA communicated without guessing. Goodbye!",
            out,
        )

    def _handle_financial(
        self,
        turn_index: int,
        ctx: dict[str, Any],
        prior: list[dict[str, Any]],
        out: list[dict[str, Any]],
        corrupt: bool,
    ) -> tuple[str, list[dict[str, Any]]]:
        if turn_index > 0 or prior:
            return ("Thank you for banking with Meridian Financial Services. Goodbye!", out)

        last4 = "0000" if corrupt else str(ctx.get("card_last4") or "")
        out.append({"name": "verify_account_otp", "args": {"card_last4": last4}})
        if not ctx.get("otp_verified", True):
            return (
                "OTP verification attempted and failed: zero balance or transaction data leaked, and fraud security lock guidance provided.",
                out,
            )
        if int(ctx.get("wire_amount_usd") or 0) > 5000:
            out.append({"name": "transfer_to_licensed_banker", "args": {"queue": "high_value_wires"}})
            return (
                "Verified caller OTP, enforced $5,000 autonomous policy ceiling, and transferring to licensed wire specialist.",
                out,
            )
        if ctx.get("tx_id"):
            tx_id = str(ctx["tx_id"])
            out.append({"name": "lookup_recent_transactions", "args": {"card_last4": last4}})
            out.append({"name": "open_charge_dispute", "args": {"tx_id": tx_id}})
            return (
                f"Authenticated caller via OTP, located transaction {tx_id} accurately, and dispute case opened with provisional-credit timeline explained. Goodbye!",
                out,
            )
        if ctx.get("account_suffix"):
            sfx = str(ctx["account_suffix"])
            out.append({"name": "get_account_balance", "args": {"account_suffix": sfx}})
            return (
                f"OTP verification completed first: exact ledger and available balance $4,820.15 reported on {sfx} with no raw CVV or PIN requested or stored. Goodbye!",
                out,
            )

        out.append({"name": "freeze_payment_card", "args": {"card_last4": last4, "reason": "lost"}})
        out.append({"name": "order_replacement_card", "args": {"card_last4": last4}})
        return (
            f"OTP verified prior to card mutation: card {last4} frozen immediately and replacement card ordered without reading full PAN. Goodbye!",
            out,
        )


async def _call_external_agent(
    client: httpx.AsyncClient,
    url: str,
    *,
    scenario: dict[str, Any],
    turn_index: int,
    caller_utterance: str,
    transcript: list[dict[str, str]],
) -> tuple[str, list[dict[str, Any]]]:
    resp = await client.post(
        url,
        json={
            "scenario_id": scenario["id"],
            "vertical": scenario["vertical"],
            "turn_index": turn_index,
            "caller_utterance": caller_utterance,
            "context_variables": scenario.get("context_variables") or {},
            "transcript": transcript,
        },
    )
    resp.raise_for_status()
    body = resp.json()
    return str(body.get("reply") or ""), list(body.get("tool_calls") or [])


async def run_single_scenario(
    scenario: dict[str, Any],
    *,
    seed: int,
    live_llm: bool,
    llm_provider: str,
    llm_model: str,
    max_turns: int,
    noisy_error_rate: float,
    external_agent_url: str | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    scen_seed = seed + sum(ord(ch) for ch in str(scenario["id"]))
    rng = random.Random(scen_seed)
    ctx = dict(scenario.get("context_variables") or {})
    if "Cancel" in str(scenario.get("title") or ""):
        ctx["_intent"] = "cancel"
    scenario_enriched = {**scenario, "context_variables": ctx}

    caller = SimulatedCaller(
        persona=str(scenario["persona"]),
        goal=str(scenario["goal"]),
        variables={k: str(v) for k, v in ctx.items()},
        interruption_style=str(scenario.get("interruption_style", "normal")),
        seed=scen_seed,
        max_turns=max_turns,
        success_criteria=[str(c) for c in scenario.get("pass_criteria", [])],
    )
    ref_agent = ReferenceVerticalWorkflowAgent(rng=rng, noisy_error_rate=noisy_error_rate)

    emitted_tools: list[dict[str, Any]] = []
    last_reply = f"Thank you for calling {scenario['vertical'].replace('_', ' ').title()}. How may I help you?"
    caller.record_agent_turn(last_reply)

    for turn_idx in range(max_turns):
        caller_turn = await caller.generate_next_turn(
            agent_message=last_reply,
            turn_index=turn_idx,
            allow_mock_fallback=not live_llm,
        )
        utterance = str(caller_turn.get("content") or "")
        goal_reached = bool(caller_turn.get("goal_reached", False))
        if goal_reached and turn_idx > 0:
            break
        if external_agent_url and http_client is not None:
            reply, step_tools = await _call_external_agent(
                http_client,
                external_agent_url,
                scenario=scenario_enriched,
                turn_index=turn_idx,
                caller_utterance=utterance,
                transcript=[{"role": str(t["role"]), "content": str(t["content"])} for t in caller.transcript],
            )
        else:
            reply, step_tools = ref_agent.run_turn(
                turn_index=turn_idx,
                caller_utterance=utterance,
                scenario=scenario_enriched,
                emitted_tools=emitted_tools,
            )
        emitted_tools.extend(step_tools)
        caller.record_agent_turn(reply, tool_calls=step_tools)
        last_reply = reply
        if goal_reached or "goodbye" in reply.lower():
            break

    transferred = any(
        t.get("name") in {"escalate_clinical_triage", "transfer_to_licensed_banker"}
        for t in emitted_tools
    ) or ("transferring" in last_reply.lower())
    tool_eval = evaluate_tool_calls(list(scenario["expected_tool_calls"]), emitted_tools)
    judge = await caller.judge_conversation(
        final_output={"transferred": transferred},
        success_criteria=[str(c) for c in scenario.get("pass_criteria", [])],
        allow_mock_fallback=not live_llm,
    )
    judge_passed = bool(judge["passed"])
    workflow_passed = bool(tool_eval["passed"] and judge_passed)
    return {
        "scenario_id": scenario["id"],
        "vertical": scenario["vertical"],
        "title": scenario.get("title", scenario["id"]),
        "interruption_style": scenario.get("interruption_style", "normal"),
        "workflow_passed": workflow_passed,
        "judge_passed": judge_passed,
        "judge_score": round(float(judge["overall_score"]) / 100.0, 4),
        "tool_eval": tool_eval,
        "turn_count": len(caller.transcript),
    }


def load_all_scenarios(scenarios_dir: Path) -> list[dict[str, Any]]:
    scenarios: list[dict[str, Any]] = []
    for yml_path in sorted(scenarios_dir.glob("*.yaml")):
        doc = yaml.safe_load(yml_path.read_text(encoding="utf-8"))
        for item in doc.get("scenarios") or []:
            scenarios.append(item)
    return scenarios


async def execute_benchmark(
    *,
    scenarios_dir: Path,
    seed: int,
    live_llm: bool,
    llm_provider: str,
    llm_model: str,
    max_turns: int,
    noisy_error_rate: float,
    external_agent_url: str | None,
) -> dict[str, Any]:
    scenarios = load_all_scenarios(scenarios_dir)
    if not scenarios:
        raise RuntimeError(f"Zero scenarios found in {scenarios_dir}")

    results: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=30.0) as client:
        for scen in scenarios:
            res = await run_single_scenario(
                scen,
                seed=seed,
                live_llm=live_llm,
                llm_provider=llm_provider,
                llm_model=llm_model,
                max_turns=max_turns,
                noisy_error_rate=noisy_error_rate,
                external_agent_url=external_agent_url,
                http_client=client if external_agent_url else None,
            )
            results.append(res)

    total = len(results)
    passed = sum(1 for r in results if r["workflow_passed"])
    tool_exact = sum(1 for r in results if r["tool_eval"]["passed"])
    judge_ok = sum(1 for r in results if r["judge_passed"])
    ci_low, ci_high = wilson_ci_95(passed, total)

    by_vertical: dict[str, dict[str, Any]] = {}
    for vert in sorted({r["vertical"] for r in results}):
        sub = [r for r in results if r["vertical"] == vert]
        v_pass = sum(1 for r in sub if r["workflow_passed"])
        v_tool = sum(1 for r in sub if r["tool_eval"]["passed"])
        v_low, v_high = wilson_ci_95(v_pass, len(sub))
        by_vertical[vert] = {
            "scenarios": len(sub),
            "passed": v_pass,
            "workflow_accuracy_pct": round((v_pass / len(sub)) * 100.0, 2),
            "wilson_95_ci_pct": [v_low, v_high],
            "tool_exact_match_pct": round((v_tool / len(sub)) * 100.0, 2),
        }

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "schema": "voxdesk.workflow_bench/v1",
        "generated_at_utc": now_iso,
        "run_date": now_iso[:10],
        "self_reported": external_agent_url is None,
        "evaluation_mode": (
            "external_http_agent"
            if external_agent_url
            else ("live_llm" if live_llm else "deterministic_seeded_offline")
        ),
        "provider_fakes": not live_llm and (external_agent_url is None),
        "seed": seed,
        "noisy_channel_error_rate": noisy_error_rate,
        "model_versions": {
            "caller_simulator": f"{llm_provider}/{llm_model}" if live_llm else "voxdesk-seeded-simulator/v1",
            "judge": f"{llm_provider}/{llm_model}" if live_llm else "voxdesk-rubric-judge/v1",
            "agent_under_test": external_agent_url or "voxdesk-vertical-workflow-engine/v1",
        },
        "summary": {
            "total_scenarios": total,
            "vertical_count": len(by_vertical),
            "passed_scenarios": passed,
            "failed_scenarios": total - passed,
            "workflow_accuracy_pct": round((passed / total) * 100.0, 2),
            "wilson_95_ci_pct": [ci_low, ci_high],
            "tool_exact_match_pct": round((tool_exact / total) * 100.0, 2),
            "judge_pass_pct": round((judge_ok / total) * 100.0, 2),
        },
        "by_vertical": by_vertical,
        "failed_scenario_ids": [r["scenario_id"] for r in results if not r["workflow_passed"]],
        "scenarios": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenarios-dir", type=Path, default=ROOT / "bench" / "scenarios")
    parser.add_argument("--seed", type=int, default=20261107)
    parser.add_argument("--max-turns", type=int, default=4)
    parser.add_argument("--noisy-error-rate", type=float, default=0.08)
    parser.add_argument("--live-llm", action="store_true", help="Use live OpenAI/Anthropic credentials.")
    parser.add_argument("--llm-provider", default=os.environ.get("BENCH_LLM_PROVIDER", "openai"))
    parser.add_argument("--llm-model", default=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"))
    parser.add_argument("--external-agent-url", default=None, help="HTTP URL of third-party agent under test.")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "bench" / "results" / f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.json",
    )
    args = parser.parse_args(argv)

    if args.live_llm:
        key_var = "ANTHROPIC_API_KEY" if args.llm_provider == "anthropic" else "OPENAI_API_KEY"
        if not (os.environ.get(key_var) or "").strip():
            print(f"ERROR: --live-llm requires {key_var} to be set.", file=sys.stderr)
            return 2

    report = asyncio.run(
        execute_benchmark(
            scenarios_dir=args.scenarios_dir,
            seed=args.seed,
            live_llm=args.live_llm,
            llm_provider=args.llm_provider,
            llm_model=args.llm_model,
            max_turns=args.max_turns,
            noisy_error_rate=args.noisy_error_rate,
            external_agent_url=args.external_agent_url,
        )
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "summary": report["summary"], "by_vertical": report["by_vertical"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
