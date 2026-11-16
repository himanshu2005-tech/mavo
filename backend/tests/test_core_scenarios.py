"""From running the person's 21 Mavo Core tests: partial work is delivered and said to be
partial, an empty web search is a finding, an over-long list is trimmed, a busy planning model
is tried once more, and a corrected fact replaces the old one instead of living beside it."""

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from mavo.crew_workers import WorkCheckpoint
from mavo.model_router import ModelProviderError
from mavo.shared_memory import add_fact, facts, forget_text
from mavo.site_skills import WebParams, web_read
from tests.test_tasks import scripted, setup, start, wait


def test_a_writer_still_writes_from_what_finished_and_says_what_did_not(client, auth, monkeypatch):
    """Test 17: Gmail was turned off during the task, and the whole report was lost."""
    runtime = setup(client, auth)
    seen = scripted(runtime, helper={"writer": "Calendar: no conflicts. Mail: not available."})
    runner = runtime.task_runner
    monkeypatch.setattr(runner, "google_readable", lambda: {"gmail", "calendar"})

    async def read(resource):
        if resource == "gmail":
            from mavo.google_auth import GoogleConnectionError

            raise GoogleConnectionError("Gmail access was turned off")
        return {"items": [{"id": "e1", "summary": "Standup", "start": "10:00"}]}

    monkeypatch.setattr(runtime.google_pool, "read", read)
    plan = {
        "title": "Interview check",
        "deliver": "Interview and conflicts",
        "final": "w",
        "steps": [
            {"id": "mail", "kind": "read", "source": "gmail"},
            {"id": "cal", "kind": "read", "source": "calendar"},
            {
                "id": "w",
                "kind": "helper",
                "role": "writer",
                "title": "Write",
                "instructions": "Report.",
                "uses": ["mail", "cal"],
            },
        ],
    }
    done = wait(client, auth, start(client, auth, plan))
    assert done["status"] == "done", [(s["id"], s["status"], s.get("error")) for s in done["steps"]]
    assert done["result"].startswith("Calendar: no conflicts.")
    assert "Not completed:" in done["result"] and "Gmail access was turned off" in done["result"]
    prompt = seen["helper_prompts"][0][1]
    assert "NOT AVAILABLE" in prompt and "Standup" in prompt


@pytest.mark.asyncio
async def test_an_empty_web_search_is_a_finding_not_a_failed_task(monkeypatch):
    searched = []

    class Desktop:
        page = None

        def check(self):
            pass

        async def browser(self, operation, args, rule, owner, scope_guard=None, limit=None):
            searched.append(args["query"])
            return {"results": []}

    out = json.loads(
        await web_read(
            Desktop(),
            {"mode": "unrestricted", "domains": []},
            "o",
            WebParams(query="can small language models outperform larger models on tasks"),
        )
    )
    assert out["pages_read"] == [] and "gave no results" in out["note"]
    assert searched[-1] == "can small language models outperform larger"  # shorter, once


def test_an_over_long_list_in_an_agent_reply_is_trimmed_not_failed():
    checkpoint = WorkCheckpoint.model_validate(
        {
            "state": "wait_for_next_review",
            "summary": "Looked things up.",
            "lookups": [{"source": "wikipedia", "query": str(i)} for i in range(6)],
        }
    )
    assert len(checkpoint.lookups) == 4


@pytest.mark.asyncio
async def test_a_busy_planning_model_is_tried_once_more(client):
    runtime = client.app.state.crew_runtime
    planner = runtime.planner
    calls = []

    async def chat(provider, model, messages, system, credential_id=None):
        calls.append(1)
        if len(calls) == 1:
            raise ModelProviderError("AIRouter could not complete the request (503)")
        return '{"reply": "ok"}'

    runtime.router.chat = chat
    route = SimpleNamespace(provider="airouter", model="m", credential_id="c")
    assert await planner.planning_call(route, [], "system") == '{"reply": "ok"}'
    assert len(calls) == 2
    calls.clear()

    async def refused(provider, model, messages, system, credential_id=None):
        calls.append(1)
        raise ModelProviderError("The provider rejected the saved API key")

    runtime.router.chat = refused
    with pytest.raises(ModelProviderError):
        await planner.planning_call(route, [], "system")
    assert len(calls) == 1  # a refused key is not tried again


def test_a_corrected_fact_replaces_the_old_one(client):
    """Tests 09 and 10: the new deadline and database were added beside the old ones."""
    with client.app.state.crew_runtime.factory() as session:
        add_fact(session, "Project Orion's database is PostgreSQL")
        add_fact(session, "Project Orion's deadline is December 18")
        assert forget_text(session, "project orion's database is postgresql.") == 1
        add_fact(session, "Project Orion's database is MongoDB")
        texts = [f["text"] for f in facts(session)]
        assert texts == [
            "Project Orion's deadline is December 18",
            "Project Orion's database is MongoDB",
        ]
        assert forget_text(session, "A code that was never stored") == 0


def test_a_step_feeding_an_action_is_told_its_exact_fields():
    from mavo.recipes import json_for_actions
    from mavo.task_plan import TaskPlan

    plan = json_for_actions(
        TaskPlan.model_validate(
            {
                "title": "t",
                "deliver": "d",
                "steps": [
                    {
                        "id": "find",
                        "kind": "helper",
                        "role": "extract",
                        "title": "Find",
                        "instructions": "Find the reminder.",
                    },
                    {"id": "add", "kind": "act", "operation": "tasks.create", "args_from": "find"},
                ],
            }
        )
    )
    assert "exactly the fields title; optional: notes, due" in plan.steps[0].instructions


def test_a_cut_off_plan_is_asked_for_again_shorter():
    from mavo.crew_planner import repair_request

    assert "much shorter" in repair_request('{"reply":"x","task":{"steps":[{"id', ValueError("x"))
    assert "had no JSON" in repair_request("I'll research this.", ValueError("x"))
    assert repair_request('{"reply": 1}', ValueError("bad")).startswith("Return valid JSON")


def test_bad_evidence_is_dropped_only_after_the_repair():
    good = {
        "kind": "source",
        "url": "https://example.org/a",
        "quote": "A quote long enough to keep.",
    }
    reply = {
        "state": "wait_for_next_review",
        "summary": "x",
        "evidence": [good, {**good, "url": "example.org/b"}],
    }
    with pytest.raises(ValidationError):
        WorkCheckpoint.model_validate(reply)
    kept = WorkCheckpoint.model_validate(reply, context={"repaired": True})
    assert len(kept.evidence) == 1


