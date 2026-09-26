"""Value objects for resource scope. ORM mappings stay in ``app.db.models``."""

from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class ResourceScope:
    organization_id: uuid.UUID | None
    tenant_id: uuid.UUID
    environment_id: uuid.UUID
    environment_kind: str
    environment_status: str
    resource_type: str
    resource_id: str


@dataclass(frozen=True)
class ResourcePage:
    resource_type: str
    items: tuple[dict, ...]
    limit: int
    offset: int
    total: int
