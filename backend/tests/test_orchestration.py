"""Deterministic orchestration checks. No external accounts, paid models or desktop actions."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from mavo.configuration import (
    ConfigurationPatch,
    ConfigurationProposal,
    apply_edit,
    classify_scope,
    propose,
)
from mavo.database import (
    AgentDelivery,
    AgentEnvelope,
    ConversationEvent,
    CrewInstance,
    CrewRun,
    TaskRun,
    utcnow,
)
from mavo.orchestration import (
    MissingCapability,
    WorkMessage,
    claim_messages,
    event,
    finish_messages,
    publish,
    request_capability,
)
from mavo.task_plan import validate_planning_graph
from tests.test_multi_agent import make_session
from tests.test_planner import planning  # noqa: F401


@pytest.fixture
def store(tmp_path):
    factory = make_session(tmp_path)
    with factory() as session:
        for key in ("a", "b", "c"):
            session.add(
                CrewInstance(
                    id=key,
                    name=key,
                    template_id="research",
                    responsibilities="Research facts",
                    provider="ollama",
                    model="test",
                    status="active",
                    trigger_type="interval",
                    review_interval_minutes=5,
                    is_running=True,
                    permissions=["model:invoke"],
                )
            )
        session.commit()
    yield factory
    factory.kw["bind"].dispose()


def message(key="m", recipients=None, **extra):
    return WorkMessage(
        key=key,
        kind="result",
        sender="crew:a",
        recipients=recipients or ["crew:b", "crew:c"],
        summary="Checked findings",
        **extra,
    )


def test_deleted_crew_requests_disappear_but_audit_and_other_updates_survive(client, auth):
    runtime = client.app.state.crew_runtime
    with runtime.factory() as session:
        crew = CrewInstance(
            id="deleted-news",
            name="Hourly News",
            template_id="research",
            responsibilities="Summarize news",
            provider="ollama",
            model="test",
            status="active",
            permissions=["model:invoke"],
        )
        session.add(crew)
        session.flush()
        session.add(TaskRun(id="deleted-task", crew_id=crew.id, title="News", plan={}))
        session.flush()
        for key, crew_id, task_id in (
            ("crew-blocked", crew.id, "deleted-task"),
            ("task-question", None, "deleted-task"),
            ("other-update", None, None),
        ):
            event(
                session,
                key,
                "blocked",
                "core",
                {"summary": key},
                crew_id=crew_id,
                task_id=task_id,
                pending=True,
            )
        session.commit()
    assert len(client.get("/api/v1/orchestration/events", headers=auth).json()) == 3
    assert client.delete("/api/v1/crew/deleted-news", headers=auth).status_code == 204
    rows = client.get("/api/v1/orchestration/events", headers=auth).json()
    assert [r["payload"]["summary"] for r in rows] == ["other-update"]
    with runtime.factory() as session:
        audit = session.scalars(
            select(ConversationEvent).where(
                ConversationEvent.source_key.in_(["crew-blocked", "task-question"])
            )
        ).all()
        assert len(audit) == 2
        assert all(r.status == "cancelled" and r.resolved_at and r.revision == 1 for r in audit)
        request_id = audit[0].id
    assert (
        client.post(
            f"/api/v1/orchestration/events/{request_id}/answer",
            headers=auth,
            json={"revision": 0, "decision": "decline"},
        ).status_code
        == 409
    )


@pytest.mark.parametrize("missing", ["crew", "task"])
def test_old_orphaned_requests_are_hidden_and_cannot_be_answered(client, auth, missing):
    runtime = client.app.state.crew_runtime
    with runtime.factory() as session:
        row = event(
            session,
            "old-orphan",
            "question",
            "core",
            {"summary": "Old question"},
            crew_id="gone" if missing == "crew" else None,
            task_id="gone" if missing == "task" else None,
            pending=True,
        )
        session.commit()
        request_id = row.id
    assert client.get("/api/v1/orchestration/events", headers=auth).json() == []
    assert (
        client.post(
            f"/api/v1/orchestration/events/{request_id}/answer",
            headers=auth,
            json={"revision": 0, "answer": "Yes"},
        ).status_code
        == 409
    )
    with runtime.factory() as session:
        assert session.scalar(select(ConversationEvent.id)) == request_id


def test_atomic_fanout_is_idempotent_with_independent_consumption(store):
    with store() as session:
        first = publish(session, message())
        assert publish(session, message()).id == first.id
        session.commit()
    with store() as session:
        assert len(session.scalars(select(AgentEnvelope)).all()) == 1
        assert len(session.scalars(select(AgentDelivery)).all()) == 2
        assert len(claim_messages(session, "crew:b", "run-b")) == 1
        finish_messages(session, "run-b", consumed=True)
        session.commit()
    with store() as session:
        assert claim_messages(session, "crew:b", "again") == []
        assert len(claim_messages(session, "crew:c", "run-c")) == 1


def test_concurrent_duplicate_publish_keeps_one_message(store):
    def send(_):
        with store() as session:
            result = publish(session, message()).id
            session.commit()
            return result

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert len(set(pool.map(send, range(8)))) == 1


def test_key_reuse_cannot_change_recipients_or_payload(store):
    with store() as session:
        publish(session, message())
        with pytest.raises(ValueError, match="recipients"):
            publish(session, message(recipients=["crew:b"]))


def test_delayed_delivery_survives_restart_and_has_a_recoverable_lease(store):
    with store() as session:
        publish(session, message())
        session.commit()
    with store() as session:
        assert claim_messages(session, "crew:b", "crashed")
        session.commit()
    with store() as session:
        assert not claim_messages(session, "crew:b", "other")
        delivery = session.scalar(select(AgentDelivery).where(AgentDelivery.recipient == "crew:b"))
        delivery.lease_until = utcnow() - timedelta(seconds=1)
        session.commit()
    with store() as session:
        assert claim_messages(session, "crew:b", "recovered")
        finish_messages(session, "crashed", consumed=True)
        assert (
            session.scalar(select(AgentDelivery).where(AgentDelivery.recipient == "crew:b")).status
            == "claimed"
        )
        finish_messages(session, "recovered", consumed=False)
        session.commit()
    with store() as session:
        assert claim_messages(session, "crew:b", "next")


def test_pending_messages_are_not_trimmed_when_inbox_exceeds_context_limit(store):
    with store() as session:
        for n in range(27):
            publish(session, message(str(n), ["crew:b"]))
        session.commit()
    with store() as session:
        first = claim_messages(session, "crew:b", "first")
        assert len(first) == 20
        finish_messages(session, "first", consumed=True)
        session.commit()
    with store() as session:
        second = claim_messages(session, "crew:b", "second")
        assert len(second) == 7
        assert not {n["message_id"] for n in first} & {n["message_id"] for n in second}


def test_handoff_does_not_expand_privileges_and_revocation_is_rechecked(store):
    with store() as session:
        session.get(CrewInstance, "a").permissions = ["google:gmail:read"]
        session.flush()
        with pytest.raises(PermissionError):
            publish(session, message())
        for key in ("b", "c"):
            session.get(CrewInstance, key).permissions = ["google:gmail:read"]
        publish(session, message())
        session.commit()
    with store() as session:
        session.get(CrewInstance, "b").permissions = []
        session.flush()
        assert not claim_messages(session, "crew:b", "denied")
        session.commit()
    with store() as session:
        assert (
            session.scalar(select(AgentDelivery).where(AgentDelivery.recipient == "crew:b")).status
            == "revoked"
        )


def test_private_specialist_never_implicitly_shares(store):
    with store() as session:
        session.get(CrewInstance, "a").template_id = "health"
        with pytest.raises(PermissionError, match="Private"):
            publish(session, message())


@pytest.mark.parametrize(
    "text,scope",
    [
        ("Make it 10 minutes for all future runs", "persistent"),
        ("Only this run, use the other model", "run"),
        ("For all future tasks prefer local models", "global"),
        ("Only this step", "step"),
        ("Make it 10 minutes", None),
        ("only this run and from now on", None),
    ],
)
def test_scope_classification_does_not_guess(text, scope):
    assert classify_scope(text) == scope


def change(interval=10):
    return ConfigurationProposal(
        crew_id="a",
        scope="persistent",
        changes=ConfigurationPatch(review_interval_minutes=interval),
    )


def test_persistent_interval_updates_canonical_config_scheduler_and_version(store):
    with store() as session:
        crew = session.get(CrewInstance, "a")
        crew.configuration_plan = {"draft": {"review_interval_minutes": 5}}
        edit = propose(session, change(), "edit")
        assert crew.review_interval_minutes == 5  # preview does not execute
        apply_edit(session, edit)
        session.commit()
        assert crew.review_interval_minutes == 10 and crew.config_version == 1
        assert crew.configuration_plan["draft"]["review_interval_minutes"] == 10
        assert 590 <= (crew.next_run_at - utcnow()).total_seconds() <= 601


def test_conflicting_changes_do_not_overwrite_newer_configuration(store):
    with store() as session:
        first = propose(session, change(10), "one")
        stale = propose(session, change(20), "two")
        apply_edit(session, first)
        with pytest.raises(ValueError, match="changed"):
            apply_edit(session, stale)


def test_active_work_defers_future_configuration_until_safe_boundary(store):
    from mavo.configuration import apply_pending

    with store() as session:
        run = CrewRun(
            id="r",
            crew_id="a",
            crew_name="a",
            route_name="r",
            provider="ollama",
            model="m",
            prompt="x",
            status="running",
        )
        session.add(run)
        session.flush()
        edit = propose(session, change(), "edit")
        apply_edit(session, edit)
        assert edit.status == "deferred"
        assert session.get(CrewInstance, "a").review_interval_minutes == 5
        run.status = "completed"
        session.flush()
        apply_pending(session, session.get(CrewInstance, "a"))
        assert session.get(CrewInstance, "a").review_interval_minutes == 10


def test_temporary_instructions_do_not_change_identity_or_schedule(store):
    from mavo.configuration import claim_overrides

    with store() as session:
        crew = session.get(CrewInstance, "a")
        edit = propose(
            session,
            ConfigurationProposal(
                crew_id="a",
                scope="run",
                changes=ConfigurationPatch(responsibilities="Only compare these two papers"),
            ),
            "once",
        )
        apply_edit(session, edit)
        run = CrewRun(
            id="r",
            crew_id="a",
            crew_name="a",
            route_name="r",
            provider="ollama",
            model="m",
            prompt=crew.responsibilities,
            status="running",
        )
        session.add(run)
        session.flush()
        claim_overrides(session, crew, run)
        assert run.prompt == "Only compare these two papers"
        assert crew.responsibilities == "Research facts" and crew.review_interval_minutes == 5
        assert edit.target_id == "r"


def test_permissions_are_not_a_configuration_field_and_intervals_cannot_be_run_scoped():
    with pytest.raises(ValidationError):
        ConfigurationPatch(permissions=["terminal:all"])
    with pytest.raises(ValidationError):
        ConfigurationProposal(
            crew_id="a", scope="run", changes=ConfigurationPatch(review_interval_minutes=10)
        )


def test_strict_planner_graph_rejects_cycles_missing_ids_and_unknown_tools():
    base = {
        "title": "Research",
        "deliver": "Compare",
        "steps": [
            {"id": "a", "kind": "helper", "instructions": "Research", "uses": ["b"]},
            {"id": "b", "kind": "helper", "instructions": "Compare", "uses": ["a"]},
        ],
    }
    with pytest.raises(ValueError, match="cycle"):
        validate_planning_graph(base)
    base["steps"][0]["uses"] = ["missing"]
    with pytest.raises(ValueError, match="invalid dependency"):
        validate_planning_graph(base)
    base["steps"][0]["uses"] = []
    base["steps"][0]["actions"] = ["execute.anything"]
    with pytest.raises(ValueError, match="unsupported"):
        validate_planning_graph(base)


def test_permission_request_is_typed_and_grant_is_user_only(client, auth):
    runtime = client.app.state.crew_runtime
    with runtime.factory() as session:
        crew = CrewInstance(
            id="monitor",
            name="Computer",
            template_id="computer",
            status="active",
            responsibilities="Read counters",
            provider="ollama",
            model="m",
            permissions=["model:invoke"],
            runtime_state="needs_input",
            is_running=True,
        )
        session.add(crew)
        session.flush()
        row = request_capability(
            session,
            crew,
            MissingCapability(
                capability="device:read", explanation="Read CPU, memory and storage counters"
            ),
        )
        session.commit()
        request_id = row.id
    body = {"revision": 0, "decision": "grant"}
    assert (
        client.post(f"/api/v1/orchestration/events/{request_id}/answer", json=body).status_code
        == 401
    )
    granted = client.post(
        f"/api/v1/orchestration/events/{request_id}/answer", headers=auth, json=body
    )
    assert granted.status_code == 200, granted.text
    assert (
        client.post(
            f"/api/v1/orchestration/events/{request_id}/answer", headers=auth, json=body
        ).status_code
        == 409
    )
    with runtime.factory() as session:
        crew = session.get(CrewInstance, "monitor")
        assert "device:read" in crew.permissions
        assert "action:terminal.run" not in crew.permissions
        assert crew.runtime_state == "waiting"


def test_two_pending_questions_require_explicit_step_and_survive_no_live_future(client, auth):
    runtime = client.app.state.crew_runtime
    with runtime.factory() as session:
        run = TaskRun(
            id="run",
            title="Questions",
            status="needs_you",
            plan={},
            steps=[{"id": "a", "status": "blocked"}, {"id": "b", "status": "blocked"}],
        )
        session.add(run)
        for step in ("a", "b"):
            event(
                session,
                step,
                "question",
                "core",
                {"summary": f"Which {step}?"},
                task_id="run",
                step_id=step,
                pending=True,
            )
        session.commit()
    assert not runtime.task_runner.answer("run", "ambiguous")
    assert runtime.task_runner.answer("run", "First answer", "a")
    with runtime.factory() as session:
        rows = session.scalars(
            select(ConversationEvent).order_by(ConversationEvent.source_key)
        ).all()
        assert rows[0].status == "resolved" and rows[1].status == "pending"
        assert session.get(TaskRun, "run").steps[0]["answer"] == "First answer"


def test_atomic_model_budget_cannot_be_overspent_by_parallel_steps(client, auth):
    runner = client.app.state.crew_runtime.task_runner
    from mavo.limits import CYCLE_MAX
    from mavo.tasks import OverBudget

    with runner.factory() as session:
        # Two calls left under Mavo's task ceiling.
        session.add(
            TaskRun(id="budget", title="Budget", plan={}, steps=[], used_calls=CYCLE_MAX - 2)
        )
        session.commit()

    def spend(_):
        try:
            runner.spend("budget", 1)
            return True
        except OverBudget:
            return False

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(spend, range(8))) == 2
    with runner.factory() as session:
        assert session.get(TaskRun, "budget").used_calls == CYCLE_MAX


def test_mentioned_agent_ids_are_validated_not_guessed_from_names(client, auth):
    from uuid import uuid4

    response = client.post(
        "/api/v1/core/turn",
        headers=auth,
        json={
            "message": "@Research do it",
            "request_id": str(uuid4()),
            "mentioned_crew_ids": ["does-not-exist"],
        },
    )
    assert response.status_code == 409


def test_step_override_is_consumed_once_at_a_safe_model_call_boundary(store):
    from mavo.configuration import override_block

    with store() as session:
        run = CrewRun(
            id="active",
            crew_id="a",
            crew_name="a",
            route_name="r",
            provider="ollama",
            model="m",
            prompt="base",
            status="running",
        )
        session.add(run)
        session.flush()
        edit = propose(
            session,
            ConfigurationProposal(
                crew_id="a",
                scope="step",
                changes=ConfigurationPatch(responsibilities="Compare price only"),
            ),
            "step-only",
        )
        apply_edit(session, edit)
        assert edit.target_id == "active"
        assert "Compare price only" in override_block(session, "a", "active")
        session.flush()
        assert override_block(session, "a", "active") == ""
        assert session.get(CrewInstance, "a").responsibilities == "Research facts"


def test_read_permission_denial_does_not_change_grants(client, auth):
    runtime = client.app.state.crew_runtime
    with runtime.factory() as session:
        crew = CrewInstance(
            id="denied",
            name="Denied monitor",
            template_id="computer",
            responsibilities="Counters",
            provider="ollama",
            model="m",
            status="active",
            permissions=["model:invoke"],
        )
        session.add(crew)
        session.flush()
        request = request_capability(
            session, crew, MissingCapability(capability="device:read", explanation="Read counters")
        )
        session.commit()
        request_id = request.id
    response = client.post(
        f"/api/v1/orchestration/events/{request_id}/answer",
        headers=auth,
        json={"revision": 0, "decision": "decline"},
    )
    assert response.status_code == 200
    with runtime.factory() as session:
        assert "device:read" not in session.get(CrewInstance, "denied").permissions


def test_retry_retains_committed_outputs_instead_of_replaying_completed_steps(client, auth):
    from tests.test_tasks import plan, setup, start, step, wait

    runtime = setup(client, auth)
    calls = {"a": 0, "b": 0}

    async def perform(run_id, plan, step, inputs):
        calls[step.id] += 1
        if step.id == "b" and calls["b"] == 1:
            raise RuntimeError("Temporary failure")
        return "Research" if step.id == "a" else inputs["a"] + " and draft"

    runtime.task_runner.perform = perform
    run_id = start(client, auth, plan(step("a"), step("b", uses=["a"]), final="b"))
    assert wait(client, auth, run_id)["status"] == "failed"
    assert client.post(f"/api/v1/tasks/{run_id}/retry", headers=auth).status_code == 201
    assert wait(client, auth, run_id)["result"] == "Research and draft"
    assert calls == {"a": 1, "b": 2}


def test_interrupted_tools_require_verified_checkpoint_and_are_not_replayed(client, auth):
    from tests.test_tasks import setup, wait

    runtime = setup(client, auth)
    from mavo.task_plan import TaskPlan

    plan = TaskPlan.model_validate(
        {
            "title": "Interrupted",
            "deliver": "Verified outcome",
            "steps": [
                {
                    "id": "read",
                    "kind": "agent",
                    "instructions": "Read the page",
                    "actions": ["browser.read"],
                    "sites": ["example.com"],
                }
            ],
        }
    )
    with runtime.factory() as session:
        session.add(
            TaskRun(
                id="uncertain",
                title="Interrupted",
                plan=plan.model_dump(mode="json"),
                steps=[{"id": "read", "title": "Read", "kind": "agent", "status": "running"}],
            )
        )
        session.commit()
    runtime.task_runner.recover()
    assert client.post("/api/v1/tasks/uncertain/retry", headers=auth).status_code == 409
    checkpoint = client.post(
        "/api/v1/tasks/uncertain/checkpoint",
        headers=auth,
        json={"step_id": "read", "verified_output": "I checked the result: three listings."},
    )
    assert checkpoint.status_code == 200, checkpoint.text
    assert client.post("/api/v1/tasks/uncertain/retry", headers=auth).status_code == 201
    done = wait(client, auth, "uncertain")
    assert done["status"] == "done" and done["used_calls"] == 0
    assert done["result"] == "I checked the result: three listings."


def test_existing_crews_collaborate_via_stable_mentions_and_one_consolidated_task(planning):
    from mavo.schemas import CoordinatorDecision
    from tests.test_planner import turn
    from tests.test_tasks import plan, scripted, setup, step, wait

    client, auth, runtime, coordinator = planning
    setup(client, auth)
    route_id = client.get("/api/v1/routes", headers=auth).json()[0]["id"]
    crews = [
        client.post(
            "/api/v1/crew",
            headers=auth,
            json={
                "template_id": kind,
                "name": name,
                "route_id": route_id,
                "responsibilities": name,
                "trigger_type": "manual",
            },
        ).json()
        for kind, name in [("research", "Research Crew"), ("communication", "Writing Crew")]
    ]
    ids = [crew["id"] for crew in crews]
    coordinator.decision = CoordinatorDecision.model_validate(
        {
            "reply": "Starting the task.",
            "task": plan(
                step("research", "crew", crew_id=ids[0]),
                step("write", "crew", crew_id=ids[1], uses=["research"]),
                final="write",
            ),
        }
    )
    seen = scripted(
        runtime,
        crew=[
            {
                "state": "wait_for_next_review",
                "summary": "Three internships compared.",
                "next_step": "",
            },
            {
                "state": "wait_for_next_review",
                "summary": "Three application drafts prepared.",
                "next_step": "",
            },
        ],
    )
    response = turn(planning, "@Research Crew @Writing Crew work together", mentioned_crew_ids=ids)
    run_id = response["task_run_id"]
    assert wait(client, auth, run_id)["result"] == "Three application drafts prepared."
    assert coordinator.calls[-1].context.addressed_crew_ids == ids
    assert "Three internships compared" in seen["crew_prompts"][1]
    assert len(client.get("/api/v1/crew", headers=auth).json()) == 2
    with runtime.factory() as session:
        delivery = session.execute(
            select(AgentEnvelope, AgentDelivery)
            .join(AgentDelivery)
            .where(
                AgentEnvelope.sender == f"task:{run_id}:research",
                AgentDelivery.recipient == f"task:{run_id}:write",
            )
        ).one()
        assert delivery[1].status == "consumed"


def test_migration_roundtrip_preserves_old_rows(tmp_path):
    import sqlite3
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    from mavo.database import Conversation

    store = make_session(tmp_path)
    with store() as session:
        session.add(Conversation(id="keep", title="Saved chat"))
        session.add(
            TaskRun(
                id="keep-task",
                title="Keep checkpoint",
                plan={},
                steps=[{"id": "a", "status": "done", "output": "Saved work"}],
            )
        )
        session.commit()
    url = str(store.kw["bind"].url)
    store.kw["bind"].dispose()
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.downgrade(config, "0017")
    command.upgrade(config, "head")
    with sqlite3.connect(tmp_path / "multi.db") as connection:
        assert connection.execute("SELECT title FROM conversations WHERE id='keep'").fetchone() == (
            "Saved chat",
        )
        assert (
            "Saved work"
            in connection.execute("SELECT steps FROM task_runs WHERE id='keep-task'").fetchone()[0]
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