def test_an_invented_tool_never_fails_an_agents_whole_reply():
    """Test 14: a workspace scan proposed tools Mavo does not have. A bad quote still goes
    back for repair, because it means the answer itself may not hold up."""
    good = {
        "kind": "source",
        "url": "https://example.org/a",
        "quote": "A quote long enough to keep.",
    }
    checkpoint = WorkCheckpoint.model_validate(
        {
            "state": "wait_for_next_review",
            "summary": "Scanned the workspace.",
            "actions": [
                {"operation": "files.tree", "arguments": {}},
                {"operation": "files.list", "arguments": {"folder": "C:\\Projects"}},
            ],
            "evidence": [good],
        }
    )
    assert [a.operation for a in checkpoint.actions] == ["files.list"]
    assert len(checkpoint.evidence) == 1
    with pytest.raises(ValidationError):
        WorkCheckpoint.model_validate(
            {"state": "wait_for_next_review", "summary": "x", "evidence": [{**good, "quote": ""}]}
        )
    assert "ignored 1 action(s) it does not have: files.tree" in checkpoint.summary


@pytest.mark.asyncio
async def test_what_the_search_page_showed_is_given_to_the_model():
    """A search gave no results because the engine showed a check page. Mavo does not decide
    what the page means from fixed words: it passes what the page showed, and the model says."""

    class Desktop:
        page = None

        def check(self):
            pass

        async def browser(self, operation, args, rule, owner, scope_guard=None, limit=None):
            return {"results": [], "search_page": "Please confirm you are a person."}

    out = json.loads(
        await web_read(
            Desktop(),
            {"mode": "unrestricted", "domains": []},
            "o",
            WebParams(query="small language models"),
        )
    )
    assert "Please confirm you are a person." in out["note"]
    assert "never fill the gap from memory" in out["note"]


def test_the_person_reads_what_an_action_did_not_its_json(client, auth, monkeypatch):
    """Test 19: the result was '[{"title": "Review Project Orion", ...}]'."""
    runtime = setup(client, auth)
    scripted(runtime, helper={"extract": '[{"title": "Review Project Orion"}]'})

    def propose(draft, crew_id=None, generation=None, event_key=""):
        return {"id": "a1"}

    async def maybe_execute(action_id):
        return {"id": action_id, "status": "completed", "target_summary": "Review Project Orion"}

    monkeypatch.setattr(runtime.actions, "propose", propose)
    monkeypatch.setattr(runtime.actions, "maybe_execute", maybe_execute)
    plan = {
        "title": "Add task",
        "deliver": "The task",
        "final": "args",
        "steps": [
            {
                "id": "args",
                "kind": "helper",
                "role": "extract",
                "title": "Details",
                "instructions": "Give the task.",
            },
            {"id": "add", "kind": "act", "operation": "tasks.create", "args_from": "args"},
        ],
    }
    done = wait(client, auth, start(client, auth, plan))
    assert done["result"] == "Done: Create task (Review Project Orion)"


def test_a_refused_action_leaves_no_json_as_the_answer(client, auth, monkeypatch):
    """Test 03: the follow-up task was refused and the person was shown its JSON."""
    runtime = setup(client, auth)
    scripted(runtime, helper={"extract": '[{"title": "Follow up"}]'})

    def propose(draft, crew_id=None, generation=None, event_key=""):
        return {"id": "a1"}

    async def maybe_execute(action_id):
        return {"id": action_id, "status": "rejected", "error": "Tasks is off"}

    monkeypatch.setattr(runtime.actions, "propose", propose)
    monkeypatch.setattr(runtime.actions, "maybe_execute", maybe_execute)
    plan = {
        "title": "Add task",
        "deliver": "The task",
        "final": "args",
        "steps": [
            {
                "id": "args",
                "kind": "helper",
                "role": "extract",
                "title": "Details",
                "instructions": "Give the task.",
            },
            {"id": "add", "kind": "act", "operation": "tasks.create", "args_from": "args"},
        ],
    }
    done = wait(client, auth, start(client, auth, plan))
    assert "[{" not in (done.get("result") or "")


def test_an_out_of_credits_planning_route_says_so():
    from mavo.core_errors import PROBLEMS

    assert "out of credits" in PROBLEMS["core_route_credits"][0]


def test_refused_action_details_are_corrected_by_the_model(client, auth, monkeypatch):
    """Mavo does not map field names or formats itself: the refused details, the action's real
    fields and the error go back to a model once, and its correction is used."""
    runtime = setup(client, auth)
    seen = scripted(
        runtime,
        helper={"extract": '[{"name": "Follow up", "due_date": "2026-11-17T10:00"}]'},
        crew=[{"title": "Follow up", "due": "2026-11-17"}],
    )
    proposed = []

    def propose(draft, crew_id=None, generation=None, event_key=""):
        proposed.append(draft.arguments)
        return {"id": "a1"}

    async def maybe_execute(action_id):
        return {"id": action_id, "status": "completed", "target_summary": "Follow up"}

    monkeypatch.setattr(runtime.actions, "propose", propose)
    monkeypatch.setattr(runtime.actions, "maybe_execute", maybe_execute)
    plan = {
        "title": "Add task",
        "deliver": "The task",
        "final": "args",
        "steps": [
            {
                "id": "args",
                "kind": "helper",
                "role": "extract",
                "title": "Details",
                "instructions": "Give the task.",
            },
            {"id": "add", "kind": "act", "operation": "tasks.create", "args_from": "args"},
        ],
    }
    done = wait(client, auth, start(client, auth, plan))
    assert done["result"] == "Done: Create task (Follow up)"
    assert proposed and proposed[0]["due"] == "2026-11-17"
    asked = seen["crew_prompts"][0]
    assert "exactly the fields title" in asked and "due_date" in asked


@pytest.mark.asyncio
async def test_a_task_and_an_unasked_agent_go_back_to_the_model_once(client):
    """The model, not a keyword list, decides whether the person asked for ongoing work."""
    from mavo.coordinator_contract import CoordinatorDecision, DialogueMessage

    planner = client.app.state.crew_runtime.planner
    first = CoordinatorDecision.model_validate(
        {"reply": "Started, and drafted a weekly check-in.", "crew_proposals": [], "task": None}
    )
    checked = CoordinatorDecision.model_validate({"reply": "Started.", "task": None})
    sent = []

    async def complete(turn):
        sent.append(turn)
        return checked, "m"

    planner._complete_plan = complete
    turn = SimpleNamespace(
        messages=[DialogueMessage(role="user", content="Improve my job prospects")],
        model_copy=lambda update: SimpleNamespace(**update),
    )
    out = await planner.one_thing(turn, first)
    assert out is checked
    note = sent[0].messages[-1].content
    assert "not the person" in note and "repeats or keeps watching" in note


