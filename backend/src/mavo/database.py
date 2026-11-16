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
    mentioned_crew_ids: Mapped[list[str]] = mapped_column(JSON, default=list, server_default="[]")
    mentioned_agent_ids: Mapped[list[str]] = mapped_column(JSON, default=list, server_default="[]")
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

    agents: Mapped[list[CrewAgent]] = relationship(
        foreign_keys="CrewAgent.crew_id",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="CrewAgent.created_at",
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    template_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    responsibilities: Mapped[str] = mapped_column(Text)
    configuration_plan: Mapped[dict] = mapped_column(JSON, default=dict)
    config_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    @property
    def question(self):
        return (self.observation or {}).get("question")

    @property
    def hotkey(self):
        """Its keyboard shortcut ("Win+F9"), set when it was made or in its settings."""
        from mavo.hotkeys import hotkey_of

        return hotkey_of(self)

    @property
    def hotkey_problem(self):
        """Why its shortcut could not be registered (another app has it), if so."""
        return (self.observation or {}).get("hotkey_problem")

    @property
    def asks(self):
        """Its agent's questions still waiting for the person (desk.ask_person): each answered
        from its alert or from the agent's own window, by the alert's id."""
        asks = (self.observation or {}).get("desk_asks") or {}
        return [
            {"id": key, "question": a.get("question"), "choices": a.get("choices") or []}
            for key, a in asks.items()
            if isinstance(a, dict) and not a.get("answer")
        ]

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


class CrewAgent(Base):
    """Logical persistent member; its worker is an internal execution adapter, not a Crew."""

    __tablename__ = "crew_agents"
    __table_args__ = (UniqueConstraint("crew_id", "key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    crew_id: Mapped[str] = mapped_column(
        ForeignKey("crew_instances.id", ondelete="CASCADE"), index=True
    )
    key: Mapped[str] = mapped_column(String(24))
    name: Mapped[str] = mapped_column(String(80))
    role: Mapped[str] = mapped_column(String(80))
    instructions: Mapped[str] = mapped_column(Text, default="")
    route_id: Mapped[str | None] = mapped_column(
        ForeignKey("model_routes.id", ondelete="RESTRICT"), nullable=True
    )
    definition: Mapped[dict] = mapped_column(JSON, default=dict)
    execution: Mapped[dict] = mapped_column(JSON, default=dict)
    calls_used: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    budget_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")
    worker_id: Mapped[str | None] = mapped_column(
        ForeignKey("crew_instances.id", ondelete="SET NULL"), nullable=True, unique=True
    )
    worker: Mapped[CrewInstance | None] = relationship(foreign_keys=[worker_id], lazy="selectin")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    @property
    def runtime_state(self):
        if self.worker and self.worker.runtime_state == "working":
            return "working"
        if (
            self.worker
            and self.worker.is_running
            and (self.worker.observation or {}).get("question")
        ):
            return "needs_input"
        if self.worker and not self.worker.is_running and self.execution.get("status") == "running":
            return "stopped"
        return self.execution.get("status", self.worker.runtime_state if self.worker else "idle")

    @property
    def checkpoint(self):
        return self.worker.checkpoint if self.worker else None


class TaskRun(Base):
    """One run of a one-time task (see task_plan.py): the plan, each step's progress and answer,
    and the result that was reported."""

    __tablename__ = "task_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    crew_id: Mapped[str | None] = mapped_column(
        ForeignKey("crew_instances.id", ondelete="CASCADE"), nullable=True, index=True
    )
    crew_generation: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(String(80))
    request: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="running", index=True)
    plan: Mapped[dict] = mapped_column(JSON)
    steps: Mapped[list] = mapped_column(JSON, default=list)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    budget_calls: Mapped[int] = mapped_column(Integer, default=12)
    used_calls: Mapped[int] = mapped_column(Integer, default=0)
    revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    reserved_calls: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    conversation_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkspaceSetting(Base):
    __tablename__ = "workspace_settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)


class AgentEnvelope(Base):
    """Immutable, bounded work message; delivery state belongs to each recipient."""

    __tablename__ = "agent_envelopes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    idempotency_key: Mapped[str] = mapped_column(String(180), unique=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    sender: Mapped[str] = mapped_column(String(160), index=True)
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentDelivery(Base):
    __tablename__ = "agent_deliveries"
    __table_args__ = (UniqueConstraint("message_id", "recipient"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    message_id: Mapped[str] = mapped_column(
        ForeignKey("agent_envelopes.id", ondelete="CASCADE"), index=True
    )
    recipient: Mapped[str] = mapped_column(String(160), index=True)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ConversationEvent(Base):
    """Durable, correlated user-facing activity, separate from internal model chatter."""

    __tablename__ = "conversation_events"
    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(String(36), unique=True, default=lambda: str(uuid4()))
    source_key: Mapped[str] = mapped_column(String(180), unique=True)
    conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    crew_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    step_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    sender: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(24), default="recorded", index=True)
    revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    payload: Mapped[dict] = mapped_column(JSON)
    private: Mapped[bool] = mapped_column(default=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ConfigurationEdit(Base):
    __tablename__ = "configuration_edits"
    __table_args__ = (UniqueConstraint("crew_id", "version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    request_key: Mapped[str] = mapped_column(String(180), unique=True)
    crew_id: Mapped[str | None] = mapped_column(
        ForeignKey("crew_instances.id", ondelete="CASCADE"), nullable=True, index=True
    )
    version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    base_version: Mapped[int] = mapped_column(Integer, default=0)
    scope: Mapped[str] = mapped_column(String(24))
    target_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="proposed", index=True)
    before: Mapped[dict] = mapped_column(JSON)
    changes: Mapped[dict] = mapped_column(JSON)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
