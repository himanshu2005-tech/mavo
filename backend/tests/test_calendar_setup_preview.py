"""Calendar setup must be actionable, non-writing during tests, and explicitly enabled."""

import json
from unittest.mock import AsyncMock

import pytest

from mavo.database import ActionRequest, CrewInstance, WorkspaceSetting
from mavo.schemas import CoordinatorDecision, CrewDraft
from tests.test_actions import action_env  # noqa: F401


def setup_planner(action_env):
    client, auth, engine, state = action_env
    runtime = client.app.state.crew_runtime
    route = client.post(
        "/api/v1/routes",
        headers=auth,
        json={"name": "Test", "provider": "ollama", "model": "fixture"},
    ).json()
    proposal = CrewDraft.model_validate(
        {
            "template_id": "schedule",
            "name": "Daily Planner",
            "responsibilities": "Read my calendar and plan focus time. "
            "Create a calendar block, without modifying my tasks.",
            "trigger_type": "interval",
            "review_interval_minutes": 1440,
            "route_id": route["id"],
            "max_model_calls": 8,
            "daily_call_limit": 30,
            "composition": {
                "agents": [
                    {
                        "key": "planner",
                        "name": "Planner",
                        "role": "Day planner",
                        "instructions": "Read the calendar and propose a focus block.",
                        "capabilities": ["google:calendar:read", "google:calendar:write"],
                        "actions": ["calendar.create"],
                        "max_model_calls": 3,
                        "completion_criteria": "Propose a valid focus event, not a claimed write.",
                    }
                ],
                "limits": {"model_calls": 8},
            },
        }
    )
    with runtime.factory() as session:
        crew = runtime.planner.apply_plan(
            session, CoordinatorDecision(reply="Review this draft", crew_proposal=proposal), None
        )
        crew_id = crew.id
        session.commit()
    runtime.google_pool.read = AsyncMock(return_value={"items": []})
    runtime.router.chat = AsyncMock(
        side_effect=[
            json.dumps(
                {
                    "state": "continue",
                    "summary": "Proposing a focus block.",
                    "actions": [
                        {
                            "operation": "calendar.create",
                            "arguments": {
                                "summary": "Focus block",
                                "start": "2026-11-10T10:00:00+05:30",
                                "end": "2026-11-10T11:00:00+05:30",
                            },
                        }
                    ],
                }
            ),
            json.dumps(
                {
                    "state": "wait_for_next_review",
                    "summary": "Preview: one focus block. Nothing was written.",
                }
            ),
        ]
    )
    return client, auth, runtime, crew_id, state


def preview(env):
    client, auth, runtime, crew_id, state = env
    response = client.post(
        f"/api/v1/crew/{crew_id}/access",
        headers=auth,
        json={
            "capabilities": ["calendar:read", "calendar:write"],
            "actions": {"calendar.create": {"enabled": True, "mode": "unrestricted"}},
        },
    )
    assert response.status_code == 200, response.text
    crew = response.json()
    record = crew["configuration_plan"]["first_run"]
    assert record["status"] == "passed", record
    return crew, record


