# ruff: noqa: F811, E501
"""End-to-end scenarios that mirror what people actually ask Mavo for. Each drives the real HTTP
API, planner, access rules, worker and action engine. Only the two model calls (the publisher's
planner and the user's agent connection) and the browser window are stubbed."""

import asyncio
import json
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

from mavo.database import CrewInstance, CrewRun
from mavo.reminders import create_due_reminders
from mavo.schemas import CoordinatorDecision
from tests.test_planner import planning, turn  # noqa: F401

IST = timezone(timedelta(hours=5, minutes=30))


def route(client, auth):
    return client.post(
        "/api/v1/routes", headers=auth, json={"name": "Agent", "provider": "ollama", "model": "t"}
    ).json()


def decide(planning, **decision):
    planning[3].decision = CoordinatorDecision.model_validate({"reply": "Done.", **decision})


def verify(client, auth, crew, **access):
    response = client.post(f"/api/v1/crew/{crew['id']}/access", headers=auth, json=access)
    assert response.status_code == 200, response.text
    return response.json()


def run_work(runtime, crew_id, reply):
    runtime.router.chat = AsyncMock(return_value=json.dumps(reply))
    asyncio.run(runtime.work(crew_id))
    return runtime.router.chat


def checkpoint(summary="Done", state="wait_for_next_review", **extra):
    return {"state": state, "summary": summary, "next_step": "Wait", **extra}


# 1. "Remind me to eat at 9, 1 and 7:45" ---------------------------------------------------


def test_work_at_a_time_of_day_is_a_crew_that_runs_daily_at_that_time(planning):
    """ "Every morning at 8, send me the top AI news": the assistant proposed a news Crew with a
    daily time, and it was refused because a daily time was only for plain reminders."""
    client, auth, runtime, _ = planning
    r = route(client, auth)
    decide(
        planning,
        crew_proposal={
            "template_id": "research",
            "name": "Morning AI news",
            "responsibilities": "Every morning, the top AI news in five lines.",
            "route_id": r["id"],
            "daily_triggers": [{"time": "08:00", "message": "AI news"}],
        },
    )
    draft = turn(planning, "Every morning at 8, send me the top AI news")["draft"]
    assert draft["trigger_type"] == "interval" and draft["schedule_time"] == "08:00"
    assert not draft["daily_triggers"]
    # "send me": its reports go to the desktop, so desktop updates are part of the setup.
    assert draft["configuration_plan"]["draft"]["requested_notifications"] is True
    crew = verify(client, auth, draft, notifications=True)
    assert crew["is_running"], (
        crew["runtime_state"],
        crew.get("last_runtime_error"),
        crew.get("configuration_plan", {}).get("phase"),
    )
    # It works, then waits for 8 o'clock, not for "1440 minutes after now".
    run_work(runtime, crew["id"], checkpoint("Nothing new."))
    with runtime.factory() as session:
        stored = session.get(CrewInstance, crew["id"])
        after = stored.next_run_at.replace(tzinfo=stored.next_run_at.tzinfo or UTC).astimezone()
        assert (after.hour, after.minute) == (8, 0)
        assert timedelta(0) < after - datetime.now().astimezone() <= timedelta(days=1)
        # And it is not a plain reminder.
        assert create_due_reminders(session, after) == []


def test_meal_reminders_one_crew_three_times(planning):
    client, auth, runtime, _ = planning
    r = route(client, auth)
    decide(
        planning,
        crew_proposal={
            "template_id": "schedule",
            "name": "Meals",
            "responsibilities": "Remind me about meals",
            "route_id": r["id"],
            "daily_triggers": [
                {"time": "09:00", "message": "Have breakfast"},
                {"time": "13:00", "message": "Have lunch"},
                {"time": "19:45", "message": "Have dinner"},
            ],
        },
    )
    draft = turn(planning, "Remind me about breakfast, lunch and dinner")["draft"]
    assert draft["status"] == "draft" and not draft["is_running"]
    assert [t["time"] for t in draft["daily_triggers"]] == ["09:00", "13:00", "19:45"]
    crew = verify(client, auth, draft)
    assert crew["is_running"] and crew["status"] == "active"
    assert len(client.get("/api/v1/crew", headers=auth).json()) == 1

    with runtime.factory() as session:
        at = lambda h, m: datetime(2026, 11, 3, h, m, tzinfo=IST)  # noqa: E731
        assert create_due_reminders(session, at(8, 59)) == []
        assert [r.message for r in create_due_reminders(session, at(9, 0))] == ["Have breakfast"]
        assert {r.message for r in create_due_reminders(session, at(13, 5))} == {
            "Have breakfast",
            "Have lunch",
        }
    # The overlay asks the API what is due, then dismisses it.
    due = client.get("/api/v1/reminders/due", headers=auth).json()
    assert {d["message"] for d in due} <= {"Have breakfast", "Have lunch", "Have dinner"}
    for item in due:
        assert (
            client.post(f"/api/v1/reminders/{item['id']}/dismiss", headers=auth).status_code == 204
        )


