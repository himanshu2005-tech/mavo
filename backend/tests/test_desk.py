"""The guarded agent, on fake models: whatever it decides, every action goes through code that
refuses what is not safe. These run once for every crew; a crew gets no test of its own."""

import json
import re
import time
from datetime import datetime, timedelta
from uuid import uuid4

from mavo.database import CrewInstance, TaskRun
from mavo.desk import DeskSettings, desk_plan, times_in
from mavo.task_plan import TaskPlan
from mavo.workflow_write import zone_label
from tests.test_tasks import setup

LOCAL = datetime.now().astimezone().tzinfo  # the person's time zone is their computer's


def at(hour, minute=0, days=3):
    day = datetime.now(LOCAL).date() + timedelta(days=days)
    while day.weekday() > 4:
        day += timedelta(days=1)
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=LOCAL).isoformat()


def mail(n, sender, subject, text, thread=None):
    return {
        "id": f"m{n}",
        "thread_id": thread or f"t{n}",
        "sender": sender,
        "subject": subject,
        "text": text,
    }


BUSY = [
    {
        "id": "e1",
        "summary": "Review",
        "status": "confirmed",
        "start": {"dateTime": at(14)},
        "end": {"dateTime": at(15)},
    }
]


class Desk:
    """A crew with the guarded agent, its reads faked, its actions recorded, its agent a fake."""

    def __init__(
        self, client, auth, monkeypatch, actions, sources=("gmail", "calendar"), **settings
    ):
        self.client, self.auth = client, auth
        self.runtime = runtime = setup(client, auth)
        self.mails, self.calendar, self.made, self.rows = [], list(BUSY), [], []
        self.brain = None
        self.prompts = []

        async def read(resource):
            return {
                "resource": resource,
                "items": self.mails if resource == "gmail" else self.calendar,
            }

        async def honest(*_args, **_kwargs):
            return None

        def propose(draft, crew_id=None, generation=None, event_key=""):
            self.made.append((draft.operation, dict(draft.arguments)))
            from mavo.database import ActionRequest

            with runtime.factory() as session:  # recorded as the app records it
                row = ActionRequest(
                    crew_id=crew_id,
                    account_subject="test",
                    operation=draft.operation,
                    arguments=dict(draft.arguments),
                    fingerprint=str(uuid4()).replace("-", ""),
                    status="pending",
                    target_summary=str(draft.arguments.get("summary") or "") or None,
                )
                session.add(row)
                session.commit()
                self.rows.append(row.id)
                return {"id": row.id}

        self.waits = False  # its first changes wait for the person's OK

        async def maybe_execute(action_id):
            from mavo.database import ActionRequest

            status = "pending" if self.waits else "completed"
            with runtime.factory() as session:
                row = session.get(ActionRequest, action_id)
                row.status = status
                if status == "completed" and row.operation == "calendar.create":
                    row.result = {"id": f"event-{action_id[:8]}"}  # as Google answers
                    self.calendar.append(
                        {  # and it is on the calendar from then on
                            "id": row.result["id"],
                            "etag": f'"etag-{action_id[:6]}"',
                            "summary": row.arguments.get("summary"),
                            "status": "confirmed",
                            "start": {"dateTime": row.arguments["start"]},
                            "end": {"dateTime": row.arguments["end"]},
                        }
                    )
                session.commit()
            return {"id": action_id, "status": status, "target_summary": "ok"}

        async def chat(provider, model, messages, system, credential_id=None):
            self.prompts.append(messages[0]["content"])
            return json.dumps(self.brain(messages[0]["content"]))

        monkeypatch.setattr(runtime.google_pool, "read", read)
        monkeypatch.setattr(
            runtime.task_runner, "google_readable", lambda: {"gmail", "calendar", "tasks"}
        )
        monkeypatch.setattr(runtime.task_runner, "unsupported_claims", honest)
        monkeypatch.setattr(runtime.actions, "propose", propose)
        monkeypatch.setattr(runtime.actions, "maybe_execute", maybe_execute)
        runtime.router.chat = chat
        self.settings = DeskSettings.model_validate({"timezone": zone_label(), **settings})
        self.sources = list(sources)
        with runtime.factory() as session:
            crew = CrewInstance(
                name="Desk",
                template_id="research",
                status="active",
                is_running=True,
                provider="ollama",
                model="m",
                permissions=["model:invoke"],
                trigger_type="gmail_message",
                responsibilities="Book meetings people ask for.",
                configuration_plan={
                    "changes_things": True,
                    "composition": {"agents": [{"key": "agent", "actions": list(actions)}]},
                },
            )
            session.add(crew)
            session.commit()
            self.crew_id = crew.id

    def run(self, brain) -> str:
        self.brain = brain
        plan = TaskPlan.model_validate(desk_plan(self.sources, self.settings, "Book meetings."))
        run_id = str(uuid4())
        runner = self.runtime.task_runner

        async def begin():  # inside the app's own loop, as a crew's run begins
            with self.runtime.factory() as session:
                run = runner.prepare(session, plan, "desk", run_id)
                crew = session.get(CrewInstance, self.crew_id)
                run.crew_id, run.crew_generation = crew.id, crew.runtime_generation
                session.commit()
            runner.schedule(run_id)

        self.client.portal.call(begin)
        for _ in range(400):
            with self.runtime.factory() as session:
                run = session.get(TaskRun, run_id)
                if run.status not in {"running", "queued"}:
                    return run.result or run.error or run.status
            time.sleep(0.02)
        raise AssertionError("the run did not end")

    def memory(self) -> dict:
        with self.runtime.factory() as session:
            return dict(session.get(CrewInstance, self.crew_id).observation or {})


