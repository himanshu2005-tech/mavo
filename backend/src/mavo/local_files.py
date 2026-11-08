"""Notes and files on this computer, inside the folders the person chose.

Every path is resolved (symlinks and ".." included) and must land inside one of the rule's
folders; a rule with no folders allows nothing, even "without limits". Nothing here deletes or
overwrites: notes are created or appended to, files are moved only where nothing has that name,
and a backup is a new zip beside the others.
"""

from __future__ import annotations

import os
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

NOTE_SUFFIXES = {".md", ".markdown", ".txt"}
SKIP_DIRS = {".git", ".obsidian", ".trash", "node_modules", "__pycache__", ".venv"}
MAX_SCAN = 5000  # files looked at by one search
MAX_NOTE_BYTES = 1_000_000
MAX_BACKUP_BYTES = 2 * 1024**3
MAX_BACKUP_FILES = 50_000


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class NotesFind(Args):
    """Search the notes (Markdown and text files) in the allowed folders by words in their name
    or text. Returns paths, titles and a short snippet; read a note for its full text."""

    query: str = Field(min_length=1, max_length=200)
    limit: int = Field(default=10, ge=1, le=25)


class NotesRead(Args):
    """Read one note. `path` is absolute, or relative to the first allowed folder."""

    path: str = Field(min_length=1, max_length=1024)


class NotesWrite(Args):
    """Write a note. `create` makes a new note and fails if one exists at that path; `append`
    adds text to the end of an existing note (or creates it). Never overwrites. `path` is
    absolute, or relative to the first allowed folder, and ends in .md."""

    path: str = Field(min_length=1, max_length=1024)
    text: str = Field(min_length=1, max_length=20000)
    mode: Literal["create", "append"] = "create"

    @field_validator("path")
    @classmethod
    def markdown(cls, value):
        if not value.lower().endswith((".md", ".markdown")):
            raise ValueError("Notes are Markdown files: end the path in .md")
        return value


class FilesList(Args):
    """List what is in a folder (names, sizes, when changed). `pattern` filters by name, like
    *.pdf."""

    folder: str = Field(min_length=1, max_length=1024)
    pattern: str = Field(default="*", min_length=1, max_length=100)
    limit: int = Field(default=100, ge=1, le=300)


class FilesMove(Args):
    """Move (or rename) a file or folder into another allowed folder. Fails if something already
    has that name there: nothing is overwritten, nothing is deleted."""

    source: str = Field(min_length=1, max_length=1024)
    destination_folder: str = Field(min_length=1, max_length=1024)
    new_name: str | None = Field(default=None, min_length=1, max_length=255)

    @field_validator("new_name")
    @classmethod
    def plain_name(cls, value):
        if value is not None and (value in {".", ".."} or any(c in value for c in '/\\:*?"<>|')):
            raise ValueError("Give a plain file name, without folders")
        return value


class FilesBackup(Args):
    """Zip a folder into a new dated archive in another allowed folder."""

    folder: str = Field(min_length=1, max_length=1024)
    destination_folder: str = Field(min_length=1, max_length=1024)


FILE_MODELS = {
    "notes.find": NotesFind,
    "notes.read": NotesRead,
    "notes.write": NotesWrite,
    "files.list": FilesList,
    "files.move": FilesMove,
    "files.backup": FilesBackup,
}


def roots(rule) -> list[Path]:
    folders = [Path(f).expanduser() for f in (rule or {}).get("folders") or []]
    if not folders:
        raise ValueError("Choose the folders this may use first (in its action rule).")
    return [f.resolve() for f in folders]


def inside(path: str, rule, *, must_exist=True) -> Path:
    """The resolved path, if it is inside an allowed folder."""
    allowed = roots(rule)
    raw = Path(path).expanduser()
    target = (raw if raw.is_absolute() else allowed[0] / raw).resolve(strict=False)
    if not any(target == root or target.is_relative_to(root) for root in allowed):
        raise ValueError("That path is outside the allowed folders")
    if must_exist and not target.exists():
        raise ValueError(f"Nothing at {target}")
    return target


def walk_notes(rule):
    seen = 0
    for root in roots(rule):
        for here, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
            for name in files:
                if Path(name).suffix.lower() in NOTE_SUFFIXES:
                    seen += 1
                    if seen > MAX_SCAN:
                        return
                    yield Path(here) / name


def read_text(path: Path) -> str:
    if path.stat().st_size > MAX_NOTE_BYTES:
        raise ValueError("That note is too large to read (over 1 MB)")
    return path.read_text(encoding="utf-8", errors="replace")


