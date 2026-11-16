"""First-run execution uses fake models and isolated DBs, never live accounts."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from mavo.database import CrewInstance, CrewRun, ReminderEvent, TaskRun, utcnow
from mavo.first_run import calibrated_budget, recover_first_runs, verify_first_run
from tests.test_planner import planning, turn  # noqa: F401


def draft(client, auth, runtime, **changes):
    route = client.post(
        "/api/v1/routes",
        headers=auth,
        json={
            "name": "Test",
            "provider": "ollama",
            "model": "test",
        },
    ).json()
    with runtime.factory() as session:
        crew = CrewInstance(
            id="first-run-source",
            name="Test Crew",
            template_id="research",
            responsibilities="Write a concise explanation.",
            provider="ollama",
            model="test",
            route_id=route["id"],
            status="draft",
            max_model_calls=10,
            daily_call_limit=30,
            permissions=["model:invoke"],
            configuration_plan={},
            **changes,
        )
        session.add(crew)
        session.commit()
    runtime.router.chat = AsyncMock(
        return_value=json.dumps(
            {
                "state": "wait_for_next_review",
                "summary": "Here is the requested explanation.",
                "actions": [],
            }
        )
    )
    return crew.id


def test_budget_buffer_never_exceeds_approved_ceiling():
    assert calibrated_budget(4, 10)["configured_calls"] == 7
    assert calibrated_budget(4, 5)["buffer_complete"] is False
    # The work fits, so it passes with the room there is: 2 used, limit 3 (it once failed setup
    # as "2 measured + 3 spare = 5").
    assert calibrated_budget(2, 3) | {} == {
        **calibrated_budget(2, 3),
        "configured_calls": 3,
        "fits": True,
    }
    assert calibrated_budget(4, 3)["fits"] is False


def test_add_endpoint_runs_verification_before_publishing_and_sets_measured_budget(
    planning, monkeypatch
):
    from mavo.schemas import CoordinatorDecision

    client, auth, runtime, coordinator = planning
    monkeypatch.setattr("mavo.first_run.FIRST_RUN_TIMEOUT", 10)
    del runtime.verify_first_run  # this integration test exercises the real verifier
    route = client.post(
        "/api/v1/routes",
        headers=auth,
        json={
            "name": "Test",
            "provider": "ollama",
            "model": "test",
        },
    ).json()
    coordinator.decision = CoordinatorDecision.model_validate(
        {
            "reply": "Here is the setup",
            "crew_proposal": {
                "template_id": "research",
                "name": "Explanation",
                "responsibilities": "Explain a useful programming concept every hour.",
                "trigger_type": "interval",
                "route_id": route["id"],
            },
        }
    )
    created = turn(planning, "Explain a programming concept every hour")["draft"]
    assert client.get("/api/v1/crew", headers=auth).json() == []
    runtime.router.chat = AsyncMock(
        return_value=json.dumps(
            {
                "state": "wait_for_next_review",
                "summary": "A variable stores a value.",
                "actions": [],
            }
        )
    )
    response = client.post(f"/api/v1/crew/{created['id']}/access", headers=auth, json={})
    assert response.status_code == 200, response.text
    added = response.json()
    assert added["status"] == "active", json.dumps(added["configuration_plan"]["first_run"])
    assert added["configuration_plan"]["first_run"]["measured_calls"] == 1
    assert added["max_model_calls"] == 4
    assert runtime.router.chat.await_count == 1
    assert len(client.get("/api/v1/crew", headers=auth).json()) == 1


def test_first_run_executes_real_worker_once_and_cleans_private_adapters(client, auth):
    runtime = client.app.state.crew_runtime
    crew_id = draft(client, auth, runtime)

    async def check():
        return await asyncio.wait_for(verify_first_run(runtime, crew_id), timeout=12)

    result = asyncio.run(check())
    assert result["status"] == "passed", result
    assert result["measured_calls"] == 1
    assert result["configured_calls"] == 4
    assert client.get("/api/v1/orchestration/messages", headers=auth).json() == []
    assert client.get("/api/v1/orchestration/events", headers=auth).json() == []
    with runtime.factory() as session:
        source = session.get(CrewInstance, crew_id)
        assert source.status == "draft" and not source.is_running
        assert source.daily_calls_used == 1
        assert session.query(CrewInstance).count() == 1
        assert session.query(TaskRun).count() == 0
        assert session.query(ReminderEvent).count() == 0


def test_conditional_actions_are_not_run_or_claimed_verified_without_sample_arguments(client, auth):
    runtime = client.app.state.crew_runtime
    crew_id = draft(client, auth, runtime)
    with runtime.factory() as session:
        session.get(CrewInstance, crew_id).permissions = ["model:invoke", "action:terminal.run"]
        session.commit()
    enabled = client.put(
        "/api/v1/action-policies",
        headers=auth,
        json={
            "crew_id": crew_id,
            "operation": "terminal.run",
            "rule": {"enabled": True, "mode": "unrestricted"},
        },
    )
    assert enabled.status_code == 200
    result = asyncio.run(verify_first_run(runtime, crew_id))
    assert result["status"] == "passed" and result["kind"] == "action_preview"
    assert result["confirmed"] is False and not result["proposed_actions"]
    assert "terminal.run" in result["unexercised_actions"]
    assert "without a sample input were not exercised" in result["message"]
    assert runtime.router.chat.await_count == 1


def test_schedule_test_does_not_deliver_an_early_reminder(client, auth):
    runtime = client.app.state.crew_runtime
    crew_id = draft(client, auth, runtime, trigger_type="daily", schedule_time="16:00")
    result = asyncio.run(verify_first_run(runtime, crew_id))
    assert result["status"] == "passed" and result["kind"] == "schedule_validation"
    runtime.router.chat.assert_not_called()
    with runtime.factory() as session:
        assert not session.scalar(select(ReminderEvent.id))


def test_device_verification_needs_no_model_calls_even_when_daily_budget_is_used(
    client, auth, monkeypatch
):
    runtime = client.app.state.crew_runtime
    crew_id = draft(client, auth, runtime)
    metrics = {
        "cpu_percent": 2,
        "memory_percent": 30,
        "storage_percent": 40,
        "storage_free_gb": 80,
        "storage_total_gb": 200,
        "battery_percent": 70,
        "plugged_in": True,
        "process_count": 100,
    }
    monkeypatch.setattr("mavo.crew_workers.read_device_metrics", lambda: metrics)
    with runtime.factory() as session:
        source = session.get(CrewInstance, crew_id)
        source.template_id = "computer"
        source.permissions = ["model:invoke", "device:read"]
        source.budget_day = utcnow().astimezone().date().isoformat()
        source.daily_calls_used = source.daily_call_limit
        session.commit()
    result = asyncio.run(verify_first_run(runtime, crew_id))
    assert result["status"] == "passed" and result["kind"] == "device_metrics", result
    assert result["measured_calls"] == 0
    runtime.router.chat.assert_not_called()


def test_device_cleanup_preview_never_deletes_temp_or_recycle_bin(client, auth, monkeypatch):
    from unittest.mock import Mock

    runtime = client.app.state.crew_runtime
    crew_id = draft(client, auth, runtime)
    clean = Mock(side_effect=AssertionError("Never clean temp to test setup"))
    recycle = Mock(side_effect=AssertionError("Never empty the bin to test setup"))
    monkeypatch.setattr("mavo.crew_workers.clean_temp", clean)
    monkeypatch.setattr("mavo.crew_workers.empty_recycle_bin", recycle)
    monkeypatch.setattr(
        "mavo.crew_workers.read_storage_metrics",
        lambda drive: {
            "drive": drive,
            "storage_percent": 40,
            "storage_free_gb": 80,
            "storage_total_gb": 200,
        },
    )
    with runtime.factory() as session:
        source = session.get(CrewInstance, crew_id)
        source.template_id = "computer"
        source.permissions = ["model:invoke", "device:read", "device:clean_temp"]
        source.configuration_plan = {
            "device_monitor": {
                "kind": "storage",
                "drive": "C:",
                "clean_temp_days": 7,
                "empty_recycle_bin": True,
            }
        }
        session.commit()
    result = asyncio.run(verify_first_run(runtime, crew_id))
    assert result["status"] == "passed", result.get("message")
    assert result["kind"] == "action_preview" and result["confirmed"] is False
    assert result["proposed_actions"][0]["operation"] == "device.clean_temp"
    assert "no files changed" in result["preview"]
    assert result["measured_calls"] == 0
    clean.assert_not_called()
    recycle.assert_not_called()
    runtime.router.chat.assert_not_called()


def test_two_agents_are_measured_separately_with_one_shared_cycle_budget(client, auth):
    from mavo.crew_composition import set_composition

    runtime = client.app.state.crew_runtime
    crew_id = draft(client, auth, runtime)
    with runtime.factory() as session:
        set_composition(
            session,
            session.get(CrewInstance, crew_id),
            {
                "agents": [
                    {
                        "key": key,
                        "name": key,
                        "role": key,
                        "instructions": "Write a useful explanation.",
                        "depends_on": ["read"] if key == "write" else [],
                        "completion_criteria": "Return the explanation",
                    }
                    for key in ("read", "write")
                ]
            },
        )
        session.commit()

    async def check():
        return await asyncio.wait_for(verify_first_run(runtime, crew_id), timeout=12)

    result = asyncio.run(check())
    assert result["status"] == "passed", result
    assert result["measured_calls"] == 2 and result["cycle_calls"] == 8
    assert all(
        a["measured_calls"] == 1 and a["configured_calls"] == 4 for a in result["agents"].values()
    )


def test_a_small_limit_that_fits_the_work_passes_without_a_silent_budget_increase(client, auth):
    """Spare calls are room, not a requirement: work that fits its limit passes, and the limit is
    never raised behind the person's back (3 stays 3)."""
    runtime = client.app.state.crew_runtime
    crew_id = draft(client, auth, runtime)
    with runtime.factory() as session:
        session.get(CrewInstance, crew_id).max_model_calls = 3
        session.commit()
    result = asyncio.run(verify_first_run(runtime, crew_id))
    assert result["status"] == "passed", result
    worker = result["agents"]["worker"]
    assert worker["recommended_calls"] == 4 and worker["configured_calls"] == 3
    with runtime.factory() as session:
        source = session.get(CrewInstance, crew_id)
        assert source.max_model_calls == 3
        assert source.daily_calls_used == 1


