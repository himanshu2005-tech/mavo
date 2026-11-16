"""Agents and the person's files: read (text, code, PDF, Word), find by name or text, copy, write
new or add to, and delete to the Recycle Bin only with the person's OK. Inside the folders the
person chose (a whole drive, if they choose it), never Mavo's own data, never overwriting."""

import os
from pathlib import Path

import pytest

from mavo import local_files
from mavo.action_rules import ALWAYS_ASKS, CATALOG, LOOKS_ONLY
from mavo.desktop_actions import TerminalArgs, sequence_script


def rule(*folders):
    return {"enabled": True, "mode": "restricted", "folders": [str(f) for f in folders]}


@pytest.fixture
def home(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "invoice-nov.txt").write_text(
        "Invoice total: 1,240 INR\n", encoding="utf-8"
    )
    (tmp_path / "docs" / "notes.md").write_text("Call Ravi about the roadmap.\n", encoding="utf-8")
    (tmp_path / "docs" / "photo.jpg").write_bytes(b"\xff\xd8")
    return tmp_path


def test_it_reads_text_files_in_parts_and_says_which_it_cannot(home):
    allowed = rule(home)
    read = local_files.perform(
        "files.read", {"path": str(home / "docs" / "invoice-nov.txt")}, allowed
    )
    assert read["text"].startswith("Invoice total") and read["more"] is False
    part = local_files.perform(
        "files.read", {"path": "docs/notes.md", "start": 5, "max_chars": 100}, allowed
    )
    assert part["text"].startswith("Ravi")
    with pytest.raises(ValueError, match=".jpg files are not text"):
        local_files.perform("files.read", {"path": str(home / "docs" / "photo.jpg")}, allowed)


def test_it_finds_files_by_name_and_by_what_they_say(home):
    allowed = rule(home)
    by_name = local_files.perform("files.find", {"query": "invoice"}, allowed)
    assert [f["name"] for f in by_name["files"]] == ["invoice-nov.txt"]
    assert local_files.perform("files.find", {"query": "roadmap"}, allowed)["files"] == []
    by_text = local_files.perform("files.find", {"query": "roadmap", "in_text": True}, allowed)
    assert [f["name"] for f in by_text["files"]] == ["notes.md"]
    pdfs = local_files.perform("files.find", {"pattern": "*.jpg"}, allowed)
    assert [f["name"] for f in pdfs["files"]] == ["photo.jpg"]


def test_it_copies_and_writes_but_never_overwrites(home):
    allowed = rule(home)
    local_files.perform(
        "files.copy", {"source": "docs/notes.md", "destination_folder": "backup"}, allowed
    )
    assert (home / "backup" / "notes.md").read_text(encoding="utf-8").startswith("Call Ravi")
    with pytest.raises(ValueError, match="already exists"):
        local_files.perform(
            "files.copy", {"source": "docs/notes.md", "destination_folder": "backup"}, allowed
        )
    local_files.perform(
        "files.write", {"path": "out/summary.txt", "text": "Two invoices.\n"}, allowed
    )
    with pytest.raises(ValueError, match="already exists"):
        local_files.perform("files.write", {"path": "out/summary.txt", "text": "Replace!"}, allowed)
    local_files.perform(
        "files.write", {"path": "out/summary.txt", "text": "One paid.\n", "mode": "append"}, allowed
    )
    assert (home / "out" / "summary.txt").read_text(
        encoding="utf-8"
    ) == "Two invoices.\nOne paid.\n"


def test_nothing_outside_the_chosen_folders_and_never_mavos_own_data(home, tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="outside the allowed folders"):
        local_files.perform(
            "files.read", {"path": str(home.parent / "elsewhere.txt")}, rule(home / "docs")
        )
    own = tmp_path / "localappdata"
    (own / "dev.mavo.desktop").mkdir(parents=True)
    (own / "dev.mavo.desktop" / "mavo.db").write_text("secret", encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA", str(own))
    whole_drive = rule(tmp_path)  # even when the person allows everything above it
    with pytest.raises(ValueError, match="Mavo's own data"):
        local_files.perform(
            "files.read", {"path": str(own / "dev.mavo.desktop" / "mavo.db")}, whole_drive
        )
    found = local_files.perform("files.find", {"query": "mavo"}, whole_drive)
    assert all("dev.mavo.desktop" not in f["path"] for f in found["files"])


def test_deleting_always_asks_and_keeps_the_chosen_folder_itself(home):
    assert "files.delete" in ALWAYS_ASKS and CATALOG["files.delete"][2] is True
    assert {"files.read", "files.find"} <= LOOKS_ONLY  # reading is only looking
    with pytest.raises(ValueError, match="allowed folder itself"):
        local_files.perform("files.delete", {"path": str(home)}, rule(home))


@pytest.mark.skipif(
    os.name != "nt" or os.environ.get("MAVO_TEST_RECYCLE_BIN") != "1",
    reason="uses the real Recycle Bin: set MAVO_TEST_RECYCLE_BIN=1 to run it",
)
def test_a_deleted_file_goes_to_the_recycle_bin(home):
    target = home / "docs" / "photo.jpg"
    result = local_files.perform("files.delete", {"path": str(target)}, rule(home))
    assert result["deleted"] and not target.exists()


def test_a_sequence_of_commands_is_one_script_reviewed_and_run_as_a_whole():
    args = TerminalArgs.model_validate({"steps": ["npm install", "npm test"], "folder": "."})
    assert args.shell == sequence_script(["npm install", "npm test"])
    assert "--- step 1 of 2: npm install" in args.shell and "exit $LASTEXITCODE" in args.shell
    assert TerminalArgs.model_validate({"shell": "dir", "folder": "."}).shell == "dir"
    with pytest.raises(ValueError):
        TerminalArgs.model_validate({"folder": "."})  # a command, a line or steps: one is needed
    assert Path(".").exists()
