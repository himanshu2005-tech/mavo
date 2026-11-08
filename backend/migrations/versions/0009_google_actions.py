"""Durable reviewed Google actions. Additive migration."""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "action_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "crew_id", sa.String(36), sa.ForeignKey("crew_instances.id", ondelete="SET NULL")
        ),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("crew_name", sa.String(80)),
        sa.Column("generation", sa.Integer()),
        sa.Column("account_subject", sa.String(255), nullable=False),
        sa.Column("operation", sa.String(40), nullable=False),
        sa.Column("arguments", sa.JSON(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False, unique=True),
        sa.Column("status", sa.String(20), nullable=False, index=True),
        sa.Column("result", sa.JSON()),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )


def downgrade():
    raise RuntimeError("Export the action audit history before downgrading")
