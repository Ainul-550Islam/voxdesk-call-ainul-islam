"""Custom-domain claim, verify and host resolution, against the real models.

The DNS answers come from a fake resolver; everything else — the unique constraints, the
state machine, the tenant scoping and the stored check log — is the production code path.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.platform_v2_models import CustomDomain, WlDomainCheck
from app.platform_v2.errors import Conflict, DomainTaken, NotFound, ValidationFailed
from app.platform_v2.whitelabel import dns, domains
from tests.platform_v2.conftest import PlatScope


class FakeResolver:
    """Answers from a mutable map so a test can change DNS between attempts."""

    def __init__(self, answers: dict[tuple[str, str], list[str]] | None = None):
        self.answers = answers or {}
        self.nameservers = ["127.0.0.53"]

    async def resolve(self, name, record_type, lifetime=None):  # noqa: ARG002
        values = self.answers.get((name, record_type))
        if values is None:
            raise RuntimeError(f"NXDOMAIN: {name} {record_type}")
        return [_Answer(value, record_type) for value in values]


class _Answer:
    def __init__(self, value: str, record_type: str) -> None:
        self._value = value
        self._type = record_type
        if record_type == "TXT":
            self.strings = [value.encode("utf-8")]

    def __str__(self) -> str:
        return f'"{self._value}"' if self._type == "TXT" else self._value


@pytest.fixture(autouse=True)
def _tls_ask_off(monkeypatch):
    """Verification moves straight to ``active`` unless a test wants the cert step.

    With ``TLS_ASK_ENABLED`` on, a verified domain waits for a certificate before it
    becomes routable — the honest default in production, but it needs an edge to exist.
    """
    from app.platform_v2 import settings as settings_mod

    monkeypatch.setenv("TLS_ASK_ENABLED", "false")
    settings_mod.reset_plat2_settings_cache()
    yield
    settings_mod.reset_plat2_settings_cache()


async def test_claim_returns_a_token_once_and_stores_only_its_digest(plat_scope: PlatScope, db):
    row, token = await domains.claim(
        db, tenant_id=plat_scope.tenant.id, domain="Support.Acme.TEST", commit=True
    )
    assert row.domain == "support.acme.test"
    assert row.status == domains.STATUS_PENDING
    assert row.cname_target
    assert row.ownership_record_name == "_voxdesk-challenge.support.acme.test"
    assert token
    # The plaintext token must not be recoverable from the row.
    assert token not in (row.ownership_token_hash or "")
    assert row.ownership_token_hash == dns.token_digest(token)
    assert token not in domains.describe(row).__str__()


async def test_a_second_tenant_cannot_claim_a_taken_hostname(plat_scope: PlatScope, plat_scope_b: PlatScope, db):
    await domains.claim(db, tenant_id=plat_scope.tenant.id, domain="support.acme.test", commit=True)
    with pytest.raises(DomainTaken) as caught:
        await domains.claim(db, tenant_id=plat_scope_b.tenant.id, domain="support.acme.test")
    # The message must not reveal which tenant owns it.
    assert "tenant" not in caught.value.message or "another" not in caught.value.message
    assert caught.value.status_code == 409


async def test_the_same_tenant_claiming_twice_is_a_conflict_not_a_second_row(plat_scope: PlatScope, db):
    await domains.claim(db, tenant_id=plat_scope.tenant.id, domain="support.acme.test", commit=True)
    with pytest.raises(Conflict) as caught:
        await domains.claim(db, tenant_id=plat_scope.tenant.id, domain="support.acme.test")
    assert caught.value.detail["status"] == domains.STATUS_PENDING
    rows = (await db.execute(select(CustomDomain))).scalars().all()
    assert len(rows) == 1


async def test_the_platform_own_hostname_cannot_be_claimed(plat_scope: PlatScope, db, monkeypatch):
    from app.platform_v2 import settings as settings_mod

    monkeypatch.setenv("PUBLIC_BASE_URL", "https://app.voxdesk.test")
    settings_mod.reset_plat2_settings_cache()
    try:
        with pytest.raises(ValidationFailed):
            await domains.claim(db, tenant_id=plat_scope.tenant.id, domain="app.voxdesk.test")
    finally:
        settings_mod.reset_plat2_settings_cache()


async def test_claim_refuses_a_hostname_that_belongs_to_another_tenant_sso(
    plat_scope: PlatScope, plat_scope_b: PlatScope, db
):
    from datetime import datetime, timezone

    from app.auth.identity.models import EnterpriseDomain

    # `enterprise_domains` is the older record of "this address belongs to this
    # customer"; claiming that hostname as a custom domain would be a takeover.
    db.add(
        EnterpriseDomain(
            tenant_id=plat_scope_b.tenant.id,
            domain="acme.test",
            verified_at=datetime.now(tz=timezone.utc),
            enforcement="off",
        )
    )
    await db.commit()
    with pytest.raises(DomainTaken):
        await domains.claim(db, tenant_id=plat_scope.tenant.id, domain="acme.test")


async def test_verification_passes_when_both_records_are_correct(plat_scope: PlatScope, db):
    row, token = await domains.claim(db, tenant_id=plat_scope.tenant.id, domain="support.acme.test", commit=True)
    resolver = FakeResolver(
        {
            (row.ownership_record_name, "TXT"): [token],
            (row.domain, "CNAME"): [row.cname_target],
        }
    )
    outcome = await domains.verify(
        db, tenant_id=plat_scope.tenant.id, domain_id=row.id, token=token, resolver=resolver, commit=True
    )
    assert outcome.passed is True
    assert outcome.status == domains.STATUS_ACTIVE
    assert outcome.reason == ""
    assert row.verified_at is not None
    assert row.reverify_due_at is not None
    assert [check["check_type"] for check in outcome.checks] == [
        domains.CHECK_OWNERSHIP,
        domains.CHECK_CNAME,
        domains.CHECK_ADDRESS,
    ]


async def test_verification_fails_without_the_ownership_record(plat_scope: PlatScope, db):
    row, token = await domains.claim(db, tenant_id=plat_scope.tenant.id, domain="support.acme.test", commit=True)
    resolver = FakeResolver({(row.domain, "CNAME"): [row.cname_target]})
    outcome = await domains.verify(
        db, tenant_id=plat_scope.tenant.id, domain_id=row.id, token=token, resolver=resolver
    )
    assert outcome.passed is False
    assert outcome.status == domains.STATUS_FAILED
    assert "TXT" in outcome.reason
    assert row.failure_reason


async def test_verification_fails_when_the_cname_points_somewhere_else(plat_scope: PlatScope, db):
    row, token = await domains.claim(db, tenant_id=plat_scope.tenant.id, domain="support.acme.test", commit=True)
    resolver = FakeResolver(
        {
            (row.ownership_record_name, "TXT"): [token],
            (row.domain, "CNAME"): ["somewhere.else.test"],
            (row.domain, "A"): [],
        }
    )
    outcome = await domains.verify(
        db, tenant_id=plat_scope.tenant.id, domain_id=row.id, token=token, resolver=resolver
    )
    assert outcome.passed is False
    assert row.cname_target in outcome.reason


async def test_verification_works_from_the_stored_digest_when_no_token_is_supplied(
    plat_scope: PlatScope, db
):
    row, token = await domains.claim(db, tenant_id=plat_scope.tenant.id, domain="support.acme.test", commit=True)
    resolver = FakeResolver(
        {
            (row.ownership_record_name, "TXT"): [f"voxdesk-verify={token}"],
            (row.domain, "CNAME"): [row.cname_target],
        }
    )
    outcome = await domains.verify(db, tenant_id=plat_scope.tenant.id, domain_id=row.id, resolver=resolver)
    assert outcome.passed is True


async def test_a_wrong_token_does_not_verify(plat_scope: PlatScope, db):
    row, _real_token = await domains.claim(
        db, tenant_id=plat_scope.tenant.id, domain="support.acme.test", commit=True
    )
    # The caller supplies a token that is not the one in DNS: the record and the claim
    # disagree, which is exactly the case that must not pass.
    resolver = FakeResolver(
        {
            (row.ownership_record_name, "TXT"): ["a-token-from-somewhere-else"],
            (row.domain, "CNAME"): [row.cname_target],
        }
    )
    outcome = await domains.verify(
        db,
        tenant_id=plat_scope.tenant.id,
        domain_id=row.id,
        token="a-different-token",
        resolver=resolver,
    )
    assert outcome.passed is False
    assert outcome.status == domains.STATUS_FAILED


async def test_each_attempt_is_recorded_with_what_it_observed(plat_scope: PlatScope, db):
    row, token = await domains.claim(db, tenant_id=plat_scope.tenant.id, domain="support.acme.test", commit=True)
    resolver = FakeResolver({(row.ownership_record_name, "TXT"): [token]})
    await domains.verify(db, tenant_id=plat_scope.tenant.id, domain_id=row.id, token=token, resolver=resolver, commit=True)
    checks = await domains.checks_for(db, tenant_id=plat_scope.tenant.id, domain_id=row.id)
    assert len(checks) == 3
    ownership = next(check for check in checks if check.check_type == domains.CHECK_OWNERSHIP)
    assert ownership.passed is True
    assert ownership.resolvers["expected"]["expected_name"] == row.ownership_record_name
    assert ownership.resolvers["observed"]["values"] == [token]
    cname = next(check for check in checks if check.check_type == domains.CHECK_CNAME)
    assert cname.passed is False
    # One sentence for the console: here DNS answered with NXDOMAIN, so the reason is
    # "could not read" rather than "points at the wrong place" — different problems.
    assert "could not read the CNAME record" in cname.detail
    assert cname.detail.count("NXDOMAIN") == 1
    # ...and the structured record of what DNS actually said.
    assert "NXDOMAIN" in cname.resolvers["observed"]["error"]


async def test_a_domain_is_invisible_to_another_tenant(plat_scope: PlatScope, plat_scope_b: PlatScope, db):
    row, _token = await domains.claim(db, tenant_id=plat_scope.tenant.id, domain="support.acme.test", commit=True)
    with pytest.raises(NotFound):
        await domains.get(db, tenant_id=plat_scope_b.tenant.id, domain_id=row.id)
    assert await domains.list_for_tenant(db, tenant_id=plat_scope_b.tenant.id) == []


async def test_unknown_host_never_resolves_to_a_tenant(plat_scope: PlatScope, db):
    # Nothing is routable before verification...
    row, token = await domains.claim(
        db,
        tenant_id=plat_scope.tenant.id,
        domain="support.acme.test",
        environment_id=plat_scope.environment.id,
        commit=True,
    )
    assert await domains.resolve_host(db, "support.acme.test") is None
    assert await domains.resolve_host(db, "never-claimed.acme.test") is None
    assert await domains.resolve_host(db, "") is None

    resolver = FakeResolver(
        {(row.ownership_record_name, "TXT"): [token], (row.domain, "CNAME"): [row.cname_target]}
    )
    await domains.verify(db, tenant_id=plat_scope.tenant.id, domain_id=row.id, token=token, resolver=resolver, commit=True)

    # ...and routable only afterwards, and only for the exact hostname.
    resolved = await domains.resolve_host(db, "Support.Acme.TEST:443")
    assert resolved is not None
    assert resolved["tenant_id"] == plat_scope.tenant.id
    assert resolved["environment_id"] == plat_scope.environment.id
    assert await domains.resolve_host(db, "other.acme.test") is None


async def test_an_unverified_host_is_not_routable_even_when_it_resolves_in_dns(plat_scope: PlatScope, db):
    """DNS pointing at us is not authorisation; the row's status decides."""
    row, token = await domains.claim(
        db, tenant_id=plat_scope.tenant.id, domain="support.acme.test", commit=True
    )
    # Ownership passes but the CNAME is missing, so the row stays failed. The
    # resolver handed in resolves the TXT record only.
    outcome = await domains.verify(
        db,
        tenant_id=plat_scope.tenant.id,
        domain_id=row.id,
        token=token,
        resolver=FakeResolver({(row.ownership_record_name, "TXT"): [token]}),
    )
    assert outcome.passed is False
    assert await domains.resolve_host(db, "support.acme.test") is None