def did(prompt: str) -> list[str]:
    """The tools the fake agent already used for this item, from its own prompt."""
    part = prompt.split("WHAT YOU DID SO FAR:")[-1] if "WHAT YOU DID SO FAR:" in prompt else ""
    return re.findall(r"^([\w.]+) ", part, re.M)


def found(prompt: str) -> dict:
    """What find_free_times answered, from the prompt."""
    match = re.search(r"find_free_times .*? -> (\{.*\})", prompt)
    found = json.loads(match.group(1)) if match else {}
    return {
        "chosen": found.get("their_time"),
        "alternatives": found.get("offer_instead"),
        "why": found.get("why"),
    }


def spoken(iso: str) -> str:
    return datetime.fromisoformat(iso).astimezone(LOCAL).strftime("%I:%M %p").lstrip("0")


def test_a_free_offered_time_is_booked_then_confirmed_in_the_thread(client, auth, monkeypatch):
    desk = Desk(client, auth, monkeypatch, ["calendar.create", "gmail.send"])
    desk.mails = [mail(1, "Asha <asha@example.com>", "Sync", "Can we meet at 3:30 PM?")]

    def brain(prompt):
        steps = did(prompt)
        if "find_free_times" not in steps:
            return {
                "tool": "find_free_times",
                "args": {"offered": [{"start": at(15, 30), "end": at(16)}]},
            }
        chosen = found(prompt)["chosen"]
        if "calendar.create" not in steps:
            return {"tool": "calendar.create", "args": {"summary": "Sync with Asha", **chosen}}
        if "gmail.send" not in steps:
            return {
                "tool": "gmail.send",
                "args": {"body": f"{spoken(chosen['start'])} works. It is booked."},
            }
        return {"tool": "done", "args": {"summary": "Booked and confirmed."}}

    result = desk.run(brain)
    assert [op for op, _ in desk.made] == ["calendar.create", "gmail.send"]
    booking, reply = desk.made[0][1], desk.made[1][1]
    assert booking["start"] == at(15, 30)
    # code filled the address, thread and subject: the agent gave only the words
    assert (reply["to"], reply["thread_id"], reply["subject"]) == (
        "asha@example.com",
        "t1",
        "Re: Sync",
    )
    # The report in plain words, written by code from what was done: whose email, then each
    # thing done. Not the agent's own sentence over raw "Done: Create event (...)" lines.
    lines = result.splitlines()
    assert lines[0] == "Asha · Sync"
    assert re.fullmatch(r"- Booked \w{3} \d+ \w{3}, 3:30–4:00 PM · Sync with Asha", lines[1])
    assert lines[2].startswith("- Replied: “") and lines[2].endswith("works. It is booked.”")
    assert "Done:" not in result and "Booked and confirmed." not in result
    assert "Needs you" not in result
    with desk.runtime.factory() as session:  # what it was thinking, move by move
        run = session.query(TaskRun).filter(TaskRun.crew_id == desk.crew_id).one()
        thoughts = next(s["thinking"] for s in run.steps if s["id"] == "desk")
    labels = [t["label"] for t in thoughts]
    assert labels[0] == "Asha: Sync" and thoughts[0]["text"] == "Reading it."
    assert "Asha: Sync · checking your calendar" in labels
    assert "Asha: Sync · booking" in labels and "Asha: Sync · sending an email" in labels
    checked = next(t for t in thoughts if t["label"].endswith("checking your calendar"))
    assert checked["text"].startswith("Free: ")


