"""Independent persistent Core conversations; preserve the legacy main chat."""

import json
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = depends_on = None


def upgrade():
    connection = op.get_bind()
    if connection.dialect.name == "sqlite":
        # SQLite supports an inline nullable reference in ADD COLUMN. No table
        # recreation, lost rows, or temporary foreign-key disabling is needed.
        op.execute(
            "ALTER TABLE coordinator_sessions ADD COLUMN conversation_id VARCHAR(36) "
            "REFERENCES conversations(id) ON DELETE CASCADE"
        )
    else:
        op.add_column(
            "coordinator_sessions",
            sa.Column(
                "conversation_id",
                sa.String(36),
                sa.ForeignKey("conversations.id", ondelete="CASCADE"),
                nullable=True,
            ),
        )
    op.create_index(
        "ix_coordinator_sessions_conversation_id",
        "coordinator_sessions",
        ["conversation_id"],
        unique=True,
    )
    setting = connection.execute(
        sa.text("SELECT value FROM workspace_settings WHERE key='core-conversation'")
    ).scalar()
    value = json.loads(setting) if isinstance(setting, str) else setting or {}
    conversation_id = value.get("id")
    if (
        not conversation_id
        or not connection.execute(
            sa.text("SELECT id FROM conversations WHERE id=:id"), {"id": conversation_id}
        ).scalar()
    ):
        conversation_id = connection.execute(
            sa.text("SELECT id FROM conversations ORDER BY updated_at DESC LIMIT 1")
        ).scalar()
    if conversation_id:
        thread = connection.execute(
            sa.text("SELECT id FROM coordinator_sessions WHERE id='main'")
        ).scalar()
        if not thread:
            connection.execute(
                sa.text("INSERT INTO coordinator_sessions (id, messages) VALUES ('main', '[]')")
            )
        connection.execute(
            sa.text("UPDATE coordinator_sessions SET conversation_id=:id WHERE id='main'"),
            {"id": conversation_id},
        )
        if setting is None:
            connection.execute(
                sa.text(
                    "INSERT INTO workspace_settings (key,value) VALUES ('core-conversation',:value)"
                ),
                {"value": json.dumps({"id": conversation_id})},
            )
    for old_id in (
        connection.execute(
            sa.text(
                "SELECT id FROM conversations WHERE id NOT IN "
                "(SELECT conversation_id FROM coordinator_sessions "
                "WHERE conversation_id IS NOT NULL)"
            )
        )
        .scalars()
        .all()
    ):
        connection.execute(
            sa.text(
                "INSERT INTO coordinator_sessions (id,conversation_id,messages) "
                "VALUES (:id,:conversation,'[]')"
            ),
            {"id": str(uuid4()), "conversation": old_id},
        )


def downgrade():
    raise RuntimeError(
        "Preserve independent conversations and coordinator history before downgrading"
    )
