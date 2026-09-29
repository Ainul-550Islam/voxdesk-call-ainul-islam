from __future__ import annotations
from typing import Any

REQUIRED_FIELDS = ("matter_type", "client_reference", "description", "jurisdiction")

def triage_intake(payload: dict[str, Any], known_references: set[str] | None = None) -> dict[str, Any]:
    missing = [key for key in REQUIRED_FIELDS if not str(payload.get(key, "")).strip()]
    duplicate = bool(payload.get("client_reference") and known_references and payload["client_reference"] in known_references)
    confidence = "insufficient_information" if missing else ("low_confidence" if duplicate or payload.get("classification_uncertain") else "high_confidence")
    return {"matter_type": payload.get("matter_type") if confidence == "high_confidence" else None,
            "urgency": payload.get("urgency") if payload.get("urgency") in {"low", "normal", "high", "urgent"} else "unknown",
            "missing_fields": missing, "possible_duplicate": duplicate, "confidence": confidence,
            "routing_recommendation": payload.get("routing_hint") if confidence == "high_confidence" else None,
            "review_required": True, "review_state": "required",
            "review_reason": "Legal intake triage requires qualified human confirmation",
            "requested_controls": ["qualified_human_review", "matter_classification_confirmation"],
            "source_references": [],
            "disclaimer": "Triage assistance only; not a legal outcome or advice."}
