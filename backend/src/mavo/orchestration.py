"""Durable communication around the existing Crew and TaskRun execution authorities.

No model can call the mutation API: grants and configuration decisions are authenticated
user operations. Tool output and handoffs are data, never policy or system instructions.
"""

from __future__ import annotations

from datetime import UTC, timedelta
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select, update
from sqlalchemy.dialects.sqlite import insert

from mavo.database import (
    ActionRequest,
    AgentDelivery,
    AgentEnvelope,
    Conversation,
    ConversationEvent,
    CrewInstance,
    CrewRun,
    TaskRun,
    utcnow,
)

MessageKind = Literal[
    "task_assignment",
    "progress",
    "result",
    "review_request",
    "revision_request",
    "question",
    "blocked",
    "resource_request",
    "approval_request",
    "completion",
    "failure",
]


class WorkMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=1, max_length=180)
    kind: MessageKind
    sender: str = Field(min_length=1, max_length=160)
    recipients: list[str] = Field(min_length=1, max_length=40)
    task_id: str | None = Field(default=None, max_length=36)
    run_id: str | None = Field(default=None, max_length=36)
    summary: str = Field(max_length=6000)
    artifacts: list[str] = Field(default_factory=list, max_length=12)
    handoff_role: Literal["result", "report", "task", "revision", "person"] = "result"
    scopes: list[str] = Field(default_factory=list, max_length=100)


class MissingCapability(BaseModel):
    model_config = ConfigDict(extra="forbid")
    capability: str = Field(max_length=100)
    operation: str | None = Field(default=None, max_length=100)
    scope: dict[str, list[str]] = Field(default_factory=dict)
    risk: Literal["read", "change", "sensitive"] = "read"
    duration: Literal["crew"] = "crew"
    setup: Literal["grant", "resources", "provider", "actions"] = "grant"
    explanation: str = Field(max_length=500)


def event(
    session,
    key,
    kind,
    sender,
    payload,
    *,
    conversation_id=None,
    crew_id=None,
    task_id=None,
    step_id=None,
    pending=False,
    private=False,
):
    """Insert once in the caller's transaction. A duplicate cannot reopen a resolved request."""
    session.execute(
        insert(ConversationEvent)
        .values(
            id=str(uuid4()),
            source_key=key,
            kind=kind,
            sender=sender,
            payload=jsonable_encoder(payload),
            conversation_id=conversation_id,
            crew_id=crew_id,
            task_id=task_id,
            step_id=step_id,
            status="pending" if pending else "recorded",
            private=private,
            created_at=utcnow(),
            revision=0,
        )
        .on_conflict_do_nothing(index_elements=["source_key"])
    )
    return session.scalar(select(ConversationEvent).where(ConversationEvent.source_key == key))


def _crew(session, actor):
    if actor.startswith("agent:"):
        from mavo.database import CrewAgent

        member = session.get(CrewAgent, actor[6:])
        return (
            session.get(CrewInstance, member.crew_id)
            if member and member.status == "active"
            else None
        )
    return session.get(CrewInstance, actor[5:]) if actor.startswith("crew:") else None


def actor_permissions(session, actor, crew):
    if actor.startswith("agent:"):
        from mavo.crew_composition import effective_permissions
        from mavo.database import CrewAgent

        return effective_permissions(session, session.get(CrewAgent, actor[6:]))
    return set(crew.permissions)


def receive_permissions(session, actor, crew, sender):
    """What `actor` may be handed. Between two agents of ONE crew, what the crew itself may still
    read also counts: a triage agent that reads mail may pass what it found to the crew's alert
    agent, which never reads mail itself. (That hand-over used to fail, so any crew whose first
    agent read mail could not get past it.) Across crews nothing changes: the receiver needs
    the access itself. A crew losing that access stops waiting messages, as before."""
    allowed = actor_permissions(session, actor, crew)
    if actor.startswith("agent:") and sender.startswith("agent:"):
        from mavo.database import CrewAgent

        mine, theirs = session.get(CrewAgent, actor[6:]), session.get(CrewAgent, sender[6:])
        if mine and theirs and mine.crew_id == theirs.crew_id and mine.status == "active":
            parent = session.get(CrewInstance, mine.crew_id)
            if parent:
                allowed |= {p for p in parent.permissions if p.endswith((":read", ":body"))}
    return allowed


