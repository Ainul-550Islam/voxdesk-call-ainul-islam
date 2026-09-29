from __future__ import annotations
from typing import Any

def prepare_billing_package(lines: list[dict[str, Any]], checks: list[dict[str, Any]], *, connector_name: str | None = None, action_name: str | None = None, approval_reference: str | None = None) -> dict[str, Any]:
    if len(lines) != len(checks): raise ValueError("each billing line must have exactly one validation result")
    flagged=[i for i,result in enumerate(checks) if result.get("status") != "pass"]
    ready=not flagged and bool(connector_name and action_name)
    approved=bool(approval_reference)
    return {"line_count":len(lines),"flagged_indices":flagged,"action_readiness":"ready_for_governed_approval" if ready and not approved else "approved_reference_present" if ready and approved else "not_ready",
            "connector_name":connector_name,"action_name":action_name,"approval_reference":approval_reference,
            "external_write_executed":False,"reason":"Prepared only; external writes require existing connector path and governance approval."}
