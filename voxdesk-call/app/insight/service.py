"""Cited, governed insight generation with tenant-scoped knowledge retrieval."""
from __future__ import annotations

import hashlib
import inspect
import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.llm_factory import api_key_for
from app.agent.text_agent import _complete_anthropic, _complete_openai, _openai_style_client
from app.governance.context import GovernanceScope
from app.governance.hashing import sha256_hex
from app.governance.models import ModelVersion
from app.db.models import KnowledgeChunk, KnowledgeDocument, SEARCHABLE_DOCUMENT_STATUSES
from app.knowledge.retrieval import retrieve
from app.specialized_agents.context import SpecializedAgentContext
from app.specialized_agents.enums import ReviewState
from app.specialized_agents.exceptions import SourceValidationError, UnsupportedFeatureError
from app.specialized_agents.sources import SafeSourceContext, SourceReference, normalize_sources

InsightProvider = Callable[[dict[str, Any], SpecializedAgentContext], dict[str, Any] | Awaitable[dict[str, Any]]]


class InsightService:
    """Validate citations; retrieval is tenant/environment scoped and fail-closed."""

    def __init__(self, provider: InsightProvider | None = None):
        self.provider = provider

    async def _model_provider(self, session: AsyncSession, model_version_id, payload: dict[str, Any], context: SpecializedAgentContext) -> dict[str, Any]:
        model = await session.get(ModelVersion, model_version_id)
        if model is None or not model.provider or not model.model_name:
            raise UnsupportedFeatureError("approved insight model metadata is unavailable")
        api_key = api_key_for(model.provider)
        if not api_key:
            raise UnsupportedFeatureError("configured credentials for the approved insight provider are unavailable")
        system = (
            "Return only a JSON object with an items array. Each item must have kind, statement, and source_references. "
            "Allowed kinds: observed_fact, derived_metric, model_interpretation, recommendation_for_review. "
            "Use only supplied sources. Do not invent citations. Keep facts distinct from interpretation."
        )
        prompt = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        messages = [{"role": "user", "content": prompt}]
        if model.provider in {"openai", "google"}:
            client = _openai_style_client(model.provider, api_key)
            text, _calls, _raw, _tokens = await _complete_openai(client, model.model_name, messages, [], 0.0, provider=model.provider)
        elif model.provider == "anthropic":
            text, _calls, _raw, _tokens = await _complete_anthropic(api_key, model.model_name, system, messages, [], 0.0, provider=model.provider)
        else:
            raise UnsupportedFeatureError("approved model provider is unsupported for insight generation")
        try:
            result = json.loads(text)
        except (TypeError, ValueError) as exc:
            raise UnsupportedFeatureError("insight provider response was not valid structured JSON") from exc
        if not isinstance(result, dict):
            raise UnsupportedFeatureError("insight provider returned an invalid structure")
        return result

    async def generate(
        self,
        *,
        metrics: list[dict[str, Any]],
        sources: SafeSourceContext,
        context: SpecializedAgentContext,
        provider_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not metrics:
            raise SourceValidationError("at least one structured metric is required")
        payload = {"metrics": metrics, "source_references": sources.as_dicts(), "source_fingerprints": list(sources.fingerprints), **(provider_payload or {})}
        provider = self.provider
        if provider is None:
            raise UnsupportedFeatureError("insight provider context is not configured")
        result = provider(payload, context)
        result = await result if inspect.isawaitable(result) else result
        if not isinstance(result, dict) or not isinstance(result.get("items"), list):
            raise SourceValidationError("insight provider must return an items list")
        normalized = []
        known = set(sources.fingerprints)
        authoritative_references = sources.as_dicts()
        for item in result["items"]:
            if not isinstance(item, dict):
                raise SourceValidationError("insight item must be an object")
            kind = item.get("kind")
            if kind not in {"observed_fact", "derived_metric", "model_interpretation", "recommendation_for_review"}:
                raise SourceValidationError("insight item kind is unsupported")
            references = item.get("source_references", [])
            if not isinstance(references, list):
                raise SourceValidationError("source_references must be a list")
            if kind in {"observed_fact", "derived_metric"} and not references:
                raise SourceValidationError("facts and derived metrics require source references")
            for reference in references:
                if not isinstance(reference, dict) or reference.get("content_fingerprint") not in known or reference not in authoritative_references:
                    raise SourceValidationError("insight cited a source that was not supplied or retrieved")
            statement = str(item.get("statement") or "").strip()
            if not statement:
                raise SourceValidationError("insight statement must not be empty")
            normalized.append({"kind": kind, "statement": statement, "source_references": references, "statement_fingerprint": sha256_hex(statement), "review_required": kind in {"model_interpretation", "recommendation_for_review"}})
        review_required = any(item["review_required"] for item in normalized)
        return {"items": normalized, "review_required": review_required, "review_state": ReviewState.REQUIRED.value if review_required else ReviewState.NOT_REQUIRED.value, "source_fingerprints": list(sources.fingerprints), "disclaimer": "Model interpretation is not an observed fact and recommendations require human review."}

    async def resolve_sources(
        self, *, session: AsyncSession, scope: GovernanceScope, question: str,
        document_ids: list[str] | None = None,
        supplied_references: list[dict[str, Any]] | None = None,
    ) -> tuple[SafeSourceContext, list[Any]]:
        """Resolve source claims only after executor governance admission.

        Supplied references are checked against actual current, searchable
        knowledge chunks in the authenticated tenant and environment. Search
        uses the existing tenant-bound retriever. No client-supplied document
        or chunk identifier is accepted as citation proof by itself.
        """
        references: list[SourceReference] = []
        chunks: list[Any] = []
        if document_ids:
            try:
                authorized_document_ids = [uuid.UUID(str(document_id)) for document_id in document_ids]
            except (ValueError, TypeError) as exc:
                raise SourceValidationError("knowledge document id is invalid") from exc
            chunks = await retrieve(
                session, tenant_id=scope.tenant_id, environment_id=scope.environment_id,
                query=question, document_ids=authorized_document_ids,
            )
            for chunk in chunks:
                references.append(SourceReference.from_text(
                    document_id=chunk.document_id, chunk_id=chunk.chunk_id,
                    source_title=chunk.title, text=chunk.text,
                    page_number=chunk.metadata.get("page"),
                ))
            if not chunks:
                raise SourceValidationError("requested knowledge sources returned no retrievable evidence")
        if supplied_references:
            claims = normalize_sources(supplied_references)
            for claim in claims.references:
                try:
                    doc_id = uuid.UUID(claim.document_id)
                    chunk_id = uuid.UUID(claim.chunk_id)
                except ValueError as exc:
                    raise SourceValidationError("source reference is not an authoritative knowledge chunk") from exc
                row = await session.execute(
                    select(KnowledgeChunk, KnowledgeDocument)
                    .join(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id)
                    .where(
                        KnowledgeDocument.id == doc_id,
                        KnowledgeChunk.id == chunk_id,
                        KnowledgeDocument.tenant_id == scope.tenant_id,
                        KnowledgeChunk.tenant_id == scope.tenant_id,
                        KnowledgeDocument.environment_id == scope.environment_id,
                        KnowledgeChunk.environment_id == scope.environment_id,
                        KnowledgeDocument.status.in_(list(SEARCHABLE_DOCUMENT_STATUSES)),
                        KnowledgeChunk.version == KnowledgeDocument.version,
                    )
                )
                source_row = row.first()
                if source_row is None:
                    raise SourceValidationError("source reference is not available in the authorized tenant/environment")
                chunk, document = source_row
                actual_fingerprint = hashlib.sha256(chunk.text.encode("utf-8")).hexdigest()
                if actual_fingerprint != claim.content_fingerprint.lower():
                    raise SourceValidationError("source fingerprint does not match the stored knowledge chunk")
                references.append(SourceReference(
                    document_id=str(document.id), chunk_id=str(chunk.id),
                    source_title=document.title, content_fingerprint=actual_fingerprint,
                    page_number=(chunk.chunk_metadata or {}).get("page"),
                ))
        unique = {reference.key(): reference for reference in references}
        return normalize_sources(unique.values()), chunks

    async def analyze(
        self, *, session: AsyncSession, scope: GovernanceScope, context: SpecializedAgentContext,
        model_version_id, metrics: list[dict[str, Any]], question: str,
        sources: SafeSourceContext, retrieved_chunks: list[Any],
        time_range: dict[str, str] | None = None, analysis_type: str = "summary",
    ) -> dict[str, Any]:
        if not question.strip() or len(question) > 4000:
            raise SourceValidationError("question is required and must be at most 4000 characters")
        if not metrics:
            raise SourceValidationError("at least one structured metric is required")
        excerpts = []
        for chunk in retrieved_chunks:
            actual = SourceReference.from_text(
                document_id=chunk.document_id, chunk_id=chunk.chunk_id,
                source_title=chunk.title, text=chunk.text,
                page_number=chunk.metadata.get("page"),
            )
            if actual.key() not in {reference.key() for reference in sources.references}:
                raise SourceValidationError("retrieved excerpt is not present in the admitted source set")
            excerpts.append({"reference": actual.as_dict(), "excerpt": chunk.text})
        provider_payload = {
            "question": question, "time_range": time_range or {},
            "analysis_type": analysis_type, "retrieved_excerpts": excerpts,
        }
        provider = self.provider or (lambda payload, ctx: self._model_provider(session, model_version_id, payload, ctx))
        temporary = InsightService(provider=provider)
        return await temporary.generate(
            metrics=metrics, sources=sources, context=context, provider_payload=provider_payload,
        )
