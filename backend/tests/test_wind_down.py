""" "Remind me of lunch at 11:00", then a new agent "Wind Down: whenever my @Lunch Reminder works,
put my laptop to sleep". A reminder going off used to hand nothing on, there was no sleep action,
and a reacting Crew could act on its first run, or on the same handoff again later."""

import asyncio
import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

from mavo.database import CrewInstance, utcnow
from mavo.reminders import create_due_reminders
from tests.test_planner import planning  # noqa: F401
from tests.test_scenarios import checkpoint, route

SLEEP = checkpoint(
    "Lunch Reminder went off: putting the laptop to sleep.",
    actions=[{"operation": "windows.sleep", "arguments": {}}],
)


def test_whenever_the_lunch_reminder_goes_off_the_laptop_sleeps_once(planning):
    client, auth, runtime, _ = planning
    r = route(client, auth)
    lunch = client.post(
        "/api/v1/crew",
        headers=auth,
        json={
            "template_id": "schedule",
            "name": "Lunch Reminder",
            "route_id": r["id"],
            "trigger_type": "daily",
            "schedule_time": "11:00",
            "reminder_text": "Time for lunch",
            "responsibilities": "Remind me to have lunch at 11:00.",
        },
    )
    assert lunch.status_code in (200, 201), lunch.text
    lunch = lunch.json()
    client.post(f"/api/v1/crew/{lunch['id']}/start", headers=auth).raise_for_status()
    wind = client.post(
        "/api/v1/crew",
        headers=auth,
        json={
            "template_id": "operator",
            "name": "Wind Down",
            "route_id": r["id"],
            "trigger_type": "manual",
            "responsibilities": "Whenever Lunch Reminder goes off, put the laptop to sleep.",
            "max_model_calls": 4,
        },
    ).json()
    with runtime.factory() as session:  # the planner links it like this (depends_on / connect)
        crew = session.get(CrewInstance, wind["id"])
        crew.configuration_plan = {**crew.configuration_plan, "upstream": [lunch["id"]]}
        session.commit()
    client.put(
        "/api/v1/action-policies",
        headers=auth,
        json={
            "operation": "windows.sleep",
            "crew_id": wind["id"],
            "rule": {"enabled": True, "mode": "unrestricted"},
        },
    ).raise_for_status()
    client.post(f"/api/v1/crew/{wind['id']}/start", headers=auth).raise_for_status()

    slept = []
    runtime.actions.desktop.windows = lambda operation, args, rule: (
        slept.append(operation) or {"sleeping_in_seconds": 5}
    )
    runtime.router.chat = AsyncMock(return_value=json.dumps(SLEEP))

    def run_wind():
        with runtime.factory() as session:
            c = session.get(CrewInstance, wind["id"])
            c.runtime_state, c.next_run_at = "waiting", utcnow() - timedelta(seconds=5)
            session.commit()
        asyncio.run(runtime.work(wind["id"]))

    # Added, nothing has happened yet: it waits, with no model call and no sleep.
    run_wind()
    assert slept == [] and runtime.router.chat.await_count == 0

    # 11:00 passes: the reminder goes off and hands it on.
    eleven = datetime.now().astimezone().replace(hour=11, minute=0, second=30, microsecond=0)
    with runtime.factory() as session:
        create_due_reminders(session, now=eleven)
        create_due_reminders(session, now=eleven + timedelta(minutes=2))  # polled again: once only
        crew = session.get(CrewInstance, wind["id"])
        assert [h["from"] for h in crew.observation["handoffs"]] == ["Lunch Reminder"]
        assert crew.next_run_at is not None  # woken

    run_wind()
    assert slept == ["windows.sleep"]
    prompt = runtime.router.chat.await_args_list[0].args[2][0]["content"]
    assert "NEW results handed over" in prompt and "Lunch Reminder went off at 11:00" in prompt

    # Later, nothing new: it does not run again, so the laptop is not put to sleep twice.
    run_wind()
    assert slept == ["windows.sleep"] and runtime.router.chat.await_count == 1


def test_sleep_is_an_action_that_must_be_allowed():
    from mavo.action_rules import CATALOG
    from mavo.desktop_actions import DESKTOP_MODELS

    assert CATALOG["windows.sleep"] == ("Windows", "Put the computer to sleep", True)  # asks first
    assert DESKTOP_MODELS["windows.sleep"].model_validate({}).model_dump() == {}
