"""Recipes: an agent's job planned once as tagged steps, replayed every run. Reads, conditions
and actions run as plain code; a model only for think steps. Tags come from what is connected
and allowed, every action goes through the person's permissions, and a recipe that keeps
failing is planned again."""

import json
from types import SimpleNamespace

import pytest

from mavo.recipes import (
    after_run,
    available_tags,
    check,
    compile_recipe,
    parse_json,
    recipe_line,
)
from mavo.task_plan import TaskPlan, TaskStep
from mavo.tasks import condition_met, fill, is_empty
from tests.test_tasks import scripted, setup, start, wait

EVENT = {
    "summary": "Team sync",
    "start": "2026-11-20T10:00:00+05:30",
    "end": "2026-11-20T10:30:00+05:30",
}


def recipe(**extra):
    return {
        "title": "Meetings from mail",
        "deliver": "New meetings added to the calendar",
        "final": "add",
        "steps": [
            {"id": "mail", "kind": "read", "source": "gmail", "new_only": True},
            {
                "id": "find",
                "kind": "helper",
                "role": "extract",
                "title": "Find meetings",
                "instructions": "Reply with ONLY a JSON array of events, or [].",
                "uses": ["mail"],
                "when": {"step": "mail", "is": "not_empty"},
            },
            {"id": "add", "kind": "act", "operation": "calendar.create", "args_from": "find"},
        ],
        **extra,
    }


def tags(read=("gmail",), act=("calendar.create",)):
    return {"read": {r: r for r in read}, "act": {a: a for a in act}, "think": {}}


def test_read_and_act_steps_keep_their_shape_and_conditions_order_the_steps():
    plan = TaskPlan.model_validate(recipe())
    mail, find, add = plan.steps
    assert (mail.kind, mail.source, mail.new_only) == ("read", "gmail", True)
    assert find.kind == "helper" and find.when == {"step": "mail", "is": "not_empty"}
    assert (add.kind, add.operation, add.args_from, add.uses) == (
        "act",
        "calendar.create",
        "find",
        ["find"],
    )
    # A condition on a step it does not read yet: it waits for that step anyway.
    later = TaskStep.model_validate(
        {"id": "x", "kind": "helper", "title": "x", "instructions": "x", "when": {"step": "mail"}}
    )
    assert later.when == {"step": "mail", "is": "not_empty"}
    with pytest.raises(ValueError):
        TaskStep.model_validate({"id": "y", "kind": "act", "operation": "rm.everything"})


def test_conditions_emptiness_and_argument_filling():
    assert is_empty('{"source": "gmail", "count": 0, "items": []}')
    assert is_empty("[]") and is_empty("Nothing new.") and is_empty("")
    assert not is_empty('[{"summary": "x"}]') and not is_empty("Two urgent mails.")
    outputs = {"mail": '{"items": [1]}', "triage": "URGENT: the bank"}
    assert condition_met({"step": "mail", "is": "not_empty"}, outputs)
    assert condition_met({"step": "triage", "has": "urgent"}, outputs)
    assert not condition_met({"step": "missing", "is": "not_empty"}, outputs)
    filled = fill(
        {"body": "{{triage}}", "title": "{{info.title}}"}, {**outputs, "info": '{"title": "T"}'}
    )
    assert filled == {"body": "URGENT: the bank", "title": "T"}


def test_a_recipe_uses_only_the_tags_it_was_given():
    good = TaskPlan.model_validate(recipe())
    assert check(good, tags()) is good
    with pytest.raises(ValueError, match="not available"):
        check(good, tags(act=()))  # calendar writes not given: no calendar.create tag
    with pytest.raises(ValueError, match="not available"):
        check(good, tags(read=("calendar",)))
    assert recipe_line(good.model_dump()) == "Read Gmail → Extract → Create event"


def test_tags_come_from_what_is_connected_and_turned_on(client, auth):
    runtime = client.app.state.crew_runtime
    found = available_tags(runtime)
    assert not any(t.startswith(("gmail", "calendar", "tasks")) for t in found["act"])
    assert "gmail" not in found["read"]  # Google is not connected
    assert "terminal.run" not in found["act"]  # not turned on in Actions
    assert {"files", "memory"} <= set(found["read"]) and "summarise" in found["think"]
    turned_on = client.put(
        "/api/v1/action-policies",
        headers=auth,
        json={"operation": "terminal.run", "rule": {"enabled": True, "mode": "unrestricted"}},
    )
    assert turned_on.status_code == 200, turned_on.text
    assert "terminal.run" in available_tags(runtime)["act"]


