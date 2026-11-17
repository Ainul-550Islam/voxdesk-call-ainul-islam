# File: app/api/role_routes.py — tenant-defined roles, grants and the effective-permission preview
"""Custom role API (PART 16, P16-01).

Four things a tenant admin needs, and one thing they must not be able to do:

    GET    /api/auth/permission-catalogue          the permission vocabulary by module
    GET    /api/auth/custom-roles                  roles in this tenant
    POST   /api/auth/custom-roles                  create
    PATCH  /api/auth/custom-roles/{id}             rename, retune, deactivate
    DELETE /api/auth/custom-roles/{id}             remove (grants cascade)
    POST   /api/auth/custom-roles/{id}/assignments grant to a subject
    DELETE /api/auth/custom-roles/{id}/assignments/{assignment_id}
    GET    /api/auth/effective-permissions?user_id= what a subject actually holds

The thing they must not be able to do is **escalate**. Every write runs
`custom_roles.assert_grantable`, which refuses a permission the actor does not
hold and refuses platform-only permissions outright. The check is on the server
and it is on the *role definition* as well as on the grant, because an admin who
cannot write a powerful role cannot then assign it to themselves.

A role id from another tenant is a 404, never a 403 — a 403 confirms the row
exists. Every write invalidates the grant cache so the next request sees the new
permissions rather than waiting out the TTL.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import custom_roles as policy
from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.auth.rbac import effective_permissions, permissions_for
from app.db.models import User
from app.db.role_models import SUBJECT_TYPES, CustomRole, CustomRoleAssignment
from app.db.session import get_session
from app.tenancy.context import reject_client_override

router = APIRouter(prefix="/api/auth", tags=["custom-roles"])

MAX_ROLES_PER_TENANT = 50
MAX_PERMISSIONS_PER_ROLE = 200
MAX_ASSIGNMENTS_PER_ROLE = 500


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RoleCreate(_Strict):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    permissions: list[str] = Field(default_factory=list, max_length=MAX_PERMISSIONS_PER_ROLE)
    is_active: bool = True
    tenant_id: uuid.UUID | None = None


class RolePatch(_Strict):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    permissions: list[str] | None = Field(default=None, max_length=MAX_PERMISSIONS_PER_ROLE)
    is_active: bool | None = None


class AssignmentCreate(_Strict):
    subject_type: str = Field(pattern="^(user|service_account|scim_group)$")
    subject_id: str = Field(min_length=1, max_length=120)


def _role_dict(role: CustomRole, *, assignment_count: int | None = None) -> dict[str, Any]:
    ids = role.permission_ids()
    return {
        "id": str(role.id),
        "name": role.name,
        "description": role.description,
        "permissions": sorted(ids),
        "modules": {module: count for module, count in sorted(policy.permission_owner_modules(ids).items())},
        "is_active": bool(role.is_active),
        "assignment_count": assignment_count,
        "created_at": role.created_at.isoformat() if role.created_at else None,
        "updated_at": role.updated_at.isoformat() if role.updated_at else None,
    }


async def _load_role(session: AsyncSession, tenant_id: uuid.UUID, role_id: uuid.UUID) -> CustomRole:
    role = await session.get(CustomRole, role_id)
    if role is None or role.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "role not found"})
    return role


def _actor_permissions(ctx: TenantContext) -> frozenset[Permission]:
    """What the caller holds, custom roles included — the escalation baseline."""
    return effective_permissions(ctx.role, ctx.custom_permissions)


@router.get("/permission-catalogue")
async def permission_catalogue(
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
) -> dict[str, Any]:
    """The vocabulary the editor is built from, grouped by module.

    `grantable` marks what this caller may bundle into a role, so the UI disables
    the rest instead of offering a permission that the server will refuse.
    """
    held = _actor_permissions(ctx)
    modules = []
    for module in policy.catalogue():
        ids = list(module["permissions"])  # type: ignore[arg-type]
        modules.append(
            {
                **module,
                "grantable": sorted(
                    pid
                    for pid in ids
                    if Permission(pid) in held and not policy.is_platform_permission(Permission(pid))
                ),
            }
        )
    return {
        "modules": modules,
        "total_permissions": len(list(Permission)),
        "ungrouped": policy.ungrouped_permissions(),
    }


@router.get("/custom-roles")
async def list_custom_roles(
    include_inactive: bool = Query(default=True),
    ctx: TenantContext = Depends(require_permission(Permission.USER_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    query = select(CustomRole).where(CustomRole.tenant_id == ctx.tenant_id)
    if not include_inactive:
        query = query.where(CustomRole.is_active.is_(True))
    rows = (await session.scalars(query.order_by(CustomRole.name))).all()
    counts = dict(
        (
            await session.execute(
                select(CustomRoleAssignment.role_id, func.count())
                .where(CustomRoleAssignment.tenant_id == ctx.tenant_id)
                .group_by(CustomRoleAssignment.role_id)
            )
        ).all()
    )
    return {
        "roles": [_role_dict(row, assignment_count=int(counts.get(row.id, 0))) for row in rows],
        "subject_types": list(SUBJECT_TYPES),
        "max_roles": MAX_ROLES_PER_TENANT,
    }


@router.post("/custom-roles", status_code=status.HTTP_201_CREATED)
async def create_custom_role(
    payload: RoleCreate,
    ctx: TenantContext = Depends(require_permission(Permission.USER_ROLE_CHANGE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    reject_client_override(ctx, payload.tenant_id)
    try:
        granted = policy.assert_grantable(_actor_permissions(ctx), payload.permissions)
    except policy.CustomRoleError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "escalation_refused", "message": str(exc)},
        ) from exc

    existing = await session.scalar(
        select(CustomRole).where(
            CustomRole.tenant_id == ctx.tenant_id, CustomRole.name == payload.name.strip()
        )
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "name_taken", "message": f"a role named {payload.name!r} exists"},
        )
    count = await session.scalar(
        select(func.count()).select_from(CustomRole).where(CustomRole.tenant_id == ctx.tenant_id)
    )
    if int(count or 0) >= MAX_ROLES_PER_TENANT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "role_limit",
                "message": f"at most {MAX_ROLES_PER_TENANT} custom roles per tenant",
            },
        )

    role = CustomRole(
        tenant_id=ctx.tenant_id,
        name=payload.name.strip(),
        description=payload.description.strip(),
        permissions=sorted(p.value for p in granted),
        is_active=payload.is_active,
        created_by_user_id=ctx.user_id,
    )
    session.add(role)
    await session.commit()
    await session.refresh(role)
    policy.invalidate_cache(ctx.tenant_id)
    return _role_dict(role, assignment_count=0)


@router.patch("/custom-roles/{role_id}")
async def patch_custom_role(
    role_id: uuid.UUID,
    payload: RolePatch,
    ctx: TenantContext = Depends(require_permission(Permission.USER_ROLE_CHANGE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    role = await _load_role(session, ctx.tenant_id, role_id)
    changes = payload.model_dump(exclude_unset=True)

    if changes.get("permissions") is not None:
        try:
            granted = policy.assert_grantable(_actor_permissions(ctx), changes["permissions"])
        except policy.CustomRoleError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "escalation_refused", "message": str(exc)},
            ) from exc
        role.permissions = sorted(p.value for p in granted)

    if changes.get("name") is not None:
        new_name = str(changes["name"]).strip()
        clash = await session.scalar(
            select(CustomRole).where(
                CustomRole.tenant_id == ctx.tenant_id,
                CustomRole.name == new_name,
                CustomRole.id != role.id,
            )
        )
        if clash is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "name_taken", "message": f"a role named {new_name!r} exists"},
            )
        role.name = new_name

    if changes.get("description") is not None:
        role.description = str(changes["description"]).strip()
    if changes.get("is_active") is not None:
        role.is_active = bool(changes["is_active"])

    await session.commit()
    await session.refresh(role)
    policy.invalidate_cache(ctx.tenant_id)
    return _role_dict(role)


@router.delete("/custom-roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_custom_role(
    role_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.USER_ROLE_CHANGE)),
    session: AsyncSession = Depends(get_session),
):
    role = await _load_role(session, ctx.tenant_id, role_id)
    await session.delete(role)
    await session.commit()
    policy.invalidate_cache(ctx.tenant_id)
    return None


@router.get("/custom-roles/{role_id}/assignments")
async def list_assignments(
    role_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.USER_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    role = await _load_role(session, ctx.tenant_id, role_id)
    rows = (
        await session.scalars(
            select(CustomRoleAssignment)
            .where(
                CustomRoleAssignment.tenant_id == ctx.tenant_id,
                CustomRoleAssignment.role_id == role.id,
            )
            .order_by(CustomRoleAssignment.created_at)
        )
    ).all()
    return {"role_id": str(role.id), "assignments": [row.as_dict() for row in rows]}


@router.post("/custom-roles/{role_id}/assignments", status_code=status.HTTP_201_CREATED)
async def create_assignment(
    role_id: uuid.UUID,
    payload: AssignmentCreate,
    ctx: TenantContext = Depends(require_permission(Permission.USER_ROLE_CHANGE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    role = await _load_role(session, ctx.tenant_id, role_id)

    # Assigning is also a grant: the actor must hold every permission the role
    # carries, or an admin could hand out a role they could not have written.
    try:
        policy.assert_grantable(_actor_permissions(ctx), role.permission_ids())
    except policy.CustomRoleError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "escalation_refused", "message": str(exc)},
        ) from exc

    await _check_subject_exists(session, ctx.tenant_id, payload.subject_type, payload.subject_id)

    existing = await session.scalar(
        select(CustomRoleAssignment).where(
            CustomRoleAssignment.tenant_id == ctx.tenant_id,
            CustomRoleAssignment.role_id == role.id,
            CustomRoleAssignment.subject_type == payload.subject_type,
            CustomRoleAssignment.subject_id == payload.subject_id,
        )
    )
    if existing is not None:
        # Idempotent rather than an error: "make sure this is granted" is what a
        # script means, and the unique key already makes the second row impossible.
        return existing.as_dict()

    total = await session.scalar(
        select(func.count())
        .select_from(CustomRoleAssignment)
        .where(CustomRoleAssignment.role_id == role.id)
    )
    if int(total or 0) >= MAX_ASSIGNMENTS_PER_ROLE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "assignment_limit",
                "message": f"at most {MAX_ASSIGNMENTS_PER_ROLE} assignments per role",
            },
        )

    assignment = CustomRoleAssignment(
        tenant_id=ctx.tenant_id,
        role_id=role.id,
        subject_type=payload.subject_type,
        subject_id=payload.subject_id,
        assigned_by_user_id=ctx.user_id,
    )
    session.add(assignment)
    await session.commit()
    await session.refresh(assignment)
    policy.invalidate_cache(ctx.tenant_id)
    return assignment.as_dict()


@router.delete(
    "/custom-roles/{role_id}/assignments/{assignment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_assignment(
    role_id: uuid.UUID,
    assignment_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.USER_ROLE_CHANGE)),
    session: AsyncSession = Depends(get_session),
):
    role = await _load_role(session, ctx.tenant_id, role_id)
    assignment = await session.get(CustomRoleAssignment, assignment_id)
    if (
        assignment is None
        or assignment.tenant_id != ctx.tenant_id
        or assignment.role_id != role.id
    ):
        raise HTTPException(
            status_code=404, detail={"code": "not_found", "message": "assignment not found"}
        )
    await session.delete(assignment)
    await session.commit()
    policy.invalidate_cache(ctx.tenant_id)
    return None


@router.get("/effective-permissions")
async def effective_permission_preview(
    user_id: uuid.UUID = Query(...),
    ctx: TenantContext = Depends(require_permission(Permission.USER_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """What this person actually holds: built-in role ∪ custom roles.

    Reported as two lists plus a union, because "why can they do that?" is the
    question an auditor asks, and a single flat list cannot answer it.
    """
    user = await session.get(User, user_id)
    if user is None or user.tenant_id != ctx.tenant_id:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "user not found"})

    held, granted = await policy.grants_for_user(session, ctx.tenant_id, user_id=user.id)
    builtin = sorted(p.value for p in permissions_for(user.role))
    extra = sorted({p.value for p in granted})
    return {
        "user_id": str(user.id),
        "email": user.email,
        "builtin_role": user.role.value,
        "builtin_permissions": builtin,
        "custom_roles": policy.summarise(held),
        "custom_permissions": extra,
        "effective_permissions": sorted(set(builtin) | set(extra)),
    }


async def _check_subject_exists(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    subject_type: str,
    subject_id: str,
) -> None:
    """A grant to a subject that does not exist is a typo, and it should fail.

    Refusing here is what keeps `custom_role_assignments` honest: an assignment
    that points at nothing looks like a grant in the UI and grants nothing in
    practice, which is the worst of both.
    """
    from app.auth.identity.models import SCIMGroupMapping, ServiceAccount

    try:
        subject_uuid = uuid.UUID(subject_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "bad_subject", "message": f"{subject_type} id must be a uuid"},
        ) from exc

    if subject_type == "user":
        row = await session.get(User, subject_uuid)
        exists = row is not None and row.tenant_id == tenant_id
    elif subject_type == "service_account":
        row = await session.get(ServiceAccount, subject_uuid)
        exists = row is not None and row.tenant_id == tenant_id
    else:
        row = await session.get(SCIMGroupMapping, subject_uuid)
        exists = row is not None and row.tenant_id == tenant_id

    if not exists:
        raise HTTPException(
            status_code=404,
            detail={"code": "subject_not_found", "message": f"{subject_type} not found"},
        )
