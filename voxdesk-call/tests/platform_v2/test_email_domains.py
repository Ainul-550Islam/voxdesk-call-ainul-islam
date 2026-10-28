"""Sender-domain checks: SPF, DKIM and DMARC.

Branded mail from a tenant's own domain is the one place a mistake reaches their
customers' inboxes, so the parsers are strict: two SPF records fail, ``+all`` fails, a
revoked DKIM key fails, and ``p=none`` is not a pass.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.platform_v2_models import DnsRecordCheck
from app.platform_v2.errors import Conflict, NotFound
from app.platform_v2.whitelabel import email_domains as email
from tests.platform_v2.conftest import PlatScope

KEY = "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA" + "A" * 24
OTHER_KEY = "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA" + "B" * 24


class FakeResolver:
    def __init__(self, answers: dict[tuple[str, str], list[str]]):
        self.answers = answers
        self.nameservers = ["127.0.0.53"]

    async def resolve(self, name, record_type, lifetime=None):  # noqa: ARG002
        values = self.answers.get((name, record_type))
        if values is None:
            raise RuntimeError(f"NXDOMAIN: {name} {record_type}")
        return [_Answer(value) for value in values]


class _Answer:
    def __init__(self, value: str) -> None:
        self._value = value
        self.strings = [value.encode("utf-8")]

    def __str__(self) -> str:
        return f'"{self._value}"'


SPF_OK = "v=spf1 include:mail.voxdesk.test -all"
DKIM_OK = f"v=DKIM1; k=rsa; p={KEY}"
DMARC_OK = "v=DMARC1; p=quarantine; rua=mailto:dmarc@acme.test; pct=100"


@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    from app.platform_v2 import settings as settings_mod

    monkeypatch.setenv("EMAIL_DKIM_PUBLIC_KEY", KEY)
    monkeypatch.setenv("EMAIL_SPF_INCLUDE", "mail.voxdesk.test")
    monkeypatch.setenv("EMAIL_DKIM_SELECTOR", "voxdesk")
    settings_mod.reset_plat2_settings_cache()
    yield
    settings_mod.reset_plat2_settings_cache()


# ------------------------------------------------------------------- parsing ---


def test_a_correct_spf_record_passes():
    parsed = email.parse_spf([SPF_OK, "v=spf1 -all"])
    # Two records is itself a failure, even if both are individually fine.
    assert parsed["valid"] is False
    assert "more than one" in parsed["reason"]
    assert email.parse_spf([SPF_OK])["valid"] is True
    assert email.parse_spf(["v=spf1 include:mail.voxdesk.test ~all"])["valid"] is True


@pytest.mark.parametrize(
    ("values", "reason"),
    [
        ([], "no v=spf1 record found"),
        (["v=spf1 +all"], "+all"),
        (["v=spf1 include:mail.voxdesk.test"], "-all or ~all"),
        (["v=spf1 a mx +all"], "+all"),
    ],
    ids=["missing-record", "open-all-policy", "missing-terminator", "all-policy-with-mechanisms"],
)
def test_a_dangerous_or_incomplete_spf_record_fails(values, reason):
    parsed = email.parse_spf(values)
    assert parsed["valid"] is False
    assert reason in parsed["reason"]


def test_spf_that_does_not_mention_us_is_still_accepted_if_it_is_closed():
    """The value is judged on its shape, not on whether it lists our host.

    A tenant routing mail through their own gateway is legitimate; the check that
    matters is that the policy is closed and unambiguous.
    """
    assert email.parse_spf(["v=spf1 include:mail.tenant-gateway.test -all"])["valid"] is True


def test_dkim_with_the_right_key_passes():
    parsed = email.parse_dkim([DKIM_OK], expected_key=KEY)
    assert parsed["valid"] is True
    # Whitespace in the published key is normal; comparison normalises it.
    wrapped = "v=DKIM1; k=rsa; p=" + KEY[:20] + " " + KEY[20:]
    assert email.parse_dkim([wrapped], expected_key=KEY)["valid"] is True


def test_dkim_with_a_stale_key_fails():
    parsed = email.parse_dkim([f"v=DKIM1; k=rsa; p={OTHER_KEY}"], expected_key=KEY)
    assert parsed["valid"] is False
    assert "does not match" in parsed["reason"]


def test_dkim_with_an_empty_key_is_a_revocation():
    parsed = email.parse_dkim(["v=DKIM1; k=rsa; p="], expected_key=KEY)
    assert parsed["valid"] is False
    assert "revoked" in parsed["reason"]


def test_dkim_without_a_key_type_or_the_record_fails():
    assert email.parse_dkim([f"v=DKIM1; p={KEY}"], expected_key=KEY)["valid"] is False
    assert email.parse_dkim([], expected_key=KEY)["valid"] is False


def test_dmarc_must_be_stricter_than_none():
    assert email.parse_dmarc([DMARC_OK])["valid"] is True
    assert email.parse_dmarc(["v=DMARC1; p=reject"])["valid"] is True
    monitored = email.parse_dmarc(["v=DMARC1; p=none; rua=mailto:dmarc@acme.test"])
    assert monitored["valid"] is False
    assert "quarantine or reject" in monitored["reason"]
    assert email.parse_dmarc([])["valid"] is False
    assert email.parse_dmarc(["v=DMARC1; rua=mailto:x@acme.test"])["valid"] is False


def test_expectations_lists_the_three_records_with_names():
    records = email.expectations(
        "acme.test", selector="voxdesk", platform_spf_include="mail.voxdesk.test", public_key=KEY
    )
    assert set(records) == {"spf", "dkim", "dmarc"}
    assert records["spf"]["name"] == "acme.test"
    assert records["dkim"]["name"] == "voxdesk._domainkey.acme.test"
    assert records["dmarc"]["name"] == "_dmarc.acme.test"
    assert KEY in records["dkim"]["value"]
    assert "p=quarantine" in records["dmarc"]["value"]


# ---------------------------------------------------------- record handling ---


async def test_creating_a_sender_domain_stores_the_records_the_tenant_must_publish(
    plat_scope: PlatScope, db
):
    row, instructions = await email.create(
        db, tenant_id=plat_scope.tenant.id, domain="Mail.Acme.TEST", from_name="Acme", commit=True
    )
    assert row.domain == "mail.acme.test"
    assert row.dkim_selector == "voxdesk"
    assert row.status == email.STATUS_PENDING
    assert row.records["dkim"]["name"] == "voxdesk._domainkey.mail.acme.test"
    assert instructions == row.records


async def test_a_duplicate_sender_domain_is_a_conflict(plat_scope: PlatScope, db):
    await email.create(db, tenant_id=plat_scope.tenant.id, domain="mail.acme.test", commit=True)
    with pytest.raises(Conflict):
        await email.create(db, tenant_id=plat_scope.tenant.id, domain="mail.acme.test")


async def test_an_invalid_dkim_selector_is_refused(plat_scope: PlatScope, db):
    with pytest.raises(Conflict):
        await email.create(db, tenant_id=plat_scope.tenant.id, domain="mail.acme.test", dkim_selector="has space")


async def test_verification_passes_only_when_all_three_records_are_right(plat_scope: PlatScope, db):
    row, _instructions = await email.create(
        db, tenant_id=plat_scope.tenant.id, domain="mail.acme.test", commit=True
    )
    resolver = FakeResolver(
        {
            ("mail.acme.test", "TXT"): [SPF_OK],
            ("voxdesk._domainkey.mail.acme.test", "TXT"): [DKIM_OK],
            ("_dmarc.mail.acme.test", "TXT"): [DMARC_OK],
        }
    )
    result = await email.verify(db, tenant_id=plat_scope.tenant.id, sender_id=row.id, resolver=resolver, commit=True)
    assert result["status"] == email.STATUS_VERIFIED
    assert row.spf_verified and row.dkim_verified and row.dmarc_verified
    assert row.verified_at is not None
    # verified_from_address returns a ready-to-use From: address, not the bare domain.
    assert email.verified_from_address(row) == "no-reply@mail.acme.test"


async def test_a_passing_spf_does_not_verify_a_domain_without_dmarc(plat_scope: PlatScope, db):
    row, _instructions = await email.create(
        db, tenant_id=plat_scope.tenant.id, domain="mail.acme.test", commit=True
    )
    resolver = FakeResolver(
        {
            ("mail.acme.test", "TXT"): [SPF_OK],
            ("voxdesk._domainkey.mail.acme.test", "TXT"): [DKIM_OK],
            ("_dmarc.mail.acme.test", "TXT"): ["v=DMARC1; p=none"],
        }
    )
    result = await email.verify(db, tenant_id=plat_scope.tenant.id, sender_id=row.id, resolver=resolver, commit=True)
    assert result["status"] == email.STATUS_PENDING
    assert row.spf_verified is True
    assert row.dmarc_verified is False
    # verify() nests per-record verdicts under "checks"; the router flattens them for the API.
    assert "quarantine or reject" in result["checks"]["dmarc"]["reason"]


async def test_every_check_is_stored_with_expected_and_observed(plat_scope: PlatScope, db):
    row, _instructions = await email.create(
        db, tenant_id=plat_scope.tenant.id, domain="mail.acme.test", commit=True
    )
    resolver = FakeResolver({("mail.acme.test", "TXT"): [SPF_OK]})
    await email.verify(db, tenant_id=plat_scope.tenant.id, sender_id=row.id, resolver=resolver, commit=True)
    checks = await email.records_for(db, tenant_id=plat_scope.tenant.id, sender_id=row.id)
    assert len(checks) == 3
    spf = next(check for check in checks if check.detail["check"] == "spf")
    assert spf.passed is True
    assert SPF_OK in spf.observed
    dkim = next(check for check in checks if check.detail["check"] == "dkim")
    assert dkim.passed is False
    assert "could not read TXT records" in dkim.detail["reason"]
    assert dkim.name == "voxdesk._domainkey.mail.acme.test"


async def test_a_sender_domain_is_invisible_to_another_tenant(plat_scope: PlatScope, plat_scope_b: PlatScope, db):
    row, _instructions = await email.create(
        db, tenant_id=plat_scope.tenant.id, domain="mail.acme.test", commit=True
    )
    with pytest.raises(NotFound):
        await email.get(db, tenant_id=plat_scope_b.tenant.id, sender_id=row.id)
    assert await email.list_for_tenant(db, tenant_id=plat_scope_b.tenant.id) == []


async def test_delete_removes_the_domain_and_its_checks(plat_scope: PlatScope, db):
    row, _instructions = await email.create(
        db, tenant_id=plat_scope.tenant.id, domain="mail.acme.test", commit=True
    )
    resolver = FakeResolver({("mail.acme.test", "TXT"): [SPF_OK]})
    await email.verify(db, tenant_id=plat_scope.tenant.id, sender_id=row.id, resolver=resolver, commit=True)
    await email.delete(db, tenant_id=plat_scope.tenant.id, sender_id=row.id, commit=True)
    assert await email.list_for_tenant(db, tenant_id=plat_scope.tenant.id) == []
    assert (await db.execute(select(DnsRecordCheck))).scalars().all() == []


async def test_a_sender_domain_may_not_be_the_platform_own_hostname(plat_scope: PlatScope, db, monkeypatch):
    from app.platform_v2 import settings as settings_mod

    monkeypatch.setenv("PUBLIC_BASE_URL", "https://app.voxdesk.test")
    settings_mod.reset_plat2_settings_cache()
    try:
        with pytest.raises(Exception) as caught:
            await email.create(db, tenant_id=plat_scope.tenant.id, domain="app.voxdesk.test")
        assert "platform" in str(caught.value).lower()
    finally:
        settings_mod.reset_plat2_settings_cache()
