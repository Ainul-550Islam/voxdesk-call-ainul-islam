"""Hostname validation and DNS proof for custom domains.

The validator is a security control, not a formatting nicety: a permissive one turns a
tenant's claim into a certificate for somebody else's domain. The resolution helpers are
exercised against a fake resolver so the tests never touch the network — the live
behaviour is covered separately by ``test_live_custom_domain_tls.py``.
"""

from __future__ import annotations

import pytest

from app.platform_v2.errors import ValidationFailed
from app.platform_v2.whitelabel import dns


class FakeResolver:
    """A resolver double: answers a fixed map, raises for unknown names."""

    def __init__(self, answers: dict[tuple[str, str], list[str]], *, nameservers: list[str] | None = None):
        self.answers = answers
        self.nameservers = nameservers or ["127.0.0.53"]
        self.asked: list[tuple[str, str]] = []

    async def resolve(self, name, record_type, lifetime=None):  # noqa: ARG002 - signature parity
        self.asked.append((name, record_type))
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


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Support.Example.COM", "support.example.com"),
        ("support.example.com.", "support.example.com"),
        ("  support.example.com  ", "support.example.com"),
        ("a-b.c-d.example.com", "a-b.c-d.example.com"),
    ],
)
def test_normalize_domain_canonicalises_the_accepted_forms(raw, expected):
    assert dns.normalize_domain(raw) == expected


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ("https://support.example.com", "scheme"),
        ("support.example.com:8443", "port"),
        ("support.example.com/path", "path"),
        ("support.example.com?x=1", "path"),
        ("support.example.com#frag", "path"),
        ("user@example.com", "email"),
        ("*.example.com", "wildcard"),
        ("localhost", "dot"),
        ("", "empty"),
        ("192.0.2.10", "IP"),
        ("2001:db8::1", "IP"),
        ("under_score.example.com", "underscore"),
        ("-leading.example.com", "letter"),
        ("trailing-.example.com", "letter"),
        ("double..example.com", "empty labels"),
        ("com", "public suffix"),
        ("co.uk", "public suffix"),
        ("a" * 64 + ".example.com", "label"),
    ],
)
def test_normalize_domain_refuses_everything_that_is_not_a_hostname(raw, reason):
    with pytest.raises(ValidationFailed) as caught:
        dns.normalize_domain(raw)
    # The message names the actual problem; "invalid domain" teaches nobody anything.
    assert caught.value.message


def test_normalize_domain_refuses_a_name_that_is_too_long():
    long_name = ".".join(["a" * 60] * 5)
    with pytest.raises(ValidationFailed):
        dns.normalize_domain(long_name)


def test_reject_platform_domain_refuses_our_own_hostnames():
    hosts = ["app.voxdesk.com", "edge.voxdesk.com"]
    with pytest.raises(ValidationFailed):
        dns.reject_platform_domain("app.voxdesk.com", hosts)
    with pytest.raises(ValidationFailed):
        # A subdomain of the platform is still the platform.
        dns.reject_platform_domain("tenant.app.voxdesk.com", hosts)
    # A tenant hostname that merely ends with a similar string is fine.
    dns.reject_platform_domain("notapp.voxdesk.com.evil.test", hosts)
    dns.reject_platform_domain("support.example.com", hosts)


def test_reject_platform_domain_ignores_blank_entries():
    dns.reject_platform_domain("support.example.com", ["", "  "])


def test_challenge_name_is_the_documented_owner_name():
    assert dns.challenge_name("support.example.com") == "_voxdesk-challenge.support.example.com"


def test_new_token_is_long_lowercase_and_unpredictable():
    first = dns.new_token()
    second = dns.new_token()
    assert first != second
    assert len(first) == 40
    assert first.isalnum() and first.islower()
    assert len(dns.new_token(12)) == 12


def test_token_digest_is_stored_instead_of_the_token():
    token = dns.new_token()
    digest = dns.token_digest(token)
    assert digest != token
    assert len(digest) == 64
    assert dns.digest_matches(token, digest) is True
    assert dns.digest_matches(token + "x", digest) is False


