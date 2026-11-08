"""Add named model routes.

Revision ID: 0004
Revises: 0003
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_routes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=160), nullable=False),
        sa.Column("credential_id", sa.String(length=80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_model_routes_provider", "model_routes", ["provider"])
    with op.batch_alter_table("crew_instances") as batch_op:
        batch_op.add_column(sa.Column("route_id", sa.String(length=36), nullable=True))
        batch_op.create_index("ix_crew_instances_route_id", ["route_id"])
        batch_op.create_foreign_key(
            "fk_crew_instances_route_id_model_routes",
            "model_routes",
            ["route_id"],
            ["id"],
            ondelete="RESTRICT",
        )


def downgrade() -> None:
    with op.batch_alter_table("crew_instances") as batch_op:
        batch_op.drop_constraint("fk_crew_instances_route_id_model_routes", type_="foreignkey")
        batch_op.drop_index("ix_crew_instances_route_id")
        batch_op.drop_column("route_id")
    op.drop_index("ix_model_routes_provider", table_name="model_routes")
    op.drop_table("model_routes")
