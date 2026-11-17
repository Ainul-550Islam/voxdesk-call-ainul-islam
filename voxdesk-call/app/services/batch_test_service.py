# File: app/services/batch_test_service.py — define cases, run them in bulk, import/export
"""Batch testing (PART 15, P15-08).

A batch run is a *job*, not a request: the API returns a run row immediately and
the caller polls it. That shape is deliberate -- twenty cases against a real
model take minutes, and a request that blocks for minutes is a request that
times out halfway through with no record of how far it got.

Three rules the implementation keeps:

1. **A case passes only if it actually ran.** An executor that raised produces a
   ``failed`` result with the error, never a silent skip, and never a pass.
2. **The run's totals are stored on the run.** A completed run stays readable
   after its per-case rows are pruned, and a run whose totals disagree with its
   results is a bug the report can see.
3. **Import is idempotent per case name.** Re-importing the same JSON updates the
   case instead of duplicating it, because the alternative is two silently
   diverging copies of "refund request".

The executor is injectable: production wires it to the same pinned-config
simulation the playground uses; the tests inject a deterministic one, so the
suite tests this module rather than the model.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import log
from app.db.monitoring_models import BatchTestCase, BatchTestResult, BatchTestRun

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

MAX_CASES_PER_IMPORT = 500


class BatchTestError(Exception):
    """Raised for a request the service refuses to guess at."""

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass(frozen=True, slots=True)
class CaseOutcome:
    """What the executor returned for one case."""

    reply: str
    latency_ms: float | None = None
    error: str | None = None


#: An executor turns a case into a reply. Real ones call the agent; tests inject
#: a deterministic one.
CaseExecutor = Callable[[BatchTestCase], Awaitable[CaseOutcome]]


@dataclass(frozen=True, slots=True)
class Verdict:
    passed: bool
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "failure_reason": self.reason}


def check_case(case: BatchTestCase, outcome: CaseOutcome) -> Verdict:
    """Apply the case's assertions to one reply.

    Order matters for the failure reason a human reads: an executor error is
    reported first, then the forbidden phrases, then the expected ones, then the
    latency budget.
    """
    if outcome.error:
        return Verdict(False, f"executor_error:{outcome.error}"[:300])
    reply = (outcome.reply or "").strip()
    if not reply:
        return Verdict(False, "empty_reply")
    lowered = reply.lower()
    for phrase in case.forbidden_contains or []:
        if str(phrase).strip() and str(phrase).lower() in lowered:
            return Verdict(False, f"forbidden_phrase:{phrase}"[:300])
    for phrase in case.expected_contains or []:
        if str(phrase).strip() and str(phrase).lower() not in lowered:
            return Verdict(False, f"missing_phrase:{phrase}"[:300])
    if case.max_latency_ms is not None and outcome.latency_ms is not None:
        if float(outcome.latency_ms) > float(case.max_latency_ms):
            return Verdict(
                False,
                f"latency:{int(outcome.latency_ms)}ms>{int(case.max_latency_ms)}ms"[:300],
            )
    return Verdict(True)


# --------------------------------------------------------------------------
# Case CRUD
# --------------------------------------------------------------------------


def _clean_phrases(values: Sequence[Any] | None) -> list[str]:
    out: list[str] = []
    for value in values or ():
        text = str(value).strip()
        if text and text not in out:
            out.append(text)
    return out


async def get_case(
    session: AsyncSession, tenant_id: uuid.UUID, agent_id: str, name: str
) -> BatchTestCase | None:
    return await session.scalar(
        select(BatchTestCase).where(
            BatchTestCase.tenant_id == tenant_id,
            BatchTestCase.agent_id == agent_id,
            BatchTestCase.name == name,
        )
    )


async def upsert_case(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    agent_id: str,
    name: str,
    user_message: str,
    expected_contains: Sequence[Any] | None = None,
    forbidden_contains: Sequence[Any] | None = None,
    max_latency_ms: int | None = None,
    agent_version_number: int | None = None,
    enabled: bool = True,
) -> tuple[BatchTestCase, str]:
    """Create or update one case. Returns ``(row, "created"|"updated")``."""
    clean_name = (name or "").strip()
    if not clean_name:
        raise BatchTestError("invalid_case", "case name is required", 422)
    message = (user_message or "").strip()
    if not message:
        raise BatchTestError("invalid_case", f"case {clean_name!r} has no user_message", 422)

    row = await get_case(session, tenant_id, agent_id, clean_name)
    action = "updated"
    if row is None:
        row = BatchTestCase(tenant_id=tenant_id, agent_id=agent_id, name=clean_name)
        session.add(row)
        action = "created"
    row.user_message = message
    row.expected_contains = _clean_phrases(expected_contains)
    row.forbidden_contains = _clean_phrases(forbidden_contains)
    row.max_latency_ms = int(max_latency_ms) if max_latency_ms is not None else None
    row.agent_version_number = (
        int(agent_version_number) if agent_version_number is not None else row.agent_version_number
    )
    row.enabled = bool(enabled)
    await session.flush()
    return row, action


async def list_cases(
    session: AsyncSession, tenant_id: uuid.UUID, agent_id: str, *, enabled_only: bool = False
) -> list[BatchTestCase]:
    query = select(BatchTestCase).where(
        BatchTestCase.tenant_id == tenant_id, BatchTestCase.agent_id == agent_id
    )
    if enabled_only:
        query = query.where(BatchTestCase.enabled.is_(True))
    return list((await session.scalars(query.order_by(BatchTestCase.name))).all())


async def delete_case(session: AsyncSession, tenant_id: uuid.UUID, case_id: uuid.UUID) -> bool:
    row = await session.get(BatchTestCase, case_id)
    if row is None or row.tenant_id != tenant_id:
        return False
    await session.delete(row)
    await session.flush()
    return True


def case_as_dict(case: BatchTestCase) -> dict[str, Any]:
    return {
        "id": str(case.id),
        "agent_id": case.agent_id,
        "agent_version_number": case.agent_version_number,
        "name": case.name,
        "user_message": case.user_message,
        "expected_contains": list(case.expected_contains or []),
        "forbidden_contains": list(case.forbidden_contains or []),
        "max_latency_ms": case.max_latency_ms,
        "enabled": bool(case.enabled),
    }


# --------------------------------------------------------------------------
# Import / export
# --------------------------------------------------------------------------


@dataclass
class ImportReport:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "created": len(self.created),
            "updated": len(self.updated),
            "rejected": len(self.errors),
            "created_names": list(self.created),
            "updated_names": list(self.updated),
            "errors": list(self.errors),
        }


def _case_payloads(payload: Any) -> list[Mapping[str, Any]]:
    """Accept ``[...]``, ``{"cases": [...]}`` or ``{"cases": {...}}``."""
    if isinstance(payload, Mapping):
        payload = payload.get("cases", [])
    if isinstance(payload, Mapping):
        payload = list(payload.values())
    if not isinstance(payload, list):
        raise BatchTestError("invalid_payload", "expected a list of cases", 422)
    if len(payload) > MAX_CASES_PER_IMPORT:
        raise BatchTestError(
            "too_many_cases",
            f"at most {MAX_CASES_PER_IMPORT} cases per import, got {len(payload)}",
            413,
        )
    return [item for item in payload if isinstance(item, Mapping)]


async def import_cases(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    agent_id: str,
    payload: Any,
    agent_version_number: int | None = None,
) -> ImportReport:
    """Import a case set. One bad case does not abandon the good ones."""
    report = ImportReport()
    for index, item in enumerate(_case_payloads(payload)):
        name = str(item.get("name") or "").strip()
        try:
            _row, action = await upsert_case(
                session,
                tenant_id,
                agent_id=agent_id,
                name=name,
                user_message=str(item.get("user_message") or item.get("message") or ""),
                expected_contains=item.get("expected_contains") or item.get("expect") or [],
                forbidden_contains=item.get("forbidden_contains") or item.get("forbid") or [],
                max_latency_ms=item.get("max_latency_ms"),
                agent_version_number=agent_version_number
                if agent_version_number is not None
                else item.get("agent_version_number"),
                enabled=bool(item.get("enabled", True)),
            )
        except BatchTestError as exc:
            report.errors.append({"index": str(index), "name": name, "error": exc.message})
            continue
        (report.created if action == "created" else report.updated).append(_row.name)
    return report


async def export_cases(
    session: AsyncSession, tenant_id: uuid.UUID, agent_id: str
) -> dict[str, Any]:
    """The same shape :func:`import_cases` accepts, so the round trip is exact."""
    cases = await list_cases(session, tenant_id, agent_id)
    return {
        "version": 1,
        "agent_id": agent_id,
        "cases": [
            {
                "name": case.name,
                "user_message": case.user_message,
                "expected_contains": list(case.expected_contains or []),
                "forbidden_contains": list(case.forbidden_contains or []),
                "max_latency_ms": case.max_latency_ms,
                "agent_version_number": case.agent_version_number,
                "enabled": bool(case.enabled),
            }
            for case in cases
        ],
    }


async def copy_cases(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    from_agent_id: str,
    to_agent_id: str,
) -> int:
    """Copy a case set onto another agent (the duplicate-agent path).

    ``to_agent_id`` names are updated in place, so duplicating twice does not
    produce "Refund request (2)".
    """
    if from_agent_id == to_agent_id:
        return 0
    source = await list_cases(session, tenant_id, from_agent_id)
    for case in source:
        await upsert_case(
            session,
            tenant_id,
            agent_id=to_agent_id,
            name=case.name,
            user_message=case.user_message,
            expected_contains=case.expected_contains,
            forbidden_contains=case.forbidden_contains,
            max_latency_ms=case.max_latency_ms,
            agent_version_number=case.agent_version_number,
            enabled=bool(case.enabled),
        )
    return len(source)


# --------------------------------------------------------------------------
# Runs
# --------------------------------------------------------------------------


async def default_executor(session: AsyncSession, tenant_id: uuid.UUID) -> CaseExecutor:
    """Executor backed by the pinned-config simulation the playground uses.

    It is created per run so one session is reused across the whole batch; a
    per-case session would serialise the run behind connection acquisition.
    """
    from app.domain.evaluation_models import LLMPlaygroundRunRequest
    from app.services import simulation_service

    async def run(case: BatchTestCase) -> CaseOutcome:
        if case.agent_version_number is None:
            return CaseOutcome(reply="", error="agent_version_not_pinned")
        payload = LLMPlaygroundRunRequest(
            agent_id=case.agent_id,
            agent_version_number=int(case.agent_version_number),
            user_message=case.user_message,
        )
        started = _now()
        try:
            response = await simulation_service.run_llm_playground(
                session, tenant_id, payload
            )
        except Exception as exc:  # the run records the error; it does not die
            return CaseOutcome(reply="", error=f"{type(exc).__name__}: {exc}"[:300])
        reply = _reply_from_response(response)
        latency = ( _now() - started).total_seconds() * 1000.0
        return CaseOutcome(reply=reply, latency_ms=round(latency, 2))

    return run


def _reply_from_response(response: Any) -> str:
    """Pull the assistant's last line out of a simulation response."""
    transcript = getattr(response, "transcript_snapshot", None) or []
    for turn in reversed(list(transcript)):
        if not isinstance(turn, Mapping):
            continue
        if str(turn.get("role", "")).lower() in ("assistant", "agent"):
            content = str(turn.get("content") or "").strip()
            if content:
                return content
    final = getattr(response, "final_output", None) or {}
    if isinstance(final, Mapping):
        for key in ("reply", "text", "content", "message"):
            value = final.get(key)
            if value:
                return str(value)
    return ""


