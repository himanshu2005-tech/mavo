"""One large ceiling, not many small budgets. Small per-crew call counts (set by a model when the
crew was designed) kept cutting good work short: "It needed one more step to finish", "2
measured + 3 spare = 5 calls; ceiling 3". Now only a runaway is stopped."""

from mavo.crew_composition import CrewComposition
from mavo.crew_workers import CrewWorkerRuntime, per_run_calls
from mavo.database import CrewInstance
from mavo.limits import DAY_MAX, RUN_MAX, day_limit
from tests.test_crew_v2 import seed
from tests.test_multi_agent import make_session


def agent(key, calls):
    return {
        "key": key,
        "name": key.title(),
        "role": "Worker",
        "instructions": "Do the step.",
        "max_model_calls": calls,
        "completion_criteria": "Done.",
    }


def test_a_small_crew_budget_no_longer_limits_a_run_or_a_day():
    crew = CrewInstance(max_model_calls=1, daily_call_limit=3)
    assert per_run_calls(crew) == RUN_MAX
    assert day_limit(crew) == DAY_MAX
    # A very frequent schedule asked for more than the ceiling: that is kept.
    assert day_limit(CrewInstance(daily_call_limit=DAY_MAX + 500)) == DAY_MAX + 500


def test_a_model_written_call_count_never_refuses_a_crew_design():
    design = CrewComposition.model_validate(
        {
            "agents": [agent("a", 99), agent("b", "lots"), agent("c", 20)],
            "limits": {"model_calls": 3},
        }
    )
    assert [a.max_model_calls for a in design.agents] == [40, 3, 20]


def test_crews_stopped_by_the_old_small_budgets_carry_on_by_themselves(tmp_path):
    factory = make_session(tmp_path)
    with factory() as session:
        run_stuck = seed(
            session,
            runtime_state="needs_input",
            observation={
                "limit": {"kind": "run", "current": 3, "suggested": 5},
                "question": "Allow 5?",
            },
        )
        day_paused = CrewInstance(
            name="Daily",
            template_id="research",
            responsibilities="x",
            provider="ollama",
            model="fixture",
            route_id=run_stuck.route_id,
            is_running=True,
            runtime_state="budget_paused",
            daily_call_limit=12,
            daily_calls_used=12,
        )
        asking = CrewInstance(
            name="Asking",
            template_id="research",
            responsibilities="x",
            provider="ollama",
            model="fixture",
            route_id=run_stuck.route_id,
            is_running=True,
            runtime_state="needs_input",
            observation={"question": "Which label counts as urgent?"},
        )
        session.add_all([day_paused, asking])
        session.commit()
        ids = run_stuck.id, day_paused.id, asking.id
    assert CrewWorkerRuntime(factory, None).retry_per_run_limits() == 2
    with factory() as session:
        run_stuck, day_paused, asking = (session.get(CrewInstance, i) for i in ids)
        assert run_stuck.runtime_state == day_paused.runtime_state == "waiting"
        assert "limit" not in run_stuck.observation and "question" not in run_stuck.observation
        assert asking.runtime_state == "needs_input"  # a real question still waits for the person
