"""Tenant-aware asynchronous persistence boundary for governance rows."""

from __future__ import annotations

import uuid
from typing import TypeVar

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from .context import GovernanceScope
from .models import (
    GovernancePolicy,
    GovernancePolicyDecision,
    LineageRecord,
    ModelRegistry,
    ModelVersion,
    RiskAssessment,
)

T = TypeVar("T")


class GovernanceRepository:
    """Small explicit query boundary; callers cannot omit tenant scope accidentally."""

    def __init__(self, session: AsyncSession, scope: GovernanceScope):
        self.session = session
        self.scope = scope

    def _scope_statement(self, statement: Select) -> Select:
        return statement.where(
            getattr(statement.column_descriptions[0]["entity"], "tenant_id") == self.scope.tenant_id,
            getattr(statement.column_descriptions[0]["entity"], "organization_id")
            == self.scope.organization_id,
        )

    async def get_policy(self, policy_id: uuid.UUID, *, for_update: bool = False) -> GovernancePolicy | None:
        statement = select(GovernancePolicy).where(
            GovernancePolicy.id == policy_id,
            GovernancePolicy.tenant_id == self.scope.tenant_id,
            GovernancePolicy.organization_id == self.scope.organization_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return await self.session.scalar(statement)

    async def get_risk(self, assessment_id: uuid.UUID, *, for_update: bool = False) -> RiskAssessment | None:
        statement = select(RiskAssessment).where(
            RiskAssessment.id == assessment_id,
            RiskAssessment.tenant_id == self.scope.tenant_id,
            RiskAssessment.organization_id == self.scope.organization_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return await self.session.scalar(statement)

    async def get_registry(self, registry_id: uuid.UUID, *, for_update: bool = False) -> ModelRegistry | None:
        statement = select(ModelRegistry).where(
            ModelRegistry.id == registry_id,
            ModelRegistry.tenant_id == self.scope.tenant_id,
            ModelRegistry.organization_id == self.scope.organization_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return await self.session.scalar(statement)

    async def get_version(self, version_id: uuid.UUID, *, for_update: bool = False) -> ModelVersion | None:
        statement = select(ModelVersion).where(
            ModelVersion.id == version_id,
            ModelVersion.tenant_id == self.scope.tenant_id,
            ModelVersion.organization_id == self.scope.organization_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return await self.session.scalar(statement)

    async def list_decisions(self, *, limit: int = 100) -> list[GovernancePolicyDecision]:
        result = await self.session.execute(
            select(GovernancePolicyDecision)
            .where(
                GovernancePolicyDecision.tenant_id == self.scope.tenant_id,
                GovernancePolicyDecision.organization_id == self.scope.organization_id,
            )
            .order_by(GovernancePolicyDecision.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars())

    async def list_lineage(self, *, trace_id: str | None = None, limit: int = 100) -> list[LineageRecord]:
        statement = select(LineageRecord).where(
            LineageRecord.tenant_id == self.scope.tenant_id,
            LineageRecord.organization_id == self.scope.organization_id,
        )
        if trace_id:
            statement = statement.where(LineageRecord.trace_id == trace_id)
        result = await self.session.execute(
            statement.order_by(LineageRecord.created_at.asc()).limit(limit)
        )
        return list(result.scalars())
