"""Deterministic V2 tests. No external account, browser or paid model calls."""

import asyncio
import json
import re
from collections import Counter
from datetime import UTC
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from mavo.crew_composition import (
    AgentSpec,
    CrewComposition,
    default_agent,
    dispatch,
    effective_permissions,
    set_composition,
    validate_worker,
    worker_for,
)
from mavo.database import CrewInstance, ModelRoute, TaskRun, utcnow
from mavo.orchestration import WorkMessage, claim_messages, finish_messages, publish
from tests.test_multi_agent import make_session
from tests.test_planner import planning  # noqa: F401
from tests.test_tasks import setup, wait


def spec(key, **extra):
    return dict(
        key=key,
        name=key.title(),
        role=key.title(),
        instructions=f"Perform {key}",
        completion_criteria=f"Return a usable {key} deliverable",
        **extra,
    )


def composition(*agents, **extra):
    return CrewComposition(agents=[AgentSpec(**a) for a in agents], **extra)


def seed(session, **extra):
    route = ModelRoute(id=str(uuid4()), name="Local", provider="ollama", model="fixture")
    session.add(route)
    session.flush()
    crew = CrewInstance(
        name="Internships",
        template_id="research",
        responsibilities="Find suitable internships and prepare drafts",
        provider="ollama",
        model="fixture",
        route_id=route.id,
        is_running=True,
        permissions=["model:invoke"],
        trigger_type="interval",
        **extra,
    )
    session.add(crew)
    session.flush()
    return crew


@pytest.mark.parametrize(
    "agents",
    [
        [spec("a", depends_on=["missing"])],
        [spec("a", depends_on=["b"]), spec("b", depends_on=["a"])],
        [spec("a"), spec("a")],
        [spec("a", capabilities=["invented:permission"])],
        [spec("a", capabilities=["action:gmail.invented_operation"])],
    ],
)
def test_composition_rejects_bad_graph_and_capabilities(agents):
    with pytest.raises(ValidationError):
        composition(*agents)


def test_call_budget_is_a_ceiling_not_a_target_team_size():
    one = composition(spec("monitor"))
    assert len(one.agents) == 1
    # Agent call counts are history now: their sum over the cycle number is no reason to
    # refuse a design (limits.py caps the real work).
    two = composition(spec("one", max_model_calls=10), spec("two", max_model_calls=10))
    assert [a.max_model_calls for a in two.agents] == [10, 10]


def test_review_checkpoint_is_typed_without_making_legacy_verdicts_invalid():
    from mavo.crew_workers import parse_checkpoint

    checkpoint = {"state": "wait_for_next_review", "summary": "Reviewed supplied work"}
    assert parse_checkpoint(json.dumps(checkpoint)).review_verdict is None
    assert (
        parse_checkpoint(json.dumps({**checkpoint, "review_verdict": "fix"})).review_verdict
        == "fix"
    )
    with pytest.raises(ValidationError):
        parse_checkpoint(json.dumps({**checkpoint, "review_verdict": "grant permissions"}))


def test_legacy_identity_and_checkpoint_are_preserved(tmp_path):
    factory = make_session(tmp_path)
    with factory() as session:
        crew = seed(session, checkpoint="already committed work")
        default_agent(session, crew)
        default_agent(session, crew)
        session.commit()
        assert len(crew.agents) == 1
        assert crew.agents[0].worker_id == crew.id
        assert crew.checkpoint == "already committed work"
        assert session.query(TaskRun).count() == 0
    factory.kw["bind"].dispose()


def test_membership_edit_keeps_ids_and_retires_instead_of_erasing_history(tmp_path):
    factory = make_session(tmp_path)
    with factory() as session:
        crew = seed(session)
        set_composition(
            session, crew, composition(spec("find"), spec("review", depends_on=["find"]))
        )
        original = {a.key: a.id for a in crew.agents}
        set_composition(
            session, crew, composition(spec("find"), spec("write", depends_on=["find"]))
        )
        assert next(a for a in crew.agents if a.key == "find").id == original["find"]
        assert next(a for a in crew.agents if a.key == "review").status == "retired"
    factory.kw["bind"].dispose()


def test_agent_can_only_reduce_parent_permissions_and_revocation_is_immediate(tmp_path):
    factory = make_session(tmp_path)
    with factory() as session:
        crew = seed(session)
        crew.permissions += ["weather:read"]
        set_composition(session, crew, composition(spec("find", capabilities=["weather:read"])))
        agent = crew.agents[0]
        assert effective_permissions(session, agent) == {"model:invoke", "weather:read"}
        crew.permissions = ["model:invoke"]
        assert effective_permissions(session, agent) == {"model:invoke"}
        worker = worker_for(session, crew, agent)
        with pytest.raises(ValueError, match="assignment"):
            validate_worker(session, worker)
    factory.kw["bind"].dispose()


