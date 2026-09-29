from __future__ import annotations
import datetime as dt
import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.compliance.models import ComplianceFramework, ComplianceControl, ComplianceFinding, Remediation
from app.governance.context import GovernanceScope
from app.tenancy.isolation import BoundaryDenied, LifecycleDenied

async def get_framework(session: AsyncSession, scope: GovernanceScope, framework_id: uuid.UUID, *, lock: bool = False) -> ComplianceFramework:
    stmt = select(ComplianceFramework).where(ComplianceFramework.id == framework_id, ComplianceFramework.tenant_id == scope.tenant_id, ComplianceFramework.organization_id == scope.organization_id, ComplianceFramework.environment_id == scope.environment_id)
    if lock: stmt = stmt.with_for_update()
    row = await session.scalar(stmt)
    if row is None: raise BoundaryDenied()
    return row

async def list_frameworks(session: AsyncSession, scope: GovernanceScope, *, framework_type: str | None = None, limit: int = 100) -> list[ComplianceFramework]:
    stmt=select(ComplianceFramework).where(ComplianceFramework.tenant_id==scope.tenant_id,ComplianceFramework.organization_id==scope.organization_id,ComplianceFramework.environment_id==scope.environment_id)
    if framework_type: stmt=stmt.where(ComplianceFramework.framework_type==framework_type)
    return list((await session.scalars(stmt.order_by(ComplianceFramework.created_at.desc()).limit(max(1,min(limit,200))))).all())

async def controls_for(session: AsyncSession, framework_id: uuid.UUID, *, active_only: bool = True) -> list[ComplianceControl]:
    stmt=select(ComplianceControl).where(ComplianceControl.framework_id==framework_id)
    if active_only: stmt=stmt.where(ComplianceControl.active.is_(True))
    return list((await session.scalars(stmt.order_by(ComplianceControl.control_key))).all())

async def create_control(session: AsyncSession, framework: ComplianceFramework, *, values: dict) -> ComplianceControl:
    row=ComplianceControl(framework_id=framework.id,**values);session.add(row);await session.flush();return row

async def finding_for_update(session: AsyncSession, scope: GovernanceScope, finding_id: uuid.UUID) -> ComplianceFinding:
    stmt = select(ComplianceFinding).where(ComplianceFinding.id == finding_id, ComplianceFinding.tenant_id == scope.tenant_id, ComplianceFinding.organization_id == scope.organization_id, ComplianceFinding.environment_id == scope.environment_id).with_for_update()
    row = await session.scalar(stmt)
    if row is None: raise BoundaryDenied()
    return row

async def get_finding(session: AsyncSession, scope: GovernanceScope, finding_id: uuid.UUID) -> ComplianceFinding:
    stmt=select(ComplianceFinding).where(ComplianceFinding.id==finding_id,ComplianceFinding.tenant_id==scope.tenant_id,ComplianceFinding.organization_id==scope.organization_id,ComplianceFinding.environment_id==scope.environment_id)
    row=await session.scalar(stmt)
    if row is None:raise BoundaryDenied()
    return row

async def list_findings(session: AsyncSession, scope: GovernanceScope, *, status: str|None=None, framework_id: uuid.UUID|None=None, limit: int=100) -> list[ComplianceFinding]:
    stmt=select(ComplianceFinding).where(ComplianceFinding.tenant_id==scope.tenant_id,ComplianceFinding.organization_id==scope.organization_id,ComplianceFinding.environment_id==scope.environment_id)
    if status:stmt=stmt.where(ComplianceFinding.status==status)
    if framework_id:stmt=stmt.where(ComplianceFinding.framework_id==framework_id)
    return list((await session.scalars(stmt.order_by(ComplianceFinding.created_at.desc()).limit(max(1,min(limit,200))))).all())

async def remediation_for_update(session: AsyncSession, scope: GovernanceScope, remediation_id: uuid.UUID) -> Remediation:
    stmt = select(Remediation).where(Remediation.id == remediation_id, Remediation.tenant_id == scope.tenant_id, Remediation.organization_id == scope.organization_id, Remediation.environment_id == scope.environment_id).with_for_update()
    row = await session.scalar(stmt)
    if row is None: raise BoundaryDenied()
    return row

async def list_remediations(session: AsyncSession, scope: GovernanceScope, *, status: str|None=None, limit:int=100) -> list[Remediation]:
    stmt=select(Remediation).where(Remediation.tenant_id==scope.tenant_id,Remediation.organization_id==scope.organization_id,Remediation.environment_id==scope.environment_id)
    if status:stmt=stmt.where(Remediation.status==status)
    return list((await session.scalars(stmt.order_by(Remediation.created_at.desc()).limit(max(1,min(limit,200))))).all())

async def transition_remediation(session: AsyncSession, scope: GovernanceScope, remediation_id: uuid.UUID, target: str, *, owner_id: uuid.UUID|None=None, due_at: dt.datetime|None=None, resolution_reference: str|None=None) -> Remediation:
    row=await remediation_for_update(session,scope,remediation_id)
    allowed={"open":{"in_progress","cancelled"},"in_progress":{"open","resolved","cancelled"},"resolved":{"open"},"cancelled":set()}
    if target not in allowed.get(row.status,set()):raise LifecycleDenied("invalid remediation state transition")
    if target=="resolved" and not resolution_reference:raise LifecycleDenied("resolution reference is required")
    row.status=target
    if owner_id is not None:row.owner_id=owner_id
    if due_at is not None:row.due_at=due_at
    if target=="resolved":row.resolved_at=dt.datetime.now(dt.timezone.utc);row.resolution_reference=resolution_reference
    elif target=="open":row.resolved_at=None;row.resolution_reference=None
    await session.flush();return row
