"""Supported deployment adapter implementations."""
from app.deployment.adapters.airgap import AirGapAdapter
from app.deployment.adapters.base import DeploymentAdapter, DeploymentObservation, DeploymentRequest
from app.deployment.adapters.container import ContainerAdapter
from app.deployment.adapters.kubernetes import KubernetesAdapter
from app.deployment.adapters.registry import ArtifactRegistryAdapter, OCIRegistryClient

__all__ = ["AirGapAdapter", "ArtifactRegistryAdapter", "OCIRegistryClient", "ContainerAdapter", "DeploymentAdapter", "DeploymentObservation", "DeploymentRequest", "KubernetesAdapter"]
