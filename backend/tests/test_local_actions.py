"""Notes, files and small PC controls: they work only inside the folders the person chose, never
overwrite or delete, and go through the same rules, pool and checks as every other action.
Windows itself is replaced here: nothing is locked, muted or spoken during the tests."""

import zipfile

import pytest

from mavo import local_files, pc_controls
from mavo.action_rules import CATALOG, LOCAL_SERVICES
from mavo.connectors.catalog import action_grant, provider_for
from mavo.google_actions import MODELS, ActionDraft
from mavo.resource_pool import RESOURCES, resource_for_operation
from tests.test_desktop_actions import save

NEW = [
    "windows.lock",
    "windows.volume",
    "windows.media",
    "windows.speak",
    "notes.find",
    "notes.read",
    "notes.write",
    "files.list",
    "files.move",
    "files.backup",
]


def test_every_new_action_is_known_everywhere_an_action_must_be():
    for op in NEW:
        assert op in CATALOG and op in MODELS
        assert op.split(".")[0] in LOCAL_SERVICES and provider_for(op) == "local"
        assert action_grant(op) == "action:" + op
        assert resource_for_operation(op) in RESOURCES
    assert (
        resource_for_operation("notes.write") == "notes"
        and resource_for_operation("files.move") == "files"
    )
    assert CATALOG["files.move"][2] is True  # moving is consequential


def rule(*folders):
    return {"enabled": True, "mode": "restricted", "folders": [str(f) for f in folders]}


def test_a_rule_without_folders_allows_nothing_even_without_limits(tmp_path):
    with pytest.raises(ValueError, match="Choose the folders"):
        local_files.perform("notes.find", {"query": "x"}, {"mode": "unrestricted", "folders": []})