def test_agent_messages_have_durable_fanout_and_acknowledgement(tmp_path):
    factory = make_session(tmp_path)
    with factory() as session:
        crew = seed(session)
        set_composition(session, crew, composition(spec("find"), spec("match"), spec("write")))
        agents = crew.agents
        message = WorkMessage(
            key="fanout",
            kind="result",
            sender=f"agent:{agents[0].id}",
            recipients=[f"agent:{a.id}" for a in agents[1:]],
            summary="Bounded findings",
        )
        first = publish(session, message)
        assert publish(session, message).id == first.id
        session.commit()
        ids = [a.id for a in agents]
    with factory() as session:
        for i in ids[1:]:
            assert len(claim_messages(session, f"agent:{i}", i)) == 1
            finish_messages(session, i, consumed=True)
        session.commit()
    with factory() as session:
        assert not claim_messages(session, f"agent:{ids[1]}", "another-owner")
    factory.kw["bind"].dispose()


def test_three_real_worker_steps_use_one_crew_and_no_manager_call(planning):
    client, auth, _, _ = planning
    runtime = setup(client, auth)
    calls = []

    async def chat(provider, model, messages, system=None, credential_id=None):
        calls.append(system)
        return json.dumps(
            dict(
                state="wait_for_next_review",
                summary=f"Deliverable {len(calls)}",
                next_step="",
                notification=None,
                question=None,
                actions=[],
            )
        )

    runtime.router.chat = chat
    with runtime.factory() as session:
        crew = CrewInstance(
            name="Internship team",
            template_id="research",
            responsibilities="Find and match internships then prepare applications",
            route_id=session.scalar(select(ModelRoute.id)),
            provider="ollama",
            model="m",
            is_running=True,
            permissions=["model:invoke"],
            trigger_type="interval",
        )
        session.add(crew)
        session.flush()
        set_composition(
            session,
            crew,
            composition(
                spec("discover"),
                spec("match", depends_on=["discover"]),
                spec("prepare", depends_on=["match"]),
            ),
        )
        crew_id = crew.id
        session.commit()
    assert client.portal.call(dispatch, runtime, crew_id)
    with runtime.factory() as session:
        run_id = session.scalar(select(TaskRun.id).where(TaskRun.crew_id == crew_id))
    run = wait(client, auth, run_id)
    assert run["status"] == "done", run
    assert len(calls) == 3  # no separate manager inference
    with runtime.factory() as session:
        assert session.query(CrewInstance).filter(CrewInstance.hidden.is_(False)).count() == 1
        assert all(
            a.execution["status"] == "done" for a in session.get(CrewInstance, crew_id).agents
        )
        assert session.get(CrewInstance, crew_id).runtime_state == "waiting"
    crew = client.get("/api/v1/crew", headers=auth).json()[0]
    assert len(crew["agents"]) == 3
    traffic = client.get(f"/api/v1/orchestration/messages?crew_id={crew_id}", headers=auth).json()
    assert any(
        m["sender"].startswith("agent:") and m["recipient"].startswith("agent:") for m in traffic
    )


def test_source_verification_does_not_accept_a_homepage_or_fabricated_listings(tmp_path):
    from mavo.database import ActionRequest
    from mavo.outcomes import verify_sources

    factory = make_session(tmp_path)
    with factory() as session:
        crew = seed(session)
        crew.permissions = [*crew.permissions, "action:browser.read"]
        began = utcnow()
        session.add(
            ActionRequest(
                crew_id=crew.id,
                account_subject="local",
                operation="browser.read",
                source="crew",
                arguments={},
                fingerprint="fixture",
                status="completed",
                result={
                    "url": "https://example.com/jobs",
                    "text": "Software Internship — Python, remote, apply by 30 November",
                },
            )
        )
        session.flush()
        assert verify_sources(session, crew.id, began, [])["step"] == "unverified"
        claim = {
            "kind": "listing",
            "url": "https://example.com/jobs",
            "title": "Software Internship",
            "quote": "Software Internship — Python, remote, apply by 30 November",
        }
        result = verify_sources(session, crew.id, began, [claim])
        assert result["step"] == "evidence_checked" and result["objective"] == "requires_review"
        claim["quote"] = "A fabricated listing which was never observed on this website"
        assert verify_sources(session, crew.id, began, [claim])["step"] == "unverified"
    factory.kw["bind"].dispose()


