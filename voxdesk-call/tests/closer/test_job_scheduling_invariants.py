"""Every job type that production code enqueues has a handler, and no handler is registered twice.

``register_handler_function`` (app/jobs/types.py) overwrites silently on a duplicate
type, so duplicate registration is a closer-level failure rather than a runtime error.
The worker claims only registered types; an enqueued type with no handler sits in
``queued`` forever. ``telephony.callback`` was exactly that defect.

Types that are written as a *synchronous idempotency record* (completed inside the
request, never executed by the worker) are listed in RECORD_ONLY_JOB_TYPES with the
reason. A type can only be exempt by being listed there.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.closer.registry_probe import clean_registry_report

REPO_ROOT = Path(__file__).resolve().parents[2]

# Declared constants that no production code enqueues and that have no handler.
# Each entry must say why the type is inert. Removing a constant is not the fix.
NO_PRODUCER_JOB_TYPES: dict[str, str] = {
    "automation": (
        "No app code enqueues an 'automation' job. app/automation/dlq.py only inspects "
        "rows of this type, so that DLQ path is inert until a producer exists. Recorded in "
        "evidence/closer/ISSUES.json."
    ),
}

# Types written by production code that are completed inside the request and must
# never be claimed by the worker.
RECORD_ONLY_JOB_TYPES: dict[str, str] = {
    "lead_import": (
        "app/leads/importers.py begin_import_job writes this row as a synchronous idempotency "
        "record; app/leads/service.py finish_job completes it inside the import request. "
        "The worker must never claim it; tests/closer/test_job_scheduling_invariants.py proves that."
    ),
}


def _module_string_constants(tree: ast.Module) -> dict[str, str]:
    out: dict[str, str] = {}
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    out[target.id] = node.value.value
    return out


def _job_type_constants() -> dict[str, str]:
    tree = ast.parse((REPO_ROOT / "app" / "jobs" / "types.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "JobType":
            return {
                t.id: s.value.value
                for s in node.body
                if isinstance(s, ast.Assign) and isinstance(s.value, ast.Constant)
                for t in s.targets
                if isinstance(t, ast.Name)
            }
    raise AssertionError("JobType not found in app/jobs/types.py")


def _enqueued_job_types() -> dict[str, list[str]]:
    """job_type values written as literals, module constants or JobType.X at call sites in app/."""
    jobtype = _job_type_constants()
    found: dict[str, list[str]] = {}
    for path in sorted((REPO_ROOT / "app").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        consts = _module_string_constants(tree)
        rel = path.relative_to(REPO_ROOT)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if kw.arg != "job_type":
                    continue
                value = kw.value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    resolved = value.value
                elif isinstance(value, ast.Name) and value.id in consts:
                    resolved = consts[value.id]
                elif (
                    isinstance(value, ast.Attribute)
                    and isinstance(value.value, ast.Name)
                    and value.value.id == "JobType"
                    and value.attr in jobtype
                ):
                    resolved = jobtype[value.attr]
                else:
                    # Pass-through parameters: the value is resolved where the caller passes it.
                    continue
                found.setdefault(resolved, []).append(f"{rel}:{node.lineno}")
    return found


def test_every_enqueued_job_type_has_a_handler_or_a_documented_exemption():
    registered = set(clean_registry_report()["registered"])
    enqueued = _enqueued_job_types()
    assert enqueued, "the scan found no enqueue sites; the scanner is broken"

    unhandled = {
        job_type: sites
        for job_type, sites in enqueued.items()
        if job_type not in registered and job_type not in RECORD_ONLY_JOB_TYPES
    }
    assert unhandled == {}, f"enqueued job types with no handler (sites shown): {unhandled}"


def test_every_declared_job_type_is_registered_or_documented_as_inert():
    registered = set(clean_registry_report()["registered"])
    declared = set(_job_type_constants().values())
    undocumented = sorted(
        t
        for t in declared - registered
        if t not in NO_PRODUCER_JOB_TYPES and t not in RECORD_ONLY_JOB_TYPES
    )
    assert (
        undocumented == []
    ), f"declared but unregistered job types without a reason: {undocumented}"


def test_exemptions_refer_to_declared_job_types_only():
    declared = set(_job_type_constants().values())
    stale = sorted((set(NO_PRODUCER_JOB_TYPES) | set(RECORD_ONLY_JOB_TYPES)) - declared)
    assert stale == [], f"exemptions for job types that are no longer declared: {stale}"


def test_no_handler_is_registered_twice():
    """A duplicate registration overwrites silently, so the spy's call log is the proof."""
    report = clean_registry_report()
    seen = report["seen"]
    assert len(seen) == len(report["registered"]) > 0, "the spy did not observe every registration"
    duplicates = sorted({job_type for job_type in seen if seen.count(job_type) > 1})
    assert duplicates == [], f"job types registered more than once: {duplicates}"


async def test_a_record_only_job_is_never_claimed_by_the_worker(db, tenant_a):
    """lead_import rows are completed inside the import request; the worker's claim must skip them.

    The claim mirrors app/jobs/worker.py: when no explicit types are given the worker
    passes ``tuple(registered_job_types())``. A control row of a registered type is
    created alongside, so the test also proves the claim itself works.
    """
    import uuid

    from app.jobs.queue import claim_next, enqueue
    from app.leads.importers import begin_import_job

    job, created = await begin_import_job(
        db,
        tenant_id=tenant_a.id,
        environment_id=uuid.uuid4(),
        organization_id=None,
        key=f"closer-{uuid.uuid4()}",
    )
    assert created and job.job_type == "lead_import"
    control, _ = await enqueue(
        db,
        tenant_id=tenant_a.id,
        job_type="outbox.delivery",
        idempotency_key=f"closer-control-{uuid.uuid4()}",
        payload={},
    )
    await db.flush()

    claimed = await claim_next(
        db, worker_id="closer-test", job_types=tuple(clean_registry_report()["registered"])
    )

    assert claimed is not None and claimed.id == control.id
    assert claimed.id != job.id