def test_missing_action_access_never_silently_activates_or_spends_calls(action_env):
    client, auth, runtime, crew_id, state = setup_planner(action_env)
    response = client.put(
        "/api/v1/action-policies",
        headers=auth,
        json={"crew_id": crew_id, "operation": "calendar.create", "rule": {"enabled": False}},
    )
    assert response.status_code == 200
    response = client.post(
        f"/api/v1/crew/{crew_id}/access",
        headers=auth,
        json={"capabilities": ["calendar:read", "calendar:write"]},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "draft"
    assert "Allow calendar.create" in response.json()["configuration_plan"]["missing"]
    runtime.router.chat.assert_not_called()
    assert not state["writes"]


def test_calendar_test_previews_changes_and_requires_explicit_enable(action_env):
    env = setup_planner(action_env)
    client, auth, runtime, crew_id, state = env
    crew, record = preview(env)
    assert crew["status"] == "draft" and not crew["is_running"]
    assert record["kind"] == "action_preview" and record["measured_calls"] == 2
    assert record["proposed_actions"][0]["arguments"]["summary"] == "Focus block"
    assert (
        "Review the test preview before enabling changes" in crew["configuration_plan"]["missing"]
    )
    assert not state["writes"]
    assert runtime.router.chat.await_count == 2
    with runtime.factory() as session:
        assert session.query(ActionRequest).count() == 0  # no orphan preview in real activity
    payload = {"verification_id": record["adapter_id"], "generation": record["generation"]}
    response = client.post(f"/api/v1/crew/{crew_id}/activate-preview", headers=auth, json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "active" and response.json()["is_running"]
    repeated = client.post(f"/api/v1/crew/{crew_id}/activate-preview", headers=auth, json=payload)
    assert repeated.status_code == 200 and not state["writes"]
    assert runtime.router.chat.await_count == 2


@pytest.mark.parametrize("change", ["policy", "account", "route", "configuration"])
def test_changed_access_invalidates_the_preview(action_env, change):
    env = setup_planner(action_env)
    client, auth, runtime, crew_id, state = env
    _, record = preview(env)
    with runtime.factory() as session:
        crew = session.get(CrewInstance, crew_id)
        if change == "policy":
            row = session.get(
                WorkspaceSetting, runtime.actions.rules.key("calendar.create", crew_id)
            )
            row.value = {**row.value, "enabled": False}
        elif change == "account":
            from mavo.database import GoogleAccount

            session.get(GoogleAccount, "google").subject = "other-account"
        elif change == "route":
            from mavo.database import ModelRoute

            session.get(ModelRoute, crew.route_id).model = "different-model"
        else:
            crew.responsibilities = "Different instructions"
        session.commit()
    response = client.post(
        f"/api/v1/crew/{crew_id}/activate-preview",
        headers=auth,
        json={"verification_id": record["adapter_id"], "generation": record["generation"]},
    )
    assert response.status_code == 409
    assert not state["writes"]


def test_revocation_during_model_call_prevents_even_a_change_preview(action_env):
    env = setup_planner(action_env)
    client, auth, runtime, crew_id, state = env

    # Explicit response, independent of AsyncMock's internal iterator representation.
    async def revoke(*args, **kwargs):
        with runtime.factory() as session:
            row = session.get(
                WorkspaceSetting, runtime.actions.rules.key("calendar.create", crew_id)
            )
            row.value = {**row.value, "enabled": False}
            session.commit()
        return json.dumps(
            {
                "state": "continue",
                "summary": "Proposed focus time",
                "actions": [
                    {
                        "operation": "calendar.create",
                        "arguments": {
                            "summary": "Focus",
                            "start": "2026-11-10T10:00:00+05:30",
                            "end": "2026-11-10T11:00:00+05:30",
                        },
                    }
                ],
            }
        )

    runtime.router.chat = AsyncMock(side_effect=revoke)
    response = client.post(
        f"/api/v1/crew/{crew_id}/access",
        headers=auth,
        json={
            "capabilities": ["calendar:read", "calendar:write"],
            "actions": {"calendar.create": {"enabled": True, "mode": "unrestricted"}},
        },
    )
    assert response.status_code == 200
    result = response.json()
    assert (
        result["status"] == "draft"
        and result["configuration_plan"]["first_run"]["status"] == "failed"
    )
    assert not state["writes"]


def test_private_preview_cleanup_keeps_unrelated_user_action_history(action_env):
    env = setup_planner(action_env)
    client, auth, runtime, crew_id, state = env
    with runtime.factory() as session:
        saved = ActionRequest(
            operation="calendar.create",
            source="user",
            account_subject="user-1",
            arguments={},
            fingerprint="unrelated-user-action",
            status="pending",
        )
        session.add(saved)
        session.commit()
        action_id = saved.id
    preview(env)
    with runtime.factory() as session:
        assert session.get(ActionRequest, action_id).status == "pending"
        assert session.query(ActionRequest).count() == 1
    assert not state["writes"]


def test_delete_is_allowed_in_setup_but_unexercised_is_not_claimed_verified(action_env):
    client, auth, runtime, crew_id, state = setup_planner(action_env)
    with runtime.factory() as session:
        crew = session.get(CrewInstance, crew_id)
        plan = dict(crew.configuration_plan)
        plan["draft"] = {
            **plan["draft"],
            "requested_actions": [
                *plan["draft"]["requested_actions"],
                {"operation": "calendar.delete", "reason": "Unsupported setup test"},
            ],
        }
        crew.configuration_plan = plan
        session.commit()
    response = client.post(
        f"/api/v1/crew/{crew_id}/access",
        headers=auth,
        json={
            "capabilities": ["calendar:read", "calendar:write"],
            "actions": {
                op: {"enabled": True, "mode": "unrestricted"}
                for op in ["calendar.create", "calendar.delete"]
            },
        },
    )
    assert response.status_code == 200
    result = response.json()["configuration_plan"]["first_run"]
    assert result["status"] == "passed" and result["kind"] == "action_preview"
    assert result["confirmed"] is False
    assert "calendar.delete" in result["unexercised_actions"]
    assert runtime.router.chat.await_count == 2
    assert not state["writes"]


def test_mixed_old_runtime_refuses_write_testing_until_restart(action_env):
    client, auth, runtime, crew_id, state = setup_planner(action_env)
    runtime.actions.first_run_preview_protocol = 0
    response = client.post(
        f"/api/v1/crew/{crew_id}/access",
        headers=auth,
        json={
            "capabilities": ["calendar:read", "calendar:write"],
            "actions": {"calendar.create": {"enabled": True, "mode": "unrestricted"}},
        },
    )
    assert response.status_code == 200
    record = response.json()["configuration_plan"]["first_run"]
    assert record["status"] == "failed" and "Restart Mavo" in record["message"]
    runtime.router.chat.assert_not_called()
    assert not state["writes"]
