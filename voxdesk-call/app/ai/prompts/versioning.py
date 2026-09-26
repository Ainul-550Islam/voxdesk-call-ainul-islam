"""Immutable prompt versions.

A published body is never edited. A new draft is a new row. Checksums are
sha256 of the stored body; a mismatch fails closed.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.models import AIPrompt, AIPromptVersion
from app.tenancy.isolation import Conflict, LifecycleDenied, NotFound, ValidationFailed

MAX_BODY = 16000


def checksum(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _clean_body(body: str) -> str:
    text = body if isinstance(body, str) else ""
    if not text.strip():
        raise ValidationFailed("Prompt body is empty")
    if len(text) > MAX_BODY:
        raise ValidationFailed("Prompt body is too large")
    if "\x00" in text:
        raise ValidationFailed("Prompt body is malformed")
    return text


async def add_version(
    session: AsyncSession,
    prompt: AIPrompt,
    body: str,
    *,
    author_user_id: uuid.UUID | None,
) -> AIPromptVersion:
    if prompt.status == "retired":
        raise LifecycleDenied("A retired prompt cannot grow a version")
    cleaned = _clean_body(body)
    current = await session.scalar(
        select(func.max(AIPromptVersion.version_number)).where(
            AIPromptVersion.prompt_id == prompt.id
        )
    )
    number = int(current or 0) + 1
    row = AIPromptVersion(
        prompt_id=prompt.id,
        tenant_id=prompt.tenant_id,
        version_number=number,
        body=cleaned,
        checksum=checksum(cleaned),
        status="draft",
        author_user_id=author_user_id,
    )
    session.add(row)
    await session.flush()
    return row


async def require_version(
    session: AsyncSession, prompt: AIPrompt, version_number: int
) -> AIPromptVersion:
    row = await session.scalar(
        select(AIPromptVersion).where(
            AIPromptVersion.prompt_id == prompt.id,
            AIPromptVersion.version_number == version_number,
            AIPromptVersion.tenant_id == prompt.tenant_id,
        )
    )
    if row is None:
        raise NotFound()
    if checksum(row.body) != row.checksum:
        raise Conflict("Prompt checksum does not match the stored body")
    return row


async def publish(
    session: AsyncSession, version: AIPromptVersion, prompt: AIPrompt
) -> AIPromptVersion:
    if version.status == "retired" or prompt.status == "retired":
        raise LifecycleDenied("A retired prompt version cannot be published")
    if checksum(version.body) != version.checksum:
        raise Conflict("Prompt checksum does not match the stored body")
    if version.status != "published":
        version.status = "published"
        version.published_at = datetime.utcnow()
    prompt.current_version = version.version_number
    prompt.updated_at = datetime.utcnow()
    await session.flush()
    return version


async def retire_version(session: AsyncSession, version: AIPromptVersion) -> AIPromptVersion:
    if version.status == "retired":
        return version
    version.status = "retired"
    await session.flush()
    return version


def assert_runnable(version: AIPromptVersion, *, environment_kind: str) -> None:
    """Production may run a published body only. A checksum mismatch fails closed."""
    if checksum(version.body) != version.checksum:
        raise Conflict("Prompt checksum does not match the stored body")
    if environment_kind == "production" and version.status != "published":
        raise LifecycleDenied("Unpublished prompt cannot run in production")


def refuse_mutation(version: AIPromptVersion) -> None:
    if version.status != "draft":
        raise LifecycleDenied("A published prompt version cannot be edited")
