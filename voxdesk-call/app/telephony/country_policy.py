"""Country allow/deny policy (PART 13, P13-06).

A tenant's regulator does not care about our dialer settings: if the business is
allowed to call Bangladesh and nothing else, an agent that hallucinates a US
number must not be able to spend money reaching it. So the rule is checked at
three points -- before an outbound dial, before an inbound call is answered and
before a transfer leg is placed -- and each check returns the same decision
object, which names the rule that fired.

Precedence is *most specific wins*: an agent rule beats a number rule, a number
rule beats the tenant default. Within one scope an exact country beats ``"*"``.
That is the only ordering an operator can predict without reading code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

try:  # pragma: no cover - the import itself is exercised by every other test
    import phonenumbers
except ModuleNotFoundError:  # pragma: no cover
    phonenumbers = None  # type: ignore[assignment]

ACTIONS = ("allow", "deny")
SCOPES = ("tenant", "number", "agent")
#: Specific-to-general. `decide` walks this list and stops at the first hit.
PRECEDENCE = ("agent", "number", "tenant")
#: Where a decision is being made. Kept on the object so an audit row can say
#: "this was refused at answer time" rather than just "refused".
STAGES = ("dial", "answer", "transfer", "dry_run")

WILDCARD = "*"


class CountryPolicyError(Exception):
    """Malformed rule or unparseable number. Never silently allowed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def country_of(number: str) -> str | None:
    """ISO-3166 alpha-2 for an E.164 number, or ``None``.

    Uses libphonenumber, not a prefix table: +1 is not one country, +7 is not
    one country, and a hand-written table gets those wrong silently.
    """
    if not number:
        return None
    if phonenumbers is None:  # pragma: no cover - dependency is in requirements.txt
        raise CountryPolicyError("lookup_unavailable", "phonenumbers is not installed")
    text = number.strip()
    if not text.startswith("+"):
        return None
    try:
        parsed = phonenumbers.parse(text, None)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_possible_number(parsed):
        return None
    region = phonenumbers.region_code_for_number(parsed)
    return region or None


def is_e164(number: str) -> bool:
    if not number or not number.startswith("+") or not number[1:].isdigit():
        return False
    return 8 <= len(number) <= 16


@dataclass(frozen=True)
class Rule:
    scope: str
    country: str
    action: str
    scope_id: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.scope not in SCOPES:
            raise CountryPolicyError("invalid_scope", f"unknown scope {self.scope!r}")
        if self.action not in ACTIONS:
            raise CountryPolicyError("invalid_action", f"unknown action {self.action!r}")
        country = (self.country or "").strip().upper()
        if country != WILDCARD and len(country) != 2:
            raise CountryPolicyError("invalid_country", f"not an ISO country: {self.country!r}")
        object.__setattr__(self, "country", country)
        if self.scope != "tenant" and not self.scope_id:
            raise CountryPolicyError("missing_scope_id", f"{self.scope} rules need a scope_id")

    @classmethod
    def from_row(cls, row: Any) -> "Rule":
        return cls(
            scope=row.scope,
            scope_id=row.scope_id,
            country=row.country,
            action=row.action,
            reason=row.reason,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "scope_id": self.scope_id,
            "country": self.country,
            "action": self.action,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class Decision:
    allowed: bool
    country: str | None
    action: str
    matched_scope: str
    matched_id: str | None
    reason: str | None
    stage: str
    code: str = "ok"

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "country": self.country,
            "action": self.action,
            "matched_scope": self.matched_scope,
            "matched_id": self.matched_id,
            "reason": self.reason,
            "stage": self.stage,
            "code": self.code,
        }


