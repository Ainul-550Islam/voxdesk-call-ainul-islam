"""A successful sign-in sends a notice. A failed one, and a refresh, do not.

The notice is courtesy. It must not carry the password or the token, and a
failure to send it must not change the HTTP result of a login that already
succeeded.
"""

from __future__ import annotations

import pytest

from app.auth.identity import email as identity_email
from tests.conftest import TEST_PASSWORD, login

pytestmark = pytest.mark.asyncio


async def test_a_successful_password_login_notifies_and_a_failure_does_not(
    client, owner_a, monkeypatch
):
    sent: list[dict] = []

    async def capture(**kwargs):
        sent.append(kwargs)
        return identity_email.DeliveryResult(delivered=True, transport="log")

    monkeypatch.setattr(identity_email, "notify_sign_in", capture)

    refused = await login(client, owner_a.email, "not-the-password")
    assert refused.status_code == 401
    assert sent == []

    accepted = await login(client, owner_a.email, TEST_PASSWORD)
    assert accepted.status_code == 200, accepted.text
    assert len(sent) == 1
    notice = sent[0]
    assert notice["to"] == owner_a.email
    assert notice["method"] == "password"
    assert TEST_PASSWORD not in str(notice)
    assert accepted.json()["access_token"] not in str(notice)

    refreshed = await client.post("/auth/refresh")
    assert refreshed.status_code == 200, refreshed.text
    assert len(sent) == 1, "a refresh is not a new sign-in"


async def test_a_notice_failure_does_not_fail_the_login(client, owner_a, monkeypatch):
    async def explode(**kwargs):
        raise RuntimeError("mail transport down")

    monkeypatch.setattr(identity_email, "send_security_notice", explode)
    accepted = await login(client, owner_a.email, TEST_PASSWORD)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["access_token"]