def run_composed(planning, team, responses, *, pause=0, trace=None):
    client, auth, runtime, _ = planning
    setup(client, auth)
    seen = []

    async def chat(provider, model, messages, system=None, credential_id=None):
        seen.append(system)
        if trace is not None:
            trace["active"] = trace.get("active", 0) + 1
            trace["peak"] = max(trace.get("peak", 0), trace["active"])
        await asyncio.sleep(pause)
        if trace is not None:
            trace["active"] -= 1
        return json.dumps(dict(state="wait_for_next_review", summary=responses.pop(0), actions=[]))

    runtime.router.chat = chat
    with runtime.factory() as session:
        crew = seed(session)
        set_composition(session, crew, team)
        crew_id = crew.id
        session.commit()
    client.portal.call(dispatch, runtime, crew_id)
    with runtime.factory() as session:
        run_id = session.scalar(select(TaskRun.id).where(TaskRun.crew_id == crew_id))
    return client, auth, runtime, crew_id, run_id, seen


def test_review_fix_is_bounded_and_uses_durable_revision_messages(planning):
    team = composition(spec("draft"), spec("review", depends_on=["draft"], reviews="draft"))
    client, auth, runtime, crew_id, run_id, seen = run_composed(
        planning,
        team,
        [
            "First draft",
            "Correct the claims\nVERDICT: fix",
            "Revised draft",
            "Checked\nVERDICT: pass",
        ],
    )
    run = wait(client, auth, run_id)
    assert run["status"] == "done", run
    assert len(seen) == 4
    assert "Revised draft" in run["result"]
    messages = client.get(f"/api/v1/orchestration/messages?crew_id={crew_id}", headers=auth).json()
    assert any(m["kind"] == "revision_request" for m in messages)
    with runtime.factory() as session:
        assert all(a.calls_used == 2 for a in session.get(CrewInstance, crew_id).agents)


def test_missing_access_blocks_before_model_and_retry_rechecks_parent_grant(planning):
    client, auth, runtime, crew_id, run_id, seen = run_composed(
        planning, composition(spec("notify", capabilities=["notifications:show"])), ["Prepared"]
    )
    assert wait(client, auth, run_id)["status"] == "needs_you"
    assert not seen
    events = client.get("/api/v1/orchestration/events", headers=auth).json()
    request = next(e for e in events if e["kind"] == "permission_request")
    assert request["crew_id"] == crew_id and request["task_id"] == run_id
    assert request["sender"].startswith("agent:")
    # Explicit fixture authorization, not a model grant.
    with runtime.factory() as session:
        crew = session.get(CrewInstance, crew_id)
        crew.permissions = [*crew.permissions, "notifications:show"]
        session.commit()
    assert client.portal.call(runtime.task_runner.retry, run_id) == run_id
    assert wait(client, auth, run_id)["status"] == "done"
    assert len(seen) == 1


def test_restart_cleanup_retains_registered_agent_private_context(planning):
    client, auth, runtime, _ = planning
    setup(client, auth)
    with runtime.factory() as session:
        crew = seed(session)
        set_composition(session, crew, composition(spec("find")))
        worker = worker_for(session, crew, crew.agents[0])
        worker.observation = {"private_note": "keep my own progress"}
        worker_id = worker.id
        session.commit()
    runtime.task_runner.recover()
    with runtime.factory() as session:
        assert (
            session.get(CrewInstance, worker_id).observation["private_note"]
            == "keep my own progress"
        )


def test_persistent_composition_edits_are_versioned_and_run_selection_does_not_rewrite_membership(
    tmp_path,
):
    from mavo.configuration import ConfigurationProposal, apply_edit, propose

    factory = make_session(tmp_path)
    with factory() as session:
        crew = seed(session)
        team = composition(spec("find"), spec("write", depends_on=["find"]))
        set_composition(session, crew, team)
        before = crew.configuration_plan["composition"]
        temporary = propose(
            session,
            ConfigurationProposal(
                crew_id=crew.id, scope="run", changes={"run_agent_keys": ["find"]}
            ),
            "one-run",
        )
        apply_edit(session, temporary)
        assert crew.configuration_plan["composition"] == before
        update = propose(
            session,
            ConfigurationProposal(
                crew_id=crew.id,
                scope="persistent",
                changes={
                    "composition": composition(spec("find")).model_dump(),
                    "review_interval_minutes": 10,
                },
            ),
            "future",
        )
        apply_edit(session, update)
        assert crew.config_version == 1 and crew.review_interval_minutes == 10
        assert sum(a.status == "active" for a in crew.agents) == 1
        assert next(a for a in crew.agents if a.key == "write").status == "retired"
    factory.kw["bind"].dispose()


