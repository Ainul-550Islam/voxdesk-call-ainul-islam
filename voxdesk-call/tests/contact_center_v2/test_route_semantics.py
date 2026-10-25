from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from tests.contact_center_v2.test_routes_rbac_tenancy import auth_headers


async def test_precheck_get_never_refreshes_and_post_idempotency_skips_mutation(
    client: AsyncClient, db: AsyncSession, policy_factory, lead_factory, manager_a: User,
):
    await lead_factory()
    policy = await policy_factory()
    headers = await auth_headers(client, manager_a)
    prepared = await client.post(
        f"/api/cc2/dialer/policies/{policy.id}/prepare",
        headers={**headers, "Idempotency-Key": "prepare-route-semantics"},
    )
    assert prepared.status_code == 200, prepared.text
    version = prepared.json()["policy"]["dnc_scrub_version"]
    scrubbed_at = prepared.json()["policy"]["dnc_scrubbed_at"]

    replay = await client.post(
        f"/api/cc2/dialer/policies/{policy.id}/prepare",
        headers={**headers, "Idempotency-Key": "prepare-route-semantics"},
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["replayed"] is True
    assert replay.json()["policy"]["dnc_scrub_version"] == version
    assert replay.json()["policy"]["dnc_scrubbed_at"] == scrubbed_at

    read = await client.get(
        f"/api/cc2/dialer/policies/{policy.id}/precheck?refresh=true", headers=headers
    )
    assert read.status_code == 200, read.text
    await db.refresh(policy)
    assert policy.dnc_scrub_version == version

    refreshed = await client.post(
        f"/api/cc2/dialer/policies/{policy.id}/precheck",
        headers={**headers, "Idempotency-Key": "precheck-route-semantics"},
    )
    assert refreshed.status_code == 200, refreshed.text
    refreshed_version = refreshed.json()["evidence"]["dnc_scrub_version"]
    assert refreshed_version != version

    refreshed_replay = await client.post(
        f"/api/cc2/dialer/policies/{policy.id}/precheck",
        headers={**headers, "Idempotency-Key": "precheck-route-semantics"},
    )
    assert refreshed_replay.status_code == 200, refreshed_replay.text
    assert refreshed_replay.json()["replayed"] is True
    assert refreshed_replay.json()["evidence"]["dnc_scrub_version"] == refreshed_version