async def start_run(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    agent_id: str,
    created_by: str | None = None,
) -> BatchTestRun:
    """Create the run row and count the cases. Execution is a separate step."""
    cases = await list_cases(session, tenant_id, agent_id, enabled_only=True)
    if not cases:
        raise BatchTestError("no_cases", f"agent {agent_id!r} has no enabled test cases", 409)
    run = BatchTestRun(
        tenant_id=tenant_id,
        agent_id=agent_id,
        status=STATUS_PENDING,
        total=len(cases),
        created_by=created_by,
    )
    session.add(run)
    await session.flush()
    log.info(
        "batch_tests.run_created",
        tenant_id=str(tenant_id),
        run_id=str(run.id),
        agent_id=agent_id,
        total=run.total,
    )
    return run


async def execute_run(
    session: AsyncSession,
    run: BatchTestRun,
    *,
    executor: CaseExecutor,
    case_ids: Sequence[uuid.UUID] | None = None,
) -> BatchTestRun:
    """Run every enabled case once and store one result per case."""
    if run.status in (STATUS_COMPLETED, STATUS_CANCELLED):
        return run
    cases = await list_cases(session, run.tenant_id, run.agent_id, enabled_only=True)
    if case_ids is not None:
        wanted = set(case_ids)
        cases = [case for case in cases if case.id in wanted]

    run.status = STATUS_RUNNING
    run.started_at = _now()
    await session.flush()

    passed = 0
    failed = 0
    for case in cases:
        outcome = await _safe_execute(executor, case)
        verdict = check_case(case, outcome)
        session.add(
            BatchTestResult(
                run_id=run.id,
                case_id=case.id,
                case_name=case.name,
                passed=verdict.passed,
                reply=(outcome.reply or "")[:4000],
                latency_ms=outcome.latency_ms,
                failure_reason=verdict.reason,
            )
        )
        passed += 1 if verdict.passed else 0
        failed += 0 if verdict.passed else 1
        await session.flush()

    run.passed = passed
    run.failed = failed
    run.total = passed + failed
    run.status = STATUS_COMPLETED if run.total else STATUS_FAILED
    run.finished_at = _now()
    await session.flush()
    log.info(
        "batch_tests.run_finished",
        run_id=str(run.id),
        total=run.total,
        passed=passed,
        failed=failed,
    )
    return run


