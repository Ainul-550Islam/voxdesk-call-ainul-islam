"""Incidents, breach clocks, access-review campaigns, DSAR and HIPAA safeguards.

These are the controls an auditor asks to see operating: an incident has a
timeline nobody can rewrite, a regulatory clock records when notice was given,
an access-review campaign can only be decided by its reviewers, a DSAR export is
signed and can be re-verified later, and break-glass access needs a second
person and expires on its own.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from app.auth.identity.models import UserSession
from app.compliance_v2.hipaa import session_policy as auto_logoff
from tests.conftest import auth_headers

UTC = dt.timezone.utc


def _url(tenant_id, path: str = "") -> str:
    return f"/api/tenants/{tenant_id}/compliance{path}"


def _now() -> dt.datetime:
    return dt.datetime.now(UTC)


def record_values(record: dict) -> str:
    """Everything in a record, flattened, for a "no plaintext left" assertion."""
    return " ".join(str(value) for value in record.values())


# ----------------------------------------------------------------- incidents ---


async def test_session_sweep_revokes_idle_and_expired_sessions(db, tenant_a, agent_a):
    now = _now()
    idle = UserSession(
        tenant_id=tenant_a.id,
        user_id=agent_a.id,
        created_at=now - dt.timedelta(hours=2),
        last_seen_at=now - dt.timedelta(hours=2),
        expires_at=now + dt.timedelta(hours=10),
        idle_expires_at=now - dt.timedelta(minutes=1),
    )
    expired = UserSession(
        tenant_id=tenant_a.id,
        user_id=agent_a.id,
        created_at=now - dt.timedelta(days=2),
        last_seen_at=now - dt.timedelta(days=1),
        expires_at=now - dt.timedelta(minutes=5),
        idle_expires_at=now + dt.timedelta(hours=1),
    )
    live = UserSession(
        tenant_id=tenant_a.id,
        user_id=agent_a.id,
        created_at=now,
        last_seen_at=now,
        expires_at=now + dt.timedelta(hours=8),
        idle_expires_at=now + dt.timedelta(minutes=30),
    )
    db.add_all([idle, expired, live])
    await db.commit()

    limits = auto_logoff.limits_for(hipaa_mode=False)
    outcome = await auto_logoff.sweep(db, tenant_id=tenant_a.id, limits=limits)
    await db.commit()
    for row in (idle, expired, live):
        await db.refresh(row)

    assert outcome["checked"] == 2
    assert outcome["idle"] == 1
    assert outcome["absolute"] == 1
    assert idle.revoked_at is not None and idle.revoked_reason
    assert expired.revoked_at is not None
    assert live.revoked_at is None


async def test_hipaa_mode_shortens_the_session_windows(db):
    standard = auto_logoff.limits_for(hipaa_mode=False)
    strict = auto_logoff.limits_for(hipaa_mode=True)

    assert strict.idle_minutes <= standard.idle_minutes
    assert strict.absolute_minutes <= standard.absolute_minutes
    assert strict.hipaa_mode is True


async def test_sweep_is_tenant_scoped(db, tenant_a, tenant_b, agent_a):
    now = _now()
    other = UserSession(
        tenant_id=tenant_b.id,
        user_id=agent_a.id,
        created_at=now - dt.timedelta(hours=3),
        last_seen_at=now - dt.timedelta(hours=3),
        expires_at=now + dt.timedelta(hours=5),
        idle_expires_at=now - dt.timedelta(minutes=1),
    )
    db.add(other)
    await db.commit()

    outcome = await auto_logoff.sweep(db, tenant_id=tenant_a.id)
    await db.commit()
    await db.refresh(other)

    assert outcome["checked"] == 0
    assert other.revoked_at is None


async def test_users_within_their_window_are_untouched(db, tenant_a, agent_a):
    now = _now()
    fresh = UserSession(
        tenant_id=tenant_a.id,
        user_id=agent_a.id,
        created_at=now,
        last_seen_at=now,
        expires_at=now + dt.timedelta(hours=8),
        idle_expires_at=now + dt.timedelta(minutes=30),
    )
    db.add(fresh)
    await db.commit()

    outcome = await auto_logoff.sweep(db, tenant_id=tenant_a.id)
    await db.commit()

    rows = list(
        (await db.execute(select(UserSession).where(UserSession.id == fresh.id))).scalars()
    )
    assert outcome["checked"] == 0
    assert rows[0].revoked_at is None


# ------------------------------------- privilege freshness on the wire ---
#
# A re-authentication window that no route consults is documentation, not a
# control. These two tests hold the sensitive endpoints to it: the DSAR erasure
# and the KMS rekey both ask the session-policy module before doing anything.


async def _pending_erasure(db, tenant_a, owner_a, compliance_settings):
    from app.compliance_v2.dsar import service as dsar_service

    request = await dsar_service.create_request(
        db,
        tenant_id=tenant_a.id,
        kind="erase",
        subject_type="user",
        subject_key=owner_a.email,
        requested_by=owner_a.id,
        settings=compliance_settings,
    )
    await dsar_service.mark_verified(
        db, tenant_id=tenant_a.id, request_id=request.id, actor_user_id=owner_a.id
    )
    await db.commit()
    return request


async def test_erasure_is_refused_when_the_session_is_stale(
    client, db, tenant_a, owner_a, compliance_settings, monkeypatch
):
    from app.compliance_v2.hipaa import session_policy

    request = await _pending_erasure(db, tenant_a, owner_a, compliance_settings)
    headers = await auth_headers(client, owner_a)

    def _stale(*_args, **_kwargs):
        raise session_policy.ReauthRequired(
            "erase requires re-authentication within 15 minutes", window_minutes=15
        )

    monkeypatch.setattr(session_policy, "require_recent_auth", _stale)

    response = await client.post(
        _url(tenant_a.id, f"/dsar/requests/{request.id}/execute"), headers=headers
    )

    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "reauth_required"


async def test_the_refusal_is_written_to_the_audit_trail(
    client, db, tenant_a, owner_a, compliance_settings, monkeypatch
):
    from sqlalchemy import select

    from app.compliance_v2.hipaa import session_policy
    from app.db.models import AuditLog

    request = await _pending_erasure(db, tenant_a, owner_a, compliance_settings)
    headers = await auth_headers(client, owner_a)

    def _stale(*_args, **_kwargs):
        raise session_policy.ReauthRequired("stale", window_minutes=15)

    monkeypatch.setattr(session_policy, "require_recent_auth", _stale)
    await client.post(
        _url(tenant_a.id, f"/dsar/requests/{request.id}/execute"), headers=headers
    )

    events = list(
        (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.tenant_id == tenant_a.id,
                    AuditLog.event_type == "sensitive_action_denied",
                )
            )
        ).scalars()
    )
    assert events


async def test_rekey_is_refused_when_the_session_is_stale(
    client, db, tenant_a, owner_a, compliance_settings, monkeypatch
):
    from app.compliance_v2.hipaa import session_policy

    headers = await auth_headers(client, owner_a)

    def _stale(*_args, **_kwargs):
        raise session_policy.ReauthRequired("stale", window_minutes=15)

    monkeypatch.setattr(session_policy, "require_recent_auth", _stale)

    response = await client.post(
        _url(tenant_a.id, "/kms/rekey"), headers=headers, json={"dry_run": True}
    )

    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "reauth_required"


async def test_a_fresh_session_passes_the_control(
    client, db, tenant_a, owner_a, compliance_settings
):
    headers = await auth_headers(client, owner_a)

    response = await client.post(
        _url(tenant_a.id, "/kms/rekey"), headers=headers, json={"dry_run": True}
    )

    assert response.status_code in {200, 201}, response.text
