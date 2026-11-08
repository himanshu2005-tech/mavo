"""Human-readable action targets for approval review."""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = depends_on = None


def upgrade():
    op.add_column("action_requests", sa.Column("target_summary", sa.String(500), nullable=True))


def downgrade():
    raise RuntimeError("Preserve action review data before downgrading")
