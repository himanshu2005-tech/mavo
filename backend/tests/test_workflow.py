"""A workflow's code parts: contracts between steps, gates, and finding a free time. Every case
here failed when a model did it: a busy 2:30 tried three times though 3:30 was offered and
free, 11:30 offered inside an 11:00 to 12:00 call, an accepted time never booked."""

import json
from datetime import datetime, timedelta, timezone

from mavo.workflow import (
    contract_errors,
    contract_text,
    fill_item,
    find_slots,
    gate_met,
    only_passing,
    tidy_contract,
    tidy_gate,
)
from tests.test_tasks import scripted, setup, start, wait

IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime(2026, 11, 14, 17, 30, tzinfo=IST)  # a Saturday afternoon
PARAMS = {
    "timezone": "+05:30",
    "start": "09:00",
    "end": "18:00",
    "days": ["mon", "tue", "wed", "thu", "fri"],
    "duration_minutes": 30,
    "alternatives": 3,
    "horizon_days": 7,
}


def at(day, hour, minute=0):
    return datetime(2026, 11, day, hour, minute, tzinfo=IST).isoformat()


def event(title, day, h1, h2):
    return {
        "summary": title,
        "status": "confirmed",
        "start": {"dateTime": at(day, h1)},
        "end": {"dateTime": at(day, h2)},
    }


CALENDAR = [
    event("Team standup", 16, 10, 11),
    event("Client call", 17, 11, 12),
    event("Project review", 19, 14, 15),
]


def offer(day, hour, minute=0, minutes=30):
    start = datetime(2026, 11, day, hour, minute, tzinfo=IST)
    return {"start": start.isoformat(), "end": (start + timedelta(minutes=minutes)).isoformat()}


def slot(requests):
    return find_slots(requests, CALENDAR, PARAMS, now=NOW)


def test_the_first_offered_time_that_is_free_is_taken():
    [found] = slot([{"offered": [offer(19, 14, 30), offer(19, 15, 30)]}])
    assert found["chosen"] == offer(19, 15, 30)  # 2:30 clashes with 2 to 3; 3:30 is free
    assert "busy (Project review)" in found["why"] and found["alternatives"] == []


def test_when_every_offered_time_is_busy_free_times_are_found_never_inside_an_event():
    [found] = slot([{"offered": [offer(16, 10), offer(17, 11)]}])
    assert found["chosen"] is None and len(found["alternatives"]) == 3
    for alt in found["alternatives"]:
        start = datetime.fromisoformat(alt["start"])
        end = datetime.fromisoformat(alt["end"])
        assert start > NOW and start.weekday() < 5
        assert 9 <= start.hour and (end.hour, end.minute) <= (18, 0)
        assert not any(  # never inside an event (11:30 inside an 11 to 12 call was offered)
            start < datetime.fromisoformat(e["end"]["dateTime"])
            and end > datetime.fromisoformat(e["start"]["dateTime"])
            for e in CALENDAR
        )


def test_times_outside_the_hours_or_days_are_not_taken():
    [found] = slot([{"offered": [offer(18, 20), offer(19, 7, 30), offer(21, 11)]}])
    assert found["chosen"] is None and found["alternatives"]
    assert (
        "outside 09:00 to 18:00" in found["why"] and "Saturday is not a working day" in found["why"]
    )


def test_no_time_given_gets_free_times_and_two_requests_never_get_one_time():
    [none] = slot([{"offered": []}])
    assert none["chosen"] is None and len(none["alternatives"]) == 3
    assert none["why"] == "no time was offered"
    first, second = slot([{"offered": [offer(16, 16)]}, {"offered": [offer(16, 16)]}])
    assert first["chosen"] == offer(16, 16) and second["chosen"] is None


