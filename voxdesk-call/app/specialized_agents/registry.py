"""Static specialized-agent capability registry.

The registry is deliberately metadata-only. Durable model approval and policy
admission remain in Prompt-1 governance services; this module never approves a
model or authorizes an execution by itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .enums import AgentCapability, AgentStatus, AgentType, RiskTier
from .exceptions import UnknownAgentError


@dataclass(frozen=True)
class AgentDefinition:
    id: str
    type: str
    name: str
    version: str
    status: str
    risk_tier: str
    capabilities: tuple[str, ...]
    supported_inputs: tuple[str, ...]
    supported_outputs: tuple[str, ...]
    required_controls: tuple[str, ...]

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "name": self.name,
            "version": self.version,
            "status": self.status,
            "risk_tier": self.risk_tier,
            "capabilities": list(self.capabilities),
            "supported_inputs": list(self.supported_inputs),
            "supported_outputs": list(self.supported_outputs),
            "required_controls": list(self.required_controls),
        }


_DEFINITIONS = (
    AgentDefinition(
        id="specialized-legal",
        type=AgentType.LEGAL.value,
        name="Legal document analysis",
        version="1.0.0",
        status=AgentStatus.ACTIVE.value,
        risk_tier=RiskTier.HIGH.value,
        capabilities=(AgentCapability.DOCUMENT_ANALYSIS.value,),
        supported_inputs=("normalized_document_chunks", "source_references"),
        supported_outputs=("legal_findings", "citations", "review_state"),
        required_controls=("approved_model", "policy_admission", "human_review"),
    ),
    AgentDefinition(
        id="specialized-translation",
        type=AgentType.TRANSLATION.value,
        name="Governed translation",
        version="1.0.0",
        status=AgentStatus.ACTIVE.value,
        risk_tier=RiskTier.MODERATE.value,
        capabilities=(AgentCapability.TRANSLATION.value,),
        supported_inputs=("segments", "glossary"),
        supported_outputs=("translated_segments", "quality_flags", "review_state"),
        required_controls=("approved_model", "policy_admission", "language_validation"),
    ),
    AgentDefinition(
        id="specialized-anomaly",
        type=AgentType.ANOMALY.value,
        name="Deterministic anomaly detection",
        version="1.0.0",
        status=AgentStatus.ACTIVE.value,
        risk_tier=RiskTier.MODERATE.value,
        capabilities=(AgentCapability.STATISTICAL_DETECTION.value,),
        supported_inputs=("observations", "detector_configuration"),
        supported_outputs=("anomaly_results", "quality_state"),
        required_controls=("approved_model", "policy_admission", "deterministic_configuration"),
    ),
    AgentDefinition(
        id="specialized-insight",
        type=AgentType.INSIGHT.value,
        name="Cited insight generation",
        version="1.0.0",
        status=AgentStatus.ACTIVE.value,
        risk_tier=RiskTier.HIGH.value,
        capabilities=(AgentCapability.INSIGHT_GENERATION.value,),
        supported_inputs=("structured_metrics", "knowledge_sources"),
        supported_outputs=("insight_items", "citations", "review_state"),
        required_controls=("approved_model", "policy_admission", "source_citations"),
    ),
    AgentDefinition(
        id="specialized-forecasting",
        type=AgentType.FORECASTING.value,
        name="Decision intelligence forecasting",
        version="1.0.0",
        status=AgentStatus.ACTIVE.value,
        risk_tier=RiskTier.HIGH.value,
        capabilities=(AgentCapability.FORECASTING.value,),
        supported_inputs=("usage_series", "forecast_method", "horizon"),
        supported_outputs=("projections", "uncertainty", "data_quality"),
        required_controls=("approved_model", "policy_admission", "uncertainty_disclosure"),
    ),
)

_LEGAL_DEFINITIONS = (
    AgentDefinition("legal-intake-flow", "intake_flow", "Legal intake triage", "1.0.0", AgentStatus.ACTIVE.value, RiskTier.HIGH.value, ("triage", "matter_classification"), ("intake_request", "source_references"), ("triage", "missing_fields", "review_state"), ("approved_model", "policy_admission", "human_review")),
    AgentDefinition("legal-virtual-paralegal", "virtual_paralegal", "Virtual paralegal assistance", "1.0.0", AgentStatus.ACTIVE.value, RiskTier.HIGH.value, ("source_bound_assistance",), ("question", "knowledge_sources", "validated_documents"), ("facts", "interpretations", "citations", "review_state"), ("approved_model", "policy_admission", "source_citations", "human_review")),
    AgentDefinition("legal-billing-guard", "billing_guard", "Pre-bill billing checks", "1.0.0", AgentStatus.ACTIVE.value, RiskTier.HIGH.value, ("billing_validation",), ("billing_lines", "configured_rules"), ("billing_findings", "review_state"), ("policy_admission", "configured_controls", "human_review")),
    AgentDefinition("legal-billing-ops", "billing_ops", "Governed billing operations", "1.0.0", AgentStatus.ACTIVE.value, RiskTier.HIGH.value, ("billing_action_preparation",), ("validated_billing_package", "connector_action"), ("action_readiness", "review_state"), ("policy_admission", "connector_authorization", "human_approval")),
    AgentDefinition("legal-ocg-compliance", "ocg_compliance", "Configured OCG compliance rules", "1.0.0", AgentStatus.ACTIVE.value, RiskTier.HIGH.value, ("configured_rule_validation",), ("items", "configured_controls", "source_references"), ("findings", "evidence", "review_state"), ("policy_admission", "source_citations", "human_review")),
    AgentDefinition("legal-metrics-insights", "metrics_insights", "Legal operational metrics", "1.0.0", AgentStatus.ACTIVE.value, RiskTier.MODERATE.value, ("persisted_metrics",), ("persisted_metrics_query",), ("metrics", "data_quality"), ("policy_admission", "persisted_sources", "evidence")),
)

_COMPLIANCE_DEFINITIONS = (
    AgentDefinition("specialized-qms-compliance", AgentType.QMS_COMPLIANCE.value, "QMS configured-control evaluation", "1.0.0", AgentStatus.ACTIVE.value, RiskTier.HIGH.value, ("configured_qms_controls",), ("framework_id", "subject", "observations", "source_references"), ("findings", "evidence", "review_state"), ("approved_model", "policy_admission", "configured_controls", "human_review")),
    AgentDefinition("specialized-healthcare", AgentType.HEALTHCARE.value, "Healthcare operational controls", "1.0.0", AgentStatus.ACTIVE.value, RiskTier.HIGH.value, ("configured_healthcare_control_checks",), ("framework_id", "subject", "observations", "source_references"), ("findings", "evidence", "review_state"), ("approved_model", "policy_admission", "configured_controls", "human_review", "no_clinical_diagnosis")),
    AgentDefinition("specialized-manufacturing", AgentType.MANUFACTURING.value, "Manufacturing operational controls", "1.0.0", AgentStatus.ACTIVE.value, RiskTier.HIGH.value, ("configured_manufacturing_control_checks",), ("framework_id", "subject", "observations", "source_references"), ("findings", "evidence", "review_state"), ("approved_model", "policy_admission", "configured_controls", "human_review")),
    AgentDefinition("specialized-retail", AgentType.RETAIL.value, "Retail operational controls", "1.0.0", AgentStatus.ACTIVE.value, RiskTier.HIGH.value, ("configured_retail_control_checks",), ("framework_id", "subject", "observations", "source_references"), ("findings", "evidence", "review_state"), ("approved_model", "policy_admission", "configured_controls", "human_review")),
)

_RETIRED_DEFINITIONS = (
    AgentDefinition(
        id="specialized-education-retired",
        type=AgentType.EDUCATION.value,
        name="Education agent (retired)",
        version="1.0.0",
        status=AgentStatus.RETIRED.value,
        risk_tier=RiskTier.HIGH.value,
        capabilities=(),
        supported_inputs=(),
        supported_outputs=(),
        required_controls=("retired_no_execution",),
    ),
)

_ALL_DEFINITIONS = _DEFINITIONS + _LEGAL_DEFINITIONS + _COMPLIANCE_DEFINITIONS + _RETIRED_DEFINITIONS
_REGISTRY = {definition.type: definition for definition in _ALL_DEFINITIONS}


def definitions() -> tuple[AgentDefinition, ...]:
    return _ALL_DEFINITIONS


def list_agents() -> list[AgentDefinition]:
    return list(_ALL_DEFINITIONS)


def resolve_agent(agent_type: str) -> AgentDefinition:
    try:
        definition = _REGISTRY[agent_type]
    except KeyError as exc:
        raise UnknownAgentError(agent_type) from exc
    if definition.status != AgentStatus.ACTIVE.value:
        raise UnknownAgentError(agent_type)
    return definition


def validate_capabilities(agent_type: str, inputs: Iterable[str]) -> AgentDefinition:
    definition = resolve_agent(agent_type)
    unsupported = sorted(set(inputs) - set(definition.supported_inputs))
    if unsupported:
        from .exceptions import UnsupportedFeatureError

        raise UnsupportedFeatureError(
            f"Agent {agent_type} does not support inputs: {', '.join(unsupported)}"
        )
    return definition
