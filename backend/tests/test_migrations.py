from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

from mavo.migrations import run_migrations


def test_repairs_legacy_database_with_empty_revision(tmp_path) -> None:
    database_path = tmp_path / "legacy.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    backend_root = Path(__file__).resolve().parents[1]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "0001")

    with sqlite3.connect(database_path) as connection:
        connection.execute("DELETE FROM alembic_version")
        connection.commit()

    run_migrations(database_url)

    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
        message_columns = {row[1] for row in connection.execute("PRAGMA table_info(messages)")}

    assert "provider_configs" in tables
    assert "crew_instances" in tables
    assert "model_routes" in tables
    assert "reminder_events" in tables
    assert "provider" in message_columns
    assert "crew_runs" in tables
    assert revision == ("0017",)


def test_incomplete_legacy_setup_becomes_a_draft_without_losing_configuration(tmp_path):
    from mavo.database import CoordinatorSession, CrewInstance, CrewRun, build_session_factory

    url = f"sqlite:///{(tmp_path / 'draft-migration.db').as_posix()}"
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "0012")
    factory = build_session_factory(url)
    plan = {"phase": "needs_input", "questions": ["Which portal?"], "draft": {"name": "Portal"}}
    with factory() as session:
        from sqlalchemy import text as sql

        for crew_id, name in [("draft", "Portal"), ("used", "Existing Crew")]:
            session.execute(
                sql(
                    "INSERT INTO crew_instances (id,template_id,name,responsibilities,"
                    "configuration_plan,autonomy_mode,status,provider,model,max_model_calls,"
                    "resources,permissions,triggers,is_running,runtime_state,"
                    "review_interval_minutes,daily_call_limit,daily_calls_used,runtime_generation,"
                    "observation,trigger_type,event_cursor,created_at,updated_at) VALUES "
                    "(:id,'operator',:name,'Saved responsibility',:plan,'reactive','active',"
                    "'unassigned','',5,'[]','[\"model:invoke\"]','[]',0,'stopped',30,12,0,0,'{}',"
                    "'interval',0,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
                ),
                {"id": crew_id, "name": name, "plan": json.dumps(plan)},
            )
        session.flush()
        session.add(
            CrewRun(
                crew_id="used",
                crew_name="Existing Crew",
                route_name="Agent",
                provider="ollama",
                model="test",
                prompt="Earlier work",
                status="completed",
            )
        )
        from sqlalchemy import text

        session.execute(
            text(
                "INSERT INTO coordinator_sessions (id,crew_id,messages) "
                "VALUES ('main','draft','[]')"
            )
        )
        session.commit()
    run_migrations(url)
    with factory() as session:
        draft = session.get(CrewInstance, "draft")
        assert draft.status == "draft" and draft.configuration_plan == plan
        assert draft.responsibilities == "Saved responsibility" and not draft.is_running
        assert session.get(CrewInstance, "used").status == "active"
        assert session.get(CoordinatorSession, "main").crew_id == "draft"
    factory.kw["bind"].dispose()


def test_multiple_chat_migration_preserves_all_history_and_main_context(tmp_path):
    import json

    path = tmp_path / "multi-chat.db"
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    url = f"sqlite:///{path.as_posix()}"
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "0013")
    with sqlite3.connect(path) as connection:
        for chat_id in ("older", "existing-main"):
            connection.execute(
                "INSERT INTO conversations (id,title,created_at,updated_at) "
                "VALUES (?,?,datetime('now'),datetime('now'))",
                (chat_id, chat_id),
            )
            connection.execute(
                "INSERT INTO messages (id,conversation_id,role,content,provider,created_at) "
                "VALUES (?,?, 'user','Saved message','airouter',datetime('now'))",
                (chat_id, chat_id),
            )
        connection.execute(
            "INSERT INTO workspace_settings (key,value) VALUES ('core-conversation',?)",
            (json.dumps({"id": "existing-main"}),),
        )
        connection.execute(
            "INSERT INTO coordinator_sessions (id,messages) VALUES ('main',?)",
            (json.dumps([{"role": "user", "content": "Preserved context"}]),),
        )
    run_migrations(url)
    run_migrations(url)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM conversations").fetchone() == (2,)
        assert connection.execute("SELECT COUNT(*) FROM messages").fetchone() == (2,)
        rows = connection.execute(
            "SELECT id,conversation_id,messages FROM coordinator_sessions"
        ).fetchall()
        main = next(row for row in rows if row[0] == "main")
        assert main[1] == "existing-main" and "Preserved context" in main[2]
        assert {row[1] for row in rows} == {"older", "existing-main"}
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_repair_empty_revision_after_multiple_chat_upgrade_is_non_destructive(tmp_path):
    path = tmp_path / "empty-version.db"
    url = f"sqlite:///{path.as_posix()}"
    run_migrations(url)
    with sqlite3.connect(path) as connection:
        connection.execute("DELETE FROM alembic_version")
    run_migrations(url)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == ("0017",)
