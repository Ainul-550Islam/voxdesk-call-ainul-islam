"""Tenant-defined roles (PART 16, P16-01).

Two tables, and the split matters:

* `custom_roles` is the role itself — a name and a set of permission ids from
  the single vocabulary in `app.auth.permissions`. There is deliberately **no**
  second permission list here: a custom role cannot invent a capability, only
  name existing ones.
* `custom_role_assignments` is the grant — one row per (role, subject), where a
  subject is a user, a service account, or a SCIM group. The unique key makes a
  duplicate grant impossible rather than merely unlikely, and the tenant column
  is on both rows so a query that forgets to join still cannot cross tenants.

A custom role is *additive*. Nothing here can remove a permission the built-in
role already holds; a tenant that wants less uses a smaller built-in role. That
keeps `app.auth.rbac.ROLE_PERMISSIONS` the single place where "a viewer cannot
delete calls" is decided.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base

#: The subject kinds a grant can name. A check constraint enforces it in the
#: database, so a new kind cannot slip in through a hand-written INSERT.
SUBJECT_TYPES: tuple[str, ...] = ("user", "service_account", "scim_group")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CustomRole(Base):
    """A tenant-scoped, named set of existing permissions."""

    __tablename__ = "custom_roles"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_custom_roles_tenant_name"),
        Index("ix_custom_roles_tenant_active", "tenant_id", "is_active"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    #: Permission *ids* (`call:read`), never a role name and never free text.
    permissions: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    def permission_ids(self) -> list[str]:
        return [str(value) for value in (self.permissions or [])]


class CustomRoleAssignment(Base):
    """One grant: this custom role applies to this subject."""

    __tablename__ = "custom_role_assignments"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "role_id", "subject_type", "subject_id",
            name="uq_custom_role_assignment",
        ),
        CheckConstraint(
            "subject_type IN ('user', 'service_account', 'scim_group')",
            name="ck_custom_role_assignment_subject",
        ),
        Index("ix_custom_role_assignments_subject", "tenant_id", "subject_type", "subject_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("custom_roles.id", ondelete="CASCADE"), index=True, nullable=False
    )
    subject_type: Mapped[str] = mapped_column(String(24), nullable=False)
    #: UUIDs are stored as text so a SCIM group key from another IDP still fits.
    subject_id: Mapped[str] = mapped_column(String(120), nullable=False)
    assigned_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "role_id": str(self.role_id),
            "subject_type": self.subject_type,
            "subject_id": self.subject_id,
            "assigned_by_user_id": str(self.assigned_by_user_id) if self.assigned_by_user_id else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
