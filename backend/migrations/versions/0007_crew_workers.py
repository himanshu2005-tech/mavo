"""Add persistent Crew workers, checkpoints, budgets, and work leases.

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in [
        sa.Column("is_running", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("runtime_state", sa.String(32), nullable=False, server_default="stopped"),
        sa.Column("review_interval_minutes", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("daily_call_limit", sa.Integer(), nullable=False, server_default="12"),
        sa.Column("daily_calls_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("budget_day", sa.String(10), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("runtime_generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_runtime_error", sa.Text(), nullable=True),
        sa.Column("checkpoint", sa.Text(), nullable=True),
        sa.Column("observation", sa.JSON(), nullable=False, server_default="{}"),
    ]:
        op.add_column("crew_instances", column)
    op.create_index("ix_crew_instances_is_running", "crew_instances", ["is_running"])
    op.add_column(
        "crew_runs", sa.Column("kind", sa.String(16), nullable=False, server_default="manual")
    )
    op.add_column("crew_runs", sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "crew_runs",
        sa.Column("runtime_generation", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    # Preserve worker/checkpoint data; rollback requires an explicit data export.
    raise RuntimeError("Export Crew worker data before downgrading this schema")