def test_a_busy_time_gets_only_times_code_found_free_and_no_booking(client, auth, monkeypatch):
    desk = Desk(client, auth, monkeypatch, ["calendar.create", "gmail.send"])
    desk.mails = [mail(1, "Kabir <kabir@example.net>", "Call", "Can we talk at 2:30 PM?")]

    def brain(prompt):
        steps = did(prompt)
        if "find_free_times" not in steps:
            return {
                "tool": "find_free_times",
                "args": {"offered": [{"start": at(14, 30), "end": at(15)}]},
            }
        if "gmail.send" not in steps:
            first = found(prompt)["alternatives"][0]["start"]
            return {
                "tool": "gmail.send",
                "args": {"body": f"2:30 is taken. Could you do {spoken(first)} instead?"},
            }
        return {"tool": "done", "args": {"summary": "Offered another time."}}

    desk.run(brain)
    assert [op for op, _ in desk.made] == ["gmail.send"]  # nothing booked


def test_what_code_never_checked_is_refused_and_the_agent_is_told_why(client, auth, monkeypatch):
    desk = Desk(client, auth, monkeypatch, ["calendar.create", "gmail.send"])
    desk.mails = [mail(1, "Asha <asha@example.com>", "Sync", "Meet at 3:30 PM?")]
    tries = iter(
        [
            {
                "tool": "calendar.create",
                "args": {"summary": "x", "start": at(20), "end": at(20, 30)},
            },
            {"tool": "gmail.send", "args": {"body": "See you at 11:00 AM."}},
            {
                "tool": "gmail.send",
                "args": {"to": "stranger@evil.example", "subject": "Hi", "body": "Hello."},
            },
            {"tool": "done", "args": {"summary": "Gave up."}},
        ]
    )

    def brain(prompt):
        return next(tries)

    desk.run(brain)
    assert desk.made == []  # none of it reached anyone
    last = desk.prompts[-1]
    assert "book only a time find_free_times gave" in last
    assert "names 11:00, which find_free_times did not give" in last
    assert "stranger@evil.example has not written to you" in last


