"""Different crews, written the way Mavo Core writes them, run through the real setup test on
fake models: no credits, every change to the engine. The engine is the same for all of them;
a fault in it (a branch skipping the step after it, a test email without ids) breaks every crew
and is caught here before anyone runs a test of their own."""

import json
import re
from datetime import datetime, timedelta, timezone

from mavo.database import CrewInstance
from mavo.workflow_write import setup_test
from tests.test_tasks import setup

IST = timezone(timedelta(hours=5, minutes=30))


def soon(hour, minute=0):
    day = datetime.now(IST).date() + timedelta(days=3)
    while day.weekday() > 4:
        day += timedelta(days=1)
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST).isoformat()


def mail(n, sender, subject, text):
    return {"id": f"m{n}", "thread_id": f"t{n}", "sender": sender, "subject": subject, "text": text}


def run_shape(client, auth, monkeypatch, written, acts, answers):
    """The setup test of a crew whose path is `written`; `answers` are the think steps' fake
    model, keyed by the start of each step's instructions."""
    runtime = setup(client, auth)
    runtime.recipes = True
    tags = {
        "read": {"gmail": "", "calendar": "", "memory": ""},
        "act": {op: "" for op in acts},
        "think": {},
    }
    monkeypatch.setattr("mavo.recipes.available_tags", lambda runtime, crew=None: tags)
    asked = []

    class Core:
        async def workflow(self, request):
            asked.append(request)
            return json.dumps(written)

    async def chat(provider, model, messages, system, credential_id=None):
        prompt = messages[0]["content"]
        for start, answer in answers.items():
            if f"YOUR JOB: {start}" in prompt:
                return answer(prompt)
        return "Done."

    async def honest(*_args, **_kwargs):
        return None

    runtime.coordinator = Core()
    runtime.router.chat = chat
    monkeypatch.setattr(runtime.task_runner, "unsupported_claims", honest)
    with runtime.factory() as session:
        crew = CrewInstance(
            name="Shape",
            template_id="research",
            status="draft",
            provider="ollama",
            model="m",
            responsibilities=written["deliver"],
            permissions=["model:invoke"],
            trigger_type="gmail_message",
            configuration_plan={
                "changes_things": True,
                "composition": {"agents": [{"key": "doer", "actions": list(acts)}]},
            },
        )
        session.add(crew)
        session.commit()
        crew_id = crew.id
    from mavo.workflow_write import basis, check_workflow

    plan, tests = check_workflow(written, tags)
    with runtime.factory() as session:  # a stored program: its test emails run through it
        crew = session.get(CrewInstance, crew_id)
        crew.configuration_plan = {
            **crew.configuration_plan,
            "workflow_draft": plan.model_dump(mode="json"),
            "workflow_tests": tests,
            "workflow_basis": basis(crew),
        }
        session.commit()
    record = client.portal.call(setup_test, runtime, crew_id)
    assert record.get("kind") == "workflow", record
    return record, asked


def addresses(prompt):
    return set(re.findall(r"[\w.+-]+@example\.(?:com|net|org)", prompt))


# -- inbox triage: urgent mail gets a holding reply drafted, the rest is left --------------------

BOSS = mail(1, "Priya <priya@example.com>", "Server down", "The server is down, reply today.")
NEWS = mail(2, "News <news@example.org>", "Weekly news", "Our top stories this week.")


def test_inbox_triage_drafts_a_reply_only_to_urgent_mail(client, auth, monkeypatch):
    urgent = {"step": "sort", "field": "urgent", "equals": True, "label": "it is urgent"}
    written = {
        "title": "Urgent mail",
        "deliver": "Draft a holding reply to urgent mail.",
        "final": "tell",
        "steps": [
            {"id": "mail", "kind": "read", "source": "gmail", "new_only": True},
            {
                "id": "sort",
                "kind": "helper",
                "role": "classify",
                "title": "Sort mail",
                "instructions": "Mark each email urgent or not.",
                "uses": ["mail"],
                "returns": {
                    "fields": {
                        "thread_id": "id",
                        "sender": "email",
                        "subject": "text",
                        "urgent": "bool",
                    }
                },
            },
            {
                "id": "write",
                "kind": "helper",
                "role": "write",
                "title": "Holding replies",
                "instructions": "Write a short holding reply to each urgent email.",
                "uses": ["sort"],
                "where": urgent,
                "returns": {
                    "fields": {"to": "email", "subject": "text", "thread_id": "id", "body": "text"}
                },
            },
            {"id": "draft", "kind": "act", "operation": "gmail.draft", "args_from": "write"},
            {
                "id": "tell",
                "kind": "helper",
                "role": "write",
                "title": "Tell",
                "instructions": "Say what was drafted.",
                "uses": ["draft"],
            },
        ],
        "tests": [
            {
                "name": "Urgent mail",
                "inputs": {"gmail": [BOSS]},
                "expect": {"gmail.draft": [{"to": "priya@example.com"}]},
            },
            {"name": "Only a newsletter", "inputs": {"gmail": [NEWS]}, "expect": {}},
            {
                "name": "Both at once",
                "inputs": {"gmail": [BOSS, NEWS]},
                "expect": {"gmail.draft": [{"to": "priya@example.com"}]},
            },
            {"name": "Nothing new", "inputs": {"gmail": []}, "expect": {}},
        ],
    }

    def sort(prompt):
        found = addresses(prompt)
        rows = []
        if "priya@example.com" in found:
            rows.append(
                {
                    "thread_id": "t1",
                    "sender": "priya@example.com",
                    "subject": "Server down",
                    "urgent": True,
                }
            )
        if "news@example.org" in found:
            rows.append(
                {
                    "thread_id": "t2",
                    "sender": "news@example.org",
                    "subject": "Weekly news",
                    "urgent": False,
                }
            )
        return json.dumps(rows)

    def write(prompt):
        return json.dumps(
            [
                {
                    "to": "priya@example.com",
                    "subject": "Re: Server down",
                    "thread_id": "t1",
                    "body": "On it, will update you soon.",
                }
            ]
        )

    record, asked = run_shape(
        client,
        auth,
        monkeypatch,
        written,
        ["gmail.draft"],
        {"Mark each email": sort, "Write a short holding": write},
    )
    assert record["status"] == "passed", record["message"]
    assert asked == []  # stored, not written again


