"""Feature-flag evaluation. Not a second product switch and not a store.

Precedence, when overrides are trusted, is environment, then tenant, then
organization, then the global default. Privileged flags stay off. Evaluation
does not write a row and does not change ``Tenant.outbound_enabled`` or any
other product column.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.tenancy.exceptions import FlagDenied, ValidationFailed

# Safe defaults. Privileged bypass flags are locked off.
_DEFAULTS: dict[str, bool] = {
    "outbound_calling": False,
    "call_recording": False,
    "sms_channel": False,
    "cross_environment_debug": False,
    "residency_override": False,
}

LOCKED = frozenset({"cross_environment_debug", "residency_override"})
CATALOG = frozenset(_DEFAULTS)


@dataclass(frozen=True)
class FlagDecision:
    flag: str
    enabled: bool
    source: str
    persisted: bool

    def as_dict(self) -> dict:
        return {
            "flag": self.flag,
            "enabled": self.enabled,
            "source": self.source,
            "persisted": self.persisted,
        }


def parse_flag(flag: str) -> str:
    name = (flag or "").strip().lower()
    if name not in CATALOG:
        raise ValidationFailed("Unknown feature flag")
    return name


def evaluate(
    flag: str,
    *,
    organization: dict[str, bool] | None = None,
    tenant: dict[str, bool] | None = None,
    environment: dict[str, bool] | None = None,
    trust_overrides: bool = False,
) -> FlagDecision:
    """Resolve one flag. ``persisted`` is always false."""
    name = parse_flag(flag)
    if name in LOCKED or not trust_overrides:
        source = "locked" if name in LOCKED else "global"
        enabled = False if name in LOCKED else _DEFAULTS[name]
        return FlagDecision(name, enabled, source, False)
    chosen = _DEFAULTS[name]
    source = "global"
    for layer, values in (
        ("organization", organization),
        ("tenant", tenant),
        ("environment", environment),
    ):
        if values is not None and name in values:
            chosen = bool(values[name])
            source = layer
    return FlagDecision(name, chosen, source, False)


def reject_client_override(
    *,
    environment_override: bool | None,
    tenant_override: bool | None,
    organization_override: bool | None,
) -> None:
    """A request body cannot install a flag. There is no flag table to write."""
    if (
        environment_override is not None
        or tenant_override is not None
        or organization_override is not None
    ):
        raise FlagDenied("Feature-flag overrides are not accepted from the client")
