from __future__ import annotations
from typing import Any
from app.governance.hashing import sha256_hex

def validate_billing_line(line: dict[str, Any], rules: dict[str, Any], *, known_fingerprints: set[str] | None = None, source_references: list[str] | None = None) -> dict[str, Any]:
    issues=[]
    for field in rules.get("required_fields", []):
        if line.get(field) in (None, ""): issues.append({"code":"missing_metadata","field":field})
    categories=rules.get("allowed_categories")
    if categories is not None and line.get("category") not in categories: issues.append({"code":"category_not_configured","field":"category"})
    fp=sha256_hex(line)
    if known_fingerprints and fp in known_fingerprints: issues.append({"code":"possible_duplicate","field":"line"})
    if rules.get("require_source_reference") and not any(ref in (source_references or []) for ref in line.get("source_references", [])): issues.append({"code":"source_reference_missing","field":"source_references"})
    if rules.get("approval_required") and line.get("approval_reference") not in (source_references or []): issues.append({"code":"approval_missing","field":"approval_reference"})
    state="pass" if not issues else ("blocked" if any(i["code"] in rules.get("blocking_issue_codes", []) for i in issues) else "review_required" if rules.get("review_issue_codes") and any(i["code"] in rules["review_issue_codes"] for i in issues) else "flag")
    return {"status":state,"issues":issues,"line_fingerprint":fp,"rule_version":rules.get("version"),"disclaimer":"Configured billing checks only; no finding of fraud or illegality."}
