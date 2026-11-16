"""Saved files: attached in the chat or added on the Knowledge page, read as text for the chat
and agents, and the only files an agent may attach to a web form (a résumé on a job site)."""

import base64
import io
import zipfile
from types import SimpleNamespace

import mavo.knowledge_files as knowledge_files
from mavo.crew_planner import with_attachments


def docx(text: str) -> bytes:
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr(
            "word/document.xml",
            f"<w:document><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>",
        )
    return data.getvalue()


def upload(client, auth, name, content):
    return client.post(
        "/api/v1/files",
        headers=auth,
        json={"name": name, "data": base64.b64encode(content).decode()},
    )


def test_a_file_is_saved_read_listed_downloaded_replaced_and_removed(client, auth, tmp_path):
    saved = upload(client, auth, "Resume.docx", docx("Asha Python React"))
    assert saved.status_code == 201
    item = saved.json()
    assert item["name"] == "Resume.docx" and item["words"] == 3
    assert [f["name"] for f in client.get("/api/v1/files", headers=auth).json()] == ["Resume.docx"]
    got = client.get(f"/api/v1/files/{item['id']}", headers=auth)
    assert got.status_code == 200 and got.content.startswith(b"PK")
    # The same name again is a newer version, not a second copy.
    newer = upload(client, auth, "resume.docx", docx("Updated")).json()
    files = client.get("/api/v1/files", headers=auth).json()
    assert [f["id"] for f in files] == [newer["id"]]
    assert client.delete(f"/api/v1/files/{newer['id']}", headers=auth).status_code == 204
    assert client.get("/api/v1/files", headers=auth).json() == []


def test_only_document_and_image_files_are_kept(client, auth):
    refused = upload(client, auth, "setup.exe", b"MZ")
    assert refused.status_code == 400 and "PDF" in refused.json()["detail"]
    named = upload(client, auth, "..\\..\\notes.txt", b"hello").json()
    assert named["name"] == "notes.txt"  # never a path out of Mavo's folder


def test_an_agent_finds_a_saved_file_by_name_and_nothing_else(tmp_path):
    knowledge_files.add(tmp_path, "Resume.pdf", b"%PDF-1.4 not really")
    assert knowledge_files.find(tmp_path, "resume")[0]["name"] == "Resume.pdf"
    assert knowledge_files.find(tmp_path, "RESUME.PDF") is not None
    assert knowledge_files.find(tmp_path, "C:/Windows/win.ini") is None
    assert "Resume.pdf" in knowledge_files.files_block(tmp_path)
    assert knowledge_files.files_block(None) == ""


def test_an_attached_file_is_read_by_the_model_within_a_chat_message(tmp_path, monkeypatch):
    item = knowledge_files.add(tmp_path, "Cover.txt", b"I built Mavo, a desktop assistant.")
    monkeypatch.setattr(knowledge_files, "ACTIVE", tmp_path)
    payload = SimpleNamespace(message="Tighten my cover letter", attachments=[item["id"]])
    text = with_attachments(payload, [item])
    assert text.startswith("Tighten my cover letter")
    assert "ATTACHED FILE Cover.txt" in text and "I built Mavo" in text
    long = SimpleNamespace(message="x" * 7990, attachments=[item["id"]])
    assert len(with_attachments(long, [item])) <= 8000
