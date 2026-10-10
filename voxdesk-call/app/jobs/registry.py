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
    return {name: handler_for(name) for name in registered_job_types() if handler_for(name) is not None}


__all__ = ["bootstrap", "handler_for", "register_handler", "register_handler_function", "registered_job_types"]
