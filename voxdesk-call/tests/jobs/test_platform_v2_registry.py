"""Batch 07 durable work shares the canonical persisted job registry."""

from __future__ import annotations

import inspect

from app.jobs.types import JobType, handler_for

PLATFORM_JOB_TYPES = frozenset(
    {
        JobType.WFM_HISTORY_ROLLUP,
        JobType.WFM_FORECAST_GENERATE,
        JobType.WFM_FORECAST_SCORE,
        JobType.WFM_ADHERENCE_SWEEP,
        JobType.WFM_SCHEDULE_PUBLISH,
        JobType.WL_DOMAIN_REVERIFY,
        JobType.WL_CERT_REFRESH,
        JobType.WL_SENDER_DOMAIN_CHECK,
        JobType.MOBILE_PUSH_RETRY,
        JobType.MOBILE_TOKEN_GC,
    }
)


def test_platform_v2_job_types_are_in_the_persisted_vocabulary():
    assert PLATFORM_JOB_TYPES <= JobType.ALL
    assert len(PLATFORM_JOB_TYPES) == 10


def test_every_platform_v2_job_has_one_async_registered_handler():
    from app.jobs.registry import bootstrap

    handlers = bootstrap()
    assert PLATFORM_JOB_TYPES <= handlers.keys()
    for job_type in sorted(PLATFORM_JOB_TYPES):
        handler = handler_for(job_type)
        assert handler is handlers[job_type], job_type
        assert inspect.iscoroutinefunction(handler), job_type
