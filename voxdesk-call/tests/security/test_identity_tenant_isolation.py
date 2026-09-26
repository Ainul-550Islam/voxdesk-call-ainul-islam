"""Unlink cannot be used to reach a mapping that belongs to another workspace.

The list and delete routes answer 404 for a foreign connection id, the same
answer they give for an id that does not exist. The mapping, the user, and the
role in the owning workspace are unchanged.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.auth.identity.models import IdentityMapping
from app.db.models import User, UserRole
from tests.auth.sso.providers import create_saml_connection, post_assertion, start_saml_login
from tests.conftest import auth_headers, make_user

pytestmark = pytest.mark.asyncio


async def test_unlink_does_not_cross_a_tenant_boundary(
    client, db, tenant_a, owner_a, owner_b, idp
):
    person = await make_user(db, tenant_a, UserRole.MANAGER, email="boundary@acme.test")
    connection = await create_saml_connection(
        client, owner_a, idp, slug="boundary", allow_account_linking=True
    )
    started = await start_saml_login(client, slug="boundary")
    completed = await post_assertion(
        client,
        idp,
        started=started,
        slug="boundary",
        response=idp.response(
            request_id=started["request_id"],
            email="boundary@acme.test",
            name_id="subject-boundary",
        ),
    )
    assert completed.status_code == 200, completed.text

    mapping = (
        await db.execute(
            select(IdentityMapping).where(IdentityMapping.user_id == person.id)
        )
    ).scalar_one()

    foreign = await auth_headers(client, owner_b)
    listed = await client.get(
        f"/api/sso/connections/{connection['id']}/links", headers=foreign
    )
    deleted = await client.delete(
        f"/api/sso/connections/{connection['id']}/links/{mapping.id}",
        headers=foreign,
    )
    assert listed.status_code == 404, listed.text
    assert deleted.status_code == 404, deleted.text

    still = (
        await db.execute(
            select(IdentityMapping)
            .where(IdentityMapping.id == mapping.id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert still.tenant_id == owner_a.tenant_id
    assert still.external_subject == "subject-boundary"

    stored = (
        await db.execute(
            select(User).where(User.id == person.id).execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert stored.role is UserRole.MANAGER
    assert stored.tenant_id == owner_a.tenant_id
