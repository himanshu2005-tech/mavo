"""Projects: a folder Mavo makes for a task, the text files an agent writes into it, and the scripts
(Python, Blender) it may run there once the person has said yes.

What is and is not a boundary here, plainly:
- Creating, writing and reading are confined to the project folder: plain names only, no `..`,
  no links or junctions, text file types only, small files, a bounded number of them. None of
  that runs anything.
- Running is the one step that executes code, and it runs with the person's own Windows rights.
  Mavo cannot sandbox that (Blender least of all). So a run waits for the person's approval, which
  is tied to a hash of the scripts they were shown, unless they have marked the project trusted.
  A run is bounded in time and output and gets a reduced environment (no keys or tokens).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import psutil
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MARKER = ".mavo-project.json"
RUN_LOG = ".mavo-run.log"
# Files an agent may write: text only, nothing a double-click would execute.
TEXT_SUFFIXES = {
    ".py",
    ".txt",
    ".md",
    ".json",
    ".csv",
    ".tsv",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".html",
    ".css",
    ".svg",
    ".xml",
    ".obj",
    ".mtl",
}
FILE_BYTES = 200_000
PROJECT_FILES = 200
PROJECT_BYTES = 5_000_000
READ_CHARS = 12_000
OUTPUT_CHARS = 6_000
RUN_LIMITS = {"python": 120, "blender": 600}
RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{n}" for n in range(1, 10)),
    *(f"lpt{n}" for n in range(1, 10)),
}
TOOLS = {"python": "python.exe", "blender": "blender.exe"}
NEEDS_APPROVAL = {"project.run"}
# What a script is given of the person's environment: enough to run, never keys or tokens.
ENVIRONMENT = {
    "PATH",
    "SYSTEMROOT",
    "WINDIR",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "PROGRAMDATA",
    "COMMONPROGRAMFILES",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
    "OS",
    "HOMEDRIVE",
    "HOMEPATH",
}


class ProjectArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ProjectCreate(ProjectArgs):
    name: str = Field(min_length=1, max_length=48)


class ProjectWrite(ProjectArgs):
    # A file is stored exactly as written: no stripping of the content (only of the names). The
    # parent's config is merged, not replaced, so stripping is switched off explicitly.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    project: str = Field(min_length=1, max_length=48)
    path: str = Field(min_length=1, max_length=160)
    content: str = Field(max_length=FILE_BYTES)

    @field_validator("project", "path")
    @classmethod
    def trimmed(cls, value):
        return value.strip()

    @model_validator(mode="before")
    @classmethod
    def content_from_the_reply(cls, data, info):
        """A file may arrive beside the JSON, as a fenced block labelled with its path, instead of
        inside it. Code inside a JSON string is easy to break (an unescaped quote, a missing
        comma) and small models break it often; raw code in a block cannot be broken that way.
        The content comes from the block only when the action does not carry its own."""
        files = (getattr(info, "context", None) or {}).get("files") or {}
        # The block is the real file. Models leave "content" empty, or put a placeholder such as
        # "see the attached file" in it, and the code in the block: a block for this path always
        # wins, so a placeholder is never saved as the script.
        if isinstance(data, dict):
            path = str(data.get("path", "")).strip().removeprefix("./")
            if path in files:
                return {**data, "content": files[path]}
        return data


class ProjectRead(ProjectArgs):
    project: str = Field(min_length=1, max_length=48)
    path: str | None = Field(default=None, max_length=160)  # none: list the files


class ProjectRun(ProjectArgs):
    project: str = Field(min_length=1, max_length=48)
    tool: Literal["python", "blender"]
    script: str | None = Field(default=None, max_length=160)  # a .py file in the project
    file: str | None = Field(default=None, max_length=160)  # a .blend file to open (Blender)
    show: bool = False  # Blender: open its window and leave it open
    args: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="before")
    @classmethod
    def forgive_the_obvious(cls, data):
        """Small models mix `script` and `file`, or ask Python to open a window. Where the intent
        is plain, take it that way instead of rejecting the whole reply."""
        if not isinstance(data, dict):
            return data
        data = dict(data)
        file = str(data.get("file") or "")
        if file.lower().endswith(".py"):  # a script given as the file to open
            data.setdefault("script", file) if not data.get("script") else None
            data.pop("file")
        if data.get("tool") == "python":
            data.pop("show", None)  # Python has no window to show
        return data

    @field_validator("args")
    @classmethod
    def plain_args(cls, values):
        if any(not 0 < len(v) <= 200 or any(ord(c) < 32 for c in v) for v in values):
            raise ValueError("Arguments must be short plain text")
        return values

    @model_validator(mode="after")
    def coherent(self):
        if self.tool == "python" and (not self.script or self.file or self.show):
            raise ValueError("Python needs a script, and cannot open a window or a file")
        if self.tool == "blender" and not (self.script or self.file):
            raise ValueError("Blender needs a script, a .blend file, or both")
        if self.script and not self.script.lower().endswith(".py"):
            raise ValueError("The script must be a .py file")
        if self.file and not self.file.lower().endswith(".blend"):
            raise ValueError("The file must be a .blend file")
        return self


PROJECT_MODELS = {
    "project.create": ProjectCreate,
    "project.write": ProjectWrite,
    "project.read": ProjectRead,
    "project.run": ProjectRun,
}


def slug_of(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40].strip("-")
    if not slug or slug in RESERVED:
        raise ValueError("Give the project a name with letters or numbers")
    return slug


def clean_path(path: str) -> list[str]:
    """A path inside a project: forward slashes, plain names, nothing that climbs or hides."""
    if "\\" in path or ":" in path or path.startswith("/") or any(ord(c) < 32 for c in path):
        raise ValueError("Use a plain relative path such as scripts/drone.py")
    parts = path.split("/")
    if not 1 <= len(parts) <= 5:
        raise ValueError("That path is too deep")
    for part in parts:
        if (
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._()-]{0,63}", part)
            or part.endswith((" ", "."))
            or part.split(".")[0].lower() in RESERVED
        ):
            raise ValueError("Use plain file names (letters, numbers, spaces, - _ .)")
    return parts


def is_link(path: Path) -> bool:
    """A symlink or a Windows junction: somewhere else wearing this folder's name."""
    try:
        info = path.lstat()
    except OSError:
        return False
    return path.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def kill_tree(pid: int) -> None:
    try:
        parent = psutil.Process(pid)
    except psutil.Error:
        return
    for child in parent.children(recursive=True):
        with contextlib.suppress(psutil.Error):
            child.kill()
    with contextlib.suppress(psutil.Error):
        parent.kill()


