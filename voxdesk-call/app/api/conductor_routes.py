"""FastAPI routes for the Conductor AI Control Plane (`/api/v1/conductor`).

Exposes:
- Session creation, listing, and retrieval
- Scoped context assembly
- Natural-language / structured proposal creation
- Proposal retrieval, changes, deterministic diff, validation, simulation, and evidence
- Granular per-change and per-proposal approve, reject, undo, and transactional apply
- Resulting immutable AgentVersion lookup
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from typing import Any, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import Permission, TenantContext, require_permission
from app.core.errors import AppError
from app.db.session import get_session
from app.domain.conductor_models import (
    ConductorApplyProposalRequest,
    ConductorApplyResultResponse,
    ConductorChangeResponse,
    ConductorChangeReviewRequest,
    ConductorEvidenceResponse,
    ConductorPromptRequest,
    ConductorProposalDiffResponse,
    ConductorProposalResponse,
    ConductorProposalReviewRequest,
    ConductorSessionCreateRequest,
    ConductorSessionResponse,
    ConductorSimulateProposalRequest,
)
from app.security.policy import ConductorAction, enforce_conductor_permission
from app.services import conductor_context_service, conductor_service

router = APIRouter(prefix="/api/v1/conductor", tags=["conductor"])


def _raise_http(exc: AppError) -> NoReturn:
    raise HTTPException(
        status_code=getattr(exc, "status_code", getattr(exc, "http_status", 400)),
        detail={
            "code": str(getattr(exc.code, "value", exc.code)),
            "message": exc.message,
            "details": getattr(exc, "detail", getattr(exc, "details", None)),
        },
    ) from exc


@asynccontextmanager
async def _operation(
    session: AsyncSession,
    ctx: TenantContext,
    action: ConductorAction,
    *,
    commit: bool = False,
) -> AsyncIterator[None]:
    """Preserve route-specific policy and atomic commit/rollback in one boundary."""
    try:
        enforce_conductor_permission(ctx, action)
        yield
        if commit:
            await session.commit()
    except AppError as exc:
        if commit:
            await session.rollback()
        _raise_http(exc)


# -------------------------------------------------------------------- Sessions


@router.post(
    "/sessions",
    response_model=ConductorSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_session_endpoint(
    payload: ConductorSessionCreateRequest,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> ConductorSessionResponse:
    async with _operation(session, ctx, ConductorAction.VIEW_CONDUCTOR, commit=True):
        resp = await conductor_service.create_conductor_session(
            session,
            tenant_id=ctx.tenant_id,
            payload=payload,
            actor_user_id=ctx.user_id,
        )
        return resp


@router.get("/sessions", response_model=list[ConductorSessionResponse])
async def list_sessions_endpoint(
    agent_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> list[ConductorSessionResponse]:
    async with _operation(session, ctx, ConductorAction.VIEW_CONDUCTOR, commit=False):
        return await conductor_service.list_conductor_sessions(
            session,
            ctx.tenant_id,
            agent_id=agent_id,
            limit=limit,
        )


@router.get("/sessions/{session_id}", response_model=ConductorSessionResponse)
async def get_session_endpoint(
    session_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> ConductorSessionResponse:
    async with _operation(session, ctx, ConductorAction.VIEW_CONDUCTOR, commit=False):
        return await conductor_service.get_conductor_session(
            session, ctx.tenant_id, session_id
        )


@router.get("/context")
async def get_scoped_context_endpoint(
    agent_id: str = Query(..., min_length=1),
    agent_kind: str = Query(default="voice"),
    base_version_number: int | None = Query(default=None, ge=1),
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    async with _operation(session, ctx, ConductorAction.VIEW_CONDUCTOR, commit=False):
        return await conductor_context_service.assemble_conductor_context(
            session,
            tenant_id=ctx.tenant_id,
            agent_id=agent_id,
            agent_kind=agent_kind,
            base_version_number=base_version_number,
        )


# ------------------------------------------------------------------- Proposals


@router.post(
    "/proposals",
    response_model=ConductorProposalResponse,
    status_code=status.HTTP_201_CREATED,
)
async def submit_conductor_request_endpoint(
    payload: ConductorPromptRequest,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> ConductorProposalResponse:
    async with _operation(session, ctx, ConductorAction.CREATE_PROPOSAL, commit=True):
        resp = await conductor_service.create_proposal_from_request(
            session,
            tenant_id=ctx.tenant_id,
            payload=payload,
            actor_user_id=ctx.user_id,
        )
        return resp


@router.get("/proposals", response_model=list[ConductorProposalResponse])
async def list_proposals_endpoint(
    agent_id: str | None = Query(default=None),
    session_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> list[ConductorProposalResponse]:
    async with _operation(session, ctx, ConductorAction.VIEW_CONDUCTOR, commit=False):
        return await conductor_service.list_proposals(
            session,
            ctx.tenant_id,
            agent_id=agent_id,
            session_id=session_id,
            limit=limit,
        )


@router.get("/proposals/{proposal_id}", response_model=ConductorProposalResponse)
async def get_proposal_endpoint(
    proposal_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> ConductorProposalResponse:
    async with _operation(session, ctx, ConductorAction.VIEW_CONDUCTOR, commit=True):
        resp = await conductor_service.get_proposal(
            session, ctx.tenant_id, proposal_id
        )
        return resp


@router.get(
    "/proposals/{proposal_id}/changes",
    response_model=list[ConductorChangeResponse],
)
async def get_proposal_changes_endpoint(
    proposal_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> list[ConductorChangeResponse]:
    async with _operation(session, ctx, ConductorAction.VIEW_CONDUCTOR, commit=False):
        rows = await conductor_service.get_proposal_changes(
            session, ctx.tenant_id, proposal_id
        )
        return [ConductorChangeResponse.model_validate(r) for r in rows]


@router.get(
    "/proposals/{proposal_id}/diff",
    response_model=ConductorProposalDiffResponse,
)
async def get_proposal_diff_endpoint(
    proposal_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> ConductorProposalDiffResponse:
    async with _operation(session, ctx, ConductorAction.VIEW_CONDUCTOR, commit=False):
        return await conductor_service.get_proposal_diff(
            session, ctx.tenant_id, proposal_id
        )


@router.get(
    "/proposals/{proposal_id}/evidence",
    response_model=list[ConductorEvidenceResponse],
)
async def list_proposal_evidence_endpoint(
    proposal_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> list[ConductorEvidenceResponse]:
    async with _operation(session, ctx, ConductorAction.VIEW_CONDUCTOR, commit=False):
        await conductor_service.get_proposal(session, ctx.tenant_id, proposal_id)
        rows = await conductor_service.list_proposal_evidence(
            session, ctx.tenant_id, proposal_id
        )
        return [ConductorEvidenceResponse.model_validate(r) for r in rows]


# --------------------------------------------------- Validation & Simulation


@router.post(
    "/proposals/{proposal_id}/validate",
    response_model=ConductorProposalResponse,
)
async def validate_proposal_endpoint(
    proposal_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> ConductorProposalResponse:
    async with _operation(session, ctx, ConductorAction.CREATE_PROPOSAL, commit=True):
        resp = await conductor_service.validate_proposal(
            session,
            tenant_id=ctx.tenant_id,
            proposal_id=proposal_id,
            actor_user_id=ctx.user_id,
        )
        return resp


@router.post(
    "/proposals/{proposal_id}/simulate",
    response_model=ConductorProposalResponse,
)
async def simulate_proposal_endpoint(
    proposal_id: uuid.UUID,
    payload: ConductorSimulateProposalRequest | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> ConductorProposalResponse:
    async with _operation(session, ctx, ConductorAction.CREATE_PROPOSAL, commit=True):
        resp = await conductor_service.simulate_proposal(
            session,
            tenant_id=ctx.tenant_id,
            proposal_id=proposal_id,
            payload=payload or ConductorSimulateProposalRequest(),
            actor_user_id=ctx.user_id,
        )
        return resp


# ------------------------------------------- Human Review, Undo, & Safe Apply


@router.post(
    "/proposals/{proposal_id}/changes/{change_id}/approve",
    response_model=ConductorProposalResponse,
)
async def approve_change_endpoint(
    proposal_id: uuid.UUID,
    change_id: uuid.UUID,
    payload: ConductorChangeReviewRequest | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> ConductorProposalResponse:
    async with _operation(session, ctx, ConductorAction.APPROVE_PROPOSAL, commit=True):
        resp = await conductor_service.approve_one_change(
            session,
            tenant_id=ctx.tenant_id,
            proposal_id=proposal_id,
            change_id=change_id,
            actor_user_id=ctx.user_id,
            reason=(payload.reason if payload else ""),
        )
        return resp


@router.post(
    "/proposals/{proposal_id}/changes/{change_id}/reject",
    response_model=ConductorProposalResponse,
)
async def reject_change_endpoint(
    proposal_id: uuid.UUID,
    change_id: uuid.UUID,
    payload: ConductorChangeReviewRequest | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> ConductorProposalResponse:
    async with _operation(session, ctx, ConductorAction.REJECT_PROPOSAL, commit=True):
        resp = await conductor_service.reject_one_change(
            session,
            tenant_id=ctx.tenant_id,
            proposal_id=proposal_id,
            change_id=change_id,
            actor_user_id=ctx.user_id,
            reason=(payload.reason if payload else ""),
        )
        return resp


@router.post(
    "/proposals/{proposal_id}/changes/{change_id}/undo",
    response_model=ConductorProposalResponse,
)
async def undo_change_endpoint(
    proposal_id: uuid.UUID,
    change_id: uuid.UUID,
    payload: ConductorChangeReviewRequest | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> ConductorProposalResponse:
    async with _operation(session, ctx, ConductorAction.APPROVE_PROPOSAL, commit=True):
        resp = await conductor_service.undo_one_change(
            session,
            tenant_id=ctx.tenant_id,
            proposal_id=proposal_id,
            change_id=change_id,
            actor_user_id=ctx.user_id,
            reason=(payload.reason if payload else ""),
        )
        return resp


@router.post(
    "/proposals/{proposal_id}/approve",
    response_model=ConductorProposalResponse,
)
async def approve_proposal_endpoint(
    proposal_id: uuid.UUID,
    payload: ConductorProposalReviewRequest | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> ConductorProposalResponse:
    async with _operation(session, ctx, ConductorAction.APPROVE_PROPOSAL, commit=True):
        resp = await conductor_service.approve_proposal(
            session,
            tenant_id=ctx.tenant_id,
            proposal_id=proposal_id,
            actor_user_id=ctx.user_id,
            reason=(payload.reason if payload else ""),
            safe_only=bool(payload.safe_only if payload else False),
        )
        return resp


@router.post(
    "/proposals/{proposal_id}/reject",
    response_model=ConductorProposalResponse,
)
async def reject_proposal_endpoint(
    proposal_id: uuid.UUID,
    payload: ConductorProposalReviewRequest | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> ConductorProposalResponse:
    async with _operation(session, ctx, ConductorAction.REJECT_PROPOSAL, commit=True):
        resp = await conductor_service.reject_proposal(
            session,
            tenant_id=ctx.tenant_id,
            proposal_id=proposal_id,
            actor_user_id=ctx.user_id,
            reason=(payload.reason if payload else ""),
        )
        return resp


@router.post(
    "/proposals/{proposal_id}/undo",
    response_model=ConductorProposalResponse,
)
async def undo_proposal_endpoint(
    proposal_id: uuid.UUID,
    payload: ConductorProposalReviewRequest | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> ConductorProposalResponse:
    async with _operation(session, ctx, ConductorAction.APPROVE_PROPOSAL, commit=True):
        resp = await conductor_service.undo_proposal(
            session,
            tenant_id=ctx.tenant_id,
            proposal_id=proposal_id,
            actor_user_id=ctx.user_id,
            reason=(payload.reason if payload else ""),
        )
        return resp


@router.post(
    "/proposals/{proposal_id}/apply",
    response_model=ConductorApplyResultResponse,
)
async def apply_proposal_endpoint(
    proposal_id: uuid.UUID,
    payload: ConductorApplyProposalRequest | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> ConductorApplyResultResponse:
    async with _operation(session, ctx, ConductorAction.APPLY_PROPOSAL, commit=True):
        resp = await conductor_service.apply_proposal(
            session,
            tenant_id=ctx.tenant_id,
            proposal_id=proposal_id,
            payload=payload or ConductorApplyProposalRequest(),
            actor_user_id=ctx.user_id,
        )
        return resp


@router.get("/proposals/{proposal_id}/resulting-version")
async def get_resulting_version_endpoint(
    proposal_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    async with _operation(session, ctx, ConductorAction.VIEW_CONDUCTOR, commit=False):
        return await conductor_service.get_proposal_resulting_version(
            session, ctx.tenant_id, proposal_id
        )


# ===========================================================================
# PART 15 (P15-11) — Conductor on every page
#
# The panel mounts on every dashboard page, so the backend owes it three
# things the proposal flow did not need:
#   * what the page is about (so the copilot does not ask "which call?"),
#   * an investigation over the rows the user is looking at,
#   * a chart built from real aggregates, never from a plausible-looking series.
# Plus attachments on a proposal, stored as evidence so they travel with the
# change that cites them.
# ===========================================================================

from pydantic import BaseModel, Field  # noqa: E402

from app.services import analytics_service as _analytics  # noqa: E402

#: Page registry. The panel is only as page-aware as this table, and that is the
#: point: an unknown page gets a generic context instead of an invented one.
PAGE_CONTEXT: dict[str, dict[str, Any]] = {
    "/dashboard/live": {
        "surface": "live-calls",
        "entity": "call",
        "prompts": [
            "Which live calls are at risk right now?",
            "Why did the score drop on the lowest-scoring call?",
        ],
        "actions": ["open_call", "whisper", "takeover"],
    },
    "/dashboard/calls": {
        "surface": "call-history",
        "entity": "call",
        "prompts": [
            "What do these calls have in common?",
            "Group these results by the issue they hit.",
        ],
        "actions": ["open_call", "export", "save_view"],
    },
    "/dashboard/qa": {
        "surface": "qa",
        "entity": "review",
        "prompts": ["What are the repeat failures this week?", "Which agent needs coaching?"],
        "actions": ["open_review", "assign"],
    },
    "/dashboard/alerts": {
        "surface": "alerts",
        "entity": "alert_rule",
        "prompts": ["Which rule fired most often in the last day?", "Is this threshold too tight?"],
        "actions": ["edit_rule", "test_fire"],
    },
    "/dashboard/anomaly": {
        "surface": "anomaly",
        "entity": "detector",
        "prompts": ["Which detector should become an alert rule?"],
        "actions": ["promote_to_alert"],
    },
    "/dashboard/analytics": {
        "surface": "analytics",
        "entity": "chart",
        "prompts": ["Build a chart of failures by agent this week."],
        "actions": ["build_chart"],
    },
}

DEFAULT_PAGE_CONTEXT: dict[str, Any] = {
    "surface": "unknown",
    "entity": None,
    "prompts": ["What should I look at on this page?"],
    "actions": [],
}


class InvestigateBody(BaseModel):
    call_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)
    question: str = Field(default="", max_length=500)


class BuildChartBody(BaseModel):
    metric: str = Field(default="calls")
    group_by: str = Field(default="day", max_length=80)
    start: str | None = None
    end: str | None = None
    filters: dict[str, Any] = Field(default_factory=dict)
    max_groups: int = Field(default=20, ge=1, le=100)


class AttachmentBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=1, max_length=2000)
    kind: str = Field(default="link", max_length=32)
    note: str = Field(default="", max_length=500)


@router.get("/page-context")
async def get_page_context(
    path: str = Query(default="/", max_length=200),
    agent_id: str | None = Query(default=None, max_length=120),
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
) -> dict[str, Any]:
    """What the panel knows about the page it is mounted on.

    Longest-prefix match, so `/dashboard/calls/123` inherits the call-history
    context rather than falling back to the generic one.
    """
    clean = "/" + path.lstrip("/").split("?")[0].rstrip("/")
    match = None
    for prefix in sorted(PAGE_CONTEXT, key=len, reverse=True):
        if clean == prefix or clean.startswith(prefix + "/"):
            match = prefix
            break
    context = dict(PAGE_CONTEXT.get(match, DEFAULT_PAGE_CONTEXT)) if match else dict(DEFAULT_PAGE_CONTEXT)
    context["path"] = clean
    context["matched_prefix"] = match
    context["agent_id"] = agent_id
    context["tenant_id"] = str(ctx.tenant_id)
    return context


@router.post("/investigate-calls")
async def investigate_calls(
    payload: InvestigateBody,
    ctx: TenantContext = Depends(require_permission(Permission.CALL_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """"What do these failures have in common?"

    The answer is a cluster list computed from the selected calls' own facets
    (intent, failure reason, transfer reason, agent). Calls from another tenant
    are dropped before analysis, and the response says how many were dropped --
    a silently smaller set would make the cluster sizes wrong without saying so.
    """
    from sqlalchemy import select as _select

    from app.db.models import Call as _Call
    from app.qa import analyst as _analyst

    rows = (
        await session.scalars(
            _select(_Call).where(
                _Call.tenant_id == ctx.tenant_id, _Call.id.in_(payload.call_ids)
            )
        )
    ).all()
    found_ids = {row.id for row in rows}
    ignored = [str(call_id) for call_id in payload.call_ids if call_id not in found_ids]

    issues = [
        _analyst.IssueItem(call_id=row.id, text=_analyst.facet_text(row))
        for row in rows
        if not _analyst.is_resolved(row)
    ]
    clusters = _analyst.cluster_issues(issues)
    unresolved = sum(1 for row in rows if not _analyst.is_resolved(row))
    return {
        "question": payload.question,
        "calls_considered": len(rows),
        "unresolved": unresolved,
        "ignored_call_ids": ignored,
        "clusters": [cluster.as_dict() for cluster in clusters],
        "summary": (
            f"{len(clusters)} group(s) across {unresolved} unresolved call(s)"
            if issues
            else "no unresolved calls in the selection"
        ),
        "explanation_source": "call facets: intent, failure reason, transfer reason, agent",
    }


@router.post("/build-chart")
async def build_chart(
    payload: BuildChartBody,
    ctx: TenantContext = Depends(require_permission(Permission.ANALYTICS_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Turn a plain request into a chart spec backed by a real aggregate."""
    from datetime import datetime as _dt

    def _parse(value: str | None) -> _dt | None:
        if not value:
            return None
        try:
            return _dt.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None

    start, end = _parse(payload.start), _parse(payload.end)
    if payload.group_by.startswith(_analytics.ANALYSIS_GROUP_PREFIX):
        data = await _analytics.group_by_analysis_field(
            session,
            ctx.tenant_id,
            payload.group_by[len(_analytics.ANALYSIS_GROUP_PREFIX) :],
            start=start,
            end=end,
            max_groups=payload.max_groups,
        )
        points = [{"label": group["value"], "value": group["calls"]} for group in data["groups"]]
        spec = {"x": "value", "y": "calls", "kind": "bar"}
    else:
        data = await _analytics.compute_metrics_and_breakdown(
            session,
            ctx.tenant_id,
            start=start,
            end=end,
            group_by=payload.group_by,
            filters=payload.filters,
            max_groups=payload.max_groups,
        )
        groups = data.get("groups") or []
        points = [
            {"label": str(group.get("key") or group.get("day") or ""), "value": group.get("calls", 0)}
            for group in groups
        ]
        spec = {"x": "label", "y": "calls", "kind": "line"}
    return {
        "chart": {
            "title": f"{payload.metric} by {payload.group_by}",
            "spec": spec,
            "points": points,
            "series": [{"name": payload.metric, "values": [p["value"] for p in points]}],
            "group_by": payload.group_by,
        },
        "source": "aggregated from stored rows; no interpolated points",
        "raw_group_count": len(points),
    }


@router.post("/proposals/{proposal_id}/attachments", status_code=status.HTTP_201_CREATED)
async def add_attachment(
    proposal_id: uuid.UUID,
    payload: AttachmentBody,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Attach a link or document reference to a proposal.

    Stored as Conductor evidence with `source_type="attachment"`, so an approved
    change carries the material it was justified with instead of a URL that
    lived in a chat message.
    """
    from app.db.retell_models import ConductorEvidence

    async with _operation(session, ctx, ConductorAction.CREATE_PROPOSAL, commit=True):
        row = ConductorEvidence(
            proposal_id=proposal_id,
            tenant_id=ctx.tenant_id,
            source_type="attachment",
            source_id=payload.url[:120],
            evidence_summary=f"{payload.kind}: {payload.name}"[:300],
            evidence_payload={
                "name": payload.name,
                "url": payload.url,
                "kind": payload.kind,
                "note": payload.note,
            },
        )
        session.add(row)
        await session.flush()
        return {
            "id": str(row.id),
            "proposal_id": str(proposal_id),
            "name": payload.name,
            "kind": payload.kind,
            "url": payload.url,
        }