async def _safe_execute(executor: CaseExecutor, case: BatchTestCase) -> CaseOutcome:
    """Never let one case's exception end the batch."""
    try:
        return await executor(case)
    except Exception as exc:
        log.warning(
            "batch_tests.executor_failed",
            case_id=str(case.id),
            error_type=type(exc).__name__,
        )
        return CaseOutcome(reply="", error=f"{type(exc).__name__}: {exc}"[:300])


async def cancel_run(session: AsyncSession, tenant_id: uuid.UUID, run_id: uuid.UUID) -> BatchTestRun:
    run = await session.get(BatchTestRun, run_id)
    if run is None or run.tenant_id != tenant_id:
        raise BatchTestError("not_found", "run not found", 404)
    if run.status in (STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED):
        raise BatchTestError("not_cancellable", f"run is already {run.status}", 409)
    run.status = STATUS_CANCELLED
    run.finished_at = _now()
    await session.flush()
    return run


def run_as_dict(run: BatchTestRun) -> dict[str, Any]:
    pass_rate = None
    if run.total:
        pass_rate = round(run.passed / run.total * 100.0, 2)
    return {
        "id": str(run.id),
        "agent_id": run.agent_id,
        "status": run.status,
        "total": run.total,
        "passed": run.passed,
        "failed": run.failed,
        "pass_rate": pass_rate,
        "created_by": run.created_by,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "created_at": run.created_at.isoformat() if run.created_at else None,
    }