def test_a_new_email_is_judged_with_its_text_when_the_person_allowed_it(client, auth):
    """A meeting request titled "Internship Offer" was passed over: the step looking for
    meeting requests saw a sender and a subject and nothing else."""
    runtime = setup(client, auth)
    seen = scripted(runtime, helper={"classify": "[]"})

    class Pool:
        auth = SimpleNamespace(
            status=lambda: {"signed_in": True, "capabilities": ["gmail:read", "gmail:body"]}
        )

        async def read(self, source):
            return {"items": [{"id": "m1", "sender": "a@b.com", "subject": "Internship Offer"}]}

        async def message_body(self, message_id):
            return {"id": message_id, "body": "Could we do a 30-minute call this week?"}

    runtime.google_pool = Pool()
    plan = {
        "title": "Mail",
        "deliver": "Meeting requests",
        "final": "find",
        "steps": [
            {"id": "mail", "kind": "read", "source": "gmail", "new_only": True},
            {
                "id": "find",
                "kind": "helper",
                "role": "classify",
                "title": "Find",
                "instructions": "Pick meeting requests.",
                "uses": ["mail"],
            },
        ],
    }
    wait(client, auth, start(client, auth, plan))
    assert "Could we do a 30-minute call this week?" in seen["helper_prompts"][0][1]


def test_without_permission_an_emails_text_is_not_read(client, auth):
    runtime = setup(client, auth)
    seen = scripted(runtime, helper={"classify": "[]"})
    asked = []

    class Pool:
        auth = SimpleNamespace(status=lambda: {"signed_in": True, "capabilities": ["gmail:read"]})

        async def read(self, source):
            return {"items": [{"id": "m1", "sender": "a@b.com", "subject": "Hello"}]}

        async def message_body(self, message_id):
            asked.append(message_id)
            return {"body": "secret"}

    runtime.google_pool = Pool()
    plan = {
        "title": "Mail",
        "deliver": "x",
        "final": "find",
        "steps": [
            {"id": "mail", "kind": "read", "source": "gmail"},
            {
                "id": "find",
                "kind": "helper",
                "role": "classify",
                "title": "Find",
                "instructions": "Look.",
                "uses": ["mail"],
            },
        ],
    }
    wait(client, auth, start(client, auth, plan))
    assert asked == [] and "secret" not in seen["helper_prompts"][0][1]


@pytest.mark.asyncio
async def test_a_reply_draft_joins_the_conversation_it_answers():
    """The draft carried Gmail's thread id as its In-Reply-To and sat outside the thread."""
    from mavo.google_actions import GoogleActions

    class Found:
        status_code, is_error = 200, False

        @staticmethod
        def json():
            return {
                "messages": [
                    {
                        "payload": {
                            "headers": [
                                {"name": "Message-ID", "value": "<abc@mail.gmail.com>"},
                                {"name": "References", "value": "<first@mail.gmail.com>"},
                            ]
                        }
                    }
                ]
            }

    class Missing:
        status_code, is_error = 404, True

    async def found(*args, **kwargs):
        return Found()

    async def missing(*args, **kwargs):
        return Missing()

    me = SimpleNamespace(request=found)
    args = {"thread_id": "1a1d3bca5bb60108", "in_reply_to": "1a1d3bca5bb60108"}
    assert await GoogleActions.reply_headers(me, args, None) == (
        "1a1d3bca5bb60108",
        "<abc@mail.gmail.com>",
        "<first@mail.gmail.com> <abc@mail.gmail.com>",
    )
    me.request = missing  # a thread Gmail does not know: a plain new draft, not a failure
    assert await GoogleActions.reply_headers(me, args, None) == (None, None, None)


def test_a_crews_history_includes_its_task_runs(client, auth):
    """ "Run now" on a Crew of several agents came back with nothing to show, though it ran."""
    from mavo.database import CrewInstance, TaskRun

    runtime = client.app.state.crew_runtime
    with runtime.factory() as session:
        session.add(
            CrewInstance(
                id="team",
                name="Meeting Reply Desk",
                template_id="communication",
                responsibilities="Draft replies.",
                permissions=["model:invoke"],
                provider="ollama",
                model="m",
                trigger_type="interval",
            )
        )
        session.add(
            TaskRun(
                id="t1",
                crew_id="team",
                title="Cycle",
                request="Draft replies.",
                status="done",
                result="Nothing new.",
                plan={"steps": []},
                steps=[],
            )
        )
        session.commit()
    runs = client.get("/api/v1/crew-runs?crew_id=team", headers=auth).json()
    assert [(r["kind"], r["status"], r["result"]) for r in runs] == [
        ("task", "completed", "Nothing new.")
    ]


def test_a_draft_to_an_address_nothing_mentioned_goes_back_to_the_model(client, auth, monkeypatch):
    """A setup test's draft went to asha.r@example.com, which no email had come from."""
    runtime = setup(client, auth)
    scripted(
        runtime,
        helper={"extract": '[{"to": "someone@example.com", "subject": "Re: Call", "body": "Hi"}]'},
        crew=[{}],  # the model, shown the error, finds no real address: nothing is drafted
    )
    proposed = []

    def propose(draft, crew_id=None, generation=None, event_key=""):
        proposed.append(draft.arguments)
        return {"id": "a1"}

    async def maybe_execute(action_id):
        return {"id": action_id, "status": "completed"}

    monkeypatch.setattr(runtime.actions, "propose", propose)
    monkeypatch.setattr(runtime.actions, "maybe_execute", maybe_execute)
    plan = {
        "title": "Reply",
        "deliver": "A draft reply",
        "final": "args",
        "steps": [
            {
                "id": "args",
                "kind": "helper",
                "role": "extract",
                "title": "Details",
                "instructions": "Give the draft.",
            },
            {"id": "draft", "kind": "act", "operation": "gmail.draft", "args_from": "args"},
        ],
    }
    done = wait(client, auth, start(client, auth, plan))
    assert proposed == []
    assert "not an address in anything this task read" in (done.get("error") or "") + (
        done.get("result") or ""
    )


def test_the_model_sees_the_coming_days_with_their_weekdays():
    from mavo.tasks import now_line

    line = now_line()
    assert line.startswith("NOW: ") and "Next days: " in line
    assert len(line.split("Next days: ")[1].split(", ")) == 14


def test_writing_for_the_person_keeps_to_what_was_given():
    from mavo.crew_engine import crew_system_prompt
    from mavo.helpers import ACTING_RULES, system_for

    assert ACTING_RULES in system_for("writer")
    assert "never leave a placeholder" in ACTING_RULES and "starts after NOW" in ACTING_RULES
    crew = SimpleNamespace(template_id="communication", name="Desk", responsibilities="Reply.")
    assert ACTING_RULES in crew_system_prompt(crew)


def test_a_long_read_is_cut_to_whole_items_and_stays_json():
    from mavo.tasks import fitted_json

    value = {"source": "gmail", "items": [{"id": str(i), "text": "x" * 5000} for i in range(30)]}
    out = fitted_json(value, 20000)
    assert len(out) <= 20000 and json.loads(out)["source"] == "gmail"