@pytest.mark.asyncio
async def test_planning_once_checks_the_recipe_and_repairs_one_bad_answer():
    runtime = SimpleNamespace(
        google_pool=None, connectors=None, actions=SimpleNamespace(rules=None), factory=None
    )
    replies = [
        json.dumps(
            {
                **recipe(),
                "steps": [
                    {
                        "id": "a",
                        "kind": "act",
                        "operation": "gmail.trash",
                        "args": {"message_id": "x"},
                    }
                ],
            }
        ),
        json.dumps(recipe()),
    ]
    asked = []

    async def chat(messages):
        asked.append(messages)
        return replies.pop(0)

    crew = SimpleNamespace(
        name="Meetings",
        responsibilities="Add meetings from my mail to my calendar",
        permissions=[],
        trigger_type="gmail_message",
        schedule_time=None,
        review_interval_minutes=30,
        id="c1",
    )
    from unittest.mock import patch

    with patch("mavo.recipes.available_tags", return_value=tags()):
        plan = await compile_recipe(runtime, crew, chat)
    assert [s.kind for s in plan.steps] == ["read", "helper", "act"]
    assert len(asked) == 2 and "does not check out" in asked[1][-1]["content"]
    assert "gmail.trash" in asked[1][-1]["content"]


def test_parse_json_takes_the_object_from_fences_or_prose():
    assert parse_json('```json\n[{"a": 1}]\n```') == [{"a": 1}]
    assert parse_json('Here it is: {"a": 2} hope that helps') == {"a": 2}
    with pytest.raises(ValueError):
        parse_json("no json here")


def run_recipe(client, auth, monkeypatch, mail_items, extract_reply):
    runtime = setup(client, auth)
    seen = scripted(runtime, helper={"extract": extract_reply})
    runner = runtime.task_runner
    monkeypatch.setattr(runner, "google_readable", lambda: {"gmail", "calendar"})

    async def read(resource):
        return {"resource": resource, "items": mail_items}

    monkeypatch.setattr(runtime.google_pool, "read", read)
    proposed, executed = [], []

    def propose(draft, crew_id=None, generation=None, event_key=""):
        proposed.append((draft.operation, draft.arguments))
        return {"id": f"a{len(proposed)}"}

    async def maybe_execute(action_id):
        executed.append(action_id)
        return {"id": action_id, "status": "completed", "target_summary": "Team sync"}

    monkeypatch.setattr(runtime.actions, "propose", propose)
    monkeypatch.setattr(runtime.actions, "maybe_execute", maybe_execute)
    done = wait(client, auth, start(client, auth, recipe()))
    return done, seen, proposed, executed


def test_a_run_reads_thinks_once_and_acts_through_permissions(client, auth, monkeypatch):
    mail = [{"id": "m1", "subject": "Team sync Thursday 10am", "from": "boss@example.com"}]
    done, seen, proposed, executed = run_recipe(
        client, auth, monkeypatch, mail, json.dumps([EVENT])
    )
    assert done["status"] == "done", done
    assert len(seen["helper_prompts"]) == 1  # one model call: the think step
    assert proposed == [("calendar.create", {**EVENT})] or proposed[0][0] == "calendar.create"
    assert executed == ["a1"]
    assert "Done: Create event (Team sync)" in done["result"]
    read_step = next(s for s in done["steps"] if s["id"] == "mail")
    assert read_step["seen_ids"] == ["m1"]


def test_a_run_with_nothing_new_skips_the_rest_and_calls_no_model(client, auth, monkeypatch):
    done, seen, proposed, executed = run_recipe(client, auth, monkeypatch, [], "[]")
    assert done["status"] == "done" and done["result"] == "Nothing new."
    assert seen["helper_prompts"] == [] and proposed == [] and executed == []
    statuses = {s["id"]: s["status"] for s in done["steps"]}
    assert statuses == {"mail": "done", "find": "skipped", "add": "skipped"}


def test_a_think_step_that_finds_nothing_to_do_acts_on_nothing(client, auth, monkeypatch):
    mail = [{"id": "m2", "subject": "Newsletter"}]
    done, seen, proposed, _ = run_recipe(client, auth, monkeypatch, mail, "[]")
    assert done["status"] == "done" and len(seen["helper_prompts"]) == 1 and proposed == []


def test_a_finished_run_remembers_what_it_saw_and_failures_lead_to_a_new_plan():
    crew = SimpleNamespace(observation={}, configuration_plan={"recipe": recipe()})
    run = SimpleNamespace(
        plan={"trigger_context": {"recipe": True}},
        status="done",
        steps=[{"id": "mail", "seen_ids": ["m1", "m2"]}, {"id": "find"}],
    )
    after_run(crew, run)
    assert crew.observation["recipe_seen"] == {"mail": ["m1", "m2"]}
    failed = SimpleNamespace(plan=run.plan, status="failed", steps=[])
    after_run(crew, failed)
    assert "recipe" in crew.configuration_plan and crew.observation["recipe_failures"] == 1
    after_run(crew, failed)
    assert "recipe" not in crew.configuration_plan  # planned again at the next run
    assert crew.configuration_plan["recipe_replanned"] == 1
    plain = SimpleNamespace(plan={}, status="failed", steps=[])
    before = dict(crew.observation)
    after_run(crew, plain)  # not a recipe run: nothing changes
    assert crew.observation == before


