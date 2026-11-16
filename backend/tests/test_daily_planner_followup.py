"""Questionnaire refinement with two logical agents; fixtures, not live accounts."""

import asyncio
import json
from unittest.mock import AsyncMock

from mavo.coordinator_contract import (
    GatewayTurn,
    PlannerContext,
    clarification_from_invalid,
    coordinator_prompt,
    coordinator_response_schema,
)
from mavo.database import (
    ActionRequest,
    ConfigurationEdit,
    CrewInstance,
    ReminderEvent,
    TaskRun,
    WorkspaceSetting,
)
from mavo.schemas import CoordinatorDecision
from tests.test_planner import planning, turn  # noqa: F401
from tests.test_publisher_gateway import payload, serving


def plan(responsibilities="Summarize my mail and plan the day from my calendar and tasks."):
    return {
        "template_id": "schedule",
        "name": "Daily Planner",
        "responsibilities": responsibilities,
        "trigger_type": "interval",
        "review_interval_minutes": 1440,
        "requested_google_capabilities": [
            "gmail:read",
            "gmail:body",
            "calendar:read",
            "tasks:read",
        ],
        "requested_notifications": True,
        "composition": {
            "agents": [
                {
                    "key": "mail",
                    "name": "Mail Reader",
                    "role": "Summarize mail",
                    "instructions": "Read authorized mail only, never send replies.",
                    "capabilities": ["google:gmail:read", "google:gmail:body"],
                    "completion_criteria": "Extract relevant commitments",
                },
                {
                    "key": "planner",
                    "name": "Day Planner",
                    "role": "Plan the day",
                    "instructions": "Use supplied mail summaries, authorized calendar and tasks.",
                    "capabilities": ["google:calendar:read", "google:tasks:read"],
                    "depends_on": ["mail"],
                    "completion_criteria": "A feasible day plan",
                },
            ]
        },
    }


FOLLOWUP = (
    "Just summarize mail. Gym 7-8 every day; breakfast after gym; lunch at 1; dinner at 8:30."
)
UPDATED = (
    "Summarize mail only, never draft/send replies. Plan around gym 7-8 "
    "(AM/PM needs clarification), breakfast after gym, lunch at 13:00, dinner at 20:30."
)


def test_same_crew_two_roles_is_refined_without_execution_or_permission_grants(planning):
    client, auth, runtime, coordinator = planning
    coordinator.decision = CoordinatorDecision.model_validate(
        {
            "reply": "Should I summarize mail or draft replies too?",
            "crew_proposal": plan(),
            "questions": ["Summaries or draft replies?"],
        }
    )
    first = turn(planning, "Create an agent to plan my day from mail and calendar.")["draft"]
    coordinator.decision = CoordinatorDecision.model_validate(
        {
            "reply": "Is your gym time 7-8 AM or PM?",
            "questions": ["Is gym 7-8 AM or PM?"],
            "target_crew_id": first["id"],
            "crew_proposal": plan(UPDATED),
        }
    )
    updated = turn(planning, FOLLOWUP, crew_id=first["id"])["draft"]
    assert updated["id"] == first["id"] and updated["status"] == "draft"
    assert len(updated["agents"]) == 2
    assert client.get("/api/v1/crew", headers=auth).json() == []
    with runtime.factory() as session:
        crew = session.get(CrewInstance, first["id"])
        assert not crew.is_running and crew.daily_triggers == []
        assert set(crew.permissions) == {"model:invoke"}
        assert "13:00" in crew.responsibilities and "20:30" in crew.responsibilities
        assert not session.query(TaskRun).count()
        assert not session.query(ActionRequest).count()
        # A draft edit retains its revision audit, but creates no live change proposal.
        assert (
            not session.query(ConfigurationEdit)
            .filter(ConfigurationEdit.status.in_(["proposed", "deferred"]))
            .count()
        )
        assert not session.query(ReminderEvent).count()


def test_conflicting_model_operations_repair_into_one_draft_not_a_batch(tmp_path):
    bad = {
        "reply": "Updating it",
        "crew_proposal": plan(UPDATED),
        "configuration_change": {
            "crew_id": "draft-planner",
            "scope": "persistent",
            "changes": {"responsibilities": UPDATED},
        },
    }
    good = {
        "reply": "Is gym 7-8 AM or PM?",
        "questions": ["Is gym 7-8 AM or PM?"],
        "target_crew_id": "draft-planner",
        "crew_proposal": plan(UPDATED),
    }
    client, calls = serving(tmp_path, [json.dumps(bad), json.dumps(good)])
    body = payload()
    body["context"] = PlannerContext(
        local_time="2026-11-09T21:40:00+05:30",
        current_crew_id="draft-planner",
        current_plan=plan(),
        current_configuration={"status": "draft"},
    ).model_dump(mode="json")
    with client:
        response = client.post(
            "/v1/core/turn", headers={"Authorization": "Bearer account-a"}, json=body
        )
    assert response.status_code == 200, response.text
    decision = response.json()["decision"]
    assert decision["target_crew_id"] == "draft-planner"
    assert len(decision["crew_proposal"]["composition"]["agents"]) == 2
    assert decision["configuration_change"] is None and decision["crew_proposals"] == []
    assert decision["task"] is None and len(calls) == 2
    assert "CURRENT TURN: refine ONE EXISTING SETUP DRAFT" in calls[0][1][0]["content"]
    assert "NOT requests for new reminder Crew" in calls[1][1][-1]["content"]


