"""Add one-time task runs (the plan, each step's progress, the reported result) and a flag for the
short-lived agents that do their steps."""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = depends_on = None


def upgrade():
    op.add_column(
        "crew_instances",
        sa.Column("hidden", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "task_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("title", sa.String(80), nullable=False),
        sa.Column("request", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("plan", sa.JSON(), nullable=False),
        sa.Column("steps", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("budget_calls", sa.Integer(), nullable=False, server_default="12"),
        sa.Column("used_calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("conversation_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_task_runs_status", "task_runs", ["status"])
    op.create_index("ix_task_runs_created_at", "task_runs", ["created_at"])
    op.create_index("ix_task_runs_conversation_id", "task_runs", ["conversation_id"])


def downgrade():
    raise RuntimeError("This additive migration preserves user data; restore a backup to downgrade")
