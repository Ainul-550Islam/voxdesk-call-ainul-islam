# File: tests/api/test_agent_version_merge.py — POST /api/agents/{id}/versions/merge
"""Three-way merge of agent versions (PART 12, P12-21).

The dashboard's version page has always offered a merge dialog
(`dashboard-next/app/dashboard/agents/[id]/versions/page.tsx`), and until this
route existed it called a path the backend did not serve — a button that could
only ever fail. `tests/api/test_frontend_backend_contract.py` caught it.

What the route must not do is pick a winner silently. So these tests check the
*report*:

1. a change on one side only is merged;
2. the same change on both sides is not a conflict;
3. different changes to the same key are a conflict, and the base value is kept
   so the merged document stays valid;
4. delete-vs-modify is a conflict, delete-vs-untouched is not;
5. ``resolutions`` applies a human's choice and only then reports ``clean``;
6. an unknown version is a 404, an unknown strategy a 422, and another tenant's
   agent a 404 — never a 200 with an empty document.
"""

from __future__ import annotations

import uuid

import pytest

import app.db.models  # noqa: F401
from app.db.models import Agent, AgentVersion, Tenant, UserRole
from tests.conftest import auth_headers, make_tenant, make_user

MERGE = "/api/agents/{agent_id}/versions/merge"


async def _seed_agent(db, tenant: Tenant) -> Agent:
    agent = Agent(
        tenant_id=tenant.id,
        external_key=f"agent-{uuid.uuid4().hex[:10]}",
        name="Merge Target",
        description="",
        agent_type="voice",
        status="published",
    )
    db.add(agent)
    await db.commit()
    await db.refresh(agent)
    return agent


async def _seed_version(db, tenant: Tenant, agent: Agent, number: int, snapshot: dict) -> None:
    db.add(
        AgentVersion(
            tenant_id=tenant.id,
            agent_id=agent.id,
            version_number=number,
            version_label=f"v{number}",
            status="published",
            config_snapshot=snapshot,
            config_hash=f"hash-{number}",
            changelog="",
            published_environment="production",
            is_active=number == 1,
        )
    )
    await db.commit()


@pytest.fixture
def base_snapshot() -> dict:
    return {
        "name": "Merge Target",
        "persona": {"tone": "warm", "language": "en"},
        "first_message": "Hello.",
        "settings": {"interruption": True, "barge_in": False, "filler": "mm-hmm"},
    }


async def _seed_three(client, db, base_snapshot: dict):
    tenant = await make_tenant(db, "Merge Tenant")
    user = await make_user(db, tenant, UserRole.OWNER)
    headers = await auth_headers(client, user)
    agent = await _seed_agent(db, tenant)

    # Two independent edits (greeting, language) and two collisions (tone, filler).
    ours = {
        **base_snapshot,
        "persona": {"tone": "calm", "language": "en"},
        "first_message": "Hello — thanks for calling Acme Dental.",
        "settings": {"interruption": True, "barge_in": False, "filler": "right"},
    }
    theirs = {
        **base_snapshot,
        "persona": {"tone": "brisk", "language": "es"},
        "settings": {"interruption": True, "barge_in": True, "filler": "okay"},
    }
    await _seed_version(db, tenant, agent, 1, base_snapshot)
    await _seed_version(db, tenant, agent, 2, ours)
    await _seed_version(db, tenant, agent, 3, theirs)
    return tenant, headers, agent


