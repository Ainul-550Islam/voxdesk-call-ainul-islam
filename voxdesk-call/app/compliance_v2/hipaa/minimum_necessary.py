"""Minimum-necessary field filtering for PHI-bearing exports and replays.

"Minimum necessary" (45 CFR 164.502(b)) means a caller sees the fields their job
requires and no more. This module is the single implementation of that rule:

* an allowlist per role, per surface (export, replay, transcript);
* an allowlist may be narrowed per tenant through the policy's
  ``minimum_necessary`` mapping, never widened beyond the role's list;
* a field that is neither allowlisted nor present is simply absent from the
  response — the API does not return a null placeholder for data the caller may
  not see, because the number of keys itself leaks structure;
* the filter reports what it removed so the caller can audit the decision.

Non-PHI tenants are unaffected: with HIPAA mode off, the filter passes the
record through unchanged apart from the role's ordinary field policy.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Iterable, Mapping

from app.compliance_v2.hipaa.phi_tagging import PhiPolicyView, phi_fields_for
from app.db.models import UserRole

SURFACE_EXPORT = "export"
SURFACE_REPLAY = "replay"
SURFACE_TRANSCRIPT = "transcript"
SURFACES = (SURFACE_EXPORT, SURFACE_REPLAY, SURFACE_TRANSCRIPT)

#: PHI fields each role may see, per surface. Owners and admins get the full
#: clinical content; managers get the operational subset; agents get the least
#: that still lets them handle a call.
ROLE_PHI_ALLOWLIST: dict[str, dict[str, frozenset[str]]] = {
    UserRole.OWNER.value: {
        SURFACE_EXPORT: frozenset({"summary", "text", "reason", "custom_fields", "name", "full_name", "email", "customer_name"}),
        SURFACE_REPLAY: frozenset({"summary", "text", "reason"}),
        SURFACE_TRANSCRIPT: frozenset({"text", "summary"}),
    },
    UserRole.ADMIN.value: {
        SURFACE_EXPORT: frozenset({"summary", "text", "reason", "custom_fields", "name", "full_name", "email", "customer_name"}),
        SURFACE_REPLAY: frozenset({"summary", "text", "reason"}),
        SURFACE_TRANSCRIPT: frozenset({"text", "summary"}),
    },
    UserRole.MANAGER.value: {
        SURFACE_EXPORT: frozenset({"summary", "reason", "name"}),
        SURFACE_REPLAY: frozenset({"summary"}),
        SURFACE_TRANSCRIPT: frozenset({"text"}),
    },
    UserRole.AGENT.value: {
        SURFACE_EXPORT: frozenset({"summary"}),
        SURFACE_REPLAY: frozenset({"summary"}),
        SURFACE_TRANSCRIPT: frozenset({"text"}),
    },
    UserRole.VIEWER.value: {
        SURFACE_EXPORT: frozenset(),
        SURFACE_REPLAY: frozenset(),
        SURFACE_TRANSCRIPT: frozenset(),
    },
}


@dataclasses.dataclass(frozen=True)
class FilterOutcome:
    record: dict[str, Any]
    removed: tuple[str, ...]

    @property
    def filtered(self) -> bool:
        return bool(self.removed)


def _role_key(role: UserRole | str) -> str:
    return role.value if isinstance(role, UserRole) else str(role)


def allowlist_for(
    role: UserRole | str,
    surface: str,
    *,
    policy: PhiPolicyView | None = None,
) -> frozenset[str]:
    """The effective allowlist: the role's list, narrowed by the tenant policy."""
    if surface not in SURFACES:
        raise ValueError(f"unknown surface {surface!r}")
    base = ROLE_PHI_ALLOWLIST.get(_role_key(role), {}).get(surface, frozenset())
    if policy is None or not policy.minimum_necessary:
        return frozenset(base)
    override = policy.minimum_necessary.get(surface)
    if not isinstance(override, list):
        return frozenset(base)
    requested = {str(item) for item in override}
    # An override narrows; it can never grant a field the role does not hold.
    return frozenset(base & requested)


def filter_record(
    record: Mapping[str, Any],
    *,
    table: str,
    role: UserRole | str,
    surface: str,
    policy: PhiPolicyView,
) -> FilterOutcome:
    """Remove PHI fields the caller's role may not see on this surface."""
    if not isinstance(record, Mapping):
        return FilterOutcome(record={}, removed=())
    output = dict(record)
    if not policy.hipaa_mode:
        return FilterOutcome(record=output, removed=())
    allowed = allowlist_for(role, surface, policy=policy)
    tagged = phi_fields_for(table)
    removed: list[str] = []
    for column in sorted(tagged):
        if column in output and column not in allowed:
            output.pop(column)
            removed.append(column)
    return FilterOutcome(record=output, removed=tuple(removed))


def filter_records(
    records: Iterable[Mapping[str, Any]],
    *,
    table: str,
    role: UserRole | str,
    surface: str,
    policy: PhiPolicyView,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Filter many records and summarise what was removed."""
    output: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for record in records:
        outcome = filter_record(record, table=table, role=role, surface=surface, policy=policy)
        output.append(outcome.record)
        for column in outcome.removed:
            counts[column] = counts.get(column, 0) + 1
    return output, counts


def surface_for_path(path: str) -> str:
    """Map a request path onto a minimum-necessary surface."""
    lowered = (path or "").lower()
    if "transcript" in lowered:
        return SURFACE_TRANSCRIPT
    if "replay" in lowered or "recording" in lowered:
        return SURFACE_REPLAY
    return SURFACE_EXPORT


__all__ = [
    "ROLE_PHI_ALLOWLIST",
    "SURFACES",
    "SURFACE_EXPORT",
    "SURFACE_REPLAY",
    "SURFACE_TRANSCRIPT",
    "FilterOutcome",
    "allowlist_for",
    "filter_record",
    "filter_records",
    "surface_for_path",
]
