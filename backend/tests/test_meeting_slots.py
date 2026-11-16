"""Booking a meeting against a real calendar: Mavo itself refuses a busy time, whichever agent
chose it, and the time is chosen again from the free ones. A booker once took the 11:00 the
sender offered first, though the calendar showed it busy; its test had never had a clash."""

import json
from types import SimpleNamespace

import pytest

from mavo.calendar_conflicts import busy_reason, clash
from tests.test_tasks import setup, start, wait

IST = "+05:30"


def event(start, end, summary="Busy", **extra):
    return {
        "id": extra.pop("id", summary),
        "summary": summary,
        "status": "confirmed",
        "start": {"dateTime": f"2026-11-17T{start}:00{IST}"},
        "end": {"dateTime": f"2026-11-17T{end}:00{IST}"},
        **extra,
    }


def at(hhmm):
    return f"2026-11-17T{hhmm}:00{IST}"


# ---- the rule itself ---------------------------------------------------------------------------


def test_1_a_free_time_is_free():
    assert clash([event("09:00", "10:00")], at("11:00"), at("11:30")) is None


def test_2_the_first_offered_time_is_busy_the_next_is_free():
    calendar = [event("11:00", "11:30", "TEST – Busy Slot")]
    assert clash(calendar, at("11:00"), at("11:30"))["summary"] == "TEST – Busy Slot"
    assert clash(calendar, at("14:00"), at("14:30")) is None


def test_3_a_partial_overlap_is_busy():
    assert clash([event("10:15", "10:45")], at("10:00"), at("10:30")) is not None


def test_4_back_to_back_is_not_a_clash():
    assert clash([event("10:30", "11:00")], at("11:00"), at("11:30")) is None


def test_5_holidays_and_all_day_notes_do_not_block():
    holiday = {
        "summary": "Guru Nanak Jayanti",
        "start": {"date": "2026-11-17"},
        "end": {"date": "2026-11-18"},
        "read_only": True,
    }
    own_all_day = {"summary": "Mom's birthday", "start": {"date": "2026-11-17"}}
    assert clash([holiday, own_all_day], at("11:00"), at("11:30")) is None


def test_6_moving_an_event_does_not_clash_with_itself_and_cancelled_ones_are_gone():
    mine = event("11:00", "11:30", "Standup", id="e1")
    gone = event("14:00", "14:30", "Old call", status="cancelled")
    assert clash([mine], at("11:15"), at("11:45"), ignore_id="e1") is None
    assert clash([gone], at("14:00"), at("14:30")) is None


def test_7_the_reason_says_what_is_there_and_what_to_do():
    reason = busy_reason(event("11:00", "11:30", "TEST – Busy Slot"))
    assert "'TEST – Busy Slot'" in reason and "11:00–11:30" in reason
    assert "Choose another time that is free" in reason and "never move" in reason


# ---- through a whole task ------------------------------------------------------------------------

OFFER = (
    "Our available interview slots are: Tuesday, November 17, 2026 — 11:00–11:30 AM IST; "
    "Tuesday, November 17, 2026 — 2:00–2:30 PM IST; Wednesday, November 18, 2026 — "
    "10:00–10:30 AM IST. Please select the earliest available slot."
)


