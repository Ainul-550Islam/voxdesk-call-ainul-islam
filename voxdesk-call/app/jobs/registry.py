"""Canonical job-handler registry bootstrap over app.jobs.types (no second registry)."""
from __future__ import annotations
from app.jobs.types import handler_for, register_handler, register_handler_function, registered_job_types


def bootstrap() -> dict[str, object]:
    from app.jobs import ai_specialized_jobs  # noqa: F401
    from app.voice import clone_worker  # noqa: F401
    from app.outbox import dispatcher  # noqa: F401
    from app.services import post_call_analysis_service  # noqa: F401 - analysis.backfill
    from app.services import post_call_workflow  # noqa: F401 - workflow.execution
    from app.qa import auto_review  # noqa: F401 - QA_AUTO_REVIEW
    from app.telephony import post_call  # noqa: F401 - registers POST_CALL
    from app.deployment import runtime as deployment_runtime  # noqa: F401 - registers deployment handler
    from app.services import simulation_service  # noqa: F401 - registers evaluation handler
    # BATCH06: contact-center-v2 durable jobs (dialer start/tick, assist turn and
    # ACD callback) were enqueued by their services but had no execution handler
    # because this bootstrap never imported their jobs module. Importing it once
    # registers all four; Python's module cache keeps bootstrap() idempotent, so
    # ACD_CALLBACK is registered exactly once, never double-registered.
    from app import contact_center_v2 as _cc2_pkg  # noqa: F401
    from app.contact_center_v2 import jobs as _cc2_jobs  # noqa: F401 - registers CC2_* + ACD_CALLBACK handlers
    # BATCH07:platform_v2_jobs — the WFM, white-label, and mobile enqueue sites
    # use the same durable worker, retry budget, lease, and dead-letter path.
    from app.platform_v2 import jobs as _platform_v2_jobs  # noqa: F401 - registers Batch 07 handlers
    return {name: handler_for(name) for name in registered_job_types() if handler_for(name) is not None}


__all__ = ["bootstrap", "handler_for", "register_handler", "register_handler_function", "registered_job_types"]