def test_a_message_saying_what_the_person_never_said_is_not_drafted(client, auth, monkeypatch):
    """A reply to a feedback survey said the person had "encountered some minor usability
    issues". A model checks a message against what was read before it goes in their name."""
    runtime = setup(client, auth)
    scripted(
        runtime,
        helper={
            "extract": '[{"to": "team@service.example", "subject": "Re: Survey", '
            '"body": "I found the interface confusing."}]'
        },
        crew=[
            {"ok": False, "problem": "It gives an opinion the person never gave."},
            # asked again, its writer gave the same opinion: checked again, still refused
            {"ok": False, "problem": "It still gives an opinion the person never gave."},
        ],
    )

    class Pool:
        auth = SimpleNamespace(status=lambda: {"signed_in": True, "capabilities": ["gmail:read"]})

        async def read(self, source):
            return {
                "items": [
                    {"id": "m1", "sender": "Team <team@service.example>", "subject": "Survey"}
                ]
            }

    runtime.google_pool = Pool()
    proposed = []
    monkeypatch.setattr(
        runtime.actions, "propose", lambda draft, *a, **k: proposed.append(draft) or {"id": "a1"}
    )
    plan = {
        "title": "Replies",
        "deliver": "Draft replies",
        "final": "args",
        "steps": [
            {"id": "mail", "kind": "read", "source": "gmail"},
            {
                "id": "args",
                "kind": "helper",
                "role": "extract",
                "title": "Details",
                "instructions": "Give the drafts.",
                "uses": ["mail"],
            },
            {"id": "draft", "kind": "act", "operation": "gmail.draft", "args_from": "args"},
        ],
    }
    done = wait(client, auth, start(client, auth, plan))
    assert proposed == []
    assert "opinion the person never gave" in (done.get("error") or "") + (done.get("result") or "")


def test_the_report_reads_what_its_actions_really_did():
    """A report said "a Focus Time block was created" when the action made none."""
    from mavo.recipes import json_for_actions
    from mavo.task_plan import TaskPlan

    plan = json_for_actions(
        TaskPlan.model_validate(
            {
                "title": "Focus",
                "deliver": "A focus block",
                "final": "report",
                "steps": [
                    {
                        "id": "decide",
                        "kind": "helper",
                        "role": "planner",
                        "title": "Decide",
                        "instructions": "Pick a slot.",
                    },
                    {
                        "id": "book",
                        "kind": "act",
                        "operation": "calendar.create",
                        "args_from": "decide",
                    },
                    {
                        "id": "report",
                        "kind": "helper",
                        "role": "writer",
                        "title": "Report",
                        "instructions": "Say what happened.",
                        "uses": ["decide"],
                    },
                ],
            }
        )
    )
    report = next(s for s in plan.steps if s.id == "report")
    assert "book" in report.uses


def test_work_on_some_days_keeps_its_days():
    """ "Every Friday at 5 pm" ran every day at 5 pm."""
    from datetime import datetime, timedelta

    from mavo.crew_workers import next_work_time
    from mavo.daily_triggers import DailyTrigger
    from mavo.schemas import CrewDraft

    draft = CrewDraft.model_validate(
        {
            "template_id": "communication",
            "name": "Friday drafts",
            "responsibilities": "Draft replies to this week's unanswered mail.",
            "trigger_type": "interval",
            "review_interval_minutes": 1440,
            "schedule_time": "17:00",
            "daily_triggers": [{"time": "17:00", "weekdays": [4]}],
            "requested_google_resources": ["gmail"],
        }
    )
    assert draft.trigger_type == "interval" and draft.daily_triggers[0].weekdays == [4]
    crew = SimpleNamespace(
        daily_triggers=[DailyTrigger(time="17:00", weekdays=[4]).model_dump()],
        schedule_time="17:00",
        review_interval_minutes=1440,
        last_run_at=None,
        reminder_text="",
        responsibilities="x",
    )
    now = datetime.now().astimezone()
    for days in range(7):
        assert next_work_time(crew, now + timedelta(days=days)).astimezone().weekday() == 4


def test_a_crew_working_as_a_task_keeps_its_time_and_days():
    """A "Friday at 5 pm" Crew set up at 1:48 pm worked every day at 1:48 pm."""
    from mavo.crew_composition import next_cycle
    from mavo.daily_triggers import DailyTrigger

    crew = SimpleNamespace(
        trigger_type="interval",
        schedule_time="17:00",
        review_interval_minutes=1440,
        last_run_at=None,
        daily_triggers=[DailyTrigger(time="17:00", weekdays=[4]).model_dump()],
        reminder_text="",
        responsibilities="x",
    )
    when = next_cycle(crew, event_based=False).astimezone()
    assert when.weekday() == 4 and when.strftime("%H:%M") == "17:00"


def test_an_event_that_has_already_started_goes_back_to_the_model(client, auth, monkeypatch):
    """A focus block was booked at 2 pm for 9 to 11 that morning."""
    runtime = setup(client, auth)
    scripted(
        runtime,
        helper={
            "extract": '[{"summary": "Focus", "start": "2020-01-01T09:00:00+05:30", '
            '"end": "2020-01-01T11:00:00+05:30"}]'
        },
        crew=[{}],  # shown that the time has passed, the model leaves it out
    )
    proposed = []
    monkeypatch.setattr(
        runtime.actions, "propose", lambda draft, *a, **k: proposed.append(draft) or {"id": "a1"}
    )
    plan = {
        "title": "Focus",
        "deliver": "A focus block",
        "final": "args",
        "steps": [
            {
                "id": "args",
                "kind": "helper",
                "role": "extract",
                "title": "Details",
                "instructions": "Give the block.",
            },
            {"id": "book", "kind": "act", "operation": "calendar.create", "args_from": "args"},
        ],
    }
    done = wait(client, auth, start(client, auth, plan))
    assert proposed == [] and "has already passed" in (done.get("error") or "") + (
        done.get("result") or ""
    )