def booking_task(client, auth, monkeypatch, calendar, chosen, corrected):
    """A task that reads mail and calendar, chooses a time and books it. `chosen` is what the
    choosing step answers; `corrected` what the corrector answers once Mavo refuses it."""
    runtime = setup(client, auth)
    runner = runtime.task_runner
    seen = {"fixer": 0}

    async def chat(provider, model, messages, system, credential_id=None):
        if "fit an action's details" in system:
            seen["fixer"] += 1
            seen["fixer_prompt"] = messages[0]["content"]
            return json.dumps(corrected)
        return json.dumps(chosen)

    runtime.router.chat = chat
    monkeypatch.setattr(runner, "google_readable", lambda: {"gmail", "calendar"})

    async def read(resource):
        if resource == "gmail":
            return {
                "items": [
                    {
                        "id": "m1",
                        "sender": "Ananya <hr@example.com>",
                        "subject": "Interview",
                        "text": OFFER,
                    }
                ]
            }
        return {"items": calendar}

    monkeypatch.setattr(runtime.google_pool, "read", read)
    monkeypatch.setattr(runner, "may_read_mail_text", lambda run_id: False)
    made = []

    def propose(draft, crew_id=None, generation=None, event_key=""):
        made.append(dict(draft.arguments))
        return {"id": f"a{len(made)}"}

    async def maybe_execute(action_id):
        return {"id": action_id, "status": "completed", "target_summary": "Interview"}

    monkeypatch.setattr(runtime.actions, "propose", propose)
    monkeypatch.setattr(runtime.actions, "maybe_execute", maybe_execute)
    plan = {
        "title": "Book it",
        "deliver": "The booking",
        "final": "book",
        "steps": [
            {"id": "mail", "kind": "read", "source": "gmail"},
            {"id": "cal", "kind": "read", "source": "calendar"},
            {
                "id": "pick",
                "kind": "helper",
                "role": "extract",
                "title": "Pick a time",
                "instructions": "Choose the earliest offered time that is free.",
                "uses": ["mail", "cal"],
            },
            {"id": "book", "kind": "act", "operation": "calendar.create", "args_from": "pick"},
        ],
    }
    done = wait(client, auth, start(client, auth, plan))
    return done, made, seen


def interview(hhmm_start, hhmm_end):
    return {
        "summary": "NovaTech AI – Technical Interview",
        "start": at(hhmm_start),
        "end": at(hhmm_end),
    }


def test_8_a_busy_choice_is_refused_and_chosen_again_from_the_free_times(client, auth, monkeypatch):
    done, made, seen = booking_task(
        client,
        auth,
        monkeypatch,
        calendar=[event("11:00", "11:30", "TEST – Busy Slot")],
        chosen=[interview("11:00", "11:30")],
        corrected=interview("14:00", "14:30"),
    )
    assert [
        m["start"].isoformat() if hasattr(m["start"], "isoformat") else m["start"] for m in made
    ] == ["2026-11-17T14:00:00+05:30"]
    assert seen["fixer"] == 1 and "That time is busy" in seen["fixer_prompt"]
    assert "Change ONLY what was refused" in seen["fixer_prompt"]
    assert done["status"] == "done"


def test_9_when_every_offered_time_is_busy_nothing_is_booked_and_it_says_why(
    client, auth, monkeypatch
):
    done, made, _ = booking_task(
        client,
        auth,
        monkeypatch,
        calendar=[event("10:00", "12:00", "Workshop"), event("13:30", "15:00", "Lab")],
        chosen=[interview("11:00", "11:30")],
        corrected=interview("14:00", "14:30"),  # also busy: refused again
    )
    assert made == []
    assert "busy" in (done.get("error") or "") + (done.get("result") or "")


def test_10_two_requests_never_get_the_same_time(client, auth, monkeypatch):
    done, made, _ = booking_task(
        client,
        auth,
        monkeypatch,
        calendar=[],
        chosen=[
            interview("14:00", "14:30"),
            {**interview("14:00", "14:30"), "summary": "Second call"},
        ],
        corrected={},  # nothing else to offer for the second one
    )
    assert len(made) == 1
    assert "busy" in done["result"]


# ---- any agent, not only a task ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_11_an_agents_event_on_a_busy_time_is_refused_with_the_reason(client):
    actions = client.app.state.crew_runtime.actions

    async def read(resource):
        return {"items": [event("11:00", "11:30", "TEST – Busy Slot")]}

    actions.pool.read = read
    busy = SimpleNamespace(
        operation="calendar.create", crew_id=None, arguments=interview("11:00", "11:30")
    )
    free = SimpleNamespace(
        operation="calendar.create", crew_id=None, arguments=interview("14:00", "14:30")
    )
    assert "That time is busy" in await actions.busy(busy)
    assert await actions.busy(free) is None