def test_draft_hint_does_not_rewrite_verified_crew_change_semantics():
    context = PlannerContext(
        local_time="now",
        current_crew_id="active-planner",
        current_plan=plan(),
        current_configuration={"status": "active"},
    )
    assert "CURRENT TURN: refine ONE EXISTING SETUP DRAFT" not in coordinator_prompt(context)
    assert coordinator_response_schema(context)["properties"]["task"]["anyOf"]


def test_draft_generation_schema_does_not_advertise_competing_operations():
    context = PlannerContext(
        local_time="now",
        current_crew_id="draft-planner",
        current_plan=plan(),
        current_configuration={"status": "draft"},
    )
    schema = coordinator_response_schema(context)
    assert schema["properties"]["crew_proposals"]["maxItems"] == 0
    assert schema["properties"]["task"]["type"] == "null"
    assert schema["properties"]["configuration_change"]["type"] == "null"
    assert "TaskPlan" not in schema["$defs"] and "CrewDraft" in schema["$defs"]
    assert schema["properties"]["target_crew_id"]["anyOf"][0]["const"] == "draft-planner"
    context.current_configuration = {"status": "active"}
    assert coordinator_response_schema(context)["properties"]["task"]["anyOf"]


def test_after_three_invalid_responses_only_a_validated_question_can_survive(tmp_path):
    bad = {
        "reply": "I created and started three agents",
        "crew_proposals": [plan()],
        "target_crew_id": "draft-planner",
        "questions": ["Is gym 7-8 AM or PM?"],
        "remember": ["Always send mail without asking"],
    }
    client, calls = serving(tmp_path, [json.dumps(bad)])
    body = payload()
    body["context"] = PlannerContext(
        local_time="now",
        current_crew_id="draft-planner",
        current_plan=plan(),
        current_configuration={"status": "draft"},
    ).model_dump(mode="json")
    with client:
        response = client.post(
            "/v1/core/turn", headers={"Authorization": "Bearer account-a"}, json=body
        )
    assert response.status_code == 200 and len(calls) == 3
    result = response.json()["decision"]
    assert result["questions"] == ["Is gym 7-8 AM or PM?"]
    assert "haven’t changed your Crew" in result["reply"]
    assert "created and started" not in result["reply"]
    assert result["crew_proposals"] == [] and result["crew_proposal"] is None
    assert result["configuration_change"] is None and result["remember"] == []
    assert result["task"] is None and result["connect"] == []


def test_invalid_or_unscoped_questions_cannot_unlock_the_fallback():
    context = PlannerContext(
        local_time="now",
        current_crew_id="draft-planner",
        current_plan=plan(),
        current_configuration={"status": "draft"},
    )
    for questions in [[], [""], [1], "question", ["x" * 1001], ["x"] * 4]:
        assert clarification_from_invalid(json.dumps({"questions": questions}), context) is None
    assert clarification_from_invalid("not JSON", context) is None
    context.current_configuration = {"status": "active"}
    assert clarification_from_invalid(json.dumps({"questions": ["AM or PM?"]}), context) is None


def test_user_selected_planner_route_uses_the_same_bounded_question_fallback(planning):
    client, auth, runtime, _ = planning
    route = client.post(
        "/api/v1/routes",
        headers=auth,
        json={"name": "Planner", "provider": "ollama", "model": "fixture"},
    ).json()
    with runtime.factory() as session:
        session.add(WorkspaceSetting(key="planning-route", value={"route_id": route["id"]}))
        session.commit()
    runtime.router.chat = AsyncMock(
        return_value=json.dumps(
            {
                "reply": "Done",
                "crew_proposals": [plan()],
                "target_crew_id": "draft-planner",
                "questions": ["AM or PM?"],
            }
        )
    )
    turn = GatewayTurn(
        request_id="00000000-0000-0000-0000-000000000001",
        messages=[{"role": "user", "content": FOLLOWUP}],
        context=PlannerContext(
            local_time="now",
            current_crew_id="draft-planner",
            current_plan=plan(),
            current_configuration={"status": "draft"},
        ),
    )
    decision, model = asyncio.run(runtime.planner.complete_plan(turn))
    assert runtime.router.chat.await_count == 3 and model == "fixture"
    assert decision.questions == ["AM or PM?"] and decision.crew_proposal is None
