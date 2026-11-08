"""Link a desktop alert to the task step it is about, so a question a task asks on the desktop is
answered there (it was sent to Mavo's hidden owner Crew instead: "Crew not found")."""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = depends_on = None


def upgrade():
    op.add_column("reminder_events", sa.Column("task_run_id", sa.String(36), nullable=True))
    op.add_column("reminder_events", sa.Column("task_step_id", sa.String(40), nullable=True))


def downgrade():
    raise RuntimeError("This additive migration preserves user data; restore a backup to downgrade")