def validate_actor(session, actor):
    if actor in {"core", "user"}:
        return
    if actor.startswith(("crew:", "agent:")) and _crew(session, actor):
        return
    if actor.startswith("task:"):
        parts = actor.split(":", 2)
        run = session.get(TaskRun, parts[1]) if len(parts) == 3 else None
        if run and any(s.get("id") == parts[2] for s in (run.plan or {}).get("steps", [])):
            return
    raise ValueError("Agent reference does not exist")


def publish(session, message: WorkMessage) -> AgentEnvelope:
    """Atomic fan-out with immutable IDs and a per-recipient acknowledgement.

    Sensitive/private Crew never implicitly share their output. Other results carry the
    sender's read provenance and require the same grants at every Crew recipient. This is
    deliberately conservative; a linked Crew is not an authorization shortcut.
    """
    validate_actor(session, message.sender)
    sender = _crew(session, message.sender)
    if message.sender.startswith("crew:") and sender is None:
        raise ValueError("Message sender no longer exists")
    scopes = set(message.scopes)
    if sender:
        if sender.template_id in {"health", "wellbeing"}:
            own_run = session.get(TaskRun, message.task_id) if message.task_id else None
            if (
                not own_run
                or own_run.crew_id != sender.id
                or any(
                    actor not in {"user"}
                    and (not _crew(session, actor) or _crew(session, actor).id != sender.id)
                    for actor in message.recipients
                )
            ):
                raise PermissionError("Private Crew results require separately authorized sharing")
        scopes |= {
            p
            for p in actor_permissions(session, message.sender, sender)
            if p.endswith((":read", ":body")) and p != "weather:read" and not p.startswith("feeds:")
        }
    recipients = sorted(set(message.recipients))
    for actor in recipients:
        validate_actor(session, actor)
        if actor.startswith("task:") and message.sender.startswith("task:"):
            if actor.split(":")[1] != message.sender.split(":")[1]:
                raise PermissionError("Task-scoped artifacts cannot cross task boundaries")
        recipient = _crew(session, actor)
        if actor.startswith("crew:") and recipient is None:
            raise ValueError("Message recipient no longer exists")
        if recipient and not scopes <= receive_permissions(
            session, actor, recipient, message.sender
        ):
            raise PermissionError("Recipient does not have the source information permissions")
        if actor not in {"core", "user"} and recipient is None and not actor.startswith("task:"):
            raise ValueError("Unknown actor reference")
    payload = {
        "summary": message.summary,
        "artifacts": [p[:500] for p in message.artifacts],
        "handoff_role": message.handoff_role,
    }
    session.execute(
        insert(AgentEnvelope)
        .values(
            id=str(uuid4()),
            idempotency_key=message.key,
            kind=message.kind,
            sender=message.sender,
            task_id=message.task_id,
            run_id=message.run_id,
            payload=payload,
            scopes=sorted(scopes),
            created_at=utcnow(),
        )
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
    )
    envelope = session.scalar(
        select(AgentEnvelope).where(AgentEnvelope.idempotency_key == message.key)
    )
    if (
        envelope.sender != message.sender
        or envelope.payload != payload
        or envelope.kind != message.kind
        or envelope.scopes != sorted(scopes)
        or envelope.task_id != message.task_id
        or envelope.run_id != message.run_id
    ):
        raise ValueError("Idempotency key was reused for a different message")
    existing = set(
        session.scalars(
            select(AgentDelivery.recipient).where(AgentDelivery.message_id == envelope.id)
        )
    )
    if existing and existing != set(recipients):
        raise ValueError("Idempotency key was reused for different recipients")
    for recipient in recipients:
        session.execute(
            insert(AgentDelivery)
            .values(
                id=str(uuid4()),
                message_id=envelope.id,
                recipient=recipient,
                status="pending",
            )
            .on_conflict_do_nothing(index_elements=["message_id", "recipient"])
        )
    return envelope