def test_paths_outside_the_folders_are_refused_however_they_are_written(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (tmp_path / "secret.md").write_text("no")
    for path in [str(tmp_path / "secret.md"), "../secret.md", "sub/../../secret.md"]:
        with pytest.raises(ValueError, match="outside the allowed folders"):
            local_files.perform("notes.read", {"path": path}, rule(vault))


def test_notes_are_found_read_created_and_appended_never_overwritten(tmp_path):
    vault = tmp_path / "vault"
    (vault / "people").mkdir(parents=True)
    (vault / "people" / "Mom.md").write_text("# Mom\nBirthday: 12 March\nLikes jasmine.\n")
    (vault / "Groceries.md").write_text("milk\n")
    (vault / ".obsidian").mkdir()
    (vault / ".obsidian" / "mom.md").write_text("birthday config")

    found = local_files.perform("notes.find", {"query": "mom birthday"}, rule(vault))["notes"]
    assert [n["title"] for n in found] == ["Mom"]  # the app's own folder is skipped
    assert "Birthday: 12 March" in found[0]["snippet"]
    assert (
        "jasmine"
        in local_files.perform("notes.read", {"path": "people/Mom.md"}, rule(vault))["text"]
    )

    made = local_files.perform(
        "notes.write", {"path": "Journal/2026-11-06.md", "text": "Good day."}, rule(vault)
    )
    assert made["created"] and (vault / "Journal" / "2026-11-06.md").read_text() == "Good day.\n"
    with pytest.raises(ValueError, match="already exists"):
        local_files.perform("notes.write", {"path": "Groceries.md", "text": "eggs"}, rule(vault))
    local_files.perform(
        "notes.write", {"path": "Groceries.md", "text": "eggs", "mode": "append"}, rule(vault)
    )
    assert (vault / "Groceries.md").read_text() == "milk\n\neggs\n"


def test_a_note_must_be_markdown():
    with pytest.raises(ValueError, match="Markdown"):
        ActionDraft.model_validate(
            {"operation": "notes.write", "arguments": {"path": "run.bat", "text": "x"}}
        )


def test_files_move_only_where_nothing_has_that_name(tmp_path):
    downloads, sorted_ = tmp_path / "Downloads", tmp_path / "Sorted"
    downloads.mkdir()
    sorted_.mkdir()
    (downloads / "bill.pdf").write_text("a")
    (downloads / "photo.jpg").write_text("b")
    (sorted_ / "photo.jpg").write_text("older")
    scope = rule(downloads, sorted_)

    listed = local_files.perform(
        "files.list", {"folder": str(downloads), "pattern": "*.pdf"}, scope
    )["items"]
    assert [i["name"] for i in listed] == ["bill.pdf"]
    moved = local_files.perform(
        "files.move",
        {"source": str(downloads / "bill.pdf"), "destination_folder": str(sorted_ / "Bills")},
        scope,
    )
    assert (
        moved["moved"]
        and (sorted_ / "Bills" / "bill.pdf").exists()
        and not (downloads / "bill.pdf").exists()
    )
    with pytest.raises(ValueError, match="already exists there. Nothing was moved"):
        local_files.perform(
            "files.move",
            {"source": str(downloads / "photo.jpg"), "destination_folder": str(sorted_)},
            scope,
        )
    assert (sorted_ / "photo.jpg").read_text() == "older" and (downloads / "photo.jpg").exists()
    with pytest.raises(ValueError, match="outside the allowed folders"):
        local_files.perform(
            "files.move",
            {
                "source": str(downloads / "photo.jpg"),
                "destination_folder": str(tmp_path / "Elsewhere"),
            },
            scope,
        )
    with pytest.raises(ValueError, match="cannot be moved"):
        local_files.perform(
            "files.move", {"source": str(downloads), "destination_folder": str(sorted_)}, scope
        )


def test_a_backup_is_a_new_dated_zip_outside_the_folder(tmp_path):
    project, backups = tmp_path / "Project", tmp_path / "Backups"
    (project / "src").mkdir(parents=True)
    (project / "src" / "main.py").write_text("print(1)")
    (project / ".git").mkdir()
    (project / ".git" / "HEAD").write_text("ref")
    scope = rule(project, backups)
    first = local_files.perform(
        "files.backup", {"folder": str(project), "destination_folder": str(backups)}, scope
    )
    second = local_files.perform(
        "files.backup", {"folder": str(project), "destination_folder": str(backups)}, scope
    )
    assert first["backup"] != second["backup"] and first["files"] == 1
    with zipfile.ZipFile(first["backup"]) as archive:
        assert [n.replace("\\", "/") for n in archive.namelist()] == ["src/main.py"]
    with pytest.raises(ValueError, match="outside the folder being backed up"):
        local_files.perform(
            "files.backup",
            {"folder": str(project), "destination_folder": str(project / "old")},
            scope,
        )


def test_notes_run_through_the_action_rules_like_every_other_action(client, auth, tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    payload = {
        "operation": "notes.write",
        "arguments": {"path": "Ideas.md", "text": "A drone that waters plants."},
    }
    assert (
        client.post("/api/v1/actions", headers=auth, json=payload).status_code == 409
    )  # not allowed yet
    save(client, auth, "notes.write", folders=[str(vault)])
    done = client.post("/api/v1/actions", headers=auth, json=payload).json()
    assert done["status"] == "completed", done
    assert (vault / "Ideas.md").read_text().startswith("A drone")
    outside = {
        "operation": "notes.write",
        "arguments": {"path": str(tmp_path / "x.md"), "text": "no"},
    }
    failed = client.post("/api/v1/actions", headers=auth, json=outside).json()
    assert failed["status"] == "failed" and "outside the allowed folders" in failed["error"]
    assert not (tmp_path / "x.md").exists()


def test_the_desktop_says_which_agent_needs_you_and_why(client, auth):
    from mavo.database import CrewInstance
    from tests.test_tasks import setup

    runtime = setup(client, auth)
    routes = client.get("/api/v1/routes", headers=auth).json()
    made = client.post(
        "/api/v1/crew",
        headers=auth,
        json={
            "template_id": "computer",
            "name": "Battery Watch",
            "route_id": routes[0]["id"],
            "trigger_type": "interval",
            "review_interval_minutes": 30,
            "responsibilities": "Watch the battery.",
        },
    ).json()
    with runtime.factory() as session:
        crew = session.get(CrewInstance, made["id"])
        crew.runtime_state = "error"
        crew.is_running = True
        crew.last_runtime_error = (
            "Allow device metrics in Edit to monitor this computer.\nDetails follow."
        )
        session.commit()
        # Mavo's own hidden agent (it carries one-time tasks' alerts) waits on purpose: never
        # counted as needing the person, nor as running.
        hidden = CrewInstance(
            **{
                c.name: getattr(crew, c.name)
                for c in CrewInstance.__table__.columns
                if c.name not in {"id", "name"}
            }
            | {
                "name": "Mavo tasks",
                "hidden": True,
                "runtime_state": "needs_input",
                "is_running": True,
            }
        )
        session.add(hidden)
        session.commit()
    status = client.get("/api/v1/desktop-status", headers=auth).json()
    assert status["running"] == 1  # Battery Watch only
    assert status["pending"] == 1
    assert status["working"] == []
    with runtime.factory() as session:
        session.get(CrewInstance, made["id"]).runtime_state = "working"
        session.commit()
    assert client.get("/api/v1/desktop-status", headers=auth).json()["working"] == ["Battery Watch"]
    with runtime.factory() as session:
        session.get(CrewInstance, made["id"]).runtime_state = "error"
        session.commit()
    assert status["needs"] == [
        {
            "id": made["id"],
            "name": "Battery Watch",
            "reason": "Allow device metrics in Edit to monitor this computer.",
        }
    ]


class Keys:
    def __init__(self):
        self.pressed = []

    def keybd_event(self, vk, scan, flags, extra):
        if flags == 1:
            self.pressed.append(vk)

    def LockWorkStation(self):
        self.pressed.append("lock")
        return 1


@pytest.fixture
def keys(monkeypatch):
    fake = Keys()
    monkeypatch.setattr(pc_controls.ctypes, "WinDLL", lambda name, **kw: fake)
    monkeypatch.setattr(pc_controls.os, "name", "nt")
    return fake


def test_volume_goes_to_zero_then_up_to_the_level(keys):
    assert pc_controls.perform("windows.volume", {"level": 30}) == {"volume": 30}
    assert keys.pressed == [0xAE] * 50 + [0xAF] * 15
    keys.pressed.clear()
    assert pc_controls.perform("windows.volume", {"level": 0}) == {"volume": 0}
    assert keys.pressed == [0xAE] * 50


def test_music_keys_and_lock(keys):
    pc_controls.perform("windows.media", {"key": "play_pause"})
    pc_controls.perform("windows.media", {"key": "next"})
    pc_controls.perform("windows.lock", {})
    assert keys.pressed == [0xB3, 0xB0, "lock"]


def test_spoken_text_is_never_part_of_the_command(monkeypatch):
    seen = {}

    def run(args, **kw):
        seen.update(args=args, input=kw["input"])
        return type("Done", (), {"returncode": 0, "stderr": ""})()

    monkeypatch.setattr(pc_controls.subprocess, "run", run)
    monkeypatch.setattr(pc_controls.os, "name", "nt")
    text = "Hello'; Remove-Item C:\\ -Recurse; '"
    assert pc_controls.perform("windows.speak", {"text": text})["spoken"]
    assert seen["input"] == text and not any(text in a for a in seen["args"])
