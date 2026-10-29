#!/usr/bin/env python3
"""Collect security scanner outputs into one evidence summary.

Reads the artifacts the security workflow uploads (SARIF from Bandit, Trivy and
Semgrep; JSON from npm audit, checkov and gitleaks; hadolint's JSON lines) and
writes ``reports/SECURITY_EVIDENCE_SUMMARY.json``: findings counted by severity,
per-scanner status, and the accepted-risk register carried in ``SECURITY.md``.

Two rules keep this honest:

* a scanner whose file is absent is reported as ``missing``, and the command
  exits non-zero unless ``--allow-missing`` is passed. A scanner that did not
  run cannot be counted as a scanner that found nothing;
* ``accepted_risks`` is parsed from ``SECURITY.md``. Findings that match an
  accepted entry are listed under it but never silently dropped from the count
  -- they are *attributed*, so a reviewer can see what was waived and why.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

#: Filename stem -> (scanner, kind). Matched case-insensitively as a substring
#: of the artifact path so CI directory layouts do not have to line up exactly.
KNOWN_SCANNERS: tuple[tuple[str, str, str], ...] = (
    ("bandit", "bandit", "sarif"),
    ("trivy", "trivy", "sarif"),
    ("semgrep", "semgrep", "sarif"),
    ("gitleaks", "gitleaks", "json"),
    ("npm-audit", "npm-audit", "json"),
    ("hadolint", "hadolint", "jsonl"),
    ("checkov", "checkov", "json"),
    ("pip-audit", "pip-audit", "json"),
)

SEVERITIES: tuple[str, ...] = ("critical", "high", "medium", "low", "info", "unknown")

#: SARIF ``level`` -> our severity vocabulary. ``error`` is treated as high
#: because every scanner in this pipeline is configured to fail on high.
_SARIF_LEVEL = {
    "error": "high",
    "warning": "medium",
    "note": "low",
    "none": "info",
}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def _severity(value: str | None) -> str:
    text = (value or "").strip().lower()
    if text in SEVERITIES:
        return text
    if text in _SARIF_LEVEL:
        return _SARIF_LEVEL[text]
    if text in {"moderate"}:  # npm audit's own vocabulary
        return "medium"
    if text in {"error", "fatal"}:
        return "high"
    if text in {"warn"}:
        return "medium"
    return "unknown"


def parse_sarif(path: Path) -> dict[str, Any]:
    """Count SARIF results by severity and keep the rule ids that fired."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"parse_error": f"{exc}", "counts": {}, "findings": [], "total": 0}

    counts = {severity: 0 for severity in SEVERITIES}
    findings: list[dict[str, Any]] = []
    total = 0
    for run in document.get("runs") or []:
        rules: dict[str, dict[str, Any]] = {}
        for rule in (run.get("tool", {}) or {}).get("driver", {}).get("rules") or []:
            if isinstance(rule, dict) and rule.get("id"):
                rules[str(rule["id"])] = rule
        for result in run.get("results") or []:
            if not isinstance(result, dict):
                continue
            total += 1
            rule_id = str(result.get("ruleId") or "")
            severity = _severity(result.get("level") or (rules.get(rule_id) or {}).get(
                "defaultConfiguration", {}
            ).get("level"))
            counts[severity] += 1
            if len(findings) < 200:
                location = ""
                locations = result.get("locations") or []
                if locations and isinstance(locations[0], dict):
                    physical = (locations[0].get("physicalLocation") or {})
                    location = str(
                        (physical.get("artifactLocation") or {}).get("uri") or ""
                    )
                findings.append(
                    {
                        "rule_id": rule_id,
                        "severity": severity,
                        "path": location,
                        "message": str((result.get("message") or {}).get("text") or "")[:200],
                    }
                )
    return {"counts": counts, "findings": findings, "total": total}


