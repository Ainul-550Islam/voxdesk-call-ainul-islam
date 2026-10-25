import pytest
from httpx import AsyncClient

from app.db.models import User
from tests.conftest import auth_headers
from tests.contact_center_v2.conftest import Cc2Scope


async def test_softphone_registration_is_idempotent_and_does_not_persist_tokens(
    client: AsyncClient,
    cc2_scope: Cc2Scope,
    agent_a: User,
    viewer_a: User,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        "app.contact_center_v2.softphone.service.access_token", lambda row, config=None: ("test-only-jwt", 60),
    )
    monkeypatch.setattr("app.contact_center_v2.softphone.service.ice_servers", lambda user_id, config=None: [])
    headers = await auth_headers(client, agent_a)
    viewer_headers = await auth_headers(client, viewer_a)
    body = {"environment_id": str(cc2_scope.environment.id), "device_label": "Test browser"}
    denied = await client.post(
        "/api/cc2/softphone/sessions", json=body,
        headers={**viewer_headers, "Idempotency-Key": "viewer-registration"},
    )
    assert denied.status_code == 403
    first = await client.post(
        "/api/cc2/softphone/sessions", json=body,
        headers={**headers, "Idempotency-Key": "agent-registration"},
    )
    assert first.status_code == 200, first.text
    replay = await client.post(
        "/api/cc2/softphone/sessions", json=body,
        headers={**headers, "Idempotency-Key": "agent-registration"},
    )
    assert replay.status_code == 200
    assert replay.json()["session"]["id"] == first.json()["session"]["id"]
    assert first.json()["session"]["token"] == "test-only-jwt"
    session_id = first.json()["session"]["id"]
    heartbeat = await client.post(f"/api/cc2/softphone/sessions/{session_id}/heartbeat", headers=headers)
    assert heartbeat.status_code == 200
    revoked = await client.delete(f"/api/cc2/softphone/sessions/{session_id}", headers=headers)
    assert revoked.status_code == 200
    current = await client.get("/api/cc2/softphone/sessions/current", headers=headers)
    assert current.json() == {"session": None}