def test_core_addresses_a_stopped_crew_member_without_starting_its_schedule(planning):
    client, auth, runtime, _ = planning
    setup(client, auth)

    async def chat(*args, **kwargs):
        return json.dumps(
            {
                "state": "wait_for_next_review",
                "summary": "My last assignment is ready",
                "actions": [],
            }
        )

    runtime.router.chat = chat
    with runtime.factory() as session:
        crew = seed(session)
        crew.is_running = False
        set_composition(session, crew, composition(spec("discover")))
        crew_id, agent_id = crew.id, crew.agents[0].id
        session.commit()
    response = client.post(
        "/api/v1/tasks",
        headers=auth,
        json={
            "request": "Explain your last run",
            "plan": {
                "title": "Ask Discovery",
                "deliver": "An update",
                "steps": [
                    {
                        "id": "update",
                        "kind": "crew",
                        "crew_id": crew_id,
                        "agent_id": agent_id,
                        "title": "Discovery update",
                        "instructions": "Explain your last run",
                    }
                ],
            },
        },
    )
    assert response.status_code == 201, response.text
    result = wait(client, auth, response.json()["id"])
    assert result["status"] == "done", result
    with runtime.factory() as session:
        assert not session.get(CrewInstance, crew_id).is_running


def test_deleting_a_v2_crew_removes_internal_workers_but_preserves_routes(planning):
    client, auth, runtime, crew_id, run_id, _ = run_composed(
        planning, composition(spec("find")), ["Done"]
    )
    assert wait(client, auth, run_id)["status"] == "done"
    with runtime.factory() as session:
        worker_id = session.get(CrewInstance, crew_id).agents[0].worker_id
    response = client.delete(f"/api/v1/crew/{crew_id}", headers=auth)
    assert response.status_code == 204, response.text
    with runtime.factory() as session:
        assert session.get(CrewInstance, worker_id) is None
        assert session.scalar(select(ModelRoute.id))


def test_parallel_agents_fan_in_under_the_crew_concurrency_limit(planning):
    trace = {}
    client, auth, _, _, run_id, seen = run_composed(
        planning,
        composition(
            spec("research"), spec("compare"), spec("summarize", depends_on=["research", "compare"])
        ),
        ["Source A", "Source B", "Consolidated comparison"],
        pause=0.1,
        trace=trace,
    )
    result = wait(client, auth, run_id)
    assert result["status"] == "done", result
    assert trace["peak"] == 2 and len(seen) == 3


def test_eight_agent_diamond_graph_repairs_a_draft_without_replaying_discovery(planning):
    """Two sources fan in, flow through matching, and fix once before final delivery."""
    client, auth, runtime, _ = planning
    setup(client, auth)
    team = composition(
        spec("portal"),
        spec("inbox"),
        spec("deduplicate", depends_on=["portal", "inbox"]),
        spec("eligibility", depends_on=["deduplicate"]),
        spec("rank", depends_on=["eligibility"]),
        spec("draft", depends_on=["rank"]),
        spec("review", depends_on=["draft"], reviews="draft"),
        spec("deliver", depends_on=["draft", "review"]),
        limits={"model_calls": 24, "concurrency": 2, "review_rounds": 1},
    )
    counts = Counter()
    active, peak = 0, 0

    async def chat(provider, model, messages, system=None, credential_id=None):
        nonlocal active, peak
        key = re.search(r"\nRole: ([^\n]+)", system)[1].casefold()
        prompt = messages[0]["content"]
        counts[key] += 1
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.04)
        active -= 1
        if key == "deduplicate":
            assert "portal artifact" in prompt and "inbox artifact" in prompt
        if key == "review":
            output = "Fix the missing role link" if counts[key] == 1 else "Checked"
            assert "REVIEW OUTCOME" in system and '"review_verdict"' in system
        elif key == "draft":
            output = "REVISED_DRAFT with the role link" if counts[key] == 2 else "Initial draft"
        elif key == "deliver":
            assert "REVISED_DRAFT" in prompt
            output = "Verified final application package: REVISED_DRAFT"
        else:
            output = key + " artifact"
        checkpoint = {"state": "wait_for_next_review", "summary": output, "actions": []}
        if key == "review":
            checkpoint["review_verdict"] = "fix" if counts[key] == 1 else "pass"
        return json.dumps(checkpoint)

    runtime.router.chat = chat
    with runtime.factory() as session:
        crew = seed(session, daily_call_limit=30)
        set_composition(session, crew, team)
        crew_id = crew.id
        session.commit()
    client.portal.call(dispatch, runtime, crew_id)
    with runtime.factory() as session:
        run_id = session.scalar(select(TaskRun.id).where(TaskRun.crew_id == crew_id))
    result = wait(client, auth, run_id)
    assert result["status"] == "done", result
    assert "REVISED_DRAFT" in result["result"]
    assert peak == 2 and sum(counts.values()) == 10
    assert counts["draft"] == counts["review"] == 2
    assert all(
        counts[key] == 1
        for key in ("portal", "inbox", "deduplicate", "eligibility", "rank", "deliver")
    )
    messages = client.get(f"/api/v1/orchestration/messages?crew_id={crew_id}", headers=auth).json()
    assert sum(m["kind"] == "revision_request" for m in messages) == 1
    with runtime.factory() as session:
        assert session.query(TaskRun).count() == 1
        assert len(session.get(CrewInstance, crew_id).agents) == 8


