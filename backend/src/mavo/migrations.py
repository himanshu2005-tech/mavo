from __future__ import annotations

import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def run_migrations(database_url: str) -> None:
    backend_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    _repair_legacy_unversioned_database(config, database_url)
    command.upgrade(config, "head")


def _repair_legacy_unversioned_database(config: Config, database_url: str) -> None:
    """Stamp databases created before the Alembic transaction fix without changing their data."""
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            inspector = inspect(connection)
            tables = set(inspector.get_table_names())
            if "alembic_version" not in tables or not {"conversations", "messages"} <= tables:
                return
            has_revision = connection.execute(
                text("SELECT COUNT(*) FROM alembic_version")
            ).scalar_one()
            if has_revision:
                return
            message_columns = {column["name"] for column in inspector.get_columns("messages")}
            crew_columns = (
                {column["name"] for column in inspector.get_columns("crew_instances")}
                if "crew_instances" in tables
                else set()
            )
            if "coordinator_sessions" in tables:
                columns = {
                    column["name"] for column in inspector.get_columns("coordinator_sessions")
                }
                reminder_columns = (
                    {column["name"] for column in inspector.get_columns("reminder_events")}
                    if "reminder_events" in tables
                    else set()
                )
                revision = (
                    "0017"
                    if "task_run_id" in reminder_columns
                    else "0016"
                    if "task_runs" in tables
                    else "0015"
                    if "crew_ids" in columns
                    else "0014"
                    if "conversation_id" in columns
                    else "0012"
                )
            elif "action_requests" in tables:
                columns = {column["name"] for column in inspector.get_columns("action_requests")}
                revision = (
                    "0011"
                    if "policy_revision" in columns
                    else "0010"
                    if "target_summary" in columns
                    else "0009"
                )
            elif "google_accounts" in tables:
                revision = "0008"
            elif "is_running" in crew_columns:
                revision = "0007"
            elif "crew_runs" in tables:
                revision = "0006"
            elif "reminder_events" in tables:
                revision = "0005"
            elif "model_routes" in tables:
                revision = "0004"
            elif "crew_instances" in tables:
                revision = "0003"
            elif "provider_configs" in tables and "provider" in message_columns:
                revision = "0002"
            else:
                revision = "0001"
        command.stamp(config, revision)
    finally:
        engine.dispose()
