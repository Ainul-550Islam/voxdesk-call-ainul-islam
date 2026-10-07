"""Deployment metadata. This module records a fact. It does not deploy.

There is no Kubernetes client, no Terraform runner and no shell. A release
version stored here is a label an operator typed, not evidence that a rollout
happened.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from app.tenancy.isolation import ValidationFailed

_RELEASE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+\-]{0,63}$")
_SOURCE = re.compile(r"^[a-z0-9][a-z0-9._/\-]{0,63}$")
_STATUSES = frozenset({"idle", "recorded"})
_HEALTH = frozenset({"unknown", "healthy", "degraded", "unhealthy"})


@dataclass(frozen=True)
class DeploymentState:
    release_version: str
    deployed_at: datetime | None
    status: str
    source: str
    health_state: str
    executed: bool = False


def validate_release(version: str) -> str:
    cleaned = (version or "").strip()
    if not cleaned or not _RELEASE.match(cleaned):
        raise ValidationFailed("Release version must be 1-64 letters, digits, dots, plus or hyphen")
    return cleaned


def validate_source(source: str) -> str:
    cleaned = (source or "").strip().lower()
    if not cleaned:
        return ""
    if not _SOURCE.match(cleaned):
        raise ValidationFailed("Deployment source must be a short label, not a URL or a secret")
    return cleaned


def validate_status(status: str) -> str:
    if status not in _STATUSES:
        raise ValidationFailed("Deployment status must be idle or recorded")
    return status


def validate_health(health: str) -> str:
    if health not in _HEALTH:
        raise ValidationFailed("Health must be unknown, healthy, degraded or unhealthy")
    return health


def view_from_environment(environment) -> DeploymentState:
    return DeploymentState(
        release_version=environment.release_version or "",
        deployed_at=environment.deployed_at,
        status=environment.deployment_status or "idle",
        source=environment.deployment_source or "",
        health_state=environment.health_state or "unknown",
        executed=False,
    )


def apply_record(
    environment,
    *,
    release_version: str,
    source: str = "",
    status: str = "recorded",
    health_state: str = "unknown",
    deployed_at: datetime | None = None,
) -> DeploymentState:
    """Write metadata onto the environment row. Does not contact a deployer."""
    environment.release_version = validate_release(release_version)
    environment.deployment_source = validate_source(source)
    environment.deployment_status = validate_status(status)
    environment.health_state = validate_health(health_state)
    environment.deployed_at = deployed_at or datetime.utcnow()
    return view_from_environment(environment)