def test_stop_fences_all_member_assignments(planning):
    client, auth, runtime, crew_id, run_id, _ = run_composed(
        planning,
        composition(spec("research"), spec("write", depends_on=["research"])),
        ["Not delivered"],
        pause=0.5,
    )
    stopped = client.post(f"/api/v1/crew/{crew_id}/stop", headers=auth)
    assert stopped.status_code == 200
    assert wait(client, auth, run_id)["status"] == "stopped"
    with runtime.factory() as session:
        parent = session.get(CrewInstance, crew_id)
        assert not parent.is_running
        assert not any(a.worker and a.worker.is_running for a in parent.agents)


def test_migration_backfills_one_agent_without_requeueing_a_running_legacy_worker(tmp_path):
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    from mavo.database import CrewRun
    from mavo.migrations import run_migrations

    factory = make_session(tmp_path)
    with factory() as session:
        crew = seed(session, checkpoint="Keep this checkpoint")
        crew_id = crew.id
        session.add(
            CrewRun(
                id="already-running",
                crew_id=crew_id,
                crew_name=crew.name,
                route_name="Local",
                provider="ollama",
                model="fixture",
                prompt="Earlier work",
                status="running",
            )
        )
        session.commit()
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    url = f"sqlite:///{(tmp_path / 'multi.db').as_posix()}"
    config.set_main_option("sqlalchemy.url", url)
    command.downgrade(config, "0019")
    run_migrations(url)
    with factory() as session:
        parent = session.get(CrewInstance, crew_id)
        assert len(parent.agents) == 1 and parent.agents[0].worker_id == crew_id
        assert parent.checkpoint == "Keep this checkpoint"
        assert session.get(CrewRun, "already-running").status == "running"
        assert session.query(TaskRun).count() == 0
    factory.kw["bind"].dispose()


def test_a_composed_reminder_uses_one_existing_deterministic_worker(tmp_path):
    from datetime import datetime

    from mavo.reminders import create_due_reminders

    factory = make_session(tmp_path)
    with factory() as session:
        crew = CrewInstance(
            name="Water",
            template_id="schedule",
            responsibilities="Drink water",
            provider="unassigned",
            model="",
            status="active",
            is_running=True,
            trigger_type="daily",
            schedule_time="16:00",
            reminder_text="Drink water",
            permissions=["notifications:show"],
            daily_triggers=[
                {
                    "id": "water",
                    "time": "16:00",
                    "message": "Drink water",
                    "weekdays": list(range(7)),
                    "enabled": True,
                }
            ],
        )
        session.add(crew)
        session.flush()
        set_composition(
            session, crew, composition(spec("remind", capabilities=["notifications:show"]))
        )
        reminders = create_due_reminders(session, datetime(2026, 11, 8, 16, 1, tzinfo=UTC))
        assert len(reminders) == 1 and reminders[0].message == "Drink water"
        assert len(crew.agents) == 1 and crew.agents[0].worker_id == crew.id
        assert session.query(TaskRun).count() == 0
        assert reminders[0].status == "pending"  # delivery is not falsely asserted
    factory.kw["bind"].dispose()


def test_revoked_source_permissions_prevent_consuming_an_already_delivered_message(tmp_path):
    factory = make_session(tmp_path)
    with factory() as session:
        crew = seed(session)
        crew.permissions = [*crew.permissions, "google:gmail:read"]
        set_composition(
            session,
            crew,
            composition(
                spec("mail", capabilities=["google:gmail:read"]),
                spec("match", capabilities=["google:gmail:read"]),
            ),
        )
        sender, recipient = crew.agents
        publish(
            session,
            WorkMessage(
                key="private-result",
                kind="result",
                sender=f"agent:{sender.id}",
                recipients=[f"agent:{recipient.id}"],
                summary="Authorized headers",
            ),
        )
        crew.permissions = ["model:invoke"]
        session.flush()
        assert claim_messages(session, f"agent:{recipient.id}", "reader") == []
    factory.kw["bind"].dispose()


