"""Persistent coordinator planning and access-only action policies."""

import json

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = depends_on = None


def upgrade():
    op.add_column(
        "reminder_events",
        sa.Column("kind", sa.String(20), nullable=False, server_default="reminder"),
    )
    op.add_column(
        "crew_instances",
        sa.Column("configuration_plan", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.create_table(
        "coordinator_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "crew_id",
            sa.String(36),
            sa.ForeignKey("crew_instances.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("messages", sa.JSON(), nullable=False),
        sa.Column("active_request", sa.String(36), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "coordinator_turns",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(36),
            sa.ForeignKey("coordinator_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("request_payload", sa.JSON(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    connection = op.get_bind()
    for key, value in connection.execute(
        sa.text("SELECT key,value FROM workspace_settings WHERE key LIKE 'action:%'")
    ):
        data = json.loads(value) if isinstance(value, str) else dict(value)
        old = data.get("mode", "always")
        data["enabled"] = bool(data.get("enabled")) and old != "always"
        data["mode"] = "unrestricted" if old == "free" else "restricted"
        # Existing scopes remain effective until explicitly changed by the owner.
        if old == "free" and any(data.get(k) for k in ("folders", "domains", "apps", "recipients")):
            data["mode"] = "restricted"
        connection.execute(
            sa.text("UPDATE workspace_settings SET value=:value WHERE key=:key"),
            {"value": json.dumps(data), "key": key},
        )
    connection.execute(
        sa.text(
            "UPDATE action_requests SET status='rejected',error='Access settings changed. "
            "Per-action approvals are no longer used.' WHERE status='pending'"
        )
    )


def downgrade():
    raise RuntimeError("Preserve coordinator plans and access settings before downgrading")
