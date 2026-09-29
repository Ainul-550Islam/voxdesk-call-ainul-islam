from __future__ import annotations
import datetime as dt, uuid
from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped,mapped_column
from app.db.models import Base

def now(): return dt.datetime.now(dt.timezone.utc)

class DeploymentTarget(Base):
 __tablename__="deployment_targets"
 __table_args__=(Index("ix_deployment_target_scope_status","tenant_id","organization_id","environment_id","status"),UniqueConstraint("tenant_id","environment_id","idempotency_key",name="uq_deployment_target_idempotency"))
 id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),primary_key=True,default=uuid.uuid4)
 tenant_id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey("tenants.id",ondelete="CASCADE"),nullable=False,index=True)
 organization_id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey("organizations.id",ondelete="CASCADE"),nullable=False,index=True)
 environment_id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey("environments.id",ondelete="RESTRICT"),nullable=False,index=True)
 idempotency_key:Mapped[str]=mapped_column(String(200),nullable=False)
 target_type:Mapped[str]=mapped_column(String(32),nullable=False)
 provider:Mapped[str|None]=mapped_column(String(120))
 region:Mapped[str|None]=mapped_column(String(120))
 cluster_reference:Mapped[str|None]=mapped_column(String(500))
 network_mode:Mapped[str]=mapped_column(String(32),nullable=False)
 data_residency_intent:Mapped[dict]=mapped_column(JSON,nullable=False,default=dict)
 governance_requirements:Mapped[dict]=mapped_column(JSON,nullable=False,default=dict)
 prerequisites:Mapped[dict]=mapped_column(JSON,nullable=False,default=dict)
 status:Mapped[str]=mapped_column(String(24),nullable=False,default="draft")
 created_at:Mapped[dt.datetime]=mapped_column(DateTime(timezone=True),default=now,nullable=False)
 updated_at:Mapped[dt.datetime]=mapped_column(DateTime(timezone=True),default=now,onupdate=now,nullable=False)

class DeploymentRevision(Base):
 __tablename__="deployment_revisions"
 __table_args__=(UniqueConstraint("target_id","revision_number",name="uq_deployment_revision_number"),UniqueConstraint("target_id","manifest_fingerprint",name="uq_deployment_revision_manifest"),Index("ix_deployment_revision_scope_state","tenant_id","organization_id","environment_id","state"))
 id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),primary_key=True,default=uuid.uuid4)
 target_id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey("deployment_targets.id",ondelete="CASCADE"),nullable=False,index=True)
 tenant_id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey("tenants.id",ondelete="CASCADE"),nullable=False,index=True)
 organization_id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey("organizations.id",ondelete="CASCADE"),nullable=False,index=True)
 environment_id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey("environments.id",ondelete="RESTRICT"),nullable=False,index=True)
 revision_number:Mapped[int]=mapped_column(nullable=False)
 state:Mapped[str]=mapped_column(String(24),nullable=False,default="draft")
 artifact_reference:Mapped[str]=mapped_column(String(1000),nullable=False)
 artifact_digest:Mapped[str]=mapped_column(String(128),nullable=False)
 configuration_fingerprint:Mapped[str]=mapped_column(String(64),nullable=False)
 migration_revision:Mapped[str]=mapped_column(String(100),nullable=False)
 runtime_version:Mapped[str]=mapped_column(String(100),nullable=False)
 manifest_fingerprint:Mapped[str]=mapped_column(String(64),nullable=False)
 verification_state:Mapped[str]=mapped_column(String(32),nullable=False,default="not_verified")
 policy_decision_id:Mapped[uuid.UUID|None]=mapped_column(UUID(as_uuid=True),ForeignKey("governance_policy_decisions.id",ondelete="SET NULL"))
 evidence_root:Mapped[str|None]=mapped_column(String(64))
 created_at:Mapped[dt.datetime]=mapped_column(DateTime(timezone=True),default=now,nullable=False)

