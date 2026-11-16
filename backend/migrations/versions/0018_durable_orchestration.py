"""Additive orchestration messages, conversation events and versioned configuration."""

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = depends_on = None


def upgrade():
    op.add_column(
        "crew_instances",
        sa.Column("config_version", sa.Integer(), nullable=False, server_default="0"),
    )
    for name in ("revision", "attempts", "reserved_calls"):
        op.add_column(
            "task_runs", sa.Column(name, sa.Integer(), nullable=False, server_default="0")
        )
    op.add_column("task_runs", sa.Column("lease_token", sa.String(36), nullable=True))
    op.add_column("task_runs", sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True))
    # Frozen DDL: future ORM changes must not change what this historical migration creates.
    op.create_table(
        "agent_envelopes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("idempotency_key", sa.String(180), nullable=False, unique=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("sender", sa.String(160), nullable=False),
        sa.Column("task_id", sa.String(36)),
        sa.Column("run_id", sa.String(36)),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "agent_deliveries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "message_id",
            sa.String(36),
            sa.ForeignKey("agent_envelopes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("recipient", sa.String(160), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("lease_owner", sa.String(36)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("message_id", "recipient"),
    )
    op.create_table(
        "conversation_events",
        sa.Column("seq", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("id", sa.String(36), nullable=False, unique=True),
        sa.Column("source_key", sa.String(180), nullable=False, unique=True),
        sa.Column(
            "conversation_id", sa.String(36), sa.ForeignKey("conversations.id", ondelete="CASCADE")
        ),
        sa.Column("crew_id", sa.String(36)),
        sa.Column("task_id", sa.String(36)),
        sa.Column("step_id", sa.String(40)),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("sender", sa.String(160), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("private", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "configuration_edits",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("request_key", sa.String(180), nullable=False, unique=True),
        sa.Column("crew_id", sa.String(36), sa.ForeignKey("crew_instances.id", ondelete="CASCADE")),
        sa.Column("version", sa.Integer()),
        sa.Column("base_version", sa.Integer(), nullable=False),
        sa.Column("scope", sa.String(24), nullable=False),
        sa.Column("target_id", sa.String(80)),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("before", sa.JSON(), nullable=False),
        sa.Column("changes", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("crew_id", "version"),
    )
    for table, columns in {
        "agent_envelopes": ("kind", "sender", "task_id", "run_id"),
        "agent_deliveries": ("message_id", "recipient", "status"),
        "conversation_events": ("conversation_id", "crew_id", "task_id", "kind", "status"),
        "configuration_edits": ("crew_id", "status"),
    }.items():
        for column in columns:
            op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade():
    for table in (
        "configuration_edits",
        "conversation_events",
        "agent_deliveries",
        "agent_envelopes",
    ):
        op.drop_table(table)
    for name in ("lease_until", "lease_token", "reserved_calls", "attempts", "revision"):
        op.drop_column("task_runs", name)
    op.drop_column("crew_instances", "config_version")
