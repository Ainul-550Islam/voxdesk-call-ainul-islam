"""Typed, evidence-limited response models for the final parity dashboard.

These models deliberately distinguish registered implementation evidence from
runtime verification. A route appearing in the inventory is not represented as
an E2E pass, provider connection, certification, or production-readiness claim.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


FeatureStatus = Literal[
    "MISSING",
    "PARTIAL",
    "IMPLEMENTED",
    "VERIFIED",
    "PRODUCTION_READY",
    "NOT_CONFIGURED",
    "RESOURCE_LIMITED",
]

ConnectionStatus = Literal[
    "CONNECTED",
    "NOT_CONFIGURED",
    "AUTH_REQUIRED",
    "ERROR",
    "DISABLED",
    "SANDBOX",
    "UNVERIFIED",
]

InspectionStepStatus = Literal[
    "PASS",
    "FAIL",
    "PARTIAL",
    "NOT_RUN",
    "NOT_CONFIGURED",
    "RESOURCE_LIMITED",
]


class StrictResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RouteEvidence(StrictResponse):
    method: str
    path: str
    module: str


class CapabilityItem(StrictResponse):
    key: str
    label: str
    status: FeatureStatus
    summary: str
    evidence_routes: list[RouteEvidence] = Field(default_factory=list)
    evidence_basis: Literal["registered_routes_only"] = "registered_routes_only"


class CapabilityInventoryResponse(StrictResponse):
    generated_at: datetime
    registered_api_operations: int = Field(ge=0)
    suppressed_generated_placeholder_routes: int = Field(ge=0)
    suppressed_generic_placeholder_routes: int = Field(ge=0)
    capabilities: list[CapabilityItem] = Field(default_factory=list)
    limitation: str


class IntegrationStatusItem(StrictResponse):
    integration_type: str
    provider: str
    status: ConnectionStatus
    configured: bool
    enabled: bool | None = None
    credentials_present: bool = False
    last_health_check_at: datetime | None = None
    last_health_ok: bool | None = None
    status_basis: str


class IntegrationInventoryResponse(StrictResponse):
    tenant_id: UUID
    generated_at: datetime
    items: list[IntegrationStatusItem] = Field(default_factory=list)
    limitation: str


class E2EInspectionResponse(StrictResponse):
    tenant_id: UUID
    environment_id: UUID | None = None
    agent_id: UUID
    agent_name: str
    agent_status: str
    published_version_number: int | None = None
    published_version_id: UUID | None = None
    call_id: UUID | None = None
    call_status: str | None = None
    call_agent_version_number: int | None = None
    call_agent_version_id: UUID | None = None
    is_simulation: bool | None = None
    overall_status: InspectionStepStatus
    steps: list["E2EInspectionStep"] = Field(default_factory=list)
    side_effects_performed: Literal[False] = False
    limitation: str


class E2EInspectionStep(StrictResponse):
    key: str
    label: str
    status: InspectionStepStatus
    detail: str


E2EInspectionResponse.model_rebuild()