def test_a_later_email_in_the_thread_books_what_we_offered_and_nothing_runs_twice(
    client, auth, monkeypatch
):
    desk = Desk(client, auth, monkeypatch, ["calendar.create", "gmail.send"])
    desk.mails = [mail(1, "Kabir <kabir@example.net>", "Call", "2:30 PM?")]
    offered = {}

    def first(prompt):
        steps = did(prompt)
        if "find_free_times" not in steps:
            return {
                "tool": "find_free_times",
                "args": {"offered": [{"start": at(14, 30), "end": at(15)}]},
            }
        if "gmail.send" not in steps:
            offered.update(found(prompt)["alternatives"][0])
            return {
                "tool": "gmail.send",
                "args": {"body": f"How about {spoken(offered['start'])}?"},
            }
        return {"tool": "done", "args": {"summary": "Offered."}}

    desk.run(first)
    memory = desk.memory()
    assert memory["desk_handled"] == ["m1"]
    assert memory["desk_memory"]["t1"]["round"] == 1
    # The same email again (a retry): handled already, nothing is done.
    assert desk.run(first) == "Nothing new." and len(desk.made) == 1
    # Kabir accepts in the same thread: the time we offered is booked, without asking again.
    desk.mails = [
        mail(
            2,
            "Kabir <kabir@example.net>",
            "Re: Call",
            f"{spoken(offered['start'])} works.",
            thread="t1",
        )
    ]

    def second(prompt):
        steps = did(prompt)
        assert "THIS CONVERSATION SO FAR" in prompt  # the agent sees what was offered
        if "calendar.create" not in steps:
            return {"tool": "calendar.create", "args": {"summary": "Call with Kabir", **offered}}
        return {"tool": "done", "args": {"summary": "Booked."}}

    desk.run(second)
    assert [op for op, _ in desk.made] == ["gmail.send", "calendar.create"]
    assert desk.made[1][1]["start"] == offered["start"]


def test_the_round_limit_stops_replies_and_asks_the_person(client, auth, monkeypatch):
    desk = Desk(client, auth, monkeypatch, ["calendar.create", "gmail.send"], max_rounds=1)
    desk.mails = [mail(1, "Kabir <kabir@example.net>", "Call", "Can we talk?")]

    def reply(prompt):
        steps = did(prompt)
        if "find_free_times" not in steps:
            return {"tool": "find_free_times", "args": {}}
        if "gmail.send" not in steps:
            first = found(prompt)["alternatives"][0]["start"]
            return {"tool": "gmail.send", "args": {"body": f"How about {spoken(first)}?"}}
        if "ask_you" not in steps and "refused" in prompt.split("WHAT YOU DID SO FAR:")[-1]:
            return {"tool": "ask_you", "args": {"question": "They keep declining."}}
        return {"tool": "done", "args": {"summary": "Asked."}}

    desk.run(reply)
    desk.mails = [mail(2, "Kabir <kabir@example.net>", "Re: Call", "No, other times?", thread="t1")]
    result = desk.run(reply)
    assert [op for op, _ in desk.made] == ["gmail.send"]  # the second reply was refused
    assert "the most allowed" in desk.prompts[-1] and "Needs you" in result


def test_it_works_for_other_crews_too_tasks_from_mail(client, auth, monkeypatch):
    desk = Desk(client, auth, monkeypatch, ["tasks.create"], sources=("gmail",))
    desk.mails = [mail(1, "Boss <boss@example.com>", "Report", "Send the report by Friday.")]

    def brain(prompt):
        if "tasks.create" not in did(prompt):
            return {"tool": "tasks.create", "args": {"title": "Send the report"}}
        return {"tool": "done", "args": {"summary": "Added the task."}}

    desk.run(brain)
    assert desk.made == [("tasks.create", {"title": "Send the report"})]


def test_a_confused_agent_ends_in_needs_you_never_in_a_guess(client, auth, monkeypatch):
    desk = Desk(client, auth, monkeypatch, ["calendar.create", "gmail.send"])
    desk.mails = [mail(1, "Asha <asha@example.com>", "Sync", "Meet?")]
    desk.brain = None

    def nonsense(prompt):
        return "not a tool call"

    result = desk.run(nonsense)
    assert desk.made == [] and "Needs you" in result and "could not finish" in result


def test_times_a_message_names_are_read_as_clock_times():
    assert times_in("Could you do 3:30 PM or 11 am, or 14:00?") == {"15:30", "11:00", "14:00"}
    assert times_in("2:30 is taken") == {"02:30", "14:30"}
    assert times_in("Room 12 on floor 3") == set()


