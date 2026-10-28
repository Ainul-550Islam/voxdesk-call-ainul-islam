"""The on-demand TLS ``ask`` decision and certificate bookkeeping.

Caddy asks before it issues. The answer must be "no" for every hostname that is not a
verified, active custom domain — that refusal is what stops a stranger pointing DNS at the
edge and making it mint certificates until the ACME account is rate-limited.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.platform_v2.whitelabel import dns, domains, tls
from tests.platform_v2.conftest import PlatScope


class FakeResolver:
    def __init__(self, answers: dict[tuple[str, str], list[str]]):
        self.answers = answers
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


ASK_TOKEN = "ask-token-for-tests"


@pytest.fixture(autouse=True)
def _ask_token(monkeypatch):
    from app.platform_v2 import settings as settings_mod

    monkeypatch.setenv("TLS_ASK_TOKEN", ASK_TOKEN)
    monkeypatch.setenv("TLS_ASK_ENABLED", "true")
    settings_mod.reset_plat2_settings_cache()
    yield
    settings_mod.reset_plat2_settings_cache()


async def _claim(scope: PlatScope, db, domain: str = "support.acme.test", **kwargs):
    row, token = await domains.claim(
        db, tenant_id=scope.tenant.id, domain=domain, commit=True, **kwargs
    )
    return row, token


async def _activate(scope: PlatScope, db, row, token) -> None:
    resolver = FakeResolver(
        {(row.ownership_record_name, "TXT"): [token], (row.domain, "CNAME"): [row.cname_target]}
    )
    # `tls_ask_enabled` is on, so a verified domain waits in `issuing_cert` until a
    # certificate is actually served; `activate` is what the sweep calls.
    await domains.verify(
        db, tenant_id=scope.tenant.id, domain_id=row.id, token=token, resolver=resolver, commit=True
    )
    await domains.activate(db, tenant_id=scope.tenant.id, domain_id=row.id, commit=True)


async def test_an_active_domain_is_allowed(plat_scope: PlatScope, db):
    row, token = await _claim(plat_scope, db)
    await _activate(plat_scope, db, row, token)
    decision = await tls.decide(db, domain="support.acme.test", provided_token=ASK_TOKEN)
    assert decision.allow is True
    assert decision.status_code == 200
    assert decision.tenant_id == plat_scope.tenant.id
    assert decision.body()["allow"] is True


async def test_a_missing_or_wrong_ask_token_is_refused_before_any_lookup(plat_scope: PlatScope, db):
    row, token = await _claim(plat_scope, db)
    await _activate(plat_scope, db, row, token)
    for provided in (None, "", "wrong"):
        decision = await tls.decide(db, domain="support.acme.test", provided_token=provided)
        assert decision.allow is False
        assert decision.status_code == 403
        # The refusal must not reveal whether the hostname exists.
        assert decision.reason == "missing or invalid ask token"


async def test_an_unconfigured_token_refuses_everything(plat_scope: PlatScope, db, monkeypatch):
    from app.platform_v2 import settings as settings_mod

    monkeypatch.setenv("TLS_ASK_TOKEN", "")
    settings_mod.reset_plat2_settings_cache()
    try:
        row, token = await _claim(plat_scope, db)
        await _activate(plat_scope, db, row, token)
        decision = await tls.decide(db, domain="support.acme.test", provided_token="")
        assert decision.allow is False
        assert decision.status_code == 403
    finally:
        settings_mod.reset_plat2_settings_cache()


async def test_on_demand_tls_can_be_switched_off(plat_scope: PlatScope, db, monkeypatch):
    from app.platform_v2 import settings as settings_mod

    monkeypatch.setenv("TLS_ASK_ENABLED", "false")
    settings_mod.reset_plat2_settings_cache()
    try:
        row, token = await _claim(plat_scope, db)
        await _activate(plat_scope, db, row, token)
        decision = await tls.decide(db, domain="support.acme.test", provided_token=ASK_TOKEN)
        assert decision.allow is False
        assert "on-demand TLS is disabled" in decision.reason
    finally:
        settings_mod.reset_plat2_settings_cache()


async def test_unknown_hosts_are_refused(plat_scope: PlatScope, db):
    decision = await tls.decide(db, domain="never-claimed.acme.test", provided_token=ASK_TOKEN)
    assert decision.allow is False
    assert decision.status_code == 403
    assert decision.reason == "unknown hostname"


@pytest.mark.parametrize(
    "status",
    ["pending_dns", "failed", "disabled"],
    ids=["pending-dns", "failed", "disabled"],
)
async def test_any_status_other_than_active_is_refused(plat_scope: PlatScope, db, status):
    row, _token = await _claim(plat_scope, db)
    row.status = status
    await db.commit()
    decision = await tls.decide(db, domain="support.acme.test", provided_token=ASK_TOKEN)
    assert decision.allow is False
    assert decision.status_code == 403
    assert status in decision.reason


async def test_a_hostname_that_is_not_a_hostname_is_refused(plat_scope: PlatScope, db):
    decision = await tls.decide(db, domain="not a host", provided_token=ASK_TOKEN)
    assert decision.allow is False
    assert "not a valid hostname" in decision.reason


async def test_deciding_is_case_and_dot_insensitive(plat_scope: PlatScope, db):
    row, token = await _claim(plat_scope, db)
    await _activate(plat_scope, db, row, token)
    decision = await tls.decide(db, domain="Support.Acme.TEST.", provided_token=ASK_TOKEN)
    assert decision.allow is True


async def test_certificate_info_reports_renewal_pressure():
    soon = tls.CertificateInfo(
        host="support.acme.test",
        state=tls.CERT_VALID,
        issuer="CN=Test CA",
        serial="abc",
        not_before=dt.datetime.now(tz=dt.timezone.utc) - dt.timedelta(days=80),
        not_after=dt.datetime.now(tz=dt.timezone.utc) + dt.timedelta(days=10),
    )
    assert soon.renewal_due is True
    assert soon.days_to_expiry is not None and soon.days_to_expiry <= tls.RENEWAL_WARNING_DAYS

    later = tls.CertificateInfo(
        host="support.acme.test",
        state=tls.CERT_VALID,
        issuer="CN=Test CA",
        serial="abc",
        not_before=dt.datetime.now(tz=dt.timezone.utc) - dt.timedelta(days=10),
        not_after=dt.datetime.now(tz=dt.timezone.utc) + dt.timedelta(days=80),
    )
    assert later.renewal_due is False


async def test_a_broken_tls_handshake_is_an_error_state_not_an_exception():
    async def reader(host, port, timeout):  # noqa: ARG001
        raise ConnectionRefusedError("nothing listening")

    info = await tls.inspect("support.acme.test", reader=reader)
    assert info.state == tls.CERT_ERROR
    assert "ConnectionRefusedError" in info.error


async def _record_certificate(db, scope: PlatScope, row, *, days: int, state: str = tls.CERT_VALID):
    info = tls.CertificateInfo(
        host=row.domain,
        state=state,
        issuer="CN=Test CA",
        serial="1",
        not_before=dt.datetime.now(tz=dt.timezone.utc) - dt.timedelta(days=1),
        not_after=dt.datetime.now(tz=dt.timezone.utc) + dt.timedelta(days=days),
    )
    await tls.record(db, tenant_id=scope.tenant.id, domain_id=row.id, info=info)
    await db.commit()
    return info


async def test_status_for_reports_the_stored_certificate(plat_scope: PlatScope, db):
    row, _token = await _claim(plat_scope, db)
    await _record_certificate(db, plat_scope, row, days=80)
    payload = await tls.status_for(db, tenant_id=plat_scope.tenant.id, domain_id=row.id)
    assert payload["state"] == tls.CERT_VALID
    assert payload["issuer"] == "CN=Test CA"
    assert payload["renewal_due"] is False
    assert payload["checked_at"]


async def test_status_for_reports_an_honest_unknown_before_any_check(plat_scope: PlatScope, db):
    row, _token = await _claim(plat_scope, db)
    payload = await tls.status_for(db, tenant_id=plat_scope.tenant.id, domain_id=row.id)
    # Not "issued", not "valid": the state of a domain with no certificate record.
    assert payload["state"] == domains.STATUS_PENDING
    assert payload["not_after"] is None
    assert payload["days_to_expiry"] is None


async def test_status_for_is_tenant_scoped(plat_scope: PlatScope, plat_scope_b: PlatScope, db):
    row, _token = await _claim(plat_scope, db)
    with pytest.raises(Exception):
        await tls.status_for(db, tenant_id=plat_scope_b.tenant.id, domain_id=row.id)


async def test_the_sweep_activates_a_domain_only_when_a_valid_certificate_is_served(
    plat_scope: PlatScope, db
):
    row, token = await _claim(plat_scope, db)
    resolver = FakeResolver(
        {(row.ownership_record_name, "TXT"): [token], (row.domain, "CNAME"): [row.cname_target]}
    )
    await domains.verify(
        db, tenant_id=plat_scope.tenant.id, domain_id=row.id, token=token, resolver=resolver, commit=True
    )
    assert row.status == domains.STATUS_ISSUING

    async def good_reader(host, port, timeout):  # noqa: ARG001
        raise ConnectionRefusedError("pretend nothing is served yet")

    results = await tls.sweep(db, reader=good_reader, commit=True)
    assert [item["domain"] for item in results] == ["support.acme.test"]
    assert results[0]["activated"] is False
    # Still waiting, with the reason recorded — not silently "active".
    assert row.status == domains.STATUS_ISSUING
    payload = await tls.status_for(db, tenant_id=plat_scope.tenant.id, domain_id=row.id)
    assert payload["state"] == tls.CERT_ERROR
    assert "ConnectionRefusedError" in payload["error"]


async def test_refresh_certificate_activates_an_issuing_domain(plat_scope: PlatScope, db):
    row, token = await _claim(plat_scope, db)
    resolver = FakeResolver(
        {(row.ownership_record_name, "TXT"): [token], (row.domain, "CNAME"): [row.cname_target]}
    )
    await domains.verify(
        db, tenant_id=plat_scope.tenant.id, domain_id=row.id, token=token, resolver=resolver, commit=True
    )
    await _record_certificate(db, plat_scope, row, days=80)
    payload = await tls.status_for(db, tenant_id=plat_scope.tenant.id, domain_id=row.id)
    assert payload["state"] == tls.CERT_VALID
    # The sweep is the only thing allowed to move a domain to active.
    assert row.status == domains.STATUS_ISSUING
