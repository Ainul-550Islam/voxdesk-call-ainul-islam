"""Prompt registry.

``agent.system`` remains the code prompt built by ``app.agent.prompts``. A
tenant override is a separate row. Publishing it does not rewrite the code
prompt, and the voice pipeline is not switched over in this batch.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.prompts import build_system_prompt
from app.ai.models import AIPrompt, AIPromptVersion
from app.ai.prompts.versioning import add_version, checksum
from app.db.models import Tenant
from app.tenancy.isolation import NotFound, ValidationFailed

CODE_PROMPT = "agent.system"
_KEY = __import__("re").compile(r"^[a-z0-9][a-z0-9._-]{1,78}$")


def clean_key(value: str) -> str:
    key = (value or "").strip().lower()
    if not _KEY.match(key):
        raise ValidationFailed("Prompt key must be 2-79 lowercase letters, digits, dots or hyphens")
    return key


def environment_scope(environment_id: uuid.UUID | None) -> str:
    return "" if environment_id is None else str(environment_id)


async def get_prompt(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    prompt_key: str,
    *,
    environment_id: uuid.UUID | None = None,
) -> AIPrompt | None:
    return await session.scalar(
        select(AIPrompt).where(
            AIPrompt.tenant_id == tenant_id,
            AIPrompt.prompt_key == prompt_key,
            AIPrompt.environment_scope == environment_scope(environment_id),
        )
    )


async def require_prompt(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    prompt_key: str,
    *,
    environment_id: uuid.UUID | None = None,
) -> AIPrompt:
    row = await get_prompt(session, tenant_id, prompt_key, environment_id=environment_id)
    if row is None:
        raise NotFound()
    return row


async def list_prompts(session: AsyncSession, tenant_id: uuid.UUID) -> list[AIPrompt]:
    rows = await session.scalars(
        select(AIPrompt).where(AIPrompt.tenant_id == tenant_id).order_by(AIPrompt.prompt_key)
    )
    return list(rows)


async def create_draft(
    session: AsyncSession,
    tenant: Tenant,
    *,
    prompt_key: str,
    body: str,
    author_user_id: uuid.UUID | None,
    environment_id: uuid.UUID | None = None,
) -> tuple[AIPrompt, AIPromptVersion]:
    key = clean_key(prompt_key)
    existing = await get_prompt(session, tenant.id, key, environment_id=environment_id)
    if existing is None:
        existing = AIPrompt(
            tenant_id=tenant.id,
            environment_scope=environment_scope(environment_id),
            prompt_key=key,
            status="active",
            owner_user_id=author_user_id,
        )
        session.add(existing)
        await session.flush()
    version = await add_version(session, existing, body, author_user_id=author_user_id)
    return existing, version


def _code_runtime_view(provider: str) -> dict:
    """The code prompt stays the live body until a published override exists."""
    return {
        "prompt_key": CODE_PROMPT,
        "source": "app.agent.prompts",
        "status": "published",
        "approval_state": "published",
        "version": 0,
        "body": None,
        "rollout_state": "code",
        "provider": provider,
        "mutable": False,
    }


async def resolve_for_runtime(
    session: AsyncSession,
    tenant: Tenant,
    *,
    environment_kind: str,
    environment_id: uuid.UUID | None = None,
    provider: str = "openai",
) -> dict:
    """Published override, or the code prompt. A draft is not the production body."""
    from app.ai.prompts.rollout import version_for_runtime

    row = await get_prompt(session, tenant.id, CODE_PROMPT, environment_id=environment_id)
    if row is None or row.current_version is None:
        return _code_runtime_view(provider)
    version = await version_for_runtime(
        session, row, environment_kind=environment_kind, environment_id=environment_id
    )
    if version is None:
        return _code_runtime_view(provider)
    return {
        "prompt_key": row.prompt_key,
        "source": "registry",
        "status": version.status,
        "approval_state": version.status,
        "version": version.version_number,
        "checksum": version.checksum,
        "body": version.body,
        "rollout_state": "rollout" if row.current_version != version.version_number else "current",
        "mutable": False,
    }


def code_prompt_view(tenant: Tenant) -> dict:
    body = build_system_prompt(tenant)
    return {
        "prompt_key": CODE_PROMPT,
        "source": "app.agent.prompts",
        "status": "published",
        "version": 0,
        "checksum": checksum(body),
        "mutable": False,
        "overrides_voice_pipeline": False,
    }