def test_verified_listing_alerts_are_aggregated_and_quiet_checks_do_not_repeat_them(tmp_path):
    from types import SimpleNamespace

    from mavo.database import ReminderEvent
    from mavo.tasks import TaskRunner

    factory = make_session(tmp_path)
    runner = TaskRunner(SimpleNamespace(factory=factory))
    with factory() as session:
        crew = seed(session)
        crew.permissions = [*crew.permissions, "notifications:show"]
        set_composition(
            session,
            crew,
            composition(
                spec("find", verification="listings"),
                spec("prepare", capabilities=["notifications:show"], depends_on=["find"]),
            ),
        )
        crew_id = crew.id
        for agent in crew.agents:
            worker = worker_for(session, crew, agent)
            worker.observation = {
                "requested_notification": "A suitable opening",
                "requested_notification_ids": ["job-1"],
            }
            agent.execution = {"task_id": "first", "verification": {"new_listing_ids": ["job-1"]}}
        session.add(
            TaskRun(
                id="first",
                crew_id=crew.id,
                crew_generation=crew.runtime_generation,
                title="Internships",
                status="done",
                plan={},
                steps=[],
                attempts=1,
            )
        )
        session.commit()
    runner.alert("first", "Internships", "One actual new opening", "end")
    runner.alert("first", "Internships", "Duplicate callback", "end")
    with factory() as session:
        assert session.query(ReminderEvent).count() == 1
        crew = session.get(CrewInstance, crew_id)
        assert "job-1" in crew.agents[0].worker.observation["notified_source_ids"]
        for agent in crew.agents:
            agent.execution = {"task_id": "second", "verification": {"new_listing_ids": []}}
        session.add(
            TaskRun(
                id="second",
                crew_id=crew.id,
                crew_generation=crew.runtime_generation,
                title="Internships",
                status="done",
                plan={},
                steps=[],
                attempts=1,
            )
        )
        session.commit()
    runner.alert("second", "Internships", "No new listings", "end")
    with factory() as session:
        assert session.query(ReminderEvent).count() == 1
    factory.kw["bind"].dispose()


def test_agent_inbox_claims_do_not_consume_another_tasks_assignment(tmp_path):
    factory = make_session(tmp_path)
    with factory() as session:
        crew = seed(session)
        set_composition(session, crew, composition(spec("find")))
        recipient = f"agent:{crew.agents[0].id}"
        for task in ("first", "second"):
            publish(
                session,
                WorkMessage(
                    key=f"assign-{task}",
                    kind="task_assignment",
                    sender="core",
                    recipients=[recipient],
                    task_id=task,
                    summary=task,
                ),
            )
        assert len(claim_messages(session, recipient, "first-owner", task_id="first")) == 1
        finish_messages(session, "first-owner", consumed=True)
        second = claim_messages(session, recipient, "second-owner", task_id="second")
        assert len(second) == 1 and second[0]["result"] == "second"
    factory.kw["bind"].dispose()


def test_repeated_assignment_id_is_idempotent_after_binding_agent_policy(tmp_path):
    from types import SimpleNamespace

    from mavo.task_plan import TaskPlan
    from mavo.tasks import TaskRunner

    factory = make_session(tmp_path)
    runner = TaskRunner(SimpleNamespace(factory=factory))
    with factory() as session:
        crew = seed(session)
        set_composition(session, crew, composition(spec("find", actions=["browser.open"])))
        plan = {
            "title": "Find sources",
            "deliver": "Sources",
            "steps": [
                {
                    "id": "find",
                    "kind": "crew",
                    "crew_id": crew.id,
                    "agent_id": crew.agents[0].id,
                    "title": "Find",
                    "instructions": "Find sources",
                }
            ],
        }
        first = runner.prepare(session, TaskPlan.model_validate(plan), "Find sources", "same-id")
        session.commit()
        second = runner.prepare(session, TaskPlan.model_validate(plan), "Find sources", "same-id")
        assert first.id == second.id and session.query(TaskRun).count() == 1
        assert second.plan["steps"][0]["actions"] == ["browser.open"]
    factory.kw["bind"].dispose()


def test_core_assigns_to_a_whole_crew_in_one_task_with_a_deterministic_fan_in(planning):
    client, auth, runtime, _ = planning
    setup(client, auth)
    calls = []

    async def chat(*args, **kwargs):
        calls.append("call")
        return json.dumps(
            {"state": "wait_for_next_review", "summary": f"Role result {len(calls)}", "actions": []}
        )

    runtime.router.chat = chat
    with runtime.factory() as session:
        crew = seed(session)
        crew.is_running = False
        set_composition(session, crew, composition(spec("find"), spec("compare")))
        crew_id = crew.id
        session.commit()
    response = client.post(
        "/api/v1/tasks",
        headers=auth,
        json={
            "request": "Work together now",
            "plan": {
                "title": "Compare sources",
                "deliver": "Both results",
                "steps": [
                    {
                        "id": "team",
                        "kind": "crew",
                        "crew_id": crew_id,
                        "title": "Internship team",
                        "instructions": "Work together",
                    }
                ],
            },
        },
    )
    assert response.status_code == 201, response.text
    run = wait(client, auth, response.json()["id"])
    assert run["status"] == "done", run
    assert len(calls) == 2 and "Role result 1" in run["result"] and "Role result 2" in run["result"]
    assert len(run["steps"]) == 3  # two real members and a no-inference fan-in
    with runtime.factory() as session:
        assert session.query(TaskRun).count() == 1
        assert not session.get(CrewInstance, crew_id).is_running


