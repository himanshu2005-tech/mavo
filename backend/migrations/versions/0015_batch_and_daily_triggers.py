"""Add bounded daily trigger instances and chat batch bindings without removing old data."""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = depends_on = None


def upgrade():
    op.add_column(
        "crew_instances",
        sa.Column("daily_triggers", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )
    op.add_column(
        "coordinator_sessions",
        sa.Column("crew_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )
    # Legacy schedule_time/deduplication keys remain intact. Runtime adapts them
    # to a stable 'legacy' trigger instead of reissuing already-delivered reminders.


def downgrade():
    raise RuntimeError("This additive migration preserves user data; restore a backup to downgrade")
