"""Notes and files on this computer, inside the folders the person chose (a whole drive, if they
choose it).

Every path is resolved (symlinks and ".." included) and must land inside one of the rule's
folders; a rule with no folders allows nothing, even "without limits". Mavo's own data (its
database, sign-ins and keys) is never reachable, whatever folder is chosen. Nothing here
overwrites: notes and files are created or appended to, files are moved or copied only where
nothing has that name, a backup is a new zip beside the others, and deleting sends to the Recycle
Bin, only once the person says yes.
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


class FilesRead(Args):
    """Read a file's text: plain text, code, CSV, JSON, Markdown, PDF or Word (.docx). Up to
    `max_chars` characters, from `start` (to read a long file in parts)."""

    path: str = Field(min_length=1, max_length=1024)
    start: int = Field(default=0, ge=0)
    max_chars: int = Field(default=20000, ge=100, le=60000)


class FilesFind(Args):
    """Find files under `folder` (or all allowed folders): by words in their name, and, with
    `in_text`, inside text files too. Newest first. `pattern` narrows by name, like *.pdf."""

    query: str = Field(default="", max_length=200)
    folder: str | None = Field(default=None, max_length=1024)
    pattern: str = Field(default="*", min_length=1, max_length=100)
    in_text: bool = False
    limit: int = Field(default=25, ge=1, le=100)


class FilesCopy(Args):
    """Copy a file or folder into an allowed folder. Fails if something already has that name
    there: nothing is overwritten."""

    source: str = Field(min_length=1, max_length=1024)
    destination_folder: str = Field(min_length=1, max_length=1024)
    new_name: str | None = Field(default=None, min_length=1, max_length=255)

    @field_validator("new_name")
    @classmethod
    def plain_name(cls, value):
        if value is not None and (value in {".", ".."} or any(c in value for c in '/\\:*?"<>|')):
            raise ValueError("Give a plain file name, without folders")
        return value


class FilesWrite(Args):
    """Write a text file: `create` makes a new one (fails if one exists), `append` adds to the
    end (or creates it). Never replaces what is there."""

    path: str = Field(min_length=1, max_length=1024)
    text: str = Field(min_length=1, max_length=200000)
    mode: Literal["create", "append"] = "create"


class FilesDelete(Args):
    """Delete a file or folder: it goes to the Recycle Bin (it can be restored), and only once
    the person has said yes."""

    path: str = Field(min_length=1, max_length=1024)


FILE_MODELS = {
    "notes.find": NotesFind,
    "notes.read": NotesRead,
    "notes.write": NotesWrite,
    "files.list": FilesList,
    "files.move": FilesMove,
    "files.backup": FilesBackup,
    "files.read": FilesRead,
    "files.find": FilesFind,
    "files.copy": FilesCopy,
    "files.write": FilesWrite,
    "files.delete": FilesDelete,
}

TEXT_SUFFIXES = {
    ".txt",
    ".md",
    ".markdown",
    ".csv",
    ".json",
    ".log",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".html",
    ".css",
    ".xml",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".sql",
    ".sh",
    ".ps1",
    ".bat",
    ".java",
    ".c",
    ".cpp",
    ".h",
    ".cs",
    ".go",
    ".rs",
    ".rb",
}
MAX_READ_BYTES = 20 * 1024**2


def protected() -> list[Path]:
    """Mavo's own data: its database, the sign-ins and keys it keeps. Never an agent's to read,
    write or delete, whatever folder (or drive) the person allowed."""
    places = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        places += [Path(local) / "dev.mavo.desktop", Path(local) / "Mavo"]
    places.append(Path.home() / "AppData" / "Local" / "Mavo")
    return [p.resolve() for p in places]


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
    if any(target == own or target.is_relative_to(own) for own in protected()):
        raise ValueError("That is Mavo's own data: no agent may touch it")
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


def read_file(args, rule):
    target = inside(args["path"], rule)
    if not target.is_file():
        raise ValueError("That is a folder, not a file")
    if target.stat().st_size > MAX_READ_BYTES:
        raise ValueError("That file is too large to read (over 20 MB)")
    suffix = target.suffix.lower()
    if suffix in {".pdf", ".docx"}:
        from mavo.knowledge_files import extract_text

        text = extract_text(target)
        if not text.strip():
            raise ValueError("No text could be read from that file")
    elif suffix in TEXT_SUFFIXES or not suffix:
        text = target.read_text(encoding="utf-8", errors="replace")
    else:
        raise ValueError(f"{suffix} files are not text Mavo can read")
    start, size = args.get("start", 0), args.get("max_chars", 20000)
    part = text[start : start + size]
    return {
        "path": str(target),
        "text": part,
        "characters": len(text),
        "more": start + len(part) < len(text),
        "untrusted": True,
    }


def find_files(args, rule):
    places = [inside(args["folder"], rule)] if args.get("folder") else roots(rule)
    words = [w for w in str(args.get("query") or "").lower().split() if w]
    own = protected()
    found, seen = [], 0
    for place in places:
        if not place.is_dir():
            continue
        for here, dirs, names in os.walk(place):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
            if any(Path(here).resolve().is_relative_to(p) for p in own):
                dirs[:] = []
                continue
            for name in names:
                path = Path(here) / name
                if not path.match(args.get("pattern", "*")):
                    continue
                seen += 1
                if seen > MAX_SCAN * 4:
                    break
                hay = name.lower()
                if words and not all(w in hay for w in words):
                    if not args.get("in_text") or path.suffix.lower() not in TEXT_SUFFIXES:
                        continue
                    try:
                        if path.stat().st_size > MAX_NOTE_BYTES:
                            continue
                        hay = path.read_text(encoding="utf-8", errors="ignore").lower()
                    except OSError:
                        continue
                    if not all(w in hay for w in words):
                        continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                found.append((stat.st_mtime, path, stat.st_size))
    found.sort(key=lambda f: -f[0])
    return {
        "files": [
            {
                "path": str(p),
                "name": p.name,
                "bytes": size,
                "changed": datetime.fromtimestamp(m).isoformat(timespec="minutes"),
            }
            for m, p, size in found[: args.get("limit", 25)]
        ],
        "looked_at": seen,
        "untrusted": True,
    }


def copy_file(args, rule):
    source = inside(args["source"], rule)
    destination = inside(args["destination_folder"], rule, must_exist=False)
    if destination.exists() and not destination.is_dir():
        raise ValueError("The destination is a file, not a folder")
    if source.is_dir() and (destination == source or destination.is_relative_to(source)):
        raise ValueError("A folder cannot be copied into itself")
    target = destination / (args.get("new_name") or source.name)
    if target.exists():
        raise ValueError(f"{target.name} already exists there. Nothing was copied.")
    destination.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, target, ignore=shutil.ignore_patterns(*SKIP_DIRS))
    else:
        shutil.copy2(source, target)
    return {"copied": True, "from": str(source), "to": str(target)}


def write_file(args, rule):
    target = inside(args["path"], rule, must_exist=False)
    mode = args.get("mode", "create")
    if target.exists() and not target.is_file():
        raise ValueError("That path is a folder")
    if target.exists() and mode == "create":
        raise ValueError("A file already exists there. Add to it (append), or choose another name.")
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a" if mode == "append" else "x", encoding="utf-8") as handle:
        handle.write(args["text"])
    return {"written": True, "mode": mode, "path": str(target)}


def to_recycle_bin(path: Path) -> None:  # pragma: no cover - Windows' own Recycle Bin
    """Sends a file or folder to the Recycle Bin, where the person can restore it."""
    import ctypes
    from ctypes import wintypes

    class FileOp(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", ctypes.c_uint16),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    fo_delete, allow_undo, no_confirm, silent, no_errors = 3, 0x40, 0x10, 0x4, 0x400
    # The path list ends in two NULs: the string's own, and this one.
    op = FileOp(
        None,
        fo_delete,
        str(path) + chr(0),
        None,
        allow_undo | no_confirm | silent | no_errors,
        False,
        None,
        None,
    )
    if ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) or op.fAnyOperationsAborted:
        raise ValueError("Windows could not move it to the Recycle Bin")


def delete_file(args, rule):
    target = inside(args["path"], rule)
    if target in roots(rule):
        raise ValueError("An allowed folder itself cannot be deleted")
    if os.name != "nt":
        raise ValueError("Deleting to the Recycle Bin needs Windows")
    to_recycle_bin(target)
    if target.exists():
        raise ValueError("It is still there: Windows did not delete it")
    return {"deleted": True, "path": str(target), "restorable": "from the Recycle Bin"}


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
    if operation == "files.read":
        return read_file(args, rule)
    if operation == "files.find":
        return find_files(args, rule)
    if operation == "files.copy":
        return copy_file(args, rule)
    if operation == "files.write":
        return write_file(args, rule)
    if operation == "files.delete":
        return delete_file(args, rule)
    raise ValueError("Unknown files action")
