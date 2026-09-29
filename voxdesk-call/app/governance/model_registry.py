"""Model registry lifecycle and runtime admission helpers."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .context import GovernanceScope
from .enums import ModelRegistryStatus, ModelVersionStatus
from .exceptions import GovernanceNotFound, PolicyDenied, VerificationRequired
from .models import ModelRegistry, ModelVersion
from .service import (
    add_model_version,
    approve_model_version,
    create_registry,
    list_registries,
    record_model_evaluation,
    transition_model_version,
    transition_registry,
)


async def approved_models(session: AsyncSession, scope: GovernanceScope) -> list[ModelVersion]:
    result = await session.execute(
        select(ModelVersion)
        .join(ModelRegistry, ModelRegistry.id == ModelVersion.registry_id)
        .where(
            ModelVersion.tenant_id == scope.tenant_id,
            ModelVersion.organization_id == scope.organization_id,
            ModelVersion.status == ModelVersionStatus.APPROVED.value,
            ModelRegistry.status == ModelRegistryStatus.ACTIVE.value,
        )
        .order_by(ModelVersion.created_at.desc())
    )
    return list(result.scalars())


async def admit_model_version(
    session: AsyncSession,
    scope: GovernanceScope,
    version_id: uuid.UUID,
    *,
    channel: str | None = None,
) -> ModelVersion:
    row = await session.scalar(
        select(ModelVersion)
        .join(ModelRegistry, ModelRegistry.id == ModelVersion.registry_id)
        .where(
            ModelVersion.id == version_id,
            ModelVersion.tenant_id == scope.tenant_id,
            ModelVersion.organization_id == scope.organization_id,
        )
    )
    if row is None:
        raise GovernanceNotFound()
    registry = await session.get(ModelRegistry, row.registry_id)
    if registry is None:
        raise GovernanceNotFound()
    if row.status != ModelVersionStatus.APPROVED.value:
        raise VerificationRequired("Only an approved model version may be admitted")
    if registry.status != ModelRegistryStatus.ACTIVE.value:
        raise PolicyDenied("Model registry is not active")
    if channel and registry.approved_for_channels and channel not in registry.approved_for_channels:
        raise PolicyDenied("Model is not approved for this channel")
    if scope.environment_id and registry.approved_for_environments:
        environment = str(scope.environment_id)
        if environment not in registry.approved_for_environments:
            raise PolicyDenied("Model is not approved for this environment")
    return row


__all__ = [
    "add_model_version",
    "admit_model_version",
    "approve_model_version",
    "approved_models",
    "create_registry",
    "list_registries",
    "record_model_evaluation",
    "transition_model_version",
    "transition_registry",
]