def claim_messages(session, recipient: str, owner: str, limit=20, *, task_id=None) -> list[dict]:
    """Lease a bounded inbox; pending messages beyond this page remain durable."""
    now = utcnow()
    deliveries = session.scalars(
        select(AgentDelivery)
        .join(AgentEnvelope)
        .where(
            AgentDelivery.recipient == recipient,
            *([AgentEnvelope.task_id == task_id] if task_id else []),
            or_(
                AgentDelivery.status == "pending",
                (AgentDelivery.status == "claimed") & (AgentDelivery.lease_until <= now),
            ),
        )
        .order_by(AgentEnvelope.created_at, AgentEnvelope.id)
        .limit(limit)
    ).all()
    notes = []
    for delivery in deliveries:
        envelope = session.get(AgentEnvelope, delivery.message_id)
        crew = _crew(session, recipient)
        source = _crew(session, envelope.sender)
        if (
            envelope.scopes
            and envelope.sender.startswith(("crew:", "agent:"))
            and (
                not source
                or not set(envelope.scopes) <= actor_permissions(session, envelope.sender, source)
            )
        ):
            delivery.status = "revoked"
            continue
        # Revocation after delivery must also stop information entering the next prompt.
        if crew and not set(envelope.scopes) <= receive_permissions(
            session, recipient, crew, envelope.sender
        ):
            delivery.status = "revoked"
            continue
        changed = session.execute(
            update(AgentDelivery)
            .where(
                AgentDelivery.id == delivery.id,
                or_(
                    AgentDelivery.status == "pending",
                    (AgentDelivery.status == "claimed") & (AgentDelivery.lease_until <= now),
                ),
            )
            .values(status="claimed", lease_owner=owner, lease_until=now + timedelta(minutes=10))
            .execution_options(synchronize_session="fetch")
        )
        if changed.rowcount:
            source = _crew(session, envelope.sender)
            notes.append(
                {
                    "message_id": envelope.id,
                    "from_id": envelope.sender.removeprefix("crew:"),
                    "from": source.name if source else envelope.sender,
                    "at": envelope.created_at.replace(tzinfo=UTC).isoformat(),
                    "kind": envelope.payload.get("handoff_role")
                    or {
                        "revision_request": "revision",
                        "result": "result",
                        "task_assignment": "task",
                    }.get(envelope.kind, envelope.kind),
                    "files": envelope.payload.get("artifacts", []),
                    "result": envelope.payload["summary"],
                }
            )
    return notes


def finish_messages(session, owner: str, *, consumed: bool):
    session.execute(
        update(AgentDelivery)
        .where(
            AgentDelivery.lease_owner == owner,
            AgentDelivery.status == "claimed",
        )
        .values(
            status="consumed" if consumed else "pending",
            lease_owner=None,
            lease_until=None,
            consumed_at=utcnow() if consumed else None,
        )
    )


def charge_parent_task(session, crew, calls):
    from mavo.limits import CYCLE_MAX, RUN_MAX, day_limit

    task_id = (crew.configuration_plan or {}).get("task_run_id")
    if not task_id or not calls:
        return True
    from mavo.crew_composition import execution_agent

    agent = execution_agent(session, crew.id)
    if agent and agent.crew_id != crew.id:
        from mavo.database import CrewAgent

        parent = session.get(CrewInstance, agent.crew_id)
        day = utcnow().astimezone().date().isoformat()
        if parent.budget_day != day:
            parent.budget_day, parent.daily_calls_used = day, 0
            session.flush()
        try:
            with session.begin_nested():
                if agent.budget_run_id != task_id:
                    agent.budget_run_id, agent.calls_used = task_id, 0
                    session.flush()
                if not session.execute(
                    update(CrewAgent)
                    .where(
                        CrewAgent.id == agent.id,
                        CrewAgent.calls_used + calls <= RUN_MAX,
                    )
                    .values(calls_used=CrewAgent.calls_used + calls)
                ).rowcount:
                    raise ValueError("Agent cycle budget exhausted")
                if not session.execute(
                    update(CrewInstance)
                    .where(
                        CrewInstance.id == parent.id,
                        CrewInstance.daily_calls_used + calls <= day_limit(parent),
                    )
                    .values(daily_calls_used=CrewInstance.daily_calls_used + calls)
                ).rowcount:
                    raise ValueError("Crew daily budget exhausted")
                if not session.execute(
                    update(TaskRun)
                    .where(
                        TaskRun.id == task_id,
                        TaskRun.status == "running",
                        or_(
                            TaskRun.crew_generation.is_(None),
                            TaskRun.crew_generation == parent.runtime_generation,
                        ),
                        TaskRun.used_calls + calls <= CYCLE_MAX,
                    )
                    .values(used_calls=TaskRun.used_calls + calls)
                ).rowcount:
                    raise ValueError("Cycle budget exhausted")
            return True
        except ValueError:
            return False
    return (
        session.execute(
            update(TaskRun)
            .where(
                TaskRun.id == task_id,
                TaskRun.status == "running",
                TaskRun.used_calls + calls <= CYCLE_MAX,
            )
            .values(used_calls=TaskRun.used_calls + calls)
        ).rowcount
        == 1
    )


