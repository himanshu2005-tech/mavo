"""Add cloud model provider configuration.

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("messages") as batch_op:
        batch_op.add_column(
            sa.Column("provider", sa.String(length=32), nullable=False, server_default="ollama")
        )
    op.create_table(
        "provider_configs",
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("default_model", sa.String(length=160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("provider"),
    )


def downgrade() -> None:
    op.drop_table("provider_configs")
    with op.batch_alter_table("messages") as batch_op:
        batch_op.drop_column("provider")