def test_a_recurring_agent_is_planned_once_and_then_replays_its_recipe(client, auth):
    from datetime import timedelta

    from sqlalchemy import select

    from mavo.crew_composition import dispatch, set_composition
    from mavo.database import CrewInstance, ModelRoute, TaskRun, utcnow
    from tests.test_crew_v2 import composition, spec

    runtime = setup(client, auth)
    runtime.recipes = True
    client.post("/api/v1/memory", headers=auth, json={"text": "My college is City College"})
    planned = {
        "title": "Weekly profile digest",
        "deliver": "A short digest of my profile",
        "final": "sum",
        "steps": [
            {"id": "facts", "kind": "read", "source": "memory"},
            {
                "id": "sum",
                "kind": "helper",
                "role": "summarise",
                "title": "Summarise",
                "instructions": "Summarise the facts.",
                "uses": ["facts"],
                "when": {"step": "facts", "is": "not_empty"},
            },
        ],
    }
    calls = {"plan": 0, "think": 0}

    async def chat(provider, model, messages, system, credential_id=None):
        if "RECIPE" in messages[0]["content"]:
            calls["plan"] += 1
            return json.dumps(planned)
        calls["think"] += 1
        return "You study at City College."

    runtime.router.chat = chat
    with runtime.factory() as session:
        crew = CrewInstance(
            name="Profile digest",
            template_id="research",
            responsibilities="Every week, summarise what Mavo knows about me",
            route_id=session.scalar(select(ModelRoute.id)),
            provider="ollama",
            model="m",
            status="active",
            is_running=True,
            permissions=["model:invoke"],
            trigger_type="interval",
        )
        session.add(crew)
        session.flush()
        set_composition(session, crew, composition(spec("digest")))
        crew_id = crew.id
        session.commit()
    for cycle in range(2):
        assert client.portal.call(dispatch, runtime, crew_id)
        with runtime.factory() as session:
            run_id = session.scalars(
                select(TaskRun.id)
                .where(TaskRun.crew_id == crew_id)
                .order_by(TaskRun.created_at.desc())
            ).first()
        run = wait(client, auth, run_id)
        assert run["status"] == "done", run
        assert run["result"] == "You study at City College."
        assert [s["kind"] for s in run["steps"]] == ["read", "helper"]
        with runtime.factory() as session:
            crew = session.get(CrewInstance, crew_id)
            assert crew.configuration_plan["recipe"]["title"] == "Weekly profile digest"
            crew.next_run_at = utcnow() + timedelta(minutes=cycle + 1)  # the next cycle
            session.commit()
    assert calls == {"plan": 1, "think": 2}  # planned once, one think call per run
    shown = client.get(f"/api/v1/crew/{crew_id}/recipe", headers=auth).json()
    assert shown["line"] == "Read Memory → Summarise" and shown["enabled"]
    again = client.post(f"/api/v1/crew/{crew_id}/recipe/replan", headers=auth).json()
    assert again["recipe"] is None  # planned afresh at the next run


def test_what_a_planner_forgets_is_put_right_in_code():
    """From real plans by the person's models: a task step reading a prose plan, an unasked
    notes.write, an agent given clicking for a job that only reads, and new mail with no gate."""
    from mavo.recipes import tighten

    evening = TaskPlan.model_validate(
        {
            "title": "Evening plan",
            "deliver": "Tomorrow's plan",
            "final": "add",
            "steps": [
                {"id": "cal", "kind": "read", "source": "calendar"},
                {
                    "id": "plan",
                    "kind": "helper",
                    "role": "write",
                    "title": "Plan",
                    "instructions": "Plan tomorrow.",
                    "uses": ["cal"],
                },
                {"id": "add", "kind": "act", "operation": "tasks.create", "args_from": "plan"},
            ],
        }
    )
    tight = tighten(evening, "Plan tomorrow from my calendar and add missing prep tasks")
    ids = [s.id for s in tight.steps]
    assert ids == ["cal", "plan", "add_args", "add"]
    assert tight.steps[3].args_from == "add_args" and tight.final == "plan"
    assert "JSON array" in tight.steps[2].instructions

    digest = TaskPlan.model_validate(
        {
            "title": "Mail digest",
            "deliver": "A digest",
            "final": "note",
            "steps": [
                {"id": "mail", "kind": "read", "source": "gmail", "new_only": True},
                {
                    "id": "sum",
                    "kind": "helper",
                    "role": "summarise",
                    "title": "Sum",
                    "instructions": "Summarise.",
                    "uses": ["mail"],
                },
                {
                    "id": "note",
                    "kind": "act",
                    "operation": "notes.write",
                    "args": {"path": "x.md", "text": "{{sum}}"},
                    "uses": ["sum"],
                },
                {
                    "id": "web",
                    "kind": "agent",
                    "title": "Look",
                    "instructions": "Look it up.",
                    "actions": ["browser.search", "browser.click", "browser.fill"],
                },
            ],
        }
    )
    tight = tighten(digest, "Every morning, summarise my important unread mail")
    assert [s.id for s in tight.steps] == ["mail", "sum", "web"]  # the unasked note is gone
    assert tight.final == "web" or tight.final == "sum"
    assert tight.steps[1].when == {"step": "mail", "is": "not_empty"}  # no new mail: no call
    assert set(tight.steps[2].actions) == {"browser.search", "browser.open", "browser.read"}


