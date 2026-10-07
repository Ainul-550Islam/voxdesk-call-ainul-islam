"""Environment promotion policy.

A promotion plan is not a deployment and not a configuration copy. Development
may be planned toward staging, and staging toward production. No other step
is allowed. Secrets are named so a caller can see they are withheld.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.tenancy.exceptions import BoundaryDenied, LifecycleDenied
from app.tenancy.settings import SECRET_FIELDS

_NEXT = {
    "development": "staging",
    "staging": "production",
}


@dataclass(frozen=True)
class PromotionPlan:
    source_kind: str
    target_kind: str
    copies_secrets: bool
    copies_configuration: bool
    overwrites_production: bool
    executes_deployment: bool
    withheld_fields: tuple[str, ...]

    def as_dict(self) -> dict:
        return {
            "source_kind": self.source_kind,
            "target_kind": self.target_kind,
            "copies_secrets": self.copies_secrets,
            "copies_configuration": self.copies_configuration,
            "overwrites_production": self.overwrites_production,
            "executes_deployment": self.executes_deployment,
            "withheld_fields": list(self.withheld_fields),
        }


def assert_same_tenant(source_tenant_id: uuid.UUID, target_tenant_id: uuid.UUID) -> None:
    if source_tenant_id != target_tenant_id:
        raise BoundaryDenied()


def plan_promotion(source_kind: str, target_kind: str) -> PromotionPlan:
    """Describe the only legal next step. Does not read or write a row."""
    expected = _NEXT.get(source_kind)
    if expected is None or expected != target_kind:
        raise LifecycleDenied(
            "Promotion must follow development to staging, or staging to production"
        )
    return PromotionPlan(
        source_kind=source_kind,
        target_kind=target_kind,
        copies_secrets=False,
        copies_configuration=False,
        overwrites_production=False,
        executes_deployment=False,
        withheld_fields=tuple(sorted(SECRET_FIELDS)),
    )
