"""Tenant-scoped A/B experiment management.

This module exposes persisted draft experiment/variant management only. It does
not select an agent version for a call, collect experiment metrics, promote a
variant, or roll back a published agent. Those operations fail closed until an
immutable AgentVersion-to-call assignment is persisted and integrated with the
inbound/outbound call paths.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.db.enterprise_models import Experiment, ExperimentStatus, ExperimentVariant
from app.db.models import Agent
from app.db.session import get_session

router = APIRouter(prefix="/api/experiments", tags=["ab-testing"])


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VariantCreate(_Strict):
    name: str = Field(min_length=1, max_length=200)
    weight: int = Field(ge=0, le=100)
    config: dict[str, Any] = Field(default_factory=dict)
    prompt: str = Field(default="", max_length=20_000)
    is_control: bool = False


class ExperimentCreate(_Strict):
    agent_id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2_000)
    variants: list[VariantCreate] = Field(min_length=2, max_length=10)


class ExperimentUpdate(_Strict):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2_000)


class VariantUpdate(_Strict):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    weight: int | None = Field(default=None, ge=0, le=100)
    config: dict[str, Any] | None = None
    prompt: str | None = Field(default=None, max_length=20_000)
    is_control: bool | None = None


class ExperimentOut(_Strict):
    id: str
    tenant_id: str
    agent_id: str
    name: str
    description: str
    status: str
    traffic_split: dict[str, int]
    winner_variant_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    variants: list[dict[str, Any]] | None = None


class VariantOut(_Strict):
    id: str
    experiment_id: str
    name: str
    weight: int
    is_control: bool
    metrics: dict[str, Any]
    created_at: str | None = None


class PromoteRequest(_Strict):
    variant_id: uuid.UUID
    reason: str | None = Field(default=None, max_length=500)


class AssignmentOut(_Strict):
    experiment_id: str
    variant_id: str
    variant_name: str
    is_control: bool
    config: dict[str, Any]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _not_configured(capability: str) -> None:
    raise HTTPException(
        status_code=501,
        detail={
            "code": "AB_TESTING_CAPABILITY_UNAVAILABLE",
            "capability": capability,
            "message": (
                "A/B experiment management is persisted, but this operation is not "
                "connected to immutable call-version assignment. No call, metric, "
                "promotion, or rollback side effect was performed."
            ),
        },
    )


def _validate_variant_weights(weights: list[int]) -> None:
    if len(weights) < 2 or len(weights) > 10:
        raise HTTPException(
            status_code=422,
            detail="an experiment requires between 2 and 10 variants",
        )
    if any(weight < 0 or weight > 100 for weight in weights):
        raise HTTPException(
            status_code=422,
            detail="variant weights must be between 0 and 100",
        )
    total = sum(weights)
    if total != 100:
        raise HTTPException(
            status_code=422,
            detail=f"variant weights must sum to 100, got {total}",
        )


def _validate_control_flags(control_flags: list[bool]) -> None:
    if len(control_flags) < 2 or len(control_flags) > 10:
        raise HTTPException(
            status_code=422,
            detail="an experiment requires between 2 and 10 variants",
        )
    if sum(control_flags) != 1:
        raise HTTPException(
            status_code=422,
            detail="exactly one variant must be the control",
        )


def _validate_variant_names(names: list[str]) -> list[str]:
    normalized = [name.strip() for name in names]
    if any(not name for name in normalized):
        raise HTTPException(status_code=422, detail="variant names cannot be blank")
    if len({name.casefold() for name in normalized}) != len(normalized):
        raise HTTPException(status_code=422, detail="variant names must be unique")
    return normalized


def _exp_out(
    experiment: Experiment,
    variants: list[ExperimentVariant] | None = None,
) -> ExperimentOut:
    data = experiment.as_dict()
    return ExperimentOut(
        id=data["id"],
        tenant_id=data["tenant_id"],
        agent_id=data["agent_id"],
        name=data["name"],
        description=data["description"],
        status=data["status"],
        traffic_split=data["traffic_split"],
        winner_variant_id=data["winner_variant_id"],
        created_at=data["created_at"],
        updated_at=data["updated_at"],
        variants=[variant.as_dict() for variant in variants] if variants is not None else None,
    )


def _variant_out(variant: ExperimentVariant) -> VariantOut:
    data = variant.as_dict()
    return VariantOut(
        id=data["id"],
        experiment_id=data["experiment_id"],
        name=data["name"],
        weight=data["weight"],
        is_control=data["is_control"],
        metrics=data["metrics"],
        created_at=data["created_at"],
    )


async def _get_experiment(
    session: AsyncSession,
    experiment_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> Experiment:
    experiment = await session.get(Experiment, experiment_id)
    if experiment is None or experiment.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="experiment not found")
    return experiment


async def _get_variants(
    session: AsyncSession,
    experiment_id: uuid.UUID,
    tenant_id: uuid.UUID,
) -> list[ExperimentVariant]:
    result = await session.execute(
        select(ExperimentVariant)
        .where(
            ExperimentVariant.experiment_id == experiment_id,
            ExperimentVariant.tenant_id == tenant_id,
        )
        .order_by(ExperimentVariant.created_at, ExperimentVariant.id)
    )
    return list(result.scalars().all())


@router.post("", response_model=ExperimentOut, status_code=201)
async def create_experiment(
    payload: ExperimentCreate,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> ExperimentOut:
    """Persist a tenant-owned draft experiment and its weighted variants."""
    try:
        agent_id = uuid.UUID(payload.agent_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="agent_id must identify a persisted agent") from exc

    agent = await session.scalar(
        select(Agent).where(
            Agent.id == agent_id,
            Agent.tenant_id == ctx.tenant_id,
        )
    )
    if agent is None:
        raise HTTPException(status_code=404, detail="agent not found")

    weights = [variant.weight for variant in payload.variants]
    controls = [variant.is_control for variant in payload.variants]
    names = _validate_variant_names([variant.name for variant in payload.variants])
    _validate_variant_weights(weights)
    _validate_control_flags(controls)

    experiment = Experiment(
        tenant_id=ctx.tenant_id,
        agent_id=str(agent.id),
        name=payload.name.strip(),
        description=payload.description,
        status=ExperimentStatus.DRAFT.value,
        traffic_split={},
        created_by=ctx.user_id,
    )
    session.add(experiment)
    await session.flush()

    variants = [
        ExperimentVariant(
            experiment_id=experiment.id,
            tenant_id=ctx.tenant_id,
            name=name,
            weight=variant.weight,
            config=variant.config,
            prompt=variant.prompt,
            is_control=variant.is_control,
            metrics={},
        )
        for name, variant in zip(names, payload.variants, strict=True)
    ]
    session.add_all(variants)
    await session.flush()
    experiment.traffic_split = {str(variant.id): variant.weight for variant in variants}
    experiment.updated_at = _now()
    await session.commit()
    await session.refresh(experiment)
    for variant in variants:
        await session.refresh(variant)
    return _exp_out(experiment, variants)


@router.get("", response_model=dict)
async def list_experiments(
    agent_id: str | None = Query(default=None),
    status: ExperimentStatus | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    filters = [Experiment.tenant_id == ctx.tenant_id]
    if agent_id is not None:
        try:
            normalized_agent_id = str(uuid.UUID(agent_id))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="agent_id must be a UUID") from exc
        filters.append(Experiment.agent_id == normalized_agent_id)
    if status is not None:
        filters.append(Experiment.status == status.value)

    total_result = await session.execute(
        select(func.count(Experiment.id)).where(*filters)
    )
    total = int(total_result.scalar() or 0)
    rows_result = await session.execute(
        select(Experiment)
        .where(*filters)
        .order_by(Experiment.created_at.desc(), Experiment.id)
        .offset(offset)
        .limit(limit)
    )
    experiments = list(rows_result.scalars().all())
    return {
        "experiments": [experiment.as_dict() for experiment in experiments],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/{experiment_id}", response_model=ExperimentOut)
async def get_experiment(
    experiment_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> ExperimentOut:
    experiment = await _get_experiment(session, experiment_id, ctx.tenant_id)
    variants = await _get_variants(session, experiment_id, ctx.tenant_id)
    return _exp_out(experiment, variants)


@router.patch("/{experiment_id}", response_model=ExperimentOut)
async def update_experiment(
    experiment_id: uuid.UUID,
    payload: ExperimentUpdate,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> ExperimentOut:
    experiment = await _get_experiment(session, experiment_id, ctx.tenant_id)
    if experiment.status != ExperimentStatus.DRAFT.value:
        raise HTTPException(status_code=409, detail="only draft experiment metadata can be updated")
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        variants = await _get_variants(session, experiment_id, ctx.tenant_id)
        return _exp_out(experiment, variants)
    if "name" in changes:
        experiment.name = changes["name"].strip()
    if "description" in changes:
        experiment.description = changes["description"]
    experiment.updated_at = _now()
    await session.commit()
    await session.refresh(experiment)
    variants = await _get_variants(session, experiment_id, ctx.tenant_id)
    return _exp_out(experiment, variants)


@router.get("/{experiment_id}/variants", response_model=dict)
async def list_variants(
    experiment_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await _get_experiment(session, experiment_id, ctx.tenant_id)
    variants = await _get_variants(session, experiment_id, ctx.tenant_id)
    return {"variants": [variant.as_dict() for variant in variants], "total": len(variants)}


@router.patch("/{experiment_id}/variants/{variant_id}", response_model=VariantOut)
async def update_variant(
    experiment_id: uuid.UUID,
    variant_id: uuid.UUID,
    payload: VariantUpdate,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> VariantOut:
    experiment = await _get_experiment(session, experiment_id, ctx.tenant_id)
    if experiment.status != ExperimentStatus.DRAFT.value:
        raise HTTPException(status_code=409, detail="variants can only be changed in draft")
    variants = await _get_variants(session, experiment_id, ctx.tenant_id)
    variant = next((item for item in variants if item.id == variant_id), None)
    if variant is None:
        raise HTTPException(status_code=404, detail="variant not found")

    changes = payload.model_dump(exclude_unset=True)
    prospective_weights = [
        changes.get("weight", item.weight) if item.id == variant_id else item.weight
        for item in variants
    ]
    prospective_controls = [
        changes.get("is_control", item.is_control) if item.id == variant_id else item.is_control
        for item in variants
    ]
    prospective_names = [
        changes.get("name", item.name) if item.id == variant_id else item.name
        for item in variants
    ]
    _validate_variant_weights(prospective_weights)
    _validate_control_flags(prospective_controls)
    normalized_names = _validate_variant_names(prospective_names)

    if "name" in changes:
        variant.name = normalized_names[variants.index(variant)]
    if "weight" in changes:
        variant.weight = changes["weight"]
    if "config" in changes:
        variant.config = changes["config"]
    if "prompt" in changes:
        variant.prompt = changes["prompt"]
    if "is_control" in changes:
        variant.is_control = changes["is_control"]
    experiment.traffic_split = {
        str(item.id): (changes["weight"] if item.id == variant_id else item.weight)
        for item in variants
    }
    experiment.updated_at = _now()
    await session.commit()
    await session.refresh(variant)
    return _variant_out(variant)


@router.post("/{experiment_id}/start", response_model=ExperimentOut)
async def start_experiment(
    experiment_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> ExperimentOut:
    _not_configured("live call traffic selection")


@router.post("/{experiment_id}/pause", response_model=ExperimentOut)
async def pause_experiment(
    experiment_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> ExperimentOut:
    _not_configured("live call traffic selection")


@router.get("/{experiment_id}/assignment", response_model=AssignmentOut)
async def get_assignment(
    experiment_id: uuid.UUID,
    call_id: uuid.UUID = Query(..., description="Call identifier for a persisted immutable version assignment"),
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> AssignmentOut:
    _not_configured("per-call immutable version assignment")


@router.get("/{experiment_id}/metrics", response_model=dict)
async def get_metrics(
    experiment_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    _not_configured("call-linked experiment metrics")


@router.post("/{experiment_id}/promote", response_model=ExperimentOut)
async def promote_variant(
    experiment_id: uuid.UUID,
    payload: PromoteRequest,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> ExperimentOut:
    _not_configured("approval-controlled immutable version promotion")


@router.post("/{experiment_id}/rollback", response_model=ExperimentOut)
async def rollback_experiment(
    experiment_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> ExperimentOut:
    _not_configured("approval-controlled immutable version rollback")
