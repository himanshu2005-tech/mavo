# ruff: noqa: E501, F811
"""Saved automations: an approved script run, kept to run again with no model, bound to the exact
scripts the person read."""

import time
from datetime import timedelta

from mavo.database import ActionRequest, utcnow
from tests.test_projects import HELLO, do, finished, prepare
from tests.test_tasks import alerts, plan, setup, start, step, wait

SAVE = {"name": "Say hello", "every_minutes": 60, "notify": "always"}


def approve_and_save(client, auth, tmp_path, script=HELLO, **save):
    """The person approves a script run and chooses to keep it as an automation."""
    asked = do(
        client,
        auth,
        "project.run",
        project="drone",
        tool="python",
        script="hello.py",
        args=[str(time.time())],
    ).json()
    assert asked["status"] == "pending", asked
    waiting = next(
        w
        for w in client.get("/api/v1/projects/approvals", headers=auth).json()
        if w["id"] == asked["id"]
    )
    body = {"sha256": waiting["sha256"], "automation": {**SAVE, **save}}
    assert client.post(
        f"/api/v1/actions/{asked['id']}/approve", headers=auth, json=body
    ).json() == {"status": "approved"}
    return finished(client, auth, asked["id"])


def test_saving_at_approval_then_running_it_again_with_no_model(client, auth, tmp_path):
    runtime = setup(client, auth)
    prepare(client, auth, tmp_path)
    assert client.get("/api/v1/automations", headers=auth).json() == []
    first = approve_and_save(client, auth, tmp_path)
    assert first["status"] == "completed" and first["approved_by"] == "user"
    saved = client.get("/api/v1/automations", headers=auth).json()
    assert [
        (a["name"], a["every_minutes"], a["notify"], a["script"], a["last_status"]) for a in saved
    ] == [("Say hello", 60, "always", "hello.py", None)]
    (tmp_path / "drone" / "result.txt").unlink()  # prove the next run is a real one
    ran = client.post("/api/v1/automations/Say hello/run", headers=auth).json()
    assert ran["status"] == "ok" and "hello from drone" in ran["output"] and ran["exit_code"] == 0
    assert (tmp_path / "drone" / "result.txt").read_text() == "ok"
    now = client.get("/api/v1/automations", headers=auth).json()[0]
    assert (
        now["last_status"] == "ok"
        and "hello from drone" in now["last_output"]
        and now["last_run_at"]
    )
    # It went through the action pipeline, so it is in Activity, and used no model at all.
    history = [
        a
        for a in client.get("/api/v1/actions", headers=auth).json()
        if a["operation"] == "project.run"
    ]
    assert len(history) == 2 and all(
        a["status"] == "completed" and a["approved_by"] == "user" for a in history
    )
    assert [a["message"] for a in alerts(client, auth)][-1].startswith(
        "hello from drone"
    )  # "always": told every run
    assert runtime.router.chat is not None


def test_scripts_that_changed_since_the_person_read_them_do_not_run_and_it_says_so(
    client, auth, tmp_path
):
    prepare(client, auth, tmp_path)
    approve_and_save(client, auth, tmp_path)
    (tmp_path / "drone" / "result.txt").unlink()
    do(
        client,
        auth,
        "project.write",
        project="drone",
        path="hello.py",
        content="open('result.txt','w').write('CHANGED')\n",
    )
    ran = client.post("/api/v1/automations/Say hello/run", headers=auth).json()
    assert ran["status"] == "changed" and "approve it to save it again" in ran["problem"]
    assert not (tmp_path / "drone" / "result.txt").exists()  # the changed script never ran
    assert client.get("/api/v1/automations", headers=auth).json()[0]["last_status"] == "changed"
    assert any("changed" in a["message"] for a in alerts(client, auth))
    # Reading and approving the new scripts saves it again; then it runs.
    do(client, auth, "project.write", project="drone", path="hello.py", content=HELLO)
    approve_and_save(client, auth, tmp_path)
    assert client.post("/api/v1/automations/Say hello/run", headers=auth).json()["status"] == "ok"
    assert (
        len(client.get("/api/v1/automations", headers=auth).json()) == 1
    )  # the same name replaced itself


def test_it_does_not_run_while_actions_are_paused_or_the_script_tool_is_off(client, auth, tmp_path):
    prepare(client, auth, tmp_path)
    approve_and_save(client, auth, tmp_path)
    client.post("/api/v1/actions/pause", headers=auth)
    paused = client.post("/api/v1/automations/Say hello/run", headers=auth).json()
    assert paused["status"] == "blocked" and "paused" in paused["problem"]
    client.post("/api/v1/actions/resume", headers=auth)
    client.put(
        "/api/v1/action-policies",
        headers=auth,
        json={
            "operation": "project.run",
            "rule": {"enabled": False, "mode": "restricted", "apps": ["python.exe"]},
        },
    )
    off = client.post("/api/v1/automations/Say hello/run", headers=auth).json()
    assert off["status"] == "blocked" and "Turn on Run a script" in off["problem"]
    assert client.post("/api/v1/automations/Nothing/run", headers=auth).status_code == 404


