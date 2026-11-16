"""The person's saved files: a résumé, a cover letter, an ID scan. Attached in the chat or added
on the Knowledge page, kept in Mavo's data folder, read as text for the chat and agents, and the
ONLY files an agent may attach to a web form (browser.upload): "if I get new jobs, the bot opens
the links and fills in the application" needs the résumé, and nothing else on the PC.

The index lives next to the files (files/index.json), so the browser can find a file without a
database session."""

from __future__ import annotations

import base64
import json
import os
import re
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

ACTIVE: Path | None = None  # this app's data folder (main.py), for prompts and the browser
MAX_BYTES = 15 * 1024 * 1024
MAX_FILES = 200
TEXT_CHARS = 60000  # what is kept of a file's text for the chat and agents
KINDS = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".json": "application/json",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


class AddFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=160)
    data: str = Field(min_length=1, max_length=MAX_BYTES * 4 // 3 + 16)  # base64


def folder(data_dir: Path) -> Path:
    path = Path(data_dir) / "files"
    path.mkdir(parents=True, exist_ok=True)
    return path


def listing(data_dir: Path) -> list[dict]:
    index = folder(data_dir) / "index.json"
    try:
        items = json.loads(index.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [i for i in items if isinstance(i, dict) and i.get("id")]


def _write(data_dir: Path, items: list[dict]) -> None:
    index = folder(data_dir) / "index.json"
    temp = index.with_suffix(".tmp")
    temp.write_text(json.dumps(items, indent=1), encoding="utf-8")
    os.replace(temp, index)


def clean_name(name: str) -> str:
    """A plain file name: no folders, nothing Windows refuses."""
    base = Path(str(name).replace("\\", "/")).name
    base = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", base).strip(" .")
    return base[:160] or "file"


def extract_text(path: Path) -> str:
    """The words in a file, for a model to read. Images have none here."""
    suffix = path.suffix.lower()
    try:
        if suffix == ".pdf":
            from pypdf import PdfReader

            reader = PdfReader(str(path))
            return "\n".join((page.extract_text() or "") for page in reader.pages[:60])
        if suffix == ".docx":
            with zipfile.ZipFile(path) as archive:
                xml = archive.read("word/document.xml").decode("utf-8", "ignore")
            xml = re.sub(r"</w:p>", "\n", xml)
            return re.sub(r"<[^>]+>", "", xml)
        if suffix in {".txt", ".md", ".csv", ".json"}:
            return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:  # noqa: BLE001 - an unreadable file is still a saved file
        return ""
    return ""


def add(data_dir: Path, name: str, content: bytes) -> dict:
    name = clean_name(name)
    suffix = Path(name).suffix.lower()
    if suffix not in KINDS:
        raise ValueError("Mavo keeps PDF, Word (.docx), text, CSV, JSON and image files.")
    if len(content) > MAX_BYTES:
        raise ValueError("That file is over 15 MB.")
    items = listing(data_dir)
    if len(items) >= MAX_FILES:
        raise ValueError("Mavo already keeps 200 files. Remove some first.")
    file_id = str(uuid4())
    path = folder(data_dir) / f"{file_id}{suffix}"
    path.write_bytes(content)
    text = " ".join(extract_text(path).split())[:TEXT_CHARS]
    (folder(data_dir) / f"{file_id}.txt").write_text(text, encoding="utf-8") if text else None
    item = {
        "id": file_id,
        "name": name,
        "kind": KINDS[suffix],
        "size": len(content),
        "words": len(text.split()),
        "added_at": datetime.now(UTC).isoformat(),
    }
    # The same name again is a newer version of that file (an updated résumé).
    old = [i for i in items if i["name"].lower() == name.lower()]
    for item_old in old:
        remove_files(data_dir, item_old)
    _write(data_dir, [*[i for i in items if i not in old], item])
    return item


def remove_files(data_dir: Path, item: dict) -> None:
    base = folder(data_dir)
    for path in (
        base / f"{item['id']}{Path(item['name']).suffix.lower()}",
        base / f"{item['id']}.txt",
    ):
        path.unlink(missing_ok=True)


def find(data_dir: Path, name_or_id: str) -> tuple[dict, Path] | None:
    """A saved file by its id or name (case does not matter; a name without its extension
    too: "resume" finds "Resume.pdf")."""
    wanted = str(name_or_id or "").strip().lower()
    for item in reversed(listing(data_dir)):
        name = item["name"].lower()
        if wanted in {item["id"], name, Path(name).stem}:
            path = folder(data_dir) / f"{item['id']}{Path(item['name']).suffix.lower()}"
            return (item, path) if path.exists() else None
    return None


def text_of(data_dir: Path, item: dict, limit: int = TEXT_CHARS) -> str:
    path = folder(data_dir) / f"{item['id']}.txt"
    try:
        return path.read_text(encoding="utf-8")[:limit]
    except OSError:
        return ""


def files_block(data_dir: Path | None) -> str:
    """For a model's prompt: the files the person saved, which an agent may attach to a web
    form with browser.upload {ref, file: "<name>"}."""
    items = listing(data_dir) if data_dir else []
    if not items:
        return ""
    return (
        "\nThe person's saved files (attach one to a web form's file field with browser.upload "
        "{ref, file: name}; never any other file):\n"
        + "\n".join(f"- {i['name']}" for i in items[-30:])
    )


def build_files_api(data_dir: Path) -> APIRouter:
    router = APIRouter()

    @router.get("/files")
    def files():
        return list(reversed(listing(data_dir)))

    @router.post("/files", status_code=201)
    def save(payload: AddFile):
        try:
            content = base64.b64decode(payload.data, validate=True)
        except ValueError:
            raise HTTPException(400, "That file could not be read.") from None
        try:
            return add(data_dir, payload.name, content)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @router.get("/files/{file_id}")
    def download(file_id: str):
        found = find(data_dir, file_id)
        if not found or found[0]["id"] != file_id:
            raise HTTPException(404, "That file is not saved in Mavo")
        item, path = found
        return FileResponse(path, media_type=item["kind"], filename=item["name"])

    @router.delete("/files/{file_id}", status_code=204)
    def delete(file_id: str):
        items = listing(data_dir)
        item = next((i for i in items if i["id"] == file_id), None)
        if not item:
            raise HTTPException(404, "That file is not saved in Mavo")
        remove_files(data_dir, item)
        _write(data_dir, [i for i in items if i["id"] != file_id])

    return router
