"""Turning an agent on without its setup test: one that only reads goes straight on; one that
changes things asks before its first few changes, since the test is where its mistakes were
caught before they reached anyone."""

import json
from unittest.mock import AsyncMock

from mavo.action_rules import ASK_FIRST, asks_first
from mavo.database import ActionRequest, CrewInstance, utcnow
from tests.test_planner import planning, turn  # noqa: F401


def proposal(planning, **extra):
    from mavo.schemas import CoordinatorDecision

    client, auth, runtime, coordinator = planning
    route = client.post(
        "/api/v1/routes",
        headers=auth,
        json={"name": "Test", "provider": "ollama", "model": "test"},
    ).json()
    coordinator.decision = CoordinatorDecision.model_validate(
        {
            "reply": "Here is the setup",
            "crew_proposal": {
                "template_id": "research",
                "name": "Explainer",
                "responsibilities": "Explain a useful programming concept every hour.",
                "trigger_type": "interval",
                "route_id": route["id"],
                **extra,
            },
        }
    )
    runtime.router.chat = AsyncMock(
        return_value=json.dumps({"state": "wait_for_next_review", "summary": "x", "actions": []})
    )
    return turn(planning, "Explain a programming concept every hour")["draft"]


def test_an_agent_that_only_reads_goes_on_without_its_test(planning):
    client, auth, runtime, _ = planning
    created = proposal(planning)
    added = client.post(
        f"/api/v1/crew/{created['id']}/access", headers=auth, json={"skip_test": True}
    ).json()
    assert added["status"] == "active"
    assert added["configuration_plan"]["first_run"]["status"] == "skipped"
    assert "ask_first" not in added["configuration_plan"]
    assert added["configuration_plan"]["changes_things"] is False
    runtime.router.chat.assert_not_called()  # no test run, no model call


def test_an_agent_that_acts_asks_before_its_first_changes_when_its_test_is_skipped(planning):
    client, auth, runtime, _ = planning
    created = proposal(planning, requested_actions=[{"operation": "notes.write"}])
    added = client.post(
        f"/api/v1/crew/{created['id']}/access",
        headers=auth,
        json={
            "skip_test": True,
            "ask_first": True,
            "actions": {"notes.write": {"enabled": True, "mode": "unrestricted"}},
        },
    ).json()
    assert added["status"] == "active", added["configuration_plan"].get("missing")
    assert added["configuration_plan"]["ask_first"]["count"] == ASK_FIRST
    assert added["configuration_plan"]["changes_things"] is True
    with runtime.factory() as session:

        def action(n, **kw):
            operation = kw.pop("operation", "notes.write")
            rule = runtime.actions.rules.read(session, operation, created["id"])
            row = ActionRequest(
                crew_id=created["id"],
                account_subject="local",
                operation=operation,
                arguments={},
                fingerprint=f"skip-{n}",
                policy_revision=rule["revision"],
                **kw,
            )
            session.add(row)
            session.flush()
            return row

        change = action(0)
        assert asks_first(session, change)
        assert not runtime.actions.rules.automatic(session, change)
        assert not asks_first(session, action(1, operation="browser.read"))  # looking is free
        change.approved_by = "user"
        assert runtime.actions.rules.automatic(session, change)  # the person said yes to it
        for n in range(ASK_FIRST):
            action(10 + n, approved_by="user", status="completed", created_at=utcnow())
        assert not asks_first(session, action(2))  # three approved: it runs on its own now


def test_testing_again_ends_the_ask_first_period(planning):
    client, auth, runtime, _ = planning
    created = proposal(planning, requested_actions=[{"operation": "notes.write"}])
    rule = {"notes.write": {"enabled": True, "mode": "unrestricted"}}
    url = f"/api/v1/crew/{created['id']}/access"
    client.post(url, headers=auth, json={"skip_test": True, "ask_first": True, "actions": rule})
    with runtime.factory() as session:
        crew = session.get(CrewInstance, created["id"])
        crew.status = "draft"  # back to a draft, as when the person edits its setup
        session.commit()
    again = client.post(url, headers=auth, json={"actions": rule}).json()
    assert "ask_first" not in again["configuration_plan"]


def test_turning_it_on_runs_its_checked_path_at_once_and_asks_before_its_first_changes(planning):
    """No waiting on a test: the path Core wrote and code checked runs from the start, and the
    person approves its first few changes."""
    from mavo.recipes import eligible
    from mavo.workflow_write import basis

    client, auth, runtime, _ = planning
    runtime.recipes = True
    created = proposal(planning, requested_actions=[{"operation": "notes.write"}])
    path = {"title": "Notes", "deliver": "Write notes", "final": "tell", "steps": []}
    with runtime.factory() as session:
        crew = session.get(CrewInstance, created["id"])
        crew.configuration_plan = {
            **crew.configuration_plan,
            "workflow_draft": path,
            "workflow_basis": basis(crew),
        }
        session.commit()
    added = client.post(
        f"/api/v1/crew/{created['id']}/access",
        headers=auth,
        json={
            "skip_test": True,
            "ask_first": True,
            "actions": {"notes.write": {"enabled": True, "mode": "unrestricted"}},
        },
    ).json()
    plan = added["configuration_plan"]
    assert (
        plan["recipe"] == path
        and plan["recipe_checked"]
        and plan["ask_first"]["count"] == ASK_FIRST
    )
    from types import SimpleNamespace

    agents = {"agents": [{"key": "w", "actions": ["notes.write"]}]}
    on = SimpleNamespace(
        hidden=False,
        status="active",
        is_running=True,
        trigger_type="gmail_message",
        template_id="research",
        configuration_plan={**plan, "composition": agents},
    )
    assert eligible(on)  # a crew with agents runs this checked path, not its agents


def test_a_test_and_try_it_first_never_share_the_browser_at_once(planning, monkeypatch):
    """Run together, one found Mavo's browser taken: the sample sat on "Opening the site" and
    the test's agent had no page to quote ("No source evidence matched")."""
    import asyncio

    import pytest
    from fastapi import HTTPException

    from mavo import trial

    client, auth, runtime, _ = planning
    created = proposal(planning)
    monkeypatch.setattr(trial, "trial_running", lambda crew_id: crew_id == created["id"])
    busy = client.post(f"/api/v1/crew/{created['id']}/access", headers=auth, json={})
    assert busy.status_code == 409 and "Try it first" in busy.text
    monkeypatch.setattr(trial, "trial_running", lambda crew_id: False)
    with runtime.factory() as session:
        crew = session.get(CrewInstance, created["id"])
        crew.configuration_plan = {**crew.configuration_plan, "first_run": {"status": "running"}}
        session.commit()
        with pytest.raises(HTTPException) as refused:
            asyncio.run(trial.run_trial(runtime, session, created["id"]))
    assert refused.value.status_code == 409 and "test is running" in refused.value.detail