@pytest.mark.asyncio
async def test_12_a_setup_test_checks_its_own_sample_calendar(client):
    from mavo.database import CrewInstance

    runtime = client.app.state.crew_runtime
    with runtime.factory() as session:
        session.add(
            CrewInstance(
                id="adapter",
                name="Booker test",
                template_id="schedule",
                responsibilities="Book.",
                permissions=["model:invoke"],
                provider="ollama",
                model="m",
                configuration_plan={
                    "sample_inputs": {"calendar": [event("11:00", "11:30", "Sample busy")]}
                },
            )
        )
        session.commit()

    async def never(resource):
        raise AssertionError("a test with a sample never reads the real calendar")

    runtime.actions.pool.read = never
    row = SimpleNamespace(
        operation="calendar.create", crew_id="adapter", arguments=interview("11:00", "11:30")
    )
    assert "'Sample busy'" in await runtime.actions.busy(row)


# ---- a correction never moves the time ----------------------------------------------------------


def test_13_the_times_a_message_states_are_read_the_same_however_written():
    from mavo.tasks import when_said

    assert when_said("Tuesday, 17 November 2026, 2:00 PM – 2:30 PM IST") == when_said(
        "Tue 17 November at 2 pm to 2:30 p.m."
    )
    assert when_said("2:00 PM on 17 November") != when_said("10:00 AM on 18 November")
    assert when_said("Thanks, see you then.") == frozenset()


def reply_task(client, auth, monkeypatch, redo_answer, corrector_answer):
    """A reply whose wording is refused ("booked", though no event was made). The step that
    wrote it answers `redo_answer` when asked again; the corrector `corrector_answer`."""
    runtime = setup(client, auth)
    runner = runtime.task_runner
    reply = {
        "to": "hr@example.com",
        "subject": "Re: Interview",
        "body": "I have booked Tuesday, 17 November, 2:00 PM - 2:30 PM.",
    }
    seen = {"corrector": [], "redo": 0}

    async def chat(provider, model, messages, system, credential_id=None):
        text = messages[0]["content"]
        if "check a message before it is sent" in system:
            if "booked" in text.split("MESSAGE:")[1].split("WHAT WAS READ")[0]:
                return '{"ok": false, "problem": "It says booked, but no event was made."}'
            return '{"ok": true}'
        if "fit an action's details" in system:
            seen["corrector"].append(text)
            return json.dumps(corrector_answer)
        if "mavo_refused" in text:
            seen["redo"] += 1
            return json.dumps(redo_answer)
        return json.dumps([reply])

    runtime.router.chat = chat
    monkeypatch.setattr(runner, "google_readable", lambda: {"gmail"})

    async def read(resource):
        return {
            "items": [
                {"id": "m1", "sender": "HR <hr@example.com>", "subject": "Interview", "text": OFFER}
            ]
        }

    monkeypatch.setattr(runtime.google_pool, "read", read)
    monkeypatch.setattr(runner, "may_read_mail_text", lambda run_id: False)
    sent = []

    def propose(draft, *a, **k):
        sent.append(dict(draft.arguments))
        return {"id": "x"}

    async def maybe_execute(action_id):
        return {"id": action_id, "status": "completed", "target_summary": "Re: Interview"}

    monkeypatch.setattr(runtime.actions, "propose", propose)
    monkeypatch.setattr(runtime.actions, "maybe_execute", maybe_execute)
    plan = {
        "title": "Reply",
        "deliver": "The reply",
        "final": "send",
        "steps": [
            {"id": "mail", "kind": "read", "source": "gmail"},
            {
                "id": "draft",
                "kind": "helper",
                "role": "extract",
                "title": "Draft",
                "instructions": "Draft the reply.",
                "uses": ["mail"],
            },
            {"id": "send", "kind": "act", "operation": "gmail.send", "args_from": "draft"},
        ],
    }
    done = wait(client, auth, start(client, auth, plan))
    return done, sent, seen


def test_14_the_step_that_wrote_the_reply_fixes_its_words_and_keeps_the_time(
    client, auth, monkeypatch
):
    fixed = {
        "to": "hr@example.com",
        "subject": "Re: Interview",
        "body": "Tuesday, 17 November, 2:00 PM - 2:30 PM works for me.",
    }
    done, sent, seen = reply_task(client, auth, monkeypatch, [fixed], {})
    assert seen["redo"] == 1 and seen["corrector"] == []  # its writer, not a stranger
    assert [m["body"] for m in sent] == [fixed["body"]]
    assert done["status"] == "done"