def test_user_edits_a_trigger_and_stops_and_restarts_the_crew(planning):
    client, auth, runtime, _ = planning
    r = route(client, auth)
    decide(
        planning,
        crew_proposal={
            "template_id": "schedule",
            "name": "Meals",
            "responsibilities": "Meals",
            "route_id": r["id"],
            "daily_triggers": [{"time": "09:00", "message": "Breakfast"}],
        },
    )
    crew = verify(client, auth, turn(planning, "meals")["draft"])
    edited = client.patch(
        f"/api/v1/crew/{crew['id']}",
        headers=auth,
        json={
            "daily_triggers": [
                {"time": "08:30", "message": "Early breakfast"},
                {"time": "20:00", "message": "Dinner", "weekdays": [0, 1, 2, 3, 4]},
            ]
        },
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["schedule_time"] == "08:30"
    assert (
        client.patch(
            f"/api/v1/crew/{crew['id']}",
            headers=auth,
            json={"daily_triggers": [{"time": "08:30"}, {"time": "08:30"}]},
        ).status_code
        == 422
    )
    stopped = client.post(f"/api/v1/crew/{crew['id']}/stop", headers=auth).json()
    assert not stopped["is_running"]
    with runtime.factory() as session:
        assert create_due_reminders(session, datetime(2026, 11, 3, 21, 0, tzinfo=IST)) == []
    assert client.post(f"/api/v1/crew/{crew['id']}/start", headers=auth).json()["is_running"]


# 2. "I'm flying Hyderabad to Chennai on Friday" ---------------------------------------------


def test_trip_chain_hands_the_forecast_to_the_next_crew(planning):
    client, auth, runtime, _ = planning
    r = route(client, auth)
    decide(
        planning,
        crew_proposals=[
            {
                "template_id": "travel",
                "name": "Trip watch",
                "route_id": r["id"],
                "responsibilities": "Watch the Chennai forecast for my Friday flight from Hyderabad",
                "trigger_type": "interval",
                "requested_weather": True,
                "requested_notifications": True,
                "max_model_calls": 4,
                "daily_call_limit": 8,
            },
            {
                "template_id": "travel",
                "name": "Leave for airport",
                "route_id": r["id"],
                "responsibilities": "From the forecast, say when to leave and suggest a cab",
                "trigger_type": "interval",
                "requested_notifications": True,
                "depends_on": ["Trip watch"],
            },
        ],
    )
    result = turn(planning, "I'm flying Hyderabad to Chennai on Friday")
    assert [c["name"] for c in result["crews"]] == ["Trip watch", "Leave for airport"]
    watch, leave = result["crews"]

    # Weather must be on in Resources first: the missing list says so, nothing runs.
    blocked = verify(client, auth, watch, weather=True, notifications=True)
    assert not blocked["is_running"]
    assert any("Weather" in m for m in blocked["configuration_plan"]["missing"])
    client.put("/api/v1/pool/weather", headers=auth, json={"level": "read"})
    runtime.planner.next_reconcile = 0
    runtime.planner.reconcile_pending()
    watch = next(
        c for c in client.get("/api/v1/crew", headers=auth).json() if c["name"] == "Trip watch"
    )
    assert watch["is_running"] and "weather:read" in watch["permissions"]
    leave = verify(client, auth, leave, notifications=True)
    assert leave["is_running"]

    run_work(runtime, watch["id"], checkpoint("Rain likely in Chennai on Friday afternoon."))
    with runtime.factory() as session:
        downstream = session.get(CrewInstance, leave["id"])
        assert downstream.observation["handoffs"][0]["from"] == "Trip watch"
    chat = run_work(runtime, leave["id"], checkpoint("Leave by 11:30. Consider booking a cab."))
    prompt = chat.call_args.args[2][0]["content"]
    assert "Rain likely in Chennai" in prompt and "UNTRUSTED DATA" in prompt


# 3. Mail: reading is fine, sending needs the right level --------------------------------------


def test_mail_helper_cannot_send_while_gmail_is_read_only(planning):
    client, auth, runtime, _ = planning
    client.put("/api/v1/pool/gmail", headers=auth, json={"level": "read"})
    decide(
        planning,
        crew_proposal={
            "template_id": "communication",
            "name": "Reply helper",
            "responsibilities": "Reply to my landlord",
            "trigger_type": "gmail_message",
            "requested_google_capabilities": ["gmail:read", "gmail:send"],
            "requested_actions": [{"operation": "gmail.send", "reason": "reply"}],
        },
    )
    draft = turn(planning, "Reply to my landlord's emails")["draft"]
    missing = draft["configuration_plan"]["missing"]
    assert any("read only" in m for m in missing)
    assert not draft["is_running"]
    client.put("/api/v1/pool/gmail", headers=auth, json={"level": "write"})
    runtime.planner.next_reconcile = 0
    runtime.planner.reconcile_pending()
    after = client.get("/api/v1/crew-drafts", headers=auth).json()[0]
    assert not any("read only" in m for m in after["configuration_plan"]["missing"])


# 4. "Search the web for ..." -----------------------------------------------------------------


def web_crew(planning, domains):
    client, auth, runtime, _ = planning
    r = route(client, auth)
    decide(
        planning,
        crew_proposal={
            "template_id": "research",
            "name": "Web research",
            "route_id": r["id"],
            "responsibilities": "Find the cheapest Hyderabad to Chennai train fares",
            "trigger_type": "manual",
            "requested_actions": [
                {"operation": "browser.search", "reason": "find fares", "domains": domains},
                {"operation": "browser.read", "reason": "read results", "domains": domains},
            ],
        },
    )
    draft = turn(planning, "Search the web for train fares to Chennai")["draft"]
    rule = {"enabled": True, "mode": "restricted", "domains": domains}
    return verify(
        client,
        auth,
        draft,
        route_id=r["id"],
        actions={"browser.search": rule, "browser.read": rule},
    ), runtime


def test_web_search_runs_through_policy_and_reads_results(planning):
    crew, runtime = web_crew(planning, ["html.duckduckgo.com"])
    assert crew["is_running"]
    seen = []

    async def fake_browser(operation, args, rule, owner, scope_guard=None):
        seen.append((operation, args))
        return {"url": "https://html.duckduckgo.com/html/", "title": "results", "untrusted": True}

    runtime.actions.desktop.browser = fake_browser
    run_work(
        runtime,
        crew["id"],
        checkpoint(
            "Searching",
            state="continue",
            actions=[
                {
                    "operation": "browser.search",
                    "arguments": {"query": "hyderabad chennai train fare"},
                },
                {"operation": "browser.read", "arguments": {}},
            ],
        ),
    )
    assert [op for op, _ in seen] == ["browser.search", "browser.read"]
    assert seen[0][1] == {"query": "hyderabad chennai train fare"}


def test_web_search_is_refused_when_its_engine_is_not_an_allowed_site(planning):
    crew, runtime = web_crew(planning, ["example.com"])
    calls = []

    async def fake_browser(*args, **kwargs):
        calls.append(args)
        return {}

    runtime.actions.desktop.browser = fake_browser
    run_work(
        runtime,
        crew["id"],
        checkpoint(
            "Searching",
            state="continue",
            actions=[{"operation": "browser.search", "arguments": {"query": "anything"}}],
        ),
    )
    assert calls == []
    with runtime.factory() as session:
        stored = session.get(CrewInstance, crew["id"])
        assert stored.runtime_state == "needs_input"
        assert "html.duckduckgo.com" in stored.observation["question"]


# 5. "Warn me when my disk is nearly full" ------------------------------------------------------


def test_disk_watch_uses_no_model_calls(planning):
    client, auth, runtime, _ = planning
    r = route(client, auth)
    decide(
        planning,
        crew_proposal={
            "template_id": "computer",
            "name": "Disk watch",
            "route_id": r["id"],
            "responsibilities": "Warn me when C: has under 5 GB free",
            "trigger_type": "interval",
            "review_interval_minutes": 15,
            "requested_device_metrics": True,
            "requested_notifications": True,
            "device_monitor": {
                "kind": "storage",
                "drive": "C:",
                "notifications": "alerts",
                "min_free_gb": 5,
            },
        },
    )
    draft = turn(planning, "Warn me when my disk is nearly full")["draft"]
    crew = verify(client, auth, draft, device_metrics=True, notifications=True)
    assert crew["is_running"]
    chat = AsyncMock(side_effect=AssertionError("Disk checks must not call a model"))
    runtime.router.chat = chat
    asyncio.run(runtime.work(crew["id"]))
    chat.assert_not_called()
    with runtime.factory() as session:
        run = session.query(CrewRun).one()
        assert run.status == "completed" and run.model_calls == 0


# 6. Safety nets every user hits ------------------------------------------------------------


def test_pause_all_stops_every_crew_and_resume_does_not_auto_start_them(planning):
    client, auth, runtime, _ = planning
    r = route(client, auth)
    decide(
        planning,
        crew_proposal={
            "template_id": "schedule",
            "name": "Water",
            "responsibilities": "Drink water",
            "route_id": r["id"],
            "daily_triggers": [{"time": "10:00", "message": "Water"}],
        },
    )
    crew = verify(client, auth, turn(planning, "water")["draft"])
    assert crew["is_running"]
    assert client.post("/api/v1/actions/pause", headers=auth).status_code == 200
    assert not client.get("/api/v1/crew", headers=auth).json()[0]["is_running"]
    client.post("/api/v1/actions/resume", headers=auth)
    assert not client.get("/api/v1/crew", headers=auth).json()[0]["is_running"]


def test_planner_failures_leave_no_half_built_crew_and_the_chat_recovers(planning):
    client, auth, runtime, coordinator = planning
    coordinator.complete = AsyncMock(side_effect=RuntimeError("gateway down"))
    first = client.post(
        "/api/v1/core/turn", headers=auth, json={"message": "hi", "request_id": str(uuid4())}
    )
    assert first.status_code == 502 and "Nothing was changed" in first.json()["detail"]
    assert client.get("/api/v1/crew-drafts", headers=auth).json() == []
    coordinator.complete = AsyncMock(return_value=(CoordinatorDecision(reply="Back."), "m"))
    assert turn(planning, "hi again")["reply"] == "Back."


def test_duplicate_names_get_a_suffix_and_timeline_lists_them(planning):
    client, auth, _, _ = planning
    r = route(client, auth)
    for _ in range(2):
        decide(
            planning,
            crew_proposal={
                "template_id": "schedule",
                "name": "Water",
                "responsibilities": "Drink water",
                "route_id": r["id"],
                "daily_triggers": [{"time": "10:00", "message": "Water"}],
            },
        )
        verify(client, auth, turn(planning, "water")["draft"])
    names = sorted(c["name"] for c in client.get("/api/v1/crew", headers=auth).json())
    assert names == ["Water", "Water 2"]
    body = client.get("/api/v1/timeline?days=2", headers=auth).json()
    assert {e["crew_name"] for e in body["upcoming"]} == {"Water", "Water 2"}
    assert all(not e["uses_model"] for e in body["upcoming"])


# 8. "Watch my AIRouter credits": find the page like a person, and never mistake a login for a balance


def credit_crew(planning):
    client, auth, runtime, _ = planning
    r = route(client, auth)
    domains = ["www.airouter.in"]
    decide(
        planning,
        crew_proposal={
            "template_id": "operator",
            "name": "Credit watch",
            "route_id": r["id"],
            "responsibilities": "Every 30 minutes open https://www.airouter.in, find Credits, and notify me if the balance is under $0.20",
            "trigger_type": "interval",
            "requested_notifications": True,
            "requested_actions": [
                {"operation": op, "reason": "x", "domains": domains}
                for op in ("browser.open", "browser.navigate", "browser.read")
            ],
        },
    )
    draft = turn(planning, "Watch my AIRouter credits")["draft"]
    rule = {"enabled": True, "mode": "restricted", "domains": domains}
    crew = verify(
        client,
        auth,
        draft,
        route_id=r["id"],
        notifications=True,
        actions={op: rule for op in ("browser.open", "browser.navigate", "browser.read")},
    )
    return crew, runtime


def test_credit_watch_navigates_then_reads_and_alerts_on_a_low_balance(planning):
    crew, runtime = credit_crew(planning)
    assert crew["is_running"]
    seen = []

    async def fake_browser(operation, args, rule, owner, scope_guard=None):
        seen.append((operation, args))
        if operation == "browser.navigate":
            return {
                "url": "https://www.airouter.in/billing/credits",
                "title": "Credits",
                "navigation": {
                    "reached": True,
                    "steps": [{"opened": "Account"}, {"clicked": "Credits"}],
                },
            }
        if operation == "browser.read":
            return {
                "url": "https://www.airouter.in/billing/credits",
                "title": "Credits",
                "text": "Balance $0.15",
            }
        return {"url": "https://www.airouter.in/", "title": "Home"}

    runtime.actions.desktop.browser = fake_browser
    run_work(
        runtime,
        crew["id"],
        checkpoint(
            "Balance is $0.15, below $0.20.",
            state="continue",
            notification="AIRouter credits are $0.15, below your $0.20 limit.",
            actions=[
                {"operation": "browser.open", "arguments": {"url": "https://www.airouter.in"}},
                {"operation": "browser.navigate", "arguments": {"goal": "credits"}},
                {"operation": "browser.read", "arguments": {}},
            ],
        ),
    )
    assert [op for op, _ in seen] == ["browser.open", "browser.navigate", "browser.read"]
    assert seen[1][1] == {"goal": "credits"}
    with runtime.factory() as session:
        from mavo.database import ReminderEvent

        assert [r.message for r in session.query(ReminderEvent)] == [
            "AIRouter credits are $0.15, below your $0.20 limit."
        ]


def wall_browser(runtime):
    async def fake_browser(operation, args, rule, owner, scope_guard=None):
        return {
            "url": "https://www.airouter.in/login",
            "title": "Sign in",
            "sign_in_required": {"kind": "login", "host": "www.airouter.in"},
        }

    runtime.actions.desktop.browser = fake_browser


def work_rounds(runtime, crew_id, *replies):
    """One model round per call to work(): each reply is the agent's answer for that round."""
    runtime.router.chat = AsyncMock(side_effect=[json.dumps(r) for r in replies])
    for _ in replies:
        asyncio.run(runtime.work(crew_id))
    return runtime.router.chat


OPEN_AND_READ = [
    {"operation": "browser.open", "arguments": {"url": "https://www.airouter.in"}},
    {"operation": "browser.read", "arguments": {}},
]


def test_a_suspected_sign_in_goes_back_to_the_agent_which_decides(planning):
    from mavo.database import ReminderEvent

    crew, runtime = credit_crew(planning)
    wall_browser(runtime)
    chat = work_rounds(
        runtime,
        crew["id"],
        checkpoint("Reading balance", state="continue", actions=OPEN_AND_READ),
        checkpoint(
            "That is a sign-in page.",
            state="waiting_for_input",
            question="Please sign in to www.airouter.in",
        ),
    )
    assert chat.await_count == 2  # the application did not decide for the agent
    first_prompt = chat.await_args_list[1].args[2][0]["content"]
    assert "sign_in_required" in first_prompt  # the agent was shown the hint with the page
    with runtime.factory() as session:
        stored = session.get(CrewInstance, crew["id"])
        assert stored.runtime_state == "needs_input"
        assert stored.observation["question"] == "Please sign in to www.airouter.in"
        assert [r.message for r in session.query(ReminderEvent)] in (
            [],
            [stored.observation["question"]],
        )


def test_the_agent_can_carry_on_when_the_sign_in_hint_was_wrong(planning):
    crew, runtime = credit_crew(planning)
    wall_browser(runtime)
    work_rounds(
        runtime,
        crew["id"],
        checkpoint("Reading balance", state="continue", actions=OPEN_AND_READ),
        checkpoint("Balance is $3.40, above your limit."),
    )
    with runtime.factory() as session:
        stored = session.get(CrewInstance, crew["id"])
        assert stored.runtime_state == "waiting"
        assert not stored.observation.get("question")
        assert stored.observation.get("wall_strikes") == 0


def test_an_agent_that_has_only_looked_at_a_sign_in_screen_gets_to_act_before_the_person_is_asked(
    planning,
):
    """Opening Microsoft Forms showed "Pick an account". The agent's second turn read the page to find
    the account, and that read counted as a second sighting: the run stopped and asked the person
    to sign in, before the agent could click the account that signs in from the saved session."""
    crew, runtime = credit_crew(planning)
    wall_browser(runtime)
    look = [{"operation": "browser.read", "arguments": {}}]
    go = [{"operation": "browser.navigate", "arguments": {"goal": "credits"}}]
    work_rounds(
        runtime,
        crew["id"],
        checkpoint("Opened it.", state="continue", actions=OPEN_AND_READ),
        checkpoint("Looking for the account.", state="continue", actions=look),
    )
    with runtime.factory() as session:
        stored = session.get(CrewInstance, crew["id"])
        assert stored.runtime_state == "waiting" and not stored.observation.get("question")
    # Its next turn acts, and the page is still a sign-in: now the person is asked.
    work_rounds(runtime, crew["id"], checkpoint("Trying.", state="continue", actions=go))
    with runtime.factory() as session:
        stored = session.get(CrewInstance, crew["id"])
        assert stored.runtime_state == "needs_input"
        assert "needs you to sign in" in stored.observation["question"]


def test_an_action_the_crew_was_not_given_goes_back_to_the_agent_not_to_the_person(planning):
    """The agent clicked "Get API Key" where it should have navigated. The Crew has open, read and
    navigate only: that used to stop the run and ask the person to enable clicking in Actions."""
    crew, runtime = credit_crew(planning)

    async def fake_browser(operation, args, rule, owner, scope_guard=None):
        return {"url": "https://www.airouter.in/", "title": "Home", "text": "Balance $0.73 " * 20}

    runtime.actions.desktop.browser = fake_browser
    click = {"operation": "browser.click", "arguments": {"ref": "e7"}}
    chat = work_rounds(
        runtime,
        crew["id"],
        checkpoint("Trying a link.", state="continue", actions=[click]),
        checkpoint("Balance is $0.73."),
    )
    assert "NOT allowed for this Crew" in chat.await_args_list[0].args[3]
    assert "browser.click" in chat.await_args_list[0].args[3].split("NOT allowed")[1][:80]
    again = chat.await_args_list[1].args[2][0]["content"]
    assert "REFUSED, not allowed for this Crew" in again  # the agent is told, in plain words
    with runtime.factory() as session:
        stored = session.get(CrewInstance, crew["id"])
        assert stored.runtime_state == "waiting" and not stored.observation.get("question")

    # A third slip in one review is not a slip any more: now the person is asked.
    work_rounds(
        runtime,
        crew["id"],
        *[checkpoint("Trying a link.", state="continue", actions=[click])] * 3,
    )
    with runtime.factory() as session:
        stored = session.get(CrewInstance, crew["id"])
        assert stored.runtime_state == "needs_input"
        assert "disabled" in stored.observation["question"]


def test_a_login_looking_page_on_the_way_does_not_stop_a_batch_that_ends_on_the_real_page(planning):
    """Straight after opening, a signed-in site can still show "Sign in" until its login widget has
    loaded. That used to cut the batch short after the open, cost the agent extra turns, and on
    the second sighting ask the person to sign in. The page the batch ends on decides."""
    crew, runtime = credit_crew(planning)
    seen = []

    async def fake_browser(operation, args, rule, owner, scope_guard=None):
        seen.append(operation)
        if operation == "browser.open":
            return {
                "url": "https://www.airouter.in/",
                "title": "Home",
                "sign_in_required": {"kind": "login", "host": "www.airouter.in"},
            }
        return {
            "url": "https://www.airouter.in/settings/credits",
            "title": "Credits",
            "text": "Balance $0.73 " * 20,
        }

    runtime.actions.desktop.browser = fake_browser
    batch = [
        {"operation": "browser.open", "arguments": {"url": "https://www.airouter.in"}},
        {"operation": "browser.navigate", "arguments": {"goal": "credits"}},
        {"operation": "browser.read", "arguments": {}},
    ]
    work_rounds(
        runtime,
        crew["id"],
        checkpoint("Looking.", state="continue", actions=batch),
        checkpoint("Balance is $0.73."),
    )
    assert seen[:3] == ["browser.open", "browser.navigate", "browser.read"]
    with runtime.factory() as session:
        stored = session.get(CrewInstance, crew["id"])
        assert stored.runtime_state == "waiting" and not stored.observation.get("question")
        assert stored.observation.get("wall_strikes") == 0


def test_every_review_really_visits_the_site_again(planning):
    """The same open and read in a later review used to be matched with the earlier request and
    answered with its saved result: a Crew told to check a site every 30 minutes reported the
    first check for ever, and never touched the site again."""
    from datetime import timedelta

    from mavo.database import utcnow

    crew, runtime = credit_crew(planning)
    balance = {"now": "$0.74"}
    visits = []

    async def fake_browser(operation, args, rule, owner, scope_guard=None):
        visits.append(operation)
        return {
            "url": "https://www.airouter.in/settings/credits",
            "title": "Credits",
            "text": "Balance " + balance["now"] + " " * 300,
            "view": "PAGE Balance " + balance["now"],
        }

    runtime.actions.desktop.browser = fake_browser
    batch = [
        {"operation": "browser.open", "arguments": {"url": "https://www.airouter.in"}},
        {"operation": "browser.read", "arguments": {}},
    ]
    for review, amount in ((1, "$0.74"), (2, "$0.73"), (3, "$0.72")):
        balance["now"] = amount
        if review > 1:
            with runtime.factory() as session:
                due = session.get(CrewInstance, crew["id"])
                due.next_run_at = utcnow() - timedelta(seconds=5)
                session.commit()
        chat = work_rounds(
            runtime,
            crew["id"],
            checkpoint("Looking.", state="continue", actions=batch),
            checkpoint("Balance is " + amount + "."),
        )
        shown = chat.await_args_list[1].args[2][0]["content"]
        assert "PAGE Balance " + amount in shown, f"review {review} was shown an old page"
    assert visits == ["browser.open", "browser.read"] * 3  # six real visits, not two


def test_a_batch_never_clicks_on_a_page_that_looks_like_a_login(planning):
    crew, runtime = credit_crew(planning)
    seen = []

    async def fake_browser(operation, args, rule, owner, scope_guard=None):
        seen.append(operation)
        return {
            "url": "https://www.airouter.in/",
            "title": "Home",
            "sign_in_required": {"kind": "login", "host": "www.airouter.in"},
        }

    runtime.actions.desktop.browser = fake_browser
    guarded = [
        {"operation": "browser.open", "arguments": {"url": "https://www.airouter.in"}},
        {"operation": "browser.click", "arguments": {"ref": "e3"}},
    ]
    work_rounds(runtime, crew["id"], checkpoint("Clicking.", state="continue", actions=guarded))
    assert seen[0] == "browser.open" and "browser.click" not in seen


def test_the_application_steps_in_only_when_the_agent_hits_the_sign_in_twice(planning):
    from mavo.database import ReminderEvent

    crew, runtime = credit_crew(planning)
    wall_browser(runtime)
    again = checkpoint("Reading balance", state="continue", actions=OPEN_AND_READ)
    work_rounds(runtime, crew["id"], again, again)
    with runtime.factory() as session:
        stored = session.get(CrewInstance, crew["id"])
        assert stored.runtime_state == "needs_input"
        assert "www.airouter.in needs you to sign in" in stored.observation["question"]
        assert [r.message for r in session.query(ReminderEvent)] == [stored.observation["question"]]


# 9. Answering setup questions must refine the one draft, and adding it must leave no drafts behind


def portal_proposal(**extra):
    return {
        "template_id": "operator",
        "name": "Placements Watch",
        "responsibilities": "Every 30 minutes read https://placements.haveloc.com/jobs and list jobs that are Open For Applications",
        "trigger_type": "interval",
        "max_model_calls": 6,
        "requested_notifications": True,
        "requested_actions": [
            {"operation": op, "reason": "x", "domains": ["placements.haveloc.com"]}
            for op in ("browser.open", "browser.read", "browser.navigate")
        ],
        **extra,
    }


def test_answering_questions_refines_the_same_draft_and_adding_it_leaves_nothing_behind(planning):
    client, auth, runtime, _ = planning
    r = route(client, auth)
    decide(planning, crew_proposal=portal_proposal(), questions=["Which page lists the jobs?"])
    first = turn(planning, "Check my placements jobs")["draft"]
    # The model forgets target_crew_id on its follow-up, as it did in real use.
    decide(planning, crew_proposal=portal_proposal(route_id=r["id"]))
    second = turn(planning, "The jobs page. Every 30 minutes.")["draft"]
    assert second["id"] == first["id"]
    assert len(client.get("/api/v1/crew-drafts", headers=auth).json()) == 1

    rule = {"enabled": True, "mode": "restricted", "domains": ["placements.haveloc.com"]}
    crew = verify(
        client,
        auth,
        second,
        route_id=r["id"],
        notifications=True,
        actions={op: rule for op in ("browser.open", "browser.read", "browser.navigate")},
    )
    assert crew["status"] == "active"
    assert client.get("/api/v1/crew-drafts", headers=auth).json() == []
    assert [c["name"] for c in client.get("/api/v1/crew", headers=auth).json()] == [
        "Placements Watch"
    ]


def test_a_different_crew_in_the_same_chat_is_not_mistaken_for_a_refinement(planning):
    client, auth, _, _ = planning
    decide(planning, crew_proposal=portal_proposal())
    turn(planning, "Watch my placements")
    decide(
        planning,
        crew_proposal={
            "template_id": "schedule",
            "name": "Water",
            "responsibilities": "Drink water",
            "daily_triggers": [{"time": "10:00", "message": "Water"}],
        },
    )
    turn(planning, "Also remind me to drink water")
    assert sorted(d["name"] for d in client.get("/api/v1/crew-drafts", headers=auth).json()) == [
        "Placements Watch",
        "Water",
    ]


def test_verifying_removes_older_leftover_drafts_of_the_same_setup(planning):
    client, auth, runtime, _ = planning
    r = route(client, auth)
    with runtime.factory() as session:
        for crew_id, name, stamp in (
            ("old", "Placements Watch", "2026-11-01 10:00:00"),
            ("new", "Placements Watch 2", "2026-11-01 10:05:00"),
            ("other", "Unrelated", "2026-11-01 09:00:00"),
        ):
            session.execute(
                __import__("sqlalchemy").text(
                    "INSERT INTO crew_instances (id,template_id,name,responsibilities,configuration_plan,autonomy_mode,status,provider,model,max_model_calls,resources,permissions,triggers,is_running,runtime_state,review_interval_minutes,daily_call_limit,daily_calls_used,runtime_generation,observation,trigger_type,event_cursor,daily_triggers,created_at,updated_at) VALUES (:i,'schedule',:n,'Water','{}','reactive','draft','unassigned','',5,'[]','[\"model:invoke\"]','[]',0,'stopped',30,12,0,0,'{}','daily',0,'[]',:t,:t)"
                ),
                {"i": crew_id, "n": name, "t": stamp},
            )
        session.commit()
    decide(
        planning,
        crew_proposal={
            "template_id": "schedule",
            "name": "Placements Watch 2",
            "responsibilities": "Water",
            "route_id": r["id"],
            "daily_triggers": [{"time": "10:00", "message": "Water"}],
        },
        target_crew_id="new",
    )
    out = client.post(
        "/api/v1/core/turn",
        headers=auth,
        json={"message": "x", "request_id": str(uuid4()), "crew_id": "new"},
    )
    assert out.status_code == 200, out.text
    verify(client, auth, out.json()["draft"], route_id=r["id"])
    left = {d["id"] for d in client.get("/api/v1/crew-drafts", headers=auth).json()}
    assert "old" not in left and "other" in left
    assert [c["name"] for c in client.get("/api/v1/crew", headers=auth).json()] == [
        "Placements Watch 2"
    ]


def test_site_sign_in_check_reports_a_login_wall_and_closes_the_browser(planning):
    from unittest.mock import AsyncMock

    client, auth, runtime, _ = planning
    desktop = runtime.actions.desktop
    desktop.browser = AsyncMock(
        return_value={
            "url": "https://www.airouter.in/",
            "title": "Home",
            "sign_in_required": {"kind": "login", "host": "www.airouter.in"},
        }
    )
    desktop.close_owner = AsyncMock()
    out = client.post(
        "/api/v1/browser/probe",
        headers=auth,
        json={"url": "https://www.airouter.in/settings/credits"},
    ).json()
    assert (
        out["signed_in"] is False
        and out["wall"]["kind"] == "login"
        and out["host"] == "www.airouter.in"
    )
    desktop.close_owner.assert_awaited()
    # A page with a sign-out control proves a sign-in.
    desktop.browser = AsyncMock(
        return_value={
            "url": "https://www.airouter.in/settings/credits",
            "title": "Credits",
            "session": "in",
        }
    )
    proven = client.post(
        "/api/v1/browser/probe",
        headers=auth,
        json={"url": "https://www.airouter.in/settings/credits"},
    ).json()
    assert proven["signed_in"] is True and proven["session"] == "in"
    # A public front page that only offers "Sign in" is signed OUT, even without a login form...
    desktop.browser = AsyncMock(
        return_value={
            "url": "https://www.airouter.in/",
            "title": "AIRouter",
            "session": "out",
            "login_url": "https://www.airouter.in/signin",
        }
    )
    public = client.post(
        "/api/v1/browser/probe", headers=auth, json={"url": "https://www.airouter.in/"}
    ).json()
    assert public["signed_in"] is False and public["session"] == "out"
    assert public["wall"] is None and public["login_url"] == "https://www.airouter.in/signin"
    # ...and one that shows neither is "unknown", never "signed in".
    desktop.browser = AsyncMock(return_value={"url": "https://www.airouter.in/", "title": "x"})
    unclear = client.post(
        "/api/v1/browser/probe", headers=auth, json={"url": "https://www.airouter.in/"}
    ).json()
    assert unclear["signed_in"] is False and unclear["session"] == "unknown"
    desktop.browser = AsyncMock(
        side_effect=ValueError("Mavo's browser is open for sign-in. Close it first.")
    )
    blocked = client.post(
        "/api/v1/browser/probe", headers=auth, json={"url": "https://www.airouter.in"}
    )
    assert blocked.status_code == 409 and "Close it first" in blocked.json()["detail"]
    assert (
        client.post(
            "/api/v1/browser/probe", headers=auth, json={"url": "http://127.0.0.1"}
        ).status_code
        == 422
    )


# 10. The chat stays a conversation: one short message, one question at a time


def test_the_assistant_asks_one_question_and_keeps_its_message_short(planning):
    client, auth, runtime, _ = planning
    wall_of_text = (
        "Got it. I will set up a watcher for your portal. Here is everything you need to know:\n\n"
        "1. Sign in with the key button\n2. The budget is 96 calls a day\n3. Permissions follow\n\nMore text "
        * 5
    )
    decide(
        planning,
        reply=wall_of_text,
        crew_proposal=portal_proposal(),
        questions=["Which page lists the jobs?", "How often?", "Notify you?"],
    )
    result = turn(planning, "Check my placements jobs")
    assert result["reply"] == "Which page lists the jobs?"
    assert result["questions"] == ["Which page lists the jobs?"]
    assert "is a draft" not in result["reply"]

    decide(planning, reply=wall_of_text, crew_proposal=portal_proposal())
    ready = turn(planning, "The jobs page")["reply"]
    assert ready == "Got it. I will set up a watcher for your portal." and len(ready) < 240

    decide(planning, reply="Just a normal answer, with details.\n\n- and a list")
    assert turn(planning, "What time is it?")["reply"].startswith("Just a normal answer")


# 11. Filling a form is one batch; a failed step stops it and the model is told why


def form_crew(planning):
    client, auth, runtime, _ = planning
    r = route(client, auth)
    crew = client.post(
        "/api/v1/crew",
        headers=auth,
        json={
            "template_id": "operator",
            "name": "Form filler",
            "route_id": r["id"],
            "trigger_type": "manual",
            "responsibilities": "Fill and submit the form at https://forms.example.com",
            "max_model_calls": 6,
        },
    ).json()
    for op in ("browser.open", "browser.read", "browser.fill", "browser.click"):
        client.put(
            "/api/v1/action-policies",
            headers=auth,
            json={
                "operation": op,
                "crew_id": crew["id"],
                "rule": {"enabled": True, "mode": "restricted", "domains": ["forms.example.com"]},
            },
        )
    client.post(f"/api/v1/crew/{crew['id']}/start", headers=auth)
    return crew, runtime


def test_a_failed_form_step_stops_the_batch_and_the_crew_tries_again(planning):
    crew, runtime = form_crew(planning)
    done = []

    async def desktop(operation, args, rule, owner, scope_guard=None):
        done.append((operation, args))
        if operation == "browser.fill" and args["ref"] == "e2":
            raise ValueError("That field is gone. Read the page again for fresh refs.")
        return {"url": "https://forms.example.com/", "title": "Form"}

    runtime.actions.desktop.browser = desktop
    batch = [
        {"operation": "browser.fill", "arguments": {"ref": f"e{i}", "text": "x"}} for i in (1, 2, 3)
    ]
    batch.append({"operation": "browser.click", "arguments": {"ref": "e9"}})
    run_work(runtime, crew["id"], checkpoint("Filling the form", state="continue", actions=batch))
    assert [a["ref"] for op, a in done if op == "browser.fill"] == ["e1", "e2"]
    assert not [op for op, _ in done if op == "browser.click"], (
        "never the submit click after a failure"
    )
    assert done[-1][0] == "browser.read", "the page is read again so the next step has fresh refs"
    with runtime.factory() as session:
        from mavo.database import ActionRequest

        rows = session.query(ActionRequest).order_by(ActionRequest.created_at).all()
        assert [r.status for r in rows] == ["completed", "failed", "completed"]
        assert "gone" in rows[1].error and rows[2].operation == "browser.read"
        stored = session.get(CrewInstance, crew["id"])
        assert stored.runtime_state == "waiting" and stored.next_run_at is not None


def test_up_to_sixteen_actions_run_in_one_step_and_a_bad_reply_gets_one_repair(planning):
    from unittest.mock import AsyncMock

    crew, runtime = form_crew(planning)

    async def desktop(operation, args, rule, owner, scope_guard=None):
        return {"url": "https://forms.example.com/", "title": "Form"}

    runtime.actions.desktop.browser = desktop
    sixteen = [
        {"operation": "browser.fill", "arguments": {"ref": f"e{i}", "text": "x"}}
        for i in range(1, 17)
    ]
    too_many = {**checkpoint("Filling", state="continue"), "actions": sixteen + sixteen[:1]}
    valid = checkpoint("Filled all 16 fields", state="continue", actions=sixteen)
    runtime.router.chat = AsyncMock(side_effect=[json.dumps(too_many), json.dumps(valid)])
    asyncio.run(runtime.work(crew["id"]))
    with runtime.factory() as session:
        from mavo.database import ActionRequest

        assert (
            session.query(ActionRequest).filter_by(status="completed").count() == 17
        )  # 16 + the look
        assert session.query(CrewRun).one().model_calls == 2
    repair = runtime.router.chat.await_args_list[1].args[2][-1]["content"]
    assert "not valid" in repair and "actions" in repair


# 11. A scheduled Crew that finished its cycle keeps its schedule; only a question holds it


def test_a_finished_cycle_on_a_schedule_is_not_a_request_for_input(planning):
    crew, runtime = credit_crew(planning)
    # The agent says it is done and waiting, but asks nothing: it simply runs again later.
    run_work(
        runtime,
        crew["id"],
        checkpoint("1 open job.", state="waiting_for_input", next_step="Await the next check."),
    )
    with runtime.factory() as session:
        stored = session.get(CrewInstance, crew["id"])
        assert stored.runtime_state == "waiting" and stored.next_run_at is not None
        assert not stored.observation.get("question")


def test_a_question_still_holds_a_scheduled_crew_for_the_person(planning):
    crew, runtime = credit_crew(planning)
    run_work(
        runtime,
        crew["id"],
        checkpoint(
            "Two portals match.",
            state="waiting_for_input",
            question="Which portal should I watch?",
        ),
    )
    with runtime.factory() as session:
        stored = session.get(CrewInstance, crew["id"])
        assert stored.runtime_state == "needs_input"
        assert stored.observation["question"] == "Which portal should I watch?"


def test_a_crew_an_older_version_parked_without_a_question_resumes_on_the_next_tick(planning):
    crew, runtime = credit_crew(planning)
    with runtime.factory() as session:
        stored = session.get(CrewInstance, crew["id"])
        stored.is_running, stored.runtime_state, stored.next_run_at = True, "needs_input", None
        stored.observation = {"question": None}
        session.commit()
    # A worker is already "running" for it, so this tick only repairs the state and starts nothing.
    from types import SimpleNamespace

    with runtime.factory() as session:
        generation = session.get(CrewInstance, crew["id"]).runtime_generation
    runtime.tasks[crew["id"]] = (
        generation,
        SimpleNamespace(done=lambda: False, cancel=lambda: None),
    )
    asyncio.run(runtime.tick())
    with runtime.factory() as session:
        stored = session.get(CrewInstance, crew["id"])
        assert stored.runtime_state == "waiting"

    # One that is really waiting for an answer stays put.
    with runtime.factory() as session:
        stored = session.get(CrewInstance, crew["id"])
        stored.runtime_state, stored.next_run_at = "needs_input", None
        stored.observation = {"question": "Which portal?"}
        session.commit()
    asyncio.run(runtime.tick())
    with runtime.factory() as session:
        assert session.get(CrewInstance, crew["id"]).runtime_state == "needs_input"


def test_saved_login_check_is_instant_and_only_reads_the_cookie_store(planning):
    client, auth, runtime, _ = planning
    desktop = runtime.actions.desktop
    desktop.browser = AsyncMock(side_effect=AssertionError("must not start a browser"))
    desktop.saved_session = lambda host: host == "placements.haveloc.com"
    ok = client.get("/api/v1/browser/saved?host=placements.haveloc.com", headers=auth)
    assert ok.status_code == 200 and ok.json() == {"host": "placements.haveloc.com", "saved": True}
    assert (
        client.get("/api/v1/browser/saved?host=other.example", headers=auth).json()["saved"]
        is False
    )
    desktop.saved_session = lambda host: None  # the cookie file is locked: cannot tell, not "no"
    locked = client.get("/api/v1/browser/saved?host=placements.haveloc.com", headers=auth)
    assert locked.status_code == 200 and locked.json()["saved"] is None
    for bad in ("http://x", "a b", "../x", ""):
        assert (
            client.get("/api/v1/browser/saved", params={"host": bad}, headers=auth).status_code
            == 422
        )


# 9. "Alert me when my credits are low": the agent is told, plainly, that only `notification` reaches the desktop


def test_a_watching_crew_with_desktop_alerts_is_told_only_the_notification_field_reaches_the_desktop(
    planning,
):
    """Without this, the agent wrote "Status: LOW" in its report and never set the field: in three
    real reviews no alert was created, and the user saw nothing on the desktop."""
    crew, runtime = credit_crew(planning)
    chat = run_work(
        runtime,
        crew["id"],
        checkpoint(
            "Balance $0.15, low.",
            state="wait_for_next_review",
            notification="AIRouter credits are $0.15, below your limit.",
        ),
    )
    system = chat.await_args_list[0].args[3]
    assert "DESKTOP ALERTS ARE ON FOR THIS CREW" in system and "ONLY" in system
    assert system.rstrip().endswith("review.")  # said last, where a model weighs it most
    with runtime.factory() as session:
        from mavo.database import ReminderEvent

        assert [r.message for r in session.query(ReminderEvent)] == [
            "AIRouter credits are $0.15, below your limit."
        ]


def test_the_alert_rule_is_left_out_when_there_is_nothing_to_watch_or_no_permission(planning):
    client, auth, runtime, _ = planning
    r = route(client, auth)
    # A one-off (manual) Crew with desktop alerts allowed: nothing is being watched.
    decide(
        planning,
        crew_proposal={
            "template_id": "operator",
            "name": "One off",
            "route_id": r["id"],
            "responsibilities": "Summarise something once",
            "trigger_type": "manual",
            "requested_notifications": True,
        },
    )
    manual = verify(
        client,
        auth,
        turn(planning, "One off")["draft"],
        route_id=r["id"],
        notifications=True,
        actions={},
    )
    assert (
        "DESKTOP ALERTS ARE ON"
        not in run_work(runtime, manual["id"], checkpoint("Done")).await_args_list[0].args[3]
    )
    # A watching Crew without the desktop-alert permission.
    decide(
        planning,
        crew_proposal={
            "template_id": "operator",
            "name": "Quiet watcher",
            "route_id": r["id"],
            "responsibilities": "Every 30 minutes check something",
            "trigger_type": "interval",
        },
    )
    quiet = verify(
        client,
        auth,
        turn(planning, "Quiet watcher")["draft"],
        route_id=r["id"],
        notifications=False,
        actions={},
    )
    assert (
        "DESKTOP ALERTS ARE ON"
        not in run_work(runtime, quiet["id"], checkpoint("Done")).await_args_list[0].args[3]
    )
