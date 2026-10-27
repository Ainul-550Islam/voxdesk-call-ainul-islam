"""Mobile PKCE sign-in: code issue, single use, rotation and reuse detection.

The refresh-token reuse test is the one that matters: a stolen refresh token and a
legitimate refresh must not both succeed, because that is the signal that a token
leaked and the whole family has to be revoked.
"""

from __future__ import annotations

import base64
import hashlib
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.db.models import UserRole
from app.db.platform_v2_models import MobileAuthCode, MobileRefreshToken
from app.platform_v2.errors import Conflict, NotFound, ValidationFailed
from app.platform_v2.mobile import oauth
from tests.conftest import make_user
from tests.platform_v2.conftest import PlatScope

VERIFIER = "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJKLMNOPQRSTUV"
assert len(VERIFIER) >= 43


def _challenge(verifier: str = VERIFIER) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")


@pytest.fixture
async def mobile_user(db, plat_scope: PlatScope):
    return await make_user(db, plat_scope.tenant, UserRole.AGENT)


async def _code(db, scope: PlatScope, user, **overrides):
    kwargs = dict(
        tenant_id=scope.tenant.id,
        user_id=user.id,
        device_id="device-1",
        platform="ios",
        code_challenge=_challenge(),
    )
    kwargs.update(overrides)
    return await oauth.authorize(db, commit=True, **kwargs)


# --- primitives ---------------------------------------------------------------


