"""QA application service.

Routes call this module. A review points at an existing call. Scoring is
deterministic. AI suggestions stay in their own columns until a person accepts
them. Finalized history is snapshotted before a reopen.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import record_audit
from app.contact_center.models import QueueEntry
from app.core.logging import log
from app.db.models import AuditAction, Call, EnvironmentMembership, User
from app.qa import auto_review, coaching, evidence, rubrics, scoring
from app.qa.exceptions import (
    InvalidEvidence,
    InvalidScore,
    InvalidTransition,
    ReviewerNotAuthorized,
)
from app.qa.models import QAReview, QAReviewItem, legal
from app.qa.repository import bump_review, get_review, items_for, review_items
from app.qa.scoring import ItemInput
from app.tenancy.isolation import BoundaryDenied, NotFound

_MUTABLE = {"assigned", "in_review", "reopened", "submitted"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _audit(session, *, tenant_id, actor_id, operation: str, detail: dict) -> None:
    safe = {key: value for key, value in detail.items() if "token" not in key and "secret" not in key}
    safe["operation"] = operation
    await record_audit(
        session,
        action=AuditAction.RESOURCE_BOUND,
        tenant_id=tenant_id,
        actor_user_id=actor_id,
        detail=safe,
        commit=False,
    )


async def assert_environment(session: AsyncSession, user_id: uuid.UUID, environment_id: uuid.UUID) -> None:
    row = (
        await session.execute(
            select(EnvironmentMembership).where(
                EnvironmentMembership.environment_id == environment_id,
                EnvironmentMembership.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if row is not None and row.status in {"revoked", "expired"}:
        raise BoundaryDenied()


async def create_review(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    call_id: uuid.UUID,
    scorecard_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    idempotency_key: str | None = None,
    priority: int = 0,
    environment_id: uuid.UUID | None = None,
) -> tuple[QAReview, str]:
    call = await evidence.load_call(session, tenant_id, call_id)
    if environment_id is not None and environment_id != call.environment_id:
        raise BoundaryDenied()
    if actor_id is not None:
        await assert_environment(session, actor_id, call.environment_id)
    card, _sections, rubric_items = await rubrics.load_tree(session, tenant_id, scorecard_id)
    if card.status != "active":
        raise InvalidTransition("Scorecard is not active")
    if card.environment_id is not None and card.environment_id != call.environment_id:
        raise BoundaryDenied()
    key = (idempotency_key or f"review:{call.id}:{card.id}")[:128]
    existing = (
        await session.execute(
            select(QAReview).where(QAReview.tenant_id == tenant_id, QAReview.idempotency_key == key)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing, "duplicate"
    queue_id = (
        await session.execute(
            select(QueueEntry.queue_id).where(
                QueueEntry.tenant_id == tenant_id,
                QueueEntry.call_id == call.id,
            )
        )
    ).scalars().first()
    row = QAReview(
        tenant_id=tenant_id,
        environment_id=call.environment_id,
        call_id=call.id,
        queue_id=queue_id,
        scorecard_id=card.id,
        scorecard_version=card.version,
        status="created",
        created_by=actor_id,
        priority=priority,
        open_key=f"{call.id}:{card.id}",
        idempotency_key=key,
    )
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:
        found = (
            await session.execute(
                select(QAReview).where(
                    QAReview.tenant_id == tenant_id,
                    QAReview.open_key == f"{call.id}:{card.id}",
                )
            )
        ).scalar_one_or_none()
        if found is None:
            raise InvalidTransition("An open review already exists for this call and scorecard")
        return found, "duplicate"
    for item in rubric_items:
        session.add(QAReviewItem(tenant_id=tenant_id, review_id=row.id, item_id=item.id))
    await session.flush()
    await _audit(
        session,
        tenant_id=tenant_id,
        actor_id=actor_id,
        operation="review.created",
        detail={"review_id": str(row.id), "call_id": str(call.id)},
    )
    log.info("review.created", review_id=str(row.id), tenant_id=str(tenant_id))
    return row, "created"


async def assign_review(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    review_id: uuid.UUID,
    assignee_id: uuid.UUID,
    actor_id: uuid.UUID,
) -> tuple[QAReview, str]:
    review = await get_review(session, tenant_id, review_id)
    await assert_environment(session, actor_id, review.environment_id)
    await assert_environment(session, assignee_id, review.environment_id)
    assignee = await session.get(User, assignee_id)
    if assignee is None or assignee.tenant_id != tenant_id or not assignee.is_active:
        raise NotFound()
    if review.assignee_id == assignee_id and review.status in {"assigned", "in_review"}:
        return review, "duplicate"
    if review.status == "created":
        target = "assigned"
    elif review.status == "reopened":
        target = "assigned"
    elif review.status == "assigned":
        target = "assigned"
    else:
        raise InvalidTransition(f"Cannot assign a {review.status} review")
    if target != review.status and not legal(review.status, target) and review.status != "assigned":
        raise InvalidTransition(f"Cannot assign a {review.status} review")
    await bump_review(
        session,
        review,
        review.version,
        {"status": "assigned", "assignee_id": assignee_id},
    )
    await _audit(
        session,
        tenant_id=tenant_id,
        actor_id=actor_id,
        operation="review.assigned",
        detail={"review_id": str(review.id), "assignee_id": str(assignee_id)},
    )
    log.info("review.assigned", review_id=str(review.id), tenant_id=str(tenant_id))
    return review, "applied"


async def save_scores(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    review_id: uuid.UUID,
    actor_id: uuid.UUID,
    scores: list[dict],
) -> QAReview:
    review = await get_review(session, tenant_id, review_id)
    await assert_environment(session, actor_id, review.environment_id)
    if review.status == "finalized":
        raise InvalidTransition("A finalized review cannot be edited")
    if review.status == "cancelled":
        raise InvalidTransition("A cancelled review cannot be edited")
    if review.assignee_id is not None and review.assignee_id != actor_id:
        raise ReviewerNotAuthorized("Only the assigned reviewer can score this review")
    if review.status == "assigned":
        await bump_review(session, review, review.version, {"status": "in_review"})
    elif review.status == "reopened":
        await bump_review(session, review, review.version, {"status": "in_review"})
    elif review.status not in _MUTABLE:
        raise InvalidTransition(f"Cannot score a {review.status} review")
    rubric = {row.id: row for row in await items_for(session, tenant_id, review.scorecard_id)}
    held = {row.item_id: row for row in await review_items(session, tenant_id, review.id)}
    now = _now()
    for raw in scores:
        item_id = raw["item_id"]
        if not isinstance(item_id, uuid.UUID):
            item_id = uuid.UUID(str(item_id))
        spec = rubric.get(item_id)
        row = held.get(item_id)
        if spec is None or row is None:
            raise InvalidScore("Score item is not on this scorecard")
        na = bool(raw.get("na"))
        value = raw.get("score")
        if na and not spec.allow_na:
            raise InvalidScore("This item cannot be marked N/A")
        if not na:
            if not isinstance(value, int) or isinstance(value, bool):
                raise InvalidScore("Score must be an integer")
            if value < spec.min_score or value > spec.max_score:
                raise InvalidScore("Score is outside the item range")
        reason = (raw.get("override_reason") or "")[:300]
        if row.ai_score is not None and not na and value != row.ai_score and not reason:
            raise InvalidScore("Override reason is required when changing an AI suggestion")
        row.human_score = None if na else value
        row.human_na = na
        row.accepted_source = "human"
        row.override_reason = reason
        row.actor_id = actor_id
        row.scored_at = now
    await session.flush()
    return review


def _inputs(review_rows, rubric_items, sections) -> list[ItemInput]:
    section_weight = {row.id: row.weight for row in sections}
    spec = {row.id: row for row in rubric_items}
    built = []
    for row in review_rows:
        item = spec[row.item_id]
        if row.accepted_source == "ai" and row.human_score is None and not row.human_na:
            value = None if row.ai_na else row.ai_score
            na = row.ai_na
        elif row.human_na or row.human_score is not None or row.accepted_source == "human":
            value = None if row.human_na else row.human_score
            na = row.human_na
        else:
            value = None
            na = False
        built.append(
            ItemInput(
                item_id=str(item.id),
                section_id=str(item.section_id),
                section_weight=section_weight[item.section_id],
                weight=item.weight,
                min_score=item.min_score,
                max_score=item.max_score,
                required=item.required,
                value=value,
                not_applicable=na,
            )
        )
    return built


async def calculate_review(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    review_id: uuid.UUID,
    actor_id: uuid.UUID | None,
) -> QAReview:
    review = await get_review(session, tenant_id, review_id)
    if review.status == "finalized":
        return review
    if review.status not in {"in_review", "submitted", "calculated", "reopened"}:
        raise InvalidTransition(f"Cannot calculate a {review.status} review")
    card, sections, rubric_items = await rubrics.load_tree(session, tenant_id, review.scorecard_id)
    rows = await review_items(session, tenant_id, review.id)
    inputs = _inputs(rows, rubric_items, sections)
    result = scoring.calculate(inputs, pass_threshold=card.pass_threshold)
    snapshot = {
        "pass_threshold": card.pass_threshold,
        "scorecard_version": card.version,
        "formula": result.formula,
        "overall": result.overall,
        "passed": result.passed,
        "items": [
            {
                "item_id": item.item_id,
                "section_id": item.section_id,
                "section_weight": item.section_weight,
                "weight": item.weight,
                "min_score": item.min_score,
                "max_score": item.max_score,
                "required": item.required,
                "value": item.value,
                "not_applicable": item.not_applicable,
            }
            for item in inputs
        ],
    }
    target = review.status if review.status == "calculated" else "calculated"
    if review.status != "calculated" and not legal(review.status, "calculated") and review.status != "in_review":
        if review.status == "in_review":
            await bump_review(session, review, review.version, {"status": "submitted"})
        else:
            raise InvalidTransition(f"Cannot calculate a {review.status} review")
    if review.status == "in_review":
        await bump_review(session, review, review.version, {"status": "submitted"})
    await bump_review(
        session,
        review,
        review.version,
        {
            "status": "calculated",
            "overall_score": result.overall,
            "passed": result.passed,
            "calculation_snapshot": snapshot,
            "calculated_at": _now(),
        },
    )
    if target:
        await _audit(
            session,
            tenant_id=tenant_id,
            actor_id=actor_id,
            operation="review.calculated",
            detail={"review_id": str(review.id), "overall": result.overall, "passed": result.passed},
        )
    return review


async def submit_review(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    review_id: uuid.UUID,
    actor_id: uuid.UUID,
) -> QAReview:
    review = await get_review(session, tenant_id, review_id)
    await assert_environment(session, actor_id, review.environment_id)
    if review.status == "submitted":
        return review
    if review.status != "in_review":
        raise InvalidTransition(f"Cannot submit a {review.status} review")
    await bump_review(session, review, review.version, {"status": "submitted", "submitted_at": _now()})
    await _audit(
        session,
        tenant_id=tenant_id,
        actor_id=actor_id,
        operation="review.submitted",
        detail={"review_id": str(review.id)},
    )
    log.info("review.submitted", review_id=str(review.id), tenant_id=str(tenant_id))
    return await calculate_review(session, tenant_id=tenant_id, review_id=review.id, actor_id=actor_id)


async def finalize_review(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    review_id: uuid.UUID,
    actor_id: uuid.UUID,
) -> tuple[QAReview, str]:
    review = await get_review(session, tenant_id, review_id)
    await assert_environment(session, actor_id, review.environment_id)
    if review.status == "finalized":
        return review, "duplicate"
    if review.status != "calculated":
        raise InvalidTransition("Only a calculated review can be finalized")
    if not review.calculation_snapshot:
        raise InvalidTransition("Finalization needs a stored calculation")
    await bump_review(
        session,
        review,
        review.version,
        {
            "status": "finalized",
            "open_key": None,
            "finalized_at": _now(),
            "finalized_by": actor_id,
        },
    )
    await _audit(
        session,
        tenant_id=tenant_id,
        actor_id=actor_id,
        operation="review.finalized",
        detail={"review_id": str(review.id), "overall": review.overall_score},
    )
    log.info("review.finalized", review_id=str(review.id), tenant_id=str(tenant_id))
    return review, "applied"


async def reopen_review(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    review_id: uuid.UUID,
    actor_id: uuid.UUID,
) -> tuple[QAReview, str]:
    review = await get_review(session, tenant_id, review_id)
    await assert_environment(session, actor_id, review.environment_id)
    if review.status == "reopened":
        return review, "duplicate"
    if review.status != "finalized":
        raise InvalidTransition("Only a finalized review can be reopened")
    prior = list(review.prior_snapshots or [])
    if review.calculation_snapshot:
        prior.append(review.calculation_snapshot)
    await bump_review(
        session,
        review,
        review.version,
        {
            "status": "reopened",
            "open_key": f"{review.call_id}:{review.scorecard_id}",
            "prior_snapshots": prior,
        },
    )
    await _audit(
        session,
        tenant_id=tenant_id,
        actor_id=actor_id,
        operation="review.reopened",
        detail={"review_id": str(review.id)},
    )
    log.info("review.reopened", review_id=str(review.id), tenant_id=str(tenant_id))
    return review, "applied"


async def request_auto_review(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    review_id: uuid.UUID,
    actor_id: uuid.UUID,
    organization_id: uuid.UUID | None = None,
):
    review = await get_review(session, tenant_id, review_id)
    await assert_environment(session, actor_id, review.environment_id)
    return await auto_review.enqueue(
        session, tenant_id=tenant_id, review=review, organization_id=organization_id
    )


async def apply_ai_suggestion(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    review_id: uuid.UUID,
    item_id: uuid.UUID,
    actor_id: uuid.UUID,
) -> QAReviewItem:
    review = await get_review(session, tenant_id, review_id)
    if review.status == "finalized":
        raise InvalidTransition("A finalized review cannot accept a new suggestion")
    rows = {row.item_id: row for row in await review_items(session, tenant_id, review.id)}
    row = rows.get(item_id)
    if row is None or row.ai_score is None and not row.ai_na:
        raise InvalidEvidence("There is no AI suggestion for this item")
    if row.human_score is not None or row.human_na:
        raise InvalidTransition("A human score is already recorded")
    row.accepted_source = "ai"
    row.actor_id = actor_id
    row.scored_at = _now()
    await session.flush()
    return row


async def add_coaching(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    review_id: uuid.UUID,
    actor_id: uuid.UUID,
    agent_user_id: uuid.UUID,
    category: str,
    recommendation: str,
    priority: str = "normal",
    turn_id: uuid.UUID | None = None,
):
    review = await get_review(session, tenant_id, review_id)
    await assert_environment(session, actor_id, review.environment_id)
    evidence_row = None
    if turn_id is not None:
        evidence_row = await evidence.add_evidence(
            session,
            tenant_id=tenant_id,
            call_id=review.call_id,
            evidence_type="coaching",
            review=review,
            turn_id=turn_id,
            target_kind="coaching",
        )
    signal = await coaching.create_signal(
        session,
        tenant_id=tenant_id,
        call_id=review.call_id,
        agent_user_id=agent_user_id,
        category=category,
        recommendation=recommendation,
        priority=priority,
        review_id=review.id,
        evidence_id=None if evidence_row is None else evidence_row.id,
    )
    await _audit(
        session,
        tenant_id=tenant_id,
        actor_id=actor_id,
        operation="coaching.created",
        detail={"coaching_id": str(signal.id), "review_id": str(review.id)},
    )
    log.info("coaching.created", coaching_id=str(signal.id), tenant_id=str(tenant_id))
    return signal


# ----------------------------------------- Post-Call Sampling Policies & Exposure


async def evaluate_post_call_sampling_policy(
    session: AsyncSession,
    *,
    call: Call,
    policy: dict,
) -> tuple[bool, str]:
    """Evaluate a post-call QA sampling policy for ``call`` (1C / Part 6).

    Supported policy kinds:
    - ``percentage``: deterministic sha256 bucket ``< percent * 100``
    - ``negative_sentiment``: persisted ``SentimentResult(label='negative')`` or negative post-call sentiment step
    - ``transfers``: ``call.escalated``, ``call.status == TRANSFERRED``, or ``call.transfer_state != NONE``
    - ``long_calls``: ``call.duration_seconds >= min_duration_seconds`` (default 300s)
    """
    from app.db.models import CallStatus, TransferState
    from app.db.telephony_models import PostCallStepRun
    from app.qa import sampling
    from app.qa.models import SentimentResult

    kind = str(policy.get("kind") or "percentage").strip().lower()
    rule_uuid = uuid.UUID(str(policy["rule_id"])) if policy.get("rule_id") else call.id
    window_key = str(policy.get("window_key") or "post-call:v1")

    if kind == "percentage":
        pct = int(policy.get("percent", 100))
        if pct <= 0:
            return False, "percent_zero"
        if pct >= 100:
            return True, "percent_100"
        b = sampling.bucket(call.tenant_id, window_key, rule_uuid, call.id)
        matched = b < (pct * 100)
        return matched, f"percentage_bucket:{b}<{pct * 100}"

    if kind in {"negative_sentiment", "sentiment_negative"}:
        sent_row = await session.scalar(
            select(SentimentResult)
            .where(
                SentimentResult.tenant_id == call.tenant_id,
                SentimentResult.call_id == call.id,
            )
            .order_by(SentimentResult.created_at.desc())
        )
        if sent_row is not None and str(sent_row.label).lower() == "negative":
            return True, "negative_sentiment_result"
        step_row = await session.scalar(
            select(PostCallStepRun).where(
                PostCallStepRun.tenant_id == call.tenant_id,
                PostCallStepRun.call_id == call.id,
                PostCallStepRun.step == "sentiment",
            )
        )
        if step_row is not None and isinstance(step_row.output, dict):
            lbl = str(step_row.output.get("label") or step_row.output.get("sentiment") or "").lower()
            score = step_row.output.get("score")
            if lbl == "negative" or (isinstance(score, (int, float)) and float(score) < -0.1):
                return True, "negative_sentiment_step"
        return False, "sentiment_not_negative"

    if kind in {"transfers", "transfer", "transferred"}:
        status_val = call.status.value if hasattr(call.status, "value") else str(call.status)
        t_state = (
            call.transfer_state.value
            if hasattr(call.transfer_state, "value")
            else str(call.transfer_state)
        )
        if (
            bool(call.escalated)
            or status_val == CallStatus.TRANSFERRED.value
            or t_state != TransferState.NONE.value
        ):
            return True, "call_transferred_or_escalated"
        return False, "no_transfer"

    if kind in {"long_calls", "long_call"}:
        min_dur = float(policy.get("min_duration_seconds", 300.0))
        dur = float(call.duration_seconds or 0.0)
        return dur >= min_dur, f"duration:{dur}>={min_dur}"

    return False, f"unsupported_policy:{kind}"


async def admit_post_call_with_policies(
    session: AsyncSession,
    call: Call,
    *,
    organization_id: uuid.UUID | None = None,
    scorecard_id: uuid.UUID | None = None,
    policies: list[dict] | None = None,
) -> dict:
    """Admit a terminal call to QA auto-review using canonical percentage rules + targeted policies."""
    from app.qa import sampling

    base_admission = await sampling.admit_post_call(
        session, call, organization_id=organization_id
    )
    policy_admissions = []
    if policies and scorecard_id is not None:
        for idx, pol in enumerate(policies):
            matched, reason = await evaluate_post_call_sampling_policy(
                session, call=call, policy=pol
            )
            if not matched:
                continue
            review, _ = await create_review(
                session,
                tenant_id=call.tenant_id,
                call_id=call.id,
                scorecard_id=scorecard_id,
                actor_id=None,
                environment_id=call.environment_id,
                idempotency_key=f"post-call-policy:{call.id}:{scorecard_id}:{pol.get('kind', idx)}",
            )
            if review.status in {"finalized", "cancelled"}:
                policy_admissions.append(
                    {
                        "policy_kind": pol.get("kind"),
                        "reason": reason,
                        "review_id": str(review.id),
                        "status": review.status,
                    }
                )
                continue
            run, _ = await auto_review.enqueue(
                session,
                tenant_id=call.tenant_id,
                review=review,
                organization_id=organization_id,
            )
            policy_admissions.append(
                {
                    "policy_kind": pol.get("kind"),
                    "reason": reason,
                    "review_id": str(review.id),
                    "run_id": str(run.id),
                    "job_id": str(run.job_id),
                    "status": run.status,
                }
            )
        await session.flush()

    return {
        **base_admission,
        "policy_admissions": policy_admissions,
    }


async def get_call_qa_dashboard_summary(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    call_id: uuid.UUID,
) -> dict:
    """Expose QA reviews, rubric item scores, auto-review runs, compliance findings, and sentiment for dashboard & webhooks."""
    from app.qa.models import AutoReviewRun, ComplianceFinding, SentimentResult

    call = await evidence.load_call(session, tenant_id, call_id)
    reviews = list(
        (
            await session.execute(
                select(QAReview)
                .where(QAReview.tenant_id == tenant_id, QAReview.call_id == call.id)
                .order_by(QAReview.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    reviews_payload = []
    for rev in reviews:
        items = await review_items(session, tenant_id, rev.id)
        auto_runs = list(
            (
                await session.execute(
                    select(AutoReviewRun)
                    .where(
                        AutoReviewRun.tenant_id == tenant_id,
                        AutoReviewRun.review_id == rev.id,
                    )
                    .order_by(AutoReviewRun.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        reviews_payload.append(
            {
                **rev.as_dict(),
                "items": [it.as_dict() for it in items],
                "auto_review_runs": [ar.as_dict() for ar in auto_runs],
            }
        )

    findings = list(
        (
            await session.execute(
                select(ComplianceFinding).where(
                    ComplianceFinding.tenant_id == tenant_id,
                    ComplianceFinding.call_id == call.id,
                )
            )
        )
        .scalars()
        .all()
    )
    sentiment_rows = list(
        (
            await session.execute(
                select(SentimentResult)
                .where(
                    SentimentResult.tenant_id == tenant_id,
                    SentimentResult.call_id == call.id,
                )
                .order_by(SentimentResult.created_at.desc())
            )
        )
        .scalars()
        .all()
    )

    latest_score = next(
        (r.overall_score for r in reviews if r.overall_score is not None), None
    )
    latest_pass = next((r.passed for r in reviews if r.passed is not None), None)

    return {
        "call_id": str(call.id),
        "tenant_id": str(tenant_id),
        "overall_score": latest_score,
        "passed": latest_pass,
        "review_count": len(reviews_payload),
        "reviews": reviews_payload,
        "compliance_findings": [f.as_dict() for f in findings],
        "sentiment": [s.as_dict() for s in sentiment_rows],
    }

