from __future__ import annotations
from typing import Any
import uuid
from pydantic import BaseModel, ConfigDict, Field

TARGET_TYPES={"saas","byoc","private_cloud","hybrid","on_prem","air_gapped"}
LIFECYCLE={"draft","validated","approved","queued","deploying","deployed","degraded","failed","suspended","retired"}
class TargetInput(BaseModel):
    model_config=ConfigDict(extra="forbid")
    environment_id: uuid.UUID
    idempotency_key: str = Field(min_length=8, max_length=200)
    target_type: str
    provider: str|None=None
    region: str|None=None
    cluster_reference: str|None=None
    network_mode: str="restricted"
    data_residency_intent: dict[str,Any]=Field(default_factory=dict)
    governance_requirements: dict[str,Any]=Field(default_factory=dict)
class RevisionInput(BaseModel):
    model_config=ConfigDict(extra="forbid")
    artifact_reference: str
    artifact_digest: str
    configuration: dict[str,Any]
    migration_revision: str
    runtime_version: str
class VerificationInput(BaseModel):
    model_config=ConfigDict(extra="forbid")
    state: str
    authoritative_verifier: str|None=None
    evidence_reference: str|None=None
    observed_fingerprint: str|None=None