RESOURCE_GRANTS = {
    "device:read": "allow_device_metrics",
    "device:clean_temp": "allow_temp_cleanup",
    "weather:read": "allow_weather",
    "notifications:show": "allow_notifications",
}


def request_capability(
    session,
    crew,
    capability: MissingCapability,
    *,
    run_id=None,
    task_id=None,
    step_id=None,
    agent_id=None,
):
    """Only called at deterministic registry/policy checks, never from model prose."""
    if crew.hidden:
        from mavo.crew_composition import execution_agent

        member = execution_agent(session, crew.id)
        if member:
            task_id = (crew.configuration_plan or {}).get("task_run_id")
            step_id = (crew.configuration_plan or {}).get("task_step_id")
            agent_id = member.id
            crew = session.get(CrewInstance, member.crew_id)
    for pending in session.scalars(
        select(ConversationEvent).where(
            ConversationEvent.crew_id == crew.id,
            ConversationEvent.kind == "permission_request",
            ConversationEvent.status == "pending",
        )
    ):
        if pending.payload.get("capability", {}).get("capability") == capability.capability and (
            pending.payload.get("generation") != crew.runtime_generation
            or pending.payload.get("config_version") != crew.config_version
        ):
            pending.status, pending.resolved_at = "superseded", utcnow()
    return event(
        session,
        f"capability:{crew.id}:{run_id or crew.runtime_generation}:"
        f"{crew.config_version}:{capability.capability}",
        "permission_request",
        f"agent:{agent_id}" if agent_id else f"crew:{crew.id}",
        {
            "summary": capability.explanation,
            "capability": capability.model_dump(),
            "generation": crew.runtime_generation,
            "config_version": crew.config_version,
        },
        crew_id=crew.id,
        task_id=task_id,
        step_id=step_id,
        pending=True,
        private=crew.template_id in {"health", "wellbeing"},
    )


def worker_event(session, crew, run):
    if crew.hidden:
        return  # Temporary task work already has one consolidated TaskRun conversation surface.
    question = (crew.observation or {}).get("question")
    for pending in session.scalars(
        select(ConversationEvent).where(
            ConversationEvent.crew_id == crew.id,
            ConversationEvent.kind == "question",
            ConversationEvent.status == "pending",
        )
    ):
        if pending.payload.get("summary") != question or pending.payload.get("run_id") != run.id:
            pending.status, pending.resolved_at = "superseded", utcnow()
    kind = "question" if question else "failure" if run.status == "failed" else "completion"
    approval = (
        session.scalar(
            select(ActionRequest)
            .where(
                ActionRequest.crew_id == crew.id,
                ActionRequest.status == "pending",
            )
            .order_by(ActionRequest.created_at.desc())
            .limit(1)
        )
        if question
        else None
    )
    if approval:
        kind = "approval_request"
    if not question and not run.error and not run.result:
        return
    event(
        session,
        f"crew-run:{run.id}",
        kind,
        f"crew:{crew.id}",
        {
            "summary": (question or run.error or run.result or "")[:3000],
            "run_id": run.id,
            "name": crew.name,
            "action_request_id": approval.id if approval else None,
        },
        crew_id=crew.id,
        pending=bool(question),
        private=crew.template_id in {"health", "wellbeing"},
    )


class EventAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0)
    answer: str = Field(default="", max_length=4000)
    decision: Literal["answer", "grant", "decline"] = "answer"


def event_view(row, session=None):
    result = {
        key: getattr(row, key)
        for key in (
            "id",
            "seq",
            "kind",
            "sender",
            "conversation_id",
            "crew_id",
            "task_id",
            "step_id",
            "status",
            "revision",
            "payload",
            "created_at",
            "resolved_at",
        )
    }
    if session and row.kind == "configuration" and row.payload.get("edit"):
        from mavo.configuration import view
        from mavo.database import ConfigurationEdit

        edit = session.get(ConfigurationEdit, row.payload["edit"]["id"])
        if edit:
            result["payload"] = {**row.payload, "edit": view(edit)}
    if session and row.kind == "approval_request" and row.payload.get("action_request_id"):
        action = session.get(ActionRequest, row.payload["action_request_id"])
        if not action or action.status != "pending":
            result["status"] = "declined" if action and action.status == "rejected" else "resolved"
    return result


