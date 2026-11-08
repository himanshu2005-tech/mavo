# ruff: noqa: E501, F811
"""One-time tasks: a plan of steps done by think-only helpers, short-lived agents that use the
person's own default Actions, and their existing Crew, with the result put on the desktop by Mavo."""

import asyncio
import json
import re
import time

import pytest
from pydantic import ValidationError

from mavo.database import CrewInstance, TaskRun
from mavo.schemas import CoordinatorDecision
from mavo.task_plan import TaskPlan, levels
from tests.test_planner import planning, turn  # noqa: F401
from tests.test_scenarios import decide


def step(id, kind="helper", **extra):
    base = {"id": id, "kind": kind, "title": f"Step {id}", "instructions": f"Do {id}"}
    if kind == "agent":
        base |= {"actions": ["browser.open", "browser.read"], "sites": ["forms.example.com"]}
    return base | extra


def plan(*steps, **extra):
    return {"title": "Make a form", "deliver": "The link to the form", "steps": list(steps)} | extra


# ---- the plan ------------------------------------------------------------------------------


def test_a_plan_is_checked_and_small_slips_are_put_right_not_refused():
    ok = TaskPlan.model_validate(plan(step("write"), step("check", uses=["write"], role="critic")))
    assert ok.final == "check" and levels(ok.steps) == [["write"], ["check"]]
    independent = TaskPlan.model_validate(plan(step("a"), step("b"), step("c", uses=["a", "b"])))
    assert levels(independent.steps) == [["a", "b"], ["c"]]

    def fixed(*steps, **extra):
        return TaskPlan.model_validate(plan(*steps, **extra))

    # What a model gets slightly wrong is corrected, because the intent is clear.
    assert [s.id for s in fixed(step("a"), step("a")).steps] == ["a", "a_2"]  # a repeated id
    assert fixed(step("a", uses=["missing", "a"])).steps[0].uses == []  # reads nothing that exists
    assert [s.uses for s in fixed(step("a", uses=["b"]), step("b", uses=["a"])).steps] == [
        [],
        ["a"],
    ]  # a loop: in order
    assert fixed(step("a"), final="nope").final == "a"  # the last step is the result
    assert [s.id for s in fixed(*[step(f"s{n}") for n in range(12)]).steps] == [
        f"s{n}" for n in range(8)
    ]
    assert (
        fixed(step("a"), model_calls=999).model_calls == 40
        and fixed(step("a"), model_calls=0).model_calls == 2
    )
    odd = fixed(step("Make It!", extra_field="ignored", calls=99)).steps[0]
    assert odd.id == "make_it" and odd.calls == 20
    # An agent with nothing to do only thinks; one that names a site gets the browser; a helper
    # that asks for tools is an agent; unknown actions are dropped; a crew step without a Crew thinks.
    assert fixed(step("a", "agent", actions=[], sites=[])).steps[0].kind == "helper"
    assert fixed(step("a", "agent", actions=[])).steps[0].actions == [
        "browser.open",
        "browser.read",
    ]
    assert fixed(step("a", "helper", actions=["browser.open"])).steps[0].kind == "agent"
    assert fixed(step("a", "agent", actions=["nonsense.run", "browser.read"])).steps[0].actions == [
        "browser.read"
    ]
    assert fixed(step("a", "crew")).steps[0].kind == "helper"
    sites = (
        fixed(
            step(
                "a",
                "agent",
                sites=["https://Forms.Office.com/x?y=1", "not a site", "user@host.com:8080"],
            )
        )
        .steps[0]
        .sites
    )
    assert sites == ["forms.office.com", "host.com"]
    # What has no sensible reading is refused.
    for bad in (
        {"title": "x", "deliver": "y", "steps": []},
        {"title": "x", "deliver": "y"},
        "nonsense",
        {"steps": "no"},
    ):
        with pytest.raises(ValidationError):
            TaskPlan.model_validate(bad)


# ---- running -------------------------------------------------------------------------------


def start(client, auth, body):
    made = client.post("/api/v1/tasks", headers=auth, json={"plan": body, "request": "do it"})
    assert made.status_code == 201, made.text
    return made.json()["id"]


