"""First-party asynchronous SDK for the VoxDesk REST API."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


class VoxDeskError(RuntimeError):
    def __init__(self, status_code: int, body: Any):
        super().__init__(f"VoxDesk API request failed with HTTP {status_code}")
        self.status_code = status_code
        self.body = body


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


class VoxDeskClient:
    def __init__(
        self,
        api_key: str,
        endpoint: str = "http://localhost:8000",
        *,
        timeout: float = 15.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        self.api_key = api_key
        self.endpoint = endpoint.rstrip("/")
        self._client = client
        self._timeout = timeout

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
                return await self._request(client, method, path, **kwargs)
        return await self._request(self._client, method, path, **kwargs)

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

    async def list_calls(self, tenant_id: str) -> Any:
        return await self.request("GET", f"/api/tenants/{tenant_id}/calls")

    async def get_call(self, call_id: str) -> Any:
        return await self.request("GET", f"/api/calls/{call_id}")

    async def get_call_transcript(self, call_id: str) -> Any:
        return await self.request("GET", f"/api/calls/{call_id}/transcript")


VoiceAgentClient = VoxDeskClient