def build_orchestration_api(runtime):
    router = APIRouter()

    @router.get("/orchestration/events")
    async def events(conversation_id: str | None = None, after: int = 0, before: int | None = None):
        with runtime.factory() as session:
            if conversation_id and session.get(Conversation, conversation_id) is None:
                raise HTTPException(404, "Conversation not found")
            # Event references deliberately survive deletion for audit. They must not
            # remain actionable chat cards after their Crew/task has been removed.
            # Check at read time too, covering older deletions and concurrent finishes.
            query = select(ConversationEvent).where(
                ConversationEvent.private.is_(False),
                ConversationEvent.status != "cancelled",
                or_(
                    ConversationEvent.crew_id.is_(None),
                    select(CrewInstance.id)
                    .where(
                        CrewInstance.id == ConversationEvent.crew_id,
                        CrewInstance.hidden.is_(False),
                    )
                    .exists(),
                ),
                or_(
                    ConversationEvent.task_id.is_(None),
                    select(TaskRun.id).where(TaskRun.id == ConversationEvent.task_id).exists(),
                ),
            )
            if conversation_id:
                # A chat is a task context, not the workspace inbox. Scheduled
                # Crew events with no conversation belong in Updates/Activity.
                query = query.where(ConversationEvent.conversation_id == conversation_id)
            else:
                # What a question in an agent's room started is answered in that room.
                from mavo.crew_ask import ask_conversations

                rooms = ask_conversations(session)
                if rooms:
                    query = query.where(
                        or_(
                            ConversationEvent.conversation_id.is_(None),
                            ConversationEvent.conversation_id.not_in(rooms),
                        )
                    )
            if before is not None:
                query = query.where(ConversationEvent.seq < before)
            history = session.scalars(
                query.where(ConversationEvent.seq > after)
                .order_by(ConversationEvent.seq.desc())
                .limit(100)
            ).all()
            pending = session.scalars(
                query.where(ConversationEvent.status == "pending")
                .order_by(ConversationEvent.seq)
                .limit(500)
            ).all()
            rows = {row.id: row for row in [*history, *pending]}
            return [event_view(row, session) for row in sorted(rows.values(), key=lambda r: r.seq)]

    @router.get("/orchestration/messages")
    async def messages(crew_id: str | None = None):
        with runtime.factory() as session:
            visible_owner = or_(
                TaskRun.crew_id.is_(None),
                select(CrewInstance.id)
                .where(
                    CrewInstance.id == TaskRun.crew_id,
                    CrewInstance.hidden.is_(False),
                )
                .exists(),
            )
            query = (
                select(AgentEnvelope, AgentDelivery, TaskRun.status)
                .join(AgentDelivery)
                .outerjoin(TaskRun, TaskRun.id == AgentEnvelope.task_id)
                .where(
                    or_(
                        AgentEnvelope.task_id.is_(None),
                        select(TaskRun.id)
                        .where(
                            TaskRun.id == AgentEnvelope.task_id,
                            visible_owner,
                        )
                        .correlate(AgentEnvelope)
                        .exists(),
                    )
                )
            )
            if crew_id:
                from mavo.database import CrewAgent

                actors = [
                    f"crew:{crew_id}",
                    *[
                        f"agent:{a.id}"
                        for a in session.scalars(
                            select(CrewAgent).where(CrewAgent.crew_id == crew_id)
                        )
                    ],
                ]
                query = query.where(
                    or_(
                        AgentEnvelope.sender.in_(actors),
                        AgentDelivery.recipient.in_(actors),
                    )
                )
            rows = session.execute(query.order_by(AgentEnvelope.created_at.desc()).limit(100))
            return [
                {
                    "id": m.id,
                    "kind": m.kind,
                    "sender": m.sender,
                    "recipient": d.recipient,
                    "task_id": m.task_id,
                    "run_id": m.run_id,
                    "status": d.status,
                    "task_status": task_status,
                    "created_at": m.created_at,
                    "consumed_at": d.consumed_at,
                    "payload": m.payload,
                }
                for m, d, task_status in rows
            ]

    @router.post("/orchestration/events/{event_id}/answer")
    async def answer(event_id: str, payload: EventAnswer):
        with runtime.factory() as session:
            row = session.scalar(select(ConversationEvent).where(ConversationEvent.id == event_id))
            if row is None:
                raise HTTPException(404, "Request not found")
            if row.status != "pending" or row.revision != payload.revision:
                raise HTTPException(409, "This request has changed or is already resolved")
            crew = session.get(CrewInstance, row.crew_id) if row.crew_id else None
            if (row.crew_id and crew is None) or (
                row.task_id and session.get(TaskRun, row.task_id) is None
            ):
                raise HTTPException(409, "This Crew or task has been deleted; refresh the chat")
            if payload.decision == "grant":
                if row.kind != "permission_request" or crew is None:
                    raise HTTPException(409, "Not an active Crew permission request")
                cap = MissingCapability.model_validate(row.payload["capability"])
                if not cap.operation and row.payload.get("generation") != crew.runtime_generation:
                    raise HTTPException(
                        409, "Crew configuration changed; review its current access"
                    )
                if cap.operation:
                    from mavo.connectors.catalog import action_grant
                    from mavo.resource_pool import ceiling_error

                    if (
                        row.payload.get("config_version") != crew.config_version
                        or cap.operation not in runtime.actions.available(session, crew.id)
                        or cap.capability != action_grant(cap.operation)
                        or cap.capability not in crew.permissions
                        or not runtime.actions.rules.read(session, cap.operation, crew.id)[
                            "enabled"
                        ]
                    ):
                        raise HTTPException(409, "Save this Crew's scoped action policy first")
                    problem = ceiling_error(session, operation=cap.operation)
                    if problem:
                        raise HTTPException(409, problem)
                    # The existing action-policy endpoint remains the sole writer of tool rules.
                elif cap.setup != "grant" or cap.capability not in RESOURCE_GRANTS:
                    raise HTTPException(409, "Open the resource or action settings for this scope")
                from mavo.resource_pool import ceiling_error

                problem = ceiling_error(session, capability=cap.capability)
                if problem:
                    raise HTTPException(409, problem)
                if crew.status == "draft":
                    raise HTTPException(409, "Finish Crew setup first")
                if not cap.operation:
                    changed = session.execute(
                        update(CrewInstance)
                        .where(
                            CrewInstance.id == crew.id,
                            CrewInstance.config_version == crew.config_version,
                            CrewInstance.runtime_generation == crew.runtime_generation,
                        )
                        .values(
                            permissions=sorted(set(crew.permissions) | {cap.capability}),
                            config_version=crew.config_version + 1,
                        )
                    )
                    if not changed.rowcount:
                        raise HTTPException(
                            409, "Crew access changed concurrently; reload this request"
                        )
                # Granting a read capability never enables terminal commands or write actions.
                if crew.is_running:
                    crew.runtime_state, crew.next_run_at = "waiting", utcnow()
            elif payload.decision == "answer":
                if not payload.answer.strip():
                    raise HTTPException(422, "Enter an answer")
                if row.task_id:
                    if not runtime.task_runner.record_answer(session, row, payload.answer):
                        raise HTTPException(409, "The task is no longer waiting for this answer")
                elif crew and row.kind == "question":
                    latest = session.scalar(
                        select(CrewRun.id)
                        .where(CrewRun.crew_id == crew.id)
                        .order_by(CrewRun.created_at.desc())
                        .limit(1)
                    )
                    if not row.payload.get("held") and (
                        (crew.observation or {}).get("question") != row.payload["summary"]
                        or row.payload.get("run_id") != latest
                        or crew.runtime_state != "needs_input"
                    ):
                        raise HTTPException(409, "The Crew is no longer asking this question")
                    if row.payload.get("held"):
                        if (
                            not (crew.observation or {}).get("held")
                            or crew.question != row.payload["summary"]
                        ):
                            raise HTTPException(409, "This handoff question is no longer pending")
                        from mavo.pipelines import decide

                        decide(session, crew, True, payload.answer)
                    else:
                        runtime.planner.queue_reply(
                            session, crew, payload.answer, resolve_events=False
                        )
                else:
                    raise HTTPException(409, "This event is not a pending question")
            changed = session.execute(
                update(ConversationEvent)
                .where(
                    ConversationEvent.id == event_id,
                    ConversationEvent.status == "pending",
                    ConversationEvent.revision == payload.revision,
                )
                .values(
                    status="declined" if payload.decision == "decline" else "resolved",
                    revision=payload.revision + 1,
                    resolved_at=utcnow(),
                    payload={**row.payload, "answer": payload.answer, "decision": payload.decision},
                )
            )
            if not changed.rowcount:
                session.rollback()
                raise HTTPException(409, "Request was answered in another window")
            session.commit()
        if payload.decision == "answer" and row.task_id:
            runtime.task_runner.wake_answer(row.task_id, row.step_id, payload.answer)
        resumed = None
        if payload.decision == "grant" and row.task_id:
            # Retry keeps successful checkpoints and never reruns uncertain irreversible actions.
            resumed = await runtime.task_runner.retry(row.task_id)
        return {"resolved": True, "resumption": resumed}

    return router
