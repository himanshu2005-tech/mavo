"""One voice: routine reports from timer agents wait for Mavo's summary; anything that needs the
person, is time-bound, or that they are waiting on reaches them at once. The summary is one
message from Mavo, written by the model or, when it cannot be, a plain list, and nothing held is
ever lost or delivered for an agent that may no longer alert."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest

from mavo import attention
from mavo.database import CrewInstance, ModelRoute, ReminderEvent, utcnow
from mavo.reminders import create_due_reminders
from tests.test_crew_workers import add_crew, factory  # noqa: F401


def crew(**over):
    base = dict(
        trigger_type="interval",
        schedule_time=None,
        hidden=False,
        template_id="research",
        configuration_plan={},
    )
    return SimpleNamespace(**{**base, **over})


@pytest.mark.parametrize(
    "agent,needs,expected",
    [
        (crew(), False, "summary"),  # a routine report every 30 minutes
        (crew(), True, "now"),  # a question, an OK, a failure
        (crew(trigger_type="gmail_message"), False, "now"),  # an important mail
        (crew(trigger_type="calendar_changed"), False, "now"),
        (crew(trigger_type="daily"), False, "now"),  # a reminder at its time
        (crew(schedule_time="08:00"), False, "now"),  # the 8 AM plan
        (crew(trigger_type="manual"), False, "now"),  # asked for, or handed work
        (crew(template_id="computer"), False, "now"),  # battery at 10%
        (crew(hidden=True), False, "now"),  # a one-time task the person is waiting on
        (crew(configuration_plan={"delivery": "now"}), False, "now"),  # the person's choice
        (
            crew(trigger_type="gmail_message", configuration_plan={"delivery": "summary"}),
            False,
            "summary",
        ),
        (None, False, "now"),
    ],
)
def test_what_reaches_the_person_now_and_what_waits_for_mavos_summary(
    factory, agent, needs, expected
):  # noqa: F811
    with factory() as session:
        assert attention.when(session, agent, needs_person=needs) == expected


def held(factory, crew_id, name, text, minutes_ago=0):  # noqa: F811
    with factory() as session:
        at = utcnow() - timedelta(minutes=minutes_ago)
        event = ReminderEvent(
            crew_id=crew_id,
            crew_name=name,
            kind="crew_update",
            message=text,
            scheduled_for=at,
            local_date=at.astimezone().date().isoformat(),
            deduplication_key=f"{name}:{text}",
            status="pending",
            created_at=at,
        )
        assert attention.admit(session, event, session.get(CrewInstance, crew_id)) == "summary"
        session.add(event)
        session.commit()


def owner_of(factory):  # noqa: F811
    def get(session):
        return session.get(CrewInstance, "owner")

    return get


@pytest.fixture
def team(factory):  # noqa: F811
    with factory() as session:
        session.add(ModelRoute(id="cloud", name="Cloud", provider="airouter", model="m"))
        session.commit()
    add_crew(
        factory, id="owner", name="Mavo tasks", hidden=True, permissions=["notifications:show"]
    )
    for cid, name in (("inbox", "Inbox Digest"), ("news", "News Watch")):
        add_crew(factory, id=cid, name=name, permissions=["model:invoke", "notifications:show"])
    return factory


def test_held_reports_are_not_shown_until_mavo_sends_one_summary_of_all_of_them(team):
    held(
        team,
        "inbox",
        "Inbox Digest",
        "2 new mails: an interview at 3 PM from Razorpay.",
        minutes_ago=31,
    )
    held(team, "news", "News Watch", "Seagate's hybrid drive is back.", minutes_ago=5)
    with team() as session:
        assert create_due_reminders(session) == []  # nothing pops up on its own
    seen = []

    async def chat(provider, model, messages, system, credential_id=None):
        seen.append(messages[0]["content"])
        return (
            "**Inbox Digest**: interview at 3 PM from Razorpay.\n"
            "**News Watch**: Seagate's hybrid drive is back."
        )

    text = asyncio.run(attention.flush(team, owner_of(team), chat))
    assert "interview at 3 PM" in text and "Razorpay" in seen[0]
    with team() as session:
        [shown] = create_due_reminders(session)
        assert shown.crew_name == "Mavo" and shown.message.startswith(
            "2 updates from Inbox Digest, News Watch"
        )
        assert {e.status for e in session.query(ReminderEvent) if e.crew_name != "Mavo"} == {
            "summarised"
        }
    assert asyncio.run(attention.flush(team, owner_of(team), chat)) is None  # nothing left to send


def test_the_summary_waits_for_its_window_unless_many_are_waiting(team):
    held(team, "news", "News Watch", "One story.", minutes_ago=2)
    assert asyncio.run(attention.flush(team, owner_of(team), None)) is None
    for n in range(attention.FULL):
        held(team, "inbox", "Inbox Digest", f"Mail {n}", minutes_ago=1)
    assert asyncio.run(attention.flush(team, owner_of(team), None)) is not None


def test_without_a_model_the_summary_is_a_plain_list_and_nothing_is_lost(team):
    held(team, "inbox", "Inbox Digest", "Interview at 3 PM.", minutes_ago=40)

    async def broken(*args, **kwargs):
        raise RuntimeError("offline")

    text = asyncio.run(attention.flush(team, owner_of(team), broken))
    assert text == "**Inbox Digest**: Interview at 3 PM."


def test_a_held_report_from_an_agent_that_may_no_longer_alert_is_dropped(team):
    held(team, "inbox", "Inbox Digest", "Interview at 3 PM.", minutes_ago=40)
    with team() as session:
        session.get(CrewInstance, "inbox").permissions = ["model:invoke"]
        session.commit()
    assert asyncio.run(attention.flush(team, owner_of(team), None)) is None
    with team() as session:
        assert session.query(ReminderEvent).one().status == "cancelled"


def test_the_person_can_set_the_window_turn_summaries_off_and_choose_per_agent(client, auth):
    assert client.get("/api/v1/attention", headers=auth).json() == {
        "digest_minutes": 30,
        "enabled": True,
    }
    assert (
        client.put("/api/v1/attention", headers=auth, json={"digest_minutes": 60}).json()[
            "digest_minutes"
        ]
        == 60
    )
    assert (
        client.put("/api/v1/attention", headers=auth, json={"digest_minutes": 1}).status_code == 422
    )
    route = client.post(
        "/api/v1/routes", headers=auth, json={"name": "R", "provider": "ollama", "model": "m"}
    ).json()
    made = client.post(
        "/api/v1/crew",
        headers=auth,
        json={
            "name": "News",
            "template_id": "research",
            "route_id": route["id"],
            "responsibilities": "News every hour",
        },
    ).json()
    assert (
        client.put(
            f"/api/v1/crew/{made['id']}/delivery", headers=auth, json={"delivery": "now"}
        ).status_code
        == 200
    )
    with client.app.state.crew_runtime.factory() as session:
        stored = session.get(CrewInstance, made["id"])
        assert stored.configuration_plan["delivery"] == "now"
        assert attention.when(session, stored) == "now"
    client.put(f"/api/v1/crew/{made['id']}/delivery", headers=auth, json={"delivery": "auto"})
    off = client.put("/api/v1/attention", headers=auth, json={"enabled": False}).json()
    assert off["enabled"] is False


def test_a_held_report_from_a_stopped_agent_is_not_sent(team):
    held(team, "news", "News Watch", "Old story.", minutes_ago=40)
    with team() as session:
        session.get(CrewInstance, "news").is_running = False
        session.commit()
    assert asyncio.run(attention.flush(team, owner_of(team), None)) is None
