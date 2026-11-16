# ruff: noqa: F811 - `planning` is a fixture, imported and then named as a parameter
"""Telling an agent what is happening ("today I have a meeting at 1 PM") without changing its job:
kept as a note for now, used in its runs until the day it ends, then gone. A team's hidden worker
reads the notes and standing preferences of the agent the person sees."""

from datetime import timedelta

from mavo.crew_notes import active, context_for, owner, valid_until
from mavo.database import CrewAgent, CrewInstance, utcnow
from tests.test_crew_ask import news_crew, sorter
from tests.test_planner import planning, turn  # noqa: F401
from tests.test_scenarios import checkpoint, run_work


def day(offset=0):
    return (utcnow().astimezone().date() + timedelta(days=offset)).isoformat()


def test_a_note_for_now_is_kept_confirmed_used_and_updates_todays_work_at_once(planning):
    client, auth, runtime, coordinator = planning
    crew = news_crew(client, auth, runtime, name="Daily Planner")
    calls = len(coordinator.calls)
    seen = sorter(
        runtime,
        {
            "kind": "note",
            "note": f"Meeting at 1 PM on {day()}",
            "until": day(),
            "ack": "Noted, I'll plan around your 1 PM meeting today.",
        },
    )
    item = client.post(
        f"/api/v1/crew/{crew}/asks",
        headers=auth,
        json={"question": "today i have a meeting at 1pm"},
    ).json()
    assert item["kind"] == "note" and not item["pending"]
    assert item["answer"] == "Noted, I'll plan around your 1 PM meeting today. Updating now."
    assert len(coordinator.calls) == calls  # not a chat question
    assert f"Today is {day()}" in seen[0][0]  # the sorter can turn "today" into a date
    with runtime.factory() as session:
        stored = session.get(CrewInstance, crew)
        assert stored.configuration_plan["notes"][0]["text"] == f"Meeting at 1 PM on {day()}"
        assert stored.configuration_plan.get("preferences") is None  # its job is unchanged
        assert stored.responsibilities.startswith("Watch Indian armed forces news")
        assert stored.next_run_at <= utcnow().replace(tzinfo=stored.next_run_at.tzinfo)
    chat = run_work(runtime, crew, checkpoint("Plan ready."))
    prompt = chat.await_args_list[0].args[2][0]["content"]
    assert "What the person told you for now" in prompt and f"Meeting at 1 PM on {day()}" in prompt


def test_a_note_about_later_days_waits_for_the_next_run_and_can_be_taken_back(planning):
    client, auth, runtime, _ = planning
    crew = news_crew(client, auth, runtime, name="Daily Planner")
    sorter(
        runtime,
        {"kind": "note", "note": "Travelling in Pune all week", "until": day(5), "ack": "Noted."},
    )
    item = client.post(
        f"/api/v1/crew/{crew}/asks", headers=auth, json={"question": "in pune this week"}
    ).json()
    assert item["answer"] == "Noted."  # not about today: no extra run
    with runtime.factory() as session:
        assert session.get(CrewInstance, crew).next_run_at.replace(tzinfo=None) > utcnow().replace(
            tzinfo=None
        )
    left = client.delete(f"/api/v1/crew/{crew}/notes/0", headers=auth).json()
    assert left["configuration_plan"]["notes"] == []
    assert client.delete(f"/api/v1/crew/{crew}/notes/0", headers=auth).status_code == 404


def test_ended_notes_are_dropped_and_a_bad_end_day_means_today():
    notes = [
        {"text": "old", "until": day(-1), "said": "x"},
        {"text": "now", "until": day(), "said": "x"},
    ]
    assert [n["text"] for n in active(notes)] == ["now"]
    assert valid_until("next tuesday", day()) == day()
    assert valid_until(day(-3), day()) == day()  # never ends before it was said
    assert valid_until(day(2), day()) == day(2)