def test_s256_matches_the_rfc7636_appendix_example():
    # RFC 7636 appendix B: this exact pair is what every PKCE client is tested against.
    assert oauth.s256("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk") == (
        "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    )


def test_the_verifier_length_and_alphabet_are_enforced():
    with pytest.raises(ValidationFailed):
        oauth.validate_verifier("too-short")
    with pytest.raises(ValidationFailed):
        oauth.validate_verifier("a" * 129)
    with pytest.raises(ValidationFailed):
        oauth.validate_verifier("a" * 42 + "!")
    assert oauth.validate_verifier("a" * 43) == "a" * 43


def test_a_redirect_uri_must_be_https_or_a_custom_scheme():
    assert oauth.validate_redirect("https://app.voxdesk.ai/cb") == "https://app.voxdesk.ai/cb"
    assert oauth.validate_redirect("voxdesk://auth/callback") == "voxdesk://auth/callback"
    assert oauth.validate_redirect("") == ""
    with pytest.raises(ValidationFailed):
        oauth.validate_redirect("http://app.voxdesk.ai/cb")


# --- authorize ----------------------------------------------------------------


async def test_authorize_stores_only_a_hash_of_the_code(db, plat_scope: PlatScope, mobile_user):
    authorization = await _code(db, plat_scope, mobile_user, redirect_uri="voxdesk://cb")
    assert authorization.expires_at > authorization.expires_at.replace(microsecond=0)
    assert authorization.device_id == "device-1"

    row = (await db.execute(select(MobileAuthCode))).scalars().one()
    assert row.code_hash == oauth.hash_code(authorization.code)
    assert authorization.code not in str(row.code_hash)
    assert row.consumed_at is None
    assert row.redirect_uri == "voxdesk://cb"
    # The very short lifetime is what makes a stolen code hard to use.
    assert (row.expires_at - row.created_at) <= timedelta(seconds=oauth.CODE_TTL_SECONDS + 1)


async def test_authorize_refuses_a_plain_pkce_method(db, plat_scope: PlatScope, mobile_user):
    with pytest.raises(ValidationFailed) as caught:
        await _code(db, plat_scope, mobile_user, code_challenge_method="plain")
    assert caught.value.detail["code_challenge_method"] == "plain"


async def test_authorize_refuses_a_short_challenge_and_a_short_device_id(db, plat_scope: PlatScope, mobile_user):
    with pytest.raises(ValidationFailed):
        await _code(db, plat_scope, mobile_user, code_challenge="short")
    with pytest.raises(ValidationFailed):
        await _code(db, plat_scope, mobile_user, device_id="ab")
    with pytest.raises(ValidationFailed):
        await _code(db, plat_scope, mobile_user, platform="web")


async def test_a_second_authorize_invalidates_the_first_code(db, plat_scope: PlatScope, mobile_user):
    first = await _code(db, plat_scope, mobile_user)
    second = await _code(db, plat_scope, mobile_user)
    rows = (await db.execute(select(MobileAuthCode))).scalars().all()
    by_hash = {row.code_hash: row for row in rows}
    assert by_hash[oauth.hash_code(first.code)].consumed_at is not None
    assert by_hash[oauth.hash_code(second.code)].consumed_at is None


# --- exchange -----------------------------------------------------------------


async def test_exchange_issues_a_pair_and_consumes_the_code(db, plat_scope: PlatScope, mobile_user):
    authorization = await _code(db, plat_scope, mobile_user)
    pair = await oauth.exchange(
        db, code=authorization.code, code_verifier=VERIFIER, device_id="device-1", commit=True
    )
    assert pair.token_type == "Bearer"
    assert pair.access_token
    assert pair.refresh_token
    assert pair.expires_in > 0

    code_row = (await db.execute(select(MobileAuthCode))).scalars().one()
    assert code_row.consumed_at is not None

    stored = (await db.execute(select(MobileRefreshToken))).scalars().one()
    # Only the digest is stored: a database dump does not hand out sessions.
    assert stored.token_hash != pair.refresh_token
    assert stored.token_hash == oauth.hash_refresh_token(pair.refresh_token)
    assert stored.family_id == pair.family_id
    assert stored.device_id == "device-1"


async def test_exchange_rejects_a_wrong_verifier_and_leaves_the_code_usable(
    db, plat_scope: PlatScope, mobile_user
):
    authorization = await _code(db, plat_scope, mobile_user)
    with pytest.raises(Conflict) as caught:
        await oauth.exchange(
            db, code=authorization.code, code_verifier="x" * 43, device_id="device-1", commit=True
        )
    assert "code_challenge" in str(caught.value)
    row = (await db.execute(select(MobileAuthCode))).scalars().one()
    assert row.consumed_at is None, "a wrong verifier must not burn a valid code"

    # The real verifier still works, so a fat-fingered retry is recoverable.
    pair = await oauth.exchange(
        db, code=authorization.code, code_verifier=VERIFIER, device_id="device-1", commit=True
    )
    assert pair.refresh_token


async def test_a_code_cannot_be_exchanged_twice(db, plat_scope: PlatScope, mobile_user):
    authorization = await _code(db, plat_scope, mobile_user)
    await oauth.exchange(db, code=authorization.code, code_verifier=VERIFIER, device_id="device-1", commit=True)
    with pytest.raises(Conflict) as caught:
        await oauth.exchange(db, code=authorization.code, code_verifier=VERIFIER, device_id="device-1", commit=True)
    assert "already been used" in str(caught.value)


async def test_an_expired_code_is_refused(db, plat_scope: PlatScope, mobile_user):
    authorization = await _code(db, plat_scope, mobile_user)
    row = (await db.execute(select(MobileAuthCode))).scalars().one()
    row.expires_at = row.expires_at - timedelta(seconds=oauth.CODE_TTL_SECONDS + 60)
    await db.commit()
    with pytest.raises(Conflict) as caught:
        await oauth.exchange(db, code=authorization.code, code_verifier=VERIFIER, device_id="device-1", commit=True)
    assert "expired" in str(caught.value)


async def test_a_code_is_bound_to_its_device_and_redirect(db, plat_scope: PlatScope, mobile_user):
    authorization = await _code(db, plat_scope, mobile_user, redirect_uri="voxdesk://cb")
    with pytest.raises(Conflict):
        await oauth.exchange(
            db, code=authorization.code, code_verifier=VERIFIER, device_id="other-device", commit=True
        )
    with pytest.raises(Conflict):
        await oauth.exchange(
            db,
            code=authorization.code,
            code_verifier=VERIFIER,
            device_id="device-1",
            redirect_uri="voxdesk://evil",
            commit=True,
        )


async def test_exchange_works_without_a_tenant_because_the_code_proves_it(
    db, plat_scope: PlatScope, mobile_user
):
    authorization = await _code(db, plat_scope, mobile_user)
    pair = await oauth.exchange(db, code=authorization.code, code_verifier=VERIFIER, device_id="device-1", commit=True)
    stored = (await db.execute(select(MobileRefreshToken))).scalars().one()
    assert stored.tenant_id == plat_scope.tenant.id
    assert pair.family_id == stored.family_id


async def test_an_unknown_code_is_not_found(db, plat_scope: PlatScope, mobile_user):
    with pytest.raises(NotFound):
        await oauth.exchange(db, code="never-issued", code_verifier=VERIFIER, device_id="device-1")


async def test_exchange_refuses_an_inactive_user(db, plat_scope: PlatScope, mobile_user):
    authorization = await _code(db, plat_scope, mobile_user)
    mobile_user.is_active = False
    await db.commit()
    with pytest.raises(NotFound) as caught:
        await oauth.exchange(db, code=authorization.code, code_verifier=VERIFIER, device_id="device-1", commit=True)
    assert "user not found" in str(caught.value)


# --- refresh ------------------------------------------------------------------


async def test_refresh_rotates_the_token_and_links_the_chain(db, plat_scope: PlatScope, mobile_user):
    authorization = await _code(db, plat_scope, mobile_user)
    first = await oauth.exchange(db, code=authorization.code, code_verifier=VERIFIER, device_id="device-1", commit=True)
    second = await oauth.refresh(db, refresh_token=first.refresh_token, device_id="device-1", commit=True)

    assert second.refresh_token != first.refresh_token
    assert second.family_id == first.family_id

    rows = {
        row.token_hash: row
        for row in (await db.execute(select(MobileRefreshToken))).scalars().all()
    }
    predecessor = rows[oauth.hash_refresh_token(first.refresh_token)]
    successor = rows[oauth.hash_refresh_token(second.refresh_token)]
    assert predecessor.revoked_at is not None
    assert predecessor.revoked_reason == "rotated"
    assert predecessor.replaced_by_id == successor.id
    assert successor.revoked_at is None


async def test_reusing_a_rotated_token_revokes_the_whole_family(db, plat_scope: PlatScope, mobile_user):
    authorization = await _code(db, plat_scope, mobile_user)
    first = await oauth.exchange(db, code=authorization.code, code_verifier=VERIFIER, device_id="device-1", commit=True)
    second = await oauth.refresh(db, refresh_token=first.refresh_token, device_id="device-1", commit=True)

    with pytest.raises(Conflict) as caught:
        await oauth.refresh(db, refresh_token=first.refresh_token, device_id="device-1", commit=True)
    assert "reuse" in str(caught.value)
    assert caught.value.detail["family_id"] == str(first.family_id)

    # The live successor is collateral damage by design.
    with pytest.raises(Conflict):
        await oauth.refresh(db, refresh_token=second.refresh_token, device_id="device-1", commit=True)

    rows = (await db.execute(select(MobileRefreshToken))).scalars().all()
    assert all(row.revoked_at is not None for row in rows)


async def test_an_expired_refresh_token_is_refused(db, plat_scope: PlatScope, mobile_user):
    authorization = await _code(db, plat_scope, mobile_user)
    pair = await oauth.exchange(db, code=authorization.code, code_verifier=VERIFIER, device_id="device-1", commit=True)
    row = (await db.execute(select(MobileRefreshToken))).scalars().one()
    row.expires_at = row.expires_at - timedelta(days=400)
    await db.commit()
    with pytest.raises(Conflict) as caught:
        await oauth.refresh(db, refresh_token=pair.refresh_token, device_id="device-1", commit=True)
    assert "expired" in str(caught.value)


async def test_a_refresh_token_from_another_device_is_refused(db, plat_scope: PlatScope, mobile_user):
    authorization = await _code(db, plat_scope, mobile_user)
    pair = await oauth.exchange(db, code=authorization.code, code_verifier=VERIFIER, device_id="device-1", commit=True)
    with pytest.raises(Conflict) as caught:
        await oauth.refresh(db, refresh_token=pair.refresh_token, device_id="device-9", commit=True)
    assert "another device" in str(caught.value)


async def test_an_unknown_refresh_token_is_not_found(db, plat_scope: PlatScope, mobile_user):
    with pytest.raises(NotFound):
        await oauth.refresh(db, refresh_token="not-a-token-" + uuid.uuid4().hex)


# --- revoke and sessions ------------------------------------------------------


async def test_signing_out_revokes_every_token_in_the_family(db, plat_scope: PlatScope, mobile_user):
    authorization = await _code(db, plat_scope, mobile_user)
    first = await oauth.exchange(db, code=authorization.code, code_verifier=VERIFIER, device_id="device-1", commit=True)
    second = await oauth.refresh(db, refresh_token=first.refresh_token, device_id="device-1", commit=True)

    assert await oauth.revoke(db, refresh_token=second.refresh_token, commit=True) == 1
    rows = (await db.execute(select(MobileRefreshToken))).scalars().all()
    assert {row.revoked_reason for row in rows if row.revoked_at is not None} == {"rotated", "signed_out"}


async def test_a_revoked_token_is_not_found_for_the_wrong_user(db, plat_scope: PlatScope, mobile_user):
    authorization = await _code(db, plat_scope, mobile_user)
    pair = await oauth.exchange(db, code=authorization.code, code_verifier=VERIFIER, device_id="device-1", commit=True)
    with pytest.raises(NotFound):
        await oauth.revoke(db, refresh_token=pair.refresh_token, user_id=uuid.uuid4(), commit=True)


async def test_sessions_are_listed_per_user_and_expire_out_of_the_list(
    db, plat_scope: PlatScope, mobile_user
):
    authorization = await _code(db, plat_scope, mobile_user)
    pair = await oauth.exchange(db, code=authorization.code, code_verifier=VERIFIER, device_id="device-1", commit=True)
    sessions = await oauth.active_sessions(db, tenant_id=plat_scope.tenant.id, user_id=mobile_user.id)
    assert len(sessions) == 1
    described = oauth.describe_session(sessions[0])
    assert described["device_id"] == "device-1"
    assert described["family_id"] == str(pair.family_id)
    assert described["reuse_detected_at"] is None

    assert await oauth.active_sessions(db, tenant_id=plat_scope.tenant.id, user_id=uuid.uuid4()) == []


async def test_revoking_a_device_only_touches_that_device(db, plat_scope: PlatScope, mobile_user):
    laptop = await _code(db, plat_scope, mobile_user, device_id="device-1")
    phone = await _code(db, plat_scope, mobile_user, device_id="device-2")
    await oauth.exchange(db, code=laptop.code, code_verifier=VERIFIER, device_id="device-1", commit=True)
    await oauth.exchange(db, code=phone.code, code_verifier=VERIFIER, device_id="device-2", commit=True)

    assert await oauth.revoke_device_sessions(
        db, tenant_id=plat_scope.tenant.id, user_id=mobile_user.id, device_id="device-2", commit=True
    ) == 1
    live = await oauth.active_sessions(db, tenant_id=plat_scope.tenant.id, user_id=mobile_user.id)
    assert [row.device_id for row in live] == ["device-1"]


async def test_purge_removes_old_codes_and_tokens_but_keeps_live_sessions(
    db, plat_scope: PlatScope, mobile_user
):
    live = await _code(db, plat_scope, mobile_user)
    pair = await oauth.exchange(db, code=live.code, code_verifier=VERIFIER, device_id="device-1", commit=True)

    stale = MobileAuthCode(
        tenant_id=plat_scope.tenant.id,
        user_id=mobile_user.id,
        device_id="old",
        platform="ios",
        code_hash=oauth.hash_code("stale"),
        code_challenge=_challenge(),
        code_challenge_method="S256",
        redirect_uri="",
        expires_at=live.expires_at - timedelta(days=2),
    )
    db.add(stale)
    await db.commit()

    removed = await oauth.purge_expired(db, tenant_id=plat_scope.tenant.id, commit=True)
    assert removed == 1
    remaining = (await db.execute(select(MobileAuthCode))).scalars().all()
    # Only the expired code is deleted; the consumed-but-live one keeps its audit trail.
    assert [row.code_hash for row in remaining] == [oauth.hash_code(live.code)]
    assert len((await db.execute(select(MobileRefreshToken))).scalars().all()) == 1
    assert await oauth.active_sessions(db, tenant_id=plat_scope.tenant.id, user_id=mobile_user.id)
    assert pair.refresh_token
