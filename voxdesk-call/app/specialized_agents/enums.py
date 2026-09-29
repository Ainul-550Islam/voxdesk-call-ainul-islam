"""Closed vocabularies for governed specialized-agent execution."""

from __future__ import annotations

import enum


class AgentType(str, enum.Enum):
    LEGAL = "legal"
    TRANSLATION = "translation"
    ANOMALY = "anomaly"
    INSIGHT = "insight"
    FORECASTING = "forecasting"
    INTAKE_FLOW = "intake_flow"
    VIRTUAL_PARALEGAL = "virtual_paralegal"
    BILLING_GUARD = "billing_guard"
    BILLING_OPS = "billing_ops"
    OCG_COMPLIANCE = "ocg_compliance"
    QMS_COMPLIANCE = "qms_compliance"
    HEALTHCARE = "healthcare"
    MANUFACTURING = "manufacturing"
    RETAIL = "retail"
    EDUCATION = "education"
    METRICS_INSIGHTS = "metrics_insights"


class AgentStatus(str, enum.Enum):
    REGISTERED = "registered"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    RETIRED = "retired"


class ExecutionStatus(str, enum.Enum):
    PENDING = "pending"
    ADMITTED = "admitted"
    RUNNING = "running"
    REVIEW_REQUIRED = "review_required"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DENIED = "denied"
    CANCELLED = "cancelled"


class EvidenceStatus(str, enum.Enum):
    PENDING = "pending"
    RECORDED = "recorded"
    FAILED = "failed"


class Severity(str, enum.Enum):
    INFO = "info"
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"


class Confidence(str, enum.Enum):
    VERY_LOW = "very_low"
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    VERY_HIGH = "very_high"


class ReviewState(str, enum.Enum):
    NOT_REQUIRED = "not_required"
    REQUIRED = "required"
    PENDING = "pending"
    COMPLETED = "completed"
    APPROVED = "approved"
    REJECTED = "rejected"


class QualityState(str, enum.Enum):
    VERIFIED = "verified"
    DEGRADED = "degraded"
    INSUFFICIENT_DATA = "insufficient_data"
    NOT_AVAILABLE = "not_available"


class RiskTier(str, enum.Enum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"


class LegalIssueType(str, enum.Enum):
    DETECTED_LANGUAGE = "detected_language"
    MISSING_LANGUAGE = "missing_language"
    AMBIGUOUS_LANGUAGE = "ambiguous_language"


class AgentCapability(str, enum.Enum):
    DOCUMENT_ANALYSIS = "document_analysis"
    TRANSLATION = "translation"
    STATISTICAL_DETECTION = "statistical_detection"
    INSIGHT_GENERATION = "insight_generation"
    FORECASTING = "forecasting"


HIGH_RISK_TIERS = frozenset({RiskTier.HIGH.value, RiskTier.CRITICAL.value})
