"""The one row the agency portfolio needs: which client belongs to which agency.

PART 16 / P16-02. The spec's file tree assumed `app/organization/` could express
an agency portfolio, and it cannot: an `Organization` is the parent of tenants,
`OrganizationMembership` ties a user to the organization of *their own* tenant
(``add_member`` refuses a cross-organization target), and nothing links one
organization to another. So a portfolio needs a link table, and inventing the
grouping in application memory would be the version of this feature that loses
data the first time two workers disagree.

The link is deliberately narrow — an agency organization and a client
organization, plus who created it. It is not a billing record and it does not
grant access: every operation on a client still goes through the tenant and
organization authorization that already exists. Deleting either side cascades the
link so a deleted organization cannot keep a client attached to it.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AgencyClientLink(Base):
    """One client organization in one agency's portfolio."""

    __tablename__ = "agency_client_links"
    __table_args__ = (
        # A client belongs to exactly one agency. Two agencies serving the same
        # client is a commercial arrangement, not a data model — it would make
        # "who may open this client's account" ambiguous, which is the one
        # question this table exists to answer.
        UniqueConstraint("client_organization_id", name="uq_agency_client_link_client"),
        Index("ix_agency_client_link_agency", "agency_organization_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    agency_organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    #: Set from the authenticated principal by the route, never from a body claim.
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
