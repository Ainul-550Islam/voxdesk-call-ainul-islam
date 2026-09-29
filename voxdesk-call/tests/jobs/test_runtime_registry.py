from __future__ import annotations
from app.jobs.types import JobType, handler_for, register_handler_function


async def _handler(job):
    return job.id


def test_deployment_is_known_by_existing_durable_job_registry():
    assert JobType.DEPLOYMENT in JobType.ALL
    register_handler_function(JobType.DEPLOYMENT, _handler)
    assert handler_for(JobType.DEPLOYMENT) is _handler


def test_unknown_job_is_not_added_to_persisted_vocabulary():
    assert "arbitrary.python.call" not in JobType.ALL
