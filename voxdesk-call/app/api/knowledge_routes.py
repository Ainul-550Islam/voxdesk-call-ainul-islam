"""
Knowledge document API.

    POST   /api/knowledge/documents            upload
    GET    /api/knowledge/documents            list (tenant-scoped)
    GET    /api/knowledge/documents/{id}       one document + safe metadata
    DELETE /api/knowledge/documents/{id}       archive (or purge with ?hard=true)
    POST   /api/knowledge/documents/{id}/reindex
    POST   /api/knowledge/documents/{id}/restore
    POST   /api/knowledge/search               retrieval preview, for debugging

Authorization comes entirely from the STEP 2 permission layer -- there is not
a single role-string comparison in this file. Ownership comes from
`get_owned()`, which 404s on another tenant's id rather than 403ing, so the
API cannot be used to probe which document ids exist elsewhere.

What is deliberately *not* in any response: raw vectors, storage keys,
filesystem paths, bucket names, credentials, or similarity scores. The
response models below are allowlists, not conveniences -- a field only reaches
a client because someone wrote it out here.
"""
from __future__ import annotations

import uuid

from fastapi import (
    APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, Response,
    UploadFile,
)
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, get_owned, require_permission
from app.auth.permissions import Permission
from app.core.config import settings
from app.core.logging import log
from app.db.models import DocumentStatus, KnowledgeChunk, KnowledgeDocument
from app.db.session import get_session
from app.knowledge import ingest
from app.knowledge.jobs import enqueue_ingestion
from app.knowledge.url_ingest import CrawlError, crawl

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


# --------------------------------------------------------------- schemas ---

class DocumentOut(BaseModel):
    """
    The tenant-visible view of a document.

    `source_uri` is absent on purpose: it is a storage key, and requirement 28
    forbids handing clients anything that describes where bytes live.
    """

    id: uuid.UUID
    title: str
    status: str
    source_type: str
    original_filename: str | None = None
    file_type: str | None = None
    file_size: int = 0
    version: int = 1
    chunk_count: int = 0
    char_count: int = 0
    token_estimate: int = 0
    embedding_model: str | None = None
    embedding_dimensions: int | None = None
    error_message: str | None = None
    metadata: dict = Field(default_factory=dict)
    created_at: str | None = None
    updated_at: str | None = None
    indexed_at: str | None = None
    is_searchable: bool = False


class DocumentListOut(BaseModel):
    documents: list[DocumentOut]
    total: int
    limits: dict


class SearchRequest(BaseModel):
    query: str
    top_k: int | None = None
    environment_id: uuid.UUID | None = None


class SearchHit(BaseModel):
    """
    A retrieval preview result.

    Scores are exposed here and nowhere else: this endpoint exists so an
    operator can tune thresholds and see why the agent answered as it did. It
    still never returns a vector.
    """

    document_id: str
    chunk_id: str
    title: str
    text: str
    score: float
    page: int | None = None
    heading: str | None = None


class SearchResponse(BaseModel):
    query: str
    results: list[SearchHit]
    count: int


def _serialize(document: KnowledgeDocument) -> DocumentOut:
    return DocumentOut(
        id=document.id,
        title=document.title,
        status=document.status.value,
        source_type=document.source_type.value,
        original_filename=document.original_filename,
        # The *detected* format, which is what `mime_type` actually holds.
        file_type=document.mime_type,
        file_size=document.file_size,
        version=document.version,
        chunk_count=document.chunk_count,
        char_count=document.char_count,
        token_estimate=document.token_estimate,
        embedding_model=document.embedding_model,
        embedding_dimensions=document.embedding_dimensions,
        error_message=document.error_message,
        metadata=dict(document.doc_metadata or {}),
        created_at=document.created_at.isoformat() if document.created_at else None,
        updated_at=document.updated_at.isoformat() if document.updated_at else None,
        indexed_at=document.ingested_at.isoformat() if document.ingested_at else None,
        is_searchable=document.is_searchable,
    )


# ----------------------------------------------------------------- routes ---

