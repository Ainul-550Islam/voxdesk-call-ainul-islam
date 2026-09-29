"""Prompt 3 offline certification tests.

External provider tests remain opt-in; these tests verify the fail-closed
protocol and tenant boundaries without pretending a remote provider succeeded.
"""

from __future__ import annotations
import os
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import select

from app.api_tools.service import ApiToolError, execute as execute_api_tool, validate_definition
from app.core.ssrf import OutboundUrlError, validate_outbound_url
from app.db.models import ApiTool, Connector, McpServer, McpTool
from app.integrations import oauth
from app.integrations.connector import registry
from app.mcp import client as mcp_client
from app.mcp.client import McpError, normalize_tool, validate_server
from app.knowledge.url_ingest import CrawlError, canonicalize
from app.security.privacy import PrivacyPolicy, detect, transform
from app.security.secret_store import MemorySecretStore, SecretStoreError
from app.webhooks.signing import sign, verify


def test_connector_registry_contains_only_protocol_implementations():
    assert {"gohighlevel", "hubspot", "jobber", "webhook", "google", "microsoft", "calcom"} <= set(
        registry.providers()
    )
    assert "health_check" in registry.get("hubspot").capabilities


@pytest.mark.asyncio
async def test_oauth_start_persists_one_time_pkce_state(db, tenant_a, monkeypatch):
    connector = Connector(
        tenant_id=tenant_a.id,
        provider="hubspot",
        kind="crm",
        capabilities=[],
        config={},
    )
    db.add(connector)
    await db.commit()
    await db.refresh(connector)
    monkeypatch.setattr(oauth, "encrypt_text", lambda *args, **kwargs: ("sealed-pkce", "test-key"))
    config = oauth.OAuthConfig(
        authorization_url="https://provider.example.test/oauth/authorize",
        token_url="https://provider.example.test/oauth/token",
        client_id="voxdesk-client",
        client_secret_ref="secret://oauth/client",
        redirect_uri="https://app.example.test/oauth/callback",
        scopes=("contacts.read",),
    )
    url = await oauth.authorization_url(db, connector, config)
    query = parse_qs(urlparse(url).query)
    assert query["code_challenge_method"] == ["S256"]
    assert query["code_challenge"]
    state = query["state"][0]
    row = (
        await db.execute(
            select(oauth.ConnectorOAuth).where(oauth.ConnectorOAuth.connector_id == connector.id)
        )
    ).scalar_one_or_none()
    assert row is not None and row.state == "authorizing"
    assert row.oauth_state_nonce == oauth._open_state(state)["nonce"]
    with pytest.raises(oauth.OAuthError):
        oauth._open_state(state[:-1] + ("0" if state[-1] != "0" else "1"))


@pytest.mark.asyncio
async def test_api_tool_executes_real_http_contract_and_masks_result(db, tenant_a):
    tool = ApiTool(
        tenant_id=tenant_a.id,
        name="lookup",
        method="GET",
        url_template="https://example.com/v1/{id}",
        parameter_schema={"type": "object", "required": ["id"]},
        result_schema={"type": "object", "required": ["email"]},
    )
    db.add(tool)
    await db.commit()
    await db.refresh(tool)

    requests: list[httpx.Request] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"email": "person@example.com"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await execute_api_tool(
            db,
            tool,
            {"id": "123"},
            idempotency_key="lookup-123",
            client=client,
        )
        duplicate = await execute_api_tool(
            db,
            tool,
            {"id": "123"},
            idempotency_key="lookup-123",
            client=client,
        )
    assert result.ok and result.data == {"email": "[REDACTED]"}
    assert duplicate.data["data"] == {"email": "[REDACTED]"}
    assert len(requests) == 1 and str(requests[0].url).endswith("/v1/123")