def test_a_real_question_ends_the_test_instead_of_waiting_indefinitely(client, auth):
    runtime = client.app.state.crew_runtime
    crew_id = draft(client, auth, runtime)
    runtime.router.chat = AsyncMock(
        return_value=json.dumps(
            {
                "state": "waiting_for_input",
                "summary": "Need your choice",
                "question": "Which subject should I cover?",
                "actions": [],
            }
        )
    )

    async def check():
        return await asyncio.wait_for(verify_first_run(runtime, crew_id), timeout=12)

    result = asyncio.run(check())
    assert result["status"] == "failed", result
    assert result["questions"] == ["Which subject should I cover?"]


def test_restart_recovers_test_cost_without_repeating_work(client, auth):
    runtime = client.app.state.crew_runtime
    crew_id = draft(client, auth, runtime)
    day = utcnow().astimezone().date().isoformat()
    with runtime.factory() as session:
        source = session.get(CrewInstance, crew_id)
        source.budget_day, source.daily_calls_used = day, 8
        source.configuration_plan = {
            "first_run": {
                "status": "running",
                "adapter_id": "interrupted-adapter",
                "reserved_calls": 8,
                "budget_day": day,
            }
        }
        session.add(
            CrewInstance(
                id="interrupted-adapter",
                name="Test adapter",
                hidden=True,
                template_id="research",
                responsibilities="Read only",
                provider="ollama",
                model="test",
            )
        )
        session.flush()
        session.add(
            CrewRun(
                crew_id="interrupted-adapter",
                crew_name="Test",
                route_name="Test route",
                provider="ollama",
                model="test",
                prompt="Test",
                status="completed",
                model_calls=2,
            )
        )
        session.commit()
    recover_first_runs(runtime.factory)
    recover_first_runs(runtime.factory)
    runtime.router.chat.assert_not_called()
    with runtime.factory() as session:
        source = session.get(CrewInstance, crew_id)
        assert source.daily_calls_used == 2
        assert source.configuration_plan["first_run"]["status"] == "interrupted"
        assert session.get(CrewInstance, "interrupted-adapter") is None


