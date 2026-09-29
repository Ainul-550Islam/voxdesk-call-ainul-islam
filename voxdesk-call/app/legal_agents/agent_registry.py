from __future__ import annotations
from dataclasses import dataclass
from app.specialized_agents.registry import definitions as specialized_definitions

@dataclass(frozen=True)
class LegalAgentDefinition:
    name: str
    version: str
    risk_tier: str
    capabilities: tuple[str, ...]
    required_governance_controls: tuple[str, ...]
    supported_input: tuple[str, ...]
    supported_output: tuple[str, ...]
    review_behavior: str

_DEFINITIONS=(
 LegalAgentDefinition("intake_flow","1.0.0","high",("triage","matter_classification"),("policy_admission","human_review"),("intake_request","source_references"),("triage","missing_fields"),"uncertain triage requires review"),
 LegalAgentDefinition("virtual_paralegal","1.0.0","high",("source_bound_assistance",),("approved_model","policy_admission","citations"),("validated_documents","knowledge_sources"),("draft_assistance","source_references"),"AI interpretation is not approved automatically"),
 LegalAgentDefinition("billing_guard","1.0.0","high",("prebill_validation",),("policy_admission","configured_controls"),("billing_lines","rules"),("findings","review_state"),"flags require review as configured"),
 LegalAgentDefinition("billing_ops","1.0.0","high",("billing_action_preparation",),("policy_admission","explicit_approval","connector_authorization"),("validation_package","connector_action"),("action_readiness",),"no write without existing governed connector action"),
 LegalAgentDefinition("ocg_compliance","1.0.0","high",("configured_rule_validation",),("policy_admission","source_references"),("items","configured_controls"),("findings","evidence"),"high-risk findings may require review"),
 LegalAgentDefinition("metrics_insights","1.0.0","moderate",("persisted_metrics",),("policy_admission","evidence"),("scoped_persisted_records",),("metrics","data_quality"),"no outcome claims"),
)

def list_agents():
    # Surface the existing specialized runtime registry without creating a new runtime.
    return [*(_DEFINITIONS), *[d for d in specialized_definitions() if d.type in {"legal"}]]

def get_agent(name: str):
    for item in _DEFINITIONS:
        if item.name==name: return item
    raise KeyError(name)