def test_legacy_pipeline_handoffs_bridge_into_members_and_back_out(planning):
    from mavo.database import AgentDelivery, AgentEnvelope
    from mavo.pipelines import deliver

    client, auth, runtime, _ = planning
    setup(client, auth)
    prompts = []

    async def chat(provider, model, messages, system=None, credential_id=None):
        prompts.append(messages)
        return json.dumps(
            {"state": "wait_for_next_review", "summary": "Processed source artifact", "actions": []}
        )

    runtime.router.chat = chat
    with runtime.factory() as session:
        crew = seed(session)
        set_composition(session, crew, composition(spec("process")))
        crew.trigger_type = "manual"
        origin = CrewInstance(
            name="Source",
            template_id="research",
            responsibilities="Find sources",
            provider="ollama",
            model="fixture",
            route_id=crew.route_id,
            permissions=["model:invoke"],
            is_running=True,
        )
        session.add(origin)
        session.flush()
        crew.configuration_plan = {**crew.configuration_plan, "upstream": [origin.id]}
        downstream = CrewInstance(
            name="Legacy recipient",
            template_id="research",
            responsibilities="Receive results",
            provider="ollama",
            model="fixture",
            route_id=crew.route_id,
            permissions=["model:invoke"],
            is_running=True,
            trigger_type="manual",
            configuration_plan={"upstream": [crew.id]},
        )
        session.add(downstream)
        session.flush()
        deliver(session, origin, "Real source artifact")
        crew_id, downstream_id = crew.id, downstream.id
        session.commit()
    client.portal.call(dispatch, runtime, crew_id)
    with runtime.factory() as session:
        run_id = session.scalar(select(TaskRun.id).where(TaskRun.crew_id == crew_id))
    result = wait(client, auth, run_id)
    assert result["status"] == "done", result
    assert "Real source artifact" in json.dumps(prompts)
    with runtime.factory() as session:
        downstream = session.get(CrewInstance, downstream_id)
        assert any(
            "Processed source artifact" in n["result"] for n in downstream.observation["handoffs"]
        )
        assert session.get(CrewInstance, crew_id).runtime_state == "waiting_event", session.get(
            CrewInstance, crew_id
        ).observation
        states = list(
            session.scalars(
                select(AgentDelivery.status)
                .join(AgentEnvelope)
                .where(AgentDelivery.recipient == f"crew:{crew_id}")
            )
        )
        assert states == ["consumed"]


def test_inline_action_grant_resumes_same_cycle_without_replaying_completed_work(planning):
    client, auth, runtime, crew_id, run_id, calls = run_composed(
        planning, composition(spec("browse", actions=["browser.open"])), ["Access is now ready"]
    )
    assert wait(client, auth, run_id)["status"] == "needs_you"
    assert not calls
    events = client.get("/api/v1/orchestration/events", headers=auth).json()
    request = next(e for e in events if e["kind"] == "permission_request")
    with runtime.factory() as session:
        generation = session.get(CrewInstance, crew_id).runtime_generation
    saved = client.put(
        "/api/v1/action-policies",
        headers=auth,
        json={
            "crew_id": crew_id,
            "operation": "browser.open",
            "rule": {"enabled": True, "mode": "unrestricted"},
        },
    )
    assert saved.status_code == 200, saved.text
    granted = client.post(
        f"/api/v1/orchestration/events/{request['id']}/answer",
        headers=auth,
        json={"revision": request["revision"], "decision": "grant"},
    )
    assert granted.status_code == 200, granted.text
    assert wait(client, auth, run_id)["status"] == "done"
    with runtime.factory() as session:
        assert session.get(CrewInstance, crew_id).runtime_generation == generation
        assert session.query(TaskRun).count() == 1