def test_access_revocation_during_a_test_prevents_publication(client, auth):
    runtime = client.app.state.crew_runtime
    crew_id = draft(client, auth, runtime)

    async def revoke(*args, **kwargs):
        with runtime.factory() as session:
            source = session.get(CrewInstance, crew_id)
            source.permissions = []
            session.commit()
        return json.dumps({"state": "wait_for_next_review", "summary": "Finished", "actions": []})

    runtime.router.chat = AsyncMock(side_effect=revoke)

    async def check():
        return await asyncio.wait_for(verify_first_run(runtime, crew_id), timeout=12)

    result = asyncio.run(check())
    assert result["status"] == "failed", result
    with runtime.factory() as session:
        assert session.get(CrewInstance, crew_id).status == "draft"


def test_public_task_submission_cannot_reuse_a_private_verification_adapter(client, auth):
    from mavo.task_plan import TaskPlan

    runtime = client.app.state.crew_runtime
    crew_id = draft(client, auth, runtime)
    with runtime.factory() as session:
        source = session.get(CrewInstance, crew_id)
        source.configuration_plan = {
            "first_run": {
                "status": "running",
                "adapter_id": "private",
                "generation": source.runtime_generation,
            }
        }
        session.add(
            CrewInstance(
                id="private",
                name="Private test",
                template_id="research",
                responsibilities="Test",
                provider="ollama",
                model="test",
                hidden=True,
                status="active",
                permissions=["model:invoke"],
                configuration_plan={"first_run_source": crew_id},
            )
        )
        session.flush()
        plan = TaskPlan.model_validate(
            {
                "title": "Extra work",
                "deliver": "Extra result",
                "steps": [
                    {
                        "id": "extra",
                        "kind": "crew",
                        "crew_id": "private",
                        "title": "Extra",
                        "instructions": "Do extra work",
                    }
                ],
                "final": "extra",
                "model_calls": 4,
            }
        )
        with pytest.raises(ValueError, match="unavailable"):
            runtime.task_runner.prepare(session, plan)


