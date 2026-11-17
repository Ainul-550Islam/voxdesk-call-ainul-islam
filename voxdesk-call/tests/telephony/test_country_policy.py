"""PART 13 / P13-06 — country policy: dial, answer and transfer enforcement.

The same rule set is consulted at three points in a call's life, and each point
must answer the same way. A policy that blocks an outbound dial but lets an
inbound transfer through is not a policy, it is a speed bump.

The numbers here are real E.164 numbers, parsed with libphonenumber, because the
country lookup is the part most likely to be wrong: +1 is not one country and a
prefix table would pass a test that libphonenumber fails.
"""

from __future__ import annotations

import pytest

from app.telephony.country_policy import (
    CountryPolicy,
    CountryPolicyError,
    Rule,
    country_of,
    enforce_dial,
    enforce_inbound_answer,
    enforce_transfer,
    infer_default_action,
    is_e164,
    parse_rules,
    policy_from_rows,
)

BD = "+8801712345678"
US = "+14155550100"
GB = "+442071838750"


def _allowlist(*countries: str) -> CountryPolicy:
    return CountryPolicy(
        [Rule("tenant", country, "allow") for country in countries], default_action="deny"
    )


def test_a_number_resolves_to_its_country_not_its_prefix() -> None:
    assert country_of(BD) == "BD"
    assert country_of(US) == "US"
    assert country_of(GB) == "GB"
    assert country_of("not-a-number") is None
    assert country_of("") is None


def test_a_national_number_is_not_silently_guessed() -> None:
    # No leading +, so we do not know the country; the caller gets None and the
    # policy refuses rather than assuming plus-one.
    assert country_of("01712345678") is None
    assert is_e164("01712345678") is False


def test_dial_inside_the_allowlist_is_allowed() -> None:
    decision = enforce_dial(_allowlist("BD"), BD, agent_id="agent_1")
    assert decision.allowed is True
    assert decision.country == "BD"
    assert decision.matched_scope == "tenant"
    assert decision.stage == "dial"


def test_dial_outside_the_allowlist_is_refused_with_a_reason() -> None:
    decision = enforce_dial(_allowlist("BD"), US)
    assert decision.allowed is False
    assert decision.code == "country_not_allowed"


def test_inbound_answer_uses_the_same_policy() -> None:
    policy = _allowlist("BD")
    assert enforce_inbound_answer(policy, BD).allowed is True
    refused = enforce_inbound_answer(policy, US)
    assert refused.allowed is False
    assert refused.stage == "answer"


def test_transfer_destination_is_checked_too() -> None:
    policy = _allowlist("BD")
    assert enforce_transfer(policy, BD).allowed is True
    refused = enforce_transfer(policy, GB)
    assert refused.allowed is False
    assert refused.stage == "transfer"


def test_an_agent_rule_beats_the_tenant_rule() -> None:
    policy = CountryPolicy(
        [
            Rule("tenant", "US", "allow"),
            Rule("agent", "US", "deny", scope_id="blocked_agent", reason="vendor dispute"),
        ]
    )
    allowed = policy.decide(US, agent_id="other_agent")
    blocked = policy.decide(US, agent_id="blocked_agent")
    assert allowed.allowed is True
    assert blocked.allowed is False
    assert blocked.matched_scope == "agent"
    assert blocked.reason == "vendor dispute"


def test_a_number_rule_beats_the_tenant_rule_but_not_an_agent_rule() -> None:
    policy = CountryPolicy(
        [
            Rule("tenant", "GB", "allow"),
            Rule("number", "GB", "deny", scope_id="num_1"),
            Rule("agent", "GB", "allow", scope_id="agent_1"),
        ]
    )
    assert policy.decide(GB, number_id="num_1").allowed is False
    # The agent rule is more specific than the number rule.
    assert policy.decide(GB, number_id="num_1", agent_id="agent_1").allowed is True


def test_a_wildcard_rule_matches_every_country() -> None:
    policy = CountryPolicy([Rule("tenant", "*", "deny")])
    assert policy.decide(BD).allowed is False
    assert policy.decide(US).allowed is False


def test_an_unparseable_number_is_refused_once_a_policy_exists() -> None:
    policy = _allowlist("BD")
    decision = policy.decide("garbage")
    assert decision.allowed is False
    assert decision.code == "country_unknown"


def test_a_tenant_with_no_rules_is_not_blocked() -> None:
    decision = CountryPolicy().decide(US)
    assert decision.allowed is True
    assert decision.matched_scope == "default"


def test_the_default_flips_when_the_tenant_lists_allowed_countries() -> None:
    allow_rules = [Rule("tenant", "BD", "allow")]
    deny_rules = [Rule("tenant", "KP", "deny")]
    assert infer_default_action(allow_rules) == "deny"
    assert infer_default_action(deny_rules) == "allow"
    assert infer_default_action([]) == "allow"


def test_a_rule_with_a_bad_country_is_rejected() -> None:
    with pytest.raises(CountryPolicyError, match="ISO country"):
        Rule("tenant", "BGD", "allow")


def test_a_scoped_rule_needs_a_scope_id() -> None:
    with pytest.raises(CountryPolicyError, match="scope_id"):
        Rule("agent", "BD", "deny")


def test_parse_rules_names_the_offending_rule() -> None:
    with pytest.raises(CountryPolicyError, match="rule 1"):
        parse_rules(
            [
                {"scope": "tenant", "country": "BD", "action": "allow"},
                {"scope": "tenant", "country": "ZZZ", "action": "allow"},
            ]
        )


def test_policy_from_rows_reads_the_database_shape() -> None:
    class Row:
        scope = "tenant"
        scope_id = None
        country = "BD"
        action = "deny"
        reason = "carrier block"

    policy = policy_from_rows([Row()])
    decision = policy.decide(BD)
    assert decision.allowed is False
    assert decision.reason == "carrier block"


@pytest.mark.asyncio
async def test_the_dry_run_route_reports_the_matching_rule(client, owner_a) -> None:
    """The dashboard's dry-run is the API, not a client-side copy of the rules."""
    from tests.conftest import auth_headers

    headers = await auth_headers(client, owner_a)
    saved = await client.put(
        "/api/telephony/countries",
        json={
            "default_action": "deny",
            "rules": [{"scope": "tenant", "country": "BD", "action": "allow"}],
        },
        headers=headers,
    )
    assert saved.status_code == 200, saved.text
    assert [rule["country"] for rule in saved.json()["rules"]] == ["BD"]

    probe = await client.post(
        "/api/telephony/countries/dry-run",
        json={"number": US, "stage": "dry_run"},
        headers=headers,
    )
    assert probe.status_code == 200, probe.text
    body = probe.json()
    assert body["resolved_country"] == "US"
    assert body["decision"]["allowed"] is False
    assert body["decision"]["code"] == "country_not_allowed"

    inside = await client.post(
        "/api/telephony/countries/dry-run",
        json={"number": BD, "stage": "dial"},
        headers=headers,
    )
    assert inside.json()["decision"]["allowed"] is True
    assert inside.json()["decision"]["matched_scope"] == "tenant"


@pytest.mark.asyncio
async def test_a_duplicate_rule_is_refused(client, owner_a) -> None:
    from tests.conftest import auth_headers

    headers = await auth_headers(client, owner_a)
    response = await client.put(
        "/api/telephony/countries",
        json={
            "default_action": "allow",
            "rules": [
                {"scope": "tenant", "country": "BD", "action": "allow"},
                {"scope": "tenant", "country": "BD", "action": "deny"},
            ],
        },
        headers=headers,
    )
    assert response.status_code == 409, response.text