def test_a_change_waiting_for_the_ok_is_not_repeated_and_the_agent_carries_on(
    client, auth, monkeypatch
):
    """An agent booked, saw it wait for the person's OK, booked twice more and ran out of
    tries without replying. Now it is told plainly, a repeat never runs, and it carries on."""
    desk = Desk(client, auth, monkeypatch, ["calendar.create", "gmail.send"])
    desk.waits = True
    desk.mails = [mail(1, "Asha <asha@example.com>", "Sync", "Thursday at 3:30 PM?")]
    booking = {"summary": "Sync", "start": at(15, 30), "end": at(16)}
    moves = iter(
        [
            {
                "tool": "find_free_times",
                "args": {"offered": [{"start": at(15, 30), "end": at(16)}]},
            },
            {"tool": "calendar.create", "args": booking},
            {"tool": "calendar.create", "args": booking},  # again, as the real one did
            {"tool": "gmail.send", "args": {"body": "Thursday 3:30 PM works for me."}},
            {"tool": "done", "args": {"summary": "Proposed Thursday 3:30 PM."}},
        ]
    )
    result = desk.run(lambda prompt: next(moves))
    assert [op for op, _ in desk.made] == ["calendar.create", "gmail.send"]  # once each
    assert "waits for the person's OK" in desk.prompts[2]
    assert "you already did exactly this" in desk.prompts[3]
    assert "Needs you" not in result
    from mavo.reminders import create_due_reminders

    with desk.runtime.factory() as session:  # on the desktop at once, though alerts are off
        due = create_due_reminders(session)
        approvals = [e for e in due if e.kind == "approval"]
        assert len(approvals) == 2  # the booking, and the reply
        assert approvals[0].message == "Waiting for your OK: Create event (Sync)."
        assert approvals[0].task_step_id == f"action:{desk.rows[0]}"  # its buttons answer it


def test_mail_from_before_it_was_turned_on_is_left_alone(client, auth, monkeypatch):
    desk = Desk(client, auth, monkeypatch, ["calendar.create", "gmail.send"])
    with desk.runtime.factory() as session:
        crew = session.get(CrewInstance, desk.crew_id)
        crew.configuration_plan = {
            **crew.configuration_plan,
            "recipe_at": datetime.now().astimezone().isoformat(),
        }
        session.commit()
    old = mail(1, "Asha <asha@example.com>", "Old", "From last week.")
    old["date"] = "Mon, 09 Nov 2026 10:00:00 +0530"
    desk.mails = [old]
    assert desk.run(lambda prompt: {"tool": "done", "args": {}}) == "Nothing new."
    assert desk.prompts == []  # not even looked at by a model


def test_a_booking_waiting_for_the_ok_holds_its_time_against_a_second_email(
    client, auth, monkeypatch
):
    """Two emails about the same meeting: the second booked the same slot while the first
    waited for the person's OK."""
    desk = Desk(client, auth, monkeypatch, ["calendar.create", "gmail.send"])
    desk.waits = True
    offered = [{"start": at(15, 30), "end": at(16)}]
    for n in (1, 2):
        desk.mails = [mail(n, "Asha <asha@example.com>", f"Sync {n}", "Thursday 3:30 PM?")]

        def brain(prompt):
            steps = did(prompt)
            if "find_free_times" not in steps:
                return {"tool": "find_free_times", "args": {"offered": offered}}
            chosen = found(prompt).get("chosen")
            if chosen and "calendar.create" not in steps:
                return {"tool": "calendar.create", "args": {"summary": "Sync", **chosen}}
            return {"tool": "done", "args": {"summary": "ok"}}

        desk.run(brain)
    bookings = [args["start"] for op, args in desk.made if op == "calendar.create"]
    assert bookings == [at(15, 30)]  # the second saw 3:30 as taken