def test_what_one_agent_read_is_known_to_every_agent_after_it(client):
    """A slot planner asked the person for a sender's address: the agent that read the email
    had handed on a summary without it. The run keeps what was read, for every later agent."""
    from mavo.database import TaskRun
    from mavo.tasks import facts_of

    runtime = client.app.state.crew_runtime
    runner = runtime.task_runner
    with runtime.factory() as session:
        session.add(
            TaskRun(
                id="run1",
                title="Meetings",
                request="Book meetings",
                status="running",
                plan={"steps": []},
                steps=[{"id": "scout"}, {"id": "planner"}],
            )
        )
        session.commit()
    mail = {
        "id": "m1",
        "thread_id": "t1",
        "sender": "NovaTech AI <hr@novatech.example>",
        "subject": "Interview invitation",
        "date": "Fri, 13 Nov 2026",
        "labels": ["INBOX"],
        "text": "Long body that is never shared",
    }
    assert facts_of({"gmail": [mail]}) == {
        "gmail": [{k: mail[k] for k in ("id", "thread_id", "sender", "subject", "date")}]
    }
    runner.remember_read("run1", "scout", {"gmail": [mail], "message_contents": [{"body": "x"}]})
    shared = runner.run_knowledge("run1", {"gmail", "calendar"}, skip="planner")
    assert "hr@novatech.example" in shared and "t1" in shared
    assert "Long body" not in shared  # facts, never a message's body
    assert runner.run_knowledge("run1", {"calendar"}) == ""  # only what this step may read
    assert runner.run_knowledge("run1", {"gmail"}, skip="scout") == ""  # not its own read again


def test_a_question_with_options_is_answered_by_pressing_one(client, auth):
    """When a choice really is the person's, its options are buttons: on the task card and on
    the desktop alert, which shows the step's choices; the one pressed is the answer."""
    from tests.test_tasks import ANSWER, allow_browser, asked, fake_site, plan, step

    runtime = setup(client, auth)
    allow_browser(client, auth)
    fake_site(runtime)
    question = {
        "state": "waiting_for_input",
        "summary": "Three times were offered.",
        "question": "Which time should I book?",
        "choices": ["Mon 16 Nov 10:00", "Tue 17 Nov 11:00"],
    }
    seen = scripted(runtime, crew=[question, ANSWER])
    run_id = start(client, auth, plan(step("make", "agent")))
    run, one = asked(client, auth, run_id)
    assert one["choices"] == ["Mon 16 Nov 10:00", "Tue 17 Nov 11:00"]
    due = client.get("/api/v1/reminders/due", headers=auth).json()
    assert any(a.get("task_step_id") == "make" for a in due)  # the alert that shows the buttons
    sent = client.post(
        f"/api/v1/tasks/{run_id}/answer",
        headers=auth,
        json={"answer": "Tue 17 Nov 11:00", "step_id": "make"},
    )
    assert sent.status_code == 202
    assert wait(client, auth, run_id)["status"] == "done"
    assert "Tue 17 Nov 11:00" in seen["crew_prompts"][1]


def test_a_team_member_is_told_who_decides_after_it():
    from mavo.helpers import ACTING_RULES

    assert "never ask the person to make a choice your instructions or a later teammate make" in (
        ACTING_RULES
    )


def test_an_all_day_event_says_which_days_it_covers():
    """Google gives an all-day event's end as the day after it: a reviewer read a holiday on the
    15th as running into the 16th and refused a free slot that morning."""
    from mavo.resources.google import with_days

    holiday = with_days(
        {"summary": "Chhat Puja", "start": {"date": "2026-11-15"}, "end": {"date": "2026-11-16"}}
    )
    assert holiday["all_day_on"].startswith("2026-11-15 (all day")
    assert "all_day_on" not in with_days({"start": {"dateTime": "2026-11-16T10:00:00+05:30"}})


def test_a_crew_whose_real_data_gave_it_nothing_is_tested_with_a_sample():
    """An empty inbox passed the setup test without ever trying the Crew's booking and reply.
    Then Mavo writes a sample from the Crew's job and tests again, said to be a sample."""
    from mavo.first_run import needs_sample, safe_sample, sample_sources

    crew = SimpleNamespace(
        configuration_plan={"draft": {"requested_google_resources": ["gmail", "calendar"]}},
        permissions=["google:gmail:read", "google:calendar:read", "feeds:bbc:read"],
    )
    sources = sample_sources(crew)
    assert sources == ["calendar", "gmail"]  # account sources; feeds are left out
    untried = {
        "status": "passed",
        "kind": "action_preview",
        "proposed_actions": [],
        "unexercised_actions": ["calendar.create", "gmail.send"],
    }
    assert needs_sample(untried, sources)
    assert needs_sample(
        {"status": "passed", "kind": "read_only_execution", "preview": "Nothing new."}, sources
    )
    assert not needs_sample({**untried, "proposed_actions": [{"operation": "gmail.send"}]}, sources)
    assert not needs_sample({"status": "failed"}, sources)
    assert not needs_sample({**untried, "tested_with": "sample"}, sources)  # once, never again

    written = {
        "gmail": [
            {
                "sender": "Ananya <ananya@novatech.ai>",
                "subject": "Interview",
                "text": "Book via https://novatech.ai/slots or mail hr@novatech.ai",
            }
        ],
        "tasks": [{"title": "not this Crew's source"}],
    }
    sample = safe_sample(written, sources)
    assert list(sample) == ["gmail"]
    mail = sample["gmail"][0]
    assert mail["sample"] is True and mail["id"].startswith("sample-")
    assert "novatech.ai" not in json.dumps(mail)  # every address and link: example.com
    assert "ananya@example.com" in mail["sender"] and "SAMPLE" in mail["labels"]


def test_a_setup_test_that_stopped_on_a_question_is_tried_with_a_sample():
    """A reader that found only holidays asked for calendar access it already had."""
    from mavo.first_run import needs_sample

    asked = {"status": "failed", "questions": ["Can you grant access to your calendar?"]}
    assert needs_sample(asked, ["calendar"])
    assert not needs_sample({"status": "failed", "message": "Allow calendar.create"}, ["calendar"])


def test_a_report_written_as_json_reads_as_plain_lines():
    """ "Latest report" showed a block of JSON."""
    from mavo.tasks import readable

    text = readable(
        '```json [{"sender": "a@b.com", "subject": "Interview", "thread_id": "t1"}] ```'
    )
    assert text == "Sender: a@b.com\nSubject: Interview"  # ids are Mavo's, never shown
    assert readable("A plain report.") == "A plain report."
    assert readable("[]") == "Nothing to report."


def test_a_running_cycle_says_which_step_it_is_on(client, auth):
    """A bare "Working…" left the person unsure whether it was stuck."""
    from mavo.database import CrewInstance, TaskRun

    runtime = client.app.state.crew_runtime
    with runtime.factory() as session:
        session.add(
            CrewInstance(
                id="desk",
                name="Desk",
                template_id="communication",
                responsibilities="Book meetings.",
                permissions=["model:invoke"],
                provider="ollama",
                model="m",
                trigger_type="interval",
            )
        )
        session.add(
            TaskRun(
                id="cycle",
                crew_id="desk",
                title="Cycle",
                request="x",
                status="running",
                plan={
                    "steps": [
                        {"id": "read", "title": "Read Gmail"},
                        {"id": "decide", "title": "Decide"},
                    ]
                },
                steps=[{"id": "read", "status": "done"}, {"id": "decide", "status": "running"}],
            )
        )
        session.commit()
    runs = client.get("/api/v1/crew-runs?crew_id=desk", headers=auth).json()
    assert runs[0]["status"] == "running" and runs[0]["result"] == "Working: step 2 of 2, Decide"


