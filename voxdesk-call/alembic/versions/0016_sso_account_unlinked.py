"""account unlink, as an audit action

One new label: ``SSO_ACCOUNT_UNLINKED``.

Unlinking a federated subject from an account is a security event in its own
right. It is not ``SSO_ACCOUNT_LINKED`` with a flag, and it is not
``SSO_CONNECTION_DELETED``: the connection can stay, the user stays, and only
the subject-to-user row goes. Operators need a row that says that happened.

The label has to be added the way ``Enum(AuditAction)`` writes it: the member
*name*, upper case. This is the same trap ``0014`` and ``0015`` documented —
SQLite renders the enum as ``VARCHAR`` plus a ``CHECK`` built from the model, so
a missing PostgreSQL label is invisible to the test suite and would only surface
on a real deployment, as::

    invalid input value for enum auditaction: "SSO_ACCOUNT_UNLINKED"

Downgrade is a no-op for the same reason as ``0014``: PostgreSQL cannot remove
an enum value, and any row already written with this label must keep resolving.
"""

from __future__ import annotations

from alembic import op

revision = "0016_sso_account_unlinked"
down_revision = "0015_credential_auth_rejected"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # SQLite stores the action as VARCHAR + CHECK generated from the model,
        # which already carries the member; there is no type to alter.
        return
    op.execute("ALTER TYPE auditaction ADD VALUE IF NOT EXISTS 'SSO_ACCOUNT_UNLINKED'")


def downgrade() -> None:
    # Enum values cannot be removed from a PostgreSQL type without rotating it,
    # which would rewrite an append-only audit table. Leaving an unused label is
    # the cheaper of the two.
    return