def version_key(path: Path):
    return [int(n) for n in re.findall(r"\d+", path.parent.name)]


def find_blender() -> str:
    found = shutil.which("blender")
    if found:
        return found
    candidates = []
    for var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = os.environ.get(var)
        if base:
            for folder in (
                Path(base) / "Blender Foundation",
                Path(base) / "Programs" / "Blender Foundation",
            ):
                candidates += folder.glob("Blender*/blender.exe")
    if not candidates:
        raise ValueError("Blender is not installed (blender.exe was not found)")
    return str(max(candidates, key=version_key))


def find_python() -> list[str]:
    found = shutil.which("python")
    if found and "windowsapps" not in found.lower():  # the Store stub opens the Store, not Python
        return [found]
    launcher = shutil.which("py")
    if launcher:
        return [launcher, "-3"]
    base = Path(sys.base_prefix) / "python.exe"
    if not getattr(sys, "frozen", False) and base.is_file():
        return [str(base)]
    raise ValueError("Python is not installed (python.exe was not found)")


def clean_output(raw: bytes) -> tuple[str, bool]:
    text = raw.decode("utf-8", errors="replace").replace("\r\n", "\n")
    text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text).strip()
    return text[-OUTPUT_CHARS:], len(text) > OUTPUT_CHARS