def test_expected_txt_value_is_the_bare_token():
    token = dns.new_token(20)
    assert dns.expected_txt_value(token) == token


@pytest.mark.parametrize(
    "value",
    [
        "TOKEN",
        "voxdesk-verify=TOKEN",
        "voxdesk-domain-verification=TOKEN",
        "voxdesk=TOKEN",
    ],
)
def test_txt_contains_accepts_the_bare_and_prefixed_forms(value):
    resolution = dns.Resolution(
        name="_voxdesk-challenge.support.example.com",
        record_type="TXT",
        values=(value.replace("TOKEN", "abc123"),),
    )
    assert dns.txt_contains(resolution, "abc123") is True


def test_txt_contains_refuses_a_prefix_match_and_an_empty_token():
    # "abc123extra" is a different record that merely starts with the token.
    resolution = dns.Resolution(
        name="_voxdesk-challenge.support.example.com",
        record_type="TXT",
        values=("abc123extra",),
    )
    assert dns.txt_contains(resolution, "abc123") is False
    assert dns.txt_contains(resolution, "") is False
    assert dns.txt_contains(dns.Resolution(name="x", record_type="TXT", values=()), "abc123") is False


def test_txt_contains_handles_a_record_with_several_strings():
    resolution = dns.Resolution(
        name="_voxdesk-challenge.support.example.com",
        record_type="TXT",
        values=('"v=spf1 -all" "voxdesk-verify=abc123"',),
    )
    assert dns.txt_contains(resolution, "abc123") is True


def test_cname_points_at_ignores_case_and_the_trailing_dot():
    resolution = dns.Resolution(
        name="support.example.com",
        record_type="CNAME",
        values=("Edge.VoxDesk.App.",),
    )
    assert dns.cname_points_at(resolution, "edge.voxdesk.app") is True
    assert dns.cname_points_at(resolution, "other.voxdesk.app") is False


def test_tls_ask_authorised_requires_a_configured_and_matching_token():
    assert dns.tls_ask_authorised("shared-secret", "shared-secret") is True
    assert dns.tls_ask_authorised("wrong", "shared-secret") is False
    assert dns.tls_ask_authorised(None, "shared-secret") is False
    # An unconfigured token refuses rather than allowing: fail closed.
    assert dns.tls_ask_authorised("anything", "") is False
    assert dns.tls_ask_authorised(None, "") is False


@pytest.mark.asyncio
async def test_resolve_txt_reads_the_record_through_a_fake_resolver():
    resolver = FakeResolver({("_voxdesk-challenge.support.example.com", "TXT"): ["abc123"]})
    resolution = await dns.resolve_txt("_voxdesk-challenge.support.example.com", resolver=resolver)
    assert resolution.answered is True
    assert resolution.found is True
    assert resolution.values == ("abc123",)
    assert dns.txt_contains(resolution, "abc123") is True


@pytest.mark.asyncio
async def test_a_resolver_error_is_a_failed_check_not_an_exception():
    resolver = FakeResolver({})
    resolution = await dns.resolve_cname("support.example.com", resolver=resolver)
    assert resolution.answered is False
    assert resolution.found is False
    assert "NXDOMAIN" in resolution.error
    assert resolution.as_detail()["error"]


@pytest.mark.asyncio
async def test_resolve_a_asks_for_the_hostname_itself():
    resolver = FakeResolver({("support.example.com", "A"): ["203.0.113.10"]})
    resolution = await dns.resolve_a("support.example.com", resolver=resolver)
    assert resolution.values == ("203.0.113.10",)
    assert resolver.asked == [("support.example.com", "A")]


@pytest.mark.asyncio
async def test_resolve_aaaa_records_the_resolver_that_answered():
    resolver = FakeResolver({("support.example.com", "AAAA"): ["2001:db8::1"]}, nameservers=["127.0.0.53", "127.0.0.54"])
    resolution = await dns.resolve_aaaa("support.example.com", resolver=resolver)
    assert "127.0.0.53" in resolution.resolver
