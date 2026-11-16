"""One request, one thing: "improve my career prospects" started a task AND drafted a weekly
"Signal Scout", and the task stalled on "Needs a connected account" saving to Google Tasks."""

from mavo.task_plan import TaskStep
from tests.test_planner import planning, turn  # noqa: F401
from tests.test_scenarios import decide
from tests.test_tasks import allow_browser, plan, scripted, step

SCOUT = {
    "template_id": "research",
    "name": "Signal Scout",
    "responsibilities": "Watch hiring signals for 2027 graduates.",
    "trigger_type": "interval",
    "review_interval_minutes": 10080,
}


def test_a_job_done_now_does_not_also_draft_an_agent(planning):  # noqa: F811
    client, auth, runtime, _ = planning
    scripted(runtime, helper={"writer": "A six-month plan."})
    client.post(
        "/api/v1/routes", headers=auth, json={"name": "A", "provider": "ollama", "model": "m"}
    ).raise_for_status()
    allow_browser(client, auth)
    decide(
        planning,
        reply="I'll research this and draft your plan.",
        task=plan(step("a", role="writer"), deliver="The plan"),
        crew_proposal=SCOUT,
    )
    coordinator = planning[3]
    first = coordinator.decision
    checked = first.model_copy(update={"crew_proposal": None, "crew_proposals": []})
    answers = iter([first, checked])

    async def complete(payload):
        coordinator.calls.append(payload)
        return next(answers), "minimax-test"

    coordinator.complete = complete
    result = turn(
        planning,
        "I want to improve my career prospects over the next six months. Begin any safe, "
        "reversible research or planning tasks.",
    )
    assert result["task_run_id"] and result["draft_id"] is None
    # The model was asked once, and it decided: no agent was asked for.
    assert len(coordinator.calls) == 2
    assert "not the person" in coordinator.calls[-1].messages[-1].content
    from mavo.database import CrewInstance

    with runtime.factory() as session:
        assert not session.query(CrewInstance).filter_by(name="Signal Scout").count()


def test_asking_for_something_ongoing_keeps_its_agent(planning):  # noqa: F811
    """When the model, asked once, keeps the agent, Mavo keeps it: no keyword list decides."""
    client, auth, runtime, _ = planning
    scripted(runtime, helper={"writer": "Found two."})
    client.post(
        "/api/v1/routes", headers=auth, json={"name": "A", "provider": "ollama", "model": "m"}
    ).raise_for_status()
    allow_browser(client, auth)
    decide(
        planning,
        reply="I'll look now and keep watching weekly.",
        task=plan(step("a", role="writer"), deliver="The list"),
        crew_proposal=SCOUT,
    )
    result = turn(planning, "Find internships now and keep an eye on new ones every week")
    assert result["task_run_id"] and result["draft_id"]
    assert len(planning[3].calls) == 2


def test_a_one_time_agent_writes_out_what_to_add_instead_of_stalling():
    saver = TaskStep.model_validate(
        {
            "id": "s4",
            "kind": "agent",
            "title": "Save the plan as tasks and notes",
            "instructions": "Save the plan.",
            "actions": ["tasks.create", "gmail.send", "notes.write"],
        }
    )
    assert saver.actions == ["notes.write"]
    assert "list exactly what they should add" in saver.instructions
    only_tasks = TaskStep.model_validate(
        {
            "id": "s5",
            "kind": "agent",
            "title": "Add tasks",
            "instructions": "Add them.",
            "actions": ["tasks.create"],
        }
    )
    assert only_tasks.actions == [] and only_tasks.kind == "helper"