def test_the_person_approves_a_waiting_change_and_its_alert_goes(client, auth, monkeypatch):
    from mavo.database import ActionRequest, ReminderEvent

    desk = Desk(client, auth, monkeypatch, ["calendar.create", "gmail.send"])
    desk.waits = True
    with desk.runtime.factory() as session:
        crew = session.get(CrewInstance, desk.crew_id)
        crew.configuration_plan = {
            **crew.configuration_plan,
            "ask_first_choice": True,
            "ask_first": {"since": "2026-01-01T00:00:00+00:00", "count": 3},
        }
        session.commit()
    desk.mails = [mail(1, "Asha <asha@example.com>", "Sync", "Thursday 3:30 PM?")]
    moves = iter(
        [
            {
                "tool": "find_free_times",
                "args": {"offered": [{"start": at(15, 30), "end": at(16)}]},
            },
            {
                "tool": "calendar.create",
                "args": {"summary": "Sync", "start": at(15, 30), "end": at(16)},
            },
            {"tool": "done", "args": {}},
        ]
    )
    desk.run(lambda prompt: next(moves))
    ran = []

    async def run_approved(action_id):
        ran.append(action_id)

    monkeypatch.setattr(desk.runtime.actions, "run_approved", run_approved)
    monkeypatch.setattr(desk.runtime.actions, "check_actor", lambda session, row: None)
    action_id = desk.rows[0]
    answered = client.post(f"/api/v1/actions/{action_id}/approve", headers=auth)
    assert answered.status_code == 200, answered.text
    with desk.runtime.factory() as session:
        assert session.get(ActionRequest, action_id).approved_by == "user"
        alert = (
            session.query(ReminderEvent)
            .filter(ReminderEvent.deduplication_key == f"approval:{action_id}")
            .one()
        )
        assert alert.status == "dismissed"
    again = client.post(f"/api/v1/actions/{action_id}/approve", headers=auth)
    assert again.status_code == 409  # answered once


def test_a_busy_request_is_answered_with_offers_never_booked_on_its_own(client, auth, monkeypatch):
    """Their times were busy: it booked a free time they never agreed to, mentioned the
    person's hours and was refused, then every corrected reply was blocked too."""
    desk = Desk(client, auth, monkeypatch, ["calendar.create", "gmail.send"])
    desk.mails = [mail(1, "Ravi <ravi@example.com>", "Roadmap", "Can we meet at 2:30 PM?")]
    state = {}

    def brain(prompt):
        steps = did(prompt)
        if "find_free_times" not in steps:
            return {
                "tool": "find_free_times",
                "args": {"offered": [{"start": at(14, 30), "end": at(15)}]},
            }
        alt = found(prompt)["alternatives"][0]
        if "calendar.create" not in steps:
            return {"tool": "calendar.create", "args": {"summary": "Roadmap", **alt}}
        if not state.get("first"):
            state["first"] = True  # a reply the truth check refuses
            return {"tool": "gmail.send", "args": {"body": "Booked for Thursday 99 Nov."}}
        if not state.get("second"):
            state["second"] = True
            return {
                "tool": "gmail.send",
                "args": {"body": f"I work 9:00 to 18:00. Could you do {spoken(alt['start'])}?"},
            }
        return {"tool": "done", "args": {"summary": "Offered a time."}}

    async def picky(run_id, plan, step, arguments, evidence):
        return "it says booked" if "Booked" in str(arguments.get("body")) else None

    monkeypatch.setattr(desk.runtime.task_runner, "unsupported_claims", picky)
    result = desk.run(brain)
    assert [op for op, _ in desk.made] == ["gmail.send"]  # no booking they never agreed to
    assert "book only a time they offered" in "\n".join(desk.prompts)
    assert "- Replied: “I work 9:00 to 18:00." in result  # the offer that went out


def test_free_times_come_with_the_words_to_say_and_the_right_weekday():
    from mavo.desk import spoken_slot, tool_list

    slot = spoken_slot({"start": "2026-11-16T14:30:00+05:30", "end": "2026-11-16T15:00:00+05:30"})
    local = datetime.fromisoformat(slot["start"]).astimezone()
    assert slot["say"].startswith(f"{local:%a} {local.day} {local:%b},")  # never a made-up day
    assert slot["start"] == "2026-11-16T14:30:00+05:30"  # to book with, unchanged
    told = tool_list(["gmail.send", "calendar.create"], True)
    assert '"offer_instead" (free times to OFFER in a reply' in told
    assert 'gmail.send {"body": text}: reply to this email. Code fills the address' in told