# -- task capture: what an email asks for becomes a task -----------------------------------------


def test_task_capture_makes_a_task_from_what_the_email_asks(client, auth, monkeypatch):
    ask = mail(1, "Manager <boss@example.com>", "Report", "Please send the report by Friday.")
    chat_mail = mail(2, "Friend <amy@example.net>", "Hi", "How was the weekend?")
    due = soon(17)
    written = {
        "title": "Tasks from mail",
        "deliver": "Add a task for each thing an email asks me to do.",
        "final": "tell",
        "steps": [
            {"id": "mail", "kind": "read", "source": "gmail", "new_only": True},
            {
                "id": "find",
                "kind": "helper",
                "role": "extract",
                "title": "Find tasks",
                "instructions": "List each task an email asks for, with its due time.",
                "uses": ["mail"],
                "returns": {
                    "fields": {"title": "text", "due": "time", "thread_id": "id"},
                    "required": ["title", "thread_id"],
                },
            },
            {
                "id": "add",
                "kind": "act",
                "operation": "tasks.create",
                "args_from": "find",
                "args": {"title": "{{item.title}}", "due": "{{item.due}}"},
            },
            {
                "id": "tell",
                "kind": "helper",
                "role": "write",
                "title": "Tell",
                "instructions": "Say what was added.",
                "uses": ["add"],
            },
        ],
        "tests": [
            {
                "name": "An email asks for something",
                "inputs": {"gmail": [ask]},
                "expect": {"tasks.create": [{"title": "Send the report"}]},
            },
            {"name": "Small talk", "inputs": {"gmail": [chat_mail]}, "expect": {}},
            {"name": "Nothing new", "inputs": {"gmail": []}, "expect": {}},
        ],
    }

    def find(prompt):
        if "send the report" not in prompt:
            return "[]"
        return json.dumps([{"title": "Send the report", "due": due, "thread_id": "t1"}])

    record, _ = run_shape(
        client, auth, monkeypatch, written, ["tasks.create"], {"List each task": find}
    )
    assert record["status"] == "passed", record["message"]


# -- booking only: a free offered time is booked, a busy one is not, nothing is sent -------------


def test_booking_only_books_a_free_offered_time_and_never_a_busy_one(client, auth, monkeypatch):
    busy = {
        "id": "e1",
        "summary": "Standup",
        "status": "confirmed",
        "start": {"dateTime": soon(10)},
        "end": {"dateTime": soon(11)},
    }
    free = mail(1, "Asha <asha@example.com>", "Sync", "Can we meet at 3 PM?")
    taken = mail(2, "Kabir <kabir@example.net>", "Call", "Can we talk at 10 AM?")
    written = {
        "title": "Book requests",
        "deliver": "Put meetings people ask for on my calendar.",
        "final": "tell",
        "steps": [
            {"id": "mail", "kind": "read", "source": "gmail", "new_only": True},
            {"id": "cal", "kind": "read", "source": "calendar"},
            {
                "id": "read",
                "kind": "helper",
                "role": "extract",
                "title": "Requests",
                "instructions": "List each meeting request and its offered times.",
                "uses": ["mail"],
                "returns": {"fields": {"thread_id": "id", "subject": "text", "offered": "times"}},
            },
            {
                "id": "slots",
                "kind": "automation",
                "automation": "calendar.slots",
                "title": "Find",
                "instructions": "Find.",
                "uses": ["read", "cal"],
                "params": {"requests": "read", "timezone": "+05:30"},
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
                "id": "tell",
                "kind": "helper",
                "role": "write",
                "title": "Tell",
                "instructions": "Say what was booked.",
                "uses": ["book"],
            },
        ],
        "tests": [
            {
                "name": "Free time",
                "inputs": {"gmail": [free], "calendar": [busy]},
                "expect": {"calendar.create": [{"start": soon(15)}]},
            },
            {"name": "Busy time", "inputs": {"gmail": [taken], "calendar": [busy]}, "expect": {}},
            {
                "name": "Both",
                "inputs": {"gmail": [free, taken], "calendar": [busy]},
                "expect": {"calendar.create": [{"start": soon(15)}]},
            },
        ],
    }

    def read(prompt):
        rows = []
        if "asha@example.com" in prompt:
            rows.append(
                {
                    "thread_id": "t1",
                    "subject": "Sync",
                    "offered": [{"start": soon(15), "end": soon(15, 30)}],
                }
            )
        if "kabir@example.net" in prompt:
            rows.append(
                {
                    "thread_id": "t2",
                    "subject": "Call",
                    "offered": [{"start": soon(10), "end": soon(10, 30)}],
                }
            )
        return json.dumps(rows)

    record, _ = run_shape(
        client, auth, monkeypatch, written, ["calendar.create"], {"List each meeting request": read}
    )
    assert record["status"] == "passed", record["message"]