def parse_npm_audit(path: Path) -> dict[str, Any]:
    data = _load_json(path)
    counts = {severity: 0 for severity in SEVERITIES}
    findings: list[dict[str, Any]] = []
    total = 0
    vulnerabilities = (data or {}).get("vulnerabilities") or {}
    if isinstance(vulnerabilities, dict):
        for name, entry in vulnerabilities.items():
            if not isinstance(entry, dict):
                continue
            severity = _severity(entry.get("severity"))
            counts[severity] += 1
            total += 1
            if len(findings) < 200:
                findings.append(
                    {
                        "rule_id": name,
                        "severity": severity,
                        "path": str(entry.get("range") or ""),
                        "message": str(
                            ((entry.get("via") or [{}])[0] if entry.get("via") else {}).get("title")
                            or (entry.get("via") or [""])[0]
                            if isinstance(entry.get("via"), list) and entry.get("via")
                            else ""
                        )[:200],
                    }
                )
    return {"counts": counts, "findings": findings, "total": total}


def parse_checkov(path: Path) -> dict[str, Any]:
    data = _load_json(path)
    counts = {severity: 0 for severity in SEVERITIES}
    findings: list[dict[str, Any]] = []
    total = 0
    results = (data or {}).get("results") or {}
    failed = results.get("failed_checks") or []
    for entry in failed:
        if not isinstance(entry, dict):
            continue
        total += 1
        severity = _severity(entry.get("severity") or "high")
        counts[severity] += 1
        if len(findings) < 200:
            findings.append(
                {
                    "rule_id": str(entry.get("check_id") or ""),
                    "severity": severity,
                    "path": str(entry.get("file_path") or entry.get("resource") or ""),
                    "message": str(entry.get("check_name") or "")[:200],
                }
            )
    return {"counts": counts, "findings": findings, "total": total}


def parse_gitleaks(path: Path) -> dict[str, Any]:
    data = _load_json(path)
    if data is None:
        return {"counts": {s: 0 for s in SEVERITIES}, "findings": [], "total": 0}
    rows = data if isinstance(data, list) else (data.get("leaks") or data.get("findings") or [])
    counts = {severity: 0 for severity in SEVERITIES}
    findings: list[dict[str, Any]] = []
    for entry in rows:
        if not isinstance(entry, dict):
            continue
        counts["critical"] += 1
        if len(findings) < 200:
            findings.append(
                {
                    "rule_id": str(entry.get("RuleID") or ""),
                    "severity": "critical",
                    # Never echo the secret itself: the file and line are enough
                    # to act on, and the summary is a committed artifact.
                    "path": f"{entry.get('File', '')}:{entry.get('StartLine', '')}",
                    "message": "secret detected (value redacted)",
                }
            )
    return {"counts": counts, "findings": findings, "total": len(rows)}


def parse_hadolint(path: Path) -> dict[str, Any]:
    counts = {severity: 0 for severity in SEVERITIES}
    findings: list[dict[str, Any]] = []
    total = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        total += 1
        severity = _severity(entry.get("level"))
        counts[severity] += 1
        if len(findings) < 200:
            findings.append(
                {
                    "rule_id": str(entry.get("code") or ""),
                    "severity": severity,
                    "path": str(entry.get("file") or ""),
                    "message": str(entry.get("message") or "")[:200],
                }
            )
    return {"counts": counts, "findings": findings, "total": total}


def parse_pip_audit(path: Path) -> dict[str, Any]:
    """Count *advisories*, not pinned packages.

    pip-audit lists every resolved dependency and attaches a ``vulns`` array to
    only the vulnerable ones. Counting the outer entries reports a clean install
    as hundreds of findings, which is worse than useless: it hides the two
    packages that actually have advisories.
    """
    data = _load_json(path)
    counts = {severity: 0 for severity in SEVERITIES}
    findings: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    if isinstance(data, list):
        rows = [r for r in data if isinstance(r, dict)]
    elif isinstance(data, dict):
        rows = [r for r in (data.get("dependencies") or data.get("vulnerabilities") or [])
                if isinstance(r, dict)]

    total = 0
    for entry in rows:
        package = str(entry.get("name") or "")
        version = str(entry.get("version") or "")
        vulns = [v for v in (entry.get("vulns") or []) if isinstance(v, dict)]
        if not vulns:
            continue
        for vuln in vulns:
            total += 1
            # pip-audit carries no severity field; the advisory id is the only
            # classifier available, and guessing a severity from it would be a
            # fabricated precision.
            severity = _severity(vuln.get("severity") or "unknown")
            counts[severity] += 1
            if len(findings) < 200:
                fixed = ", ".join(str(v) for v in (vuln.get("fix_versions") or [])[:4])
                findings.append(
                    {
                        "rule_id": str(vuln.get("id") or ""),
                        "severity": severity,
                        "path": f"{package}=={version}" if version else package,
                        "package": package.lower(),
                        "message": (
                            f"fixed in {fixed}" if fixed else "no fixed version published"
                        ),
                    }
                )
    return {"counts": counts, "findings": findings, "total": total}


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"__parse_error__": str(exc)}


