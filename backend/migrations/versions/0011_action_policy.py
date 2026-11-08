"""Fence approvals against changed policies and record approval origin."""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = depends_on = None


def upgrade():
    op.add_column("action_requests", sa.Column("policy_revision", sa.String(36), nullable=True))
    op.add_column("action_requests", sa.Column("approved_by", sa.String(16), nullable=True))


def downgrade():
    raise RuntimeError("Preserve action policy history before downgrading")