def wait(client, auth, run_id, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        run = client.get(f"/api/v1/tasks/{run_id}", headers=auth).json()
        if run["status"] != "running":
            return run
        time.sleep(0.15)
    raise AssertionError("the task is still running: " + json.dumps(run["steps"]))


def setup(client, auth):
    client.post(
        "/api/v1/routes", headers=auth, json={"name": "A", "provider": "ollama", "model": "m"}
    ).raise_for_status()
    return client.app.state.crew_runtime


def scripted(runtime, helper=None, crew=None, pause=0.0):
    """A model that answers helpers by role and Crew steps from a queue; records what it saw."""
    seen = {"helper_prompts": [], "active": 0, "peak": 0, "crew_prompts": []}
    queue = list(crew or [])

    async def chat(provider, model, messages, system, credential_id=None):
        if "one-time helper agent" in system:
            seen["active"] += 1
            seen["peak"] = max(seen["peak"], seen["active"])
            seen["helper_prompts"].append((system, messages[0]["content"]))
            await asyncio.sleep(pause)
            seen["active"] -= 1
            role = re.search(r"YOUR ROLE: (\w+)", messages[0]["content"]).group(1)
            return (helper or {}).get(role, f"{role} done")
        seen["crew_prompts"].append(messages[0]["content"])
        return json.dumps(queue.pop(0))

    runtime.router.chat = chat
    return seen


def allow_browser(client, auth, mode="unrestricted", domains=()):
    for operation in ("browser.open", "browser.read", "browser.navigate"):
        rule = {"enabled": True, "mode": mode, "domains": list(domains)}
        assert (
            client.put(
                "/api/v1/action-policies", headers=auth, json={"operation": operation, "rule": rule}
            ).status_code
            == 200
        )


def alerts(client, auth):
    return client.get("/api/v1/reminders/due", headers=auth).json()


def test_a_one_time_agent_reads_the_persons_own_google_data_read_only(client, auth):
    """ "Plan my day" gave the gathering to a helper, which sees nothing: the plan was empty slots.
    Now the step is an agent given read access to what the Google connection allows."""
    from types import SimpleNamespace

    from mavo.database import CrewInstance
    from mavo.task_plan import TaskPlan

    runtime = setup(client, auth)
    runner = runtime.task_runner
    plan = TaskPlan.model_validate(
        {
            "title": "Plan my day",
            "deliver": "A plan",
            "steps": [
                {
                    "id": "gather",
                    "kind": "helper",
                    "title": "Gather",
                    "instructions": "Read my day",
                    "google": ["calendar", "Tasks", "mail", "photos"],
                },
            ],
        }
    )
    step = plan.steps[0]
    assert step.kind == "agent" and step.google == ["calendar", "tasks", "gmail"]
    # Not connected: it says what to connect, instead of guessing a day.
    runtime.google_pool = None
    assert (
        "Connect Google in Resources and allow reading Calendar, Tasks, Gmail"
        in runner.access_problems(step)
    )
    # Connected, with calendar and tasks allowed: exactly those, read-only.
    runtime.google_pool = SimpleNamespace(
        auth=SimpleNamespace(
            status=lambda: {
                "signed_in": True,
                "capabilities": ["calendar:read", "tasks:read", "calendar:write"],
            }
        )
    )
    assert "Gmail" in runner.access_problems(step) and "Calendar" not in runner.access_problems(
        step
    )
    route = runner.route()
    crew_id = runner.make_agent("run12345", plan, step, {}, route, 4)
    with runtime.factory() as session:
        crew = session.get(CrewInstance, crew_id)
        granted = [p for p in crew.permissions if p.startswith("google:")]
        assert granted == ["google:calendar:read", "google:tasks:read"]
        assert "given to you below in your observation" in crew.responsibilities
        assert "never stop for a nice-to-have" in crew.responsibilities
    # Weather off in Resources: the agent is told to leave it out, not to ask where they live.
    note = asyncio.run(runner.local_weather())
    assert note.startswith("No weather: Weather is off") and "do not ask" in note


def test_helpers_work_in_order_the_critic_reads_the_draft_and_the_result_reaches_the_desktop(
    client, auth
):
    runtime = setup(client, auth)
    seen = scripted(
        runtime,
        helper={
            "writer": "Draft: the form has Name and Email.",
            "critic": "Looks right. VERDICT: pass",
        },
    )
    run_id = start(
        client,
        auth,
        plan(
            step("write", role="writer"),
            step("check", role="critic", uses=["write"]),
            final="check",
        ),
    )
    done = wait(client, auth, run_id)
    assert (
        done["status"] == "done" and done["result"] == "Draft: the form has Name and Email."
    )  # the work, not the checker's remark
    assert [s["status"] for s in done["steps"]] == ["done", "done"] and done["used_calls"] == 2
    system, prompt = seen["helper_prompts"][1]
    assert "Draft: the form has Name and Email." in prompt and "untrusted" in prompt
    assert (
        "strict critic" in system and "no tools" in system
    )  # a critic knows its role, and its limits
    # Mavo itself puts the result on the desktop, under the task's own name.
    shown = alerts(client, auth)
    assert [(a["crew_name"], a["message"], a["kind"]) for a in shown] == [
        ("Make a form", "Draft: the form has Name and Email.", "crew_update")
    ]
    # Its owner is not one of the person's Crew.
    assert client.get("/api/v1/crew", headers=auth).json() == []


def test_steps_that_do_not_need_each_other_run_together(client, auth):
    runtime = setup(client, auth)
    seen = scripted(runtime, pause=0.3)
    run_id = start(
        client,
        auth,
        plan(
            step("a"),
            step("b"),
            step("c"),
            step("join", uses=["a", "b", "c"]),
            final="join",
            model_calls=10,
        ),
    )
    assert wait(client, auth, run_id)["status"] == "done"
    assert seen["peak"] == 3  # the three independent helpers overlapped; the join waited for them
    assert "[a]" in seen["helper_prompts"][-1][1] and "[c]" in seen["helper_prompts"][-1][1]


def test_a_task_never_spends_more_than_the_budget_it_chose(client, auth):
    runtime = setup(client, auth)
    scripted(runtime)
    run_id = start(
        client,
        auth,
        plan(
            step("a"),
            step("b", uses=["a"]),
            step("c", uses=["b"]),
            step("d", uses=["c"]),
            model_calls=2,
        ),
    )
    done = wait(client, auth, run_id)
    assert done["status"] == "failed" and done["used_calls"] == 2
    assert [s["status"] for s in done["steps"]] == ["done", "done", "failed", "skipped"]
    assert "budget" in done["steps"][2]["error"]
    assert "budget" in done["error"]


def test_a_step_that_fails_stops_only_what_depends_on_it(client, auth):
    runtime = setup(client, auth)

    async def chat(provider, model, messages, system, credential_id=None):
        if "YOUR ROLE: bad" in messages[0]["content"]:
            raise RuntimeError("the model is down")
        return "fine"

    runtime.router.chat = chat
    run_id = start(
        client,
        auth,
        plan(
            step("bad", role="bad"),
            step("good"),
            step("after", uses=["bad"]),
            step("last", uses=["good"]),
            final="last",
        ),
    )
    done = wait(client, auth, run_id)
    assert (
        done["status"] == "done" and done["result"] == "fine"
    )  # the final step did not need the failed one
    assert [s["status"] for s in done["steps"]] == ["failed", "done", "skipped", "done"]
    assert "model is down" in done["steps"][0]["error"]


def test_a_task_can_be_stopped(client, auth):
    runtime = setup(client, auth)
    scripted(runtime, pause=2)
    run_id = start(client, auth, plan(step("slow"), step("next", uses=["slow"])))
    time.sleep(0.4)
    assert client.post(f"/api/v1/tasks/{run_id}/stop", headers=auth).json()["status"] == "stopped"
    assert client.post(f"/api/v1/tasks/{run_id}/stop", headers=auth).status_code == 409
    time.sleep(2.5)
    assert client.get(f"/api/v1/tasks/{run_id}", headers=auth).json()["status"] == "stopped"
    assert alerts(client, auth) == []


# ---- agents that use tools, once -------------------------------------------------------------


def fake_site(runtime, seen=None):
    async def browser(operation, args, rule, owner, scope_guard=None, limit=None):
        if seen is not None:
            seen.append((operation, args, owner))
        base = {"url": "https://forms.example.com/", "title": "Forms"}
        return (
            {
                **base,
                "view": "PAGE https://forms.example.com/ - Forms. Your form link: https://forms.example.com/r/abc123",
            }
            if operation == "browser.read"
            else base
        )

    runtime.actions.desktop.browser = browser


OPEN_READ = {
    "state": "continue",
    "summary": "Opening it.",
    "next_step": "Read",
    "actions": [
        {"operation": "browser.open", "arguments": {"url": "https://forms.example.com"}},
        {"operation": "browser.read", "arguments": {}},
    ],
}
ANSWER = {
    "state": "wait_for_next_review",
    "summary": "https://forms.example.com/r/abc123\n\nAction browser.read: completed",
    "next_step": "Done",
}


def test_an_agent_uses_the_persons_default_actions_once_and_is_deleted(client, auth):
    runtime = setup(client, auth)
    allow_browser(client, auth)
    visits = []
    fake_site(runtime, visits)
    scripted(runtime, crew=[OPEN_READ, ANSWER])
    run_id = start(client, auth, plan(step("make", "agent"), model_calls=8))
    done = wait(client, auth, run_id)
    assert done["status"] == "done" and done["result"] == "https://forms.example.com/r/abc123", done
    assert [v[0] for v in visits] == ["browser.open", "browser.read"]
    # It ran as a hidden one-time Crew, named for the task, not as one of the person's own.
    assert all(v[2] != "core" for v in visits)
    assert 2 <= done["used_calls"] <= 8
    assert [a["message"] for a in alerts(client, auth)] == ["https://forms.example.com/r/abc123"]
    with runtime.factory() as session:  # the agent is gone; only the alert owner remains, hidden
        hidden = [c.name for c in session.query(CrewInstance)]
        assert hidden == ["Mavo tasks"]
        assert session.query(CrewInstance).one().hidden is True


def test_an_agent_that_runs_out_of_steps_has_not_finished_and_its_last_words_are_not_the_answer(
    client, auth
):
    """It kept reading the same page and ran out of steps. Reporting its last progress note as the
    result ("Reading the page again") told the person a task was done when nothing was."""
    runtime = setup(client, auth)
    allow_browser(client, auth)
    fake_site(runtime)
    scripted(runtime, crew=[{**OPEN_READ, "summary": "Reading the page again."}] * 6)
    done = wait(client, auth, start(client, auth, plan(step("make", "agent", calls=2))))
    assert done["status"] == "failed" and done["result"] is None, done
    assert done["steps"][0]["status"] == "failed"
    assert "ran out of steps" in done["error"] and "Reading the page again" in done["error"]
    assert alerts(client, auth)[0]["message"].startswith("Could not finish.")


def test_an_agent_that_builds_things_is_told_to_chain_actions_and_one_that_reads_is_not():
    from mavo.task_plan import TaskPlan
    from mavo.tasks import TaskRunner

    runner = TaskRunner.__new__(TaskRunner)
    body = TaskPlan.model_validate(
        plan(
            step("make", "agent", actions=["browser.open", "browser.click", "browser.fill"]),
            step("look", "agent", actions=["browser.open", "browser.read"]),
        )
    )
    building, reading = (runner.agent_brief(body, s, {}, 8) for s in body.steps)
    assert "chain several actions in ONE step" in building and "click-to-edit" in building
    assert "chain several actions" not in reading
    assert building.rstrip().endswith("in question.") and len(building) <= 3900


def test_an_agent_cannot_have_more_than_the_person_turned_on_and_is_told_what_to_turn_on(
    client, auth
):
    runtime = setup(client, auth)
    fake_site(runtime)
    scripted(runtime)
    # Nothing is on in the defaults.
    done = wait(client, auth, start(client, auth, plan(step("make", "agent"))))
    assert done["status"] == "needs_you" and done["steps"][0]["status"] == "blocked"
    assert "Turn on in Actions (Defaults): Open page, Read page" in done["steps"][0]["question"]
    assert "Set all" in done["steps"][0]["question"]
    assert alerts(client, auth)[0]["message"].startswith("Needs you.")
    with runtime.factory() as session:
        assert (
            session.query(CrewInstance).filter(CrewInstance.name != "Mavo tasks").count() == 0
        )  # none was ever made
    # On, but only for other sites.
    allow_browser(client, auth, mode="restricted", domains=["elsewhere.example.org"])
    blocked = wait(client, auth, start(client, auth, plan(step("make", "agent"))))
    assert (
        blocked["status"] == "needs_you"
        and "Allow these sites for Browser: forms.example.com" in blocked["steps"][0]["question"]
    )
    # Allowed, with the sign-in hosts of Microsoft added by Mavo itself.
    allow_browser(client, auth, mode="restricted", domains=["forms.office.com"])
    scripted(runtime, crew=[ANSWER])
    ok = wait(
        client,
        auth,
        start(
            client,
            auth,
            plan(step("make", "agent", sites=["forms.office.com", "login.microsoftonline.com"])),
        ),
    )
    assert ok["status"] == "done"


def test_an_agent_that_needs_the_person_stops_the_task_and_says_why(client, auth):
    runtime = setup(client, auth)
    allow_browser(client, auth)
    fake_site(runtime)
    scripted(
        runtime,
        crew=[
            {
                "state": "waiting_for_input",
                "summary": "Stopped at the sign-in.",
                "question": "forms.example.com needs you to sign in. Save a login with the key button, then run it again.",
            }
        ],
    )
    done = wait(client, auth, start(client, auth, plan(step("make", "agent"))))
    assert done["status"] == "needs_you" and "needs you to sign in" in done["error"]
    assert "sign in" in alerts(client, auth)[0]["message"]


def test_an_existing_crew_can_be_handed_a_step(client, auth):
    runtime = setup(client, auth)
    route = client.get("/api/v1/routes", headers=auth).json()[0]
    crew = client.post(
        "/api/v1/crew",
        headers=auth,
        json={
            "template_id": "operator",
            "name": "Researcher",
            "route_id": route["id"],
            "responsibilities": "Research things",
            "trigger_type": "manual",
        },
    ).json()
    seen = scripted(
        runtime,
        crew=[{"state": "wait_for_next_review", "summary": "Three findings.", "next_step": ""}],
    )
    done = wait(
        client,
        auth,
        start(
            client,
            auth,
            plan(
                step("ask", "crew", crew_id=crew["id"], instructions="Find the three best options")
            ),
        ),
    )
    assert done["status"] == "done" and done["result"] == "Three findings."
    assert "Find the three best options" in seen["crew_prompts"][0]
    # The Crew is still there and still the person's own.
    assert [c["name"] for c in client.get("/api/v1/crew", headers=auth).json()] == ["Researcher"]


def test_a_step_naming_a_crew_that_is_gone_fails_cleanly(client, auth):
    setup(client, auth)
    done = wait(client, auth, start(client, auth, plan(step("ask", "crew", crew_id="not-a-crew"))))
    assert done["status"] == "failed" and "no longer exists" in done["error"]


# ---- housekeeping --------------------------------------------------------------------------


def test_one_time_agents_are_never_listed_offered_or_given_access_by_name(client, auth):
    runtime = setup(client, auth)
    allow_browser(client, auth)
    plan_ = TaskPlan.model_validate(plan(step("make", "agent")))
    route = runtime.task_runner.route()
    crew_id = runtime.task_runner.make_agent("r" * 36, plan_, plan_.steps[0], {}, route, 3)
    assert client.get("/api/v1/crew", headers=auth).json() == []
    assert client.get("/api/v1/crew-drafts", headers=auth).json() == []
    assert client.get("/api/v1/action-policies/browser.open/crew", headers=auth).json() == []
    with runtime.factory() as session:
        crew = session.get(CrewInstance, crew_id)
        assert (
            crew.hidden and crew.is_running and "action:browser.open" in crew.permissions
        )  # a copy of the defaults
        assert crew.trigger_type == "manual" and crew.max_model_calls == 3
        assert (
            "ONE-TIME TASK" in crew.responsibilities
            and "The link to the form" in crew.responsibilities
        )


def test_after_mavo_closed_a_running_task_is_marked_and_its_agents_are_cleared(client, auth):
    runtime = setup(client, auth)
    allow_browser(client, auth)
    plan_ = TaskPlan.model_validate(plan(step("make", "agent")))
    runtime.task_runner.make_agent(
        "r" * 36, plan_, plan_.steps[0], {}, runtime.task_runner.route(), 3
    )
    with runtime.factory() as session:
        session.add(
            TaskRun(
                title="Half done",
                plan=plan_.model_dump(mode="json"),
                status="running",
                steps=[{"id": "make", "title": "x", "kind": "agent", "status": "running"}],
            )
        )
        session.commit()
    runtime.task_runner.recover()
    with runtime.factory() as session:
        run = session.query(TaskRun).one()
        assert (
            run.status == "failed"
            and "Mavo closed" in run.error
            and run.steps[0]["status"] == "failed"
        )
        assert session.query(CrewInstance).count() == 0


def test_listing_and_a_missing_task(client, auth):
    runtime = setup(client, auth)
    scripted(runtime)
    first = start(client, auth, plan(step("a")))
    wait(client, auth, first)
    listed = client.get("/api/v1/tasks", headers=auth).json()
    assert [t["id"] for t in listed] == [first] and listed[0]["title"] == "Make a form"
    assert client.get("/api/v1/tasks/nope", headers=auth).status_code == 404
    assert client.post("/api/v1/tasks", headers=auth, json={"plan": plan()}).status_code == 422


# ---- from the chat -------------------------------------------------------------------------


def test_the_chat_starts_a_task_at_once_and_a_repeated_request_does_not_start_another(planning):
    from uuid import uuid4

    client, auth, runtime, coordinator = planning
    scripted(runtime, helper={"writer": "https://forms.example.com/r/abc123"})
    client.post(
        "/api/v1/routes", headers=auth, json={"name": "A", "provider": "ollama", "model": "m"}
    ).raise_for_status()
    # The chat is told what the user's default Actions allow, so it can plan within it.
    allow_browser(client, auth)
    decide(
        planning,
        reply="Started. The link will appear on your desktop.",
        task=plan(step("a", role="writer"), deliver="The form link"),
    )
    request_id = str(uuid4())
    first = turn(
        planning, "Create a form with name and email and tell me the link", request_id=request_id
    )
    assert (
        first["task_run_id"]
        and "desktop" in first["reply"]
        and first["crew_id"] is None
        and first["draft_id"] is None
    )
    assert {"browser.open", "browser.read"} <= set(coordinator.calls[0].context.enabled_defaults)
    done = wait(client, auth, first["task_run_id"])
    assert done["status"] == "done" and done["result"] == "https://forms.example.com/r/abc123"
    # It belongs to the conversation it was asked in, so the chat can show it there.
    chat_id = first["conversation"]["id"]
    assert done["conversation_id"] == chat_id
    assert [
        t["id"]
        for t in client.get(
            "/api/v1/tasks", headers=auth, params={"conversation_id": chat_id}
        ).json()
    ] == [first["task_run_id"]]
    assert (
        client.get("/api/v1/tasks", headers=auth, params={"conversation_id": "other"}).json() == []
    )
    assert [a["message"] for a in alerts(client, auth)] == ["https://forms.example.com/r/abc123"]
    # Sending the same message again returns the same answer, not a second task.
    again = turn(
        planning, "Create a form with name and email and tell me the link", request_id=request_id
    )
    assert again["task_run_id"] == first["task_run_id"]
    assert len(client.get("/api/v1/tasks", headers=auth).json()) == 1
    # No Crew was made, and the task shows in the conversation like any answer.
    assert (
        client.get("/api/v1/crew", headers=auth).json() == []
        and client.get("/api/v1/crew-drafts", headers=auth).json() == []
    )


def test_a_task_started_while_asking_for_a_detail_is_held_and_the_question_asked(planning):
    """The chat said it had started a fare search and, in the same reply, asked for the departure
    city. The task ran without it, searched broadly and ran out of steps."""
    client, auth, runtime, _ = planning
    scripted(runtime)
    client.post(
        "/api/v1/routes", headers=auth, json={"name": "A", "provider": "ollama", "model": "m"}
    ).raise_for_status()
    decide(
        planning,
        reply="I've started a web search for the cheapest train fares to Chennai; the result "
        "will appear on your desktop. Tell me your departure city and I'll narrow it down.",
        task=plan(step("a", role="writer"), deliver="The fares"),
    )
    held = turn(planning, "Search the web for the cheapest train fares to Chennai")
    assert not held.get("task_run_id") and client.get("/api/v1/tasks", headers=auth).json() == []
    assert held["reply"] == "Tell me your departure city and I'll narrow it down."
    from mavo.crew_planner import held_for_a_detail

    assert held_for_a_detail("Started. The link will appear on your desktop.") is None
    assert held_for_a_detail("Started. Let me know if you want another date.") is None
    assert held_for_a_detail("Started it. From which city?") == "From which city?"


def test_a_task_that_names_a_crew_the_user_does_not_have_is_refused_and_one_that_mixes_jobs_is_invalid(
    planning,
):
    client, auth, runtime, _ = planning
    scripted(runtime)
    decide(planning, task=plan(step("ask", "crew", crew_id="invented-crew")))
    refused = client.post(
        "/api/v1/core/turn",
        headers=auth,
        json={"message": "ask my researcher", "request_id": str(__import__("uuid").uuid4())},
    )
    assert refused.status_code == 409 and "does not exist" in refused.text
    assert client.get("/api/v1/tasks", headers=auth).json() == []  # nothing was started
    with pytest.raises(ValidationError, match="cannot also"):
        CoordinatorDecision.model_validate(
            {"reply": "x", "task": plan(step("a")), "questions": ["Which one?"]}
        )
    with pytest.raises(ValidationError, match="cannot also"):
        CoordinatorDecision.model_validate(
            {"reply": "x", "task": plan(step("a")), "delegate_to": "abc", "instruction": "do"}
        )


def test_the_assistant_is_taught_when_to_run_a_task_instead_of_making_a_crew(client, auth):
    from mavo.coordinator_contract import PlannerContext, coordinator_prompt

    text = coordinator_prompt(
        PlannerContext(local_time="2026-11-04T10:00:00+05:30", enabled_defaults=["browser.open"])
    )
    assert (
        "ONE-TIME TASKS" in text
        and "INSTEAD of crew_proposal" in text
        and "enabled_defaults" in text
    )
    assert '"task"' in text and "TaskPlan" in text  # the response format includes it


# ---- a Crew asks a helper to check its work -----------------------------------------------


def a_crew_with_helpers(client, auth, runtime):
    route = client.get("/api/v1/routes", headers=auth).json()[0]
    crew = client.post(
        "/api/v1/crew",
        headers=auth,
        json={
            "template_id": "operator",
            "name": "Balance watcher",
            "route_id": route["id"],
            "responsibilities": "Report the balance",
            "trigger_type": "manual",
            "max_model_calls": 6,
        },
    ).json()
    rule = {"enabled": True, "mode": "unrestricted"}
    client.put(
        "/api/v1/action-policies",
        headers=auth,
        json={"operation": "helper.ask", "crew_id": crew["id"], "rule": rule},
    ).raise_for_status()
    client.post(f"/api/v1/crew/{crew['id']}/start", headers=auth).raise_for_status()
    return crew["id"]


def work_once(client, runtime, crew_id):
    from datetime import UTC, datetime, timedelta

    with runtime.factory() as session:
        crew = session.get(CrewInstance, crew_id)
        crew.next_run_at, crew.runtime_state = datetime.now(UTC) - timedelta(seconds=5), "waiting"
        session.commit()
    client.portal.call(runtime.work, crew_id)


ASK = {
    "operation": "helper.ask",
    "arguments": {
        "role": "verifier",
        "instructions": "Check the number against the page text",
        "input": "Balance: $0.66. Page text: Current balance $0.66 USD.",
    },
}


ASK_AGAIN = {
    "operation": "helper.ask",
    "arguments": {**ASK["arguments"], "input": "A second claim to check"},
}


def test_a_crew_can_ask_a_think_only_helper_to_check_its_work_within_its_own_budget(client, auth):
    from mavo.database import ActionRequest, CrewRun

    runtime = setup(client, auth)
    crew_id = a_crew_with_helpers(client, auth, runtime)
    seen = scripted(
        runtime,
        helper={"verifier": "The number matches the page. VERDICT: verified"},
        crew=[
            {
                "state": "continue",
                "summary": "Draft: $0.66.",
                "next_step": "Check it",
                "actions": [ASK, ASK_AGAIN],
            },
            {
                "state": "wait_for_next_review",
                "summary": "Balance is $0.66 (checked).",
                "next_step": "",
            },
        ],
    )
    work_once(client, runtime, crew_id)
    system, prompt = seen["helper_prompts"][0]
    assert (
        "no tools" in system
        and "verifier" in system.lower()
        and "Current balance $0.66 USD" in prompt
    )
    with runtime.factory() as session:
        asked = [
            a
            for a in session.query(ActionRequest).order_by(ActionRequest.created_at)
            if a.operation == "helper.ask"
        ]
        assert [a.status for a in asked] == [
            "completed",
            "failed",
        ]  # one helper per step; the second is refused plainly
        assert (
            asked[0].result["answer"].endswith("VERDICT: verified")
            and "No model calls are left" in asked[1].error
        )
        assert (
            session.query(CrewRun).one().model_calls == 2
        )  # its own call and the helper's, charged to it
    assert len(seen["helper_prompts"]) == 1
    # Its next step sees what the helper said, and finishes.
    work_once(client, runtime, crew_id)
    assert "VERDICT: verified" in seen["crew_prompts"][1]
    assert client.get(f"/api/v1/crew/{crew_id}", headers=auth).status_code in (200, 405, 404)


def test_a_crew_without_the_helper_permission_cannot_ask_one_and_the_rule_is_in_its_instructions_only_when_granted(
    client, auth
):
    from mavo.crew_workers import HELPER_GUIDE

    runtime = setup(client, auth)
    route = client.get("/api/v1/routes", headers=auth).json()[0]
    plain = client.post(
        "/api/v1/crew",
        headers=auth,
        json={
            "template_id": "operator",
            "name": "No helpers",
            "route_id": route["id"],
            "responsibilities": "Report",
            "trigger_type": "manual",
        },
    ).json()
    client.put(
        "/api/v1/action-policies",
        headers=auth,
        json={
            "operation": "browser.open",
            "crew_id": plain["id"],
            "rule": {"enabled": True, "mode": "unrestricted"},
        },
    ).raise_for_status()
    client.post(f"/api/v1/crew/{plain['id']}/start", headers=auth).raise_for_status()
    systems = []

    async def chat(provider, model, messages, system, credential_id=None):
        systems.append(system)
        return json.dumps({"state": "wait_for_next_review", "summary": "ok", "next_step": ""})

    runtime.router.chat = chat
    work_once(client, runtime, plain["id"])
    assert "HELPERS:" not in systems[0]
    granted = a_crew_with_helpers(client, auth, runtime)
    work_once(client, runtime, granted)
    assert HELPER_GUIDE.strip() in systems[1]
    # And a request for it without the permission is refused at once, not run.
    from mavo.google_actions import ActionDraft, ActionPolicyError

    with runtime.factory() as session:
        generation = session.get(CrewInstance, plain["id"]).runtime_generation
    with pytest.raises(ActionPolicyError, match="disabled|not allowed|access"):
        runtime.actions.propose(
            ActionDraft(operation="helper.ask", arguments=ASK["arguments"]), plain["id"], generation
        )


def test_a_step_waits_for_a_browser_another_job_is_using_instead_of_giving_up(client, auth):
    """Two agent steps once ran together: the second was told "browser busy, try again" and the
    runner took that for "finished without an answer"."""
    runtime = setup(client, auth)
    allow_browser(client, auth)
    fake_site(runtime)
    scripted(runtime, crew=[ANSWER])
    runtime.actions.desktop.browser_owner = "another-job"
    started = start(client, auth, plan(step("make", "agent"), model_calls=6))
    time.sleep(2.2)  # parked for the browser: not failed, not finished
    assert client.get(f"/api/v1/tasks/{started}", headers=auth).json()["status"] == "running"
    runtime.actions.desktop.browser_owner = None
    done = wait(client, auth, started)
    assert done["status"] == "done" and done["result"] == "https://forms.example.com/r/abc123", done


def test_agent_steps_that_use_the_browser_take_turns(client, auth):
    runtime = setup(client, auth)
    allow_browser(client, auth)
    fake_site(runtime)
    scripted(runtime, crew=[ANSWER, ANSWER])
    done = wait(
        client,
        auth,
        start(
            client,
            auth,
            plan(step("read", "agent"), step("verify", "agent"), final="verify", model_calls=10),
        ),
    )
    assert done["status"] == "done", done
    assert [s["status"] for s in done["steps"]] == ["done", "done"]


# ---- the answer is the work, not the checker's remark --------------------------------------


def test_a_checker_is_never_the_answer_and_its_verdict_is_added_only_when_it_finds_a_problem(
    client, auth
):
    runtime = setup(client, auth)
    scripted(
        runtime,
        helper={
            "writer": "The latest version is 3.15.2.",
            "verifier": "The input supports it. VERDICT: verified",
        },
    )
    clean = wait(
        client,
        auth,
        start(
            client,
            auth,
            plan(
                step("work", role="writer"),
                step("check", role="verifier", uses=["work"]),
                final="check",
            ),
        ),
    )
    assert clean["result"] == "The latest version is 3.15.2."  # nothing but what was asked for

    critique = "The date is not in the input.\nVERDICT: revise"
    scripted(runtime, helper={"writer": "The latest version is 3.15.2.", "critic": critique})
    flagged = wait(
        client,
        auth,
        start(
            client,
            auth,
            plan(
                step("work", role="writer"),
                step("check", role="critic", uses=["work"]),
                final="check",
            ),
        ),
    )
    assert (
        flagged["result"]
        == "The latest version is 3.15.2.\n\nNot confirmed by the checker: The date is not in the input."
    )
    assert alerts(client, auth)[-1]["message"] == flagged["result"]

    # A checker that checks a checker, or one whose work failed, gives no answer of its own.
    async def chat(provider, model, messages, system, credential_id=None):
        if "YOUR ROLE: writer" in messages[0]["content"]:
            raise RuntimeError("down")
        return "VERDICT: verified"

    runtime.router.chat = chat
    failed = wait(
        client,
        auth,
        start(
            client,
            auth,
            plan(
                step("work", role="writer"),
                step("check", role="verifier", uses=["work"]),
                final="check",
            ),
        ),
    )
    assert failed["status"] == "failed" and failed["result"] is None


def test_an_agent_is_told_how_many_steps_it_has_and_to_finish_by_the_last(client, auth):
    runtime = setup(client, auth)
    plan_ = TaskPlan.model_validate(plan(step("make", "agent")))
    brief = runtime.task_runner.agent_brief(plan_, plan_.steps[0], {}, 5)
    assert (
        "You have 5 steps in all" in brief
        and "by your last step" in brief
        and plan_.steps[0].calls == 6
    )


def test_a_helper_is_told_to_judge_only_against_what_it_is_given(client, auth):
    from mavo.helpers import system_for

    text = system_for("verifier")
    assert (
        "never contradict it from memory" in text
        and "only against it" in text
        and "cannot confirm from the input" in text
    )


def test_the_person_gets_the_answer_and_the_checker_gets_the_evidence(client, auth):
    from mavo.tasks import answer_of

    assert answer_of("3.15.2\n\nEVIDENCE:\nThe page lists Python 3.15.2 first.") == "3.15.2"
    assert answer_of("3.15.2\n**Evidence:** line 4") == "3.15.2"
    assert answer_of("Only an answer.") == "Only an answer." and answer_of("") == ""
    runtime = setup(client, auth)
    seen = scripted(
        runtime,
        helper={
            "writer": "3.15.2\nEVIDENCE:\nThe page says 3.15.2 is newest.",
            "verifier": "Supported by the input. VERDICT: verified",
        },
    )
    done = wait(
        client,
        auth,
        start(
            client,
            auth,
            plan(
                step("work", role="writer"),
                step("check", role="verifier", uses=["work"]),
                final="check",
            ),
        ),
    )
    assert done["result"] == "3.15.2"  # just what was asked for
    assert (
        "The page says 3.15.2 is newest." in seen["helper_prompts"][1][1]
    )  # the checker saw the evidence
    assert (
        "The page says 3.15.2 is newest." in done["steps"][0]["output"]
    )  # and it is kept with the step


def test_a_result_reads_as_plain_text_on_the_desktop():
    from mavo.tasks import plain

    assert (
        plain("**Latest Stable Python 3 Version:** 3.15.2")
        == "Latest Stable Python 3 Version: 3.15.2"
    )
    assert (
        plain("Open `forms.office.com` now") == "Open forms.office.com now"
        and plain("  x  ") == "x"
        and plain("") == ""
    )


def on_page(runtime, url, wall=None):
    """The browser, as Mavo finds it when an agent says it is stopped: on this address."""
    from types import SimpleNamespace

    async def sign_in_wall():
        return wall

    desktop = runtime.actions.desktop
    desktop.page = SimpleNamespace(url=url, is_closed=lambda: False)
    desktop.sign_in_wall = sign_in_wall


CLAIM = {
    "state": "waiting_for_input",
    "summary": "Blocked: sign-in required.",
    "question": "Microsoft redirected to its login page, so a sign-in is required.",
}


def test_an_agent_that_claims_a_sign_in_but_is_on_an_ordinary_page_is_told_once_and_carries_on(
    client, auth
):
    """After clicking the account on "Pick an account" it was on the signed-in Forms page, yet it
    reported the first screen it had seen and stopped. Mavo looks at the page itself."""
    runtime = setup(client, auth)
    allow_browser(client, auth)
    fake_site(runtime)
    on_page(runtime, "https://forms.example.com/home")
    seen = scripted(runtime, crew=[CLAIM, ANSWER])
    done = wait(client, auth, start(client, auth, plan(step("make", "agent"))))
    assert done["status"] == "done" and done["result"] == "https://forms.example.com/r/abc123", done
    assert "is not a sign-in page" in seen["crew_prompts"][1]  # it was told what Mavo saw
    assert "https://forms.example.com/home" in seen["crew_prompts"][1]


def test_an_agent_that_is_really_on_a_sign_in_page_is_believed(client, auth):
    runtime = setup(client, auth)
    allow_browser(client, auth)
    fake_site(runtime)
    # Each of these is a sign-in as far as Mavo can tell: the claim stands and the person is asked.
    for url, wall in (
        ("https://forms.example.com/home", {"kind": "login", "host": "forms.example.com"}),
        ("https://login.microsoftonline.com/common/oauth2/v2.0/authorize?x=1", None),
        ("https://accounts.example.com/pick", None),
    ):
        on_page(runtime, url, wall)
        scripted(runtime, crew=[CLAIM])
        done = wait(client, auth, start(client, auth, plan(step("make", "agent"))))
        assert done["status"] == "needs_you", (url, done)
    # And an agent that insists after being told is believed the second time: no endless loop.
    on_page(runtime, "https://forms.example.com/home")
    scripted(runtime, crew=[CLAIM, CLAIM])
    done = wait(client, auth, start(client, auth, plan(step("make", "agent"))))
    assert done["status"] == "needs_you"


def test_a_task_stopped_by_a_sign_in_says_how_to_sign_in(client, auth):
    runtime = setup(client, auth)
    allow_browser(client, auth)
    fake_site(runtime)
    scripted(
        runtime,
        crew=[
            {
                "state": "waiting_for_input",
                "summary": "Reached the sign-in page.",
                "question": "A Microsoft sign-in page appeared, so I could not continue.",
            }
        ],
    )
    done = wait(client, auth, start(client, auth, plan(step("make", "agent"))))
    assert done["status"] == "needs_you"
    assert "Microsoft sign-in page appeared" in done["error"]
    # It says where to sign in and what to press afterwards, and the card carries the address.
    assert "Sign in at https://" in done["error"] and "press Try again" in done["error"]
    site = done["sign_in_url"]
    assert site.startswith("https://") and site[len("https://") :] in done["error"]
    # Anything else the agent needs is passed on as it was said.
    from mavo.tasks import TaskRunner

    assert (
        TaskRunner.with_sign_in_help("Which of the two forms do you mean?")
        == "Which of the two forms do you mean?"
    )


def test_try_again_runs_the_same_plan_after_the_person_has_put_things_right(client, auth):
    runtime = setup(client, auth)
    fake_site(runtime)
    scripted(runtime)
    # Stopped because nothing is on yet: no sign-in address, and the message says what to press.
    blocked = wait(client, auth, start(client, auth, plan(step("make", "agent"))))
    assert blocked["status"] == "needs_you" and blocked["sign_in_url"] is None
    assert "press Try again" in blocked["error"]
    # The person turns the actions on, signs in (the window is still open), and presses Try again.
    allow_browser(client, auth)
    scripted(runtime, crew=[OPEN_READ, ANSWER])
    desktop = runtime.actions.desktop
    closed = []

    async def close_window():
        closed.append(True)

    desktop.user_window_open = lambda: True
    desktop.close_user_window = close_window
    again = client.post(f"/api/v1/tasks/{blocked['id']}/retry", headers=auth)
    assert again.status_code == 201 and again.json()["id"] != blocked["id"]
    assert closed == [True]  # the sign-in window is closed first, which saves its session
    done = wait(client, auth, again.json()["id"])
    assert done["status"] == "done" and done["result"] == "https://forms.example.com/r/abc123"
    # It is the same task: same plan, same request, nothing planned again.
    with runtime.factory() as session:
        first, second = session.get(TaskRun, blocked["id"]), session.get(TaskRun, done["id"])
        assert first.plan == second.plan and first.request == second.request
    assert (
        client.get(f"/api/v1/tasks/{blocked['id']}", headers=auth).json()["status"] == "needs_you"
    )
    assert client.post("/api/v1/tasks/nope/retry", headers=auth).status_code == 409


# ---- an agent that asks the person something ---------------------------------------------------

QUESTION = {
    "state": "waiting_for_input",
    "summary": "Need one detail.",
    "question": "Which name should the form carry?",
}


def asked(client, auth, run_id, timeout=30):
    """The run and the step in it that is waiting for the person, as soon as there is one."""
    end = time.time() + timeout
    while time.time() < end:
        run = client.get(f"/api/v1/tasks/{run_id}", headers=auth).json()
        for one in run["steps"]:
            if one.get("ask"):
                return run, one
        assert run["status"] == "running", run  # it ended without asking
        time.sleep(0.1)
    raise AssertionError("nothing asked the person")


def test_an_agent_that_asks_a_question_is_answered_on_the_task_card_and_carries_on(client, auth):
    runtime = setup(client, auth)
    allow_browser(client, auth)
    fake_site(runtime)
    seen = scripted(runtime, crew=[QUESTION, ANSWER])
    run_id = start(client, auth, plan(step("make", "agent")))
    run, one = asked(client, auth, run_id)
    # The task is still running, waiting on the card, and nothing went on without the answer.
    assert run["status"] == "running" and one["status"] == "running" and one["asked_at"]
    assert one["ask"] == QUESTION["question"] and len(seen["crew_prompts"]) == 1
    assert any("Needs your answer" in a["message"] for a in alerts(client, auth))
    sent = client.post(
        f"/api/v1/tasks/{run_id}/answer",
        headers=auth,
        json={"answer": "  Student form  ", "step_id": "make"},
    )
    assert sent.status_code == 202
    done = wait(client, auth, run_id)
    assert done["status"] == "done" and done["result"] == "https://forms.example.com/r/abc123", done
    assert "Student form" in seen["crew_prompts"][1]  # the same agent was given the answer
    assert not done["steps"][0].get("ask") and not runtime.task_runner.asking


def test_an_answer_with_nothing_asking_or_nothing_in_it_is_refused(client, auth):
    setup(client, auth)
    nobody = client.post("/api/v1/tasks/nope/answer", headers=auth, json={"answer": "x"})
    assert nobody.status_code == 409
    empty = client.post("/api/v1/tasks/nope/answer", headers=auth, json={"answer": "   "})
    assert empty.status_code == 422


def test_a_password_or_code_is_not_asked_for_in_the_text_box_nor_is_what_a_text_box_cannot_fix(
    client, auth
):
    runtime = setup(client, auth)
    allow_browser(client, auth)
    fake_site(runtime)
    for text, in_the_browser in (
        ("What is your Microsoft password?", True),
        ("Please enter the verification code sent to your phone.", True),
        (
            "forms.example.com is asking for a human check. Open Resources, Browser and do it.",
            False,
        ),
    ):
        scripted(runtime, crew=[{**QUESTION, "question": text}])
        done = wait(client, auth, start(client, auth, plan(step("make", "agent"))))
        assert done["status"] == "needs_you" and text in done["error"], done
        assert not any(s.get("ask") for s in done["steps"])
        assert ("never sent to the agent's model" in done["error"]) is in_the_browser
        assert (done["sign_in_url"] is not None) is in_the_browser


def test_a_question_nobody_answers_stops_the_task_and_can_be_tried_again(client, auth, monkeypatch):
    runtime = setup(client, auth)
    allow_browser(client, auth)
    fake_site(runtime)
    monkeypatch.setattr("mavo.tasks.APPROVAL_WAIT", 0.5)
    scripted(runtime, crew=[QUESTION])
    done = wait(client, auth, start(client, auth, plan(step("make", "agent"))))
    assert done["status"] == "needs_you" and "Nobody answered" in done["error"], done
    assert QUESTION["question"] in done["error"] and not runtime.task_runner.asking
    assert done["sign_in_url"] is None  # it is a question, not a sign-in


def test_a_task_stopped_while_it_waits_for_an_answer_stops(client, auth):
    runtime = setup(client, auth)
    allow_browser(client, auth)
    fake_site(runtime)
    scripted(runtime, crew=[QUESTION])
    run_id = start(client, auth, plan(step("make", "agent")))
    asked(client, auth, run_id)
    assert client.post(f"/api/v1/tasks/{run_id}/stop", headers=auth).status_code == 200
    stopped = wait(client, auth, run_id)
    assert stopped["status"] == "stopped"
    deadline = time.time() + 5
    while runtime.task_runner.asking and time.time() < deadline:
        time.sleep(0.1)
    assert not runtime.task_runner.asking
    late = client.post(f"/api/v1/tasks/{run_id}/answer", headers=auth, json={"answer": "x"})
    assert late.status_code == 409
