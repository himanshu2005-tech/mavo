"""Crew-owned logical agents, preserving legacy workers and running task identities."""

from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = depends_on = None


def upgrade():
    op.add_column(
        "messages", sa.Column("mentioned_agent_ids", sa.JSON(), nullable=False, server_default="[]")
    )
    op.create_table(
        "crew_agents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "crew_id",
            sa.String(36),
            sa.ForeignKey("crew_instances.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("key", sa.String(24), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("role", sa.String(80), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=False),
        sa.Column("route_id", sa.String(36), sa.ForeignKey("model_routes.id", ondelete="RESTRICT")),
        sa.Column("definition", sa.JSON(), nullable=False),
        sa.Column("execution", sa.JSON(), nullable=False),
        sa.Column("calls_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("budget_run_id", sa.String(36)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column(
            "worker_id",
            sa.String(36),
            sa.ForeignKey("crew_instances.id", ondelete="SET NULL"),
            unique=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("crew_id", "key"),
    )
    op.create_index("ix_crew_agents_crew_id", "crew_agents", ["crew_id"])
    # SQLite additive columns deliberately avoid a table rebuild while leases may exist.
    if op.get_bind().dialect.name == "sqlite":
        op.execute(
            "ALTER TABLE task_runs ADD COLUMN crew_id VARCHAR(36) "
            "REFERENCES crew_instances(id) ON DELETE CASCADE"
        )
    else:
        op.add_column(
            "task_runs",
            sa.Column(
                "crew_id",
                sa.String(36),
                sa.ForeignKey("crew_instances.id", ondelete="CASCADE"),
                nullable=True,
            ),
        )
    op.add_column("task_runs", sa.Column("crew_generation", sa.Integer(), nullable=True))
    op.create_index("ix_task_runs_crew_id", "task_runs", ["crew_id"])
    connection = op.get_bind()
    agents = sa.table(
        "crew_agents",
        *[
            sa.column(n)
            for n in (
                "id",
                "crew_id",
                "key",
                "name",
                "role",
                "instructions",
                "route_id",
                "definition",
                "execution",
                "status",
                "worker_id",
                "created_at",
            )
        ],
    )
    for row in connection.execute(
        sa.text(
            "SELECT id, name, responsibilities, created_at FROM crew_instances WHERE hidden = 0"
        )
    ).mappings():
        # Frozen data shape. No worker creation, requeue or scheduler changes during migration.
        connection.execute(
            agents.insert().values(
                id=str(uuid5(NAMESPACE_URL, "mavo:crew-agent:" + row["id"])),
                crew_id=row["id"],
                key="worker",
                name="Worker",
                role="Specialist",
                instructions=row["responsibilities"],
                route_id=None,
                definition="{}",
                execution="{}",
                status="active",
                worker_id=row["id"],
                created_at=row["created_at"],
            )
        )


def downgrade():
    connection = op.get_bind()
    # Refuse a lossy downgrade once V2 membership or executions are in use.
    count = connection.scalar(
        sa.text("SELECT COUNT(*) FROM crew_agents WHERE worker_id IS NULL OR worker_id != crew_id")
    )
    if count or connection.scalar(
        sa.text("SELECT COUNT(*) FROM task_runs WHERE crew_id IS NOT NULL")
    ):
        raise RuntimeError("Export V2 Crew configurations and executions before downgrading")
    op.drop_index("ix_task_runs_crew_id", "task_runs")
    op.drop_column("task_runs", "crew_generation")
    op.drop_column("task_runs", "crew_id")
    op.drop_table("crew_agents")
    op.drop_column("messages", "mentioned_agent_ids")
