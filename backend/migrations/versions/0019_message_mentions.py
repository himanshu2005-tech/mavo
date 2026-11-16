"""Retain canonical Crew mention identities alongside existing message text."""

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = depends_on = None


def upgrade():
    op.add_column(
        "messages", sa.Column("mentioned_crew_ids", sa.JSON(), nullable=False, server_default="[]")
    )


def downgrade():
    op.drop_column("messages", "mentioned_crew_ids")