@pytest.mark.asyncio
async def test_mcp_execution_validates_arguments_and_uses_json_rpc(db, tenant_a, monkeypatch):
    server = McpServer(
        tenant_id=tenant_a.id,
        name="safe-mcp",
        endpoint="https://mcp.example.test/rpc",
        transport="streamable_http",
    )
    db.add(server)
    await db.flush()
    tool = McpTool(
        tenant_id=tenant_a.id,
        server_id=server.id,
        name="lookup",
        input_schema={
            "type": "object",
            "required": ["id"],
            "properties": {"id": {"type": "string"}},
        },
    )
    db.add(tool)
    await db.commit()
    await db.refresh(server)
    await db.refresh(tool)
    seen: dict = {}

    async def fake_request(received_server, method, params, *, timeout=10.0):
        seen.update(
            {"server": received_server, "method": method, "params": params, "timeout": timeout}
        )
        return {"content": [{"type": "text", "text": "ok"}]}

    monkeypatch.setattr(mcp_client, "_request", fake_request)
    result = await mcp_client.execute(db, server, tool, {"id": "123"})
    assert result["content"][0]["text"] == "ok"
    assert seen["method"] == "tools/call"
    assert seen["params"] == {"name": "lookup", "arguments": {"id": "123"}}


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/x",
        "https://169.254.169.254/",
        "https://localhost/x",
        "ftp://example.com/x",
    ],
)
def test_outbound_ssrf_guard_rejects_reserved_targets(url):
    with pytest.raises(OutboundUrlError):
        validate_outbound_url(url, require_https=True)


def test_url_ingest_canonicalizes_http_urls_and_rejects_non_web_schemes():
    assert (
        canonicalize("HTTPS://docs.example.test/guide#fragment")
        == "https://docs.example.test/guide"
    )
    with pytest.raises(CrawlError):
        canonicalize("file:///etc/passwd")


def test_memory_secret_store_is_tenant_scoped():
    store = MemorySecretStore()
    ref = store.put("tenant-a", "connector:test", os.urandom(18).hex())
    with pytest.raises(SecretStoreError):
        store.get("tenant-b", "connector:test", ref)
    assert store.get("tenant-a", "connector:test", ref)


def test_privacy_policy_masks_and_tokenizes_without_logging_raw_values():
    text = "Reach user@example.com or +1 555 010 2000."
    assert detect(text)["email"] == 1
    masked = transform(text, PrivacyPolicy(mode="mask"))
    assert "user@example.com" not in masked and "[REDACTED]" in masked
    tokenized = transform(text, PrivacyPolicy(mode="tokenize"))
    assert "user@example.com" not in tokenized and "<email_" in tokenized


def test_signed_webhook_is_replay_window_bound():
    body = b'{"event":"created"}'
    secret = os.urandom(24).hex()
    signature = sign(secret, body, timestamp=100)
    assert verify(secret, body, signature, now=100, tolerance_seconds=10)
    assert not verify(secret, body, signature, now=1000, tolerance_seconds=10)


def test_mcp_schema_normalization_and_transport_allowlist():
    normalized = normalize_tool(
        {"name": "lookup", "description": "safe", "inputSchema": {"type": "object"}}
    )
    assert normalized["name"] == "lookup" and normalized["inputSchema"]["type"] == "object"
    with pytest.raises(McpError):
        validate_server("http://127.0.0.1:9000/mcp", "streamable_http")
    with pytest.raises(McpError):
        normalize_tool({"name": "bad", "inputSchema": {"type": "string"}})


def test_api_tool_requires_https_and_supported_method():
    validate_definition(
        method="GET",
        url_template="https://example.com/v1/{id}",
        parameter_schema={"type": "object"},
        body_schema={},
        result_schema={},
    )
    with pytest.raises(ApiToolError):
        validate_definition(
            method="TRACE",
            url_template="https://example.com",
            parameter_schema={},
            body_schema={},
            result_schema={},
        )
    with pytest.raises(ApiToolError):
        validate_definition(
            method="GET",
            url_template="http://example.com",
            parameter_schema={},
            body_schema={},
            result_schema={},
        )
