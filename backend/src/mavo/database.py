from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    false,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    title: Mapped[str] = mapped_column(String(160), default="New conversation")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="Message.created_at"
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(String(160), nullable=True)
    provider: Mapped[str] = mapped_column(String(32), default="ollama")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class ProviderConfig(Base):
    __tablename__ = "provider_configs"

    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    default_model: Mapped[str] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ModelRoute(Base):
    __tablename__ = "model_routes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(String(80), unique=True)
    provider: Mapped[str] = mapped_column(String(32), index=True)
    model: Mapped[str] = mapped_column(String(160))
    credential_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CrewInstance(Base):
    __tablename__ = "crew_instances"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    template_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    responsibilities: Mapped[str] = mapped_column(Text)
    configuration_plan: Mapped[dict] = mapped_column(JSON, default=dict)

    @property
    def question(self):
        return (self.observation or {}).get("question")

    @property
    def fallback(self):
        """The last run a local model could not do, and the cloud route that did it instead."""
        return (self.observation or {}).get("fallback")

    @property
    def fallback_route_id(self):
        return (self.configuration_plan or {}).get("fallback_route_id")

    @property
    def skipped_run(self):
        """A once-a-day run skipped because Mavo was not running at its time (see crew_workers)."""
        return (self.observation or {}).get("skipped_run")

    @property
    def limit(self):
        """An AI-call limit that stopped its last run: which, what it is, what would do."""
        return (self.observation or {}).get("limit")

    @property
    def held(self):
        """Work handed to this Crew that waits for the person's OK (see pipelines.py)."""
        held = (self.observation or {}).get("held")
        if not held:
            return None
        return {
            k: held.get(k) for k in ("kind", "from", "from_id", "at", "result", "files", "review")
        }

    autonomy_mode: Mapped[str] = mapped_column(String(16), default="reactive")
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(160))
    route_id: Mapped[str | None] = mapped_column(
        ForeignKey("model_routes.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    max_model_calls: Mapped[int] = mapped_column(Integer, default=5)
    schedule_time: Mapped[str | None] = mapped_column(String(5), nullable=True)
    daily_triggers: Mapped[list[dict]] = mapped_column(JSON, default=list)
    reminder_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    resources: Mapped[list[str]] = mapped_column(JSON, default=list)
    permissions: Mapped[list[str]] = mapped_column(JSON, default=list)
    triggers: Mapped[list[str]] = mapped_column(JSON, default=list)
    is_running: Mapped[bool] = mapped_column(default=False, index=True)
    # A one-time agent (or the owner of one-time task alerts): it works like any Crew but is not
    # listed, offered for delegation or counted as one of the person's own.
    hidden: Mapped[bool] = mapped_column(default=False, server_default=false())
    runtime_state: Mapped[str] = mapped_column(String(32), default="stopped")
    review_interval_minutes: Mapped[int] = mapped_column(Integer, default=30)
    daily_call_limit: Mapped[int] = mapped_column(Integer, default=12)
    daily_calls_used: Mapped[int] = mapped_column(Integer, default=0)
    budget_day: Mapped[str | None] = mapped_column(String(10), nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    runtime_generation: Mapped[int] = mapped_column(Integer, default=0)
    last_runtime_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    checkpoint: Mapped[str | None] = mapped_column(Text, nullable=True)
    observation: Mapped[dict] = mapped_column(JSON, default=dict)
    trigger_type: Mapped[str] = mapped_column(String(32), default="interval")
    mail_sender: Mapped[str | None] = mapped_column(String(160), nullable=True)
    mail_subject: Mapped[str | None] = mapped_column(String(160), nullable=True)
    event_cursor: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReminderEvent(Base):
    __tablename__ = "reminder_events"
    __table_args__ = (UniqueConstraint("deduplication_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    kind: Mapped[str] = mapped_column(String(20), default="reminder")
    crew_id: Mapped[str] = mapped_column(
        ForeignKey("crew_instances.id", ondelete="CASCADE"), index=True
    )
    crew_name: Mapped[str] = mapped_column(String(80))
    message: Mapped[str] = mapped_column(Text)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    local_date: Mapped[str] = mapped_column(String(10))
    deduplication_key: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # A one-time task's alert: the run, and the step whose question it shows (answered there).
    task_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    task_step_id: Mapped[str | None] = mapped_column(String(40), nullable=True)


class CrewRun(Base):
    __tablename__ = "crew_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    crew_id: Mapped[str] = mapped_column(
        ForeignKey("crew_instances.id", ondelete="CASCADE"), index=True
    )
    crew_name: Mapped[str] = mapped_column(String(80))
    route_name: Mapped[str] = mapped_column(String(80))
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(160))
    prompt: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="running")
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_calls: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    kind: Mapped[str] = mapped_column(String(16), default="manual")
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    runtime_generation: Mapped[int] = mapped_column(Integer, default=0)


class ActionRequest(Base):
    __tablename__ = "action_requests"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    crew_id: Mapped[str | None] = mapped_column(
        ForeignKey("crew_instances.id", ondelete="SET NULL")
    )
    source: Mapped[str] = mapped_column(String(16), default="user")
    crew_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    generation: Mapped[int | None] = mapped_column(Integer, nullable=True)
    account_subject: Mapped[str] = mapped_column(String(255))
    target_summary: Mapped[str | None] = mapped_column(String(500), nullable=True)
    policy_revision: Mapped[str | None] = mapped_column(String(36), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(16), nullable=True)
    operation: Mapped[str] = mapped_column(String(40))
    arguments: Mapped[dict] = mapped_column(JSON)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TaskRun(Base):
    """One run of a one-time task (see task_plan.py): the plan, each step's progress and answer,
    and the result that was reported."""

    __tablename__ = "task_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    title: Mapped[str] = mapped_column(String(80))
    request: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="running", index=True)
    plan: Mapped[dict] = mapped_column(JSON)
    steps: Mapped[list] = mapped_column(JSON, default=list)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    budget_calls: Mapped[int] = mapped_column(Integer, default=12)
    used_calls: Mapped[int] = mapped_column(Integer, default=0)
    conversation_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkspaceSetting(Base):
    __tablename__ = "workspace_settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)


class CoordinatorSession(Base):
    __tablename__ = "coordinator_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=True, unique=True, index=True
    )
    crew_id: Mapped[str | None] = mapped_column(
        ForeignKey("crew_instances.id", ondelete="SET NULL"), nullable=True
    )
    messages: Mapped[list] = mapped_column(JSON, default=list)
    crew_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    active_request: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CoordinatorTurn(Base):
    __tablename__ = "coordinator_turns"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("coordinator_sessions.id", ondelete="CASCADE")
    )
    fingerprint: Mapped[str] = mapped_column(String(64))
    request_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GoogleAccount(Base):
    __tablename__ = "google_accounts"
    id: Mapped[str] = mapped_column(String(16), primary_key=True, default="google")
    subject: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(320))
    name: Mapped[str] = mapped_column(String(160))
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    cursors: Mapped[dict] = mapped_column(JSON, default=dict)
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ResourceEvent(Base):
    __tablename__ = "resource_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_subject: Mapped[str] = mapped_column(String(255), index=True)
    resource: Mapped[str] = mapped_column(String(32), index=True)
    deduplication_key: Mapped[str] = mapped_column(String(300), unique=True)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CrewProposal(Base):
    __tablename__ = "crew_proposals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    config: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="proposed")
    created_crew_id: Mapped[str | None] = mapped_column(
        ForeignKey("crew_instances.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


def build_session_factory(database_url: str) -> sessionmaker[Session]:
    connect_args = (
        {"check_same_thread": False, "timeout": 10} if database_url.startswith("sqlite") else {}
    )
    engine = create_engine(database_url, connect_args=connect_args, pool_pre_ping=True)

    if database_url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def configure_sqlite(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=10000")
            cursor.close()

    return sessionmaker(bind=engine, expire_on_commit=False)


def ensure_data_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def session_dependency(factory: sessionmaker[Session]) -> Generator[Session, None, None]:
    with factory() as session:
        yield session
