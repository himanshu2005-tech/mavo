"""Smoke-test the bundled Python sidecar with an isolated, temporary database."""
from __future__ import annotations

import os
import hashlib
import secrets
import socket
import sys
import subprocess
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    executable = ROOT / "src-tauri" / "binaries" / "mavo-backend" / "mavo-backend.exe"
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    token = secrets.token_urlsafe(32)
    with tempfile.TemporaryDirectory(prefix="packaged-smoke-", dir=ROOT / ".build") as directory:
        check_gate = "--require-login" in sys.argv
        environment = {**os.environ, "MAVO_API_TOKEN": token, "MAVO_DATA_DIR": directory, "MAVO_AUTOMATION_DIR": str(Path(directory) / "automation"), "MAVO_REQUIRE_LOGIN": "true" if check_gate else "false"}
        with (Path(directory) / "backend.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                [str(executable), "--host", "127.0.0.1", "--port", str(port)],
                env=environment, stdout=log, stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            try:
                with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=3) as client:
                    for _attempt in range(60):
                        if process.poll() is not None:
                            raise RuntimeError("Packaged backend stopped before becoming ready")
                        try:
                            if client.get("/health").status_code == 200:
                                break
                        except httpx.HTTPError:
                            pass
                        time.sleep(0.2)
                    else:
                        raise RuntimeError("Packaged backend did not become ready")
                    assert client.get("/api/v1/crew/templates").status_code == 401
                    client.headers["Authorization"] = f"Bearer {token}"
                    if check_gate:
                        for path in ("crew", "crew-drafts", "routes", "actions", "action-policies", "desktop-status", "core/conversation", "core/conversations", "auth/google", "resources/access", "resources/location"):
                            assert client.get("/api/v1/" + path).status_code == 401
                        status = client.get("/api/v1/auth/firebase")
                        assert client.post("/api/v1/core/conversations").status_code == 401
                        assert status.status_code == 200 and not status.json()["signed_in"]
                        print("Packaged backend: mandatory Firebase login blocks workspace access.")
                        return
                    assert len(client.get("/api/v1/crew/templates").json()) == 15
                    assert client.get("/api/v1/crew-runs").json() == []
                    assert client.get("/api/v1/reminders").json() == []
                    assert client.get("/api/v1/core/conversation").json()["conversation"] is None
                    assert client.get("/api/v1/core/conversations").json() == []
                    chats = [client.post("/api/v1/core/conversations").json() for _ in range(2)]
                    assert chats[0]["session_id"] != chats[1]["session_id"]
                    renamed = client.patch("/api/v1/conversations/" + chats[0]["id"], json={"title": "Packaged chat"})
                    assert renamed.status_code == 200 and renamed.json()["title"] == "Packaged chat"
                    assert len(client.get("/api/v1/core/conversations").json()) == 2
                    for chat in chats:
                        assert client.delete("/api/v1/conversations/" + chat["id"]).status_code == 204
                    assert client.get("/api/v1/core/conversations").json() == []
                    assert client.get("/api/v1/crew-drafts").json() == []
                    assert client.get("/api/v1/resources/access").json() == {"weather": False, "location": False}
                    assert client.get("/api/v1/resources/location").status_code == 403
                    assert client.get("/api/v1/resources/weather/location").status_code == 403
                    assert client.put("/api/v1/resources/access", json={"weather": False, "location": True}).status_code == 200
                    location = client.put("/api/v1/resources/location", json={"name": "Current location", "source": "device", "latitude": 17.385044, "longitude": 78.486671, "accuracy_m": 100})
                    assert location.status_code == 200 and location.json()["longitude"] == 78.487
                    assert client.put("/api/v1/resources/access", json={"weather": False, "location": False}).status_code == 200
                    assert client.get("/api/v1/resources/location").status_code == 403
                    policies = client.get("/api/v1/action-policies").json()
                    assert policies["actions"] and all(a["provider"]=="local" for a in policies["actions"])
                    manifests=client.get("/api/v1/action-policies?include_unavailable=true").json()["actions"]
                    assert any(a["operation"]=="github.create_issue" and not a["available"] for a in manifests)
                    assert not any(a["operation"].startswith(("outlook.","mscalendar.","mstasks.")) for a in manifests)
                    assert {p["provider"] for p in client.get("/api/v1/connectors").json()} == {"google", "github", "feeds"}
                    assert all(not p["connected"] for p in client.get("/api/v1/connectors").json())
                    terminal = next(a for a in policies["actions"] if a["operation"] == "terminal.run")
                    assert not terminal["rule"]["enabled"]
                    saved = client.put("/api/v1/action-policies",json={"operation":"terminal.run","rule":{"enabled":True,"mode":"unrestricted","folders":[directory]}})
                    assert saved.status_code==200
                    result = client.post("/api/v1/actions",json={"operation":"terminal.run","arguments":{"command":"python.version","folder":directory}}).json()
                    assert result["status"]=="completed" and result["result"]["stdout"].startswith("Python 3.")
                    assert client.put("/api/v1/action-policies",json={"operation":"windows.type","rule":{"enabled":True,"apps":["msedge.exe"]}}).status_code==422
                    if "--desktop" in sys.argv:
                        for operation in ("browser.open","browser.read","browser.close"):
                            configured=client.put("/api/v1/action-policies",json={"operation":operation,"rule":{"enabled":True,"mode":"unrestricted","domains":["example.com"]}})
                            assert configured.status_code==200
                        for operation, arguments in (("browser.open",{"url":"https://example.com"}),("browser.read",{}),("browser.close",{})):
                            response=client.post("/api/v1/actions",json={"operation":operation,"arguments":arguments},timeout=75)
                            response.raise_for_status()
                            action=response.json()
                            assert action["status"]=="completed", action.get("error")
                            if operation!="browser.close":
                                assert "Example Domain" in action["result"]["title"]
                        print("Packaged browser: real dedicated Edge open/read/close passed.")
                        profile=Path(directory)/"automation"/"browser"/hashlib.sha256(b"core").hexdigest()[:24]
                        assert (profile/"Default").is_dir(), "Browser profile must be isolated per assistant"
                    google = client.get("/api/v1/auth/google")
                    assert google.status_code == 200 and not google.json()["signed_in"]
                    assert not google.json()["developer_setup_allowed"]
                    assert client.put("/api/v1/auth/google/client", json={
                        "client_id": "unapproved.apps.googleusercontent.com",
                    }).status_code == 409
                    assert client.get("/api/v1/resources/google/gmail").status_code == 409
                    checks = client.post("/api/v1/auth/google/check").json()
                    assert len(checks["services"]) == 5
                    assert all(s["status"] == "not_authorized" for s in checks["services"])
                    if google.json()["configured"]:
                        consent = client.post("/api/v1/auth/google/start", json={
                            "all_services": True,
                        })
                        assert consent.status_code == 200
                        assert consent.json()["resources"] == ["gmail", "calendar", "tasks", "drive", "contacts"]
                        cancelled = client.post("/api/v1/auth/google/cancel")
                        assert cancelled.status_code == 200 and cancelled.json()["phase"] == "idle"
                    route = client.post("/api/v1/routes", json={
                        "name": "Smoke local", "provider": "ollama", "model": "test-only",
                    })
                    assert route.status_code == 201
                    crew = client.post("/api/v1/crew", json={
                        "name": "Packaged schedule", "template_id": "schedule",
                        "route_id": route.json()["id"],
                        "responsibilities": "Remind me every day at 10PM",
                    })
                    assert crew.status_code == 201 and crew.json()["schedule_time"] == "22:00"
                    assert not crew.json()["is_running"]
                    started = client.post(f"/api/v1/crew/{crew.json()['id']}/start")
                    assert started.status_code == 200 and started.json()["is_running"]
                    stopped = client.post(f"/api/v1/crew/{crew.json()['id']}/stop")
                    assert stopped.status_code == 200 and not stopped.json()["is_running"]
                    assert client.delete(f"/api/v1/crew/{crew.json()['id']}").status_code == 204
                    device = client.post("/api/v1/crew", json={
                        "name": "Packaged Computer", "template_id": "computer",
                        "responsibilities": "Check device readings", "route_id": route.json()["id"],
                        "allow_device_metrics": True,
                    }).json()
                    assert client.post(f"/api/v1/crew/{device['id']}/start").status_code == 200
                    for _ in range(40):
                        runs = client.get(f"/api/v1/crew-runs?crew_id={device['id']}").json()
                        if runs and runs[0]["status"] != "running":
                            break
                        time.sleep(0.2)
                    assert runs and runs[0]["status"] == "completed"
                    assert runs[0]["model_calls"] == 0 and "CPU:" in runs[0]["result"]
                    assert client.post(f"/api/v1/crew/{device['id']}/stop").status_code == 200
                    assert client.delete(f"/api/v1/crew/{device['id']}").status_code == 204
                print("Packaged backend: health, authentication, migrations, Google public-build "
                      "boundary, Core, 15 templates, routes, Crew Run/Stop, real device "
                      "monitoring, and reminders passed.")
            finally:
                try:
                    httpx.post(f"http://127.0.0.1:{port}/api/v1/runtime/close-tools",headers={"Authorization":"Bearer "+token},timeout=5)
                except httpx.HTTPError:
                    print("Tool cleanup request could not complete; stopping the owned sidecar.")
                process.terminate()
                process.wait(timeout=10)


if __name__ == "__main__":
    main()