def classify(path: Path) -> tuple[str, str]:
    text = path.as_posix().lower()
    for stem, scanner, kind in KNOWN_SCANNERS:
        if stem in text:
            return scanner, kind
    if path.suffix == ".sarif":
        return path.stem, "sarif"
    return path.stem, "json"


def parse_artifact(path: Path) -> dict[str, Any]:
    scanner, kind = classify(path)
    if kind == "sarif":
        parsed = parse_sarif(path)
    elif scanner == "npm-audit":
        parsed = parse_npm_audit(path)
    elif scanner == "checkov":
        parsed = parse_checkov(path)
    elif scanner == "gitleaks":
        parsed = parse_gitleaks(path)
    elif scanner == "hadolint":
        parsed = parse_hadolint(path)
    elif scanner == "pip-audit":
        parsed = parse_pip_audit(path)
    else:
        parsed = parse_sarif(path) if path.suffix == ".sarif" else parse_npm_audit(path)
    row: dict[str, Any] = {
        "scanner": scanner,
        "artifact": _relative(path),
        "sha256": _sha256(path),
        "bytes": path.stat().st_size,
    }
    row.update(parsed)
    if parsed.get("parse_error") or (isinstance(parsed.get("counts"), dict) and "__parse_error__" in str(parsed)):
        row["status"] = "unparseable"
    else:
        row["status"] = "ran"
    return row


_ACCEPTED_LINE = re.compile(r"^\s*[-*]\s*(?P<body>.+?)\s*$")


def load_accepted_risks(path: Path) -> list[dict[str, str]]:
    """Read the accepted-risk register from SECURITY.md.

    Recognises two shapes, both of which already exist in the repository:

        - `GHSA-xxxx-yyyy-zzzz` -- reason text
        - **Accepted risk:** reason text

    Anything else in the document is left alone. The point is attribution, not
    suppression: a matched finding is still counted, it just carries the reason.
    """
    if not path.is_file():
        return []
    accepted: list[dict[str, str]] = []
    seen: set[str] = set()
    in_section = False
    for line in path.read_text(encoding="utf-8").splitlines():
        heading = line.strip().lower()
        if heading.startswith("#"):
            in_section = "accept" in heading or "known" in heading or "waiv" in heading
            continue
        if not in_section:
            continue

        package = ""
        reason = ""

        # Shape one: the risk register table in docs/SECURITY.md, whose second
        # column is the backticked `package version` under waiver.
        if line.lstrip().startswith("|"):
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) < 3 or set(cells[0]) <= set("-: "):
                continue
            backticked = re.search(r"`([^`]+)`", cells[1])
            if not backticked:
                continue
            spec = backticked.group(1).strip()
            package = re.split(r"[\s<>=()\[\]]+", spec, maxsplit=1)[0].strip().lower()
            reason = f"{spec}: {re.sub(r'\s+', ' ', cells[2]).strip()}"
        else:
            # Shape two: a bullet naming the identifier or waiver in backticks.
            match = _ACCEPTED_LINE.match(line)
            if not match:
                continue
            body = match.group("body")
            id_match = re.search(r"`([^`]+)`", body)
            if not id_match:
                continue
            package = id_match.group(1).strip().lower()
            reason = re.sub(r"\s+", " ", body).strip()

        # A waiver names a distribution. Anything with a path separator or a
        # file suffix is a file reference from some other part of the document
        # that happens to sit in backticks, and treating it as a package would
        # silently waive findings for whichever dependency shares its name.
        if not package or package in seen:
            continue
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", package) or "/" in package:
            continue
        if package.endswith((".py", ".md", ".json", ".yml", ".yaml")):
            continue
        seen.add(package)
        accepted.append({"identifier": package, "reason": reason})
    return accepted


