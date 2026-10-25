"""Short-lived Twilio Voice and coturn credentials for human agents."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center_v2.repository import as_utc, utcnow
from app.contact_center_v2.settings import ContactCenterV2Settings, settings
from app.db.contact_center_v2_models import SoftphoneSession
from .service import access_token, ice_servers


@dataclass(frozen=True)
class TokenBundle:
    access_token: str
    expires_in: int
    identity: str
    ice_servers: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "token": self.access_token,
            "token_ttl_seconds": self.expires_in,
            "identity": self.identity,
            "ice_servers": self.ice_servers,
        }


def mint_token_bundle(
    row: SoftphoneSession,
    config: ContactCenterV2Settings = settings,
) -> TokenBundle:
    if row.status != "active" or as_utc(row.expires_at) <= utcnow():
        raise ValueError("Cannot mint credentials for an inactive softphone session")
    token, ttl = access_token(row, config)
    return TokenBundle(token, ttl, row.client_identity, ice_servers(row.user_id, config))


def session_credentials(
    row: SoftphoneSession,
    config: ContactCenterV2Settings = settings,
) -> dict[str, Any]:
    payload = row.as_dict()
    payload.update(mint_token_bundle(row, config).as_dict())
    return payload


async def revoke_softphone_sessions(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
) -> int:
    """Revoke every active CC2 credential in the caller's auth transaction.

    Canonical logout and SCIM deactivation call this boundary before their own
    commit. Clearing the active-device key and one-time WS ticket prevents a
    disconnected browser from reusing either registration or signaling state.
    """
    result = await session.execute(
        update(SoftphoneSession)
        .where(
            SoftphoneSession.tenant_id == tenant_id,
            SoftphoneSession.user_id == user_id,
            SoftphoneSession.status == "active",
        )
        .values(
            status="revoked",
            revoked_at=utcnow(),
            active_device_key=None,
            ws_ticket_hash="",
        )
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)


__all__ = [
    "TokenBundle",
    "access_token",
    "ice_servers",
    "mint_token_bundle",
    "revoke_softphone_sessions",
    "session_credentials",
]