def test_a_contract_says_exactly_what_does_not_fit():
    contract = tidy_contract(
        {
            "fields": {
                "thread_id": "id",
                "sender": "email",
                "intent": ["request", "cancel"],
                "offered": "times",
            },
            "required": ["thread_id", "sender", "intent"],
        }
    )
    assert "ONLY a JSON array" in contract_text(contract)
    seen = "thread t1 from asha@example.com"
    good = [
        {
            "thread_id": "t1",
            "sender": "asha@example.com",
            "intent": "request",
            "offered": [offer(17, 15)],
        }
    ]
    assert contract_errors(json.dumps(good), contract, seen)[0] == []
    bad = [
        {
            "thread_id": "t9",
            "sender": "Asha <asha@example.com>",
            "intent": "maybe",
            "offered": [{"start": "2026-11-17T15:00:00", "end": "2026-11-17T15:30:00"}],
        }
    ]
    errors = contract_errors(json.dumps(bad), contract, seen)[0]
    assert (
        "item 1: thread_id t9 is not an id in what this step was given (copy it exactly)" in errors
    )
    assert "item 1: sender must be one email address, with no name" in errors
    assert "item 1: intent must be one of request, cancel" in errors
    assert "item 1: offered must give every time with its UTC offset" in errors
    invented = [{**good[0], "sender": "kabir@example.net"}]
    assert contract_errors(json.dumps(invented), contract, seen)[0] == [
        "item 1: sender kabir@example.net is not an address in what this step was given"
    ]
    assert contract_errors("not json", contract)[0] == ["the reply is not JSON"]


def test_gates_let_through_only_the_items_that_pass():
    rows = json.dumps(
        [
            {"intent": "request", "confidence": 0.9, "chosen": None},
            {"intent": "other", "confidence": 0.4, "chosen": {"start": "x"}},
        ]
    )
    meeting = tidy_gate({"step": "Read", "field": "intent", "in": ["request", "counter"]})
    assert json.loads(only_passing(rows, meeting)) == [json.loads(rows)[0]]
    assert gate_met(meeting, {"read": rows})
    sure = tidy_gate({"step": "read", "field": "confidence", "min": 0.8})
    assert len(json.loads(only_passing(rows, sure))) == 1
    booked = tidy_gate({"step": "read", "field": "chosen", "is": "not_empty"})
    assert json.loads(only_passing(rows, booked))[0]["intent"] == "other"
    assert only_passing("[]", meeting) == "[]"


def test_an_action_takes_its_details_from_its_item_exactly():
    item = {"subject": "Sync", "chosen": offer(19, 15, 30), "guests": ["a@x.com"]}
    args = {
        "summary": "Meeting: {{item.subject}}",
        "start": "{{item.chosen.start}}",
        "end": "{{item.chosen.end}}",
        "attendees": "{{item.guests}}",
    }
    assert fill_item(args, item) == {
        "summary": "Meeting: Sync",
        "start": offer(19, 15, 30)["start"],
        "end": offer(19, 15, 30)["end"],
        "attendees": ["a@x.com"],
    }


def next_weekday(days_ahead=3):
    """A weekday a few days from today, so the run's own "now" never makes it the past."""
    day = datetime.now(IST).date() + timedelta(days=days_ahead)
    while day.weekday() > 4:
        day += timedelta(days=1)
    return day