async def test_disabled_domains_stop_resolving(plat_scope: PlatScope, db):
    row, token = await domains.claim(db, tenant_id=plat_scope.tenant.id, domain="support.acme.test", commit=True)
    resolver = FakeResolver(
        {(row.ownership_record_name, "TXT"): [token], (row.domain, "CNAME"): [row.cname_target]}
    )
    await domains.verify(db, tenant_id=plat_scope.tenant.id, domain_id=row.id, token=token, resolver=resolver, commit=True)
    await domains.disable(db, tenant_id=plat_scope.tenant.id, domain_id=row.id, reason="tenant request", commit=True)
    assert await domains.resolve_host(db, "support.acme.test") is None
    with pytest.raises(Conflict):
        await domains.verify(db, tenant_id=plat_scope.tenant.id, domain_id=row.id, token=token, resolver=resolver)


async def test_enable_returns_a_domain_to_its_previous_state(plat_scope: PlatScope, db):
    row, _token = await domains.claim(db, tenant_id=plat_scope.tenant.id, domain="support.acme.test", commit=True)
    await domains.disable(db, tenant_id=plat_scope.tenant.id, domain_id=row.id, reason="abuse report", commit=True)
    assert row.status == domains.STATUS_DISABLED
    await domains.enable(db, tenant_id=plat_scope.tenant.id, domain_id=row.id, commit=True)
    assert row.status == domains.STATUS_PENDING