def test_15_a_correction_that_moves_the_time_is_never_sent(client, auth, monkeypatch):
    """The writer could not redo it; the corrector moved the free 2:00 PM to Wednesday, twice.
    Nothing goes out with a time nobody chose."""
    moved = {
        "to": "hr@example.com",
        "subject": "Re: Interview",
        "body": "Wednesday, 18 November, 10:00 AM - 10:30 AM works for me.",
    }
    done, sent, seen = reply_task(client, auth, monkeypatch, [], moved)
    assert sent == []
    assert len(seen["corrector"]) == 2
    assert "Keep these dates and times exactly" in seen["corrector"][1]
    assert "changed the meeting time" in json.dumps(done)


# ---- a reply is a reply -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_16_a_reply_without_its_thread_still_goes_into_the_conversation():
    """A reply to an interview invitation went out as a separate new email: no agent had given
    its thread id. Mavo finds the conversation itself."""
    from urllib.parse import unquote

    from mavo.google_actions import GoogleActions

    asked = []

    class Reply:
        status_code, is_error = 200, False

        def __init__(self, body):
            self.body = body

        def json(self):
            return self.body

    async def request(service, capability, method, path, *a, **k):
        asked.append(unquote(path))
        if path.startswith("/users/me/messages?"):
            return Reply({"messages": [{"id": "m1", "threadId": "t-novatech"}]})
        return Reply(
            {
                "messages": [
                    {
                        "payload": {
                            "headers": [{"name": "Message-ID", "value": "<invite@mail.gmail.com>"}]
                        }
                    }
                ]
            }
        )

    me = SimpleNamespace(request=request)
    me.thread_answered = lambda args, action: GoogleActions.thread_answered(me, args, action)
    args = {
        "to": "Ananya <hr@example.com>",
        "subject": "Re: Technical Interview Invitation – AI Engineer Intern",
    }
    thread, in_reply_to, _ = await GoogleActions.reply_headers(me, args, None)
    assert (thread, in_reply_to) == ("t-novatech", "<invite@mail.gmail.com>")
    assert 'from:hr@example.com subject:"Technical Interview Invitation' in asked[0]

    asked.clear()
    new = {"to": "hr@example.com", "subject": "Hello"}  # not a reply: stays a new email
    assert await GoogleActions.reply_headers(me, new, None) == (None, None, None)
    assert asked == []


# ---- a crew keeps every step of its recipe ------------------------------------------------------


def test_17_a_saved_recipe_keeps_the_steps_mavo_added_on_every_run():
    """A crew's recipe had nine steps once Mavo added the details its actions take; read back
    on every run it was cut to eight, and "Send & reply", the last, never ran: four events were
    made and no reply was sent."""
    from mavo.task_plan import TaskPlan, validate_planning_graph

    def helper(i, uses=()):
        return {
            "id": i,
            "kind": "helper",
            "role": "extract",
            "title": i,
            "instructions": "Give it.",
            "uses": list(uses),
        }

    steps = [
        {"id": "s1", "kind": "read", "source": "gmail"},
        {"id": "s2", "kind": "read", "source": "calendar"},
        helper("s3", ["s1"]),
        helper("s4", ["s3", "s2"]),
        helper("s5_args", ["s4"]),
        {"id": "s5", "kind": "act", "operation": "calendar.create", "args_from": "s5_args"},
        helper("s6", ["s5"]),
        helper("s7_args", ["s6"]),
        {"id": "s7", "kind": "act", "operation": "gmail.send", "args_from": "s7_args"},
    ]
    saved = {"title": "Slot Reply Desk", "deliver": "Replies", "final": "s7", "steps": steps}
    replayed = TaskPlan.model_validate(TaskPlan.model_validate(saved).model_dump())
    assert [s.id for s in replayed.steps][-1] == "s7"  # the send is still there
    with pytest.raises(ValueError):  # what a planner writes is still at most eight steps
        validate_planning_graph(saved)
