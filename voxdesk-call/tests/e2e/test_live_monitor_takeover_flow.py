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
    assert monitor.json()["mode"] == "whisper_ai"
    assert monitor.json()["status"] == "active"
    assert monitor.json()["meta"]["media_connected"] is True
    assert monitor.json()["meta"]["media_status"] == "STREAM_READY"

    whisper = await client.post(
        f"/api/calls/{call.id}/monitor/{monitor_session_id}/whisper",
        headers=headers,
        json={"text": "Please confirm the customer's request."},
    )
    assert whisper.status_code == 200, whisper.text
    assert whisper.json()["whisper"] == "Please confirm the customer's request."
    assert whisper.json()["status"] == "queued_for_pipeline"
    assert whisper.json()["delivery_status"] == "PENDING_PIPELINE"
    assert whisper.json()["media_connected"] is False
    whispers = await client.get(
        f"/api/calls/{call.id}/monitor/{monitor_session_id}/whispers",
        headers=headers,
    )
    assert whispers.status_code == 200, whispers.text
    assert whispers.json()["total"] == 1
    assert whispers.json()["whispers"][0]["text"] == "Please confirm the customer's request."

    # Without configured live carrier credentials, takeover fails closed (HTTP 501/502)
    takeover = await client.post(
        f"/api/calls/{call.id}/takeover",
        headers=headers,
        json={"notify_customer": True},
    )
    assert takeover.status_code in {501, 502}, takeover.text

    ended = await client.post(
        f"/api/calls/{call.id}/monitor/{monitor_session_id}/end",
        headers=headers,
    )
    assert ended.status_code == 200, ended.text
    assert ended.json()["status"] == "ended"

    persisted_call = await client.get(f"/api/calls/{call.id}", headers=headers)
    assert persisted_call.status_code == 200, persisted_call.text
    assert persisted_call.json()["status"] == call.status.value