class CountryPolicy:
    """An immutable view of one tenant's rules."""

    def __init__(self, rules: Iterable[Rule] = (), *, default_action: str = "allow") -> None:
        if default_action not in ACTIONS:
            raise CountryPolicyError("invalid_default", f"unknown default {default_action!r}")
        self.rules: tuple[Rule, ...] = tuple(rules)
        self.default_action = default_action

    @classmethod
    def from_rows(
        cls, rows: Iterable[Any], *, default_action: str = "allow"
    ) -> "CountryPolicy":
        return cls((Rule.from_row(row) for row in rows), default_action=default_action)

    def rules_for(self, scope: str, scope_id: str | None = None) -> list[Rule]:
        return [
            rule
            for rule in self.rules
            if rule.scope == scope and (scope_id is None or rule.scope_id == scope_id)
        ]

    def _candidates(self, number_id: str | None, agent_id: str | None) -> list[tuple[str, str | None]]:
        out: list[tuple[str, str | None]] = []
        if agent_id:
            out.append(("agent", str(agent_id)))
        if number_id:
            out.append(("number", str(number_id)))
        out.append(("tenant", None))
        return out

    def decide(
        self,
        number: str | None,
        *,
        stage: str = "dial",
        number_id: str | None = None,
        agent_id: str | None = None,
        country: str | None = None,
    ) -> Decision:
        """Evaluate one call leg.

        An unparseable number is *refused*, not allowed: the whole point of the
        policy is to be the last line of defence before a dial, and "we could
        not tell which country this is" is not a reason to spend money.
        """
        if stage not in STAGES:
            raise CountryPolicyError("invalid_stage", f"unknown stage {stage!r}")
        resolved = (country or country_of(number) or "").upper() or None
        if resolved is None:
            if self.default_action == "allow" and not self.rules:
                return Decision(True, None, "allow", "default", None, None, stage)
            return Decision(
                False,
                None,
                "deny",
                "default",
                None,
                "number country could not be determined",
                stage,
                code="country_unknown",
            )

        for scope, scope_id in self._candidates(number_id, agent_id):
            scoped = self.rules_for(scope, scope_id)
            exact = [rule for rule in scoped if rule.country == resolved]
            wildcard = [rule for rule in scoped if rule.country == WILDCARD]
            # An exact country beats `*` inside one scope; `*` matches any
            # country, so an allow `*` on an agent really does override a
            # broader deny. That is the reading an operator writes down.
            for rule in [*exact, *wildcard][:1]:
                return Decision(
                    allowed=rule.action == "allow",
                    country=resolved,
                    action=rule.action,
                    matched_scope=rule.scope,
                    matched_id=rule.scope_id,
                    reason=rule.reason,
                    stage=stage,
                    code="ok" if rule.action == "allow" else "country_denied",
                )

        # No rule matched anywhere. The tenant default is the answer, and it is
        # explicit rather than assumed.
        if self.default_action == "allow":
            return Decision(True, resolved, "allow", "default", None, None, stage)
        return Decision(
            False, resolved, "deny", "default", None, None, stage, code="country_not_allowed"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "default_action": self.default_action,
            "rules": [rule.as_dict() for rule in self.rules],
        }


def infer_default_action(rules: Iterable[Rule]) -> str:
    """What happens to a country no rule mentions.

    An operator who lists allowed countries means "only these". An operator who
    lists denied ones means "these are the exceptions". Reading the rule set is
    therefore the honest way to pick the default -- asking for it again in a
    separate field is how the two disagree.
    """
    materialised = list(rules)
    if not materialised:
        return "allow"
    return "deny" if any(rule.action == "allow" for rule in materialised) else "allow"


def policy_from_rows(rows: Iterable[Any]) -> "CountryPolicy":
    rules = [Rule.from_row(row) for row in rows]
    return CountryPolicy(rules, default_action=infer_default_action(rules))


def parse_rules(payloads: Iterable[Mapping[str, Any]]) -> list[Rule]:
    """Build rules from an API payload, rejecting the first bad one by name."""
    rules = []
    for index, payload in enumerate(payloads):
        try:
            rules.append(
                Rule(
                    scope=str(payload.get("scope", "tenant")),
                    scope_id=payload.get("scope_id"),
                    country=str(payload.get("country", "")),
                    action=str(payload.get("action", "allow")),
                    reason=payload.get("reason"),
                )
            )
        except CountryPolicyError as exc:
            raise CountryPolicyError(exc.code, f"rule {index}: {exc}") from exc
    return rules


def enforce_dial(policy: CountryPolicy, number: str, **kwargs: Any) -> Decision:
    return policy.decide(number, stage="dial", **kwargs)


def enforce_inbound_answer(policy: CountryPolicy, caller: str, **kwargs: Any) -> Decision:
    return policy.decide(caller, stage="answer", **kwargs)


def enforce_transfer(policy: CountryPolicy, destination: str, **kwargs: Any) -> Decision:
    return policy.decide(destination, stage="transfer", **kwargs)


async def decide_for_session(
    session: Any,
    tenant_id: Any,
    number: str,
    *,
    stage: str,
    number_id: str | None = None,
    agent_id: str | None = None,
) -> Decision:
    """Load the tenant's persisted rules and evaluate one production call leg.

    A database or malformed-policy error propagates to the caller, which must
    fail closed before answering or dialing. Tenants with no rules retain the
    documented allow-by-default behavior.
    """
    from sqlalchemy import select

    from app.db.telephony_models import TelephonyCountryPolicy

    rows = (
        await session.execute(
            select(TelephonyCountryPolicy).where(
                TelephonyCountryPolicy.tenant_id == tenant_id
            )
        )
    ).scalars().all()
    return policy_from_rows(rows).decide(
        number,
        stage=stage,
        number_id=number_id,
        agent_id=agent_id,
    )
