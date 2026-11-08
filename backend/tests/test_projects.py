# ruff: noqa: F811, E501
"""Projects (make a folder, write files, run scripts after the person's approval), the one-click
"set all actions" control, and the Crew that ties them together."""

import asyncio
import subprocess
import sys
import time

import pytest

from mavo.database import CrewInstance
from mavo.projects import Projects, find_blender
from tests.test_planner import planning, turn  # noqa: F401
from tests.test_scenarios import checkpoint, decide, route, run_work, verify

HELLO = "import os\nprint('hello from', os.path.basename(os.getcwd()))\nopen('result.txt','w').write('ok')\n"


def make(tmp_path):
    projects = Projects(default_root=tmp_path / "root")
    projects.create({"name": "Drone"}, {})
    return projects


# ---- files stay inside the project ---------------------------------------------------------


def test_files_are_written_and_read_only_inside_the_project(tmp_path):
    projects = make(tmp_path)
    assert (
        projects.write({"project": "drone", "path": "scripts/build.py", "content": "print(1)"}, {})[
            "bytes"
        ]
        == 8
    )
    assert projects.read({"project": "Drone", "path": "scripts/build.py"}, {})["text"] == "print(1)"
    assert [f["path"] for f in projects.read({"project": "drone"}, {})["files"]] == [
        "scripts/build.py"
    ]
    # A name with other spellings is the same project; an unknown one is not created by a write.
    with pytest.raises(ValueError, match="no project"):
        projects.write({"project": "other", "path": "a.py", "content": "x"}, {})


@pytest.mark.parametrize(
    "path",
    [
        "../escape.py",
        "a/../../escape.py",
        "/abs.py",
        "C:/x.py",
        "C:\\x.py",
        "sub\\x.py",
        ".hidden.py",
        ".mavo-project.json",
        "con.py",
        "nul",
        "a" * 70 + ".py",
        "a/b/c/d/e/f.py",
        "x.py ",
        "x.py.",
        "x\x00.py",
        "",
    ],
)
def test_paths_that_climb_hide_or_name_devices_are_refused(tmp_path, path):
    projects = make(tmp_path)
    with pytest.raises((ValueError, KeyError)):
        projects.write({"project": "drone", "path": path, "content": "x"}, {})
    assert not (tmp_path / "escape.py").exists()


@pytest.mark.parametrize(
    "name",
    ["run.bat", "run.cmd", "tool.exe", "x.ps1", "x.dll", "x.lnk", "x.vbs", "model.blend", "x"],
)
def test_only_text_files_can_be_written(tmp_path, name):
    with pytest.raises(ValueError, match="text files"):
        make(tmp_path).write({"project": "drone", "path": name, "content": "x"}, {})


def test_size_and_count_limits(tmp_path):
    projects = make(tmp_path)
    with pytest.raises(ValueError, match="text and under"):
        projects.write({"project": "drone", "path": "a.txt", "content": "x\x00"}, {})
    with pytest.raises(ValueError, match="under 200 KB"):
        projects.write({"project": "drone", "path": "a.txt", "content": "x" * 200_001}, {})
    for n in range(200):
        projects.write({"project": "drone", "path": f"f{n}.txt", "content": "x"}, {})
    with pytest.raises(ValueError, match="full"):
        projects.write({"project": "drone", "path": "one-more.txt", "content": "x"}, {})
    projects.write(
        {"project": "drone", "path": "f0.txt", "content": "changed"}, {}
    )  # replacing is fine


def test_links_inside_a_project_are_not_followed(tmp_path):
    projects = make(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret")
    link = tmp_path / "root" / "drone" / "link"
    made = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True
    )
    if made.returncode != 0:
        pytest.skip("this account cannot create a junction")
    with pytest.raises(ValueError, match="Links"):
        projects.read({"project": "drone", "path": "link/secret.txt"}, {})
    with pytest.raises(ValueError, match="Links"):
        projects.write({"project": "drone", "path": "link/new.txt", "content": "x"}, {})
    assert not (outside / "new.txt").exists()
    assert "link/secret.txt" not in [
        f["path"] for f in projects.read({"project": "drone"}, {})["files"]
    ]


