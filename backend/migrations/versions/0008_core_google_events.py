"""Core assistant proposals, Google connections, and resource event triggers.
Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "crew_instances",
        sa.Column("trigger_type", sa.String(32), nullable=False, server_default="interval"),
    )
    op.add_column("crew_instances", sa.Column("mail_sender", sa.String(160), nullable=True))
    op.add_column("crew_instances", sa.Column("mail_subject", sa.String(160), nullable=True))
    op.add_column(
        "crew_instances",
        sa.Column("event_cursor", sa.Integer(), nullable=False, server_default="0"),
    )
    op.execute("UPDATE crew_instances SET trigger_type='daily' WHERE template_id='schedule'")
    op.create_table(
        "workspace_settings",
        sa.Column("key", sa.String(80), primary_key=True),
        sa.Column("value", sa.JSON(), nullable=False),
    )
    op.create_table(
        "google_accounts",
        sa.Column("id", sa.String(16), primary_key=True),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("cursors", sa.JSON(), nullable=False),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "resource_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("account_subject", sa.String(255), nullable=False, index=True),
        sa.Column("resource", sa.String(32), nullable=False, index=True),
        sa.Column("deduplication_key", sa.String(300), nullable=False, unique=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "crew_proposals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.String(36),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column(
            "created_crew_id",
            sa.String(36),
            sa.ForeignKey("crew_instances.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    raise RuntimeError("Export Google connection and proposal data before downgrading")
