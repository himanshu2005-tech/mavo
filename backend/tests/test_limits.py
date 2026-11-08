"""AI-call limits that a person can understand and an agent can work within: at least two calls a
run (one to decide, one to finish), a day that holds a whole run, plain words when a limit stops
an agent, and a limit raised clearing the note and letting it try again."""

from mavo.crew_workers import MIN_CALLS_PER_RUN, BudgetLimit, per_run_calls
from mavo.database import CrewInstance
from tests.test_tasks import setup


def test_an_agent_always_has_room_to_finish_what_it_starts():
    assert MIN_CALLS_PER_RUN == 2
    assert [per_run_calls(CrewInstance(max_model_calls=n)) for n in (1, 2, 5)] == [2, 2, 5]


def test_the_limit_messages_are_plain_and_carry_the_fix():
    run = BudgetLimit(
        "run",
        "It needed one more step to finish, but it may use only 2 AI calls "
        "in one run. Allow 4 and it will finish next time.",
        2,
        4,
    )
    assert (run.kind, run.current, run.suggested) == ("run", 2, 4)
    assert "model-call budget" not in str(run) and "Adjust this Crew" not in str(run)


def make(client, auth, **extra):
    routes = client.get("/api/v1/routes", headers=auth).json()
    return client.post(
        "/api/v1/crew",
        headers=auth,
        json={
            "template_id": "operator",
            "name": "Motivational Quotes",
            "route_id": routes[0]["id"],
            "responsibilities": "Show me a quote every hour.",
            "trigger_type": "interval",
            "review_interval_minutes": 60,
            **extra,
        },
    ).json()


def test_limits_set_too_low_are_raised_to_what_works(client, auth):
    setup(client, auth)
    crew = make(client, auth)
    changed = client.patch(
        f"/api/v1/crew/{crew['id']}", headers=auth, json={"max_model_calls": 1}
    ).json()
    assert changed["max_model_calls"] == 2
    changed = client.patch(
        f"/api/v1/crew/{crew['id']}",
        headers=auth,
        json={"max_model_calls": 6, "daily_call_limit": 3},
    ).json()
    assert changed["daily_call_limit"] == 6  # a day holds at least one whole run


def test_raising_a_limit_clears_the_note_and_the_agent_tries_again(client, auth):
    runtime = setup(client, auth)
    crew = make(client, auth)
    with runtime.factory() as session:
        row = session.get(CrewInstance, crew["id"])
        row.is_running, row.runtime_state = True, "needs_input"
        row.observation = {
            "question": "It needed one more step to finish, but it may use "
            "only 2 AI calls in one run. Allow 4 and it will finish next time.",
            "limit": {"kind": "run", "current": 2, "suggested": 4},
        }
        session.commit()
    seen = client.get("/api/v1/crew", headers=auth).json()
    stuck = next(c for c in seen if c["id"] == crew["id"])
    assert stuck["limit"] == {"kind": "run", "current": 2, "suggested": 4}
    fixed = client.patch(
        f"/api/v1/crew/{crew['id']}", headers=auth, json={"max_model_calls": 4}
    ).json()
    assert (
        fixed["limit"] is None and fixed["question"] is None and fixed["runtime_state"] == "waiting"
    )


def test_an_old_jargon_note_is_cleared_by_raising_the_limit_too(client, auth):
    runtime = setup(client, auth)
    crew = make(client, auth)
    with runtime.factory() as session:
        row = session.get(CrewInstance, crew["id"])
        row.observation = {
            "question": "This follow-up needs a remaining session model-call "
            "budget. Adjust this Crew's access settings or tell it another way to proceed."
        }
        session.commit()
    fixed = client.patch(
        f"/api/v1/crew/{crew['id']}", headers=auth, json={"max_model_calls": 4}
    ).json()
    assert fixed["question"] is None


# ── A run's own step cap carries on; only the person's allowance stops it ─────────────────────
import json  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy import select  # noqa: E402

from mavo.crew_workers import CrewWorkerRuntime  # noqa: E402
from mavo.database import CrewRun, WorkspaceSetting  # noqa: E402
from tests.test_crew_workers import (  # noqa: E402,F401
    DestinationWeather,
    Router,  # noqa: E402
    add_crew,
    factory,
)


class KeepsAsking(Router):
    """How "Motivational Quotes" was stopped: the model's first answer is not in the right shape
    (one repair, a second call), and the repaired answer needs a look-up (a third call), more than
    one run's own two calls, though the person allowed 4."""

    async def chat(self, provider, model, messages, system, credential_id=None):
        self.calls.append((messages, system))
        if len(self.calls) % 2:
            return "Sure! Here is a quote for you."  # not a work checkpoint: repaired once
        return json.dumps(
            {
                "state": "continue",
                "summary": f"Step {len(self.calls)}",
                "next_step": "Look again",
                "notification": "",
                "weather_requests": [{"query": "Chennai, Tamil Nadu, India", "country_code": "IN"}],
            }
        )


