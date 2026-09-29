"""Deterministic, configured operational checks for industry agent scopes.

This module does not make clinical decisions, issue certifications, or invent
regulatory requirements. A tenant's active framework controls the checks; any
missing inputs become insufficient evidence and reviewable findings.
"""
from __future__ import annotations
from typing import Any
from app.governance.hashing import sha256_hex

INDUSTRY_TYPES=frozenset({"healthcare","manufacturing","retail"})

def evaluate_industry_control(control:Any, observed:dict[str,Any], evidence_references:list[str], industry_type:str)->dict[str,Any]:
    rules=dict(getattr(control,"rules",{}) or {})
    required=list(getattr(control,"required_evidence",[]) or [])
    missing=[ref for ref in required if ref not in evidence_references]
    source=getattr(control,"source_reference",None)
    field=rules.get("field")
    expected=rules.get("expected")
    actual=observed.get(field) if isinstance(field,str) else None
    operator=rules.get("operator","equals")
    status="insufficient_evidence"
    rationale="Configured control, required source, or observed field is unavailable"
    gaps={}
    if industry_type not in INDUSTRY_TYPES:
        raise ValueError("unsupported industry control scope")
    if source and not missing and isinstance(field,str) and field and "expected" in rules and actual is not None:
        if operator=="equals":passed=actual==expected
        elif operator=="in":passed=isinstance(expected,list) and actual in expected
        elif operator=="greater_than":passed=isinstance(actual,(int,float)) and not isinstance(actual,bool) and isinstance(expected,(int,float)) and not isinstance(expected,bool) and actual>expected
        elif operator=="less_than":passed=isinstance(actual,(int,float)) and not isinstance(actual,bool) and isinstance(expected,(int,float)) and not isinstance(expected,bool) and actual<expected
        else:passed=None
        if passed is None:
            status="insufficient_evidence";rationale="Configured operator or typed values are unsupported"
        elif passed:
            status="pass";rationale="Observed value matched the tenant-configured control rule"
        else:
            status="review_required" if rules.get("review_on_mismatch",True) else "fail"
            rationale="Observed value did not match the tenant-configured control rule"
            gaps[field]={"observed_fingerprint":sha256_hex(actual),"expected":expected}
    elif missing:
        gaps["required_evidence"]={"missing":missing}
    elif source is None:
        rationale="Control lacks a configured source reference"
    elif field:
        gaps[field]={"observed":actual,"expected":expected}
    severity=str(getattr(control,"severity","medium"))
    return {
        "industry_type":industry_type,
        "control_id":str(getattr(control,"id","")),
        "control_key":str(getattr(control,"control_key","")),
        "result":status,
        "severity":severity,
        "observed_state":{field:{"present":actual is not None,"fingerprint":sha256_hex(actual)}} if isinstance(field,str) and field and field in observed else {},
        "expected_state":{field:expected} if isinstance(field,str) and field and "expected" in rules else {},
        "gaps":gaps,
        "source_reference":source,
        "evidence_references":list(evidence_references),
        "evidence_fingerprint":sha256_hex({"industry_type":industry_type,"observed":observed,"references":evidence_references}),
        "review_required":status in {"review_required","fail"} or severity in {"high","critical"},
        "rationale":rationale,
        "disclaimer":"Configured operational control check only; not a clinical decision, regulatory finding, or certification.",
    }