# -- a confidence threshold: sure ones are answered, unsure ones only drafted for the person ----


def test_a_confidence_threshold_sends_when_sure_and_only_drafts_when_unsure(
    client, auth, monkeypatch
):
    sure = mail(1, "Asha <asha@example.com>", "Invoice", "Please confirm you got invoice 42.")
    vague = mail(2, "Kabir <kabir@example.net>", "Hmm", "About that thing, you know.")
    written = {
        "title": "Answer mail",
        "deliver": "Answer emails I can answer; draft the rest.",
        "final": "tell",
        "steps": [
            {"id": "mail", "kind": "read", "source": "gmail", "new_only": True},
            {
                "id": "answer",
                "kind": "helper",
                "role": "write",
                "title": "Answers",
                "instructions": "Write a reply to each email and say how sure you are.",
                "uses": ["mail"],
                "returns": {
                    "fields": {
                        "to": "email",
                        "subject": "text",
                        "thread_id": "id",
                        "body": "text",
                        "confidence": "number",
                    }
                },
            },
            {
                "id": "send",
                "kind": "act",
                "operation": "gmail.send",
                "args_from": "answer",
                "where": {
                    "step": "answer",
                    "field": "confidence",
                    "min": 0.8,
                    "label": "it is sure",
                },
                "args": {
                    "to": "{{item.to}}",
                    "subject": "{{item.subject}}",
                    "body": "{{item.body}}",
                    "thread_id": "{{item.thread_id}}",
                },
            },
            {
                "id": "draft",
                "kind": "act",
                "operation": "gmail.draft",
                "args_from": "answer",
                "where": {
                    "step": "answer",
                    "field": "confidence",
                    "max": 0.79,
                    "label": "it is unsure",
                },
                "args": {
                    "to": "{{item.to}}",
                    "subject": "{{item.subject}}",
                    "body": "{{item.body}}",
                    "thread_id": "{{item.thread_id}}",
                },
            },
            {
                "id": "tell",
                "kind": "helper",
                "role": "write",
                "title": "Tell",
                "instructions": "Say what was sent and drafted.",
                "uses": ["send", "draft"],
            },
        ],
        "tests": [
            {
                "name": "Sure",
                "inputs": {"gmail": [sure]},
                "expect": {"gmail.send": [{"to": "asha@example.com"}]},
            },
            {
                "name": "Unsure",
                "inputs": {"gmail": [vague]},
                "expect": {"gmail.draft": [{"to": "kabir@example.net"}]},
            },
            {
                "name": "One of each",
                "inputs": {"gmail": [sure, vague]},
                "expect": {
                    "gmail.send": [{"to": "asha@example.com"}],
                    "gmail.draft": [{"to": "kabir@example.net"}],
                },
            },
        ],
    }

    def answer(prompt):
        rows = []
        if "asha@example.com" in prompt:
            rows.append(
                {
                    "to": "asha@example.com",
                    "subject": "Re: Invoice",
                    "thread_id": "t1",
                    "body": "Got invoice 42, thank you.",
                    "confidence": 0.95,
                }
            )
        if "kabir@example.net" in prompt:
            rows.append(
                {
                    "to": "kabir@example.net",
                    "subject": "Re: Hmm",
                    "thread_id": "t2",
                    "body": "Could you tell me which thing you mean?",
                    "confidence": 0.3,
                }
            )
        return json.dumps(rows)

    record, _ = run_shape(
        client,
        auth,
        monkeypatch,
        written,
        ["gmail.send", "gmail.draft"],
        {"Write a reply to each": answer},
    )
    assert record["status"] == "passed", record["message"]
