"""A Crew that ends: on its last day, or when its goal is met ("find me a podcast guest" should not
keep searching once a guest said yes). Mavo stops it, keeps it, and says why."""

from datetime import date, timedelta

from mavo.database import CrewInstance, ReminderEvent, utcnow
from tests.test_planner import planning, turn  # noqa: F401
from tests.test_scenarios import checkpoint, decide, route, run_work


def crew_with(planning, name, plan, upstream=None):
    client, auth, runtime, _ = planning
    r = route(client, auth) if not runtime.planner.agent_routes(runtime.factory()) else None
    routes = client.get("/api/v1/routes", headers=auth).json()
    made = client.post(
        "/api/v1/crew",
        headers=auth,
        json={
            "template_id": "research",
            "name": name,
            "route_id": (r or routes[0])["id"],
            "trigger_type": "interval",
            "review_interval_minutes": 60,
            "responsibilities": "Find a famous AI researcher for my podcast.",
            "allow_notifications": True,
        },
    ).json()
    with runtime.factory() as session:
        crew = session.get(CrewInstance, made["id"])
        crew.configuration_plan = {
            **crew.configuration_plan,
            **plan,
            **({"upstream": upstream} if upstream else {}),
        }
        session.commit()
    client.post(f"/api/v1/crew/{made['id']}/start", headers=auth).raise_for_status()
    return made["id"]


def due(runtime, crew_id):
    with runtime.factory() as session:
        crew = session.get(CrewInstance, crew_id)
        crew.runtime_state, crew.next_run_at = "waiting", utcnow() - timedelta(seconds=5)
        session.commit()


def test_the_planner_can_give_a_crew_an_end_and_it_is_saved(planning):
    client, auth, runtime, _ = planning
    r = route(client, auth)
    decide(
        planning,
        reply="Set up.",
        crew_proposal={
            "template_id": "research",
            "name": "Guest Hunter",
            "responsibilities": "Find a famous AI researcher for my podcast.",
            "route_id": r["id"],
            "trigger_type": "interval",
            "review_interval_minutes": 1440,
            "run_until": "2026-11-30",
            "goal": "a guest is booked and in my calendar",
        },
    )
    draft = turn(planning, "Find me a famous guest for my podcast")["draft"]
    saved = draft["configuration_plan"]["draft"]
    assert (
        saved["run_until"] == "2026-11-30"
        and saved["goal"] == "a guest is booked and in my calendar"
    )


def test_past_its_last_day_a_crew_is_finished_and_says_so(planning):
    _, _, runtime, _ = planning
    crew_id = crew_with(
        planning, "Guest Hunter", {"run_until": (date.today() - timedelta(days=1)).isoformat()}
    )
    due(runtime, crew_id)
    chat = run_work(runtime, crew_id, checkpoint("Still looking."))
    assert chat.await_count == 0  # no model call once it is over
    with runtime.factory() as session:
        crew = session.get(CrewInstance, crew_id)
        assert (
            crew.is_running is False and "ran until" in crew.configuration_plan["finished"]["why"]
        )
        said = [
            e.message for e in session.query(ReminderEvent).filter(ReminderEvent.crew_id == crew_id)
        ]
    assert any(m.startswith("Guest Hunter is done: it ran until") for m in said), said


def test_when_its_goal_is_met_it_hands_on_then_stops(planning):
    _, _, runtime, _ = planning
    goal = {
        "goal": "a guest is booked and in my calendar",
        "run_until": (date.today() + timedelta(days=20)).isoformat(),
    }
    hunter = crew_with(planning, "Guest Hunter", goal)
    scheduler = crew_with(planning, "Guest Scheduler", goal, upstream=[hunter])
    due(runtime, hunter)
    # Not yet: it keeps going.
    run_work(runtime, hunter, checkpoint("Asked three people; no reply yet."))
    with runtime.factory() as session:
        assert session.get(CrewInstance, hunter).is_running is True
    due(runtime, hunter)
    done = checkpoint(
        "Andrej said yes for Thursday 14 Nov, 6 pm; it is in your calendar.", goal_met=True
    )
    chat = run_work(runtime, hunter, done)
    system = chat.await_args_list[0].args[3]
    assert "YOUR JOB ENDS WHEN: a guest is booked and in my calendar" in system
    with runtime.factory() as session:
        crew = session.get(CrewInstance, hunter)
        assert crew.is_running is False
        assert crew.configuration_plan["finished"]["why"].startswith(
            "its goal is met (a guest is booked and in my calendar): Andrej said yes"
        )
        heard = session.get(CrewInstance, scheduler).observation[
            "handoffs"
        ]  # handed on before stopping
        assert heard and "Andrej said yes" in heard[-1]["result"]
        said = [
            e.message for e in session.query(ReminderEvent).filter(ReminderEvent.crew_id == hunter)
        ]
    assert any("Guest Hunter is done: its goal is met" in m for m in said), said


def test_a_crew_without_a_goal_cannot_end_itself(planning):
    _, _, runtime, _ = planning
    news = crew_with(planning, "News Watch", {})
    due(runtime, news)
    run_work(
        runtime, news, checkpoint("Three stories.", goal_met=True)
    )  # a lasting duty never ends
    with runtime.factory() as session:
        assert session.get(CrewInstance, news).is_running is True
