"""A workflow that meets something it cannot handle fails safe: it never confirms what did not
happen, never makes a change twice, never lets one email stop the rest, never loses an email
quietly, and tells the outage (provider, Google) apart from an email that breaks it."""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from mavo.recipes import after_run
from mavo.tasks import done_key
from mavo.workflow import item_ids, split_items, tidy_contract
from tests.test_tasks import setup, start, wait

IST = timezone(timedelta(hours=5, minutes=30))


def iso(hour, minute=0):
    day = datetime.now(IST).date() + timedelta(days=3)
    while day.weekday() > 4:
        day += timedelta(days=1)
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST).isoformat()


def desk(client, auth, monkeypatch, understood, replies, refuse=()):
    """Two emails: Asha's free 3:30 (book, then confirm) and Kabir's busy 2:00 (offer times)."""
    runtime = setup(client, auth)
    mails = [
        {"id": "m1", "thread_id": "t1", "sender": "Asha <asha@example.com>", "subject": "Sync"},
        {"id": "m2", "thread_id": "t2", "sender": "Kabir <kabir@example.net>", "subject": "Call"},
    ]
    calendar = [
        {
            "id": "e1",
            "summary": "Review",
            "status": "confirmed",
            "start": {"dateTime": iso(14)},
            "end": {"dateTime": iso(15)},
        }
    ]

    async def chat(provider, model, messages, system, credential_id=None):
        prompt = messages[0]["content"]
        if "YOUR ROLE: extract" in prompt:
            return json.dumps(understood)
        if "YOUR ROLE: write" in prompt and "Reply to each" in prompt:
            return json.dumps(replies)
        return "Done."

    async def read(resource):
        return {"resource": resource, "items": mails if resource == "gmail" else calendar}

    async def honest(*_args, **_kwargs):
        return None

    runtime.router.chat = chat
    monkeypatch.setattr(runtime.task_runner, "google_readable", lambda: {"gmail", "calendar"})
    monkeypatch.setattr(runtime.google_pool, "read", read)
    monkeypatch.setattr(runtime.task_runner, "unsupported_claims", honest)
    made = []

    def propose(draft, crew_id=None, generation=None, event_key=""):
        made.append((draft.operation, dict(draft.arguments)))
        return {"id": f"a{len(made)}", "operation": draft.operation}

    async def maybe_execute(action_id):
        operation, arguments = made[int(action_id[1:]) - 1]
        if operation in refuse:
            return {"id": action_id, "status": "failed", "error": "The calendar changed"}
        return {"id": action_id, "status": "completed", "target_summary": "ok"}

    monkeypatch.setattr(runtime.actions, "propose", propose)
    monkeypatch.setattr(runtime.actions, "maybe_execute", maybe_execute)
    from mavo.workflow_write import check_workflow

    tags = {
        "read": {"gmail": "", "calendar": ""},
        "act": {"calendar.create": "", "gmail.send": ""},
        "think": {},
    }
    body = {
        "title": "Desk",
        "deliver": "Meetings",
        "final": "tell",
        "steps": [
            {"id": "mail", "kind": "read", "source": "gmail", "new_only": True},
            {"id": "cal", "kind": "read", "source": "calendar"},
            {
                "id": "understand",
                "kind": "helper",
                "role": "extract",
                "title": "Understand",
                "instructions": "Each email.",
                "uses": ["mail"],
                "returns": {
                    "fields": {
                        "thread_id": "id",
                        "sender": "email",
                        "subject": "text",
                        "offered": "times",
                    },
                    "required": ["thread_id", "sender", "subject"],
                },
            },
            {
                "id": "slots",
                "kind": "automation",
                "automation": "calendar.slots",
                "title": "Find",
                "instructions": "Find.",
                "uses": ["understand", "cal"],
                "params": {"requests": "understand", "timezone": "+05:30"},
            },
            {
                "id": "book",
                "kind": "act",
                "operation": "calendar.create",
                "args_from": "slots",
                "where": {"step": "slots", "field": "chosen", "is": "not_empty"},
                "args": {
                    "summary": "{{item.subject}}",
                    "start": "{{item.chosen.start}}",
                    "end": "{{item.chosen.end}}",
                },
            },
            {
                "id": "write",
                "kind": "helper",
                "role": "write",
                "title": "Replies",
                "instructions": "Reply to each.",
                "uses": ["slots"],
                "returns": {
                    "fields": {"to": "email", "subject": "text", "thread_id": "id", "body": "text"}
                },
            },
            {"id": "send", "kind": "act", "operation": "gmail.send", "args_from": "write"},
            {
                "id": "tell",
                "kind": "helper",
                "role": "write",
                "title": "Tell",
                "instructions": "Say what was done.",
                "uses": ["book", "send"],
            },
        ],
        "tests": [{"inputs": {}}, {"inputs": {}}, {"inputs": {}}],
    }
    plan, _ = check_workflow(body, tags)
    run = wait(client, auth, start(client, auth, plan.model_dump(mode="json")))
    return run, made