async def test_require_active_refuses_a_pending_domain(plat_scope: PlatScope, db):
    row, _token = await domains.claim(db, tenant_id=plat_scope.tenant.id, domain="support.acme.test", commit=True)
    with pytest.raises(Exception) as caught:
        domains.require_active(row)
    assert getattr(caught.value, "code", "") == "domain_not_verified"


async def test_delete_removes_the_claim_and_its_checks(plat_scope: PlatScope, db):
    row, token = await domains.claim(db, tenant_id=plat_scope.tenant.id, domain="support.acme.test", commit=True)
    resolver = FakeResolver({(row.ownership_record_name, "TXT"): [token]})
    await domains.verify(db, tenant_id=plat_scope.tenant.id, domain_id=row.id, token=token, resolver=resolver, commit=True)
    await domains.delete(db, tenant_id=plat_scope.tenant.id, domain_id=row.id, commit=True)
    assert await domains.list_for_tenant(db, tenant_id=plat_scope.tenant.id) == []
    remaining = (await db.execute(select(WlDomainCheck))).scalars().all()
    assert remaining == []


async def test_reverify_due_lists_only_domains_past_their_check_window(plat_scope: PlatScope, db):
    row, token = await domains.claim(db, tenant_id=plat_scope.tenant.id, domain="support.acme.test", commit=True)
    resolver = FakeResolver(
        {(row.ownership_record_name, "TXT"): [token], (row.domain, "CNAME"): [row.cname_target]}
    )
    await domains.verify(db, tenant_id=plat_scope.tenant.id, domain_id=row.id, token=token, resolver=resolver, commit=True)
    # Just verified: not due yet.
    assert await domains.reverify_due(db) == []
    from datetime import datetime, timedelta, timezone

    due = await domains.reverify_due(db, now=datetime.now(timezone.utc) + timedelta(days=30))
    assert [item.id for item in due] == [row.id]
