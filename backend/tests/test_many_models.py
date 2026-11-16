"""Work is shared between the person's models, and one model's work is read and checked by
another: "why are we forcing it on just one model"."""

from types import SimpleNamespace

from mavo.crew_composition import CrewComposition, spread_models
from mavo.task_plan import TaskStep
from mavo.tasks import assign_models


def model(id):
    return SimpleNamespace(id=id, name=id.title())


def step(id, kind="helper", **extra):
    return TaskStep(id=id, kind=kind, title=id, instructions=f"Do {id}.", **extra)


def test_task_steps_take_the_models_in_turn_and_a_critic_never_checks_its_own_model():
    a, b, c = model("a"), model("b"), model("c")
    steps = [
        step("research", kind="agent", actions=["browser.search"]),
        step("write", uses=["research"]),
        step("critic", role="critic", uses=["write"]),
        step("deliver", kind="automation", automation="mavo.combine", uses=["critic"]),
    ]
    two = assign_models(steps, [a, b])
    assert [two[s].id for s in ("research", "write", "critic")] == ["a", "b", "a"]
    assert "deliver" not in two  # no model thinks in an automation
    three = assign_models(steps, [a, b, c])
    assert len({three[s].id for s in ("research", "write", "critic")}) == 3
    one = assign_models(steps, [a])
    assert {m.id for m in one.values()} == {"a"}  # one model: it does everything, as before


def agent(key, **extra):
    return {
        "key": key,
        "name": key.title(),
        "role": key,
        "instructions": f"Do {key}.",
        "completion_criteria": "Done.",
        **extra,
    }


def test_crew_agents_share_models_and_a_reviewer_uses_a_different_one():
    team = CrewComposition.model_validate(
        {
            "agents": [
                agent("reader"),
                agent("planner", depends_on=["reader"]),
                agent("checker", depends_on=["planner"], reviews="planner"),
            ]
        }
    )
    spread = {a.key: a.route_id for a in spread_models(team, ["fast", "strong"]).agents}
    assert spread == {"reader": "fast", "planner": "strong", "checker": "fast"}


def test_the_planners_choice_is_kept_unless_a_reviewer_would_check_its_own_model():
    team = CrewComposition.model_validate(
        {
            "agents": [
                agent("writer", route_id="strong"),
                agent("checker", depends_on=["writer"], reviews="writer", route_id="strong"),
                agent("notes", route_id="fast"),
            ]
        }
    )
    spread = {a.key: a.route_id for a in spread_models(team, ["fast", "strong"]).agents}
    assert spread == {"writer": "strong", "checker": "fast", "notes": "fast"}


def test_a_reviewer_listed_first_still_uses_another_model():
    team = CrewComposition.model_validate(
        {
            "agents": [
                agent("checker", reviews="writer", depends_on=["writer"]),
                agent("writer", route_id="fast"),
            ]
        }
    )
    spread = {a.key: a.route_id for a in spread_models(team, ["fast", "strong"]).agents}
    assert spread["checker"] != spread["writer"]


def test_one_model_or_one_agent_is_left_as_it_is():
    solo = CrewComposition.model_validate({"agents": [agent("only")]})
    assert spread_models(solo, ["fast", "strong"]) is solo
    pair = CrewComposition.model_validate({"agents": [agent("a"), agent("b")]})
    assert spread_models(pair, ["fast"]) is pair
