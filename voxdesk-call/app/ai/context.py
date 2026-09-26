"""Immutable runtime context.

The authenticated tenant is the only tenant a governed call may use. A later
assignment cannot retarget the call: the dataclass is frozen.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.tenancy.isolation import BoundaryDenied


@dataclass(frozen=True)
class RuntimeContext:
    tenant_id: uuid.UUID
    environment_id: uuid.UUID | None
    environment_kind: str
    channel: str
    principal: str
    request_id: str
    trace_id: str
    call_id: uuid.UUID | None = None
    turn_id: uuid.UUID | None = None
    agent_id: str | None = None
    user_id: uuid.UUID | None = None

    def reject_claim(self, claimed_tenant_id: uuid.UUID | None) -> None:
        """A client tenant id never replaces the authenticated tenant."""
        if claimed_tenant_id is not None and claimed_tenant_id != self.tenant_id:
            raise BoundaryDenied()
