"""Quota checks in front of the existing resolver.

Billing remains the authority for voice minutes and plan caps. This module
rejects malformed input and then calls ``app.tenancy.quota``, which calls
``app.quotas``. It does not keep a second meter.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Tenant
from app.quotas.models import QuotaKey
from app.quotas.service import parse_key
from app.tenancy.exceptions import LimitDenied, ValidationFailed
from app.tenancy.quota import tenant_quota


def _used(value: int | None) -> int | None:
    if value is None:
        return None
    if value < 0:
        raise LimitDenied("Usage cannot be negative")
    return value


async def inspect(
    session: AsyncSession,
    tenant: Tenant,
    key: QuotaKey | str,
    *,
    used: int | None = None,
    environment_id: uuid.UUID | None = None,
):
    """Effective decision. Unknown stays unknown. Negative usage never reaches billing."""
    try:
        parse_key(key)
    except ValueError as exc:
        raise ValidationFailed("Unknown quota key") from exc
    return await tenant_quota(
        session,
        tenant,
        key,
        environment_id=environment_id,
        used=_used(used),
    )