def find_notes(args, rule):
    words = args["query"].lower().split()
    hits = []
    for path in walk_notes(rule):
        try:
            if path.stat().st_size > MAX_NOTE_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        hay = (path.stem + "\n" + text).lower()
        if not all(w in hay for w in words):
            continue
        # The snippet: the line where the first word appears in the text.
        at = text.lower().find(words[0])
        start = max(0, text.rfind("\n", 0, at) + 1) if at >= 0 else 0
        snippet = text[start : start + 220].strip()
        score = sum(path.stem.lower().count(w) * 5 + hay.count(w) for w in words)
        hits.append((score, path.stat().st_mtime, path, snippet))
    hits.sort(key=lambda h: (-h[0], -h[1]))
    return {
        "notes": [
            {
                "path": str(p),
                "title": p.stem,
                "changed": datetime.fromtimestamp(m).isoformat(timespec="minutes"),
                "snippet": s,
            }
            for _, m, p, s in hits[: args.get("limit", 10)]
        ],
        "untrusted": True,
    }


def write_note(args, rule):
    target = inside(args["path"], rule, must_exist=False)
    mode = args.get("mode", "create")
    if target.exists() and mode == "create":
        raise ValueError("A note already exists there. Append to it, or choose another name.")
    if target.exists() and not target.is_file():
        raise ValueError("That path is a folder")
    target.parent.mkdir(parents=True, exist_ok=True)
    text = args["text"]
    if mode == "append" and target.exists():
        current = read_text(target)
        joiner = (
            ""
            if not current or current.endswith("\n\n")
            else "\n"
            if current.endswith("\n")
            else "\n\n"
        )
        with target.open("a", encoding="utf-8") as handle:
            handle.write(joiner + text + ("" if text.endswith("\n") else "\n"))
        return {"appended": True, "path": str(target)}
    with target.open("x", encoding="utf-8") as handle:
        handle.write(text + ("" if text.endswith("\n") else "\n"))
    return {"created": True, "path": str(target)}


def list_files(args, rule):
    folder = inside(args["folder"], rule)
    if not folder.is_dir():
        raise ValueError("That is a file, not a folder")
    items = []
    for entry in sorted(folder.glob(args.get("pattern", "*")), key=lambda p: p.name.lower()):
        if entry.parent != folder or entry.name.startswith("."):
            continue
        stat = entry.stat()
        items.append(
            {
                "name": entry.name,
                "path": str(entry),
                "folder": entry.is_dir(),
                "bytes": None if entry.is_dir() else stat.st_size,
                "changed": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="minutes"),
            }
        )
        if len(items) >= args.get("limit", 100):
            break
    return {"folder": str(folder), "items": items, "untrusted": True}


def move_file(args, rule):
    source = inside(args["source"], rule)
    if source in roots(rule):
        raise ValueError("An allowed folder itself cannot be moved")
    destination = inside(args["destination_folder"], rule, must_exist=False)
    if destination.exists() and not destination.is_dir():
        raise ValueError("The destination is a file, not a folder")
    if source.is_dir() and (destination == source or destination.is_relative_to(source)):
        raise ValueError("A folder cannot be moved into itself")
    target = destination / (args.get("new_name") or source.name)
    if target.exists():
        raise ValueError(f"{target.name} already exists there. Nothing was moved.")
    destination.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(target))
    return {"moved": True, "from": str(source), "to": str(target)}


def backup_folder(args, rule):
    folder = inside(args["folder"], rule)
    if not folder.is_dir():
        raise ValueError("Back up a folder, not a single file")
    destination = inside(args["destination_folder"], rule, must_exist=False)
    if destination == folder or destination.is_relative_to(folder):
        raise ValueError("Put the backup outside the folder being backed up")
    files, total = [], 0
    for here, dirs, names in os.walk(folder):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in names:
            path = Path(here) / name
            try:
                total += path.stat().st_size
            except OSError:
                continue
            files.append(path)
            if len(files) > MAX_BACKUP_FILES or total > MAX_BACKUP_BYTES:
                raise ValueError(
                    "That folder is too big to back up this way (over 2 GB or 50,000 files)"
                )
    destination.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M")
    target = destination / f"{folder.name}-{stamp}.zip"
    n = 1
    while target.exists():
        n += 1
        target = destination / f"{folder.name}-{stamp}-{n}.zip"
    with zipfile.ZipFile(target, "x", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(folder))
    return {"backup": str(target), "files": len(files), "bytes": target.stat().st_size}


def perform(operation, args, rule):
    """Runs one notes or files action (blocking: call it off the event loop)."""
    if operation == "notes.find":
        return find_notes(args, rule)
    if operation == "notes.read":
        target = inside(args["path"], rule)
        if not target.is_file() or target.suffix.lower() not in NOTE_SUFFIXES:
            raise ValueError("That is not a note (a .md or .txt file)")
        return {
            "path": str(target),
            "title": target.stem,
            "text": read_text(target)[:20000],
            "untrusted": True,
        }
    if operation == "notes.write":
        return write_note(args, rule)
    if operation == "files.list":
        return list_files(args, rule)
    if operation == "files.move":
        return move_file(args, rule)
    if operation == "files.backup":
        return backup_folder(args, rule)
    raise ValueError("Unknown files action")
