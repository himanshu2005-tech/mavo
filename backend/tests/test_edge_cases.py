# ruff: noqa: E501
"""Edge cases found by tracing what really happens: a laptop asleep over a daily time, and a team
whose member cannot do what its lead asked. Each would otherwise fail silently or twice."""

import json
from datetime import UTC, datetime, timedelta

from mavo.crew_workers import skip_stale_daily_run
from mavo.database import CrewInstance
from mavo.pipelines import report_trouble, route_finished
from tests.test_lines import agent, team
from tests.test_multi_agent import make_session  # noqa: F401
from tests.test_planner import planning  # noqa: F401


def local(day_offset: int, hm: str) -> datetime:
    """A local wall-clock time `day_offset` days from today, as UTC (how the scheduler stores it)."""
    now = datetime.now().astimezone()
    h, m = map(int, hm.split(":"))
    return (
        now.replace(hour=h, minute=m, second=0, microsecond=0) + timedelta(days=day_offset)
    ).astimezone(UTC)


def poem(due: datetime) -> CrewInstance:
    return CrewInstance(
        name="Daily Suspense Poem",
        trigger_type="interval",
        schedule_time="08:20",
        next_run_at=due,
        observation={},
        runtime_state="waiting",
    )


def test_a_run_missed_yesterday_is_skipped_when_todays_time_is_still_ahead():
    crew = poem(local(-1, "08:20"))
    assert (
        skip_stale_daily_run(crew, local(0, "07:00")) is True
    )  # laptop back at 7:00: no poem now AND at 8:20
    assert (
        crew.next_run_at.astimezone().strftime("%H:%M") == "08:20"
        and crew.next_run_at.astimezone().date() == local(0, "07:00").astimezone().date()
    )
    assert (
        crew.observation["skipped_run"]["day"] == local(-1, "08:20").astimezone().date().isoformat()
    )


def test_runs_that_should_happen_are_never_skipped():
    assert (
        skip_stale_daily_run(poem(local(-1, "08:20")), local(0, "10:05")) is False
    )  # today's time passed too: run now, as today's
    assert (
        skip_stale_daily_run(poem(local(0, "08:20")), local(0, "10:05")) is False
    )  # missed today: caught up now
    assert (
        skip_stale_daily_run(poem(local(-1, "14:00")), local(0, "07:00")) is False
    )  # woken on purpose, not its time
    no_time = CrewInstance(
        trigger_type="interval", schedule_time=None, next_run_at=local(-2, "08:20"), observation={}
    )
    assert (
        skip_stale_daily_run(no_time, local(0, "07:00")) is False
    )  # every N minutes: no time of day to keep


def decision(**d):
    return "DECISION: " + json.dumps(d)


def test_a_task_for_a_stopped_member_asks_the_person_instead_of_waiting_forever(tmp_path):
    factory = team(tmp_path)
    with factory() as session:
        lead, writer = session.get(CrewInstance, "lead"), session.get(CrewInstance, "writer")
        writer.is_running = False
        route_finished(session, lead, decision(next="Script Writer", task="Write it"))
        session.commit()
        assert lead.held and "Script Writer is stopped" in lead.question
        assert not writer.observation.get("handoffs")


def test_a_member_out_of_todays_calls_keeps_the_task_and_the_lead_hears_now(tmp_path):
    factory = team(tmp_path)
    with factory() as session:
        lead, writer = session.get(CrewInstance, "lead"), session.get(CrewInstance, "writer")
        writer.runtime_state = "budget_paused"
        lead.runtime_state = "waiting_event"
        route_finished(session, lead, decision(next="Script Writer", task="Write it"))
        session.commit()
        assert writer.observation["handoffs"][-1]["kind"] == "task"  # done tomorrow
        report = lead.observation["handoffs"][-1]
        assert (
            report["kind"] == "report"
            and report["trouble"] == "budget_paused"
            and "tomorrow" in report["result"]
        )
        assert lead.runtime_state == "waiting"  # it can choose someone else