def test_a_scheduling_workflow_runs_with_code_finding_the_time(client, auth, monkeypatch):
    """Read, understand (contract), find the time (code), book (code fills the time), write
    the reply (contract), send. The model never touches a time it books."""
    runtime = setup(client, auth)
    day = next_weekday()

    def iso(hour, minute=0):
        return datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST).isoformat()

    mails = [
        {
            "id": "m1",
            "thread_id": "t1",
            "sender": "Rahul <rahul@example.com>",
            "subject": "Call?",
            "text": "2:30-3:00 PM or 3:30-4:00 PM IST?",
        },
        {
            "id": "m2",
            "thread_id": "t2",
            "sender": "Deals <offers@shop.example>",
            "subject": "Sale",
            "text": "50% off",
        },
    ]
    calendar = [
        {
            "id": "e1",
            "summary": "Project review",
            "status": "confirmed",
            "start": {"dateTime": iso(14)},
            "end": {"dateTime": iso(15)},
        }
    ]
    understood = [
        {
            "thread_id": "t1",
            "sender": "rahul@example.com",
            "subject": "Call?",
            "intent": "request",
            "offered": [
                {"start": iso(14, 30), "end": iso(15)},
                {"start": iso(15, 30), "end": iso(16)},
            ],
        },
        {
            "thread_id": "t2",
            "sender": "offers@shop.example",
            "subject": "Sale",
            "intent": "other",
            "offered": [],
        },
    ]
    replies = [
        {
            "to": "rahul@example.com",
            "subject": "Re: Call?",
            "thread_id": "t1",
            "body": "3:30 PM works. Booked.",
        }
    ]
    seen = scripted(
        runtime, helper={"extract": json.dumps(understood), "write": json.dumps(replies)}
    )
    runner = runtime.task_runner
    monkeypatch.setattr(runner, "google_readable", lambda: {"gmail", "calendar"})

    async def read(resource):
        return {"resource": resource, "items": mails if resource == "gmail" else calendar}

    async def honest(*_args, **_kwargs):
        return None

    monkeypatch.setattr(runtime.google_pool, "read", read)
    monkeypatch.setattr(runner, "unsupported_claims", honest)
    proposed = []

    def propose(draft, crew_id=None, generation=None, event_key=""):
        proposed.append((draft.operation, draft.arguments))
        return {"id": f"a{len(proposed)}"}

    async def maybe_execute(action_id):
        return {"id": action_id, "status": "completed", "target_summary": "done"}

    monkeypatch.setattr(runtime.actions, "propose", propose)
    monkeypatch.setattr(runtime.actions, "maybe_execute", maybe_execute)
    meeting = {"step": "understand", "field": "intent", "in": ["request", "counter", "accept"]}
    body = {
        "title": "Meeting desk",
        "deliver": "Meetings booked and answered",
        "final": "send",
        "steps": [
            {"id": "mail", "kind": "read", "source": "gmail", "new_only": True},
            {"id": "cal", "kind": "read", "source": "calendar"},
            {
                "id": "understand",
                "kind": "helper",
                "role": "extract",
                "title": "Understand",
                "instructions": "What each email asks.",
                "uses": ["mail"],
                "when": {"step": "mail", "is": "not_empty"},
                "returns": {
                    "fields": {
                        "thread_id": "id",
                        "sender": "email",
                        "subject": "text",
                        "intent": ["request", "counter", "accept", "cancel", "other"],
                        "offered": "times",
                    },
                    "required": ["thread_id", "sender", "subject", "intent"],
                },
            },
            {
                "id": "slots",
                "kind": "automation",
                "automation": "calendar.slots",
                "title": "Find the time",
                "instructions": "Find the time.",
                "uses": ["understand", "cal"],
                "where": meeting,
                "params": PARAMS,
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
                "title": "Write replies",
                "instructions": "A reply for each.",
                "uses": ["slots"],
                "returns": {
                    "fields": {"to": "email", "subject": "text", "thread_id": "id", "body": "text"}
                },
            },
            {"id": "send", "kind": "act", "operation": "gmail.send", "args_from": "write"},
        ],
    }
    done = wait(client, auth, start(client, auth, body))
    assert done["status"] == "done", done
    booked = [args for op, args in proposed if op == "calendar.create"]
    assert booked == [{"summary": "Call?", "start": iso(15, 30), "end": iso(16)}]
    sent = [args for op, args in proposed if op == "gmail.send"]
    assert [(m["to"], m["thread_id"]) for m in sent] == [("rahul@example.com", "t1")]
    # the writer saw only Rahul's request, with the time code found, never the newsletter
    writer = next(p for _, p in seen["helper_prompts"] if "YOUR ROLE: write" in p)
    assert iso(15, 30) in writer and "offers@shop.example" not in writer


def test_a_step_whose_output_does_not_fit_is_asked_once_more_then_stops_the_run(
    client, auth, monkeypatch
):
    runtime = setup(client, auth)
    answers = iter(['[{"sender": "Asha <asha@x.com>"}]', "still not right"])
    asked = []

    async def chat(provider, model, messages, system, credential_id=None):
        asked.append(messages[0]["content"])
        return next(answers)

    runtime.router.chat = chat
    body = {
        "title": "Contract",
        "deliver": "x",
        "final": "use",
        "steps": [
            {
                "id": "pick",
                "kind": "helper",
                "role": "extract",
                "title": "Pick",
                "instructions": "Pick the sender.",
                "returns": {"list": True, "fields": {"sender": "email"}},
            },
            {
                "id": "use",
                "kind": "helper",
                "role": "write",
                "title": "Use",
                "instructions": "Use",
                "uses": ["pick"],
            },
        ],
    }
    done = wait(client, auth, start(client, auth, body))
    assert len(asked) == 2 and "did not fit what the next step needs" in asked[1]
    pick = next(s for s in done["steps"] if s["id"] == "pick")
    assert pick["status"] == "failed" and "does not fit" in pick["error"]
    assert next(s for s in done["steps"] if s["id"] == "use")["status"] != "done"