def test_it_tells_the_person_about_a_failure_by_default_and_stays_quiet_when_it_works(
    client, auth, tmp_path
):
    prepare(client, auth, tmp_path, script="print('fine')\n")
    approve_and_save(client, auth, tmp_path, name="Quiet", notify="failure")
    assert client.post("/api/v1/automations/Quiet/run", headers=auth).json()["status"] == "ok"
    assert alerts(client, auth) == []
    do(
        client,
        auth,
        "project.write",
        project="drone",
        path="hello.py",
        content="raise SystemExit(2)\n",
    )
    approve_and_save(client, auth, tmp_path, name="Quiet", notify="failure")
    failed = client.post("/api/v1/automations/Quiet/run", headers=auth).json()
    assert failed["status"] == "failed" and failed["exit_code"] == 2
    assert [a["message"] for a in alerts(client, auth)][-1].startswith("Failed.")
    approve_and_save(client, auth, tmp_path, name="Never", notify="never")
    before = len(alerts(client, auth))
    client.post("/api/v1/automations/Never/run", headers=auth)
    assert len(alerts(client, auth)) == before


def test_scheduled_automations_run_when_due_and_not_again_until_their_time(client, auth, tmp_path):
    runtime = setup(client, auth)
    prepare(client, auth, tmp_path, script="print('tick')\n")
    approve_and_save(client, auth, tmp_path, name="Hourly", every_minutes=60, notify="never")
    approve_and_save(client, auth, tmp_path, name="Manual only", every_minutes=None, notify="never")
    automations = runtime.actions.automations
    assert automations.due() == ["Hourly"]  # never run yet; the other is not scheduled
    client.portal.call(automations.tick)
    ran = {a["name"]: a for a in client.get("/api/v1/automations", headers=auth).json()}
    assert ran["Hourly"]["last_status"] == "ok" and ran["Manual only"]["last_status"] is None
    assert automations.due() == []
    assert automations.due(utcnow() + timedelta(minutes=61)) == ["Hourly"]
    automations.last_check = 0
    client.portal.call(automations.tick)  # not due again: no second run
    with runtime.factory() as session:
        assert (
            sum(
                1
                for a in session.query(ActionRequest)
                if a.operation == "project.run"
                and a.approved_by == "user"
                and a.status == "completed"
            )
            == 3
        )  # two approvals + one scheduled run


def test_a_task_can_use_a_saved_automation_as_a_step_and_a_missing_one_needs_the_person(
    client, auth, tmp_path
):
    runtime = setup(client, auth)
    prepare(client, auth, tmp_path)
    approve_and_save(client, auth, tmp_path)
    done = wait(
        client,
        auth,
        start(
            client,
            auth,
            plan(step("go", "automation", automation="Say hello"), deliver="What it printed"),
        ),
    )
    assert (
        done["status"] == "done"
        and "hello from drone" in done["result"]
        and done["used_calls"] == 0
    )
    gone = wait(
        client, auth, start(client, auth, plan(step("go", "automation", automation="Not saved")))
    )
    assert gone["status"] == "needs_you" and "no saved automation named Not saved" in gone["error"]
    assert runtime.router.chat is not None


def test_only_the_person_can_save_one_and_removing_it_is_final(client, auth, tmp_path):
    prepare(client, auth, tmp_path)
    asked = do(
        client, auth, "project.run", project="drone", tool="python", script="hello.py"
    ).json()
    # The approval body is the only way in; a malformed or foreign request does nothing.
    waiting = client.get("/api/v1/projects/approvals", headers=auth).json()[0]
    bad = client.post(
        f"/api/v1/actions/{asked['id']}/approve",
        headers=auth,
        json={"sha256": waiting["sha256"], "automation": {"name": "x", "every_minutes": 1}},
    )
    assert bad.status_code == 422  # not more often than every five minutes
    assert client.get("/api/v1/automations", headers=auth).json() == []
    # Models cannot propose it: there is no such action.
    assert do(client, auth, "automation.save", name="x").status_code == 422
    approve_and_save(client, auth, tmp_path)
    assert client.delete("/api/v1/automations/Say hello", headers=auth).status_code == 204
    assert client.delete("/api/v1/automations/Say hello", headers=auth).status_code == 404
    assert client.get("/api/v1/automations", headers=auth).json() == []