@pytest.mark.asyncio
async def test_one_sided_changes_are_merged_even_when_other_keys_conflict(client, db, base_snapshot):
    _, headers, agent = await _seed_three(client, db, base_snapshot)

    resp = await client.post(
        MERGE.format(agent_id=agent.id),
        headers=headers,
        json={"base_version": 1, "ours_version": 2, "theirs_version": 3},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    merged = body["merged"]
    # ours touched the greeting, theirs touched the language: both survive, and a
    # conflict elsewhere does not stop the rest of the document from merging.
    assert merged["first_message"] == "Hello — thanks for calling Acme Dental."
    assert merged["persona"]["language"] == "es"
    assert merged["settings"]["barge_in"] is True
    assert body["clean"] is False


@pytest.mark.asyncio
async def test_divergent_edits_are_reported_as_conflicts(client, db, base_snapshot):
    _, headers, agent = await _seed_three(client, db, base_snapshot)

    resp = await client.post(
        MERGE.format(agent_id=agent.id),
        headers=headers,
        json={"base_version": 1, "ours_version": 2, "theirs_version": 3},
    )

    conflicts = {conflict["path"]: conflict for conflict in resp.json()["conflicts"]}
    assert set(conflicts) == {"persona.tone", "settings.filler"}
    assert conflicts["persona.tone"]["ours"] == "calm"
    assert conflicts["persona.tone"]["theirs"] == "brisk"
    assert conflicts["persona.tone"]["base"] == "warm"
    # Nothing was invented: the conflict keeps the base value in the merged doc.
    assert resp.json()["merged"]["persona"]["tone"] == "warm"
    assert resp.json()["clean"] is False


@pytest.mark.asyncio
async def test_identical_edits_on_both_sides_are_not_conflicts(client, db, base_snapshot):
    tenant = await make_tenant(db, "Same Edit")
    user = await make_user(db, tenant, UserRole.OWNER)
    headers = await auth_headers(client, user)
    agent = await _seed_agent(db, tenant)
    same = {**base_snapshot, "persona": {"tone": "brisk", "language": "en"}}
    await _seed_version(db, tenant, agent, 1, base_snapshot)
    await _seed_version(db, tenant, agent, 2, same)
    await _seed_version(db, tenant, agent, 3, same)

    resp = await client.post(
        MERGE.format(agent_id=agent.id),
        headers=headers,
        json={"base_version": 1, "ours_version": 2, "theirs_version": 3},
    )

    body = resp.json()
    assert body["conflicts"] == []
    assert body["clean"] is True
    assert body["merged"]["persona"]["tone"] == "brisk"


@pytest.mark.asyncio
async def test_delete_versus_modify_conflicts_and_delete_versus_silence_does_not(
    client, db, base_snapshot
):
    tenant = await make_tenant(db, "Delete Tenant")
    user = await make_user(db, tenant, UserRole.OWNER)
    headers = await auth_headers(client, user)
    agent = await _seed_agent(db, tenant)

    ours = dict(base_snapshot)
    ours.pop("first_message")  # deleted on our side
    theirs = {**base_snapshot, "first_message": "Hi there."}  # modified on theirs
    third = dict(base_snapshot)
    third.pop("settings")  # deleted, untouched by the other side

    await _seed_version(db, tenant, agent, 1, base_snapshot)
    await _seed_version(db, tenant, agent, 2, ours)
    await _seed_version(db, tenant, agent, 3, theirs)

    resp = await client.post(
        MERGE.format(agent_id=agent.id),
        headers=headers,
        json={"base_version": 1, "ours_version": 2, "theirs_version": 3},
    )
    conflicts = [conflict["path"] for conflict in resp.json()["conflicts"]]
    assert conflicts == ["first_message"]
    assert resp.json()["conflicts"][0]["ours"] is None

    # delete vs untouched: the key is simply gone, with no conflict.
    await _seed_version(db, tenant, agent, 4, third)
    resp = await client.post(
        MERGE.format(agent_id=agent.id),
        headers=headers,
        json={"base_version": 1, "ours_version": 4, "theirs_version": 3},
    )
    body = resp.json()
    assert "settings" not in body["merged"]
    assert "settings" not in " ".join(conflict["path"] for conflict in body["conflicts"])


@pytest.mark.asyncio
async def test_resolutions_are_applied_and_only_then_is_it_clean(client, db, base_snapshot):
    _, headers, agent = await _seed_three(client, db, base_snapshot)

    resp = await client.post(
        MERGE.format(agent_id=agent.id),
        headers=headers,
        json={
            "base_version": 1,
            "ours_version": 2,
            "theirs_version": 3,
            "strategy": "manual",
            "resolutions": {
                "persona.tone": "ours",
                "settings.filler": "theirs",
            },
        },
    )

    body = resp.json()
    assert body["conflicts"] == []
    assert body["clean"] is True
    assert body["merged"]["persona"]["tone"] == "calm"
    assert body["merged"]["settings"]["filler"] == "okay"


@pytest.mark.asyncio
async def test_strategy_ours_resolves_conflicts_without_asking(client, db, base_snapshot):
    _, headers, agent = await _seed_three(client, db, base_snapshot)

    resp = await client.post(
        MERGE.format(agent_id=agent.id),
        headers=headers,
        json={"base_version": 1, "ours_version": 2, "theirs_version": 3, "strategy": "ours"},
    )

    body = resp.json()
    assert body["merged"]["persona"]["tone"] == "calm"
    assert body["merged"]["settings"]["filler"] == "right"
    # The conflict is still reported: the strategy picks a value, it does not hide the fact.
    assert {conflict["path"] for conflict in body["conflicts"]} == {
        "persona.tone",
        "settings.filler",
    }
    assert body["clean"] is False


@pytest.mark.asyncio
async def test_unknown_version_is_404_and_bad_strategy_is_422(client, db, base_snapshot):
    _, headers, agent = await _seed_three(client, db, base_snapshot)

    missing = await client.post(
        MERGE.format(agent_id=agent.id),
        headers=headers,
        json={"base_version": 1, "ours_version": 2, "theirs_version": 99},
    )
    assert missing.status_code == 404
    assert "99" in missing.text

    bad = await client.post(
        MERGE.format(agent_id=agent.id),
        headers=headers,
        json={"base_version": 1, "ours_version": 2, "theirs_version": 3, "strategy": "last-write-wins"},
    )
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_merge_never_crosses_tenants(client, db, base_snapshot):
    _, headers, agent = await _seed_three(client, db, base_snapshot)

    other_tenant = await make_tenant(db, "Other Tenant")
    other_user = await make_user(db, other_tenant, UserRole.OWNER)
    other_headers = await auth_headers(client, other_user)

    resp = await client.post(
        MERGE.format(agent_id=agent.id),
        headers=other_headers,
        json={"base_version": 1, "ours_version": 2, "theirs_version": 3},
    )
    assert resp.status_code == 404
    assert "merged" not in resp.json()
