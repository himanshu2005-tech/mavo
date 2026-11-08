"""Persist bounded manual Crew work sessions.

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "crew_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("crew_id", sa.String(36), sa.ForeignKey("crew_instances.id", ondelete="CASCADE")),
        sa.Column("crew_name", sa.String(80), nullable=False),
        sa.Column("route_name", sa.String(80), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(160), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("model_calls", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_crew_runs_crew_id", "crew_runs", ["crew_id"])
    op.create_index(
        "uq_crew_runs_running",
        "crew_runs",
        ["crew_id"],
        unique=True,
        sqlite_where=sa.text("status = 'running'"),
        postgresql_where=sa.text("status = 'running'"),
    )


def downgrade() -> None:
    op.drop_table("crew_runs")
