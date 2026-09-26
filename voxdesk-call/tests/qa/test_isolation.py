"""A foreign tenant is missing. A client tenant id cannot switch the principal."""

from __future__ import annotations

import uuid

import pytest

from app.db.models import EnvironmentMembership, Speaker, Turn, UserRole
from app.qa.exports import rows_for, to_json
from app.qa.metrics import snapshot
from app.qa.repository import review_items
from app.qa.rubrics import create_scorecard
from app.qa.service import assign_review, create_review
from app.tenancy.isolation import BoundaryDenied
from tests.acd_support import live_call, production
from tests.conftest import auth_headers, make_user


SECTIONS = [
    {
        "name": "Opening",
        "weight": 1,
        "items": [{"name": "Greeting", "weight": 1, "min_score": 0, "max_score": 100, "required": True}],
    }
]


@pytest.mark.asyncio
async def test_other_tenant_cannot_see_a_review_scorecard_or_export(
    client, db, tenant_a, tenant_b, manager_a, owner_a
):
    env = await production(db, tenant_a)
    call = await live_call(db, tenant_a, env)
    call.recording_url = "https://secret.example/recording"
    secret = "customer-secret-phrase-should-not-leak"
    db.add(Turn(call_id=call.id, speaker=Speaker.USER, text=secret))
    card = await create_scorecard(db, tenant_id=tenant_a.id, name="Private", sections=SECTIONS)
    review, _ = await create_review(
        db, tenant_id=tenant_a.id, call_id=call.id, scorecard_id=card.id, actor_id=owner_a.id
    )
    await db.commit()
    other = await make_user(db, tenant_b, UserRole.OWNER)
    headers = await auth_headers(client, other)
    review_response = await client.get(f"/api/qa/reviews/{review.id}", headers=headers)
    card_response = await client.get(f"/api/qa/scorecards/{card.id}", headers=headers)
    assert review_response.status_code == 404
    assert card_response.status_code == 404
    assert review_response.json() == card_response.json()
    assert str(tenant_a.id) not in review_response.text
    assert secret not in review_response.text
    listed = await client.get("/api/qa/reviews", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["reviews"] == []
    conversation = await client.get(f"/api/conversations/{call.id}/intelligence", headers=headers)
    assert conversation.status_code == 404
    assert secret not in conversation.text
    assert "recording" not in conversation.text.lower()
    own_headers = await auth_headers(client, manager_a)
    own = await client.get(f"/api/conversations/{call.id}/intelligence", headers=own_headers)
    assert own.status_code == 200
    body = own.json()
    assert body["recording_url"] is None
    assert body["transcript_copied"] is False
    assert secret not in own.text
    exported = to_json(await rows_for(db, tenant_b.id, actor_tenant_id=tenant_b.id))
    assert str(review.id) not in exported
    assert secret not in exported
    assert "https://secret.example/recording" not in exported
    metrics = await snapshot(db, tenant_b.id)
    assert metrics["review_count"] == 0


@pytest.mark.asyncio
async def test_client_tenant_id_cannot_create_a_review_in_another_tenant(
    client, db, tenant_a, tenant_b, manager_a, viewer_a
):
    env = await production(db, tenant_b)
    foreign_call = await live_call(db, tenant_b, env)
    card = await create_scorecard(db, tenant_id=tenant_b.id, name="Foreign", sections=SECTIONS)
    await db.commit()
    headers = await auth_headers(client, manager_a)
    switched = await client.post(
        "/api/qa/reviews",
        json={
            "call_id": str(foreign_call.id),
            "scorecard_id": str(card.id),
            "tenant_id": str(tenant_b.id),
        },
        headers=headers,
    )
    assert switched.status_code == 404
    scorecard = await client.post(
        "/api/qa/scorecards",
        json={"name": "Stolen", "sections": SECTIONS, "tenant_id": str(tenant_b.id)},
        headers=headers,
    )
    assert scorecard.status_code == 404
    viewer = await auth_headers(client, viewer_a)
    denied = await client.post(
        "/api/qa/scorecards",
        json={"name": "Nope", "sections": SECTIONS},
        headers=viewer,
    )
    assert denied.status_code == 403
    finalize = await client.post(f"/api/qa/reviews/{uuid.uuid4()}/finalize", json={}, headers=headers)
    assert finalize.status_code == 403


@pytest.mark.asyncio
async def test_revoked_environment_membership_cannot_assign(db, tenant_a, manager_a, agent_a):
    env = await production(db, tenant_a)
    call = await live_call(db, tenant_a, env)
    card = await create_scorecard(db, tenant_id=tenant_a.id, name="Scoped", sections=SECTIONS)
    review, _ = await create_review(
        db, tenant_id=tenant_a.id, call_id=call.id, scorecard_id=card.id, actor_id=manager_a.id
    )
    db.add(
        EnvironmentMembership(
            environment_id=env.id,
            user_id=agent_a.id,
            role=UserRole.AGENT,
            status="revoked",
        )
    )
    await db.commit()
    with pytest.raises(BoundaryDenied):
        await assign_review(
            db,
            tenant_id=tenant_a.id,
            review_id=review.id,
            assignee_id=agent_a.id,
            actor_id=manager_a.id,
        )
    items = await review_items(db, tenant_a.id, review.id)
    assert items
    assert review.assignee_id is None
