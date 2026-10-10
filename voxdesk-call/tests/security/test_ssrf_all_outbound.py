"""Comprehensive SSRF regression suite across all outbound HTTP clients (Part 7 / Gate G9).

Verifies that every outbound HTTP client in VoxDesk:
- ``app.core.ssrf.validate_outbound_url`` & ``validate_resolved_outbound_url``
- ``app.webhooks.delivery.deliver`` (outbound webhooks)
- ``app.agent.tools.http_tools.execute_agent_http_tool`` (custom agent HTTP tools)
- ``app.mcp.client.validate_server`` & ``app.mcp.client._request`` (MCP client)
- ``app.knowledge.url_ingest.crawl`` (knowledge-base URL crawler)
- ``app.integrations.oauth.authorization_url`` (OAuth connector endpoints)
- ``app.telephony.number_trust.TwilioTrustHubClient`` & ``TelnyxTrustClient``
- ``app.security.kms.aws_kms.AwsKmsAdapter`` & ``VaultTransitKmsAdapter``

rejects:
- ``127.0.0.1`` (IPv4 loopback)
- ``localhost`` / ``sub.localhost``
- ``[::1]`` (IPv6 loopback)
- ``169.254.169.254`` (AWS/GCP/Azure link-local metadata IP) and ``metadata.google.internal``
- ``10.0.0.0/8`` (RFC 1918 Class A private)
- ``172.16.0.0/12`` (RFC 1918 Class B private)
- ``192.168.0.0/16`` (RFC 1918 Class C private)
- ``[fd00::1]`` (IPv6 Unique Local Address ``fc00::/7``)
- ``file:///etc/passwd`` and ``gopher://127.0.0.1:70/_`` (forbidden schemes)
- DNS rebinding (public domain resolving to ``127.0.0.1`` or ``169.254.169.254``)
"""

from __future__ import annotations

import socket
import uuid

import pytest

from app.agent.tools.http_tools import execute_agent_http_tool
from app.core.ssrf import (
    OutboundUrlError,
    validate_outbound_url,
    validate_resolved_outbound_url,
)
from app.db.models import Connector, McpServer
from app.integrations.oauth import OAuthConfig, authorization_url
from app.knowledge.url_ingest import CrawlError, crawl
from app.mcp.client import McpError, _request as mcp_request, validate_server
from app.security.kms import KmsOperationError
from app.security.kms.aws_kms import AwsKmsAdapter, VaultTransitKmsAdapter
from app.telephony.number_trust import (
    NumberTrustError,
    TelnyxTrustClient,
    TwilioTrustHubClient,
)
from app.webhooks.delivery import deliver

pytestmark = pytest.mark.asyncio

FORBIDDEN_URLS = [
    "https://127.0.0.1/hook",
    "https://localhost/hook",
    "https://[::1]/hook",
    "https://169.254.169.254/latest/meta-data/",
    "https://metadata.google.internal/computeMetadata/v1/",
    "https://10.0.0.1/internal",
    "https://172.16.0.1/internal",
    "https://192.168.1.1/internal",
    "https://[fd00::1]/internal",
    "file:///etc/passwd",
    "gopher://127.0.0.1:70/_",
]


@pytest.mark.parametrize("bad_url", FORBIDDEN_URLS)
async def test_core_ssrf_guard_rejects_all_forbidden_targets(bad_url: str):
    """Static and DNS-resolving SSRF validators reject every loopback, RFC 1918, ULA, metadata, and non-HTTP(S) URL."""
    with pytest.raises(OutboundUrlError):
        validate_outbound_url(bad_url, require_https=True)
    with pytest.raises(OutboundUrlError):
        await validate_resolved_outbound_url(bad_url, require_https=True)


