"""An agent that changes things is set up by Mavo Core writing its whole workflow, with test
scenarios, and is kept only when every scenario does what it should. A recipe planned after a
crew's test and never tested replaced it: no calendar step, the sender's address lost, nothing
sent."""

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

from mavo.database import CrewInstance
from mavo.publisher_gateway import GatewaySettings, create_gateway
from mavo.recipes import eligible
from mavo.workflow_write import check_workflow, expectation_problems, setup_test
from tests.test_publisher_gateway import Identity
from tests.test_tasks import setup

IST = timezone(timedelta(hours=5, minutes=30))
TAGS = {
    "read": {"gmail": "", "calendar": ""},
    "act": {"calendar.create": "", "gmail.send": ""},
    "think": {},
}


def day_ahead():
    day = datetime.now(IST).date() + timedelta(days=3)
    while day.weekday() > 4:
        day += timedelta(days=1)
    return day


def at(hour, minute=0):
    d = day_ahead()
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=IST).isoformat()


def workflow(expect_booking=None):
    expect_booking = expect_booking or at(15, 30)
    meeting = {"step": "understand", "field": "intent", "in": ["request", "counter"]}
    rahul = {
        "id": "m1",
        "thread_id": "t1",
        "sender": "Rahul <rahul@example.com>",
        "subject": "Call?",
        "text": "2:30 or 3:30?",
    }
    busy = {
        "id": "e1",
        "summary": "Review",
        "status": "confirmed",
        "start": {"dateTime": at(14)},
        "end": {"dateTime": at(15)},
    }
    return {
        "title": "Meeting desk",
        "deliver": "Meetings booked and answered",
        "final": "tell",
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
                "returns": {
                    "fields": {
                        "thread_id": "id",
                        "sender": "email",
                        "subject": "text",
                        "intent": ["request", "counter", "cancel", "other"],
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
                "instructions": "A reply for each.",
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
        "tests": [
            {
                "name": "Second offered time free",
                "inputs": {"gmail": [rahul], "calendar": [busy]},
                "expect": {
                    "calendar.create": [{"start": expect_booking}],
                    "gmail.send": [{"to": "rahul@example.com"}],
                },
            },
            {
                "name": "Only a newsletter",
                "inputs": {
                    "gmail": [
                        {
                            "id": "m2",
                            "thread_id": "t2",
                            "sender": "Deals <offers@shop.example>",
                            "subject": "Sale",
                            "text": "50% off",
                        }
                    ],
                    "calendar": [busy],
                },
                "expect": {},
            },
            {"name": "Nothing new", "inputs": {"gmail": [], "calendar": [busy]}, "expect": {}},
        ],
    }


def test_what_core_writes_is_checked_and_told_what_is_wrong():
    plan, tests = check_workflow(workflow(), TAGS)
    assert [s.id for s in plan.steps][-1] == "tell" and len(tests) == 3
    loose = workflow()
    loose["steps"][2].pop("returns")  # the understanding step hands on prose
    try:
        check_workflow(loose, TAGS)
        raise AssertionError("a think step another step reads needs a contract")
    except ValueError as exc:
        assert "understand: another step reads it, so it needs a contract" in str(exc)
    untested = {k: v for k, v in workflow().items() if k != "tests"}
    try:
        check_workflow(untested, TAGS)
        raise AssertionError("a workflow comes with its scenarios")
    except ValueError as exc:
        assert "tests must list" in str(exc)


def test_a_scenario_compares_what_it_would_do_with_what_it_should():
    booking = {"operation": "calendar.create", "arguments": {"start": at(15, 30)}}
    reply = {"operation": "gmail.send", "arguments": {"to": "rahul@example.com"}}
    expect = {
        "calendar.create": [{"start": at(15, 30)}],
        "gmail.send": [{"to": "rahul@example.com"}],
    }
    assert expectation_problems(expect, [booking, reply]) == []
    assert expectation_problems(expect, [reply]) == ["Bookings: did 0, expected 1"]
    wrong = {"operation": "calendar.create", "arguments": {"start": at(14, 30)}}
    assert expectation_problems(expect, [wrong, reply])[0].startswith("Bookings: expected Book ")
    assert expectation_problems({}, [reply]) == ["Emails sent: did 1, expected 0"]


def prepare(client, auth, monkeypatch, written):
    runtime = setup(client, auth)
    runtime.recipes = True
    monkeypatch.setattr("mavo.recipes.available_tags", lambda runtime, crew=None: TAGS)
    asked = []

    class Core:
        async def workflow(self, request):
            asked.append(request)
            return json.dumps(written)

    runtime.coordinator = Core()

    async def chat(provider, model, messages, system, credential_id=None):
        prompt = messages[0]["content"]
        if "YOUR ROLE: extract" in prompt:
            if "rahul@example.com" in prompt:
                return json.dumps(
                    [
                        {
                            "thread_id": "t1",
                            "sender": "rahul@example.com",
                            "subject": "Call?",
                            "intent": "request",
                            "offered": [
                                {"start": at(14, 30), "end": at(15)},
                                {"start": at(15, 30), "end": at(16)},
                            ],
                        }
                    ]
                )
            return json.dumps(
                [
                    {
                        "thread_id": "t2",
                        "sender": "offers@shop.example",
                        "subject": "Sale",
                        "intent": "other",
                        "offered": [],
                    }
                ]
            )
        if "YOUR ROLE: write" in prompt and "Replies" in prompt or "A reply for each" in prompt:
            return json.dumps(
                [
                    {
                        "to": "rahul@example.com",
                        "subject": "Re: Call?",
                        "thread_id": "t1",
                        "body": "3:30 PM works, booked.",
                    }
                ]
            )
        return "Booked Rahul at 3:30 PM and replied."

    async def honest(*_args, **_kwargs):
        return None

    runtime.router.chat = chat
    monkeypatch.setattr(runtime.task_runner, "unsupported_claims", honest)
    with runtime.factory() as session:
        crew = CrewInstance(
            name="Meeting desk",
            provider="ollama",
            model="m",
            template_id="research",
            status="draft",
            responsibilities="Book meetings people ask for by mail and reply.",
            permissions=["model:invoke"],
            trigger_type="gmail_message",
            configuration_plan={
                "changes_things": True,
                "composition": {
                    "agents": [{"key": "booker", "actions": ["calendar.create", "gmail.send"]}]
                },
            },
        )
        session.add(crew)
        session.commit()
        crew_id = crew.id
    store_path(runtime, crew_id, written)
    return runtime, crew_id, asked


def store_path(runtime, crew_id, written):
    """A written, tested program on the draft, as crews from before the guarded agent have:
    the setup test runs its test emails (the engine under test here)."""
    from mavo.workflow_write import basis

    try:
        plan, tests = check_workflow(written, TAGS)
    except ValueError:
        return
    with runtime.factory() as session:
        crew = session.get(CrewInstance, crew_id)
        crew.configuration_plan = {
            **crew.configuration_plan,
            "workflow_draft": plan.model_dump(mode="json"),
            "workflow_tests": tests,
            "workflow_basis": basis(crew),
        }
        session.commit()


def test_a_stored_program_runs_every_test_email_and_only_then_is_it_kept(client, auth, monkeypatch):
    runtime, crew_id, asked = prepare(client, auth, monkeypatch, workflow())
    record = client.portal.call(setup_test, runtime, crew_id)
    assert record["status"] == "passed", record
    assert record["message"] == "All 3 tests passed."
    first = record["tests"][0]
    assert [w["operation"] for w in first["would"]] == ["calendar.create", "gmail.send"]
    assert first["would"][0]["arguments"]["start"] == at(15, 30)  # code chose it, not a model
    assert asked == []  # nothing was written again
    with runtime.factory() as session:
        crew = session.get(CrewInstance, crew_id)
        plan = crew.configuration_plan
        assert plan["recipe_tested"] and plan["recipe"]["final"] == "tell"
        crew.status, crew.is_running = "active", True
        assert eligible(crew)  # a tested workflow may run; nothing was proposed or made
    from mavo.database import ActionRequest

    with runtime.factory() as session:
        assert session.query(ActionRequest).count() == 0


def test_a_workflow_that_fails_a_scenario_is_not_kept(client, auth, monkeypatch):
    # Core expected the busy 2:30: the run books 3:30, so the scenario fails, and it says why.
    runtime, crew_id, _ = prepare(client, auth, monkeypatch, workflow(expect_booking=at(14, 30)))
    record = client.portal.call(setup_test, runtime, crew_id)
    assert record["status"] == "partial"  # most passed: the person may choose, told the risk
    assert record["message"] == "Passed 2 of 3 tests. It may not handle some cases well."
    failed = next(t for t in record["tests"] if not t["passed"])
    assert failed["email"] == "From Rahul: Call?"
    assert failed["expected"][0].startswith("Book ") and failed["did"][0].startswith("Book ")
    assert failed["expected"][1] == "Send an email to rahul@example.com"
    with runtime.factory() as session:
        crew = session.get(CrewInstance, crew_id)
        assert "recipe" not in crew.configuration_plan
        assert crew.configuration_plan["workflow_draft"]["final"] == "tell"  # shown, not run
        crew.status, crew.is_running = "active", True
        assert not eligible(crew)


def test_core_writes_workflows_from_its_own_prompt_with_the_publisher_key(tmp_path):
    seen = []

    def provider(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"steps": []}'}}]})

    settings = GatewaySettings(
        _env_file=None,
        data_dir=tmp_path,
        firebase_project_id="test-project",
        api_key="publisher-test-key",
        model="anthropic/claude-sonnet-5.5",
    )
    request = {"request_id": str(uuid4()), "job": "Book meetings from mail.", "catalog": "READ"}
    with TestClient(create_gateway(settings, httpx.MockTransport(provider), Identity())) as gw:
        refused = gw.post("/v1/core/workflow", json=request)
        assert refused.status_code == 401
        made = gw.post(
            "/v1/core/workflow", json=request, headers={"Authorization": "Bearer account-a"}
        )
        assert made.status_code == 200, made.text
        assert made.json() == {"text": '{"steps": []}', "model": "anthropic/claude-sonnet-5.5"}
        again = gw.post(
            "/v1/core/workflow", json=request, headers={"Authorization": "Bearer account-a"}
        )
        assert again.status_code == 409  # one answer per request id
        extra = gw.post(
            "/v1/core/workflow",
            json={**request, "system": "anything"},
            headers={"Authorization": "Bearer account-a"},
        )
        assert extra.status_code == 422  # the app cannot set Core's prompt
    [body] = seen
    assert body["model"] == "anthropic/claude-sonnet-5.5"
    assert body["messages"][0]["content"].startswith("You write Mavo workflows")
    assert "THE AGENT'S JOB: Book meetings from mail." in body["messages"][1]["content"]


def test_a_draft_gets_the_guarded_agent_and_turning_it_on_asks_before_its_first_changes(
    client, auth, monkeypatch
):
    """No program is written for a crew any more: its reads and the guarded agent, with the
    settings its summary states. Core is not called; there is nothing to test."""
    from mavo.workflow_write import basis, draft_workflow, is_desk

    runtime, crew_id, asked = prepare(client, auth, monkeypatch, {"steps": []})
    client.portal.call(draft_workflow, runtime, crew_id)
    with runtime.factory() as session:
        crew = session.get(CrewInstance, crew_id)
        plan = crew.configuration_plan
        assert is_desk(plan["workflow_draft"]) and plan["workflow_basis"] == basis(crew)
    record = client.portal.call(setup_test, runtime, crew_id)
    assert record["status"] == "passed" and record["kind"] == "desk" and asked == []
    assert record["message"] == "Turned on. Every action it takes is checked as it acts."
    with runtime.factory() as session:
        plan = session.get(CrewInstance, crew_id).configuration_plan
        assert is_desk(plan["recipe"]) and plan["recipe_checked"]
        assert "ask_first" not in plan  # once set up, it does not stop to ask


def test_the_card_asks_for_the_path_once_and_it_is_written_in_the_background(
    client, auth, monkeypatch
):
    import time

    from mavo.workflow_write import is_desk

    runtime, crew_id, asked = prepare(client, auth, monkeypatch, {"steps": []})
    shown = client.post(f"/api/v1/crew/{crew_id}/workflow", headers=auth).json()
    plan = shown["configuration_plan"]
    assert plan.get("workflow_writing") or plan.get("workflow_draft")
    client.post(f"/api/v1/crew/{crew_id}/workflow", headers=auth)  # asked again while writing
    for _ in range(100):
        with runtime.factory() as session:
            plan = session.get(CrewInstance, crew_id).configuration_plan
            if plan.get("workflow_draft"):
                break
        time.sleep(0.05)
    assert is_desk(plan["workflow_draft"]) and asked == []


def test_a_draft_s_path_may_use_what_the_draft_asks_for_before_it_is_granted():
    """A draft not yet granted its mail was given only saved files and memory to read, and no
    actions: Core wrote a path that read files and only drafted replies."""
    from types import SimpleNamespace

    from mavo.workflow_write import draft_tags

    crew = SimpleNamespace(
        configuration_plan={
            "draft": {
                "requested_google_resources": ["gmail"],
                "requested_google_capabilities": ["calendar:read"],
                "requested_actions": [
                    {"operation": "calendar.create"},
                    {"operation": "gmail.send"},
                ],
            }
        }
    )
    granted = {"read": {"files": "", "memory": ""}, "act": {}, "think": {}}
    tags = draft_tags(None, crew, granted)
    assert {"gmail", "calendar", "files", "memory"} <= set(tags["read"])
    assert {"calendar.create", "gmail.send"} <= set(tags["act"])


def test_a_path_that_cannot_do_its_job_is_refused_and_core_is_told_why():
    blind = workflow()
    blind["steps"][3]["uses"] = ["understand"]  # finding a time with no calendar
    try:
        check_workflow(blind, TAGS)
        raise AssertionError("every time looks free with no calendar")
    except ValueError as exc:
        assert "finding a time needs the calendar read in its uses" in str(exc)
    drafts_only = workflow()
    drafts_only["steps"] = [s for s in drafts_only["steps"] if s["id"] != "send"]
    drafts_only["steps"][-1]["uses"] = ["book"]
    try:
        check_workflow(drafts_only, TAGS, ["calendar.create", "gmail.send"])
        raise AssertionError("the job asks to send")
    except ValueError as exc:
        assert "gmail.send" in str(exc) and (
            "no step does" in str(exc) or "the job asks" in str(exc)
        )


def test_a_confirmation_runs_after_its_booking_and_a_sentence_as_final_is_put_right():
    out_of_order = workflow()
    steps = out_of_order["steps"]
    book = next(s for s in steps if s["id"] == "book")
    steps.remove(book)
    steps.insert(steps.index(next(s for s in steps if s["id"] == "send")) + 1, book)
    out_of_order["final"] = "Tell the person briefly what happened."
    plan, _ = check_workflow(out_of_order, TAGS)
    send = next(s for s in plan.steps if s.id == "send")
    assert "book" in send.params["after"]  # the message waits for the change it reports
    assert plan.final == "tell"


def test_turning_a_draft_on_does_not_throw_its_path_away():
    from types import SimpleNamespace

    from mavo.workflow_write import basis

    plan = {
        "draft": {
            "trigger_type": "gmail_message",
            "requested_actions": [{"operation": "gmail.send"}],
        },
        "composition": {"agents": [{"key": "a", "instructions": "x"}]},
    }
    before = SimpleNamespace(
        responsibilities="Book meetings.", trigger_type="gmail_message", configuration_plan=plan
    )
    stored_again = SimpleNamespace(
        **{
            **before.__dict__,
            "configuration_plan": {
                **plan,
                "composition": {"agents": [{"key": "a", "instructions": "x", "route_id": "r"}]},
            },
        }
    )
    assert basis(before) == basis(stored_again)
    changed = SimpleNamespace(**{**before.__dict__, "responsibilities": "Book only on Fridays."})
    assert basis(before) != basis(changed)


def test_a_passing_core_failure_is_asked_again_not_a_failed_writing(client, auth, monkeypatch):
    from mavo.coordinator_client import CoordinatorUnavailable
    from mavo.workflow_write import core_chat, snapshot_of, write_workflow

    runtime, crew_id, _ = prepare(client, auth, monkeypatch, workflow())
    monkeypatch.setattr("mavo.workflow_write.RETRY_WAITS", (0, 0))
    tries = []

    class Busy:
        async def workflow(self, request):
            tries.append(request["request_id"])
            if len(tries) == 1:
                raise CoordinatorUnavailable(
                    code="core_gateway_failed", request_id=request["request_id"]
                )
            return json.dumps(workflow())

    runtime.coordinator = Busy()
    with runtime.factory() as session:
        crew = snapshot_of(session.get(CrewInstance, crew_id))
    plan, _ = client.portal.call(write_workflow, runtime, crew, core_chat(runtime))
    assert plan.final == "tell" and len(tries) == 2 and tries[0] != tries[1]


def test_core_is_asked_three_times_at_most_whatever_happens(client, auth, monkeypatch):
    from mavo.coordinator_client import CoordinatorUnavailable
    from mavo.workflow_write import core_chat, snapshot_of, write_workflow

    runtime, crew_id, _ = prepare(client, auth, monkeypatch, workflow())
    monkeypatch.setattr("mavo.workflow_write.RETRY_WAITS", (0, 0))
    tries = []

    class Down:
        async def workflow(self, request):
            tries.append(1)
            raise CoordinatorUnavailable(
                code="core_gateway_failed", request_id=request["request_id"]
            )

    runtime.coordinator = Down()
    with runtime.factory() as session:
        crew = snapshot_of(session.get(CrewInstance, crew_id))
    try:
        client.portal.call(write_workflow, runtime, crew, core_chat(runtime))
        raise AssertionError("Core was down")
    except CoordinatorUnavailable:
        pass
    assert len(tries) == 3


def test_a_test_spends_no_model_call_on_the_report(client, auth, monkeypatch):
    runtime, crew_id, _ = prepare(client, auth, monkeypatch, workflow())
    real, asked = runtime.router.chat, []

    async def counted(provider, model, messages, system, credential_id=None):
        asked.append(messages[0]["content"])
        return await real(provider, model, messages, system, credential_id)

    runtime.router.chat = counted
    assert client.portal.call(setup_test, runtime, crew_id)["status"] == "passed"
    assert not any("Say what was done." in p for p in asked)
    # 3 scenarios: understanding twice (one has no mail) and one reply written
    assert len(asked) == 3, [p[:80] for p in asked]


def test_a_crew_turned_on_after_its_test_runs_that_path_on_new_mail(client, auth, monkeypatch):
    """The whole journey, no credits: the path is written and tested, the crew is turned on,
    a new email arrives, and the tested path books, then confirms in the thread."""
    from sqlalchemy import select

    from mavo.crew_composition import dispatch, set_composition
    from mavo.database import TaskRun
    from tests.test_crew_v2 import composition, spec
    from tests.test_tasks import wait

    runtime, crew_id, _ = prepare(client, auth, monkeypatch, workflow())
    with runtime.factory() as session:
        crew = session.get(CrewInstance, crew_id)
        set_composition(
            session, crew, composition(spec("booker", actions=["calendar.create", "gmail.send"]))
        )
        session.commit()
    assert client.portal.call(setup_test, runtime, crew_id)["status"] == "passed"
    with runtime.factory() as session:
        crew = session.get(CrewInstance, crew_id)
        crew.status, crew.is_running, crew.runtime_state = "active", True, "waiting"
        session.commit()
    mail = [
        {
            "id": "m1",
            "thread_id": "t1",
            "sender": "Rahul <rahul@example.com>",
            "subject": "Call?",
            "text": "2:30 or 3:30?",
        }
    ]
    calendar = [
        {
            "id": "e1",
            "summary": "Review",
            "status": "confirmed",
            "start": {"dateTime": at(14)},
            "end": {"dateTime": at(15)},
        }
    ]

    async def read(resource):
        return {"resource": resource, "items": mail if resource == "gmail" else calendar}

    async def new_mail(crew_id):
        return {"resource": "gmail", "events": [{"id": "m1"}]}

    monkeypatch.setattr(runtime.google_pool, "read", read)
    monkeypatch.setattr(runtime.task_runner, "google_readable", lambda: {"gmail", "calendar"})
    monkeypatch.setattr(runtime, "prepare_event", new_mail)
    made = []

    def propose(draft, crew_id=None, generation=None, event_key=""):
        made.append((draft.operation, dict(draft.arguments)))
        return {"id": f"a{len(made)}"}

    async def maybe_execute(action_id):
        return {"id": action_id, "status": "completed", "target_summary": "ok"}

    monkeypatch.setattr(runtime.actions, "propose", propose)
    monkeypatch.setattr(runtime.actions, "maybe_execute", maybe_execute)
    assert client.portal.call(dispatch, runtime, crew_id)
    with runtime.factory() as session:
        run_id = session.scalar(select(TaskRun.id).where(TaskRun.crew_id == crew_id))
    run = wait(client, auth, run_id)
    assert run["status"] == "done", run
    assert [op for op, _ in made] == ["calendar.create", "gmail.send"]  # booked, then confirmed
    assert made[0][1]["start"] == at(15, 30) and made[1][1]["thread_id"] == "t1"


def test_test_emails_are_shaped_like_real_mail_and_their_runs_stay_out_of_sight(
    client, auth, monkeypatch
):
    """Core's test emails had no ids: the step that reads them had to copy a thread id that was
    not there, every email was set aside, and each test run appeared to the person as
    "Nothing new. Needs you: ..."."""
    from mavo.database import TaskRun
    from mavo.workflow_write import as_read

    shaped = as_read(
        {
            "inputs": {
                "gmail": [{"sender": "a@example.com", "subject": "Hi"}],
                "calendar": [{"summary": "Busy"}],
            }
        },
        2,
    )
    mail, event = shaped["inputs"]["gmail"][0], shaped["inputs"]["calendar"][0]
    assert mail["id"] == mail["thread_id"] == "test-mail-2-1" and mail["sample"]
    assert event["id"] == "test-event-2-1" and event["status"] == "confirmed"
    written = workflow()
    for test in written["tests"]:
        for item in test["inputs"].get("gmail", []):
            item.pop("id"), item.pop("thread_id")  # as Core wrote them
    runtime, crew_id, _ = prepare(client, auth, monkeypatch, written)

    async def chat(provider, model, messages, system, credential_id=None):
        prompt = messages[0]["content"]
        if "YOUR ROLE: extract" in prompt and "rahul@example.com" in prompt:
            return json.dumps(
                [
                    {
                        "thread_id": "test-mail-1-1",
                        "sender": "rahul@example.com",
                        "subject": "Call?",
                        "intent": "request",
                        "offered": [{"start": at(15, 30), "end": at(16)}],
                    }
                ]
            )
        if "YOUR ROLE: extract" in prompt:
            return json.dumps(
                [
                    {
                        "thread_id": "test-mail-2-1",
                        "sender": "offers@shop.example",
                        "subject": "Sale",
                        "intent": "other",
                        "offered": [],
                    }
                ]
            )
        return json.dumps(
            [
                {
                    "to": "rahul@example.com",
                    "subject": "Re: Call?",
                    "thread_id": "test-mail-1-1",
                    "body": "3:30 works.",
                }
            ]
        )

    runtime.router.chat = chat
    record = client.portal.call(setup_test, runtime, crew_id)
    assert record["status"] == "passed", record
    with runtime.factory() as session:
        assert session.query(TaskRun).count() == 0  # nothing left in the person's list
    assert client.get("/api/v1/tasks", headers=auth).json() == []


def test_find_time_uses_the_times_field_whatever_core_named_it(client, auth, monkeypatch):
    written = workflow()
    fields = written["steps"][2]["returns"]["fields"]
    fields["proposed_times"] = fields.pop("offered")
    runtime, crew_id, _ = prepare(client, auth, monkeypatch, written)
    real = runtime.router.chat

    async def renamed(provider, model, messages, system, credential_id=None):
        reply = await real(provider, model, messages, system, credential_id)
        return reply.replace('"offered"', '"proposed_times"')

    runtime.router.chat = renamed
    record = client.portal.call(setup_test, runtime, crew_id)
    assert record["status"] == "passed", record  # booked the free 3:30, not "no time offered"


def test_a_step_after_two_branches_runs_when_either_has_work(client, auth, monkeypatch):
    """Each email takes one branch; the step that writes the reply reads both. It was skipped
    whenever one branch had nothing, so 8 of 12 tests booked and sent nothing."""
    written = workflow()
    steps = written["steps"]
    understand = next(s for s in steps if s["id"] == "understand")
    understand["returns"]["fields"]["intent"].append("unclear")
    clarify = {
        "id": "clarify",
        "kind": "helper",
        "role": "extract",
        "title": "Unclear ones",
        "instructions": "List the unclear requests.",
        "uses": ["understand"],
        "where": {"step": "understand", "field": "intent", "in": ["unclear"]},
        "returns": {"fields": {"thread_id": "id", "sender": "email"}},
    }
    steps.insert(steps.index(understand) + 1, clarify)
    write = next(s for s in steps if s["id"] == "write")
    write["uses"] = ["slots", "clarify"]  # the reply writer reads both branches
    runtime, crew_id, _ = prepare(client, auth, monkeypatch, written)
    record = client.portal.call(setup_test, runtime, crew_id)
    assert record["status"] == "passed", record["message"]


def test_core_fixes_its_own_failed_test_and_the_person_sees_only_the_pass(
    client, auth, monkeypatch
):
    # A stored program whose test expects the busy 2:30; shown what the test did, Core fixes it.
    runtime, crew_id, _ = prepare(client, auth, monkeypatch, workflow(expect_booking=at(14, 30)))
    asked = []

    class Core:
        async def workflow(self, request):
            asked.append(request)
            return json.dumps(workflow())

    runtime.coordinator = Core()
    record = client.portal.call(setup_test, runtime, crew_id)
    assert record["status"] == "passed" and record["fixed_rounds"] == 1 and len(asked) == 1
    told = asked[0]["problem"]
    assert "did not lead to what they expected" in told
    assert "Second offered time free: email From Rahul: Call?" in told
    assert "did Book " in told and asked[0]["previous"].startswith('{"title"')


def test_what_core_cannot_fix_in_three_rounds_is_shown_and_core_is_asked_four_times(
    client, auth, monkeypatch
):
    runtime, crew_id, _ = prepare(client, auth, monkeypatch, workflow(expect_booking=at(14, 30)))
    asked = []

    class Stubborn:
        async def workflow(self, request):
            asked.append(request)
            return json.dumps(workflow(expect_booking=at(14, 30)))

    with runtime.factory() as session:
        crew = session.get(CrewInstance, crew_id)
        crew.configuration_plan = {**crew.configuration_plan, "test_level": "thorough"}
        session.commit()
    runtime.coordinator = Stubborn()
    record = client.portal.call(setup_test, runtime, crew_id)
    assert record["status"] == "partial" and record["fixed_rounds"] == 3 and len(asked) == 3


def test_core_is_asked_five_times_at_most_in_one_setup_test(client, auth, monkeypatch):
    from mavo.workflow_write import limited

    async def answer(request):
        return "{}"

    ask = limited(answer, 5)
    for _ in range(5):
        client.portal.call(ask, {})
    try:
        client.portal.call(ask, {})
        raise AssertionError("a sixth call")
    except ValueError as exc:
        assert "asked 5 times" in str(exc)


def test_an_agent_that_books_only_after_acceptance_keeps_that_and_code_checks_the_time(
    client, auth, monkeypatch
):
    """Moving such a booking onto the found time dropped "only after they accept": a request
    was booked at once, and twice. The agent decides when; code checks the time is free and
    inside the hours as it books."""
    from mavo.workflow_write import check_workflow

    written = workflow()
    steps = written["steps"]
    accepted = {
        "id": "accepted",
        "kind": "helper",
        "role": "extract",
        "title": "Accepted",
        "instructions": "List the meetings the sender has accepted, with the time.",
        "uses": ["understand", "slots"],
        "returns": {
            "fields": {"thread_id": "id", "subject": "text", "start": "time", "end": "time"}
        },
    }
    steps.insert(steps.index(next(s for s in steps if s["id"] == "book")), accepted)
    book = next(s for s in steps if s["id"] == "book")
    book.update(
        {
            "args_from": "accepted",
            "uses": ["accepted"],
            "where": None,
            "args": {
                "summary": "{{item.subject}}",
                "start": "{{item.start}}",
                "end": "{{item.end}}",
            },
        }
    )
    plan, _ = check_workflow(written, TAGS)  # accepted as written, never rewired
    assert next(s for s in plan.steps if s.id == "book").args_from == "accepted"


def test_a_booking_outside_the_hours_is_refused_as_it_is_booked(client, auth, monkeypatch):
    written = workflow()
    for test in written["tests"]:
        test["expect"] = {}
    steps = written["steps"]
    steps.insert(
        steps.index(next(s for s in steps if s["id"] == "book")),
        {
            "id": "late",
            "kind": "helper",
            "role": "extract",
            "title": "Late",
            "instructions": "Book a late meeting.",
            "uses": ["understand"],
            "returns": {
                "fields": {"thread_id": "id", "subject": "text", "start": "time", "end": "time"}
            },
        },
    )
    book = next(s for s in steps if s["id"] == "book")
    book.update(
        {
            "args_from": "late",
            "uses": ["late"],
            "where": None,
            "args": {
                "summary": "{{item.subject}}",
                "start": "{{item.start}}",
                "end": "{{item.end}}",
            },
        }
    )
    runtime, crew_id, _ = prepare(client, auth, monkeypatch, written)
    real = runtime.router.chat

    async def late(provider, model, messages, system, credential_id=None):
        if "YOUR JOB: Book a late meeting." in messages[0]["content"]:
            return json.dumps(
                [{"thread_id": "t1", "subject": "Call?", "start": at(20), "end": at(20, 30)}]
            )
        return await real(provider, model, messages, system, credential_id)

    runtime.router.chat = late
    monkeypatch.setattr("mavo.workflow_write.FIX_ROUNDS", 0)
    record = client.portal.call(setup_test, runtime, crew_id)
    first = record["tests"][0]
    assert not any(w["operation"] == "calendar.create" for w in first["would"])  # never 8 PM
    assert any("outside 09:00 to 18:00" in line for line in first["stopped"] + first["problems"])


def test_a_plan_core_cannot_write_is_said_in_plain_words(client, auth, monkeypatch):
    from mavo.workflow_write import core_chat, snapshot_of, write_workflow

    runtime, crew_id, _ = prepare(client, auth, monkeypatch, {"steps": []})
    with runtime.factory() as session:
        crew = snapshot_of(session.get(CrewInstance, crew_id))
    try:
        client.portal.call(write_workflow, runtime, crew, core_chat(runtime))
        raise AssertionError("nothing that checks out was written")
    except ValueError as exc:
        message = str(exc)
    assert message.startswith("Mavo Core could not write a working plan for this crew")
    assert "{{" not in message and "args_from" not in message


def test_an_optional_detail_from_a_missing_field_is_left_out_not_the_whole_plan():
    from mavo.workflow_write import check_workflow

    written = workflow()
    book = next(s for s in written["steps"] if s["id"] == "book")
    book["args"]["description"] = "{{item.description}}"  # find time hands on no description
    plan, _ = check_workflow(written, TAGS)
    assert "description" not in next(s for s in plan.steps if s.id == "book").args
    required = workflow()
    next(s for s in required["steps"] if s["id"] == "book")["args"]["start"] = "{{item.when}}"
    try:
        check_workflow(required, TAGS)
        raise AssertionError("a start time is never guessed")
    except ValueError as exc:
        assert "item.when" in str(exc)


def test_under_half_passing_is_not_recommended_and_cannot_be_turned_on_anyway(
    client, auth, monkeypatch
):
    written = workflow(expect_booking=at(14, 30))
    for test in written["tests"][1:]:
        test["expect"] = {"gmail.send": [{"to": "nobody@example.com"}]}  # all wrong
    runtime, crew_id, _ = prepare(client, auth, monkeypatch, written)
    monkeypatch.setattr("mavo.workflow_write.FIX_ROUNDS", 0)
    record = client.portal.call(setup_test, runtime, crew_id)
    assert record["status"] == "failed"
    assert record["message"] == "Passed 0 of 3 tests. Not recommended to turn on yet."
    refused = client.post(f"/api/v1/crew/{crew_id}/workflow/accept", headers=auth)
    assert refused.status_code == 409


def test_a_mostly_passing_path_is_turned_on_only_when_the_person_chooses(client, auth, monkeypatch):
    runtime, crew_id, _ = prepare(client, auth, monkeypatch, workflow(expect_booking=at(14, 30)))
    monkeypatch.setattr("mavo.workflow_write.FIX_ROUNDS", 0)
    record = client.portal.call(setup_test, runtime, crew_id)
    assert record["status"] == "partial"
    with runtime.factory() as session:
        assert "recipe" not in session.get(CrewInstance, crew_id).configuration_plan
    on = client.post(f"/api/v1/crew/{crew_id}/workflow/accept", headers=auth).json()
    plan = on["configuration_plan"]
    assert plan["recipe_tested"] and plan["recipe"]["final"] == "tell"
    assert plan["first_run"]["accepted_with"] == ["Second offered time free"]


def test_the_testing_level_sets_how_many_emails_run_and_how_many_fix_rounds(
    client, auth, monkeypatch
):
    from mavo.workflow_write import spread

    twelve = [{"name": f"case {n}"} for n in range(12)]
    assert [t["name"] for t in spread(twelve, 4)] == ["case 0", "case 3", "case 6", "case 9"]
    assert spread(twelve[:3], 8) == twelve[:3]
    runtime, crew_id, asked = prepare(
        client, auth, monkeypatch, workflow(expect_booking=at(14, 30))
    )
    with runtime.factory() as session:
        crew = session.get(CrewInstance, crew_id)
        crew.configuration_plan = {**crew.configuration_plan, "test_level": "quick"}
        session.commit()
    record = client.portal.call(setup_test, runtime, crew_id)
    assert record["total"] == 3 and record["fixed_rounds"] == 1 and len(asked) == 1


def test_the_person_sets_the_hours_and_they_stay_when_the_path_is_written_again(
    client, auth, monkeypatch
):
    """The hours are the person's, shown and changed on the card: never a model's guess."""
    from mavo.workflow_write import draft_workflow, with_settings, zone_label

    runtime, crew_id, _ = prepare(client, auth, monkeypatch, {"steps": []})
    client.portal.call(draft_workflow, runtime, crew_id)
    with runtime.factory() as session:  # saved with UTC by a model, as before
        crew = session.get(CrewInstance, crew_id)
        plan = crew.configuration_plan
        crew.configuration_plan = {
            **plan,
            "workflow_draft": with_settings(plan["workflow_draft"], {"timezone": "+00:00"}),
        }
        session.commit()
    url = f"/api/v1/crew/{crew_id}/desk"
    bad = client.post(url, json={"start": "18:00", "end": "09:00"}, headers=auth)
    assert bad.status_code == 422 and "after the start" in bad.text
    assert client.post(url, json={"days": []}, headers=auth).status_code == 422
    assert client.post(url, json={"timezone": "+00:00"}, headers=auth).status_code == 422
    saved = client.post(
        url,
        json={"start": "10:00", "end": "16:30", "days": ["Sat", "mon"], "duration_minutes": 45},
        headers=auth,
    )
    assert saved.status_code == 200, saved.text

    def desk_params():
        with runtime.factory() as session:
            plan = session.get(CrewInstance, crew_id).configuration_plan
            [step] = [s for s in plan["workflow_draft"]["steps"] if s.get("automation")]
            return step["params"]

    params = desk_params()
    assert (params["start"], params["end"], params["days"], params["duration_minutes"]) == (
        "10:00", "16:30", ["mon", "sat"], 45,
    )
    assert params["timezone"] == zone_label()
    with runtime.factory() as session:  # its path written again, as after a refined job
        crew = session.get(CrewInstance, crew_id)
        crew.configuration_plan = {
            k: v for k, v in crew.configuration_plan.items() if k != "workflow_draft"
        }
        session.commit()
    client.portal.call(draft_workflow, runtime, crew_id)
    assert desk_params()["start"] == "10:00" and desk_params()["duration_minutes"] == 45