def attribute(findings: list[dict[str, Any]], accepted: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Tag findings that match an accepted risk; never remove them."""
    for finding in findings:
        rule = str(finding.get("rule_id") or "").lower()
        path = str(finding.get("path") or "").lower()
        package = str(finding.get("package") or "").lower()
        haystack = " ".join(part for part in (rule, path, package) if part)
        matched = next(
            (
                entry["reason"]
                for entry in accepted
                if entry["identifier"] and entry["identifier"].lower() in haystack
            ),
            "",
        )
        if matched:
            finding["accepted_risk"] = matched
    return findings


def build_summary(input_dir: Path, security_md: Path) -> dict[str, Any]:
    accepted = load_accepted_risks(security_md)
    scanners: list[dict[str, Any]] = []
    problems: list[str] = []

    if not input_dir.is_dir():
        return {
            "schema": "voxdesk.security_evidence/v1",
            "generated_at": _now(),
            "input_dir": str(input_dir),
            "scanners": [
                {"scanner": scanner, "status": "missing", "reason": "no artifact found"}
                for scanner, _, _ in KNOWN_SCANNERS
            ],
            "counts": {severity: 0 for severity in SEVERITIES},
            "total": 0,
            "accepted_risks": accepted,
            "overall": "BLOCKED",
            "problems": [f"input directory not found: {input_dir}"],
        }

    files = sorted(
        p for p in input_dir.rglob("*")
        if p.is_file() and p.suffix in {".sarif", ".json", ".jsonl", ".txt"}
    )
    seen: dict[str, dict[str, Any]] = {}
    for path in files:
        row = parse_artifact(path)
        existing = seen.get(row["scanner"])
        if existing is None:
            seen[row["scanner"]] = row
            continue
        for severity in SEVERITIES:
            existing.setdefault("counts", {}).setdefault(severity, 0)
            existing["counts"][severity] += int(row.get("counts", {}).get(severity, 0))
        existing["total"] = int(existing.get("total", 0)) + int(row.get("total", 0))
        existing["findings"] = list(existing.get("findings", [])) + list(row.get("findings", []))
        existing["artifact"] = f"{existing['artifact']} + {row['artifact']}"

    for scanner, _, _ in KNOWN_SCANNERS:
        row = seen.pop(scanner, None)
        if row is None:
            scanners.append(
                {"scanner": scanner, "status": "missing", "reason": "no artifact found"}
            )
            problems.append(f"{scanner}: no artifact")
            continue
        row["findings"] = attribute(row.get("findings", []), accepted)[:200]
        row["status"] = "failed" if row.get("total") else ("unparseable" if row.get("parse_error") else "clean")
        if row["status"] == "unparseable":
            problems.append(f"{scanner}: output could not be parsed")
        scanners.append(row)
    for row in seen.values():
        row["findings"] = attribute(row.get("findings", []), accepted)[:200]
        row["status"] = "failed" if row.get("total") else "clean"
        scanners.append(row)

    counts = {severity: 0 for severity in SEVERITIES}
    for row in scanners:
        for severity in SEVERITIES:
            counts[severity] += int((row.get("counts") or {}).get(severity, 0))
    total = sum(counts.values())

    return {
        "schema": "voxdesk.security_evidence/v1",
        "generated_at": _now(),
        "input_dir": _relative(input_dir),
        "artifact_count": len(files),
        "scanners": scanners,
        "counts": counts,
        "total": total,
        "accepted_risks": accepted,
        "overall": "BLOCKED" if any(s["status"] == "missing" for s in scanners) else (
            "FAIL" if (counts["critical"] or counts["high"]) else "PASS"
        ),
        "problems": problems,
        "note": (
            "Findings matched to an accepted risk in SECURITY.md are attributed, "
            "not removed: they remain in the counts and carry the waiver reason."
        ),
    }


#: Control -> where the enforcement lives -> the artifact that proves it.
#: The renderer checks that every evidence path exists in the checkout and
#: prints MISSING when it does not, so the document can never cite a file that
#: was never produced. A control with no artifact is not a control.
CONTROLS: tuple[tuple[str, str, str], ...] = (
    (
        "Envelope encryption, per tenant",
        "app/security/kms/ (AWS KMS, Vault Transit, local key ring)",
        "tests/security/test_kms_adapters.py",
    ),
    (
        "Unconfigured KMS fails closed (no silent local-key fallback)",
        "app/security/kms/local_key_ring.py :: UnconfiguredKmsAdapter",
        "tests/security/test_kms_adapters.py",
    ),
    (
        "Browser security headers (CSP, HSTS, nosniff, frame-ancestors)",
        "app/core/security_headers.py",
        "tests/security/test_security_headers.py",
    ),
    (
        "CORS allow-list, never a wildcard, no credentials for unknown origins",
        "app/core/config.py :: Settings.validate_security",
        "tests/security/test_cors_policy.py",
    ),
    (
        "Refuses to boot on a default, short or placeholder secret",
        "app/main.py lifespan -> Settings.validate_security",
        "tests/security/test_boot_secret_policy.py",
    ),
    (
        "Authentication rate limiting per source address",
        "app/api/auth_routes.py, app/core/rate_limit.py",
        "tests/security/test_auth_rate_limits.py",
    ),
    (
        "SSRF guard on every tenant-supplied destination URL",
        "app/core/ssrf.py",
        "tests/security/test_ssrf_regression.py",
    ),
    (
        "Deployment assets are validated, not merely parsed",
        "scripts/verify_deploy_assets.sh",
        "tests/deployment/test_deploy_assets_static.py",
    ),
    (
        "Signed provider webhooks are verified before use",
        "app/telephony/ (Twilio / Telnyx / Vonage signature checks)",
        "tests/telephony/",
    ),
    (
        "Tenant isolation on every query",
        "app/auth/dependencies.py :: TenantContext.require_permission",
        "tests/security/test_tenant_isolation_matrix.py",
    ),
)

#: Certifications are the buyer's to obtain. Listing them as ours would be a
#: claim no file in this repository can support.
BUYER_OBTAINED = (
    "SOC 2 Type I / Type II",
    "ISO/IEC 27001",
    "HIPAA (BAA and assessment)",
    "PCI DSS (if card data is in scope)",
    "Penetration test by an external firm",
)


def render_posture_doc(summary: dict[str, Any]) -> str:
    """Render docs/SECURITY_POSTURE.md from the evidence summary.

    Nothing in the document is typed by a human: the counts come from
    ``reports/SECURITY_EVIDENCE_SUMMARY.json`` and every cited control is
    checked against the checkout at render time.
    """
    counts = summary.get("counts", {})
    total = int(summary.get("total", 0))
    lines: list[str] = []
    lines.append("# Security posture")
    lines.append("")
    lines.append(
        "Generated by `python scripts/security_evidence.py --write-posture-doc`. "
        "Every number below is read from `reports/SECURITY_EVIDENCE_SUMMARY.json`, "
        "which is itself built from scanner artifacts. Edit the scanners, not this file."
    )
    lines.append("")
    lines.append(f"- Generated: {summary.get('generated_at', 'unknown')}")
    lines.append(f"- Overall: **{summary.get('overall', 'UNKNOWN')}**")
    lines.append(f"- Artifacts read: {summary.get('artifact_count', 0)} from `{summary.get('input_dir', '?')}`")
    lines.append(f"- Total findings: {total}")
    lines.append("")

    lines.append("## Scanner status")
    lines.append("")
    lines.append("| Scanner | Status | Findings |")
    lines.append("|---|---|---|")
    for row in summary.get("scanners", []):
        lines.append(
            f"| `{row.get('scanner')}` | {row.get('status')} | {int(row.get('total', 0))} |"
        )
    lines.append("")
    lines.append(
        "`missing` means the scanner produced no artifact, which is not the same as "
        "`clean`: a check that did not run cannot support a claim. The security "
        "workflow uploads every artifact listed here."
    )
    lines.append("")

    lines.append("## Findings by severity")
    lines.append("")
    lines.append("| Severity | Count |")
    lines.append("|---|---|")
    for severity in SEVERITIES:
        lines.append(f"| {severity} | {int(counts.get(severity, 0))} |")
    lines.append(f"| **total** | **{total}** |")
    lines.append("")

    accepted = summary.get("accepted_risks") or []
    lines.append("## Accepted risks")
    lines.append("")
    if not accepted:
        lines.append("_No accepted risks are registered in `SECURITY.md`._")
    else:
        lines.append("| Package / finding | Documented reason |")
        lines.append("|---|---|")
        for item in accepted:
            reason = str(item.get("reason", "")).replace("|", r"\|")
            lines.append(f"| `{item.get('identifier')}` | {reason[:220]} |")
    lines.append("")
    lines.append(
        "An accepted risk is attributed, not deleted: it still counts in the totals "
        "above and carries the waiver from `SECURITY.md`."
    )
    lines.append("")

    lines.append("## Controls and the file that proves each one")
    lines.append("")
    lines.append("| Control | Enforcement | Evidence |")
    lines.append("|---|---|---|")
    for control, enforcement, evidence in CONTROLS:
        present = (ROOT / evidence.rstrip("/")).exists()
        marker = "" if present else " **(MISSING)**"
        lines.append(f"| {control} | `{enforcement}` | `{evidence}`{marker} |")
    lines.append("")
    lines.append(
        "Each evidence path is checked when this document is generated; a path "
        "marked MISSING is a control whose proof is not in the repository."
    )
    lines.append("")

    lines.append("## Certifications")
    lines.append("")
    lines.append(
        "None. The following are obtained by the buyer as part of their own "
        "compliance programme; this repository is an input to them, not a "
        "substitute for them:"
    )
    lines.append("")
    for item in BUYER_OBTAINED:
        lines.append(f"- {item}")
    lines.append("")

    problems = summary.get("problems") or []
    if problems:
        lines.append("## Open items")
        lines.append("")
        for problem in problems:
            lines.append(f"- {problem}")
        lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=ROOT / "artifacts" / "security",
        help="directory holding the SARIF/JSON outputs uploaded by the security workflow",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "reports" / "SECURITY_EVIDENCE_SUMMARY.json",
    )
    parser.add_argument(
        "--security-md",
        type=Path,
        default=ROOT / "docs" / "SECURITY.md",
        help="source of the accepted-risk register (docs/SECURITY.md §7)",
    )
    parser.add_argument(
        "--allow-missing",
        action="store_true",
        help="write the summary even when a scanner produced no artifact",
    )
    parser.add_argument(
        "--fail-on",
        default="high",
        choices=("never", "critical", "high", "medium", "low"),
        help="minimum severity that makes this command exit non-zero",
    )
    parser.add_argument(
        "--write-posture-doc",
        type=Path,
        default=None,
        metavar="PATH",
        help="render docs/SECURITY_POSTURE.md from this summary instead of hand-writing it",
    )
    args = parser.parse_args(argv)

    summary = build_summary(args.input_dir.resolve(), args.security_md.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    if args.write_posture_doc is not None:
        args.write_posture_doc.parent.mkdir(parents=True, exist_ok=True)
        args.write_posture_doc.write_text(render_posture_doc(summary), encoding="utf-8")
        print(f"wrote {_relative(args.write_posture_doc)}", file=sys.stderr)

    print(
        json.dumps(
            {
                "overall": summary["overall"],
                "counts": summary["counts"],
                "total": summary["total"],
                "scanners": [
                    {"scanner": row["scanner"], "status": row["status"], "total": row.get("total", 0)}
                    for row in summary["scanners"]
                ],
                "output": str(args.output),
            },
            indent=2,
        )
    )

    if args.fail_on != "never":
        threshold = SEVERITIES.index(args.fail_on)
        for severity in SEVERITIES[: threshold + 1]:
            if summary["counts"].get(severity):
                print(f"FAIL: {summary['counts'][severity]} {severity} finding(s)", flush=True)
                return 1
    if summary["problems"]:
        missing_only = all(problem.endswith("no artifact") for problem in summary["problems"])
        if not (args.allow_missing and missing_only):
            for problem in summary["problems"]:
                print(f"FAIL: {problem}", flush=True)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