class Projects:
    """The files and runs of every project. One instance per desktop tool set."""

    def __init__(self, default_root: Path | None = None, children: set | None = None):
        self.default_root = default_root
        self.children = children if children is not None else set()
        self.windows: dict[tuple[str, str], int] = {}  # (project folder, file) -> Blender pid

    @staticmethod
    def window_open(pid: int | None) -> bool:
        try:
            return bool(pid) and "blender" in psutil.Process(pid).name().lower()
        except psutil.Error:
            return False

    # ---- where things are -------------------------------------------------------------------

    def root(self, rule: dict | None = None) -> Path:
        folders = (rule or {}).get("folders") or []
        if folders:
            base = Path(folders[0])
            if not base.is_dir():
                raise ValueError("The projects folder in this action's settings no longer exists")
            return base
        base = (
            self.default_root
            or Path(os.environ.get("USERPROFILE", Path.home())) / "Documents" / "Mavo Projects"
        )
        base.mkdir(parents=True, exist_ok=True)
        return base

    def project_dir(self, name: str, rule: dict | None = None, *, must_exist: bool = True) -> Path:
        root = self.root(rule).resolve()
        folder = root / slug_of(name)
        if must_exist and not (folder / MARKER).is_file():
            raise ValueError(f'There is no project "{name}". Create it first with project.create.')
        if folder.exists() and (is_link(folder) or not folder.resolve().is_relative_to(root)):
            raise ValueError("That project folder is not inside the projects folder")
        return folder

    def locate(self, folder: Path, path: str, *, must_exist: bool = False) -> Path:
        """A path inside a project, refusing anything that would leave it or pass a link."""
        parts = clean_path(path)
        current = folder
        for part in parts:
            current = current / part
            if is_link(current):
                raise ValueError("Links inside a project are not followed")
        if not current.resolve().is_relative_to(folder.resolve()):
            raise ValueError("That path is outside the project")
        if must_exist and not current.is_file():
            raise ValueError(f"{path} is not in this project")
        return current

    @staticmethod
    def files(folder: Path):
        """The project's own files. A link or junction is never entered: what is behind it is
        somewhere else, and must not be listed, hashed, previewed or counted as the project's."""
        out = []
        for current, dirs, names in os.walk(folder):
            here = Path(current)
            dirs[:] = sorted(d for d in dirs if not is_link(here / d))
            for name in sorted(names):
                path = here / name
                if name.startswith(".") or is_link(path):
                    continue
                out.append(path)
                if len(out) >= 2000:
                    return out
        return out

    def manifest(self, folder: Path) -> dict[str, tuple[int, int]]:
        result = {}
        for path in self.files(folder):
            with contextlib.suppress(OSError):
                info = path.stat()
                result[path.relative_to(folder).as_posix()] = (info.st_size, info.st_mtime_ns)
        return result

    # ---- the safe steps ---------------------------------------------------------------------

    def create(self, args, rule):
        folder = self.project_dir(args["name"], rule, must_exist=False)
        existed = (folder / MARKER).is_file()
        if folder.exists() and not existed and any(folder.iterdir()):
            raise ValueError("A folder with that name already exists and is not a Mavo project")
        folder.mkdir(parents=True, exist_ok=True)
        if not existed:
            (folder / MARKER).write_text(
                json.dumps({"name": args["name"], "created": datetime.now(UTC).isoformat()}),
                encoding="utf-8",
            )
        return {"project": folder.name, "folder": str(folder), "existed": existed}

    def write(self, args, rule):
        folder = self.project_dir(args["project"], rule)
        target = self.locate(folder, args["path"])
        if target.suffix.lower() not in TEXT_SUFFIXES:
            allowed = ", ".join(sorted(TEXT_SUFFIXES))
            raise ValueError(f"Only text files can be written here ({allowed})")
        data = args["content"].encode("utf-8")
        if b"\x00" in data or len(data) > FILE_BYTES:
            raise ValueError("The file must be text and under 200 KB")
        files = self.files(folder)
        existing = target.stat().st_size if target.is_file() else 0
        total = sum(p.stat().st_size for p in files) - existing + len(data)
        if (len(files) >= PROJECT_FILES and not target.is_file()) or total > PROJECT_BYTES:
            raise ValueError("The project is full (200 files or 5 MB)")
        target.parent.mkdir(parents=True, exist_ok=True)
        if is_link(target.parent):
            raise ValueError("Links inside a project are not followed")
        target.write_bytes(data)
        return {"written": args["path"], "bytes": len(data)}

    def read(self, args, rule):
        folder = self.project_dir(args["project"], rule)
        if not args.get("path"):
            listing = [
                {"path": p.relative_to(folder).as_posix(), "bytes": p.stat().st_size}
                for p in self.files(folder)
            ][:PROJECT_FILES]
            return {"project": folder.name, "files": listing}
        target = self.locate(folder, args["path"], must_exist=True)
        if target.suffix.lower() not in TEXT_SUFFIXES:
            raise ValueError("Only text files can be read; others are listed by name and size")
        text, cut = self.text_of(target, READ_CHARS)
        return {"path": args["path"], "text": text, "truncated": cut}

    @staticmethod
    def text_of(path: Path, limit: int):
        with path.open("rb") as handle:
            raw = handle.read(limit * 4 + 1)
        text = raw[: limit * 4].decode("utf-8", errors="replace")
        return text[:limit], len(text) > limit or len(raw) > limit * 4

    # ---- running ----------------------------------------------------------------------------

    def binding(self, args, rule) -> str:
        """A hash of every Python file in the project, plus the .blend to open. What the person
        approved is exactly this: if any of it changes, the approval no longer applies."""
        folder = self.project_dir(args["project"], rule)
        wanted = []
        if args.get("script"):
            wanted.append(self.locate(folder, args["script"], must_exist=True))
        if args.get("file"):
            wanted.append(self.locate(folder, args["file"], must_exist=True))
        digest = hashlib.sha256()
        entries = {p for p in self.files(folder) if p.suffix.lower() == ".py"} | set(wanted)
        for path in sorted(entries):
            digest.update(path.relative_to(folder).as_posix().encode() + b"\0")
            digest.update(hashlib.sha256(path.read_bytes()).digest())
        digest.update(
            json.dumps([args["tool"], args.get("show", False), args.get("args", [])]).encode()
        )
        return digest.hexdigest()

    def preview(self, args, rule) -> dict:
        """What the person is shown before saying yes: the tool, the entry script first, then the
        other Python files the approval also covers."""
        folder = self.project_dir(args["project"], rule)
        main = self.locate(folder, args["script"], must_exist=True) if args.get("script") else None
        sources = []
        ordered = ([main] if main else []) + [
            p for p in self.files(folder) if p.suffix.lower() == ".py" and p != main
        ]
        for path in ordered[:12]:
            text, cut = self.text_of(path, READ_CHARS)
            sources.append(
                {"path": path.relative_to(folder).as_posix(), "text": text, "truncated": cut}
            )
        try:
            tool_path = " ".join(self.tool_command(args["tool"], None))
        except ValueError as exc:
            tool_path = str(exc)
        return {
            "project": folder.name,
            "tool": args["tool"],
            "tool_path": tool_path,
            "script": args.get("script"),
            "file": args.get("file"),
            "show": bool(args.get("show")),
            "args": args.get("args", []),
            "sources": sources,
        }

    @staticmethod
    def tool_command(tool: str, apps: list[str] | None) -> list[str]:
        if apps is not None and TOOLS[tool] not in apps:
            raise ValueError(
                f"{TOOLS[tool]} is not allowed. Add it to this action's apps in Actions."
            )
        return find_python() if tool == "python" else [find_blender()]

    def environment(self) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if k.upper() in ENVIRONMENT}
        env.update(
            PYTHONIOENCODING="utf-8", PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1", NO_COLOR="1"
        )
        return env

    async def run(self, args, rule, guard=None):
        folder = self.project_dir(args["project"], rule)
        restricted = rule.get("mode") == "restricted"
        command = self.tool_command(args["tool"], rule.get("apps", []) if restricted else None)
        script = (
            self.locate(folder, args["script"], must_exist=True) if args.get("script") else None
        )
        blend = self.locate(folder, args["file"], must_exist=True) if args.get("file") else None
        extra = args.get("args", [])
        tool, show = args["tool"], bool(args.get("show"))
        if tool == "python":
            argv = [*command, "-E", "-s", str(script), *extra]
        else:
            argv = [command[0]]
            if not show:
                # Without --python-exit-code Blender exits 0 even when the script crashed, and
                # the agent would be told the run succeeded.
                argv += ["-b", "--factory-startup", "--python-exit-code", "1"]
            if blend:
                argv.append(str(blend))
            if script:
                argv += ["--python", str(script)]
            if extra:
                argv += ["--", *extra]
        if guard:
            guard()  # the access settings may have changed since the person said yes
        before = self.manifest(folder)
        if show:
            key = (str(folder), str(blend or script))
            if self.window_open(self.windows.get(key)):
                # Asked again for what is already on screen: say so, do not open a second copy.
                return {"opened": True, "already_open": True, "pid": self.windows[key]}
            result = self.open_window(argv, folder, before)
            self.windows[key] = result["pid"]
            return result
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=folder,
            env=self.environment(),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            **({"creationflags": 0x08000000} if os.name == "nt" else {}),
        )
        self.children.add(process)
        tail, timed_out = bytearray(), False
        try:
            try:
                async with asyncio.timeout(RUN_LIMITS[tool]):
                    while chunk := await process.stdout.read(8192):
                        tail.extend(chunk)
                        if len(tail) > 64_000:
                            del tail[: len(tail) - 32_000]
                    await process.wait()
            except TimeoutError:
                timed_out = True
                kill_tree(process.pid)
                await process.wait()
        finally:
            if process.returncode is None:
                kill_tree(process.pid)
                await process.wait()
            self.children.discard(process)
        output, cut = clean_output(bytes(tail))
        return {
            "exit_code": process.returncode,
            "timed_out": timed_out,
            "output": output,
            "truncated": cut,
            "changed_files": self.changed(folder, before),
        }

    def changed(self, folder: Path, before: dict) -> list[dict]:
        after = self.manifest(folder)
        return [
            {"path": rel, "bytes": size}
            for rel, (size, stamp) in after.items()
            if before.get(rel) != (size, stamp)
        ][:30]

    def open_window(self, argv, folder: Path, before: dict) -> dict:
        """Open Blender's window on the work and leave it open. Its messages go to a log in the
        project, so an error in the script can still be read back."""
        log = folder / RUN_LOG
        flags = 0x00000008 | 0x00000200 if os.name == "nt" else 0  # detached, own process group
        with log.open("wb") as sink:
            try:
                # Break away from Mavo's process boundary so the window outlives Mavo (unsaved
                # work in it must never be lost because Mavo quit). Refused where the boundary
                # does not allow it: then it simply closes with Mavo.
                process = subprocess.Popen(  # noqa: S603 - fixed tool, validated paths
                    argv,
                    cwd=folder,
                    env=self.environment(),
                    stdin=subprocess.DEVNULL,
                    stdout=sink,
                    stderr=subprocess.STDOUT,
                    creationflags=flags | 0x01000000 if os.name == "nt" else 0,
                )
                outlives = True
            except OSError:
                process = subprocess.Popen(  # noqa: S603
                    argv,
                    cwd=folder,
                    env=self.environment(),
                    stdin=subprocess.DEVNULL,
                    stdout=sink,
                    stderr=subprocess.STDOUT,
                    creationflags=flags,
                )
                outlives = False
        return {
            "opened": True,
            "pid": process.pid,
            "stays_open_after_mavo_quits": outlives,
            "log": RUN_LOG,
            "_before": before,
        }

    async def settle_window(self, result: dict, folder: Path) -> dict:
        """A few seconds after opening: did it start, and what did the script say?"""
        before = result.pop("_before", {})
        await asyncio.sleep(6)
        try:
            alive = psutil.Process(result["pid"]).is_running()
        except psutil.Error:
            alive = False
        output, cut = (
            clean_output((folder / RUN_LOG).read_bytes()[-64_000:])
            if (folder / RUN_LOG).is_file()
            else ("", False)
        )
        result.update(
            running=alive,
            output=output,
            truncated=cut,
            changed_files=self.changed(folder, before),
        )
        return result

    # ---- the one entry point ----------------------------------------------------------------

    async def perform(self, action, rule, guard=None):
        args = action.arguments
        match action.operation:
            case "project.create":
                return self.create(args, rule)
            case "project.write":
                return self.write(args, rule)
            case "project.read":
                return self.read(args, rule)
            case "project.run":
                result = await self.run(args, rule, guard)
                if result.get("opened") and not result.get("already_open"):
                    result = await self.settle_window(
                        result, self.project_dir(args["project"], rule)
                    )
                return result
        raise ValueError("Unknown project action")