def test_the_agent_list_carries_the_cycle_it_is_working_on(client, auth):
    """The map and its panel show which step runs, and the question a step waits on."""
    from mavo.database import CrewInstance, TaskRun

    runtime = client.app.state.crew_runtime
    with runtime.factory() as session:
        session.add(
            CrewInstance(
                id="live",
                name="Live",
                template_id="communication",
                responsibilities="Book meetings.",
                permissions=["model:invoke"],
                provider="ollama",
                model="m",
                trigger_type="interval",
                status="active",
                is_running=True,
            )
        )
        session.add(
            TaskRun(
                id="now",
                crew_id="live",
                title="Cycle",
                request="x",
                status="running",
                plan={
                    "steps": [
                        {"id": "read", "title": "Read Gmail"},
                        {"id": "decide", "title": "Decide"},
                    ]
                },
                steps=[
                    {"id": "read", "status": "done"},
                    {"id": "decide", "status": "running", "ask": "Which time?"},
                ],
            )
        )
        session.commit()
    live = next(c for c in client.get("/api/v1/crew", headers=auth).json() if c["id"] == "live")
    cycle = live["cycle"]
    assert [s["title"] for s in cycle["steps"]] == ["Read Gmail", "Decide"]
    assert cycle["current"] == 1 and cycle["done"] == 1 and cycle["total"] == 2
    assert cycle["question"] == "Which time?" and cycle["question_step"] == "decide"


def test_email_text_reaches_a_teammate_that_cannot_act_and_never_one_that_sends(client):
    """A slot finder never saw the three times an email offered: the reader left them out of
    its summary. A read-only teammate gets the text; one that sends never does."""
    from mavo.database import TaskRun

    runtime = client.app.state.crew_runtime
    runner = runtime.task_runner
    with runtime.factory() as session:
        session.add(
            TaskRun(
                id="run2",
                title="Meetings",
                request="Book",
                status="running",
                plan={"steps": []},
                steps=[{"id": "reader"}, {"id": "finder"}],
            )
        )
        session.commit()
    mail = {"id": "m1", "sender": "NovaTech <hr@novatech.example>", "subject": "Interview"}
    runner.remember_read(
        "run2",
        "reader",
        {
            "gmail": [mail],
            "message_contents": [{"id": "m1", "body": "Mon 16 Nov 10:00, or Tue 17 Nov 11:00 IST"}],
        },
    )
    finder = runner.run_knowledge("run2", {"gmail", "calendar"}, skip="finder", with_text=True)
    sender = runner.run_knowledge("run2", {"gmail", "calendar"}, skip="finder")
    assert "Tue 17 Nov 11:00" in finder
    assert "Tue 17 Nov 11:00" not in sender and "hr@novatech.example" in sender


def test_run_knowledge_keeps_every_email_when_it_must_be_shortened():
    """The interview email, 8th of 10, was cut off the run knowledge with the newsletters' long
    texts, and the closer never got its sender's address."""
    from mavo.tasks import fitted_json

    mails = [{"id": f"m{i}", "sender": f"s{i}@example.com", "text": "x" * 1500} for i in range(10)]
    value = {"gmail": mails, "calendar": [{"id": "e1", "summary": "Standup"}]}
    out = json.loads(fitted_json(value, 16000))
    assert [m["id"] for m in out["gmail"]] == [m["id"] for m in mails]
    assert out["calendar"] and all(m["text"] for m in out["gmail"])


def test_an_agents_actions_are_handed_on_with_its_words(client, auth):
    """A closer proposed the event and the reply but wrote "Nothing new."; its reviewer saw no
    event and no reply, and the setup test failed every round."""
    from datetime import timedelta

    from mavo.database import ActionRequest, CrewInstance, utcnow

    runtime = setup(client, auth)
    with runtime.factory() as session:
        session.add(
            CrewInstance(
                id="closer",
                name="_agent_closer",
                template_id="schedule",
                responsibilities="Book it.",
                permissions=["model:invoke"],
                provider="ollama",
                model="m",
            )
        )
        session.flush()
        for n, (operation, arguments) in enumerate(
            [
                ("calendar.create", {"summary": "Interview", "start": "2026-11-16T10:00"}),
                ("gmail.send", {"to": "hr@example.com", "body": "Ten works.", "thread_id": "t"}),
                ("gmail.send", {"to": "hr@example.com", "body": "10:00 works.", "thread_id": "t"}),
                ("gmail.send", {"to": "cto@example.com", "body": "See you.", "thread_id": "u"}),
            ]
        ):
            session.add(
                ActionRequest(
                    crew_id="closer",
                    account_subject="local",
                    operation=operation,
                    arguments=arguments,
                    fingerprint=f"closer-{n}",
                    status="previewed",
                    created_at=utcnow() + timedelta(seconds=n),
                )
            )
        session.commit()
    out = runtime.task_runner.with_actions(
        "Nothing new.\n\nBooked.", "closer", utcnow() - timedelta(minutes=1)
    )
    assert not out.startswith("Nothing new") and out.startswith("Booked.")
    assert "Create event" in out and "2026-11-16T10:00" in out
    assert "hr@example.com" in out and "10:00 works." in out and "Proposed" in out
    # The reply proposed again replaces the first one; a reply to someone else stays.
    assert "Ten works." not in out and "cto@example.com" in out
    assert runtime.task_runner.with_actions("Done.", "nobody", utcnow()) == "Done."


def test_a_mail_review_with_an_empty_summary_keeps_its_notes():
    """An inbox reader wrote its note on each email (the interview and its offered times) but
    left summary empty, and the whole setup test failed on it."""
    checkpoint = WorkCheckpoint.model_validate(
        {
            "state": "wait_for_next_review",
            "summary": "",
            "mail_decisions": [
                {"message_id": "a", "important": False, "reason": "News", "summary": ""},
                {
                    "message_id": "b",
                    "important": True,
                    "reason": "Meeting request",
                    "summary": "NovaTech offers Mon 16 Nov 10:00 or 14:00.",
                },
            ],
        }
    )
    assert "Mon 16 Nov 10:00" in checkpoint.summary
    quiet = WorkCheckpoint.model_validate(
        {
            "state": "wait_for_next_review",
            "summary": " ",
            "mail_decisions": [{"message_id": "a", "important": False, "reason": "News"}],
        }
    )
    assert quiet.summary == "Nothing new."