class DeploymentArtifact(Base):
 __tablename__="deployment_artifacts"
 __table_args__=(UniqueConstraint("revision_id","artifact_digest",name="uq_deployment_artifact_digest"),Index("ix_deployment_artifact_scope","tenant_id","organization_id","environment_id"))
 id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),primary_key=True,default=uuid.uuid4)
 revision_id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey("deployment_revisions.id",ondelete="CASCADE"),nullable=False,index=True)
 tenant_id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey("tenants.id",ondelete="CASCADE"),nullable=False,index=True);organization_id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey("organizations.id",ondelete="CASCADE"),nullable=False,index=True);environment_id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey("environments.id",ondelete="RESTRICT"),nullable=False,index=True)
 artifact_reference:Mapped[str]=mapped_column(String(1000),nullable=False);artifact_digest:Mapped[str]=mapped_column(String(128),nullable=False);manifest_fingerprint:Mapped[str]=mapped_column(String(64),nullable=False);metadata_json:Mapped[dict]=mapped_column("metadata",JSON,nullable=False,default=dict);created_at:Mapped[dt.datetime]=mapped_column(DateTime(timezone=True),default=now,nullable=False)

class DeploymentReadiness(Base):
 __tablename__="deployment_readiness"
 id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),primary_key=True,default=uuid.uuid4)
 target_id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey("deployment_targets.id",ondelete="CASCADE"),nullable=False,index=True)
 tenant_id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey("tenants.id",ondelete="CASCADE"),nullable=False,index=True);organization_id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey("organizations.id",ondelete="CASCADE"),nullable=False,index=True);environment_id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey("environments.id",ondelete="RESTRICT"),nullable=False,index=True)
 readiness:Mapped[str]=mapped_column(String(24),nullable=False);checks:Mapped[dict]=mapped_column(JSON,nullable=False);created_at:Mapped[dt.datetime]=mapped_column(DateTime(timezone=True),default=now,nullable=False)

class DeploymentVerification(Base):
 __tablename__="deployment_verifications"
 id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),primary_key=True,default=uuid.uuid4)
 revision_id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey("deployment_revisions.id",ondelete="CASCADE"),nullable=False,index=True)
 tenant_id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey("tenants.id",ondelete="CASCADE"),nullable=False,index=True);organization_id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey("organizations.id",ondelete="CASCADE"),nullable=False,index=True);environment_id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey("environments.id",ondelete="RESTRICT"),nullable=False,index=True)
 state:Mapped[str]=mapped_column(String(32),nullable=False);authoritative_verifier:Mapped[str|None]=mapped_column(String(200));evidence_reference:Mapped[str|None]=mapped_column(String(1000));observed_fingerprint:Mapped[str|None]=mapped_column(String(128));created_at:Mapped[dt.datetime]=mapped_column(DateTime(timezone=True),default=now,nullable=False)

class DeploymentRuntimeObservation(Base):
 __tablename__="deployment_runtime_observations"
 __table_args__=(Index("ix_runtime_observation_scope_time","tenant_id","organization_id","environment_id","created_at"),)
 id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),primary_key=True,default=uuid.uuid4)
 target_id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey("deployment_targets.id",ondelete="CASCADE"),nullable=False,index=True)
 revision_id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey("deployment_revisions.id",ondelete="CASCADE"),nullable=False,index=True)
 tenant_id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey("tenants.id",ondelete="CASCADE"),nullable=False,index=True)
 organization_id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey("organizations.id",ondelete="CASCADE"),nullable=False,index=True)
 environment_id:Mapped[uuid.UUID]=mapped_column(UUID(as_uuid=True),ForeignKey("environments.id",ondelete="RESTRICT"),nullable=False,index=True)
 adapter:Mapped[str]=mapped_column(String(80),nullable=False)
 adapter_version:Mapped[str]=mapped_column(String(80),nullable=False,default="unknown")
 observed_at:Mapped[dt.datetime]=mapped_column(DateTime(timezone=True),nullable=False,default=now)
 observed_revision:Mapped[str|None]=mapped_column(String(200))
 observed_artifact:Mapped[str|None]=mapped_column(String(1000))
 health_state:Mapped[str]=mapped_column(String(32),nullable=False,default="not_verified")
 observed:Mapped[bool]=mapped_column(nullable=False)
 deployed:Mapped[bool]=mapped_column(nullable=False)
 verified:Mapped[bool]=mapped_column(nullable=False)
 state:Mapped[str]=mapped_column(String(32),nullable=False)
 observed_digest:Mapped[str|None]=mapped_column(String(128))
 observed_fingerprint:Mapped[str|None]=mapped_column(String(128))
 checks:Mapped[dict]=mapped_column(JSON,nullable=False,default=dict)
 evidence:Mapped[dict]=mapped_column(JSON,nullable=False,default=dict)
 reason:Mapped[str|None]=mapped_column(String(300))
 created_at:Mapped[dt.datetime]=mapped_column(DateTime(timezone=True),default=now,nullable=False)
