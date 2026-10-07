from __future__ import annotations

from pathlib import Path

from app.core.config_validation import validate_runtime_config
from app.jobs.types import JobState, JobType


def test_health_and_runtime_routers_are_registered_in_application_source() -> None:
    source = Path("app/main.py").read_text(encoding="utf-8")
    assert "app.include_router(health_router)" in source
    assert "app.include_router(deployment_runtime_router)" in source
    assert "app.include_router(retell_parity_router)" in source


def test_deployment_job_type_is_registered_vocabulary() -> None:
    assert JobType.DEPLOYMENT in JobType.ALL
    assert JobType.EVALUATION in JobType.ALL
    assert JobType.WORKFLOW_EXECUTION in JobType.ALL


def test_job_state_terminal_and_active_sets_are_disjoint() -> None:
    assert JobState.SUCCEEDED in JobState.TERMINAL
    assert JobState.DEAD_LETTERED in JobState.TERMINAL
    assert JobState.RUNNING not in JobState.TERMINAL


def test_non_production_config_validation_is_nonfatal_for_optional_providers() -> None:
    assert validate_runtime_config(strict=False) == []
