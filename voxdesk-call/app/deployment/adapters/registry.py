"""OCI Distribution registry resolution and digest validation."""
from __future__ import annotations
from dataclasses import dataclass
import re
from urllib.parse import quote, urlsplit
from typing import Protocol


@dataclass(frozen=True)
class ArtifactResolution:
    registry: str
    available: bool
    exists: bool
    digest: str | None = None
    immutable: bool = False
    reason: str | None = None


class RegistryClient(Protocol):
    async def resolve_digest(self, reference: str) -> str | None: ...


class OCIRegistryClient:
    """Read-only OCI/Docker Registry v2 client pinned to one configured HTTPS host.

    Credentials are constructor inputs supplied by server-side secret wiring,
    never by a tenant request or the image reference. Redirects are disabled to
    avoid credential forwarding to a different host.
    """
    def __init__(self, registry_url: str, *, bearer_token: str | None = None, client=None, allow_insecure_local: bool = False):
        parsed = urlsplit(registry_url)
        if parsed.scheme not in ({"https", "http"} if allow_insecure_local else {"https"}) or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("registry URL must be a trusted HTTPS origin")
        self.base_url = registry_url.rstrip("/")
        self.hostname = parsed.netloc.lower()
        self.bearer_token = bearer_token
        self.client = client

    async def resolve_digest(self, reference: str) -> str | None:
        first, sep, rest = reference.partition("/")
        if not sep or first.lower() != self.hostname or not rest or "@" in rest and rest.count("@") != 1:
            raise ValueError("artifact reference is outside the configured registry")
        if "@" in rest:
            repository, tag = rest.split("@", 1)
        else:
            repository, marker, tag = rest.rpartition(":")
            # A colon in the registry port is already excluded by host comparison;
            # a colon in the repository path is not a legal OCI tag separator.
            if not marker:
                repository, tag = rest, "latest"
            elif not repository or not tag:
                raise ValueError("artifact reference has an invalid tag")
        if not re.fullmatch(r"[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*", repository):
            raise ValueError("artifact repository path is invalid")
        if not (re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", tag) or re.fullmatch(r"sha256:[0-9a-f]{64}", tag)):
            raise ValueError("artifact reference tag or digest is invalid")
        manifest = quote(tag, safe=":")
        url = f"{self.base_url}/v2/{repository}/manifests/{manifest}"
        headers = {"Accept": ", ".join(("application/vnd.oci.image.index.v1+json", "application/vnd.oci.image.manifest.v1+json", "application/vnd.docker.distribution.manifest.list.v2+json", "application/vnd.docker.distribution.manifest.v2+json"))}
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        if self.client is not None:
            response = await self.client.head(url, headers=headers)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.headers.get("Docker-Content-Digest") or response.headers.get("OCI-Content-Digest")
        import httpx
        async with httpx.AsyncClient(timeout=4.0, follow_redirects=False) as client:
            response = await client.head(url, headers=headers)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.headers.get("Docker-Content-Digest") or response.headers.get("OCI-Content-Digest")


class ArtifactRegistryAdapter:
    def __init__(self, client: RegistryClient | None = None, *, name: str = "artifact_registry"):
        self.client, self.name = client, name

    async def resolve(self, reference: str, expected_digest: str) -> ArtifactResolution:
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", expected_digest or ""):
            return ArtifactResolution(self.name, True, False, reason="approved artifact digest is invalid")
        if self.client is None:
            return ArtifactResolution(self.name, False, False, reason="registry client is not configured")
        try:
            resolved = await self.client.resolve_digest(reference)
        except Exception:
            return ArtifactResolution(self.name, False, False, reason="registry digest lookup failed")
        if resolved is None:
            return ArtifactResolution(self.name, True, False, reason="artifact reference was not found or registry omitted its digest")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", resolved):
            return ArtifactResolution(self.name, True, False, reason="registry returned no valid immutable SHA-256 digest")
        return ArtifactResolution(self.name, True, True, resolved, resolved == expected_digest, None if resolved == expected_digest else "registry digest differs from the approved digest")
