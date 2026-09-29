from __future__ import annotations
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.compliance.models import ComplianceFinding, Remediation
from app.review.models import ReviewCase
from app.governance.context import GovernanceScope

async def operational_metrics(session: AsyncSession, scope: GovernanceScope) -> dict:
    if scope.environment_id is None: raise ValueError("environment scope required")
    filters=(ReviewCase.tenant_id==scope.tenant_id,ReviewCase.organization_id==scope.organization_id,ReviewCase.environment_id==scope.environment_id)
    total=int(await session.scalar(select(func.count()).select_from(ReviewCase).where(*filters)) or 0)
    open_reviews=int(await session.scalar(select(func.count()).select_from(ReviewCase).where(*filters,ReviewCase.status.in_(["pending","assigned","in_review"]))) or 0)
    open_case_rate={"value":open_reviews/total if total else None,"quality":"measured" if total else "NOT_AVAILABLE","reason":None if total else "no persisted review cases in scope"}
    finding_count=int(await session.scalar(select(func.count()).select_from(ComplianceFinding).where(ComplianceFinding.tenant_id==scope.tenant_id,ComplianceFinding.organization_id==scope.organization_id,ComplianceFinding.environment_id==scope.environment_id)) or 0)
    backlog=int(await session.scalar(select(func.count()).select_from(Remediation).where(Remediation.tenant_id==scope.tenant_id,Remediation.organization_id==scope.organization_id,Remediation.environment_id==scope.environment_id,Remediation.status.in_(["open","in_progress"]))) or 0)
    return {"source":"persisted review/compliance/remediation records","review_case_count":{"value":total,"quality":"measured"},"open_review_case_rate":open_case_rate,"compliance_findings":{"value":finding_count,"quality":"measured"},"remediation_backlog":{"value":backlog,"quality":"measured"},"review_turnaround":{"value":None,"quality":"NOT_AVAILABLE","reason":"review completion timestamps are not included in this aggregate"},"evidence_completeness":{"value":None,"quality":"NOT_AVAILABLE","reason":"no complete evidence denominator is defined"}}
