"""End-to-end MFA login lifecycle through the real HTTP auth routes (Part 7 / Gate G9).

Covers the complete user journey across HTTP endpoints in a single flow:
1. User signs in while MFA is optional, calls ``POST /api/mfa/enroll`` and ``POST /api/mfa/enroll/confirm`` to activate TOTP and receive single-use recovery codes.
2. Tenant enables ``mfa_required = True`` via ``PATCH /api/identity/policy``.
3. ``POST /auth/login`` returns HTTP 202 with ``mfa_required=True`` and a ``challenge`` token (no access token yet).
4. ``POST /auth/mfa/verify`` with a valid TOTP code completes login and issues an MFA-verified session JWT.
5. Second login completed using a single-use recovery code works once and rejects reuse of the same recovery code on a third login.
6. Repeated wrong codes on ``POST /auth/mfa/verify`` lock the MFA challenge (`423`/`429`/`401` with `mfa_challenge_locked`) and refuse even a valid TOTP code on that locked challenge.
"""

from __future__ import annotations

import pytest

from app.core.config import settings
from tests.conftest import TEST_PASSWORD, auth_headers, login, require_mfa

pytestmark = pytest.mark.asyncio


async def test_full_mfa_enrollment_login_and_recovery_code_e2e(
    client, db, owner_a, totp_clock
):
    """Full HTTP lifecycle: enroll -> confirm -> login challenge -> TOTP verify -> recovery code once."""
    # 1. Enforce MFA for the tenant and authenticate before user factor is enrolled
    await require_mfa(client, owner_a)
    initial_headers = await auth_headers(client, owner_a)
    enroll_res = await client.post("/api/mfa/enroll", headers=initial_headers)
    assert enroll_res.status_code == 201, enroll_res.text
    enroll_body = enroll_res.json()
    secret = enroll_body["secret"]
    assert secret
    assert enroll_body["provisioning_uri"].startswith("otpauth://totp/")

    confirm_res = await client.post(
        "/api/mfa/enroll/confirm",
        headers=initial_headers,
        json={"code": totp_clock.consume(secret)},
    )
    assert confirm_res.status_code == 200, confirm_res.text
    recovery_codes = confirm_res.json()["recovery_codes"]
    assert len(recovery_codes) == settings.mfa_recovery_code_count

    # 3. Password login returns 202 + challenge (no access token)
    login_step1 = await login(client, owner_a.email, TEST_PASSWORD)
    assert login_step1.status_code == 202, login_step1.text
    challenge_1 = login_step1.json()["challenge"]
    assert "access_token" not in login_step1.json()

    # 4. Complete login with TOTP code
    verify_totp = await client.post(
        "/auth/mfa/verify",
        json={"challenge": challenge_1, "code": totp_clock.consume(secret)},
    )
    assert verify_totp.status_code == 200, verify_totp.text
    totp_token = verify_totp.json()["access_token"]
    assert totp_token

    status_res = await client.get(
        "/api/mfa/status",
        headers={"Authorization": f"Bearer {totp_token}"},
    )
    assert status_res.status_code == 200
    assert status_res.json()["enrolled"] is True
    assert (
        status_res.json()["recovery_codes_remaining"]
        == settings.mfa_recovery_code_count
    )

    # 5. Start a new login and complete it using recovery_codes[0]
    login_step2 = await login(client, owner_a.email, TEST_PASSWORD)
    assert login_step2.status_code == 202
    challenge_2 = login_step2.json()["challenge"]

    verify_recovery = await client.post(
        "/auth/mfa/verify",
        json={"challenge": challenge_2, "code": recovery_codes[0]},
    )
    assert verify_recovery.status_code == 200, verify_recovery.text
    rec_token = verify_recovery.json()["access_token"]
    assert rec_token

    # Verify recovery_codes_remaining decremented by 1
    status_after = await client.get(
        "/api/mfa/status",
        headers={"Authorization": f"Bearer {rec_token}"},
    )
    assert (
        status_after.json()["recovery_codes_remaining"]
        == settings.mfa_recovery_code_count - 1
    )

    # 6. Reusing the spent recovery_codes[0] on a third login challenge is rejected
    login_step3 = await login(client, owner_a.email, TEST_PASSWORD)
    assert login_step3.status_code == 202
    challenge_3 = login_step3.json()["challenge"]

    reused_recovery = await client.post(
        "/auth/mfa/verify",
        json={"challenge": challenge_3, "code": recovery_codes[0]},
    )
    assert reused_recovery.status_code == 401
    assert "access_token" not in reused_recovery.text


async def test_mfa_login_lockout_after_repeated_bad_codes(
    client, db, owner_a, totp_clock
):
    """Repeated wrong MFA codes lock the login challenge so even a valid code cannot spend it."""
    await require_mfa(client, owner_a)
    initial_headers = await auth_headers(client, owner_a)
    enroll_res = await client.post("/api/mfa/enroll", headers=initial_headers)
    secret = enroll_res.json()["secret"]
    await client.post(
        "/api/mfa/enroll/confirm",
        headers=initial_headers,
        json={"code": totp_clock.consume(secret)},
    )

    started = await login(client, owner_a.email, TEST_PASSWORD)
    assert started.status_code == 202
    challenge = started.json()["challenge"]

    for _ in range(settings.mfa_challenge_max_failures):
        bad = await client.post(
            "/auth/mfa/verify",
            json={"challenge": challenge, "code": "000000"},
        )
        assert bad.status_code in (401, 423, 429)

    # Now even a valid TOTP code on that burned challenge must be refused
    locked = await client.post(
        "/auth/mfa/verify",
        json={"challenge": challenge, "code": totp_clock.consume(secret)},
    )
    assert locked.status_code in (401, 423, 429)
    assert "access_token" not in locked.text