def test_a_recipe_is_refused_when_an_action_takes_raw_data_or_wrong_arguments():
    raw = TaskPlan.model_validate(
        {
            "title": "t",
            "deliver": "d",
            "steps": [
                {"id": "facts", "kind": "read", "source": "gmail"},
                {"id": "open", "kind": "act", "operation": "calendar.create", "args_from": "facts"},
            ],
        }
    )
    with pytest.raises(ValueError, match="THINK step"):
        check(raw, tags())
    linux = TaskPlan.model_validate(
        {
            "title": "t",
            "deliver": "d",
            "steps": [
                {
                    "id": "disk",
                    "kind": "act",
                    "operation": "terminal.run",
                    "args": {"command": "df -h"},
                },
            ],
        }
    )
    with pytest.raises(ValueError, match="not valid.*it takes folder"):
        check(linux, tags(act=("terminal.run",)))


def test_a_crews_report_shows_what_was_done_never_the_actions_json(client, auth, monkeypatch):
    """A crew's latest report showed four replies as raw JSON above "Done: Create event"."""
    runtime = setup(client, auth)
    scripted(runtime, helper={"extract": json.dumps([EVENT])})
    runner = runtime.task_runner
    monkeypatch.setattr(runner, "google_readable", lambda: {"gmail", "calendar"})

    async def read(resource):
        return {"items": [{"id": "m1", "subject": "Team sync Thursday 10am"}]}

    monkeypatch.setattr(runtime.google_pool, "read", read)
    monkeypatch.setattr(runtime.actions, "propose", lambda draft, *a, **k: {"id": "a1"})

    async def maybe_execute(action_id):
        return {"id": action_id, "status": "completed", "target_summary": "Team sync"}

    monkeypatch.setattr(runtime.actions, "maybe_execute", maybe_execute)
    plan = recipe(final="find", trigger_context={"recipe": True})
    done = wait(client, auth, start(client, auth, plan))
    assert done["result"].startswith("Done: Create event (Team sync)"), done["result"]
    assert "{" not in done["result"] and '"summary"' not in done["result"]


def test_a_read_tag_written_with_its_description_is_that_tag():
    """A planner wrote the catalog line as the tag ("gmail = new mail in the inbox (sender,
    subject, snippet)"), and the crew could not be planned at all."""
    plan = TaskPlan.model_validate(recipe())
    plan.steps[0].source = "gmail = new mail in the inbox (sender, subject, snippet)"
    checked = check(plan, tags())
    assert checked.steps[0].source == "gmail"
    plan.steps[0].source = "outlook"
    with pytest.raises(ValueError, match="use one of: gmail"):
        check(plan, tags())


def test_an_agent_that_sends_or_books_runs_the_agents_its_test_passed_never_a_recipe():
    """A recipe planned after the test replaced a tested mail crew: no calendar step, no
    reviewer, the sender's address lost, nothing sent. One that changes things keeps its agents,
    and a recipe stored for it before is not used."""
    from mavo.recipes import eligible

    def crew(**plan):
        return SimpleNamespace(
            hidden=False,
            status="active",
            is_running=True,
            trigger_type="event",
            template_id="research",
            configuration_plan={"composition": {"agents": []}, **plan},
        )

    assert eligible(crew(changes_things=False))
    assert not eligible(crew(changes_things=True, recipe={"steps": []}))
    # planned before the flag existed: its agents' actions say it
    acting = {"agents": [{"key": "b", "actions": ["calendar.create", "gmail.send"]}]}
    reading = {"agents": [{"key": "r", "actions": ["helper.ask"]}]}
    assert not eligible(crew(composition=acting))
    assert eligible(crew(composition=reading))
