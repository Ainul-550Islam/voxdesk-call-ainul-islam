"""Deterministic prompt rollout.

The bucket is a hash of tenant, prompt key and salt. The same request sees
the same version. Rollback points at an existing published version; it does
not rewrite that version's body.
"""

from __future__ import annotations

import hashlib
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.models import AIPrompt, AIPromptRollout, AIPromptVersion
from app.ai.prompts.versioning import require_version
from app.tenancy.isolation import LifecycleDenied, ValidationFailed


def bucket(tenant_id: uuid.UUID, prompt_key: str, salt: str) -> int:
    digest = hashlib.sha256(f"{tenant_id}:{prompt_key}:{salt}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % 100


def choose(tenant_id: uuid.UUID, prompt_key: str, rollout: AIPromptRollout) -> int:
    if rollout.canary_version is None or rollout.percent <= 0:
        return rollout.stable_version
    if rollout.percent >= 100:
        return rollout.canary_version
    slot = bucket(tenant_id, prompt_key, rollout.salt)
    if slot < rollout.percent:
        return rollout.canary_version
    return rollout.stable_version


async def configure(
    session: AsyncSession,
    prompt: AIPrompt,
    *,
    stable_version: int,
    canary_version: int | None,
    percent: int,
    salt: str,
    environment_id: uuid.UUID | None = None,
) -> AIPromptRollout:
    if not 0 <= percent <= 100:
        raise ValidationFailed("Rollout percent must be between 0 and 100")
    if not salt or len(salt) > 64:
        raise ValidationFailed("Rollout salt is required")
    stable = await require_version(session, prompt, stable_version)
    if stable.status != "published":
        raise LifecycleDenied("Rollout stable version must already be published")
    if canary_version is not None:
        canary = await require_version(session, prompt, canary_version)
        if canary.status != "published":
            raise LifecycleDenied("Rollout canary version must already be published")
    scope = "" if environment_id is None else str(environment_id)
    row = await session.scalar(
        select(AIPromptRollout).where(
            AIPromptRollout.tenant_id == prompt.tenant_id,
            AIPromptRollout.prompt_id == prompt.id,
            AIPromptRollout.environment_scope == scope,
        )
    )
    if row is None:
        row = AIPromptRollout(
            tenant_id=prompt.tenant_id,
            prompt_id=prompt.id,
            environment_scope=scope,
            stable_version=stable_version,
            canary_version=canary_version,
            percent=percent,
            salt=salt,
        )
        session.add(row)
    else:
        row.stable_version = stable_version
        row.canary_version = canary_version
        row.percent = percent
        row.salt = salt
    await session.flush()
    return row


async def version_for_runtime(
    session: AsyncSession,
    prompt: AIPrompt,
    *,
    environment_kind: str,
    environment_id: uuid.UUID | None = None,
) -> AIPromptVersion | None:
    """The rollout's version, refused when production would run an unpublished body."""
    from app.ai.prompts.versioning import assert_runnable

    version = await assigned_version(session, prompt, environment_id=environment_id)
    if version is None:
        return None
    assert_runnable(version, environment_kind=environment_kind)
    return version


async def assigned_version(
    session: AsyncSession,
    prompt: AIPrompt,
    *,
    environment_id: uuid.UUID | None = None,
) -> AIPromptVersion | None:
    scope = "" if environment_id is None else str(environment_id)
    rollout = await session.scalar(
        select(AIPromptRollout).where(
            AIPromptRollout.tenant_id == prompt.tenant_id,
            AIPromptRollout.prompt_id == prompt.id,
            AIPromptRollout.environment_scope == scope,
        )
    )
    number = prompt.current_version
    if rollout is not None:
        number = choose(prompt.tenant_id, prompt.prompt_key, rollout)
    if number is None:
        return None
    return await require_version(session, prompt, number)
