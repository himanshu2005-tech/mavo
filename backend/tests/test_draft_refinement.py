"""Draft questionnaire replies cannot become live configuration changes."""

import pytest
from sqlalchemy import select

from mavo.coordinator_contract import PlannerContext, coordinator_prompt
from mavo.database import ConfigurationEdit, CrewInstance, TaskRun
from mavo.schemas import CoordinatorDecision
from tests.test_planner import planning, turn  # noqa: F401


def internship_draft():
    return {
        "template_id": "research",
        "name": "Internship Watch",
        "responsibilities": "Find suitable internships and report useful new openings.",
        "trigger_type": "interval",
        "review_interval_minutes": 60,
        "composition": {
            "agents": [
                {
                    "key": "discover",
                    "name": "Discovery",
                    "role": "Find internships",
                    "instructions": "Read authorized job sources.",
                    "completion_criteria": "Return real listings or a verified zero-result search.",
                },
                {
                    "key": "match",
                    "name": "Matching",
                    "role": "Compare eligibility",
                    "instructions": "Compare listings with the user's profile.",
                    "depends_on": ["discover"],
                    "completion_criteria": "Explain fit and any missing eligibility information.",
                },
            ]
        },
    }


def begin(planning):
    coordinator = planning[3]
    coordinator.decision = CoordinatorDecision.model_validate(
        {
            "reply": "Where should I look?",
            "questions": ["Job portals, Gmail, or both?"],
            "crew_proposal": internship_draft(),
        }
    )
    return turn(planning, "Find internships that suit me and update me.")["draft"]


def patch_decision(crew_id, *, scope="persistent", questions=None, reply="Updated the draft."):
    return CoordinatorDecision.model_validate(
        {
            "reply": reply,
            "questions": questions or [],
            "configuration_change": {
                "crew_id": crew_id,
                "scope": scope,
                "changes": {
                    "responsibilities": "Look for full-stack internships using my chosen sources."
                },
            },
        }
    )


def test_questionnaire_patch_refines_same_draft_and_never_activates_it(planning):
    client, auth, runtime, coordinator = planning
    draft = begin(planning)
    original_agents = [a["id"] for a in draft["agents"]]
    coordinator.decision = patch_decision(draft["id"], questions=["What skills should I match?"])
    updated = turn(planning, "Both job portals and email.")["draft"]
    assert updated["id"] == draft["id"]
    assert updated["status"] == "draft" and not updated["is_running"]
    assert updated["configuration_plan"]["questions"] == ["What skills should I match?"]
    assert [a["id"] for a in updated["agents"]] == original_agents
    assert client.get("/api/v1/crew", headers=auth).json() == []
    context = coordinator.calls[-1].context
    assert context.current_configuration["status"] == "draft"
    assert draft["id"] not in [c.id for c in context.available_crews]
    assert len(coordinator.calls) == 2  # one call per user turn; no hidden repair inference
    with runtime.factory() as session:
        assert session.query(CrewInstance).count() == 1
        assert session.query(TaskRun).count() == 0
        assert not session.scalar(
            select(ConfigurationEdit.id).where(ConfigurationEdit.status == "proposed")
        )


def test_draft_composition_patch_requests_resources_without_granting_them(planning):
    _, _, runtime, coordinator = planning
    draft = begin(planning)
    team = internship_draft()["composition"]
    team["agents"][0]["capabilities"] = ["google:gmail:read"]
    coordinator.decision = CoordinatorDecision.model_validate(
        {
            "reply": "What skills should I match?",
            "questions": ["What skills should I match?"],
            "configuration_change": {
                "crew_id": draft["id"],
                "scope": "persistent",
                "changes": {"composition": team},
            },
        }
    )
    updated = turn(planning, "Both, include email updates.")["draft"]
    assert "gmail:read" in updated["configuration_plan"]["draft"]["requested_google_capabilities"]
    assert "google:gmail:read" not in updated["permissions"]
    with runtime.factory() as session:
        assert session.get(CrewInstance, draft["id"]).status == "draft"


def test_question_in_patch_reply_keeps_questionnaire_open(planning):
    draft = begin(planning)
    coordinator = planning[3]
    coordinator.decision = patch_decision(draft["id"], reply="What experience do you have?")
    updated = turn(planning, "I know Python and React.")["draft"]
    assert updated["configuration_plan"]["questions"] == ["What experience do you have?"]
    assert updated["configuration_plan"]["phase"] == "needs_input"


def test_answered_draft_still_requires_access_and_explicit_add(planning):
    client, auth, _, coordinator = planning
    draft = begin(planning)
    coordinator.decision = patch_decision(draft["id"])
    updated = turn(planning, "Full-stack, beginner, available now, remote.")["draft"]
    assert updated["configuration_plan"]["questions"] == []
    assert updated["status"] == "draft" and not updated["is_running"]
    assert client.get("/api/v1/crew", headers=auth).json() == []


@pytest.mark.parametrize("scope", ["run", "step"])
def test_run_only_draft_patch_does_not_silently_become_permanent(planning, scope):
    draft = begin(planning)
    coordinator = planning[3]
    coordinator.decision = patch_decision(draft["id"], scope=scope)
    updated = turn(planning, "Only for this run.")["draft"]
    assert updated["responsibilities"] == draft["responsibilities"]
    assert updated["configuration_plan"]["questions"]
    assert not updated["is_running"]


def test_user_run_only_scope_cannot_be_overridden_by_model_persistent_scope(planning):
    draft = begin(planning)
    planning[3].decision = patch_decision(draft["id"], scope="persistent")
    updated = turn(planning, "Use those instructions only this run.")["draft"]
    assert updated["responsibilities"] == draft["responsibilities"]
    assert updated["configuration_plan"]["questions"]


def test_an_unrelated_draft_is_not_an_authorized_configuration_target(planning):
    client, auth, runtime, coordinator = planning
    current = begin(planning)
    with runtime.factory() as session:
        other = CrewInstance(
            name="Other setup",
            template_id="research",
            responsibilities="Other",
            provider="unassigned",
            model="",
            status="draft",
        )
        session.add(other)
        session.commit()
        other_id = other.id
    coordinator.decision = patch_decision(other_id)
    from uuid import uuid4

    response = client.post(
        "/api/v1/core/turn", headers=auth, json={"message": "Update it", "request_id": str(uuid4())}
    )
    assert response.status_code == 409
    with runtime.factory() as session:
        assert session.get(CrewInstance, other_id).responsibilities == "Other"
        assert session.get(CrewInstance, current["id"]).status == "draft"


def test_prompt_distinguishes_draft_refinement_from_live_configuration():
    prompt = coordinator_prompt(PlannerContext(local_time="2026-11-09T09:00:00+05:30"))
    assert "DRAFT SETUP IS NOT A LIVE CONFIGURATION CHANGE" in prompt
    assert "Never use configuration_change or delegation for a draft" in prompt
    assert "do not create extra persistent Crew for stages of a new goal" in prompt
