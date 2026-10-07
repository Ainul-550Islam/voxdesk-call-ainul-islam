from __future__ import annotations

import pytest

from tests.acd_support import live_call, production
from tests.conftest import auth_headers


@pytest.mark.asyncio
async def test_live_monitor_and_takeover_actions_are_audited_and_not_faked(
    client, db, tenant_a, owner_a
):
    environment = await production(db, tenant_a)
    call = await live_call(db, tenant_a, environment)
    headers = await auth_headers(client, owner_a)

    monitor = await client.post(
        f"/api/calls/{call.id}/monitor",
        headers=headers,
        json={"mode": "whisper"},
    )
    assert monitor.status_code == 201, monitor.text
    monitor_session_id = monitor.json()["id"]
    assert monitor.json()["mode"] == "whisper"
    assert monitor.json()["status"] == "active"
    assert monitor.json()["meta"]["media_connected"] is False
    assert monitor.json()["meta"]["media_status"] == "NOT_CONFIGURED"
    assert monitor.json()["meta"]["control_plane_only"] is True

    whisper = await client.post(
        f"/api/calls/{call.id}/monitor/{monitor_session_id}/whisper",
        headers=headers,
        json={"text": "Please confirm the customer's request."},
    )
    assert whisper.status_code == 200, whisper.text
    assert whisper.json()["whisper"] == "Please confirm the customer's request."
    assert whisper.json()["status"] == "recorded_not_delivered"
    assert whisper.json()["delivery_status"] == "NOT_CONFIGURED"
    assert whisper.json()["media_connected"] is False
    whispers = await client.get(
        f"/api/calls/{call.id}/monitor/{monitor_session_id}/whispers",
        headers=headers,
    )
    assert whispers.status_code == 200, whispers.text
    assert whispers.json()["total"] == 1
    assert whispers.json()["whispers"][0]["text"] == "Please confirm the customer's request."

    takeover = await client.post(
        f"/api/calls/{call.id}/takeover",
        headers=headers,
        json={"notify_customer": True},
    )
    assert takeover.status_code == 201, takeover.text
    takeover_session_id = takeover.json()["id"]
    assert takeover.json()["status"] == "active"
    assert takeover.json()["audit"]["media_connected"] is False
    assert takeover.json()["audit"]["control_plane_only"] is True
    assert takeover.json()["audit"]["customer_notified"] is False
    assert takeover.json()["audit"]["notify_customer"] is True

    audit = await client.get(
        f"/api/calls/{call.id}/takeover/{takeover_session_id}/audit",
        headers=headers,
    )
    assert audit.status_code == 200, audit.text
    assert audit.json()["total_events"] == 1
    assert audit.json()["audit"][0]["event"] == "takeover_joined"
    takeovers = await client.get(
        f"/api/calls/{call.id}/takeover", headers=headers
    )
    assert takeovers.status_code == 200, takeovers.text
    assert len(takeovers.json()) == 1
    assert takeovers.json()[0]["id"] == takeover_session_id

    leave = await client.post(
        f"/api/calls/{call.id}/takeover/{takeover_session_id}/leave",
        headers=headers,
    )
    assert leave.status_code == 200, leave.text
    assert leave.json()["status"] == "ended"
    assert leave.json()["audit"]["media_connected"] is False
    assert leave.json()["audit"]["audit"][-1]["event"] == "takeover_left"

    ended = await client.post(
        f"/api/calls/{call.id}/monitor/{monitor_session_id}/end",
        headers=headers,
    )
    assert ended.status_code == 200, ended.text
    assert ended.json()["status"] == "ended"

    persisted_call = await client.get(f"/api/calls/{call.id}", headers=headers)
    assert persisted_call.status_code == 200, persisted_call.text
    assert persisted_call.json()["status"] == call.status.value
