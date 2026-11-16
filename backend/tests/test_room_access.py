"""An added agent waiting on access is unblocked from its room, and a question asked there shows
what it is doing as it does it.

An inbox agent sat at "needs setup" ("Allow gmail:read · Allow gmail:body") with nothing in its
room to press; asked "Give me inbox summary", it answered "Nothing new." from 14 emails."""

from types import SimpleNamespace

from mavo.crew_workers import ASKED_NOW
from mavo.database import CrewInstance, TaskRun, WorkspaceSetting
from tests.test_actions import action_env  # noqa: F401
from tests.test_inbox_reader_access import setup_reader


def added_without_access(env):
    """An agent that is on, but was never given the reads its setup asked for."""
    runtime, crew_id = setup_reader(env)
    with runtime.factory() as session:
        crew = session.get(CrewInstance, crew_id)
        crew.status = "active"
        crew.configuration_plan = {
            **crew.configuration_plan,
            "setup_confirmed": True,
            "first_run_required": False,
            "first_run": {"status": "skipped"},
        }
        runtime.planner.reconcile(session, crew)
        session.commit()
        assert crew.runtime_state == "needs_setup"
        assert "Allow gmail:read" in crew.configuration_plan["missing"]
    return runtime, crew_id


def test_allow_gives_only_the_reads_it_asked_for(action_env):
    client, auth, _, _ = action_env
    runtime, crew_id = added_without_access(action_env)
    refused = client.post(
        f"/api/v1/crew/{crew_id}/allow", headers=auth, json={"capabilities": ["gmail:send"]}
    )
    assert refused.status_code == 400  # acting stays in its settings, with its limits
    other = client.post(
        f"/api/v1/crew/{crew_id}/allow", headers=auth, json={"capabilities": ["calendar:read"]}
    )
    assert other.status_code == 400  # never more than its setup asked for
    given = client.post(
        f"/api/v1/crew/{crew_id}/allow",
        headers=auth,
        json={"capabilities": ["gmail:read", "gmail:body"], "notifications": True},
    )
    assert given.status_code == 200, given.text
    crew = given.json()
    assert {"google:gmail:read", "google:gmail:body", "notifications:show"} <= set(
        crew["permissions"]
    )
    missing = crew["configuration_plan"]["missing"]
    assert not any(m.startswith("Allow gmail") for m in missing)
    assert "Allow desktop updates" not in missing


def test_a_question_asked_in_its_room_shows_its_steps_live(action_env):
    client, auth, _, _ = action_env
    runtime, crew_id = added_without_access(action_env)
    with runtime.factory() as session:
        session.add(
            TaskRun(
                id="asked",
                crew_id=None,  # a one-time task: not one of the agent's own runs
                title="Inbox summary",
                request="Give me inbox summary",
                status="running",
                plan={"steps": [{"id": "read", "title": "Read inbox"}]},
                steps=[
                    {
                        "id": "read",
                        "status": "running",
                        "thinking": [{"label": "", "text": "Read 14 emails"}],
                    }
                ],
            )
        )
        session.add(
            WorkspaceSetting(
                key=f"asks:{crew_id}",
                value={
                    "items": [
                        {
                            "id": "q",
                            "question": "Give me inbox summary",
                            "pending": True,
                            "task_id": "asked",
                        }
                    ]
                },
            )
        )
        session.commit()
    crew = next(c for c in client.get("/api/v1/crew", headers=auth).json() if c["id"] == crew_id)
    cycle = crew["cycle"]
    assert cycle["asked"] == "Give me inbox summary"
    assert cycle["steps"] == [{"title": "Read inbox", "status": "running"}]
    assert cycle["thoughts"][-1]["text"] == "Read 14 emails"


def test_progress_lines_land_on_the_step_and_never_break_it():
    from mavo.crew_workers import CrewWorkerRuntime

    seen = []
    pool = SimpleNamespace(
        task_runner=SimpleNamespace(add_thought=lambda run, step, t: seen.append((run, step, t)))
    )
    crew = SimpleNamespace(configuration_plan={"task_run_id": "r", "task_step_id": "s"})
    CrewWorkerRuntime.progress(pool, crew, "Reading your Gmail")
    assert seen == [("r", "s", {"label": "", "text": "Reading your Gmail"})]
    # An agent of its own (no task) has nowhere to show it; a failing runner is ignored.
    CrewWorkerRuntime.progress(pool, SimpleNamespace(configuration_plan={}), "x")
    broken = SimpleNamespace(task_runner=SimpleNamespace(add_thought=lambda *a: 1 / 0))
    CrewWorkerRuntime.progress(broken, crew, "x")
    assert len(seen) == 1


def test_a_request_made_now_is_answered_not_reported_as_nothing_new():
    assert "Never answer only 'Nothing new.'" in ASKED_NOW


def test_an_action_shows_live_in_its_own_words_then_how_it_ended():
    from mavo.crew_workers import CrewWorkerRuntime
    from mavo.google_actions import ActionDraft

    seen = []
    pool = SimpleNamespace(
        task_runner=SimpleNamespace(add_thought=lambda run, step, t: seen.append(t))
    )
    crew = SimpleNamespace(configuration_plan={"task_run_id": "r", "task_step_id": "s"})
    draft = ActionDraft.model_validate(
        {
            "operation": "browser.open",
            "arguments": {"url": "https://airouter.in"},
            "run_text": "Opening the credits page",
        },
        context={"allowed": set()},
    )
    said = CrewWorkerRuntime.action_line(pool, crew, "a1", draft, "running")
    CrewWorkerRuntime.action_line(pool, crew, "a1", draft, "completed", said)
    assert [(t["text"], t["outcome"], t["aid"]) for t in seen] == [
        ("Opening the credits page", "running", "a1"),
        ("Opening the credits page", "done", "a1"),
    ]
    # Without its own words, the action's plain name.
    plain = ActionDraft.model_validate(
        {"operation": "browser.read", "arguments": {}}, context={"allowed": set()}
    )
    assert CrewWorkerRuntime.action_line(pool, crew, "a2", plain, "pending") == "Read page"
    assert seen[-1]["outcome"] == "waiting"