def test_a_member_that_keeps_failing_tells_its_lead(tmp_path):
    factory = team(tmp_path)
    with factory() as session:
        lead, scout = session.get(CrewInstance, "lead"), session.get(CrewInstance, "scout")
        lead.runtime_state = "waiting_event"
        assert (
            report_trouble(session, scout, "error", "The model took too long to respond.") is True
        )
        session.commit()
        report = lead.observation["handoffs"][-1]
        assert (
            report["trouble"] == "error"
            and "took too long" in report["result"]
            and lead.runtime_state == "waiting"
        )
        alone = agent("alone", "Not In A Team")
        assert report_trouble(session, alone, "error", "x") is False  # no lead: nothing to tell
        assert (
            report_trouble(session, scout, "waiting", None) is False
        )  # a passing hiccup is retried, not reported


# ── Work that arrives while an agent is working is kept, and handled at once ─────────────────
from mavo.crew_workers import merge_arrivals  # noqa: E402


def test_what_arrives_during_a_run_is_merged_not_overwritten():
    mine = {
        "handoffs": [{"from": "Writer", "at": "2026-11-07T08:00:00", "result": "draft"}],
        "handoffs_used_at": "2026-11-07T08:00:00",
        "user_messages": ["yes"],
        "last_reply_seen": "yes",
        "automation_steps": 1,
    }
    saved = {
        "handoffs": [
            {"from": "Writer", "at": "2026-11-07T08:00:00", "result": "draft"},
            {"from": "Scout", "at": "2026-11-07T08:01:30", "result": "3 topics", "kind": "report"},
        ],
        "user_messages": ["yes", "yes"],
    }
    assert merge_arrivals(mine, saved) is True  # something new: it works again now
    assert [h["from"] for h in mine["handoffs"]] == ["Writer", "Scout"]
    assert (
        mine["user_messages"] == ["yes", "yes"] and mine["automation_steps"] == 1
    )  # its own progress is kept


def test_nothing_new_means_no_extra_run():
    mine = {
        "handoffs": [{"from": "Writer", "at": "2026-11-07T08:00:00"}],
        "handoffs_used_at": "2026-11-07T08:00:00",
    }
    assert (
        merge_arrivals(mine, {"handoffs": [{"from": "Writer", "at": "2026-11-07T08:00:00"}]})
        is False
    )


def test_a_reply_sent_while_it_worked_reaches_the_next_run(tmp_path):
    """End to end: the person replies while the agent is mid-run; the reply is not lost when the
    run saves, and the agent runs again for it."""
    import asyncio

    from mavo.crew_workers import CrewWorkerRuntime
    from mavo.database import ModelRoute, build_session_factory
    from mavo.migrations import run_migrations
    from tests.test_crew_workers import Router, add_crew  # noqa: F401

    url = f"sqlite:///{(tmp_path / 'w.db').as_posix()}"
    run_migrations(url)
    factory = build_session_factory(url)
    with factory() as session:
        session.add(ModelRoute(id="route", name="Test", provider="ollama", model="test"))
        session.commit()
    add_crew(factory)

    class RepliesMidRun(Router):
        async def chat(self, provider, model, messages, system, credential_id=None):
            self.calls.append(1)
            if (
                len(self.calls) == 1
            ):  # while it works, the person replies (saved by another session)
                with factory() as session:
                    row = session.get(CrewInstance, "crew")
                    row.observation = {
                        **row.observation,
                        "user_messages": ["Use the short version"],
                    }
                    session.commit()
            return json.dumps(
                {
                    "state": "wait_for_next_review",
                    "summary": "Done",
                    "next_step": "",
                    "notification": "",
                }
            )

    asyncio.run(CrewWorkerRuntime(factory, RepliesMidRun()).work("crew"))
    with factory() as session:
        row = session.get(CrewInstance, "crew")
        assert row.observation["user_messages"] == ["Use the short version"]  # kept
        due = row.next_run_at if row.next_run_at.tzinfo else row.next_run_at.replace(tzinfo=UTC)
        assert due <= datetime.now(UTC) + timedelta(seconds=5)  # and it works again for it now


# ── Unread work is never trimmed; held work is answered, not chatted past; deletes orphan no one ─
from mavo.pipelines import ORPHAN_MARK, trim_handoffs, unlink_removed  # noqa: E402