def test_concurrent_test_requests_do_not_duplicate_paid_work(client, auth):
    from fastapi import HTTPException

    runtime = client.app.state.crew_runtime
    crew_id = draft(client, auth, runtime)

    async def check():
        entered, release = asyncio.Event(), asyncio.Event()

        async def model(*args, **kwargs):
            entered.set()
            await release.wait()
            return json.dumps(
                {"state": "wait_for_next_review", "summary": "Explanation", "actions": []}
            )

        runtime.router.chat = AsyncMock(side_effect=model)
        first = asyncio.create_task(verify_first_run(runtime, crew_id))
        await asyncio.wait_for(entered.wait(), timeout=5)
        try:
            with pytest.raises(HTTPException) as raised:
                await verify_first_run(runtime, crew_id)
            assert raised.value.status_code == 409
        finally:
            release.set()
        return await asyncio.wait_for(first, timeout=10)

    result = asyncio.run(check())
    assert result["status"] == "passed", result
    assert runtime.router.chat.await_count == 1


def test_a_draft_small_daily_number_never_stops_its_setup_test(client, auth):
    """12 a day, 11 used: the test used to refuse ("Raise its AI calls per day with Refine").
    Only Mavo's large daily ceiling counts now."""
    runtime = client.app.state.crew_runtime
    crew_id = draft(client, auth, runtime)
    day = utcnow().astimezone().date().isoformat()
    with runtime.factory() as session:
        source = session.get(CrewInstance, crew_id)
        source.budget_day, source.daily_calls_used = day, source.daily_call_limit - 1
        session.commit()
    assert asyncio.run(verify_first_run(runtime, crew_id))["status"] == "passed"


def test_an_alerting_agents_test_is_told_alerts_work_and_shows_the_alert_it_would_send(
    client, auth
):
    """A quote agent's test was told "Desktop update permission: False" (a test pops nothing
    up), reported "Desktop Alert Unavailable" and passed. It is told the truth, writes its alert,
    and the alert is the test's result; nothing appears on the desktop."""
    runtime = client.app.state.crew_runtime
    crew_id = draft(client, auth, runtime, trigger_type="interval")
    with runtime.factory() as session:
        session.get(CrewInstance, crew_id).permissions = ["model:invoke", "notifications:show"]
        session.commit()
    prompts = []

    async def chat(provider, model, messages, system=None, credential_id=None, **kw):
        prompts.append(system or "")
        return json.dumps(
            {
                "state": "wait_for_next_review",
                "summary": "Quote ready.",
                "notification": "Small steps, every day.",
                "actions": [],
            }
        )

    runtime.router.chat = chat
    result = asyncio.run(verify_first_run(runtime, crew_id))
    assert result["status"] == "passed", result
    assert any("setup TEST run" in p and "permission: True" in p for p in prompts)
    assert "Desktop alert: Small steps, every day." in result["preview"]
    with runtime.factory() as session:
        assert not session.scalar(select(ReminderEvent.id))  # nothing popped up
