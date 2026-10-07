from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid

import pytest

from app.db.models import Call, CallDirection, CallStatus, Speaker, Turn
from tests.acd_support import production
from tests.conftest import auth_headers


@pytest.mark.asyncio
async def test_persisted_call_transcript_replay_and_analytics_read_paths(
    client, db, tenant_a, owner_a
):
    environment = await production(db, tenant_a)
    now = datetime.now(timezone.utc)
    call = Call(
        tenant_id=tenant_a.id,
        environment_id=environment.id,
        call_sid=f"PROMPT8-{uuid.uuid4().hex}",
        from_number="+14155550198",
        to_number=tenant_a.twilio_number,
        status=CallStatus.COMPLETED,
        direction=CallDirection.INBOUND,
        started_at=now - timedelta(minutes=2),
        ended_at=now,
        duration_seconds=120,
        summary="Caller received a billing explanation.",
        intent="billing_question",
        booked=False,
        escalated=False,
    )
    db.add(call)
    await db.flush()
    db.add_all(
        [
            Turn(call_id=call.id, speaker=Speaker.USER, text="I need help understanding my invoice."),
            Turn(call_id=call.id, speaker=Speaker.ASSISTANT, text="I can help explain the invoice details."),
        ]
    )
    await db.commit()
    await db.refresh(call)

    headers = await auth_headers(client, owner_a)
    replay = await client.get(f"/api/calls/{call.id}/replay", headers=headers)
    assert replay.status_code == 200, replay.text
    transcript = replay.json()["transcript"] or ""
    assert "understanding my invoice" in transcript.lower()
    assert "explain the invoice" in transcript.lower()

    search = await client.post(
        "/api/calls/search",
        headers=headers,
        json={"status": "COMPLETED", "direction": "inbound", "limit": 10},
    )
    assert search.status_code == 200, search.text
    assert search.json()["total"] == 1
    assert search.json()["calls"][0]["id"] == str(call.id)

    analytics = await client.get(
        "/api/analytics/calls?range=last_30_days", headers=headers
    )
    assert analytics.status_code == 200, analytics.text
    assert analytics.json()["by_status"]["answered"] == 1
    assert analytics.json()["by_direction"]["inbound"] == 1
    overview = await client.get(
        "/api/analytics/overview?range=last_30_days", headers=headers
    )
    assert overview.status_code == 200, overview.text
    assert overview.json()["calls"]["total"] == 1
    assert overview.json()["calls"]["minutes"] == 2.0

    # The custom analysis schema is persisted, but the repository does not run
    # a real hosted LLM analysis in this smoke path. Do not manufacture a result.
    schema = await client.post(
        "/api/analysis/schemas",
        headers=headers,
        json={
            "name": "Prompt 8 call outcome",
            "description": "A stored schema; not an asserted analysis result.",
            "fields": [
                {"name": "resolution", "type": "boolean", "required": True},
                {"name": "summary", "type": "text", "required": True},
            ],
        },
    )
    assert schema.status_code == 201, schema.text
    persisted_schema = await client.get(
        f"/api/analysis/schemas/{schema.json()['id']}", headers=headers
    )
    assert persisted_schema.status_code == 200
    results = await client.get(f"/api/analysis/calls/{call.id}/results", headers=headers)
    assert results.status_code == 200, results.text
    assert results.json()["results"] == []
