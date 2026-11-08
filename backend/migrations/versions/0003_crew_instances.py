"""Add persisted Crew instances.

Revision ID: 0003
Revises: 0002
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "crew_instances",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("template_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("responsibilities", sa.Text(), nullable=False),
        sa.Column("autonomy_mode", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=160), nullable=False),
        sa.Column("max_model_calls", sa.Integer(), nullable=False),
        sa.Column("resources", sa.JSON(), nullable=False),
        sa.Column("permissions", sa.JSON(), nullable=False),
        sa.Column("triggers", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_crew_instances_status", "crew_instances", ["status"])
    op.create_index("ix_crew_instances_template_id", "crew_instances", ["template_id"])


def downgrade() -> None:
    op.drop_index("ix_crew_instances_template_id", table_name="crew_instances")
    op.drop_index("ix_crew_instances_status", table_name="crew_instances")
    op.drop_table("crew_instances")