UNDERSTOOD = [
    {
        "thread_id": "t1",
        "sender": "asha@example.com",
        "subject": "Sync",
        "offered": [{"start": iso(15, 30), "end": iso(16)}],
    },
    {
        "thread_id": "t2",
        "sender": "kabir@example.net",
        "subject": "Call",
        "offered": [{"start": iso(14), "end": iso(14, 30)}],
    },
]
REPLIES = [
    {"to": "asha@example.com", "subject": "Re: Sync", "thread_id": "t1", "body": "Booked 3:30."},
    {"to": "kabir@example.net", "subject": "Re: Call", "thread_id": "t2", "body": "2:00 is busy."},
]


def test_a_confirmation_is_never_sent_for_a_booking_that_did_not_happen(client, auth, monkeypatch):
    run, made = desk(client, auth, monkeypatch, UNDERSTOOD, REPLIES, refuse={"calendar.create"})
    sent = [args["to"] for op, args in made if op == "gmail.send"]
    assert sent == ["kabir@example.net"]  # Asha's "booked" never went out; Kabir still answered
    send = next(s for s in run["steps"] if s["id"] == "send")
    assert (
        "Not done: Send & reply: not made, because an earlier action for it did not happen"
        in (send["output"])
    )


def test_one_unclear_email_is_set_aside_and_the_others_go_on(client, auth, monkeypatch):
    unclear = [UNDERSTOOD[0], {**UNDERSTOOD[1], "sender": "someone@invented.example"}]
    run, made = desk(client, auth, monkeypatch, unclear, REPLIES[:1])
    assert [op for op, _ in made] == ["calendar.create", "gmail.send"]  # Asha, fully
    assert run["status"] == "done", run
    assert "Needs you (Mavo could not read these clearly):" in run["result"]
    assert "not an address in what this step was given" in run["result"]


def test_a_retry_of_the_same_emails_finds_what_was_already_made_however_it_was_worded():
    step = SimpleNamespace(id="send", operation="gmail.send")
    first = {"to": "asha@example.com", "subject": "Re: Sync", "thread_id": "t1", "body": "Booked"}
    again = {**first, "body": "Hi Asha, it's booked for 3:30."}
    assert done_key("c1", step, ["m1"], first) == done_key("c1", step, ["m1"], again)
    # her next email in the same thread is new: answered, never taken as done
    assert done_key("c1", step, ["m1", "m3"], first) != done_key("c1", step, ["m1"], first)
    assert done_key("c1", step, [], first) is None  # nothing new read: nothing to retry
    assert item_ids({"thread_id": "t1", "subject": "Sync"}, {"to": "a@x.com"}) == {"thread:t1"}


def test_only_the_items_that_do_not_fit_are_set_aside():
    contract = tidy_contract({"fields": {"sender": "email", "intent": ["request", "other"]}})
    kept, aside = split_items(
        [{"sender": "a@example.com", "intent": "request"}, {"sender": "b", "intent": "maybe"}],
        contract,
        "a@example.com b",
    )
    assert kept == [{"sender": "a@example.com", "intent": "request"}]
    assert aside[0]["problems"] == [
        "sender must be one email address, with no name",
        "intent must be one of request, other",
    ]


def run_failed(outside=False):
    return SimpleNamespace(
        status="failed",
        error="Understand: the reply is not JSON",
        plan={"trigger_context": {"recipe": True}},
        steps=[{"id": "mail", "seen_ids": ["m9"]}, {"id": "understand", "outside": outside}],
    )


def test_an_email_that_keeps_breaking_a_tested_workflow_is_set_aside_after_three_tries():
    crew = SimpleNamespace(configuration_plan={"recipe_tested": True, "recipe": {}}, observation={})
    assert [after_run(crew, run_failed()) for _ in range(2)] == [False, False]
    last = run_failed()
    assert after_run(crew, last) is True  # the crew carries on with what comes next
    assert crew.observation["recipe_seen"]["mail"] == ["m9"]
    assert last.error.startswith("Mavo set aside 1 new item it could not handle after 3 tries")
    assert crew.configuration_plan["recipe"] == {}  # a tested workflow is kept, never re-planned


def test_an_outage_is_tried_again_and_never_sets_an_email_aside():
    crew = SimpleNamespace(configuration_plan={"recipe_tested": True, "recipe": {}}, observation={})
    assert not any(after_run(crew, run_failed(outside=True)) for _ in range(5))
    assert "recipe_seen" not in crew.observation  # the same emails are read again next run
