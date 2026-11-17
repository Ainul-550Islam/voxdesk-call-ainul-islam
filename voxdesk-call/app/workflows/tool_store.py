"""The tool store: install, enable, version and review (PART 14, P14-04).

Installing an integration is a two-part decision and the store keeps them
separate on purpose:

1. **what the tenant enabled** -- `allowed_actions`, an explicit list;
2. **who approved the permissions** -- `permission_review`, recording the scopes
   that were shown and the user who accepted them.

An install cannot be enabled while the review is missing or was made against a
different action list. That is the whole defence against "it was on by default"
in an incident review.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.channel_models import WorkflowInstall
from app.workflows import catalog


class ToolStoreError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _now() -> datetime:
    return datetime.now(timezone.utc)


def review_payload(key: str, actions: Iterable[str], *, reviewed_by: str) -> dict[str, Any]:
    """The record written when a tenant accepts a permission set."""
    scopes, unknown = catalog.permissions_for(key, actions)
    if unknown:
        raise ToolStoreError(
            "unknown_action", f"{key} has no action(s) {', '.join(unknown)}"
        )
    return {
        "scopes": scopes,
        "review_text": catalog.review_text(key, actions),
        "reviewed_by": reviewed_by,
        "reviewed_at": _now().isoformat(),
    }


async def _load(
    session: AsyncSession, tenant_id: uuid.UUID, key: str
) -> WorkflowInstall | None:
    return (
        await session.execute(
            select(WorkflowInstall).where(
                WorkflowInstall.tenant_id == tenant_id, WorkflowInstall.workflow_key == key
            )
        )
    ).scalar_one_or_none()


async def install(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    key: str,
    *,
    actions: Iterable[str],
    config: Mapping[str, Any] | None = None,
    reviewed_by: str,
    enabled: bool = False,
) -> WorkflowInstall:
    """Install or re-configure an integration, with its permission review."""
    integration = catalog.get(key)
    if not integration.available:
        raise ToolStoreError(
            "not_available", integration.reason or f"{integration.name} is not available"
        )
    action_list = sorted(set(actions))
    if not action_list:
        raise ToolStoreError("no_actions", "an install needs at least one enabled action")

    review = review_payload(key, action_list, reviewed_by=reviewed_by)
    row = await _load(session, tenant_id, key)
    if row is None:
        row = WorkflowInstall(
            tenant_id=tenant_id,
            workflow_key=key,
            version=1,
            installed_by=reviewed_by,
        )
        session.add(row)
    elif row.allowed_actions and sorted(row.allowed_actions) != action_list:
        # Re-scoping is a new review, not an edit: bump so the dashboard can show
        # which version the tenant actually approved.
        row.version = int(row.version) + 1
    row.allowed_actions = action_list
    row.config = dict(config or {})
    row.permission_review = review
    row.enabled = bool(enabled)
    await session.flush()
    return row


async def set_enabled(
    session: AsyncSession, tenant_id: uuid.UUID, key: str, enabled: bool
) -> WorkflowInstall:
    row = await _load(session, tenant_id, key)
    if row is None:
        raise ToolStoreError("not_installed", f"{key} is not installed for this tenant")
    if enabled and not row.permission_review.get("reviewed_by"):
        # Never enable something nobody signed off on.
        raise ToolStoreError("review_missing", "the permission review has not been signed off")
    row.enabled = bool(enabled)
    await session.flush()
    return row


async def uninstall(session: AsyncSession, tenant_id: uuid.UUID, key: str) -> bool:
    row = await _load(session, tenant_id, key)
    if row is None:
        return False
    await session.delete(row)
    return True


async def list_installs(session: AsyncSession, tenant_id: uuid.UUID) -> list[WorkflowInstall]:
    return list(
        (
            await session.execute(
                select(WorkflowInstall)
                .where(WorkflowInstall.tenant_id == tenant_id)
                .order_by(WorkflowInstall.workflow_key)
            )
        )
        .scalars()
        .all()
    )


def view(row: WorkflowInstall) -> dict[str, Any]:
    """The row as the API and the library page read it."""
    try:
        integration = catalog.get(row.workflow_key)
        current_version = 1 if integration else None
        needs_review = integration.scopes_for(row.allowed_actions) != list(
            (row.permission_review or {}).get("scopes", [])
        )
    except catalog.CatalogError:
        integration = None
        current_version = None
        needs_review = True
    return {
        "workflow_key": row.workflow_key,
        "name": integration.name if integration else row.workflow_key,
        "version": row.version,
        "catalog_version": current_version,
        "enabled": row.enabled,
        "allowed_actions": list(row.allowed_actions or []),
        "config": dict(row.config or {}),
        "permission_review": dict(row.permission_review or {}),
        "needs_review": needs_review,
        "installed_by": row.installed_by,
    }


async def enabled_installs(
    session: AsyncSession, tenant_id: uuid.UUID, *, action: str | None = None
) -> list[WorkflowInstall]:
    """Installs the executor may use, optionally filtered by action name."""
    rows = [row for row in await list_installs(session, tenant_id) if row.enabled]
    if action is not None:
        rows = [row for row in rows if action in (row.allowed_actions or [])]
    return rows