def test_a_team_member_knows_its_own_name():
    """A closer known only as "_agent_<id>" read "Deal Closer will send it" as someone else's job
    and booked nothing."""
    from mavo.crew_engine import crew_system_prompt

    crew = SimpleNamespace(template_id="schedule", name="_agent_1", responsibilities="Book.")
    prompt = crew_system_prompt(crew, worker=True, name="Deal Closer (Books meetings)")
    assert prompt.startswith("You are Deal Closer (Books meetings)") and "_agent_1" not in prompt
    assert crew_system_prompt(crew).startswith("You are _agent_1")


def test_a_step_that_never_ran_is_never_passed_over(client, auth, monkeypatch):
    """Test 03 in the UI: the run ended "done" with its follow-up reminder and summary never
    started, and the person was told only of the draft."""
    import mavo.tasks as tasks_module

    runtime = setup(client, auth)
    scripted(runtime, helper={"writer": "Plan written."})
    real_levels = tasks_module.levels
    monkeypatch.setattr(tasks_module, "levels", lambda steps: real_levels(steps)[:1])
    plan = {
        "title": "Two steps",
        "deliver": "A plan and a summary",
        "final": "first",
        "steps": [
            {
                "id": "first",
                "kind": "helper",
                "role": "writer",
                "title": "Plan",
                "instructions": "Plan.",
            },
            {
                "id": "later",
                "kind": "helper",
                "role": "writer",
                "title": "Summary",
                "instructions": "Sum up.",
                "uses": ["first"],
            },
        ],
    }
    done = wait(client, auth, start(client, auth, plan))
    later = next(s for s in done["steps"] if s["id"] == "later")
    assert later["status"] == "skipped" and "did not run" in later["error"]
    assert "Not completed" in done["result"] and "Summary: It did not run." in done["result"]


def test_a_report_written_by_a_checker_from_several_steps_is_the_answer():
    """Test 21 in the UI: the final report, planned with the critic role, was replaced by the
    raw digest it read first, and shown only as 'Not confirmed by the checker: # Report'."""
    from mavo.task_plan import TaskPlan
    from mavo.tasks import TaskRunner

    def step(i, kind="helper", role=None, uses=()):
        return {
            "id": i,
            "kind": kind,
            "role": role,
            "uses": list(uses),
            "title": i,
            "instructions": "Do it.",
        }

    report = TaskPlan.model_validate(
        {
            "title": "t",
            "deliver": "d",
            "final": "report",
            "steps": [
                step("collect", "agent"),
                step("plan", uses=["collect"]),
                step("make", uses=["plan"]),
                step("report", role="critic", uses=["collect", "plan", "make"]),
            ],
        }
    )
    out = TaskRunner.deliverable(
        report,
        {
            "collect": "Nothing new. Raw digest.",
            "plan": "Plan.",
            "make": "Drafts.",
            "report": "# Consolidated Report\nNothing was created.\n\nVERDICT: revise",
        },
    )
    assert "Consolidated Report" in out and "Raw digest" not in out and "VERDICT" not in out

    checked = TaskPlan.model_validate(
        {
            "title": "t",
            "deliver": "d",
            "final": "check",
            "steps": [
                step("gather", "agent"),
                step("write", role="writer", uses=["gather"]),
                step("check", role="verifier", uses=["write", "gather"]),
            ],
        }
    )
    out = TaskRunner.deliverable(
        checked,
        {
            "gather": "Sources.",
            "write": "The answer.",
            "check": "One date is unsupported.\nVERDICT: issues",
        },
    )
    assert out.startswith("The answer.") and "One date is unsupported." in out


def test_a_helper_told_to_make_a_change_gets_an_action_step():
    """Test 21 in the UI: a helper was told to create tasks "via the tasks.create action", wrote
    seven tasks "for you to copy", and created none."""
    from mavo.recipes import acts_for_named_actions, json_for_actions
    from mavo.task_plan import TaskPlan

    plan = TaskPlan.model_validate(
        {
            "title": "t",
            "deliver": "d",
            "final": "report",
            "steps": [
                {"id": "plan", "kind": "helper", "title": "Plan", "instructions": "Plan the week."},
                {
                    "id": "create",
                    "kind": "helper",
                    "title": "Make tasks",
                    "uses": ["plan"],
                    "instructions": "Create at most 7 tasks in Google Tasks via the tasks.create "
                    "action. Never use gmail.send.",
                },
                {
                    "id": "report",
                    "kind": "helper",
                    "role": "writer",
                    "title": "Report",
                    "uses": ["plan", "create"],
                    "instructions": "Report what was done.",
                },
            ],
        }
    )
    plan = json_for_actions(acts_for_named_actions(plan))
    acts = [s for s in plan.steps if s.kind == "act"]
    assert [a.operation for a in acts] == ["tasks.create"]  # never a send, though it is named
    report = next(s for s in plan.steps if s.id == "report")
    assert acts[0].id in report.uses
    # Asked twice, nothing is added twice.
    assert len([s for s in acts_for_named_actions(plan).steps if s.kind == "act"]) == 1


@pytest.mark.asyncio
async def test_an_agent_is_wrapped_up_by_every_turn_it_took_not_only_the_tasks(
    client, auth, monkeypatch
):
    """Test 05 in the UI: Mavo's scheduler ran a researcher 30 times in 20 minutes; the task
    counted only the turns it started itself, so the researcher was never told to finish, kept
    rewriting its report and held the one browser while another task starved."""
    runner = client.app.state.crew_runtime.task_runner
    states = iter(["going", "going", "going", "answered"])
    wrapped, worked = [], []

    def state(crew_id):
        now = next(states)
        return SimpleNamespace(
            state="waiting",
            soon=now == "going",
            running=True,
            question=None,
            choices=[],
            cut_short=False,
            failure=None,
            error=None,
        )

    async def work(crew_id):
        worked.append(crew_id)

    monkeypatch.setattr(runner, "status_of", lambda run_id: "running")
    monkeypatch.setattr(runner, "crew_state", state)
    monkeypatch.setattr(runner, "turns_since", lambda crew_id, since: 20)  # the scheduler's
    monkeypatch.setattr(runner, "wrap_up", lambda crew_id: wrapped.append(crew_id))
    monkeypatch.setattr(runner, "make_due", lambda crew_id: None)
    monkeypatch.setattr(runner, "last_answer", lambda crew_id, since: "Report.")
    monkeypatch.setattr(runner, "with_actions", lambda answer, crew_id, since: answer)
    monkeypatch.setattr(runner, "began", lambda run_id: None)
    monkeypatch.setattr(runner.runtime, "work", work)
    with pytest.raises(RuntimeError, match="ran out of steps"):
        await runner.drive("run", None, None, "agent", 6)
    assert wrapped == ["agent"]  # told once to answer from what it found, then stopped


