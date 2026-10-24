"""The minimum-necessary guard, as a FastAPI dependency.

The PHI field filtering itself lives in
:mod:`app.compliance_v2.hipaa.minimum_necessary`; this module only adapts it to
the request lifecycle, so a call-transcript or call-replay handler can ask for
the guard instead of re-deriving the tenant's policy on every code path.

Two design points matter:

* the guard is **inactive** until a tenant turns HIPAA mode on. A deployment that
  never handles PHI keeps the responses it always had, byte for byte;
* when it is active, filtering is a *removal* of fields the caller's role may not
  see on that surface. It never invents a placeholder, so a filtered field is
  visibly absent rather than a plausible-looking empty value.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Mapping
from typing import Any

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, get_context
from app.compliance_v2.hipaa import minimum_necessary
from app.compliance_v2.hipaa.phi_tagging import PhiPolicyView, load_policy
from app.db.session import get_session


@dataclasses.dataclass(frozen=True)
class PhiGuard:
    """A per-request view of what the caller may see on one surface."""

    surface: str
    role: str
    policy: PhiPolicyView

    @property
    def active(self) -> bool:
        return bool(self.policy.hipaa_mode)

    def allowlist(self) -> frozenset[str]:
        return minimum_necessary.allowlist_for(
            self.role, self.surface, policy=self.policy
        )

    def filter_records(
        self, records: Iterable[Mapping[str, Any]], *, table: str
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        """Filter rows through the tenant policy; a no-op when HIPAA mode is off."""
        if not self.active:
            return [dict(record) for record in records], {}
        return minimum_necessary.filter_records(
            records,
            table=table,
            role=self.role,
            surface=self.surface,
            policy=self.policy,
        )

    def filter_record(
        self, record: Mapping[str, Any], *, table: str
    ) -> dict[str, Any]:
        if not self.active or not isinstance(record, Mapping):
            return dict(record or {})
        return minimum_necessary.filter_record(
            record,
            table=table,
            role=self.role,
            surface=self.surface,
            policy=self.policy,
        ).record


def phi_guard(surface: str):
    """Build the dependency for one surface (``transcript``/``replay``/``export``)."""

    async def dependency(
        ctx: TenantContext = Depends(get_context),
        session: AsyncSession = Depends(get_session),
    ) -> PhiGuard:
        policy = await load_policy(session, ctx.tenant_id)
        return PhiGuard(surface=surface, role=ctx.role, policy=policy)

    return dependency


__all__ = ["PhiGuard", "phi_guard"]
