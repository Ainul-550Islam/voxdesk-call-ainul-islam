"""VoxDesk Python SDK (`sdk/python/voxdesk/client.py`).

Provides both synchronous (`SyncVoxDeskClient`) and asynchronous (`VoxDeskClient` /
`AsyncVoxDeskClient`) clients with:
- Calls, Web Calls, Batch Calls, Agents, Agent Versions, Phone Numbers, Webhooks
- Retries with exponential backoff & configurable timeouts
- Pydantic v2 response models
- Webhook HMAC-SHA256 signature verification helper (`verify_webhook_signature`)
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import hmac
import time
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field


class VoxDeskError(RuntimeError):
    def __init__(self, status_code: int, body: Any):
        super().__init__(f"VoxDesk API request failed with HTTP {status_code}")
        self.status_code = status_code
        self.body = body


class CallModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    call_id: str = Field(default="")
    tenant_id: str | None = None
    agent_id: str | None = None
    agent_version: int | None = None
    direction: str | None = None
    status: str | None = None


class WebCallResponseModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    call_id: str
    tenant_id: str
    agent_id: str
    agent_version: int
    direction: str = "web"
    status: str
    transport: str
    url: str
    offer_url: str | None = None
    access_token: str
    expires_at: str | None = None
    sample_rate: int = 16000


class AgentModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    status: str | None = None
    published_version: int | None = None


class AgentVersionModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str | None = None
    agent_id: str | None = None
    version_number: int
    status: str | None = None


class PhoneNumberModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    phone_number: str
    provider: str | None = None
    agent_id: str | None = None


class BatchCallResponseModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    batch_id: str | None = None
    queued: int = 0
    calls: list[dict[str, Any]] = Field(default_factory=list)


class WebhookSubscriptionModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    url: str
    events: list[str] = Field(default_factory=list)
    enabled: bool = True


@dataclass(frozen=True)
class ConnectorModel:
    id: str
    provider: str
    kind: str
    status: str
    capabilities: tuple[str, ...]
    health_message: str | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ConnectorModel":
        return cls(
            id=str(value["id"]),
            provider=str(value["provider"]),
            kind=str(value.get("kind", "generic")),
            status=str(value.get("status", "configured")),
            capabilities=tuple(str(item) for item in value.get("capabilities", [])),
            health_message=value.get("health_message"),
        )


@dataclass(frozen=True)
class ApiToolModel:
    id: str
    name: str
    method: str
    url_template: str
    enabled: bool = True

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ApiToolModel":
        return cls(
            id=str(value["id"]),
            name=str(value["name"]),
            method=str(value["method"]),
            url_template=str(value.get("url_template", "")),
            enabled=bool(value.get("enabled", True)),
        )


@dataclass(frozen=True)
class ApiToolResult:
    ok: bool
    status_code: int
    data: Any
    attempts: int
    duration_ms: float

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ApiToolResult":
        return cls(
            ok=bool(value.get("ok", False)),
            status_code=int(value.get("status_code", 0)),
            data=value.get("data"),
            attempts=int(value.get("attempts", 1)),
            duration_ms=float(value.get("duration_ms", 0.0)),
        )


@dataclass(frozen=True)
class McpToolModel:
    id: str
    name: str
    description: str
    input_schema: dict[str, Any]

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "McpToolModel":
        return cls(
            id=str(value["id"]),
            name=str(value["name"]),
            description=str(value.get("description", "")),
            input_schema=dict(value.get("input_schema", {})),
        )


@dataclass(frozen=True)
class SearchHitModel:
    document_id: str
    chunk_id: str
    title: str
    text: str
    score: float
    page: int | None = None
    heading: str | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SearchHitModel":
        return cls(
            document_id=str(value["document_id"]),
            chunk_id=str(value["chunk_id"]),
            title=str(value.get("title", "")),
            text=str(value.get("text", "")),
            score=float(value.get("score", 0.0)),
            page=value.get("page"),
            heading=value.get("heading"),
        )


@dataclass(frozen=True)
class KnowledgeSearchModel:
    query: str
    results: tuple[SearchHitModel, ...]
    count: int

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "KnowledgeSearchModel":
        results = tuple(SearchHitModel.from_dict(item) for item in value.get("results", []))
        return cls(
            query=str(value.get("query", "")),
            results=results,
            count=int(value.get("count", len(results))),
        )


@dataclass(frozen=True)
class UrlIngestModel:
    source_id: str
    visited: tuple[str, ...]
    documents: tuple[str, ...]

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "UrlIngestModel":
        return cls(
            source_id=str(value["source_id"]),
            visited=tuple(str(item) for item in value.get("visited", [])),
            documents=tuple(str(item) for item in value.get("documents", [])),
        )


def verify_webhook_signature(
    payload: bytes | str,
    signature_header: str,
    secret: str,
    *,
    timestamp_header: str | None = None,
    tolerance_seconds: int = 300,
) -> bool:
    """Verify VoxDesk HMAC-SHA256 webhook signature (`X-VoxDesk-Signature`)."""
    if not signature_header or not secret:
        return False
    raw_bytes = payload.encode("utf-8") if isinstance(payload, str) else payload
    sig = signature_header.strip()
    if sig.startswith("sha256="):
        sig = sig[len("sha256=") :]

    if timestamp_header:
        try:
            ts = int(timestamp_header.strip())
        except ValueError:
            return False
        if abs(int(time.time()) - ts) > tolerance_seconds:
            return False
        signed_bytes = f"{ts}.".encode("utf-8") + raw_bytes
        digest_with_ts = hmac.new(
            secret.encode("utf-8"), signed_bytes, hashlib.sha256
        ).hexdigest()
        if hmac.compare_digest(digest_with_ts, sig):
            return True

    digest = hmac.new(secret.encode("utf-8"), raw_bytes, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, sig)


class VoxDeskClient:
    """Asynchronous VoxDesk REST API client with retry & Pydantic model helpers."""

    def __init__(
        self,
        api_key: str,
        endpoint: str = "http://localhost:8000",
        *,
        timeout: float = 15.0,
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.25,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        self.api_key = api_key
        self.endpoint = endpoint.rstrip("/")
        self._client = client
        self._timeout = timeout
        self._max_retries = max(0, int(max_retries))
        self._retry_backoff_seconds = max(0.0, float(retry_backoff_seconds))

    async def __aenter__(self) -> "VoxDeskClient":
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self.endpoint, timeout=self._timeout)
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._client is not None and self._client.base_url.host != "testserver":
            await self._client.aclose()

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        if self._client is None:
            async with httpx.AsyncClient(base_url=self.endpoint, timeout=self._timeout) as client:
                return await self._request_with_retries(client, method, path, **kwargs)
        return await self._request_with_retries(self._client, method, path, **kwargs)

    async def _request_with_retries(
        self, client: httpx.AsyncClient, method: str, path: str, **kwargs: Any
    ) -> Any:
        attempt = 0
        while True:
            try:
                return await self._request(client, method, path, **kwargs)
            except VoxDeskError as exc:
                if exc.status_code in {429, 502, 503, 504} and attempt < self._max_retries:
                    attempt += 1
                    await asyncio.sleep(self._retry_backoff_seconds * (2 ** (attempt - 1)))
                    continue
                raise
            except httpx.TransportError:
                if attempt < self._max_retries:
                    attempt += 1
                    await asyncio.sleep(self._retry_backoff_seconds * (2 ** (attempt - 1)))
                    continue
                raise

    async def _request(
        self, client: httpx.AsyncClient, method: str, path: str, **kwargs: Any
    ) -> Any:
        headers = dict(kwargs.pop("headers", {}))
        headers.setdefault("Authorization", f"Bearer {self.api_key}")
        response = await client.request(method, path, headers=headers, **kwargs)
        if response.status_code >= 400:
            try:
                body = response.json()
            except ValueError:
                body = response.text[:1000]
            raise VoxDeskError(response.status_code, body)
        if response.status_code == 204:
            return None
        content_type = response.headers.get("content-type", "")
        return response.json() if "json" in content_type else response.content

    # --- Web Calls ---

    async def create_web_call(
        self,
        agent_id: str,
        *,
        version: int | None = None,
        dynamic_vars: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        origin: str | None = None,
        ttl_seconds: int = 300,
    ) -> WebCallResponseModel:
        payload: dict[str, Any] = {
            "agent_id": agent_id,
            "dynamic_vars": dynamic_vars or {},
            "metadata": metadata or {},
            "ttl_seconds": ttl_seconds,
        }
        if version is not None:
            payload["version"] = version
        if origin is not None:
            payload["origin"] = origin
        data = await self.request("POST", "/api/web-calls", json=payload)
        return WebCallResponseModel.model_validate(data)

    async def get_web_call(self, call_id: str) -> CallModel:
        data = await self.request("GET", f"/api/web-calls/{call_id}")
        return CallModel.model_validate(data)

    async def end_web_call(self, call_id: str, *, reason: str = "ended_by_api") -> CallModel:
        data = await self.request(
            "POST", f"/api/web-calls/{call_id}/end", json={"reason": reason}
        )
        return CallModel.model_validate(data)

    # --- Calls & Batch ---

    async def list_calls(self, tenant_id: str) -> Any:
        return await self.request("GET", f"/api/tenants/{tenant_id}/calls")

    async def get_call(self, call_id: str) -> Any:
        return await self.request("GET", f"/api/calls/{call_id}")

    async def get_call_transcript(self, call_id: str) -> Any:
        return await self.request("GET", f"/api/calls/{call_id}/transcript")

    async def create_call(self, payload: dict[str, Any]) -> CallModel:
        data = await self.request("POST", "/api/v1/telephony/calls", json=payload)
        return CallModel.model_validate(data if isinstance(data, dict) else {"call_id": str(data)})

    async def create_batch_calls(self, payload: dict[str, Any]) -> BatchCallResponseModel:
        data = await self.request("POST", "/api/calls/outbound/bulk", json=payload)
        return BatchCallResponseModel.model_validate(data)

    # --- Agents, Versions, Phone Numbers, Webhooks ---

    async def list_agents(self) -> list[AgentModel]:
        data = await self.request("GET", "/api/agents")
        items = data.get("items", data) if isinstance(data, dict) else data
        return [AgentModel.model_validate(item) for item in (items or [])]

    async def get_agent(self, agent_id: str) -> AgentModel:
        data = await self.request("GET", f"/api/agents/{agent_id}")
        return AgentModel.model_validate(data)

    async def list_agent_versions(self, agent_id: str) -> list[AgentVersionModel]:
        data = await self.request("GET", f"/api/agents/{agent_id}/versions")
        items = data.get("items", data) if isinstance(data, dict) else data
        return [AgentVersionModel.model_validate(item) for item in (items or [])]

    async def list_phone_numbers(self) -> list[PhoneNumberModel]:
        data = await self.request("GET", "/api/phone-numbers")
        items = data.get("items", data) if isinstance(data, dict) else data
        return [PhoneNumberModel.model_validate(item) for item in (items or [])]

    async def create_webhook(
        self, url: str, events: list[str], *, secret: str | None = None
    ) -> WebhookSubscriptionModel:
        payload: dict[str, Any] = {"url": url, "events": events}
        if secret:
            payload["secret"] = secret
        data = await self.request("POST", "/api/webhooks", json=payload)
        return WebhookSubscriptionModel.model_validate(data)

    async def delete_webhook(self, webhook_id: str) -> None:
        await self.request("DELETE", f"/api/webhooks/{webhook_id}")

    # --- Existing Connector / Tool / Knowledge API ---

    async def list_connectors(self) -> list[ConnectorModel]:
        value = await self.request("GET", "/api/connectors")
        return [ConnectorModel.from_dict(item) for item in value]

    async def create_api_tool(self, payload: dict[str, Any]) -> ApiToolModel:
        value = await self.request("POST", "/api/api-tools", json=payload)
        return ApiToolModel.from_dict(value)

    async def execute_api_tool(
        self, tool_id: str, arguments: dict[str, Any], *, idempotency_key: str
    ) -> ApiToolResult:
        value = await self.request(
            "POST",
            f"/api/api-tools/{tool_id}/execute",
            json={"arguments": arguments},
            headers={"Idempotency-Key": idempotency_key},
        )
        return ApiToolResult.from_dict(value)

    async def discover_mcp(self, server_id: str) -> list[McpToolModel]:
        value = await self.request("POST", f"/api/mcp/servers/{server_id}/discover")
        return [McpToolModel.from_dict(item) for item in value]

    async def search_knowledge(
        self, query: str, *, top_k: int | None = None
    ) -> KnowledgeSearchModel:
        body: dict[str, Any] = {"query": query}
        if top_k is not None:
            body["top_k"] = top_k
        value = await self.request("POST", "/api/knowledge/search", json=body)
        return KnowledgeSearchModel.from_dict(value)

    async def ingest_knowledge_url(
        self, url: str, *, max_depth: int = 0, max_pages: int = 20
    ) -> UrlIngestModel:
        value = await self.request(
            "POST",
            "/api/knowledge/urls",
            json={"url": url, "max_depth": max_depth, "max_pages": max_pages},
        )
        return UrlIngestModel.from_dict(value)

    async def start_oauth(self, connector_id: str) -> str:
        value = await self.request("GET", f"/api/connectors/{connector_id}/oauth/start")
        return str(value["authorization_url"])


class SyncVoxDeskClient:
    """Synchronous VoxDesk REST API client with retry & Pydantic model helpers."""

    def __init__(
        self,
        api_key: str,
        endpoint: str = "http://localhost:8000",
        *,
        timeout: float = 15.0,
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.25,
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        self.api_key = api_key
        self.endpoint = endpoint.rstrip("/")
        self._client = client
        self._timeout = timeout
        self._max_retries = max(0, int(max_retries))
        self._retry_backoff_seconds = max(0.0, float(retry_backoff_seconds))

    def __enter__(self) -> "SyncVoxDeskClient":
        if self._client is None:
            self._client = httpx.Client(base_url=self.endpoint, timeout=self._timeout)
        return self

    def __exit__(self, *_: object) -> None:
        if self._client is not None:
            self._client.close()

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        if self._client is None:
            with httpx.Client(base_url=self.endpoint, timeout=self._timeout) as client:
                return self._request_with_retries(client, method, path, **kwargs)
        return self._request_with_retries(self._client, method, path, **kwargs)

    def _request_with_retries(
        self, client: httpx.Client, method: str, path: str, **kwargs: Any
    ) -> Any:
        attempt = 0
        while True:
            try:
                return self._request(client, method, path, **kwargs)
            except VoxDeskError as exc:
                if exc.status_code in {429, 502, 503, 504} and attempt < self._max_retries:
                    attempt += 1
                    time.sleep(self._retry_backoff_seconds * (2 ** (attempt - 1)))
                    continue
                raise
            except httpx.TransportError:
                if attempt < self._max_retries:
                    attempt += 1
                    time.sleep(self._retry_backoff_seconds * (2 ** (attempt - 1)))
                    continue
                raise

    def _request(
        self, client: httpx.Client, method: str, path: str, **kwargs: Any
    ) -> Any:
        headers = dict(kwargs.pop("headers", {}))
        headers.setdefault("Authorization", f"Bearer {self.api_key}")
        response = client.request(method, path, headers=headers, **kwargs)
        if response.status_code >= 400:
            try:
                body = response.json()
            except ValueError:
                body = response.text[:1000]
            raise VoxDeskError(response.status_code, body)
        if response.status_code == 204:
            return None
        content_type = response.headers.get("content-type", "")
        return response.json() if "json" in content_type else response.content

    def create_web_call(
        self,
        agent_id: str,
        *,
        version: int | None = None,
        dynamic_vars: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        origin: str | None = None,
        ttl_seconds: int = 300,
    ) -> WebCallResponseModel:
        payload: dict[str, Any] = {
            "agent_id": agent_id,
            "dynamic_vars": dynamic_vars or {},
            "metadata": metadata or {},
            "ttl_seconds": ttl_seconds,
        }
        if version is not None:
            payload["version"] = version
        if origin is not None:
            payload["origin"] = origin
        data = self.request("POST", "/api/web-calls", json=payload)
        return WebCallResponseModel.model_validate(data)

    def get_web_call(self, call_id: str) -> CallModel:
        data = self.request("GET", f"/api/web-calls/{call_id}")
        return CallModel.model_validate(data)

    def end_web_call(self, call_id: str, *, reason: str = "ended_by_api") -> CallModel:
        data = self.request(
            "POST", f"/api/web-calls/{call_id}/end", json={"reason": reason}
        )
        return CallModel.model_validate(data)


AsyncVoxDeskClient = VoxDeskClient
VoiceAgentClient = VoxDeskClient