@pytest.mark.parametrize("bad_url", FORBIDDEN_URLS)
async def test_all_outbound_clients_reject_ssrf_targets(db, tenant_a, bad_url: str):
    """Webhooks, HTTP tool executor, MCP client, URL ingest crawler, OAuth, NumberTrust, and KMS reject SSRF targets."""
    # 1. Outbound webhooks
    wh_res = await deliver(
        url=bad_url,
        body=b'{"event":"call.completed"}',
        secret="whsec_test_secret",
        event_id="evt_1",
    )
    assert wh_res.kind == "permanent"
    assert wh_res.category == "invalid_endpoint"

    # 2. Agent custom HTTP tools
    tool_res = await execute_agent_http_tool(
        tool_spec={"name": "crm_lookup", "url": bad_url, "method": "POST"},
        arguments={"phone": "+14155550100"},
        require_https=True,
    )
    assert tool_res["ok"] is False
    assert "SSRF" in str(tool_res.get("error", ""))

    # 3. MCP server validator
    with pytest.raises(McpError):
        validate_server(bad_url, "streamable_http")

    # 4. Knowledge base URL ingestion crawler
    with pytest.raises(CrawlError):
        await crawl(db, tenant_id=tenant_a.id, url=bad_url, max_depth=0, max_pages=1)

    # 5. OAuth connector authorization_url
    connector = Connector(
        id=uuid.uuid4(),
        tenant_id=tenant_a.id,
        provider="custom_crm",
        kind="generic",
        status="configured",
    )
    oauth_cfg = OAuthConfig(
        authorization_url=bad_url,
        token_url="https://oauth.example.com/token",
        client_id="cid",
        client_secret_ref="secret://memory/x",
        redirect_uri="https://app.voxdesk.test/oauth/callback",
        scopes=("read",),
    )
    with pytest.raises(OutboundUrlError):
        await authorization_url(db, connector, oauth_cfg)

    # 6. Number trust clients (Twilio & Telnyx) when base_url is pointed at SSRF target
    if bad_url.startswith(("http://", "https://")):
        twilio_client = TwilioTrustHubClient(
            account_sid="AC123",
            auth_token="tok123",
            lookup_base_url=bad_url,
        )
        with pytest.raises(NumberTrustError) as tw_exc:
            await twilio_client.fetch_trust_snapshot("+14155550199")
        assert tw_exc.value.code == "SSRF_BLOCKED"

        telnyx_client = TelnyxTrustClient(
            api_key="KEY123",
            base_url=bad_url,
        )
        with pytest.raises(NumberTrustError) as tx_exc:
            await telnyx_client.fetch_trust_snapshot("+14155550199")
        assert tx_exc.value.code == "SSRF_BLOCKED"

    # 7. KMS adapters (AWS KMS & Vault Transit)
    aws_kms = AwsKmsAdapter(
        key_id="alias/voxdesk",
        region_name="us-east-1",
        access_key_id="AKIA123",
        secret_access_key="SECRET123",
        endpoint_url=bad_url,
    )
    with pytest.raises(KmsOperationError):
        await aws_kms.generate_data_key(encryption_context={"tenant_id": str(tenant_a.id)})

    if bad_url.startswith(("http://", "https://")):
        vault_kms = VaultTransitKmsAdapter(
            vault_addr=bad_url,
            vault_token="s.token123",
            key_name="voxdesk-kek",
        )
        with pytest.raises(KmsOperationError):
            await vault_kms.generate_data_key(
                encryption_context={"tenant_id": str(tenant_a.id)}
            )


@pytest.mark.parametrize(
    "rebound_ip",
    [
        "127.0.0.1",
        "169.254.169.254",
        "10.24.0.5",
        "172.20.0.9",
        "192.168.10.20",
        "::1",
        "fd00::dead:beef",
    ],
)
async def test_dns_rebinding_is_blocked_at_resolution_time(
    db, tenant_a, monkeypatch, rebound_ip: str
):
    """A public-looking hostname whose DNS resolves to a private, loopback, or metadata IP is blocked."""
    family = socket.AF_INET6 if ":" in rebound_ip else socket.AF_INET
    sockaddr = (rebound_ip, 443, 0, 0) if family == socket.AF_INET6 else (rebound_ip, 443)

    def _fake_getaddrinfo(_host, _port, *_args, **_kwargs):
        return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr)]

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)

    rebind_url = "https://rebind.attacker.example/webhook"

    # Core resolved validator blocks the rebound IP
    with pytest.raises(OutboundUrlError):
        await validate_resolved_outbound_url(rebind_url, require_https=True)

    # Webhook delivery blocks the rebound IP before opening a socket
    wh_res = await deliver(
        url=rebind_url,
        body=b'{"ping":true}',
        secret="whsec_rebind",
        event_id="evt_rebind",
    )
    assert wh_res.kind == "permanent"
    assert wh_res.category == "invalid_endpoint"

    # MCP client _request blocks the rebound IP before opening a socket
    mcp_srv = McpServer(
        id=uuid.uuid4(),
        tenant_id=tenant_a.id,
        name="Rebind MCP",
        endpoint=rebind_url,
        transport="streamable_http",
    )
    with pytest.raises(McpError, match="DNS resolution is not allowed"):
        await mcp_request(mcp_srv, "ping", {})