def test_a_teams_worker_reads_the_preferences_and_notes_of_the_agent_the_person_sees(planning):
    """Before, both were saved on the agent but its hidden worker, which does the run, never saw
    them: "show only job emails" was silently ignored by every composed agent."""
    client, auth, runtime, _ = planning
    parent_id = news_crew(client, auth, runtime, name="Inbox Pulse")
    with runtime.factory() as session:
        parent = session.get(CrewInstance, parent_id)
        worker = CrewInstance(
            **{
                c: getattr(parent, c)
                for c in (
                    "template_id",
                    "responsibilities",
                    "autonomy_mode",
                    "status",
                    "provider",
                    "model",
                    "route_id",
                    "max_model_calls",
                    "review_interval_minutes",
                    "daily_call_limit",
                    "reminder_text",
                    "event_cursor",
                    "resources",
                    "permissions",
                    "triggers",
                )
            },
            name="_agent_worker",
            trigger_type="manual",
            configuration_plan={},
            hidden=True,
        )
        session.add(worker)
        session.flush()
        worker_id = worker.id
        parent.configuration_plan = {
            "preferences": [{"text": "Show only job emails.", "at": "x"}],
            "notes": [
                {"text": "On leave today", "until": day(), "said": utcnow().isoformat()},
                {"text": "Old trip", "until": day(-2), "said": "2026-01-01"},
            ],
        }
        session.add(
            CrewAgent(
                crew_id=parent_id,
                key="w",
                name="Worker",
                role="r",
                worker_id=worker_id,
                status="active",
                definition={},
            )
        )
        session.commit()
        assert owner(session, worker).id == parent_id
        text = context_for(session, worker)
    assert "- Show only job emails." in text and "On leave today" in text and "Old trip" not in text


def test_asking_to_run_every_hour_changes_the_schedule_not_a_preference(planning):
    """It was saved as a preference and the panel kept saying "every day at 9:00 am"."""
    client, auth, runtime, _ = planning
    crew = news_crew(client, auth, runtime, name="AIRouter Credits")
    with runtime.factory() as session:
        stored = session.get(CrewInstance, crew)
        stored.schedule_time, stored.daily_call_limit, stored.max_model_calls = "09:00", 5, 4
        session.commit()
    sorter(
        runtime,
        {
            "kind": "schedule",
            "every_minutes": 60,
            "at": None,
            "ack": "Done, I'll check every hour.",
        },
    )
    item = client.post(
        f"/api/v1/crew/{crew}/asks", headers=auth, json={"question": "I need it to run every 1 hr"}
    ).json()
    assert item["kind"] == "schedule"
    assert item["answer"] == "Done, I'll check every hour."  # no call counts to read
    with runtime.factory() as session:
        stored = session.get(CrewInstance, crew)
        assert stored.review_interval_minutes == 60 and stored.schedule_time is None
        assert stored.trigger_type == "interval" and stored.daily_call_limit == 96
        assert stored.configuration_plan.get("preferences") is None  # not a preference
        assert stored.configuration_plan["draft"]["review_interval_minutes"] == 60


def test_a_daily_time_is_set_and_a_mail_triggered_agent_says_it_has_no_timer(planning):
    client, auth, runtime, _ = planning
    crew = news_crew(client, auth, runtime, name="Daily Brief")
    sorter(
        runtime,
        {"kind": "schedule", "every_minutes": None, "at": "19:30", "ack": "Done, 7:30 PM daily."},
    )
    client.post(f"/api/v1/crew/{crew}/asks", headers=auth, json={"question": "run at 7:30 pm"})
    with runtime.factory() as session:
        stored = session.get(CrewInstance, crew)
        assert stored.schedule_time == "19:30" and stored.review_interval_minutes == 1440
        stored.trigger_type = "gmail_message"
        session.commit()
    item = client.post(
        f"/api/v1/crew/{crew}/asks", headers=auth, json={"question": "every hour"}
    ).json()
    assert "runs when something arrives, not on a timer" in item["answer"]


def test_agents_apply_preferences_quietly():
    from mavo.crew_notes import context_for

    crew = CrewInstance(
        name="X",
        hidden=False,
        configuration_plan={"preferences": [{"text": "Be brief", "at": "x"}]},
    )
    assert "never mention preferences, notes, schedules" in context_for(None, crew)
