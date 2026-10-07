"""Async AI suggestion. It never finalizes a review and never erases a human score.

The model call goes through ``app.ai.gateway.govern``. No provider key is read
here. A missing executor is recorded as not executed; it is not a fake score.
"""

from __future__ import annotations

import json
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.errors import ProviderError
from app.ai.fallback import NON_RETRYABLE, is_retryable
from app.ai.gateway import govern
from app.ai.guardrails.output import check as check_output
from app.auth.dependencies import TenantContext
from app.core.logging import log
from app.db.models import User
from app.jobs.repository import create_job
from app.qa.evidence import turns_for_call
from app.qa.exceptions import DuplicateAutoReview, InvalidScore
from app.qa.models import AutoReviewRun, QAReview, QAReviewItem
from app.qa.repository import items_for, review_items, run_for_key
from app.tenancy.isolation import NotFound

PROMPT_VERSION = "qa-auto-review-v1"
_PERMANENT = {"invalid_json", "schema", "evidence", "validation"}


def idempotency_key(review: QAReview) -> str:
    return f"qa-auto-review:{review.id}:{review.scorecard_version}"


async def enqueue(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    review: QAReview,
    organization_id: uuid.UUID | None = None,
) -> tuple[AutoReviewRun, str]:
    if review.tenant_id != tenant_id:
        raise NotFound()
    if review.status == "finalized":
        raise DuplicateAutoReview("A finalized review is not sent back to the model")
    key = idempotency_key(review)
    existing = await run_for_key(session, tenant_id, key)
    if existing is not None:
        if existing.status == "permanent_failure":
            return existing, "permanent"
        return existing, "duplicate"
    job, created = await create_job(
        session,
        tenant_id=tenant_id,
        organization_id=organization_id,
        environment_id=review.environment_id,
        job_type="qa_auto_review",
        idempotency_key=key,
        payload={
            "review_id": str(review.id),
            "scorecard_version": review.scorecard_version,
            "target_tenant_id": str(tenant_id),
            "target_environment_id": str(review.environment_id),
        },
        max_attempts=3,
    )
    run = AutoReviewRun(
        tenant_id=tenant_id,
        review_id=review.id,
        scorecard_id=review.scorecard_id,
        scorecard_version=review.scorecard_version,
        idempotency_key=key,
        job_id=job.id,
        status="queued" if created else job.status,
        prompt_version=PROMPT_VERSION,
    )
    session.add(run)
    await session.flush()
    log.info("review.auto_review_requested", review_id=str(review.id), tenant_id=str(tenant_id))
    return run, "queued" if created else "duplicate"


def _parse(text: str) -> dict:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InvalidScore("Auto-review response is not JSON") from exc
    if not isinstance(payload, dict):
        raise InvalidScore("Auto-review response must be an object")
    return payload


async def _apply_suggestion(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    review: QAReview,
    suggestion: dict,
    known_turns: set[str],
) -> None:
    items = {row.id: row for row in await items_for(session, tenant_id, review.scorecard_id)}
    held = {row.item_id: row for row in await review_items(session, tenant_id, review.id)}
    for raw in suggestion.get("items") or []:
        if not isinstance(raw, dict):
            raise InvalidScore("Auto-review item is not an object")
        item_id = uuid.UUID(str(raw["item_id"]))
        rubric = items.get(item_id)
        if rubric is None:
            raise InvalidScore("Auto-review item is not on this scorecard")
        for turn_id in raw.get("turn_ids") or []:
            if str(turn_id) not in known_turns:
                raise InvalidScore("Auto-review cited a turn from another call")
        score = raw.get("score")
        na = bool(raw.get("na"))
        if na and not rubric.allow_na:
            raise InvalidScore("Auto-review marked a required item N/A")
        if score is not None:
            if not isinstance(score, int) or isinstance(score, bool):
                raise InvalidScore("Auto-review score must be an integer")
            if score < rubric.min_score or score > rubric.max_score:
                raise InvalidScore("Auto-review score is outside the item range")
        row = held.get(item_id)
        if row is None:
            row = QAReviewItem(tenant_id=tenant_id, review_id=review.id, item_id=item_id)
            session.add(row)
            held[item_id] = row
        if row.human_score is None and not row.human_na:
            row.ai_score = score
            row.ai_na = na
        else:
            # Human value stays. AI is recorded only when it was empty.
            if row.ai_score is None:
                row.ai_score = score
                row.ai_na = na
    await session.flush()