def test_what_a_reviewer_passes_is_really_made_after_the_review(planning, monkeypatch):
    """A reviewer passed a reply, and it was refused as "This agent's Crew assignment is no
    longer active": the writer's step had ended, and with it the link that lets it act."""
    client, auth, runtime, _ = planning
    setup(client, auth)
    made = []

    async def chat(provider, model, messages, system=None, credential_id=None):
        if "REVIEW OUTCOME" in (system or ""):
            assert made == []  # nothing is made before its review
            return json.dumps(
                dict(state="wait_for_next_review", summary="Checked.", review_verdict="pass")
            )
        return json.dumps(
            dict(
                state="wait_for_next_review",
                summary="Wrote the confirmation.",
                actions=[
                    {
                        "operation": "notes.write",
                        "arguments": {"path": "Notes.md", "text": "Booked"},
                    }
                ],
            )
        )

    async def perform(action):
        made.append(action.operation)
        return {"id": "note-1", "summary": "Wrote Notes.md", "verification": "local_tool_result"}

    runtime.router.chat = chat
    monkeypatch.setattr(runtime.actions, "perform", perform)
    team = composition(
        spec("writer", capabilities=["action:notes.write"], actions=["notes.write"]),
        spec("review", depends_on=["writer"], reviews="writer"),
    )
    with runtime.factory() as session:
        crew = seed(session)
        crew.permissions = [*crew.permissions, "action:notes.write"]
        set_composition(session, crew, team)
        crew_id = crew.id
        session.commit()
    rule = client.put(
        "/api/v1/action-policies",
        headers=auth,
        json={
            "crew_id": crew_id,
            "operation": "notes.write",
            "rule": {"enabled": True, "mode": "unrestricted"},
        },
    )
    assert rule.status_code == 200, rule.text
    with runtime.factory() as session:  # a rule change stops a crew: on again, as the person would
        crew = session.get(CrewInstance, crew_id)
        crew.is_running, crew.runtime_state = True, "waiting"
        session.commit()
    client.portal.call(dispatch, runtime, crew_id)
    with runtime.factory() as session:
        run_id = session.scalar(select(TaskRun.id).where(TaskRun.crew_id == crew_id))
        crew = session.get(CrewInstance, crew_id)
        assert run_id, (crew.runtime_state, crew.last_runtime_error, crew.status)
    run = wait(client, auth, run_id)
    assert run["status"] == "done", run
    assert made == ["notes.write"], run["result"]  # made once, after the review passed
    assert "Done: " in run["result"] and "no longer active" not in run["result"]


def test_the_same_change_proposed_twice_is_made_once_in_its_latest_version(planning, monkeypatch):
    """A booker proposed a booking and its reply, then again on its next turn, and the
    reviewer's pass made both: two events and two replies to the same thread."""
    client, auth, runtime, _ = planning
    setup(client, auth)
    made, turns = [], {"writer": 0}

    async def chat(provider, model, messages, system=None, credential_id=None):
        if "REVIEW OUTCOME" in (system or ""):
            return json.dumps(
                dict(state="wait_for_next_review", summary="Checked.", review_verdict="pass")
            )
        turns["writer"] += 1
        first = turns["writer"] == 1
        return json.dumps(
            dict(
                state="continue" if first else "wait_for_next_review",
                summary="Wrote the confirmation.",
                next_step="Check it once more." if first else "",
                actions=[
                    {
                        "operation": "notes.write",
                        "arguments": {"path": "Notes.md", "text": f"Booked v{turns['writer']}"},
                    }
                ],
            )
        )

    async def perform(action):
        made.append(action.arguments["text"])
        return {"id": "note-1", "summary": "Wrote Notes.md", "verification": "local_tool_result"}

    runtime.router.chat = chat
    monkeypatch.setattr(runtime.actions, "perform", perform)
    team = composition(
        spec("writer", capabilities=["action:notes.write"], actions=["notes.write"]),
        spec("review", depends_on=["writer"], reviews="writer"),
    )
    with runtime.factory() as session:
        crew = seed(session)
        crew.permissions = [*crew.permissions, "action:notes.write"]
        set_composition(session, crew, team)
        crew_id = crew.id
        session.commit()
    client.put(
        "/api/v1/action-policies",
        headers=auth,
        json={
            "crew_id": crew_id,
            "operation": "notes.write",
            "rule": {"enabled": True, "mode": "unrestricted"},
        },
    )
    with runtime.factory() as session:
        crew = session.get(CrewInstance, crew_id)
        crew.is_running, crew.runtime_state = True, "waiting"
        session.commit()
    client.portal.call(dispatch, runtime, crew_id)
    with runtime.factory() as session:
        run_id = session.scalar(select(TaskRun.id).where(TaskRun.crew_id == crew_id))
    run = wait(client, auth, run_id)
    assert run["status"] == "done", run
    assert turns["writer"] == 2
    assert made == ["Booked v2"], run["result"]  # once, its latest version


def test_one_change_is_the_same_target_however_worded():
    from mavo.action_rules import same_change

    reply = {"to": "asha@example.com", "subject": "Re: Sync", "thread_id": "t1", "body": "Hi"}
    again = {**reply, "body": "Hi Asha, booked.", "subject": "RE: Sync"}
    assert same_change("gmail.send", reply, again, "gmail.send")
    assert not same_change("gmail.send", reply, {**reply, "to": "kabir@example.net"}, "gmail.send")
    assert not same_change("gmail.send", reply, again, "calendar.create")
    event = {
        "summary": "Asha sync",
        "start": "2026-11-17T15:00:00+05:30",
        "end": "2026-11-17T15:30:00+05:30",
    }
    assert same_change("calendar.create", event, {**event, "summary": "Sync with Asha"})
    assert not same_change(
        "calendar.create", event, {**event, "start": "2026-11-18T11:00:00+05:30"}
    )
