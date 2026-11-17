"""Authenticated tenant/trunk runtime configuration boundary."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response
from starlette.requests import Request

from app.api import sip_trunk_routes as routes
from app.security.secret_store import MemorySecretStore


class _Result:
    def __init__(self, row):
        self.row = row

    def scalar_one_or_none(self):
        return self.row


class _Session:
    def __init__(self, row):
        self.row = row
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return _Result(self.row)


def _request(token: str) -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "https",
            "path": "/internal/telephony/sip-trunk-runtime-config",
            "raw_path": b"/internal/telephony/sip-trunk-runtime-config",
            "query_string": b"",
            "headers": [(b"authorization", f"Bearer {token}".encode())],
            "server": ("api.internal", 443),
            "client": ("sip-gateway", 8080),
        }
    )


def _row(tenant_id: uuid.UUID, trunk_id: uuid.UUID, secret_ref: str):
    return SimpleNamespace(
        id=trunk_id,
        tenant_id=tenant_id,
        environment_id=uuid.uuid4(),
        host="sip.carrier.example",
        port=5061,
        transport="tls",
        srtp_required=True,
        auth_username="carrier-user",
        auth_secret_ref=secret_ref,
        caller_id="+15550100",
        egress_address="198.51.100.10",
        name="Carrier A",
        status="CONFIGURED",
        last_test_at=None,
        last_test_ms=None,
        last_error=None,
        metadata_json={},
    )


def test_gateway_runtime_endpoint_requires_the_shared_bearer(monkeypatch) -> None:
    monkeypatch.setattr(
        routes,
        "get_carrier_v2_settings",
        lambda: SimpleNamespace(sip_gateway_token="strong-test-token"),
    )

    routes._require_gateway_auth(_request("strong-test-token"))
    with pytest.raises(HTTPException) as rejected:
        routes._require_gateway_auth(_request("wrong-token"))
    assert rejected.value.status_code == 401
    assert rejected.value.headers["WWW-Authenticate"] == "Bearer"


def test_password_is_sealed_and_secret_reference_is_tenant_and_purpose_bound(monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    store = MemorySecretStore()
    monkeypatch.setattr(routes, "get_secret_store", lambda: store)

    ref = routes._auth_secret_reference(
        tenant_id, password=routes.SecretStr("carrier-password")
    )
    assert ref and ref.startswith("secret://")
    assert store.get(str(tenant_id), routes.SIP_TRUNK_SECRET_PURPOSE, ref) == "carrier-password"

    foreign_ref = store.put(str(uuid.uuid4()), routes.SIP_TRUNK_SECRET_PURPOSE, "foreign")
    with pytest.raises(HTTPException) as rejected:
        routes._auth_secret_reference(tenant_id, reference=foreign_ref)
    assert rejected.value.status_code == 422
    assert "foreign" not in str(rejected.value.detail)


def test_runtime_endpoint_resolves_only_the_scoped_trunk_secret(monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    trunk_id = uuid.uuid4()
    store = MemorySecretStore()
    ref = store.put(str(tenant_id), routes.SIP_TRUNK_SECRET_PURPOSE, "carrier-password")
    row = _row(tenant_id, trunk_id, ref)
    session = _Session(row)
    monkeypatch.setattr(routes, "get_secret_store", lambda: store)
    monkeypatch.setattr(
        routes,
        "get_carrier_v2_settings",
        lambda: SimpleNamespace(sip_gateway_token="strong-test-token"),
    )

    response = Response()
    result = _run(routes.sip_trunk_runtime_config(
        _request("strong-test-token"),
        response,
        tenant_id=tenant_id,
        trunk_id=trunk_id,
        environment_id=row.environment_id,
        session=session,
    ))

    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Pragma"] == "no-cache"
    assert result["tenant_id"] == str(tenant_id)
    assert result["trunk_id"] == str(trunk_id)
    assert result["host"] == row.host
    assert result["transport"] == "tls"
    assert result["srtp_required"] is True
    assert result["username"] == row.auth_username
    assert result["password"] == "carrier-password"
    assert len(session.statements) == 1
    statement = str(session.statements[0])
    assert "sip_trunks.id" in statement
    assert "sip_trunks.tenant_id" in statement
    public = routes._serialize(row)
    assert "password" not in public
    assert "auth_password" not in public


def test_wrong_tenant_or_missing_trunk_is_not_resolved(monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    trunk_id = uuid.uuid4()
    store = MemorySecretStore()
    store.put(str(tenant_id), routes.SIP_TRUNK_SECRET_PURPOSE, "carrier-password")
    monkeypatch.setattr(routes, "get_secret_store", lambda: store)
    monkeypatch.setattr(
        routes,
        "get_carrier_v2_settings",
        lambda: SimpleNamespace(sip_gateway_token="strong-test-token"),
    )

    with pytest.raises(HTTPException) as missing:
        _run(routes.sip_trunk_runtime_config(
            _request("strong-test-token"),
            Response(),
            tenant_id=tenant_id,
            trunk_id=trunk_id,
            session=_Session(None),
        ))
    assert missing.value.status_code == 404

    wrong_purpose_ref = store.put(str(tenant_id), "other_integration", "foreign")
    invalid_row = _row(tenant_id, trunk_id, wrong_purpose_ref)
    with pytest.raises(HTTPException) as invalid_secret:
        _run(routes.sip_trunk_runtime_config(
            _request("strong-test-token"),
            Response(),
            tenant_id=tenant_id,
            trunk_id=trunk_id,
            environment_id=invalid_row.environment_id,
            session=_Session(invalid_row),
        ))
    assert invalid_secret.value.status_code == 503
    assert "foreign" not in str(invalid_secret.value.detail)


def _run(coro):
    import asyncio

    return asyncio.run(coro)
