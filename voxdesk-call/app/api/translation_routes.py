"""Tenant-scoped governed translation API.

Exposes the translation job lifecycle: create, fetch, list with filters, and
export in three formats.

Two things are deliberately true of every route here:

* **Scope comes from the server, not the request.** ``environment_id`` is
  resolved through :func:`app.api.specialized_agent_routes._scope`, and every
  query filters on tenant, organization *and* environment. A job id from
  another environment is indistinguishable from one that does not exist.
* **Exports read the stored result, they never re-translate.** Re-running the
  model on export would silently produce a different document from the one the
  reviewer approved, and the approval trail would then point at text that no
  longer exists.

SRT timings are derived, not measured: the engine stores segments and
fingerprints, not audio timings. Cue boundaries are computed from text length
at a fixed reading rate (:data:`SRT_CHARS_PER_SECOND`) so the same job always
exports byte-identical SRT. That is a property worth having — an export that
changes between two downloads of the same approved job cannot be reviewed.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.core.errors import request_id_from
from app.db.session import get_session
from app.review.service import create_case
from app.specialized_agents.executor import SpecializedExecutionRecord
from app.specialized_agents.service import SpecializedAgentService
from app.tenancy.isolation import HierarchyError, NotFound, to_http
from app.translation.engine import TranslationEngine
from app.translation.glossary import Glossary
from app.translation.job_service import get_or_create_glossary, record_translation_result
from app.translation.persistence import TranslationJobRecord, TranslationSegmentRecord
from app.translation.schemas import GlossaryEntryInput, TranslationJobRequest
from app.api.specialized_agent_routes import _scope, response_for

router = APIRouter(prefix="/api/translation", tags=["translation-agent"])

#: Alias surface for clients that pluralised the resource. It is hidden from
#: the schema so the OpenAPI document describes exactly one canonical path per
#: operation, while existing dashboards keep working.
alias_router = APIRouter(
    prefix="/api/translations", tags=["translation-agent"], include_in_schema=False
)

EXPORT_FORMATS = ("json", "srt", "txt")
EXPORT_MEDIA_TYPES = {
    "json": "application/json",
    "srt": "application/x-subrip",
    "txt": "text/plain; charset=utf-8",
}

#: Two caveats shape this number. It is a *reading* rate, not a speech rate,
#: and it is fixed rather than tuned so an export is reproducible; changing it
#: would move every historical cue boundary.
SRT_CHARS_PER_SECOND = 16
SRT_MIN_SECONDS = 1.0
SRT_GAP_SECONDS = 0.12

MAX_EXPORT_LIMIT = 200
DEFAULT_EXPORT_LIMIT = 50


# ---------------------------------------------------------------- create ---


@router.post("/jobs", response_model=dict, status_code=201)
async def create_translation_job(
    payload: TranslationJobRequest,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.ANALYTICS_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        scope = await _scope(session, ctx, payload.environment_id)
        service = SpecializedAgentService(session, scope)
        engine = TranslationEngine(session=session)
        glossary_inputs = [entry.model_dump(mode="json") for entry in payload.glossary]
        glossary_row = await get_or_create_glossary(
            session, scope, version=payload.glossary_version, entries=glossary_inputs
        )
        glossary = Glossary.from_inputs(
            payload.glossary_version,
            [GlossaryEntryInput.model_validate(entry) for entry in glossary_row.entries],
        )
        context = service.context(
            actor_id=ctx.user_id,
            request_id=request_id_from(request),
            trace_id=request.headers.get("X-Trace-ID") or uuid.uuid4().hex,
            agent_type="translation",
            agent_version=payload.agent_version,
            model_version_id=payload.model_version_id,
            risk_tier=payload.risk_tier,
            language=payload.target_language,
        )
        outcome = await service.execute(
            context,
            idempotency_key=payload.idempotency_key,
            payload={
                "source_language": payload.source_language,
                "target_language": payload.target_language,
                "glossary_version": payload.glossary_version,
                "segments": [segment.model_dump(mode="json", exclude={"translated_text"}) for segment in payload.segments],
            },
            source_references=[
                {
                    **reference.model_dump(mode="json"),
                    "content_fingerprint": reference.content_fingerprint.lower(),
                }
                for reference in payload.source_references
            ],
            handler=lambda _context, _payload, _sources: engine.translate(
                source_language=payload.source_language,
                target_language=payload.target_language,
                segments=payload.segments,
                glossary=glossary,
                context=_context,
            ),
        )
        await record_translation_result(
            session, scope, execution_id=outcome.record.id, output=outcome.output,
            idempotency_key=payload.idempotency_key, source_language=payload.source_language,
            target_language=payload.target_language, glossary_version=payload.glossary_version,
            glossary_entries=glossary_inputs,
            source_lengths={segment.segment_id: len(segment.source_text) for segment in payload.segments},
        )
        review_case = None
        if outcome.record.review_required:
            review_case = await create_case(
                session, scope, actor_user_id=ctx.user_id, case_type="translation",
                execution_id=outcome.record.id, reason="Translation quality checks require human review",
                requested_controls=["translation_quality_review"],
            )
        await session.commit()
        response = response_for(outcome.record, outcome.output).model_dump(mode="json")
        response["review_case_id"] = str(review_case.id) if review_case else None
        return response
    except HierarchyError as exc:
        raise to_http(exc) from None


# ------------------------------------------------------------------- list ---


@router.get("/jobs", response_model=dict)
async def list_translation_jobs(
    environment_id: uuid.UUID,
    status: str | None = Query(default=None, max_length=32),
    source_language: str | None = Query(default=None, max_length=32),
    target_language: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=DEFAULT_EXPORT_LIMIT, ge=1, le=MAX_EXPORT_LIMIT),
    offset: int = Query(default=0, ge=0),
    ctx: TenantContext = Depends(require_permission(Permission.ANALYTICS_READ)),
    session: AsyncSession = Depends(get_session),
):
    """List translation jobs in this environment, newest first.

    ``status`` matches either the translation projection's own status or the
    governing execution status, because the two advance at different points:
    the execution says ``review_required`` while the projection still says
    ``completed``. Matching only one of them would hide jobs the dashboard
    needs to show.
    """
    try:
        scope = await _scope(session, ctx, environment_id)
        base = (
            select(TranslationJobRecord)
            .join(
                SpecializedExecutionRecord,
                TranslationJobRecord.execution_id == SpecializedExecutionRecord.id,
            )
            .where(
                TranslationJobRecord.tenant_id == scope.tenant_id,
                TranslationJobRecord.organization_id == scope.organization_id,
                TranslationJobRecord.environment_id == scope.environment_id,
                SpecializedExecutionRecord.agent_type == "translation",
            )
        )
        if status:
            wanted = status.strip().lower()
            base = base.where(
                or_(
                    TranslationJobRecord.status == wanted,
                    SpecializedExecutionRecord.status == wanted,
                )
            )
        if source_language:
            base = base.where(
                TranslationJobRecord.source_language == source_language.strip().lower()
            )
        if target_language:
            base = base.where(
                TranslationJobRecord.target_language == target_language.strip().lower()
            )

        total = int(
            await session.scalar(
                select(func.count()).select_from(base.subquery())
            )
            or 0
        )
        rows = list(
            (
                await session.scalars(
                    base.order_by(
                        TranslationJobRecord.created_at.desc(),
                        TranslationJobRecord.execution_id.desc(),
                    )
                    .limit(limit)
                    .offset(offset)
                )
            ).all()
        )
        execution_ids = [row.execution_id for row in rows]
        executions: dict[uuid.UUID, SpecializedExecutionRecord] = {}
        if execution_ids:
            found = list(
                (
                    await session.scalars(
                        select(SpecializedExecutionRecord).where(
                            SpecializedExecutionRecord.id.in_(execution_ids),
                            SpecializedExecutionRecord.tenant_id == scope.tenant_id,
                            SpecializedExecutionRecord.organization_id == scope.organization_id,
                            SpecializedExecutionRecord.environment_id == scope.environment_id,
                        )
                    )
                ).all()
            )
            executions = {row.id: row for row in found}

        items = []
        for row in rows:
            execution = executions.get(row.execution_id)
            items.append(
                {
                    "execution_id": str(row.execution_id),
                    "id": str(row.execution_id),
                    "status": row.status,
                    "execution_status": execution.status if execution else None,
                    "source_language": row.source_language,
                    "target_language": row.target_language,
                    "glossary_version": row.glossary_version,
                    "segment_count": row.segment_count,
                    "total_segments": row.total_segments,
                    "completed_segments": row.completed_segments,
                    "failed_segments": row.failed_segments,
                    "quality_status": row.quality_status,
                    "review_required": row.review_required,
                    "review_state": row.review_state,
                    "attempt_count": row.attempt_count,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                    "completed_at": row.completed_at.isoformat() if row.completed_at else None,
                    "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                }
            )
        return {"items": items, "total": total, "limit": limit, "offset": offset}
    except HierarchyError as exc:
        raise to_http(exc) from None


# -------------------------------------------------------------------- get ---


@router.get("/jobs/{execution_id}", response_model=dict)
async def get_translation_job(
    execution_id: uuid.UUID,
    environment_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.ANALYTICS_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        scope = await _scope(session, ctx, environment_id)
        row = await session.scalar(
            select(SpecializedExecutionRecord).where(
                SpecializedExecutionRecord.id == execution_id,
                SpecializedExecutionRecord.tenant_id == scope.tenant_id,
                SpecializedExecutionRecord.organization_id == scope.organization_id,
                SpecializedExecutionRecord.environment_id == scope.environment_id,
                SpecializedExecutionRecord.agent_type == "translation",
            )
        )
        if row is None:
            raise NotFound()
        return response_for(row, row.result or {}).model_dump(mode="json")
    except HierarchyError as exc:
        raise to_http(exc) from None


# ----------------------------------------------------------------- export ---


def _srt_timestamp(seconds: float) -> str:
    """Format one SRT cue timestamp. Negative or NaN input collapses to zero."""
    try:
        total = max(0.0, float(seconds))
    except (TypeError, ValueError):
        total = 0.0
    hours = int(total // 3600)
    minutes = int((total % 3600) // 60)
    secs = int(total % 60)
    millis = int(round((total - int(total)) * 1000))
    if millis == 1000:  # rounding can carry a full second
        millis = 0
        secs += 1
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _ordered_segments(result: dict, segments: list[TranslationSegmentRecord]) -> list[dict]:
    """Return result segments in durable segment order.

    ``TranslationSegmentRecord`` is the durable ordering (it carries
    ``segment_index``); the engine result is the source of the text. The two
    are recorded in the same order, so the index list is used when it is the
    same length and the result order is used otherwise.
    """
    result_segments = [item for item in (result or {}).get("segments", []) if isinstance(item, dict)]
    if len(segments) == len(result_segments) and segments:
        return [
            result_segments[position]
            for position, _row in enumerate(
                sorted(segments, key=lambda row: row.segment_index)
            )
        ]
    return result_segments


def _build_srt(result: dict, segments: list[dict]) -> str:
    """Render SRT cues with timings derived deterministically from text length."""
    lines: list[str] = []
    cursor = 0.0
    for index, segment in enumerate(segments, start=1):
        text = str(segment.get("translated_text") or "")
        duration = max(SRT_MIN_SECONDS, len(text) / SRT_CHARS_PER_SECOND)
        start = cursor
        end = start + duration
        cursor = end + SRT_GAP_SECONDS
        lines.append(str(index))
        lines.append(f"{_srt_timestamp(start)} --> {_srt_timestamp(end)}")
        lines.append(text)
        lines.append("")
    return "\n".join(lines)


@router.get("/jobs/{execution_id}/export")
async def export_translation_job(
    execution_id: uuid.UUID,
    environment_id: uuid.UUID,
    format: str = Query(default="json", max_length=8),
    ctx: TenantContext = Depends(require_permission(Permission.ANALYTICS_READ)),
    session: AsyncSession = Depends(get_session),
):
    """Export one translation job as ``json``, ``srt`` or ``txt``.

    The export is built from the persisted result only. Nothing here calls the
    model, so an approved job always exports the text that was approved.
    """
    try:
        scope = await _scope(session, ctx, environment_id)
        wanted = (format or "json").strip().lower()
        if wanted not in EXPORT_FORMATS:
            raise NotFound()
        execution = await session.scalar(
            select(SpecializedExecutionRecord).where(
                SpecializedExecutionRecord.id == execution_id,
                SpecializedExecutionRecord.tenant_id == scope.tenant_id,
                SpecializedExecutionRecord.organization_id == scope.organization_id,
                SpecializedExecutionRecord.environment_id == scope.environment_id,
                SpecializedExecutionRecord.agent_type == "translation",
            )
        )
        if execution is None:
            raise NotFound()
        job = await session.scalar(
            select(TranslationJobRecord).where(
                TranslationJobRecord.execution_id == execution_id,
                TranslationJobRecord.tenant_id == scope.tenant_id,
                TranslationJobRecord.organization_id == scope.organization_id,
                TranslationJobRecord.environment_id == scope.environment_id,
            )
        )
        segment_rows = list(
            (
                await session.scalars(
                    select(TranslationSegmentRecord)
                    .where(
                        TranslationSegmentRecord.execution_id == execution_id,
                        TranslationSegmentRecord.tenant_id == scope.tenant_id,
                        TranslationSegmentRecord.organization_id == scope.organization_id,
                        TranslationSegmentRecord.environment_id == scope.environment_id,
                    )
                    .order_by(TranslationSegmentRecord.segment_index)
                )
            ).all()
        )
        result = dict(execution.result or {})
        segments = _ordered_segments(result, segment_rows)

        if wanted == "json":
            body = _export_json(execution, job, result, segments)
        elif wanted == "srt":
            body = _build_srt(result, segments)
        else:
            body = _export_txt(segments)

        filename = f"translation-{execution_id}.{wanted}"
        return Response(
            content=body,
            media_type=EXPORT_MEDIA_TYPES[wanted],
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "X-Translation-Export-Format": wanted,
                "X-Translation-Segment-Count": str(len(segments)),
            },
        )
    except HierarchyError as exc:
        raise to_http(exc) from None


def _export_json(
    execution: SpecializedExecutionRecord,
    job: TranslationJobRecord | None,
    result: dict,
    segments: list[dict],
) -> str:
    import json

    payload = {
        "execution_id": str(execution.id),
        "id": str(execution.id),
        "format": "json",
        "status": execution.status,
        "source_language": job.source_language if job else result.get("source_language"),
        "target_language": job.target_language if job else result.get("target_language"),
        "glossary_version": job.glossary_version if job else None,
        "quality_status": result.get("quality_status")
        or (job.quality_status if job else None),
        "review_required": bool(execution.review_required),
        "review_state": execution.review_state,
        "provider": result.get("provider"),
        "model": result.get("model"),
        "methodology": result.get("methodology"),
        "segment_count": len(segments),
        "segments": [
            {
                "segment_id": segment.get("segment_id"),
                "source_text": segment.get("source_text", ""),
                "translated_text": segment.get("translated_text", ""),
                "source_fingerprint": segment.get("source_fingerprint"),
                "target_fingerprint": segment.get("target_fingerprint"),
                "quality_flags": list(segment.get("quality_flags") or []),
                "review_required": bool(segment.get("review_required", False)),
                "status": segment.get("status") or "completed",
            }
            for segment in segments
        ],
        "exported_at": execution.updated_at.isoformat() if execution.updated_at else None,
    }
    return json.dumps(payload, indent=2, sort_keys=False)


def _export_txt(segments: list[dict]) -> str:
    """Plain-text export: one translated segment per line, in durable order.

    Blank segments are skipped rather than emitted as blank lines, so the
    reader is not left guessing whether a gap was intentional.
    """
    lines = [str(segment.get("translated_text") or "") for segment in segments]
    return "\n".join(line for line in lines if line.strip())


# ------------------------------------------------------------ alias surface ---

# Registered on both routers rather than by including ``router`` twice: the
# alias must stay out of the published schema, and a shared handler keeps the
# two surfaces from drifting apart behaviourally.
alias_router.add_api_route(
    "/jobs",
    list_translation_jobs,
    methods=["GET"],
    name="list_translation_jobs_alias",
)
alias_router.add_api_route(
    "/jobs/{execution_id}",
    get_translation_job,
    methods=["GET"],
    name="get_translation_job_alias",
)
alias_router.add_api_route(
    "/jobs/{execution_id}/export",
    export_translation_job,
    methods=["GET"],
    name="export_translation_job_alias",
)
