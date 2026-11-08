#!/usr/bin/env python3
"""Generate conservative feature claims from exact JUnit test identities and real-provider ledgers.

Outputs:
- `docs/SALES/FEATURE_MATRIX_VERIFIED.md` with two status columns (`Test status` and `Provider evidence`)
- `docs/EVIDENCE_STATUS.md` summarizing the latest real external provider run (`tests/real/`)

Enforces a `<= 7` day freshness rule and provides `--check` to fail non-zero on drift or staleness.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.junit_xml import iter_cases, iter_suites  # noqa: E402

STATUSES = {"LIVE", "API_ONLY", "PLANNED", "NOT_CONFIGURED"}
ALLOWED_TIERS = {
    "provider_gated",
    "in_process_e2e",
    "local_service_e2e",
    "compliance_attestation",
}
FRESHNESS_MAX_DAYS = 7

CANONICAL_PROVIDERS: tuple[tuple[str, str, str], ...] = (
    ("twilio", "Telephony (PSTN + Media Streams)", "tests/real/test_twilio_inbound_outbound.py"),
    ("telnyx", "Telephony (Call Control v2 + WS)", "tests/real/test_telnyx_live.py"),
    ("deepgram", "Streaming STT", "tests/real/test_stt_tts_llm_stream.py"),
    ("elevenlabs", "Streaming TTS", "tests/real/test_stt_tts_llm_stream.py"),
    ("cartesia", "Streaming TTS", "tests/real/test_stt_tts_llm_stream.py"),
    ("openai", "Streaming LLM", "tests/real/test_stt_tts_llm_stream.py"),
    ("anthropic", "Streaming LLM", "tests/real/test_stt_tts_llm_stream.py"),
    ("openai_realtime", "Speech-to-Speech (S2S)", "tests/real/test_s2s_live.py"),
    ("gemini_live", "Speech-to-Speech (S2S)", "tests/real/test_s2s_live.py"),
    ("salesforce", "CRM Sandbox Writeback", "tests/real/test_salesforce_sandbox.py"),
    ("hubspot", "CRM Sandbox Writeback", "tests/real/test_hubspot_sandbox.py"),
    ("webhook_receiver", "Signed Outbound Webhook", "tests/real/test_webhook_receiver.py"),
)


def junit_results(path: Path | None, root: Path = ROOT) -> dict[str, bool]:
    if path is None or not Path(path).exists():
        return {}
    xml_files = sorted(Path(path).glob("*.xml")) if Path(path).is_dir() else [Path(path)]
    results: dict[str, bool] = {}
    for xml_path in xml_files:
        if any(int(suite.get("errors", "0")) for suite in iter_suites(xml_path)):
            return {}
        for case in iter_cases(xml_path):
            classname = case.get("classname", "").split(".")
            node = None
            for i in range(len(classname), 0, -1):
                file = "/".join(classname[:i]) + ".py"
                if (root / file).is_file():
                    raw_name = case.get("name", "")
                    node = "::".join([file, *classname[i:], raw_name])
                    break
            if node:
                passed = not any(case.find(tag) is not None for tag in ("failure", "error", "skipped"))
                results[node] = results.get(node, True) and passed
                base_node = node.split("[", 1)[0]
                if base_node != node:
                    results[base_node] = results.get(base_node, True) and passed
    return results


def junit_execution_date(path: Path | None) -> str | None:
    """Use the oldest supplied suite timestamp, never the regeneration date.

    Pytest's offset-free timestamps are interpreted as UTC (the CI timezone).
    Missing, malformed, or future timestamps cannot establish fresh evidence.
    """
    if path is None or not Path(path).exists():
        return None
    xml_files = sorted(Path(path).glob("*.xml")) if Path(path).is_dir() else [Path(path)]
    dates: list[datetime] = []
    for xml_path in xml_files:
        for suite in iter_suites(xml_path):
            if not suite.findall("testcase"):
                continue
            stamp = suite.get("timestamp")
            if not stamp:
                return None
            try:
                parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                parsed = parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
            except ValueError:
                return None
            if parsed > datetime.now(timezone.utc):
                return None
            dates.append(parsed)
    return min(dates).date().isoformat() if dates else None


def _parse_iso_date(raw: str | None) -> date | None:
    if not raw or not isinstance(raw, str) or len(raw.strip()) < 10:
        return None
    try:
        return date.fromisoformat(raw.strip()[:10])
    except ValueError:
        return None


def _age_in_days(run_date_str: str | None, ref: date | None) -> int | None:
    if ref is None:
        return 0
    parsed = _parse_iso_date(run_date_str)
    if parsed is None:
        return None
    return (ref - parsed).days


def load_latest_real_evidence(real_root: Path) -> dict[str, Any] | None:
    if not real_root.exists():
        return None
    if real_root.is_file():
        candidates = [real_root]
    elif (real_root / "real_integrations.json").is_file():
        candidates = [real_root / "real_integrations.json"]
    else:
        candidates = sorted(real_root.glob("*/real_integrations.json"))
    if not candidates:
        return None
    chosen = candidates[-1]
    try:
        doc = json.loads(chosen.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(doc, dict) or doc.get("provider_fakes") is not False:
        return None
    doc["_artifact_path"] = chosen
    return doc


def evaluate_provider_cell(
    feature: dict[str, Any],
    real_doc: dict[str, Any] | None,
    *,
    reference_date: date | None,
    max_age_days: int = FRESHNESS_MAX_DAYS,
) -> str:
    tier = str(feature.get("evidence_tier") or "in_process_e2e")
    prov_entries = list(feature.get("provider_evidence") or [])
    if not prov_entries:
        if tier == "compliance_attestation":
            return "N/A (auditor attestation pending)"
        if tier == "local_service_e2e":
            return "LOCAL_STACK (first-party)"
        return "N/A (first-party)"

    providers = [str(item.get("provider") or "") for item in prov_entries if item.get("provider")]
    if "sip_sbc" in providers and feature.get("status") == "NOT_CONFIGURED":
        return "UNCONFIGURED (external SIP SBC)"

    if real_doc is None:
        return "UNVERIFIED_REAL (no provider run)"

    run_date = str(real_doc.get("run_date") or str(real_doc.get("generated_at") or "")[:10] or "UNVERIFIED")
    age = _age_in_days(run_date, reference_date)
    if age is None or age > max_age_days:
        return f"STALE_REAL (>{max_age_days}d: {run_date})"

    ledger = real_doc.get("providers") or {}
    statuses = [str((ledger.get(p) or {}).get("status") or "MISSING") for p in providers]
    if any(s == "FAIL" for s in statuses):
        failed = [p for p, s in zip(providers, statuses) if s == "FAIL"]
        return f"FAILED_REAL ({run_date}: {', '.join(failed)})"
    if all(s == "PASS" for s in statuses):
        return f"VERIFIED_REAL ({run_date}: {', '.join(providers)})"
    if any(s == "SKIPPED_NO_CREDENTIALS" for s in statuses):
        return f"PENDING_CREDENTIALS ({run_date})"
    return f"UNVERIFIED_REAL ({run_date})"


def render(
    features: list[dict[str, Any]],
    results: dict[str, bool],
    verified_date: str | None,
    *,
    real_doc: dict[str, Any] | None = None,
    reference_date: date | None = None,
    max_age_days: int = FRESHNESS_MAX_DAYS,
) -> str:
    junit_stale = False
    if reference_date is not None:
        age = _age_in_days(verified_date, reference_date)
        junit_stale = age is None or age > max_age_days

    ids: set[Any] = set()
    rows: list[str] = []
    for feature in features:
        if feature["id"] in ids or feature["status"] not in STATUSES:
            raise ValueError("Duplicate feature ID or unsupported status")
        ids.add(feature["id"])
        tier = str(feature.get("evidence_tier") or "in_process_e2e")
        if tier not in ALLOWED_TIERS:
            raise ValueError(f"Unsupported evidence_tier {tier!r}")
        evidence = feature["evidence_tests"]
        if not isinstance(evidence, list) or any(not isinstance(x, str) or "::" not in x for x in evidence):
            raise ValueError("Evidence must contain exact pytest node IDs")

        verified = bool(evidence) and bool(verified_date) and all(results.get(node, False) for node in evidence)
        status = feature["status"]
        if status == "LIVE" and (not verified or junit_stale):
            status = "API_ONLY"

        provider_cell = evaluate_provider_cell(
            feature,
            real_doc,
            reference_date=reference_date,
            max_age_days=max_age_days,
        )
        proof = (
            "; ".join(f"`{node}` ({'pass' if results.get(node) else 'not verified'})" for node in evidence)
            or "No qualifying execution evidence supplied"
        )
        name = feature["name"].replace("|", "\\|").replace("\n", " ")
        if verified and not junit_stale:
            date_cell = str(verified_date)
        elif verified and junit_stale:
            date_cell = f"Stale (>{max_age_days}d: {verified_date})"
        else:
            date_cell = "Not verified"
        rows.append(
            f"| {feature['id']}: {name} | {status} | {provider_cell} | {proof} | {date_cell} |"
        )

    header = (
        "# Verified feature matrix\n\n"
        "Generated by `scripts/generate_feature_matrix.py` from `tests/truth/feature_manifest.yaml`, "
        "supplied JUnit evidence (`evidence/junit/feature_evidence.xml`), and real external provider "
        "ledgers (`docs/EVIDENCE_STATUS.md`). Both `Test status` and `Provider evidence` enforce a "
        f"`<= {max_age_days}` day freshness window. No live-provider or production-readiness claim follows "
        "from `API_ONLY` or `PENDING_CREDENTIALS`. Missing, failed, skipped, stale, or ambiguous evidence "
        "never establishes `LIVE` or `VERIFIED_REAL`.\n\n"
        "| Feature | Test status | Provider evidence | Evidence | Last verified date |\n"
        "|---|---|---|---|---|\n"
    )
    return header + "\n".join(rows) + "\n"


def render_evidence_status(
    real_doc: dict[str, Any] | None,
    *,
    repo_root: Path,
    reference_date: date,
    max_age_days: int = FRESHNESS_MAX_DAYS,
) -> str:
    artifact_rel = "evidence/real-integrations/<none>"
    junit_rel = "evidence/real-integrations/<none>"
    run_date = "UNVERIFIED"
    freshness_banner = f"MISSING (no `real_integrations.json` artifact found; `<= {max_age_days}` days required)"
    providers_map: dict[str, Any] = {}

    if real_doc is not None:
        artifact_path = Path(real_doc["_artifact_path"]).resolve()
        try:
            artifact_rel = artifact_path.relative_to(repo_root.resolve()).as_posix()
        except ValueError:
            artifact_rel = artifact_path.as_posix()
        junit_candidate = artifact_path.parent / "junit.xml"
        if junit_candidate.exists():
            try:
                junit_rel = junit_candidate.relative_to(repo_root.resolve()).as_posix()
            except ValueError:
                junit_rel = junit_candidate.as_posix()
        run_date = str(
            real_doc.get("run_date") or str(real_doc.get("generated_at") or "")[:10] or "UNVERIFIED"
        )
        age = _age_in_days(run_date, reference_date)
        if age is not None and age <= max_age_days:
            freshness_banner = f"FRESH (`{age}` day(s) old; window `<= {max_age_days}` days)"
        else:
            freshness_banner = f"STALE (`{age}` day(s) old; exceeds `<= {max_age_days}` day window)"
        providers_map = dict(real_doc.get("providers") or {})

    lines = [
        "# VoxDesk — Real External Provider Evidence Status",
        "",
        "Generated automatically by `scripts/generate_feature_matrix.py` from the",
        "nightly real-provider integration ledger (`tests/real/`). Do not edit by hand.",
        "",
        f"- **Latest Run Date (UTC)**: `{run_date}`",
        f"- **Freshness Status**: {freshness_banner}",
        "- **`provider_fakes`**: `false` (enforced across every `tests/real/` suite)",
        f"- **Evidence JSON**: [`{artifact_rel}`](../{artifact_rel})",
        f"- **JUnit XML**: [`{junit_rel}`](../{junit_rel})",
        "",
        "## Per-Provider Integration Status",
        "",
        "| Provider | Capability Stage | Last Run Date | Freshness | Result | Latency (ms) | Model / Suite | Evidence Artifact |",
        "| --- | --- | --- | --- | --- | ---: | --- | --- |",
    ]

    age = _age_in_days(run_date, reference_date) if real_doc is not None else None
    fresh_cell = (
        f"`FRESH ({age}d)`"
        if (age is not None and age <= max_age_days)
        else (f"`STALE ({age}d)`" if age is not None else "`MISSING`")
    )

    for slug, stage, default_file in CANONICAL_PROVIDERS:
        entry = providers_map.get(slug) or {}
        status = str(entry.get("status") or "NOT_RUN")
        lat = entry.get("latency_ms")
        lat_cell = f"{float(lat):.1f}" if isinstance(lat, (int, float)) else "—"
        model_cell = f"`{entry['model']}`" if entry.get("model") else f"`{default_file}`"
        link_cell = f"[`real_integrations.json`](../{artifact_rel})" if real_doc is not None else "—"
        date_cell = f"`{run_date}`" if real_doc is not None else "—"
        lines.append(
            f"| `{slug}` | {stage} | {date_cell} | {fresh_cell} | `{status}` | {lat_cell} | {model_cell} | {link_cell} |"
        )

    lines.extend(
        [
            "",
            "## Reproduction Commands",
            "",
            "```bash",
            "VOXDESK_REAL_INTEGRATION=1 \\",
            "VOXDESK_REAL_EVIDENCE_DIR=evidence/real-integrations/$(date -u +%Y-%m-%d) \\",
            "pytest tests/real --junitxml=evidence/real-integrations/$(date -u +%Y-%m-%d)/junit.xml -rs",
            "python scripts/generate_feature_matrix.py",
            "python scripts/generate_feature_matrix.py --check",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    default_junit = ROOT / "evidence/junit/feature_evidence.xml"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=ROOT / "tests/truth/feature_manifest.yaml")
    parser.add_argument("--junit", type=Path, default=default_junit if default_junit.is_file() else None)
    parser.add_argument(
        "--real-evidence-dir",
        type=Path,
        default=ROOT / "evidence/real-integrations",
    )
    parser.add_argument("--output", type=Path, default=ROOT / "docs/SALES/FEATURE_MATRIX_VERIFIED.md")
    parser.add_argument(
        "--evidence-status-output",
        type=Path,
        default=ROOT / "docs/EVIDENCE_STATUS.md",
    )
    parser.add_argument("--no-evidence-status", action="store_true")
    parser.add_argument(
        "--reference-date",
        default=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    )
    parser.add_argument("--max-age-days", type=int, default=FRESHNESS_MAX_DAYS)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--require-fresh-real-runs", action="store_true")
    args = parser.parse_args(argv)

    ref_date = _parse_iso_date(args.reference_date)
    if ref_date is None:
        print(f"ERROR: invalid --reference-date {args.reference_date!r}", file=sys.stderr)
        return 2

    data = yaml.safe_load(args.manifest.read_text(encoding="utf-8"))
    evidence_date = junit_execution_date(args.junit)
    results = junit_results(args.junit, root=ROOT) if evidence_date else {}
    real_doc = load_latest_real_evidence(args.real_evidence_dir)

    text = render(
        data["features"],
        results,
        evidence_date,
        real_doc=real_doc,
        reference_date=ref_date,
        max_age_days=args.max_age_days,
    )
    status_md = render_evidence_status(
        real_doc,
        repo_root=ROOT,
        reference_date=ref_date,
        max_age_days=args.max_age_days,
    )

    errors: list[str] = []
    j_age = _age_in_days(evidence_date, ref_date)
    if j_age is None or j_age > args.max_age_days:
        errors.append(
            f"JUnit evidence is missing or stale ({evidence_date!r}; max_age_days={args.max_age_days})."
        )

    for feat in data["features"]:
        if feat["status"] in {"LIVE", "API_ONLY"}:
            if not all(results.get(node, False) for node in feat["evidence_tests"]):
                errors.append(f"Feature #{feat['id']} ({feat['name']}) has unverified/red evidence_tests.")

    if args.require_fresh_real_runs:
        if real_doc is None:
            errors.append("Missing real_integrations.json with provider_fakes=false.")
        else:
            r_date = str(real_doc.get("run_date") or str(real_doc.get("generated_at") or "")[:10])
            r_age = _age_in_days(r_date, ref_date)
            if r_age is None or r_age > args.max_age_days:
                errors.append(
                    f"Real-provider integration evidence is stale: {r_date!r} ({r_age} days > {args.max_age_days})."
                )
            failed_provs = [
                k
                for k, v in (real_doc.get("providers") or {}).items()
                if isinstance(v, dict) and v.get("status") == "FAIL"
            ]
            if failed_provs:
                errors.append(f"Real-provider integration failures: {', '.join(sorted(failed_provs))}")

    if args.check:
        if not args.output.is_file() or args.output.read_text(encoding="utf-8") != text:
            errors.append(f"Drift in {args.output}; regenerate via `python scripts/generate_feature_matrix.py`.")
        if not args.no_evidence_status:
            if (
                not args.evidence_status_output.is_file()
                or args.evidence_status_output.read_text(encoding="utf-8") != status_md
            ):
                errors.append(
                    f"Drift in {args.evidence_status_output}; regenerate via `python scripts/generate_feature_matrix.py`."
                )
        if errors:
            for err in errors:
                print(f"ERROR: {err}", file=sys.stderr)
            return 1
        print(f"OK: {args.output} and {args.evidence_status_output} are fresh and in sync.")
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")
    if not args.no_evidence_status:
        args.evidence_status_output.parent.mkdir(parents=True, exist_ok=True)
        args.evidence_status_output.write_text(status_md, encoding="utf-8")

    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
