"""Explicit automatic sampling bindings and immutable auto-review inputs.

Legacy rules remain manual; legacy run snapshots stay empty, not fabricated.
Drain QA workers and export snapshots before downgrade.
"""
from alembic import op
import sqlalchemy as sa

revision = "0054_qa_runtime"
down_revision = "0053_analysis_backfill"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("qa_sampling_rules", sa.Column("scorecard_id", sa.Uuid(), nullable=True))
    op.create_foreign_key("fk_qa_sampling_scorecard", "qa_sampling_rules", "qa_scorecards", ["scorecard_id"], ["id"], ondelete="RESTRICT")
    for name in ("rubric_snapshot", "transcript_snapshot"):
        op.add_column("qa_auto_review_runs", sa.Column(name, sa.JSON(), nullable=False, server_default="{}"))


def downgrade():
    for name in ("transcript_snapshot", "rubric_snapshot"):
        op.drop_column("qa_auto_review_runs", name)
    op.drop_constraint("fk_qa_sampling_scorecard", "qa_sampling_rules", type_="foreignkey")
    op.drop_column("qa_sampling_rules", "scorecard_id")
