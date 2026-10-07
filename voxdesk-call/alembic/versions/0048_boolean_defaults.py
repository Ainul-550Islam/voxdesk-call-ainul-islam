"""Make public-widget and telephony boolean defaults portable on PostgreSQL.

Revision ID: 0048_boolean_defaults
Revises: 0047_call_transfer_context
Create Date: 2026-10-05

The original 0040/0041 migrations used integer literals as server defaults for
Boolean columns. SQLite accepts those literals, while PostgreSQL rejects them.
The original revisions are also corrected so fresh databases migrate cleanly;
this additive migration normalizes existing schemas that already passed those
revisions on a compatible dialect or through an operator-managed rollout.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0048_boolean_defaults"
down_revision: Union[str, None] = "0047_call_transfer_context"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_BOOLEAN_DEFAULTS = (
    ("public_widget_keys", "require_published_agent", True),
    ("telephony_phone_numbers", "sip_enabled", False),
    ("telephony_phone_numbers", "inbound_enabled", True),
    ("telephony_phone_numbers", "outbound_enabled", True),
    ("telephony_call_sessions", "is_simulation", False),
    ("telephony_call_sessions", "usage_finalized", False),
    ("telephony_usage_ledger", "is_simulation", False),
)


def _set_boolean_defaults() -> None:
    for table_name, column_name, default in _BOOLEAN_DEFAULTS:
        op.alter_column(
            table_name,
            column_name,
            existing_type=sa.Boolean(),
            existing_nullable=False,
            server_default=sa.true() if default else sa.false(),
        )


def upgrade() -> None:
    _set_boolean_defaults()


def downgrade() -> None:
    # The original integer literals are not valid PostgreSQL defaults. Keep the
    # corrected boolean semantics when rolling back only this repair revision.
    _set_boolean_defaults()
