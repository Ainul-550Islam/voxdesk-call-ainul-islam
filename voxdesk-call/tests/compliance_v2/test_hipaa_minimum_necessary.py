"""Incidents, breach clocks, access-review campaigns, DSAR and HIPAA safeguards.

These are the controls an auditor asks to see operating: an incident has a
timeline nobody can rewrite, a regulatory clock records when notice was given,
an access-review campaign can only be decided by its reviewers, a DSAR export is
signed and can be re-verified later, and break-glass access needs a second
person and expires on its own.
"""

from __future__ import annotations

import datetime as dt


from app.compliance_v2.hipaa import minimum_necessary, phi_tagging
from app.db.models import UserRole

UTC = dt.timezone.utc


def _now() -> dt.datetime:
    return dt.datetime.now(UTC)


def record_values(record: dict) -> str:
    """Everything in a record, flattened, for a "no plaintext left" assertion."""
    return " ".join(str(value) for value in record.values())


# ----------------------------------------------------------------- incidents ---


async def test_phi_policy_round_trip_and_default(db, tenant_a, owner_a):
    default = await phi_tagging.load_policy(db, tenant_a.id)
    assert default.hipaa_mode is False

    policy = await phi_tagging.set_policy(
        db,
        tenant_id=tenant_a.id,
        hipaa_mode=True,
        baa_on_file=True,
        baa_reference="BAA-2026-001",
        redact_webhooks=True,
        minimum_necessary={"calls": ["transcript"]},
        updated_by=owner_a.id,
    )
    await db.commit()

    stored = await phi_tagging.load_policy(db, tenant_a.id)
    assert stored.hipaa_mode is True
    assert stored.baa_reference == "BAA-2026-001"
    assert policy.hipaa_mode is True


async def test_redaction_removes_phi_columns_without_a_baa(db, tenant_a):
    # HIPAA mode with no signed BAA redacts on every outbound path.
    policy = await phi_tagging.set_policy(
        db,
        tenant_id=tenant_a.id,
        hipaa_mode=True,
        baa_on_file=False,
        baa_reference="",
        redact_webhooks=True,
        minimum_necessary=None,
        updated_by=None,
    )
    await db.commit()

    tagged = phi_tagging.tagged_columns_document()
    table = "contacts"
    phi_column = "email"
    assert phi_column in tagged[table]
    redacted, removed = phi_tagging.redact_record(
        {phi_column: "sensitive", "id": "1"}, table=table, policy=policy
    )

    assert phi_column in removed
    assert redacted[phi_column] == phi_tagging.REDACTION
    assert redacted["id"] == "1"
    assert "sensitive" not in record_values(redacted)


async def test_minimum_necessary_filters_by_role(db, tenant_a):
    policy = await phi_tagging.set_policy(
        db,
        tenant_id=tenant_a.id,
        hipaa_mode=True,
        baa_on_file=True,
        baa_reference="BAA-1",
        redact_webhooks=True,
        minimum_necessary=None,
        updated_by=None,
    )
    await db.commit()

    surface = "export"
    owner_view = minimum_necessary.allowlist_for(UserRole.OWNER, surface, policy=policy)
    agent_view = minimum_necessary.allowlist_for(UserRole.AGENT, surface, policy=policy)
    assert owner_view, "an owner keeps a non-empty allowlist on every surface"
    assert agent_view < owner_view

    filtered, counts = minimum_necessary.filter_records(
        [{"email": "a@example.com", "name": "A", "id": "1"}],
        table="contacts",
        role=UserRole.AGENT,
        surface=surface,
        policy=policy,
    )
    assert len(filtered) == 1
    assert counts == {"email": 1, "name": 1}
    assert "email" not in filtered[0]
    assert filtered[0]["id"] == "1"




# ------------------------------------------------- the request-time guard ---
#
# The module above proves the filtering rules. These prove the rules are
# actually attached to the endpoints that return clinical content: with HIPAA
# mode on, a call transcript read by a role that may not see transcript text
# comes back without the words, and with HIPAA mode off nothing changes.