async def process_run(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    ctx: TenantContext,
    executor,
) -> AutoReviewRun:
    run = await session.get(AutoReviewRun, run_id)
    if run is None or run.tenant_id != tenant_id:
        raise NotFound()
    if run.status == "completed":
        return run
    if run.status == "permanent_failure":
        return run
    review = await session.get(QAReview, run.review_id)
    if review is None or review.tenant_id != tenant_id:
        raise NotFound()
    if review.status == "finalized":
        run.status = "permanent_failure"
        run.error_class = "finalized"
        await session.flush()
        return run
    turns = await turns_for_call(session, review.call_id)
    known = {str(turn.id) for turn in turns}
    evidence = [
        {"turn_id": str(turn.id), "speaker": getattr(turn.speaker, "value", str(turn.speaker)), "text": turn.text}
        for turn in turns[:40]
    ]
    prompt = json.dumps(
        {
            "prompt_version": PROMPT_VERSION,
            "scorecard_version": review.scorecard_version,
            "instruction": "Return JSON suggestions only. Do not claim a final score.",
            "turns": evidence,
        }
    )
    run.attempt_count += 1
    run.status = "running"
    await session.flush()
    try:
        result = await govern(
            session,
            ctx,
            text=prompt,
            channel="text",
            environment_id=review.environment_id,
            executor=executor,
            tokens_estimate=1,
            record_usage=False,
            request_id=str(run.id),
        )
    except NON_RETRYABLE as exc:
        run.status = "permanent_failure"
        run.error_class = type(exc).__name__[:64]
        log.info("review.auto_review_failed", review_id=str(review.id), error_class=run.error_class)
        await session.flush()
        return run
    except ProviderError as exc:
        run.status = "failed"
        run.error_class = type(exc).__name__[:64]
        if not is_retryable(exc) or run.attempt_count >= 3:
            run.status = "permanent_failure"
        log.info("review.auto_review_failed", review_id=str(review.id), error_class=run.error_class)
        await session.flush()
        return run
    if not result.executed:
        run.status = "failed"
        run.error_class = "executor_not_attached"
        run.provider = result.provider
        run.model = result.model
        await session.flush()
        return run
    checked = check_output(result.text)
    if not checked.allowed:
        run.status = "permanent_failure"
        run.error_class = checked.reason
        await session.flush()
        return run
    try:
        suggestion = _parse(result.text)
        await _apply_suggestion(
            session, tenant_id=tenant_id, review=review, suggestion=suggestion, known_turns=known
        )
    except (InvalidScore, ValueError, KeyError) as exc:
        run.status = "permanent_failure"
        run.error_class = "validation"
        run.suggestion = {"error": str(exc)[:200]}
        await session.flush()
        return run
    run.status = "completed"
    run.provider = result.provider
    run.model = result.model
    run.prompt_version = PROMPT_VERSION
    run.latency_ms = int(result.telemetry.get("latency_ms") or 0)
    measured_tokens = result.telemetry.get("tokens")
    run.token_count = (
        measured_tokens
        if isinstance(measured_tokens, int) and not isinstance(measured_tokens, bool)
        else None
    )
    run.suggestion = suggestion
    run.error_class = ""
    from datetime import datetime, timezone

    run.completed_at = datetime.now(timezone.utc)
    await session.flush()
    log.info("review.auto_review_completed", review_id=str(review.id), tenant_id=str(tenant_id))
    return run


async def context_for(session: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID) -> TenantContext:
    user = await session.get(User, user_id)
    if user is None or user.tenant_id != tenant_id:
        raise NotFound()
    from app.db.models import Tenant

    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise NotFound()
    return TenantContext(user=user, tenant=tenant)


def human_untouched(item: QAReviewItem, before_human: int | None, before_source: str) -> bool:
    return item.human_score == before_human and (
        item.accepted_source == before_source or item.accepted_source != "ai" or before_source == "human"
    )
