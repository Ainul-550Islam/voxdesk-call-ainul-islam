"""Canonical typed deployment adapter contract and observation result."""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from datetime import datetime, timezone
from typing import Protocol


_SHA256_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


def artifact_repository(reference: str) -> str:
    """Return an OCI repository name, excluding an optional tag or digest."""
    repository = reference.split("@", 1)[0]
    slash = repository.rfind("/")
    colon = repository.rfind(":")
    if colon > slash:
        repository = repository[:colon]
    return repository


def runtime_image_digest(image_id: str) -> str | None:
    """Extract only a complete SHA-256 digest from a runtime image identifier.

    Container runtimes use forms such as ``repo@sha256:...`` and
    ``containerd://sha256:...``. Substring matches are unsafe because malformed
    identifiers with a valid digest prefix must not establish artifact identity.
    """
    value = image_id.strip()
    if "@" in value:
        value = value.rpartition("@")[2]
    elif "://" in value:
        value = value.rpartition("://")[2]
    return value if _SHA256_DIGEST.fullmatch(value) else None


def repo_digest_matches(reference: str, repo_digest: str, expected_digest: str) -> bool:
    """Require exact repository and exact immutable digest from RepoDigests."""
    if "@" not in repo_digest:
        return False
    repository, _, digest = repo_digest.rpartition("@")
    return (
        repository == artifact_repository(reference)
        and _SHA256_DIGEST.fullmatch(digest) is not None
        and digest == expected_digest
    )


@dataclass(frozen=True)
class DeploymentRequest:
    tenant_id: str
    organization_id: str
    environment_id: str
    target_id: str
    revision_id: str
    target_type: str
    artifact_reference: str
    artifact_digest: str
    manifest_fingerprint: str
    cluster_reference: str | None = None
    namespace: str | None = None
    resource_name: str | None = None
    offline: bool = False


@dataclass(frozen=True)
class DeploymentObservation:
    adapter: str
    observed: bool
    deployed: bool
    verified: bool
    state: str
    reference: str | None = None
    observed_digest: str | None = None
    observed_fingerprint: str | None = None
    checks: dict[str, bool | str] = field(default_factory=dict)
    evidence: dict[str, str] = field(default_factory=dict)
    reason: str | None = None
    adapter_version: str = "unknown"
    observed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    observed_revision: str | None = None
    observed_artifact: str | None = None
    health_state: str = "not_verified"
    observed_tenant_id: str | None = None
    observed_organization_id: str | None = None
    observed_environment_id: str | None = None
    observed_target_id: str | None = None

    @property
    def adapter_type(self) -> str:
        return self.adapter

    @property
    def evidence_data(self) -> dict[str, str]:
        return self.evidence

    @property
    def status(self) -> str:
        if self.state in {"unavailable", "observation_failed", "manifest_invalid", "package_unverified", "scope_mismatch", "deploy_failed"}:
            return "NOT_AVAILABLE" if not self.observed else "FAILED"
        if self.verified and self.deployed:
            return "VERIFIED"
        if self.deployed:
            return "OBSERVED"
        if self.observed:
            return "OBSERVED"
        if self.state == "accepted":
            return "ACCEPTED"
        if self.state == "running":
            return "RUNNING"
        return "NOT_READY"

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "adapter_type": self.adapter_type,
            "adapter_version": self.adapter_version,
            "observed_at": self.observed_at.isoformat(),
            "observed": self.observed,
            "deployed": self.deployed,
            "verified": self.verified,
            "observed_revision": self.observed_revision,
            "observed_artifact": self.observed_artifact,
            "health_state": self.health_state,
            "observed_digest": self.observed_digest,
            "observed_fingerprint": self.observed_fingerprint,
            "evidence_data": dict(self.evidence),
            "reason": self.reason,
        }


class DeploymentAdapter(Protocol):
    name: str

    async def preflight(self, request: DeploymentRequest) -> DeploymentObservation: ...
    async def apply(self, request: DeploymentRequest) -> DeploymentObservation: ...
    async def status(self, request: DeploymentRequest) -> DeploymentObservation: ...
    async def verify(self, request: DeploymentRequest) -> DeploymentObservation: ...