def test_a_long_read_given_to_a_step_keeps_every_item():
    """Test 17 in the UI: twenty emails with their texts were cut at 10,000 characters, the
    step saw the first five, and an interview invitation was reported as not in the inbox."""
    from mavo.tasks import TaskRunner

    mails = [
        {"id": f"m{i}", "subject": f"Newsletter {i}", "text": "x" * 2000} for i in range(19)
    ] + [{"id": "m19", "subject": "Interview Invitation", "text": "Offered: Mon 10:00"}]
    block = TaskRunner.inputs_block({"mail": json.dumps({"source": "gmail", "items": mails})})
    shown = json.loads(block.split("\n", 2)[2])
    assert [m["id"] for m in shown["items"]] == [m["id"] for m in mails]
    assert "Interview Invitation" in block


def test_a_restart_during_a_web_search_resumes_on_its_own():
    """Test 20 in the UI: a restart cut off a web search, and the whole task stopped for the
    person to "review interrupted tool work", though a search changes nothing."""
    from mavo.tasks import only_looks

    plan = {
        "steps": [
            {"id": "gather", "kind": "skill", "skill": "web.read"},
            {"id": "read", "kind": "read", "source": "gmail"},
            {"id": "write", "kind": "helper"},
            {"id": "add", "kind": "act", "operation": "tasks.create"},
            {"id": "form", "kind": "skill", "skill": "microsoft_forms.create"},
            {"id": "agent", "kind": "agent"},
        ]
    }
    assert only_looks(plan) == {"gather", "read", "write"}


def test_a_task_agent_is_not_given_a_role_it_could_mistake_for_the_persons():
    """Test 17 in the UI: told it was "a Operator Crew in Mavo", a mail search dismissed the
    real interview invitation as not for the "Operator Crew in Mavo" role."""
    from mavo.crew_engine import crew_system_prompt

    crew = SimpleNamespace(
        template_id="operator",
        name="One-time 1a2b mail",
        responsibilities="Find it.",
        configuration_plan={"task_run_id": "run"},
    )
    prompt = crew_system_prompt(crew, worker=True)
    assert "Operator Crew" not in prompt.split("Purpose:")[0]
    assert "about them, never about you" in prompt


def test_steps_mavo_adds_past_the_planners_eight_still_run(client, auth, monkeypatch):
    """Tests 03 and 21 in the UI: Mavo's own "Details for ..." steps took plans past eight; read
    back when the run started, the plan was cut to eight, and the run ended "done" with its
    reminder, report or action never run."""
    runtime = setup(client, auth)
    scripted(runtime, helper={"writer": "Text.", "extract": '[{"title": "Review"}]'})
    made = []

    def propose(draft, crew_id=None, generation=None, event_key=""):
        made.append(draft.operation)
        return {"id": f"a{len(made)}"}

    async def maybe_execute(action_id):
        return {"id": action_id, "status": "completed", "target_summary": "Review"}

    monkeypatch.setattr(runtime.actions, "propose", propose)
    monkeypatch.setattr(runtime.actions, "maybe_execute", maybe_execute)

    def helper(i, uses=()):
        return {
            "id": i,
            "kind": "helper",
            "role": "writer",
            "title": i,
            "instructions": "Write.",
            "uses": list(uses),
        }

    steps = [helper("h1")] + [helper(f"h{n}", [f"h{n - 1}"]) for n in range(2, 7)]
    steps += [
        {"id": "add", "kind": "act", "operation": "tasks.create", "args_from": "h6"},
        helper("summary", ["add"]),
    ]
    plan = {"title": "Eight", "deliver": "Report", "final": "summary", "steps": steps}
    done = wait(client, auth, start(client, auth, plan))
    assert len(done["steps"]) == 9  # Mavo added the details step for the action
    assert all(s["status"] == "done" for s in done["steps"]), done["steps"]
    assert made == ["tasks.create"]


def test_a_read_step_without_a_source_goes_back_to_the_planner():
    """Test 21 in the UI: four read steps had no source, and the whole task stopped with
    "Mavo cannot read ''" instead of the planner writing them again."""
    from mavo.task_plan import validate_planning_graph

    with pytest.raises(ValueError, match="needs `source`"):
        validate_planning_graph({"steps": [{"id": "r", "kind": "read", "title": "Read mail"}]})
    for source in ("gmail", "Email", "feeds:abc", "memory"):
        validate_planning_graph({"steps": [{"id": "r", "kind": "read", "source": source}]})


def test_actions_that_ran_after_the_report_are_added_to_it():
    """Test 21 in the UI: the report was written before the actions that used its plan, and
    said "Completed actions: none" over five tasks and five events made."""
    from mavo.task_plan import TaskPlan
    from mavo.tasks import acted_after

    plan = TaskPlan.model_validate(
        {
            "title": "t",
            "deliver": "d",
            "final": "report",
            "steps": [
                {
                    "id": "report",
                    "kind": "helper",
                    "role": "writer",
                    "title": "Report",
                    "instructions": "Write.",
                },
                {
                    "id": "args",
                    "kind": "helper",
                    "role": "extract",
                    "title": "Args",
                    "instructions": "Give tasks.",
                    "uses": ["report"],
                },
                {"id": "add", "kind": "act", "operation": "tasks.create", "args_from": "args"},
            ],
        }
    )
    outputs = {
        "report": "Completed actions: none.",
        "args": "[]",
        "add": "Done: Create task (Review)\nDone: Create task (Apply)",
    }
    assert acted_after(plan, outputs) == ["Done: Create task (Review)", "Done: Create task (Apply)"]
    before = TaskPlan.model_validate({**plan.model_dump(), "final": "add"})
    assert acted_after(before, outputs) == []


@pytest.mark.asyncio
async def test_a_correction_keeps_the_time_that_was_chosen(client, auth, monkeypatch):
    """A reply booked the free 2:00 PM. Asked to fix its wording, the corrector moved it to the
    earliest offered 11:00, which the calendar showed busy, and the reply was never sent."""
    runtime = setup(client, auth)
    runner = runtime.task_runner
    seen = {}

    async def chat(provider, model, messages, system, credential_id=None):
        seen["prompt"] = messages[0]["content"]
        return '{"to": "a@example.com", "subject": "Re: Interview", "body": "2:00 PM works."}'

    runtime.router.chat = chat
    monkeypatch.setattr(runner, "spend", lambda run_id, calls: None)
    monkeypatch.setattr(
        runner,
        "route",
        lambda plan, step: SimpleNamespace(provider="ollama", model="m", credential_id=None),
    )
    step = SimpleNamespace(operation="gmail.send")
    fixed = await runner.fix_details(
        "run",
        None,
        step,
        {"to": "a@example.com", "body": "I have booked 2:00 PM."},
        ValueError("It says it booked an event, but none was made."),
        "CALENDAR: 11:00 busy",
    )
    assert fixed["body"] == "2:00 PM works."
    assert "Change ONLY what was refused" in seen["prompt"]
    assert "the chosen date and time" in seen["prompt"]