@pytest.mark.asyncio
async def test_a_run_that_needs_more_steps_carries_on_until_the_persons_allowance(factory):
    add_crew(
        factory,
        permissions=["model:invoke", "weather:read"],
        daily_call_limit=20,
        max_model_calls=4,
    )
    with factory() as session:
        session.add(
            WorkspaceSetting(
                key="default-resource-access", value={"weather": True, "location": False}
            )
        )
        session.commit()
    router = KeepsAsking()
    runtime = CrewWorkerRuntime(factory, router, weather=DestinationWeather())
    await runtime.work("crew")
    with factory() as session:
        crew = session.get(CrewInstance, "crew")
        # Its own two calls used, 4 allowed: it carries on in a fresh run, nobody is asked.
        assert crew.runtime_state == "waiting" and crew.question is None and crew.limit is None
        assert crew.observation["automation_steps"] == 2
    await runtime.work("crew")
    with factory() as session:
        crew = session.get(CrewInstance, "crew")
        assert sum(r.model_calls for r in session.scalars(select(CrewRun))) == 4
        # Now the person's allowance (4) is really used: said plainly, with a fix that helps.
        assert crew.runtime_state == "needs_input" and crew.limit == {
            "kind": "run",
            "current": 4,
            "suggested": 6,
        }
    assert len(router.calls) == 4


def test_a_per_run_note_gets_a_fresh_try_when_mavo_starts(factory):
    from mavo.crew_workers import CrewWorkerRuntime as Runtime

    add_crew(
        factory,
        runtime_state="needs_input",
        observation={
            "question": "It needed one more step to finish, but it may use "
            "only 4 AI calls in one run. Allow 6 and it will finish next time.",
            "limit": {"kind": "run", "current": 4, "suggested": 6},
            "automation_steps": 4,
        },
    )
    assert Runtime(factory, Router()).retry_per_run_limits() == 1
    with factory() as session:
        crew = session.get(CrewInstance, "crew")
        assert (
            crew.runtime_state == "waiting"
            and crew.limit is None
            and crew.question is None
            and crew.observation["automation_steps"] == 0
        )


# ── A timed agent first works at its time, however it is started ──────────────────────────────
from datetime import UTC, datetime, timedelta  # noqa: E402

from mavo.crew_workers import first_run_at  # noqa: E402
from tests.test_multi_agent import batch  # noqa: E402
from tests.test_multi_agent import crew as proposal  # noqa: E402
from tests.test_planner import planning, turn  # noqa: E402,F401


def local_hm(value):
    when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    when = when if when.tzinfo else when.replace(tzinfo=UTC)
    return when.astimezone().strftime("%H:%M")


def test_the_first_run_is_at_its_time_of_day_or_now_if_it_has_none():
    now = datetime.now(UTC)
    timed = CrewInstance(trigger_type="interval", schedule_time="08:20")
    assert local_hm(first_run_at(timed, now)) == "08:20" and first_run_at(timed, now) > now
    assert first_run_at(CrewInstance(trigger_type="interval", schedule_time=None), now) == now


def test_an_agent_made_in_chat_waits_for_its_time_then_edits_keep_it(planning):
    client, auth, runtime, coordinator = planning
    client.post(
        "/api/v1/routes",
        headers=auth,
        json={"name": "Agent", "provider": "ollama", "model": "test"},
    )
    coordinator.decision = batch(
        proposal(
            "Daily Suspense Poem",
            trigger_type="interval",
            schedule_time="08:20",
            review_interval_minutes=1440,
            requested_notifications=True,
        )
    )
    draft = turn(planning, "Send me a suspense poem every day at 8:20")["crews"][0]
    live = client.post(
        f"/api/v1/crew/{draft['id']}/access", headers=auth, json={"notifications": True}
    ).json()
    assert (
        live["is_running"] and local_hm(live["next_run_at"]) == "08:20"
    )  # not the moment it was added
    edited = client.patch(
        f"/api/v1/crew/{draft['id']}",
        headers=auth,
        json={"responsibilities": "Send me a short suspense poem."},
    ).json()
    assert local_hm(edited["next_run_at"]) == "08:20"  # saving does not send today's poem early
    with runtime.factory() as session:
        row = session.get(CrewInstance, draft["id"])
        row.runtime_state = "needs_input"
        session.commit()
    unstuck = client.patch(
        f"/api/v1/crew/{draft['id']}", headers=auth, json={"max_model_calls": 4}
    ).json()
    now = datetime.now(UTC)
    when = datetime.fromisoformat(unstuck["next_run_at"].replace("Z", "+00:00"))
    assert (when if when.tzinfo else when.replace(tzinfo=UTC)) <= now + timedelta(
        seconds=5
    )  # unsticking it goes on now