@router.post("/documents", response_model=DocumentOut, status_code=202)
async def upload_document(
    background: BackgroundTasks,
    response: Response,
    file: UploadFile = File(...),
    title: str = Form(""),
    environment_id: str = Form(""),
    ctx: TenantContext = Depends(require_permission(Permission.KNOWLEDGE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """
    Upload a document. Returns 202 immediately; indexing happens out of band.

    The response is the document in UPLOADED state -- clients poll GET
    /documents/{id} for the transition to READY or FAILED. Blocking the
    request on a 200-page PDF would tie up a worker for a minute and time the
    browser out anyway.
    """
    # Read with a hard ceiling. Streaming to disk before checking the size
    # would let a client fill the volume, so the limit is enforced on the way
    # in and the read stops one byte past it.
    limit = ingest.max_file_bytes()
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(
            status_code=413,
            detail=f"File is too large; the limit is {settings.knowledge_max_file_mb} MB",
        )

    try:
        bound_environment = None
        if environment_id:
            from app.environments.resource_scope import resolve_scope
            from app.tenancy.isolation import HierarchyError, to_http
            try:
                environment = await resolve_scope(
                    session,
                    tenant_id=ctx.tenant_id,
                    user_id=ctx.user_id,
                    explicit_environment_id=uuid.UUID(environment_id),
                    for_write=True,
                )
            except (ValueError, HierarchyError) as exc:
                if isinstance(exc, HierarchyError):
                    raise to_http(exc) from None
                raise HTTPException(status_code=422, detail="Invalid environment id") from None
            bound_environment = environment.id
        result = await ingest.create_document(
            session,
            tenant_id=ctx.tenant_id,
            data=data,
            filename=file.filename or "upload",
            title=title or None,
            # Recorded for the audit trail, never trusted for dispatch.
            declared_mime=file.content_type,
            environment_id=bound_environment,
        )
    except ingest.DuplicateDocument as duplicate:
        # 200, not 409: re-uploading the same file is idempotent from the
        # client's point of view, and a retrying uploader should not have to
        # special-case it.
        log.info(
            "knowledge.upload_duplicate",
            tenant_id=str(ctx.tenant_id),
            document_id=str(duplicate.document.id),
        )
        # 200 rather than the route's default 202: nothing was accepted for
        # processing, the existing document is simply handed back.
        response.status_code = 200
        return _serialize(duplicate.document)
    except ingest.ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    enqueue_ingestion(background, result.document.id)
    log.info(
        "knowledge.uploaded",
        tenant_id=str(ctx.tenant_id),
        actor=ctx.user.email,
        document_id=str(result.document.id),
    )
    return _serialize(result.document)


@router.get("/documents", response_model=DocumentListOut)
async def list_documents(
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx: TenantContext = Depends(require_permission(Permission.KNOWLEDGE_READ)),
    session: AsyncSession = Depends(get_session),
):
    """List this tenant's documents. The tenant filter is not optional."""
    conditions = [KnowledgeDocument.tenant_id == ctx.tenant_id]
    if status_filter:
        try:
            conditions.append(
                KnowledgeDocument.status == DocumentStatus(status_filter.lower())
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Unknown status") from exc

    total = int(
        (
            await session.execute(
                select(func.count(KnowledgeDocument.id)).where(*conditions)
            )
        ).scalar()
        or 0
    )
    rows = (
        (
            await session.execute(
                select(KnowledgeDocument)
                .where(*conditions)
                .order_by(KnowledgeDocument.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )

    return DocumentListOut(
        documents=[_serialize(row) for row in rows],
        total=total,
        limits={
            "max_file_mb": settings.knowledge_max_file_mb,
            "max_documents": settings.knowledge_max_documents_per_tenant,
            "supported_types": ["pdf", "docx", "txt", "md", "csv", "json"],
        },
    )


@router.get("/documents/{document_id}", response_model=DocumentOut)
async def get_document(
    document_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.KNOWLEDGE_READ)),
    session: AsyncSession = Depends(get_session),
):
    document = await get_owned(session, KnowledgeDocument, document_id, ctx)
    return _serialize(document)


@router.delete("/documents/{document_id}", status_code=200)
async def delete_document(
    document_id: uuid.UUID,
    hard: bool = Query(
        False, description="Permanently destroy the document, chunks and file"
    ),
    ctx: TenantContext = Depends(require_permission(Permission.KNOWLEDGE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """
    Archive by default, purge with `?hard=true`.

    Archiving stops retrieval immediately while keeping the row for audit and
    history, which is what "delete" should mean for business records. A real
    destroy is a separate, more privileged act, so it re-checks
    `knowledge:delete` rather than relying on the write permission above.
    """
    document = await get_owned(session, KnowledgeDocument, document_id, ctx)

    if hard:
        if not ctx.can(Permission.KNOWLEDGE_DELETE):
            raise HTTPException(
                status_code=403,
                detail="Requires permission: knowledge:delete",
            )
        await ingest.purge_document(session, document)
        log.info(
            "knowledge.purged",
            tenant_id=str(ctx.tenant_id), actor=ctx.user.email,
            document_id=str(document_id),
        )
        return {"ok": True, "status": "deleted"}

    await ingest.archive_document(session, document)
    log.info(
        "knowledge.archived",
        tenant_id=str(ctx.tenant_id), actor=ctx.user.email,
        document_id=str(document_id),
    )
    return {"ok": True, "status": document.status.value}


@router.post("/documents/{document_id}/reindex", response_model=DocumentOut)
async def reindex_document(
    document_id: uuid.UUID,
    background: BackgroundTasks,
    ctx: TenantContext = Depends(require_permission(Permission.KNOWLEDGE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """
    Rebuild a document's chunks -- after a chunking or embedding model change.

    Archived documents are refused rather than silently resurrected.
    """
    document = await get_owned(session, KnowledgeDocument, document_id, ctx)

    if document.status == DocumentStatus.ARCHIVED:
        raise HTTPException(
            status_code=409,
            detail="This document is archived. Restore it before reindexing.",
        )
    if document.status == DocumentStatus.PROCESSING:
        # Not an error: the client asked for indexing and indexing is
        # happening. Returning 409 here would make retrying clients noisy.
        return _serialize(document)

    document.status = DocumentStatus.UPLOADED
    document.error_message = None
    await session.commit()

    enqueue_ingestion(background, document.id, reindex=True)
    log.info(
        "knowledge.reindex_requested",
        tenant_id=str(ctx.tenant_id), actor=ctx.user.email,
        document_id=str(document_id),
    )
    return _serialize(document)


@router.post("/documents/{document_id}/restore", response_model=DocumentOut)
async def restore_document(
    document_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.KNOWLEDGE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """Bring an archived document back. It is searchable again only if indexed."""
    document = await get_owned(session, KnowledgeDocument, document_id, ctx)
    await ingest.restore_document(session, document)
    return _serialize(document)


@router.post("/search", response_model=SearchResponse)
async def search_knowledge(
    payload: SearchRequest,
    ctx: TenantContext = Depends(require_permission(Permission.KNOWLEDGE_READ)),
    session: AsyncSession = Depends(get_session),
):
    """
    Run a retrieval query as the agent would, for tuning and debugging.

    Uses the same `retrieve()` the voice pipeline uses, with the same
    tenant scoping -- so if this endpoint cannot see another tenant's content,
    neither can a call.
    """
    from app.knowledge.retrieval import retrieve

    bound_environment = payload.environment_id
    if bound_environment is not None:
        from app.environments.resource_scope import load_environment
        from app.tenancy.isolation import HierarchyError, to_http
        try:
            await load_environment(session, bound_environment, ctx.tenant_id)
        except HierarchyError as exc:
            raise to_http(exc) from None
    chunks = await retrieve(
        session,
        tenant_id=ctx.tenant_id,
        query=payload.query,
        top_k=payload.top_k,
        environment_id=bound_environment,
    )
    return SearchResponse(
        query=payload.query,
        results=[
            SearchHit(
                document_id=chunk.document_id,
                chunk_id=chunk.chunk_id,
                title=chunk.title,
                text=chunk.text,
                score=round(chunk.score, 4),
                page=chunk.metadata.get("page"),
                heading=chunk.metadata.get("heading"),
            )
            for chunk in chunks
        ],
        count=len(chunks),
    )


class UrlIngestRequest(BaseModel):
    url: str
    max_depth: int = Field(default=0, ge=0, le=3)
    max_pages: int = Field(default=20, ge=1, le=100)
    environment_id: uuid.UUID | None = None


@router.post("/urls", status_code=202)
async def ingest_url(
    payload: UrlIngestRequest,
    background: BackgroundTasks,
    ctx: TenantContext = Depends(require_permission(Permission.KNOWLEDGE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        result = await crawl(session, tenant_id=ctx.tenant_id, url=payload.url, max_depth=payload.max_depth, max_pages=payload.max_pages, environment_id=payload.environment_id)
    except CrawlError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    for item in result.documents:
        if item.document.status == DocumentStatus.UPLOADED:
            enqueue_ingestion(background, item.document.id)
    return {"source_id": str(result.source.id), "visited": list(result.visited), "documents": [str(item.document.id) for item in result.documents]}


@router.get("/stats")
async def knowledge_stats(
    ctx: TenantContext = Depends(require_permission(Permission.KNOWLEDGE_READ)),
    session: AsyncSession = Depends(get_session),
):
    """Counts by status, plus how many chunks are actually retrievable."""
    from app.knowledge.vectorstore import count_searchable_chunks

    rows = (
        await session.execute(
            select(KnowledgeDocument.status, func.count(KnowledgeDocument.id))
            .where(KnowledgeDocument.tenant_id == ctx.tenant_id)
            .group_by(KnowledgeDocument.status)
        )
    ).all()
    by_status = {status.value: count for status, count in rows}

    total_chunks = int(
        (
            await session.execute(
                select(func.count(KnowledgeChunk.id)).where(
                    KnowledgeChunk.tenant_id == ctx.tenant_id
                )
            )
        ).scalar()
        or 0
    )

    return {
        "documents": by_status,
        "total_documents": sum(by_status.values()),
        "total_chunks": total_chunks,
        "searchable_chunks": await count_searchable_chunks(
            session, tenant_id=ctx.tenant_id
        ),
        "embedding_model": settings.knowledge_embedding_model,
        "embedding_dimensions": settings.knowledge_embedding_dimensions,
    }