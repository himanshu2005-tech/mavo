"""Keep never-activated incomplete chat setups as drafts, preserving all content."""

import json

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = depends_on = None


def upgrade():
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "SELECT id, configuration_plan FROM crew_instances c "
            "WHERE is_running = false AND NOT EXISTS "
            "(SELECT 1 FROM crew_runs r WHERE r.crew_id = c.id)"
        )
    ).all()
    for crew_id, raw in rows:
        plan = json.loads(raw) if isinstance(raw, str) else raw
        if plan and plan.get("phase") in {"needs_input", "needs_access"}:
            connection.execute(
                sa.text("UPDATE crew_instances SET status='draft' WHERE id=:id"), {"id": crew_id}
            )


def downgrade():
    raise RuntimeError("Preserve saved setup drafts before downgrading.")