def test_a_burst_of_work_keeps_everything_not_yet_read():
    read = [{"from": "Old", "at": f"2026-11-07T07:0{i}:00"} for i in range(6)]
    unread = [{"from": f"Member {i}", "at": f"2026-11-07T08:{i:02d}:00"} for i in range(7)]
    kept = trim_handoffs(read + unread, used_at="2026-11-07T07:59:00")
    assert [h["from"] for h in kept if h["at"] > "2026-11-07T07:59:00"] == [
        f"Member {i}" for i in range(7)
    ]  # all 7 unread kept
    assert (
        len([h for h in kept if h["at"] <= "2026-11-07T07:59:00"]) == 4
    )  # a few read ones for context


def test_deleting_a_lead_or_a_source_leaves_no_one_waiting_forever(tmp_path):
    factory = team(tmp_path)
    with factory() as session:
        session.add(agent("digest", "Weekly Digest", upstream=["writer"]))
        session.add(agent("both", "Two Sources", upstream=["writer", "scout"]))
        for member in (
            "scout",
            "writer",
        ):  # team members work only when their lead says, as the planner sets them
            session.get(CrewInstance, member).trigger_type = "manual"
        session.commit()
        lead = session.get(CrewInstance, "lead")
        told = unlink_removed(session, lead)
        session.commit()
        scout = session.get(CrewInstance, "scout")
        assert "lead" not in scout.configuration_plan and scout.runtime_state == "needs_input"
        assert ORPHAN_MARK in scout.question and "Channel Lead, was deleted" in scout.question
        assert sorted(told) == ["Script Writer", "Topic Scout"]
        writer = session.get(CrewInstance, "writer")
        unlink_removed(session, writer)
        session.commit()
        assert (
            session.get(CrewInstance, "digest").configuration_plan["upstream"] == []
            and "was deleted" in session.get(CrewInstance, "digest").question
        )
        both = session.get(CrewInstance, "both")
        assert both.configuration_plan["upstream"] == ["scout"] and not (
            both.observation or {}
        ).get("question")  # still has a source: not bothered


def test_the_api_unlinks_on_delete_and_a_new_place_clears_the_note(planning):
    from tests.test_multi_agent import batch
    from tests.test_multi_agent import crew as proposal
    from tests.test_planner import turn

    client, auth, runtime, coordinator = planning
    coordinator.decision = batch(
        proposal("Channel Lead", trigger_type="interval", leads_team=True),
        proposal("Script Writer", lead="Channel Lead"),
        proposal("Editor"),
    )
    made = {c["name"]: c for c in turn(planning, "Run my channel")["crews"]}
    with runtime.factory() as session:
        for c in made.values():
            row = session.get(CrewInstance, c["id"])
            row.status, row.is_running = "active", True
        session.commit()
    assert (
        client.delete(f"/api/v1/crew/{made['Channel Lead']['id']}", headers=auth).status_code == 204
    )
    writer = next(
        c for c in client.get("/api/v1/crew", headers=auth).json() if c["name"] == "Script Writer"
    )
    assert writer["runtime_state"] == "needs_input" and "was deleted" in writer["question"]
    placed = client.patch(
        f"/api/v1/crew/{writer['id']}", headers=auth, json={"upstream": [made["Editor"]["id"]]}
    ).json()
    assert placed["question"] is None and placed["configuration_plan"]["upstream"] == [
        made["Editor"]["id"]
    ]


def test_a_message_to_an_agent_holding_work_is_turned_back_with_why(planning):
    from tests.test_multi_agent import batch
    from tests.test_multi_agent import crew as proposal
    from tests.test_planner import turn

    client, auth, runtime, coordinator = planning
    coordinator.decision = batch(proposal("YouTube Uploader"))
    made = turn(planning, "Upload my videos")["crews"][0]
    with runtime.factory() as session:
        row = session.get(CrewInstance, made["id"])
        row.status = "active"
        row.observation = {
            "held": {
                "kind": "approve",
                "from": "Editor",
                "at": "2026-11-07T08:00:00",
                "result": "Video ready",
            },
            "question": "Editor handed over work.",
        }
        session.commit()
    sent = client.post(
        f"/api/v1/crew/{made['id']}/asks", headers=auth, json={"question": "looks good"}
    )
    assert sent.status_code == 409 and "approve it or send it back" in sent.json()["detail"]
