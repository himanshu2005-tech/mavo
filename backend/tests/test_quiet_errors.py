"""The person hears about trouble only when there is something for them to do, and then in one
plain sentence. A run that failed and is being retried says nothing; a "try later" result says
nothing; an agent that stopped says so once, without technical text; an AI connection that is
down says so once for all the agents on it."""

import pytest
from sqlalchemy import select

from mavo.database import CrewInstance, ReminderEvent
from mavo.model_router import ModelProviderError
from mavo.plain_errors import kind_of, quiet, retrying, stopped, waits_itself
from tests.test_crew_matrix import answer, role_of, run_cycle, start
from tests.test_crew_v2 import composition, spec
from tests.test_planner import planning  # noqa: F401

TECHNICAL = (
    "checkpoint",
    "validation",
    "mail_decisions",
    "string_too_short",
    "Traceback",
    "(402)",
    "JSON",
)


@pytest.mark.parametrize(
    "text,kind",
    [
        ("AIRouter could not complete the request (402): Insufficient credits", "ai"),
        ("The provider rejected the saved API key", "ai"),
        ("AIRouter is not reachable", "ai"),
        ("The model took too long to respond.", "slow"),
        (
            "The model returned an invalid work checkpoint "
            "(mail_decisions.0.summary: string_too_short).",
            "answer",
        ),
        ("The mail review left out 10 of the 10 supplied messages: a (b: c).", "answer"),
        ("The agent ran out of steps before it finished.", "steps"),
        ("This agent's daily model-call budget is used up.", "budget_day"),
        ("Mavo's browser stayed busy with another job. Try again shortly.", "browser"),
        (
            "Credits Watch: The agent ran out of steps before it finished.",
            "steps",
        ),  # a name, not money
        ("Something nobody expected", "other"),
    ],
)
def test_internal_errors_are_sorted_into_a_few_plain_kinds(text, kind):
    assert kind_of(text)[0] == kind
    for message in (retrying(text, 2), stopped("Inbox Digest", text)):
        assert not any(word in message for word in TECHNICAL), message
        assert len(message) < 220


def test_what_clears_by_itself_is_never_a_needs_you():
    assert waits_itself("Mavo's browser stayed busy with another job. Try again shortly.")
    assert waits_itself("Reader: This agent's daily model-call budget is used up.")
    assert not waits_itself("linkedin.com needs you to sign in. Save a login with the key button.")
    assert quiet(
        "news.ycombinator.com is limiting requests right now, so it could not be read this time."
    )


def alerts(runtime, crew_id=None):
    with runtime.factory() as session:
        query = select(ReminderEvent).where(ReminderEvent.kind == "crew_update")
        if crew_id:
            query = query.where(ReminderEvent.crew_id == crew_id)
        return [e.message for e in session.scalars(query.order_by(ReminderEvent.created_at))]


def test_a_retried_failure_is_silent_and_a_stopped_agent_says_so_once_in_plain_words(planning):
    async def chat(provider, model, messages, system=None, credential_id=None):
        return "definitely not json"

    team = composition(spec("fetch"), limits={"model_calls": 12})
    client, auth, runtime, crew_id = start(planning, team, chat)
    for attempt in (1, 2):
        assert run_cycle(client, auth, runtime, crew_id)["status"] == "failed"
        with runtime.factory() as session:
            crew = session.get(CrewInstance, crew_id)
            assert crew.runtime_state == "waiting"
            assert crew.last_runtime_error.startswith(
                "Had a hiccup (the AI model's answer could not be used)"
            )
            assert "checkpoint" in crew.observation["error_detail"]  # kept for the details
        assert alerts(runtime, crew_id) == [], attempt  # nothing on the desktop
    assert run_cycle(client, auth, runtime, crew_id)["status"] == "failed"
    [only] = alerts(runtime, crew_id)
    assert only.startswith(
        "Internships is paused: the AI model's answer could not be used, three runs in a row."
    )
    assert "Press Start" in only and not any(word in only for word in TECHNICAL)


def test_an_ai_connection_that_is_down_is_told_once_for_all_its_agents(planning):
    async def chat(provider, model, messages, system=None, credential_id=None):
        raise ModelProviderError(
            "AIRouter could not complete the request (402): Insufficient credits"
        )

    client, auth, runtime, first = start(
        planning, composition(spec("a"), limits={"model_calls": 12}), chat
    )
    with runtime.factory() as session:
        one = session.get(CrewInstance, first)
        from mavo.crew_composition import set_composition

        two = CrewInstance(
            name="Second",
            template_id="research",
            responsibilities="Also works",
            provider=one.provider,
            model=one.model,
            route_id=one.route_id,
            is_running=True,
            trigger_type="interval",
            permissions=["model:invoke", "notifications:show"],
            daily_call_limit=60,
        )
        session.add(two)
        session.flush()
        set_composition(session, two, composition(spec("b"), limits={"model_calls": 12}))
        second = two.id
        session.commit()
    for crew_id in (first, second):
        for _ in range(3):
            run_cycle(client, auth, runtime, crew_id)
    told = alerts(runtime)
    assert len(told) == 1, told  # one alert, not one per agent
    assert (
        "is not working: the AI connection is out of credits" in told[0] and "Settings" in told[0]
    )


def test_a_try_later_result_is_not_sent_to_the_person(planning):
    async def chat(provider, model, messages, system=None, credential_id=None):
        return answer(
            "news.ycombinator.com is limiting requests right now, so it could not be read "
            "this time. Mavo tries again at the next run.",
            notification="read failed",
        )

    team = composition(spec("reader"), limits={"model_calls": 12})
    client, auth, runtime, crew_id = start(planning, team, chat)
    assert run_cycle(client, auth, runtime, crew_id)["status"] == "done"
    assert alerts(runtime, crew_id) == []
    assert role_of("\nRole: X\n") == "x"