def test_an_existing_folder_that_is_not_a_project_is_not_taken_over(tmp_path):
    projects = Projects(default_root=tmp_path / "root")
    (tmp_path / "root" / "mine").mkdir(parents=True)
    (tmp_path / "root" / "mine" / "important.docx").write_text("x")
    with pytest.raises(ValueError, match="not a Mavo project"):
        projects.create({"name": "mine"}, {})
    assert projects.create({"name": "Drone"}, {})["existed"] is False
    assert projects.create({"name": "drone"}, {})["existed"] is True


# ---- running -------------------------------------------------------------------------------


def run_args(script="hello.py", **extra):
    return {"project": "drone", "tool": "python", "script": script, **extra}


def test_a_python_script_runs_in_the_project_with_a_clean_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVO_API_TOKEN_TEST", "hunter2")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    projects = make(tmp_path)
    script = (
        "import os, sys\nprint('cwd', os.path.basename(os.getcwd()))\n"
        "print('token', os.environ.get('MAVO_API_TOKEN_TEST'), os.environ.get('OPENAI_API_KEY'))\n"
        "print('path', bool(os.environ.get('PATH')))\nprint('args', sys.argv[1:])\n"
        "open('result.txt', 'w').write('ok')\n"
    )
    projects.write({"project": "drone", "path": "hello.py", "content": script}, {})
    result = asyncio.run(projects.run(run_args(args=["a", "b"]), {"mode": "unrestricted"}))
    assert result["exit_code"] == 0 and not result["timed_out"]
    assert "cwd drone" in result["output"] and "token None None" in result["output"]
    assert "path True" in result["output"] and "['a', 'b']" in result["output"]
    assert [f["path"] for f in result["changed_files"]] == ["result.txt"]
    assert not projects.children


def test_a_failing_script_returns_its_error_for_the_agent_to_read(tmp_path):
    projects = make(tmp_path)
    projects.write(
        {"project": "drone", "path": "bad.py", "content": "raise RuntimeError('boom')"}, {}
    )
    result = asyncio.run(projects.run(run_args("bad.py"), {"mode": "unrestricted"}))
    assert result["exit_code"] == 1 and "RuntimeError: boom" in result["output"]


def test_a_runaway_script_is_stopped(tmp_path, monkeypatch):
    monkeypatch.setitem(
        __import__("mavo.projects", fromlist=["RUN_LIMITS"]).RUN_LIMITS, "python", 2
    )
    projects = make(tmp_path)
    projects.write(
        {
            "project": "drone",
            "path": "slow.py",
            "content": "import time\nprint('start', flush=True)\ntime.sleep(60)",
        },
        {},
    )
    began = time.monotonic()
    result = asyncio.run(projects.run(run_args("slow.py"), {"mode": "unrestricted"}))
    assert result["timed_out"] and "start" in result["output"] and time.monotonic() - began < 20
    assert not projects.children


def test_restricted_access_only_runs_the_tools_it_names(tmp_path):
    projects = make(tmp_path)
    projects.write({"project": "drone", "path": "hello.py", "content": HELLO}, {})
    with pytest.raises(ValueError, match="python.exe is not allowed"):
        asyncio.run(projects.run(run_args(), {"mode": "restricted", "apps": ["blender.exe"]}))


def test_the_approval_hash_covers_every_script_in_the_project(tmp_path):
    projects = make(tmp_path)
    projects.write({"project": "drone", "path": "hello.py", "content": HELLO}, {})
    first = projects.binding(run_args(), {})
    assert first == projects.binding(run_args(), {})
    projects.write({"project": "drone", "path": "helper.py", "content": "x = 1"}, {})
    second = projects.binding(run_args(), {})
    assert second != first  # a script it could import was added
    projects.write({"project": "drone", "path": "notes.txt", "content": "not code"}, {})
    assert projects.binding(run_args(), {}) == second  # text that cannot run does not matter
    assert projects.binding(run_args(args=["x"]), {}) != second  # nor can the arguments change
    with pytest.raises(ValueError, match="not in this project"):
        projects.binding(run_args("missing.py"), {})


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows tool lookup")
def test_blender_builds_a_model_and_saves_it_in_the_project(tmp_path):
    try:
        find_blender()
    except ValueError:
        pytest.skip("Blender is not installed")
    projects = make(tmp_path)
    script = (
        "import bpy, os\nbpy.ops.wm.read_factory_settings(use_empty=True)\n"
        "bpy.ops.mesh.primitive_cube_add(size=2)\ncube = bpy.context.active_object\ncube.name = 'Body'\n"
        "bpy.ops.wm.save_as_mainfile(filepath=os.path.join(os.getcwd(), 'drone.blend'))\n"
        "print('saved', [o.name for o in bpy.data.objects])\n"
    )
    projects.write({"project": "drone", "path": "build.py", "content": script}, {})
    result = asyncio.run(
        projects.run(
            {"project": "drone", "tool": "blender", "script": "build.py"}, {"mode": "unrestricted"}
        )
    )
    assert result["exit_code"] == 0, result["output"]
    assert "saved ['Body']" in result["output"]
    assert any(f["path"] == "drone.blend" and f["bytes"] > 1000 for f in result["changed_files"])