async def _enable_hipaa(db, tenant_a, owner_a, *, minimum_necessary=None):
    from app.compliance_v2.hipaa import phi_tagging

    await phi_tagging.set_policy(
        db,
        tenant_id=tenant_a.id,
        hipaa_mode=True,
        baa_on_file=False,
        baa_reference="",
        redact_webhooks=True,
        minimum_necessary=minimum_necessary or {},
        updated_by=owner_a.id,
    )
    await db.commit()


async def _call_with_turns(db, tenant_a):
    import uuid as _uuid

    from app.db.models import Speaker, Turn
    from tests.conftest import make_call

    call = await make_call(db, tenant_a)
    db.add_all(
        [
            Turn(call_id=call.id, speaker=Speaker.ASSISTANT, text="How can I help you today?"),
            Turn(call_id=call.id, speaker=Speaker.USER, text="I need to reschedule my MRI."),
        ]
    )
    await db.commit()
    assert isinstance(call.id, _uuid.UUID)
    return call


async def test_a_viewer_reads_a_transcript_without_the_words(client, db, tenant_a, owner_a):
    from tests.conftest import auth_headers

    call = await _call_with_turns(db, tenant_a)
    await _enable_hipaa(db, tenant_a, owner_a)

    viewer = await _make_user(db, tenant_a, "viewer", owner_a)
    response = await client.get(
        f"/api/calls/{call.id}/transcript", headers=await auth_headers(client, viewer)
    )

    assert response.status_code == 200, response.text
    turns = response.json()
    assert turns, "the turn envelope must still be returned"
    assert all("text" not in turn for turn in turns)
    assert all("speaker" in turn for turn in turns)


async def test_an_agent_still_reads_the_words_it_needs(client, db, tenant_a, owner_a, agent_a):
    from tests.conftest import auth_headers

    call = await _call_with_turns(db, tenant_a)
    await _enable_hipaa(db, tenant_a, owner_a)

    response = await client.get(
        f"/api/calls/{call.id}/transcript", headers=await auth_headers(client, agent_a)
    )

    assert response.status_code == 200, response.text
    assert [turn["text"] for turn in response.json()]


async def test_without_hipaa_mode_the_transcript_is_unchanged(client, db, tenant_a, agent_a):
    from tests.conftest import auth_headers

    call = await _call_with_turns(db, tenant_a)

    response = await client.get(
        f"/api/calls/{call.id}/transcript", headers=await auth_headers(client, agent_a)
    )

    assert response.status_code == 200, response.text
    assert all("text" in turn for turn in response.json())


async def test_the_replay_timeline_drops_turn_text_for_a_viewer(client, db, tenant_a, owner_a):
    from tests.conftest import auth_headers

    call = await _call_with_turns(db, tenant_a)
    await _enable_hipaa(db, tenant_a, owner_a)
    viewer = await _make_user(db, tenant_a, "viewer", owner_a)

    response = await client.get(
        f"/api/calls/{call.id}/replay", headers=await auth_headers(client, viewer)
    )

    assert response.status_code == 200, response.text
    body = response.json()
    transcript_turns = [row for row in body["timeline"] if row.get("event") == "transcript_turn"]
    assert transcript_turns, "the timeline must still show that turns happened"
    assert all("text" not in row for row in transcript_turns)
    assert body["transcript"] is None


async def _make_user(db, tenant, role: str, creator):
    """A real user row in the tenant, for role-specific reads."""
    import uuid as _uuid

    from app.auth import password as pw
    from tests.conftest import TEST_PASSWORD
    from app.db.models import User, UserRole

    user = User(
        id=_uuid.uuid4(),
        tenant_id=tenant.id,
        email=f"{role}-{_uuid.uuid4().hex[:8]}@example.com",
        password_hash=pw.hash_password(TEST_PASSWORD),
        full_name=f"{role.title()} User",
        role=UserRole(role),
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user
