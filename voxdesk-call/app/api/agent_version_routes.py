"""Agent version diff and environment promotion API routes (`/api/agents`).

Backed by durable PostgreSQL ``Agent`` and ``AgentVersion`` records via
``app.services.agent_service``. Eliminates all process-local deployment
dictionaries.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.core.errors import BadRequestError, NotFoundError
from app.db.session import get_session as get_db
from app.services import agent_service
from app.services.agent_version_merge_service import (
    MergeError,
    apply_resolutions,
    merge_three_way,
)

router = APIRouter(prefix="/api/agents", tags=["agent-versions"])


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PromotePayload(_StrictModel):
    from_env: Literal["draft", "staging"] = "draft"
    to_env: Literal["staging", "production"] = "staging"
    changelog: str = Field(default="", max_length=500)


class FieldDiffOut(BaseModel):
    field_path: str
    change_type: Literal["added", "removed", "modified", "unchanged"]
    old_value: Optional[Any] = None
    new_value: Optional[Any] = None


class VersionDiffOut(BaseModel):
    agent_id: str
    from_version: int
    to_version: int
    total_changes: int
    diffs: list[FieldDiffOut]


def _compute_dict_diff(
    d1: dict[str, Any], d2: dict[str, Any], prefix: str = ""
) -> list[FieldDiffOut]:
    diffs: list[FieldDiffOut] = []
    all_keys = sorted(set(d1.keys()) | set(d2.keys()))
    for k in all_keys:
        path = f"{prefix}.{k}" if prefix else k
        if k not in d1:
            diffs.append(
                FieldDiffOut(
                    field_path=path,
                    change_type="added",
                    old_value=None,
                    new_value=d2[k],
                )
            )
        elif k not in d2:
            diffs.append(
                FieldDiffOut(
                    field_path=path,
                    change_type="removed",
                    old_value=d1[k],
                    new_value=None,
                )
            )
        elif isinstance(d1[k], dict) and isinstance(d2[k], dict):
            diffs.extend(_compute_dict_diff(d1[k], d2[k], path))
        elif d1[k] != d2[k]:
            diffs.append(
                FieldDiffOut(
                    field_path=path,
                    change_type="modified",
                    old_value=d1[k],
                    new_value=d2[k],
                )
            )
    return diffs


@router.get("/{agent_id}/versions/diff", response_model=VersionDiffOut)
async def diff_agent_versions(
    agent_id: str,
    v1: int = Query(..., ge=1),
    v2: int = Query(..., ge=1),
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_db),
) -> VersionDiffOut:
    from_cfg = await agent_service.get_version_config_async(
        session, ctx.tenant, agent_id, v1
    )
    to_cfg = await agent_service.get_version_config_async(
        session, ctx.tenant, agent_id, v2
    )
    if from_cfg is None or to_cfg is None:
        raise NotFoundError(f"Version {v1} or {v2} not found for agent {agent_id}")

    diffs = _compute_dict_diff(from_cfg, to_cfg)
    return VersionDiffOut(
        agent_id=agent_id,
        from_version=v1,
        to_version=v2,
        total_changes=len(diffs),
        diffs=diffs,
    )


@router.post("/{agent_id}/promote")
async def promote_agent_environment(
    agent_id: str,
    payload: PromotePayload,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    if payload.from_env == "draft" and payload.to_env not in ("staging", "production"):
        raise BadRequestError("Draft can only be promoted to staging or production")
    if payload.from_env == "staging" and payload.to_env != "production":
        raise BadRequestError("Staging can only be promoted to production")

    row = await agent_service.get_agent_row(
        session, ctx.tenant, agent_id, for_update=True
    )
    if row is None:
        default_cfg = agent_service.from_tenant(ctx.tenant)
        if default_cfg.id == agent_id:
            row = await agent_service.create_draft_async(
                session, ctx.tenant, default_cfg, actor=ctx.user_id
            )
        else:
            raise NotFoundError("Agent not found")

    draft_cfg = await agent_service.get_draft_async(session, ctx.tenant, row.id)
    now_iso = datetime.now(timezone.utc).isoformat()
    meta = dict(row.meta or {})
    env_map = dict(
        meta.get("environment_deployments")
        or {
            "draft": {"version": 0, "updated_at": now_iso},
            "staging": {"version": None, "updated_at": None},
            "production": {"version": None, "updated_at": None},
        }
    )

    if payload.from_env == "staging":
        staging_ver = (env_map.get("staging") or {}).get("version")
        if staging_ver is None:
            raise BadRequestError(
                "No configuration deployed in staging to promote to production"
            )

    try:
        changelog_note = (
            payload.changelog
            if payload.to_env == "production"
            else f"[{payload.to_env}] {payload.changelog}".strip()
        )
        new_version = await agent_service.publish_async(
            session,
            ctx.tenant,
            draft_cfg,
            agent_identifier=row.id,
            actor=ctx.user_id,
            changelog=changelog_note,
            environment=payload.to_env,
        )
        env_map[payload.to_env] = {
            "version": new_version.version,
            "updated_at": now_iso,
            "promoted_by": str(ctx.user_id),
            "changelog": payload.changelog,
        }
        meta["environment_deployments"] = env_map
        row.meta = meta
        await session.commit()
    except ValueError as exc:
        await session.rollback()
        raise BadRequestError(str(exc)) from exc

    return {
        "agent_id": agent_id,
        "from_env": payload.from_env,
        "to_env": payload.to_env,
        "version": new_version.version,
        "promoted_at": now_iso,
        "environments": env_map,
    }


@router.get("/{agent_id}/environments")
async def get_agent_environments(
    agent_id: str,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    row = await agent_service.get_agent_row(session, ctx.tenant, agent_id)
    history = await agent_service.version_history_async(session, ctx.tenant, agent_id)
    if row is None and not history:
        default_cfg = agent_service.from_tenant(ctx.tenant)
        if default_cfg.id != agent_id:
            raise NotFoundError("Agent not found")
        now_iso = datetime.now(timezone.utc).isoformat()
        return {
            "agent_id": agent_id,
            "environments": {
                "draft": {
                    "version": 1,
                    "updated_at": now_iso,
                    "has_unpublished_changes": False,
                },
                "staging": {"version": None, "updated_at": None},
                "production": {"version": 1, "updated_at": now_iso},
            },
        }

    latest_ver = history[0].version if history else None
    latest_at = history[0].published_at.isoformat() if history else None
    draft_cfg = await agent_service.get_draft_async(session, ctx.tenant, agent_id)
    now_iso = (
        row.updated_at.isoformat()
        if row and row.updated_at
        else datetime.now(timezone.utc).isoformat()
    )

    meta = dict((row.meta if row else None) or {})
    env_map = dict(
        meta.get("environment_deployments")
        or {
            "draft": {
                "version": (latest_ver or 0) + (1 if draft_cfg else 0),
                "updated_at": now_iso,
                "has_unpublished_changes": draft_cfg is not None,
            },
            "staging": {"version": None, "updated_at": None},
            "production": {"version": latest_ver, "updated_at": latest_at},
        }
    )
    if env_map.get("production", {}).get("version") is None and latest_ver is not None:
        env_map["production"] = {"version": latest_ver, "updated_at": latest_at}

    return {
        "agent_id": agent_id,
        "environments": env_map,
    }


# --------------------------------------------------------------- three-way merge


class MergeVersionsPayload(_StrictModel):
    """A merge preview: three version numbers, and optionally the human's choices.

    ``strategy="manual"`` (the default) leaves every conflict unresolved in the
    merged document, which is what the dashboard asks for: it shows the conflict
    list and lets a person pick a side. ``resolutions`` is the second call, once
    those picks exist.
    """

    base_version: int = Field(ge=1)
    ours_version: int = Field(ge=1)
    theirs_version: int = Field(ge=1)
    strategy: Literal["ours", "theirs", "manual"] = "manual"
    resolutions: dict[str, Literal["ours", "theirs"]] = Field(default_factory=dict)

    @field_validator("resolutions")
    @classmethod
    def _bounded(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 500:
            raise ValueError("at most 500 conflict resolutions per request")
        return value


@router.post("/{agent_id}/versions/merge", response_model=dict)
async def merge_agent_versions(
    agent_id: str,
    payload: MergeVersionsPayload,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Three-way merge of three published snapshots (PART 12, P12-21).

    The merge itself lives in ``app.services.agent_version_merge_service`` and is
    pure: this route only loads the three snapshots and reports what came back.
    Nothing is persisted — a merge is a *proposal*, and the conflict list is the
    point. ``clean`` is true only when there is nothing left unresolved.
    """
    snapshots: dict[str, dict[str, Any]] = {}
    for label, number in (
        ("base", payload.base_version),
        ("ours", payload.ours_version),
        ("theirs", payload.theirs_version),
    ):
        config = await agent_service.get_version_config_async(
            session, ctx.tenant, agent_id, number
        )
        if config is None:
            raise NotFoundError(
                f"version {number} not found for agent {agent_id} ({label} side)"
            )
        snapshots[label] = config

    try:
        result = merge_three_way(
            snapshots["base"],
            snapshots["ours"],
            snapshots["theirs"],
            strategy=payload.strategy,
        )
    except MergeError as exc:
        raise BadRequestError(str(exc)) from exc

    resolved = apply_resolutions(result, payload.resolutions)
    body: dict[str, Any] = {
        "agent_id": agent_id,
        "base_version": payload.base_version,
        "ours_version": payload.ours_version,
        "theirs_version": payload.theirs_version,
        "strategy": payload.strategy,
        "resolutions_applied": {
            path: choice for path, choice in payload.resolutions.items()
        },
    }
    body.update(resolved.as_dict())
    return body
