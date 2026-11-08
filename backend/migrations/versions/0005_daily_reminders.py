"""Add daily Crew reminder schedules and durable events.

Revision ID: 0005
Revises: 0004
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("crew_instances") as batch_op:
        batch_op.add_column(sa.Column("schedule_time", sa.String(length=5), nullable=True))
        batch_op.add_column(sa.Column("reminder_text", sa.Text(), nullable=True))
    op.create_table(
        "reminder_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("crew_id", sa.String(length=36), nullable=False),
        sa.Column("crew_name", sa.String(length=80), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("local_date", sa.String(length=10), nullable=False),
        sa.Column("deduplication_key", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["crew_id"], ["crew_instances.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("deduplication_key"),
    )
    op.create_index("ix_reminder_events_crew_id", "reminder_events", ["crew_id"])
    op.create_index("ix_reminder_events_scheduled_for", "reminder_events", ["scheduled_for"])
    op.create_index("ix_reminder_events_status", "reminder_events", ["status"])


def downgrade() -> None:
    op.drop_index("ix_reminder_events_status", table_name="reminder_events")
    op.drop_index("ix_reminder_events_scheduled_for", table_name="reminder_events")
    op.drop_index("ix_reminder_events_crew_id", table_name="reminder_events")
    op.drop_table("reminder_events")
    with op.batch_alter_table("crew_instances") as batch_op:
        batch_op.drop_column("reminder_text")
        batch_op.drop_column("schedule_time")
