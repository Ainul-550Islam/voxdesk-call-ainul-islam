from __future__ import annotations
from pathlib import Path
from app.core.config_validation import validate_runtime_config
from app.deployment.verification import verify_observation
from app.jobs.types import JobType


def test_health_and_runtime_routers_are_registered_in_application_source():
    source = Path("app/main.py").read_text()
    assert "app.include_router(health_router)" in source
    assert "app.include_router(deployment_runtime_router)" in source


def test_deployment_job_type_is_registered_vocabulary():
    assert JobType.DEPLOYMENT in JobType.ALL


def test_non_production_config_validation_is_nonfatal_for_optional_providers():
    assert validate_runtime_config(strict=False) == []