def result_as_dict(result: BatchTestResult) -> dict[str, Any]:
    return {
        "id": str(result.id),
        "case_id": str(result.case_id) if result.case_id else None,
        "case_name": result.case_name,
        "passed": bool(result.passed),
        "reply": result.reply,
        "latency_ms": result.latency_ms,
        "failure_reason": result.failure_reason,
    }


async def get_run(
    session: AsyncSession, tenant_id: uuid.UUID, run_id: uuid.UUID
) -> BatchTestRun:
    run = await session.get(BatchTestRun, run_id)
    if run is None or run.tenant_id != tenant_id:
        raise BatchTestError("not_found", "run not found", 404)
    return run


async def list_runs(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    agent_id: str | None = None,
    limit: int = 20,
) -> list[BatchTestRun]:
    query = select(BatchTestRun).where(BatchTestRun.tenant_id == tenant_id)
    if agent_id:
        query = query.where(BatchTestRun.agent_id == agent_id)
    query = query.order_by(BatchTestRun.created_at.desc()).limit(max(1, min(limit, 100)))
    return list((await session.scalars(query)).all())


async def list_results(
    session: AsyncSession, run_id: uuid.UUID
) -> list[BatchTestResult]:
    rows = await session.scalars(
        select(BatchTestResult)
        .where(BatchTestResult.run_id == run_id)
        .order_by(BatchTestResult.case_name)
    )
    return list(rows.all())