# ---- asking first ---------------------------------------------------------------------------


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows tool lookup")
def test_a_blender_window_opens_once_and_is_not_duplicated(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from mavo.projects import kill_tree

    try:
        find_blender()
    except ValueError:
        pytest.skip("Blender is not installed")
    original = Projects.open_window

    def off_screen(self, argv, folder, before):  # a test, not a demonstration
        return original(
            self,
            [argv[0], "--window-geometry", "-32000", "0", "400", "300", *argv[1:]],
            folder,
            before,
        )

    monkeypatch.setattr(Projects, "open_window", off_screen)
    projects = make(tmp_path)
    projects.write(
        {"project": "drone", "path": "look.py", "content": "print('window script ran')\n"}, {}
    )
    action = SimpleNamespace(
        operation="project.run",
        arguments={"project": "drone", "tool": "blender", "script": "look.py", "show": True},
    )
    first = None
    try:
        first = asyncio.run(projects.perform(action, {"mode": "unrestricted"}))
        assert first["opened"] and first["running"] and first["pid"]
        again = asyncio.run(projects.perform(action, {"mode": "unrestricted"}))
        assert again == {"opened": True, "already_open": True, "pid": first["pid"]}
    finally:
        if first:
            kill_tree(first["pid"])  # the window is the test's own: close exactly it


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows tool lookup")
def test_a_blender_script_that_crashes_is_reported_as_a_failure(tmp_path):
    try:
        find_blender()
    except ValueError:
        pytest.skip("Blender is not installed")
    projects = make(tmp_path)
    # A real mistake from a real run: a mesh has no `color` attribute.
    script = "import bpy\nbpy.ops.mesh.primitive_cube_add()\nbpy.context.active_object.data.color = (0.2, 0.2, 0.2)\n"
    projects.write({"project": "drone", "path": "bad.py", "content": script}, {})
    result = asyncio.run(
        projects.run(
            {"project": "drone", "tool": "blender", "script": "bad.py"}, {"mode": "unrestricted"}
        )
    )
    assert result["exit_code"] == 1  # Blender alone exits 0 here
    assert "AttributeError" in result["output"] and "color" in result["output"]


def save(client, auth, operation, mode="restricted", crew_id=None, **scope):
    response = client.put(
        "/api/v1/action-policies",
        headers=auth,
        json={
            "operation": operation,
            "crew_id": crew_id,
            "rule": {"enabled": True, "mode": mode, **scope},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def allow_projects(client, auth, tmp_path, run_mode="restricted"):
    for operation in ("project.create", "project.write", "project.read"):
        save(client, auth, operation, "restricted", folders=[str(tmp_path)])
    scope = {"apps": ["python.exe"]} if run_mode == "restricted" else {}
    save(
        client,
        auth,
        "project.run",
        run_mode,
        folders=[str(tmp_path)] if run_mode == "restricted" else [],
        **scope,
    )


def do(client, auth, operation, **arguments):
    response = client.post(
        "/api/v1/actions", headers=auth, json={"operation": operation, "arguments": arguments}
    )
    return response


def finished(client, auth, action_id, wanted=("completed", "failed", "blocked", "unknown")):
    for _ in range(150):
        row = next(
            a for a in client.get("/api/v1/actions", headers=auth).json() if a["id"] == action_id
        )
        if row["status"] in wanted:
            return row
        time.sleep(0.2)
    raise AssertionError(f"still {row['status']}")


def prepare(client, auth, tmp_path, run_mode="restricted", script=HELLO):
    allow_projects(client, auth, tmp_path, run_mode)
    assert do(client, auth, "project.create", name="Drone").json()["status"] == "completed"
    wrote = do(client, auth, "project.write", project="drone", path="hello.py", content=script)
    assert wrote.json()["status"] == "completed", wrote.text


def test_a_script_run_waits_for_the_person_and_runs_only_what_they_approved(client, auth, tmp_path):
    prepare(client, auth, tmp_path)
    asked = do(client, auth, "project.run", project="drone", tool="python", script="hello.py")
    assert asked.status_code == 201 and asked.json()["status"] == "pending"
    assert asked.json()["target_summary"] == "Python: hello.py in drone"
    assert not (tmp_path / "drone" / "result.txt").exists()  # nothing ran

    waiting = client.get("/api/v1/projects/approvals", headers=auth).json()
    assert len(waiting) == 1 and waiting[0]["id"] == asked.json()["id"]
    assert waiting[0]["sources"][0] == {"path": "hello.py", "text": HELLO, "truncated": False}
    assert waiting[0]["changed"] is False and waiting[0]["trusted"] is False
    approve = f"/api/v1/actions/{asked.json()['id']}/approve"

    # What they were shown must be what runs: a wrong hash, or scripts changed since, is refused.
    assert client.post(approve, headers=auth, json={"sha256": "0" * 64}).status_code == 409
    assert client.post(approve, headers=auth).status_code == 422
    do(client, auth, "project.write", project="drone", path="hello.py", content="print('swapped')")
    changed = client.get("/api/v1/projects/approvals", headers=auth).json()[0]
    assert changed["changed"] is True
    assert (
        client.post(approve, headers=auth, json={"sha256": waiting[0]["sha256"]}).status_code == 409
    )
    assert not (tmp_path / "drone" / "result.txt").exists()

    # A fresh request for the scripts as they now are can be approved.
    do(client, auth, "project.write", project="drone", path="hello.py", content=HELLO)
    again = do(
        client, auth, "project.run", project="drone", tool="python", script="hello.py", args=["x"]
    ).json()
    assert again["status"] == "pending"
    sha = next(
        w
        for w in client.get("/api/v1/projects/approvals", headers=auth).json()
        if w["id"] == again["id"]
    )["sha256"]
    assert client.post(
        f"/api/v1/actions/{again['id']}/approve", headers=auth, json={"sha256": sha}
    ).json() == {"status": "approved"}
    done = finished(client, auth, again["id"])
    assert done["status"] == "completed" and done["approved_by"] == "user"
    assert "hello from drone" in done["result"]["output"]
    assert (tmp_path / "drone" / "result.txt").read_text() == "ok"
    # It cannot be approved twice, and is no longer waiting.
    assert (
        client.post(
            f"/api/v1/actions/{again['id']}/approve", headers=auth, json={"sha256": sha}
        ).status_code
        == 409
    )
    assert [w["id"] for w in client.get("/api/v1/projects/approvals", headers=auth).json()] == [
        asked.json()["id"]
    ]


def test_denying_a_run_leaves_it_unrun(client, auth, tmp_path):
    prepare(client, auth, tmp_path)
    asked = do(
        client, auth, "project.run", project="drone", tool="python", script="hello.py"
    ).json()
    assert client.post(f"/api/v1/actions/{asked['id']}/reject", headers=auth).json() == {
        "status": "rejected"
    }
    assert client.get("/api/v1/projects/approvals", headers=auth).json() == []
    assert not (tmp_path / "drone" / "result.txt").exists()


def test_unrestricted_access_still_asks_before_running_a_script(client, auth, tmp_path):
    prepare(client, auth, tmp_path, run_mode="unrestricted")
    assert (
        do(client, auth, "project.run", project="drone", tool="python", script="hello.py").json()[
            "status"
        ]
        == "pending"
    )


def test_a_trusted_project_runs_without_asking_and_trust_can_be_withdrawn(client, auth, tmp_path):
    prepare(client, auth, tmp_path)
    assert client.put(
        "/api/v1/projects/drone/trust", headers=auth, json={"trusted": True}
    ).json() == {"project": "drone", "trusted": True}
    ran = do(client, auth, "project.run", project="drone", tool="python", script="hello.py").json()
    assert ran["status"] == "completed" and ran["approved_by"] == "access"
    assert (tmp_path / "drone" / "result.txt").exists()
    listing = client.get("/api/v1/projects", headers=auth).json()
    assert listing["projects"] == [
        {"project": "drone", "folder": str(tmp_path / "drone"), "files": 2, "trusted": True}
    ]
    client.put("/api/v1/projects/drone/trust", headers=auth, json={"trusted": False})
    assert (
        do(
            client,
            auth,
            "project.run",
            project="drone",
            tool="python",
            script="hello.py",
            args=["again"],
        ).json()["status"]
        == "pending"
    )


def test_the_tool_must_be_one_the_settings_name_and_the_script_must_exist(client, auth, tmp_path):
    prepare(client, auth, tmp_path)
    blocked = do(client, auth, "project.run", project="drone", tool="blender", script="hello.py")
    assert blocked.status_code == 409 and "outside this action's allowed tools" in blocked.text
    missing = do(client, auth, "project.run", project="drone", tool="python", script="nope.py")
    assert missing.status_code == 409 and "not in this project" in missing.text
    # Settings that name something that cannot run are not saved.
    bad = client.put(
        "/api/v1/action-policies",
        headers=auth,
        json={
            "operation": "project.run",
            "rule": {"enabled": True, "mode": "restricted", "apps": ["cmd.exe"]},
        },
    )
    assert bad.status_code == 422
    none = client.put(
        "/api/v1/action-policies",
        headers=auth,
        json={"operation": "project.run", "rule": {"enabled": True, "mode": "restricted"}},
    )
    assert none.status_code == 422


def test_other_actions_still_have_no_per_action_approval(client, auth, tmp_path):
    save(client, auth, "terminal.run", folders=[str(tmp_path)])
    action = do(client, auth, "terminal.run", command="python.version", folder=str(tmp_path)).json()
    assert (
        client.post(
            f"/api/v1/actions/{action['id']}/approve", headers=auth, json={"sha256": "0" * 64}
        ).status_code
        == 410
    )


# ---- set every action at once ---------------------------------------------------------------


def policy(client, auth, operation):
    rows = client.get("/api/v1/action-policies", headers=auth).json()["actions"]
    return next(a for a in rows if a["operation"] == operation)["rule"]


def test_set_all_off_with_limits_and_without_limits(client, auth, tmp_path):
    limited = client.put(
        "/api/v1/action-policies/bulk", headers=auth, json={"mode": "limited"}
    ).json()
    done = {c["operation"] for c in limited["changed"]}
    assert {"project.create", "project.write", "project.read", "project.run"} <= done
    # What needs a site, folder or app list first is left alone and says why.
    reasons = {s["operation"]: s["reason"] for s in limited["skipped"]}
    assert (
        "sites" in reasons["browser.open"]
        and "folder" in reasons["terminal.run"]
        and "apps" in reasons["windows.focus"].lower()
    )
    assert not policy(client, auth, "browser.open")["enabled"]
    assert policy(client, auth, "project.run")["apps"] == ["blender.exe", "python.exe"]
    assert (
        policy(client, auth, "project.run")["enabled"]
        and policy(client, auth, "project.run")["mode"] == "restricted"
    )

    again = client.put(
        "/api/v1/action-policies/bulk", headers=auth, json={"mode": "limited"}
    ).json()
    assert again["changed"] == []  # nothing left to change

    off = client.put("/api/v1/action-policies/bulk", headers=auth, json={"mode": "off"}).json()
    assert {"project.create", "project.run"} <= {c["operation"] for c in off["changed"]} and off[
        "skipped"
    ] == []
    assert not any(
        a["rule"]["enabled"]
        for a in client.get("/api/v1/action-policies", headers=auth).json()["actions"]
    )

    assert (
        client.put(
            "/api/v1/action-policies/bulk", headers=auth, json={"mode": "unrestricted"}
        ).status_code
        == 409
    )
    wide = client.put(
        "/api/v1/action-policies/bulk", headers=auth, json={"mode": "unrestricted", "confirm": True}
    ).json()
    assert wide["skipped"] == [] and {"terminal.run", "browser.open", "project.run"} <= {
        c["operation"] for c in wide["changed"]
    }
    assert policy(client, auth, "browser.open") | {"revision": 0} == {
        **policy(client, auth, "browser.open"),
        "revision": 0,
    }
    assert (
        policy(client, auth, "browser.open")["mode"] == "unrestricted"
        and policy(client, auth, "browser.open")["domains"] == []
    )
    # Allowing everything still does not let a script run unasked.
    allow_root = tmp_path
    assert allow_root.exists()


def test_set_all_for_one_crew_grants_that_crews_tools_and_not_others(client, auth):
    made = client.post(
        "/api/v1/routes", headers=auth, json={"name": "T", "provider": "ollama", "model": "t"}
    ).json()
    ids = [
        client.post(
            "/api/v1/crew",
            headers=auth,
            json={
                "name": name,
                "template_id": "operator",
                "route_id": made["id"],
                "responsibilities": "Build things",
                "trigger_type": "manual",
            },
        ).json()["id"]
        for name in ("Builder", "Other")
    ]
    result = client.put(
        "/api/v1/action-policies/bulk", headers=auth, json={"mode": "limited", "crew_id": ids[0]}
    ).json()
    assert "project.run" in {c["operation"] for c in result["changed"]}
    runtime = client.app.state.crew_runtime
    with runtime.factory() as session:
        assert "action:project.run" in session.get(CrewInstance, ids[0]).permissions
        assert "action:project.run" not in session.get(CrewInstance, ids[1]).permissions
    assert (
        client.put(
            "/api/v1/action-policies/bulk", headers=auth, json={"mode": "off", "crew_id": "missing"}
        ).status_code
        == 404
    )


# ---- a file can come beside the JSON, not inside it -----------------------------------------


WRITE = '{"operation":"project.write","arguments":{"project":"drone","path":"scripts/build.py"}}'


def reply(actions, tail=""):
    return (
        '{"state":"continue","summary":"Wrote it.","next_step":"Run it","actions":['
        + actions
        + "]}"
        + tail
    )


def test_a_file_beside_the_json_is_attached_to_its_write():
    from mavo.crew_workers import parse_checkpoint

    code = 'import bpy\nprint("a \\"quoted\\" word", {"k": [1, 2]})\nif True:\n    pass'
    checkpoint = parse_checkpoint(reply(WRITE, f"\n\n```file:scripts/build.py\n{code}\n```\n"))
    assert checkpoint.actions[0].arguments["content"] == code + "\n"
    # In front of the JSON, with a language word, in a json fence: the same.
    for text in (
        f"```python file=scripts/build.py\n{code}\n```\n" + reply(WRITE),
        "```json\n" + reply(WRITE) + f"\n```\n```file:scripts/build.py\n{code}\n```",
        reply(WRITE, f"\r\n```path: scripts/build.py\r\n{code}\r\n```"),
    ):
        assert parse_checkpoint(text).actions[0].arguments["content"] == code + "\n"


def test_the_block_is_the_file_and_a_missing_one_is_still_invalid():
    from pydantic import ValidationError

    from mavo.crew_workers import parse_checkpoint

    # A real model wrote this: a placeholder in the JSON, the code in the block.
    placeholder = '{"operation":"project.write","arguments":{"project":"drone","path":"a.py","content":"// See attached file block for script contents."}}'
    got = parse_checkpoint(reply(placeholder, "\n```file:a.py\nprint('real')\n```"))
    assert got.actions[0].arguments["content"] == "print('real')\n"
    # Content in the JSON is used when there is no block for that path.
    own = '{"operation":"project.write","arguments":{"project":"drone","path":"a.py","content":"own"}}'
    assert (
        parse_checkpoint(reply(own, "\n```file:b.py\nother\n```")).actions[0].arguments["content"]
        == "own"
    )
    with pytest.raises(ValidationError):  # no block for this path, and no content
        parse_checkpoint(reply(WRITE, "\n```file:other.py\nx\n```"))
    with pytest.raises(ValidationError):
        parse_checkpoint(reply(WRITE))
    # Several files, each to its own write.
    two = WRITE + "," + WRITE.replace("scripts/build.py", "helper.py")
    got = parse_checkpoint(
        reply(two, "\n```file:scripts/build.py\nA\n```\n```file:helper.py\nB\n```")
    )
    assert [a.arguments["content"] for a in got.actions] == ["A\n", "B\n"]


def test_an_empty_content_beside_a_file_block_is_never_saved_as_an_empty_file():
    from mavo.crew_workers import parse_checkpoint

    empty = (
        '{"operation":"project.write","arguments":{"project":"drone","path":"a.py","content":""}}'
    )
    got = parse_checkpoint(reply(empty, "\n```file:a.py\nprint(1)\n```"))
    assert got.actions[0].arguments["content"] == "print(1)\n"
    # With no block for it, an empty file is exactly what was asked for.
    assert parse_checkpoint(reply(empty)).actions[0].arguments["content"] == ""


def test_obvious_mix_ups_in_a_run_request_are_taken_as_meant():
    from pydantic import ValidationError

    from mavo.projects import ProjectRun

    # The script given as "file" (what a real model did): it is the script.
    for tool in ("blender", "python"):
        run = ProjectRun(project="drone", tool=tool, file="build_drone.py")
        assert run.script == "build_drone.py" and run.file is None
    assert ProjectRun(project="drone", tool="python", script="a.py", show=True).show is False
    # A real .blend file to open, with the window, is still what it says.
    opened = ProjectRun(project="drone", tool="blender", file="drone.blend", show=True)
    assert opened.file == "drone.blend" and opened.show is True and opened.script is None
    for bad in (
        {"tool": "python", "file": "model.blend"},  # Python cannot open a Blender file
        {"tool": "blender"},  # nothing to run or open
        {"tool": "python", "script": "a.sh"},
    ):
        with pytest.raises(ValidationError):
            ProjectRun(project="drone", **bad)


@pytest.mark.parametrize(
    "label",
    [
        "file:scripts/build.py",
        "file: ./scripts/build.py",
        "path=scripts/build.py",
        "python:scripts/build.py",  # what a real model wrote
        "py:scripts/build.py",
        "python scripts/build.py",
        'python title="scripts/build.py"',
        "scripts/build.py",
    ],
)
def test_the_usual_ways_of_labelling_a_file_block_are_accepted(label):
    from mavo.crew_workers import parse_checkpoint

    got = parse_checkpoint(reply(WRITE, f"\n```{label}\nprint(1)\n```\n"))
    assert got.actions[0].arguments["content"] == "print(1)\n"


def test_words_after_the_json_do_not_discard_a_valid_answer_but_broken_json_does():
    from pydantic import ValidationError

    from mavo.crew_workers import parse_checkpoint

    assert (
        parse_checkpoint(reply("", "\n\nLet me know if you want changes.")).summary == "Wrote it."
    )
    assert parse_checkpoint(reply("", "\n```python\nprint(1)\n```")).summary == "Wrote it."
    for broken in (
        '{"state":"continue","summary":"x" "next_step":"y"}',  # a missing comma
        '{"state":"nonsense","summary":"x"} and more',  # trailing text, and the object is invalid
    ):
        with pytest.raises(ValidationError):
            parse_checkpoint(broken)


def test_an_ordinary_reply_and_other_code_fences_are_untouched():
    from mavo.crew_workers import parse_checkpoint, split_files

    plain = reply("")
    assert parse_checkpoint(plain).summary == "Wrote it."
    text = "```python\nprint(1)\n```"
    assert split_files(text) == (text, {})
    assert parse_checkpoint("```json\n" + plain + "\n```").summary == "Wrote it."


# ---- a Crew builds something ----------------------------------------------------------------


def project_crew(planning, tmp_path):
    client, auth, runtime, _ = planning
    r = route(client, auth)
    ops = ("project.create", "project.write", "project.read", "project.run")
    decide(
        planning,
        crew_proposal={
            "template_id": "operator",
            "name": "Builder",
            "route_id": r["id"],
            "responsibilities": "Build a drone model in Blender",
            "trigger_type": "manual",
            "requested_notifications": True,
            "requested_actions": [{"operation": op, "reason": "x"} for op in ops],
        },
    )
    draft = turn(planning, "Make me a drone in Blender")["draft"]
    scope = {"enabled": True, "mode": "restricted", "folders": [str(tmp_path)]}
    rules = {op: scope for op in ops[:3]} | {"project.run": scope | {"apps": ["python.exe"]}}
    crew = verify(client, auth, draft, route_id=r["id"], notifications=True, actions=rules)
    return crew, runtime


def test_a_crew_builds_waits_for_approval_and_continues_with_the_result(planning, tmp_path):
    client, auth, runtime, _ = planning
    crew, runtime = project_crew(planning, tmp_path)
    script = HELLO
    batch = [
        {"operation": "project.create", "arguments": {"name": "Drone"}},
        {
            "operation": "project.write",
            "arguments": {"project": "drone", "path": "hello.py", "content": script},
        },
        {
            "operation": "project.run",
            "arguments": {"project": "drone", "tool": "python", "script": "hello.py"},
        },
    ]
    run_work(runtime, crew["id"], checkpoint("Wrote the script.", state="continue", actions=batch))
    with runtime.factory() as session:
        stored = session.get(CrewInstance, crew["id"])
        assert stored.runtime_state == "needs_input"
        assert (
            "Waiting for your OK to run Python: hello.py in drone" in stored.observation["question"]
        )
    assert (tmp_path / "drone" / "hello.py").is_file() and not (
        tmp_path / "drone" / "result.txt"
    ).exists()

    waiting = client.get("/api/v1/projects/approvals", headers=auth).json()
    assert [(w["crew_name"], w["script"]) for w in waiting] == [("Builder", "hello.py")]
    client.post(
        f"/api/v1/actions/{waiting[0]['id']}/approve",
        headers=auth,
        json={"sha256": waiting[0]["sha256"]},
    ).raise_for_status()
    finished(client, auth, waiting[0]["id"])

    # The Crew is woken and its next step sees what the run printed.
    with runtime.factory() as session:
        stored = session.get(CrewInstance, crew["id"])
        assert stored.runtime_state == "waiting" and stored.next_run_at is not None
        assert stored.observation.get("question") is None
    chat = run_work(
        runtime, crew["id"], checkpoint("Built and ran it.", state="wait_for_next_review")
    )
    prompt = chat.await_args_list[0].args[2][0]["content"]
    assert "hello from drone" in prompt and '"project.run"' in prompt


def test_a_script_the_person_denies_is_reported_back_to_the_crew(planning, tmp_path):
    client, auth, runtime, _ = planning
    crew, runtime = project_crew(planning, tmp_path)
    batch = [
        {"operation": "project.create", "arguments": {"name": "Drone"}},
        {
            "operation": "project.write",
            "arguments": {"project": "drone", "path": "hello.py", "content": HELLO},
        },
        {
            "operation": "project.run",
            "arguments": {"project": "drone", "tool": "python", "script": "hello.py"},
        },
    ]
    run_work(runtime, crew["id"], checkpoint("Wrote it.", state="continue", actions=batch))
    waiting = client.get("/api/v1/projects/approvals", headers=auth).json()[0]
    client.post(f"/api/v1/actions/{waiting['id']}/reject", headers=auth).raise_for_status()
    chat = run_work(
        runtime, crew["id"], checkpoint("Understood, not running it.", state="wait_for_next_review")
    )
    assert '"status": "rejected"' in chat.await_args_list[0].args[2][0]["content"]
    assert not (tmp_path / "drone" / "result.txt").exists()


def test_a_request_that_cannot_work_goes_back_to_the_agent_not_to_the_person(planning, tmp_path):
    client, auth, runtime, _ = planning
    crew, runtime = project_crew(planning, tmp_path)
    run_work(
        runtime,
        crew["id"],
        checkpoint(
            "Running it.",
            state="continue",
            actions=[
                {
                    "operation": "project.run",
                    "arguments": {"project": "ghost", "tool": "python", "script": "x.py"},
                }
            ],
        ),
    )
    with runtime.factory() as session:
        stored = session.get(CrewInstance, crew["id"])
        assert stored.runtime_state != "needs_input" and stored.observation.get("question") is None
        assert "REFUSED" in stored.checkpoint and "no project" in stored.checkpoint
        assert stored.observation["refusals"] == 1


def test_a_failed_script_is_something_the_agent_fixes_not_a_stop(planning, tmp_path):
    client, auth, runtime, _ = planning
    crew, runtime = project_crew(planning, tmp_path)
    client.put("/api/v1/projects/drone/trust", headers=auth, json={"trusted": True})
    batch = [
        {"operation": "project.create", "arguments": {"name": "Drone"}},
        {
            "operation": "project.write",
            "arguments": {"project": "drone", "path": "bad.py", "content": "raise SystemExit(3)"},
        },
        {
            "operation": "project.run",
            "arguments": {"project": "drone", "tool": "python", "script": "bad.py"},
        },
    ]
    run_work(runtime, crew["id"], checkpoint("Ran it.", state="continue", actions=batch))
    with runtime.factory() as session:
        stored = session.get(CrewInstance, crew["id"])
        assert stored.runtime_state == "waiting"  # carries on to fix it
        from mavo.database import ActionRequest

        run = [a for a in session.query(ActionRequest) if a.operation == "project.run"][0]
        assert run.status == "completed" and run.result["exit_code"] == 3
