from __future__ import annotations
from typing import Any

def prepare_assistance(*, task: str, validated_documents: list[dict[str, Any]], legal_findings: list[dict[str, Any]], knowledge_sources: list[dict[str, Any]], missing_information: list[str] | None = None) -> dict[str, Any]:
    allowed = {"summarize_matter", "extract_facts", "retrieve_information", "review_questions", "follow_up_draft"}
    if task not in allowed: raise ValueError("unsupported paralegal task")
    source_refs=[]
    facts=[]
    for source in [*validated_documents, *knowledge_sources]:
        ref=source.get("source_reference") or source.get("reference")
        if ref:
            source_refs.append(ref)
            for fact in source.get("observed_facts", []): facts.append({"fact":fact,"source_reference":ref,"kind":"observed"})
    for finding in legal_findings:
        ref=finding.get("citation_reference")
        if ref: source_refs.append(ref)
    return {"task":task,"observed_facts":facts,"interpretations":[],"existing_findings":legal_findings,
            "source_references":sorted(set(source_refs)),"missing_information":list(missing_information or []),
            "review_questions":list(missing_information or []) if task=="review_questions" else [],
            "draft_follow_up_actions":[],"status":"insufficient_evidence" if not source_refs else "source_material_ready",
            "disclaimer":"AI-assisted preparation only; factual claims require source review."}
