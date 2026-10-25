"""Canonical grounded knowledge retrieval for agent-assist generation."""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center_v2.settings import settings
from app.knowledge.context import build_context, build_sources
from app.knowledge.retrieval import RetrievedChunk, retrieve_with_timeout


@dataclass(frozen=True)
class GroundedKnowledge:
    """Tenant-scoped chunks plus the exact context/citations sent to a provider."""

    chunks: list[RetrievedChunk]
    context: str
    sources: list[dict]


async def retrieve_grounded_knowledge(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    query: str,
) -> GroundedKnowledge | None:
    """Return only canonical, tenant/environment-scoped final KB evidence."""
    chunks = await retrieve_with_timeout(
        session,
        tenant_id=tenant_id,
        environment_id=environment_id,
        query=query[:4000],
        top_k=settings.assist_top_k,
        timeout=settings.assist_retrieval_timeout_seconds,
    )
    if not chunks:
        return None
    return GroundedKnowledge(
        chunks=list(chunks),
        context=build_context(chunks, max_chars=settings.assist_max_context_chars),
        sources=build_sources(chunks),
    )


__all__ = ["GroundedKnowledge", "retrieve_grounded_knowledge"]
