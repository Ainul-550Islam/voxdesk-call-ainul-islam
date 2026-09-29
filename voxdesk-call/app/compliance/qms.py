from __future__ import annotations
import datetime as dt
from typing import Any
from app.governance.hashing import sha256_hex


def evaluate_control(control: Any, observed: dict[str, Any], evidence_references: list[str]) -> dict[str, Any]:
    rules=dict(getattr(control,"rules",{}) or {});required=list(getattr(control,"required_evidence",[]) or [])
    missing=[ref for ref in required if ref not in evidence_references];key=str(control.control_key);actual=observed.get(key);expected=rules.get("expected")
    status="insufficient_evidence";rationale="No configured expectation or source-backed evidence is available";gaps={};source=control.source_reference
    if rules.get("controlled_document"):
        document=observed.get("document")
        if not isinstance(document,dict):
            return _result(control,status,"controlled document record is absent",{},None,rules.get("document_requirements",{}),evidence_references,observed)
        requirements=dict(rules.get("document_requirements",{}));approved_states=rules.get("approved_states",["approved"])
        if not requirements or not source or missing:
            status="insufficient_evidence";rationale="Configured document requirements, source reference, or required evidence are missing"
        else:
            for field,required_value in requirements.items():
                if document.get(field)!=required_value:gaps[field]={"observed":document.get(field),"expected":required_value}
            for field in ("revision","owner","effective_date"):
                if rules.get("require_"+field) and not document.get(field):gaps[field]={"observed":document.get(field),"expected":"required"}
            if document.get("approval_state") not in approved_states:gaps["approval_state"]={"observed":document.get("approval_state"),"expected":approved_states}
            stale_days=rules.get("stale_after_days")
            if stale_days is not None:
                reviewed=document.get("last_reviewed_at")
                try:
                    reviewed_at=dt.datetime.fromisoformat(str(reviewed).replace("Z","+00:00"))
                    if reviewed_at.tzinfo is None:reviewed_at=reviewed_at.replace(tzinfo=dt.timezone.utc)
                    age=(dt.datetime.now(dt.timezone.utc)-reviewed_at).total_seconds()/86400
                    if age>float(stale_days):gaps["last_reviewed_at"]={"observed":reviewed,"expected":{"no_older_than_days":stale_days}}
                except (TypeError,ValueError):gaps["last_reviewed_at"]={"observed":None,"expected":"valid timestamp"}
            if gaps:
                status="needs_review" if rules.get("review_on_mismatch",True) else "non_compliant";rationale="One or more configured document controls did not match"
            else:status="compliant";rationale="All configured document requirements and evidence references matched; this is not certification"
        allowed=set(requirements)|{"revision","owner","effective_date","approval_state","last_reviewed_at"}
        safe_observed={name:document.get(name) for name in allowed if name in document}
        return _result(control,status,rationale,gaps,safe_observed,requirements,evidence_references,{"subject":safe_observed})
    if missing:
        status="insufficient_evidence";rationale="Required evidence references are missing"
    elif expected is None or not source:
        status="insufficient_evidence";rationale="A configured expected value and documented source reference are required"
    elif actual==expected:
        status="compliant";rationale="Configured expected value matched the observed control state"
    else:
        status="needs_review" if rules.get("review_on_mismatch",False) else "non_compliant";rationale="Observed control state differs from configured expected value";gaps={key:{"observed":actual,"expected":expected}}
    return _result(control,status,rationale,gaps,{key:actual} if key in observed else {},{key:expected},evidence_references,observed)


def _result(control,status,rationale,gaps,actual,expected,references,hash_input):
    return {"control_id":str(control.id),"control_key":control.control_key,"observed_state":actual,"expected_state":expected,"result":status,"severity":control.severity,"rationale":rationale,"gaps":gaps,"source_reference":control.source_reference,"evidence_references":list(references),"evidence_fingerprint":sha256_hex({"observed":hash_input,"references":references}),"review_required":status=="needs_review" or control.severity in {"high","critical"}}
