"""Test every worker through a real saved AIRouter route in an isolated database."""
import json
import argparse
import os
import secrets
import sqlite3
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from fastapi.testclient import TestClient
from mavo.config import Settings
from mavo.credentials import DpapiCredentialStore, MemoryCredentialStore
from mavo.crew_templates import CREW_TEMPLATES
from mavo.main import create_app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", choices=[t.id for t in CREW_TEMPLATES])
    parser.add_argument("--instructions")
    args = parser.parse_args()
    data = Path(os.environ["LOCALAPPDATA"]) / "dev.mavo.desktop"
    with sqlite3.connect((data / "mavo.db").as_uri() + "?mode=ro", uri=True) as db:
        model, credential = db.execute(
            "SELECT model, credential_id FROM model_routes WHERE provider='airouter' "
            "ORDER BY created_at LIMIT 1").fetchone()
    key = DpapiCredentialStore(data).get(credential)
    if not key:
        raise RuntimeError("The saved AIRouter route has no key")
    build = ROOT / ".build"
    build.mkdir(exist_ok=True)
    report = {"model": model, "workers": []}
    token = secrets.token_urlsafe(32)
    headers = {"Authorization": "Bearer " + token}
    with TemporaryDirectory(prefix="worker-verification-", dir=build) as directory:
        settings = Settings(require_login=False, data_dir=Path(directory), api_token=token)
        with TestClient(create_app(settings, credentials=MemoryCredentialStore())) as client:
            router = client.app.state.crew_runtime.router
            provider_chat = router.chat
            last_output = {}

            async def capture(*values, **options):
                value = await provider_chat(*values, **options)
                last_output["text"] = value
                return value

            router.chat = capture
            route = client.post("/api/v1/routes", headers=headers, json={
                "name": "Worker verification", "provider": "airouter",
                "model": model, "api_key": key,
            }).json()
            for template in CREW_TEMPLATES:
                if args.template and template.id != args.template:
                    continue
                payload = {
                    "name": "Verify " + template.name, "template_id": template.id,
                    "responsibilities": template.purpose + " Produce one short useful first step, "
                    "using only this instruction. Then wait for my input; no further work is needed.",
                    "route_id": route["id"], "daily_call_limit": 1,
                }
                if template.id == "schedule":
                    payload["schedule_time"] = "00:00"
                if template.id == "computer":
                    payload["allow_device_metrics"] = True
                if args.instructions:
                    payload["responsibilities"] = args.instructions
                response = client.post("/api/v1/crew", headers=headers, json=payload)
                assert response.status_code == 201
                crew = response.json()
                assert not crew["is_running"]
                started = client.post(f"/api/v1/crew/{crew['id']}/start", headers=headers)
                assert started.status_code == 200 and started.json()["is_running"]
                result = None
                deadline = time.monotonic() + 135
                while time.monotonic() < deadline:
                    if template.id == "schedule":
                        reminders = client.get("/api/v1/reminders/due", headers=headers).json()
                        if reminders:
                            result = {"status": "completed", "model_calls": 0}
                            break
                    else:
                        runs = client.get(f"/api/v1/crew-runs?crew_id={crew['id']}",
                                          headers=headers).json()
                        if runs and runs[0]["status"] != "running":
                            result = runs[0]
                            break
                    time.sleep(0.25)
                passed = result is not None and result["status"] == "completed"
                stopped = client.post(f"/api/v1/crew/{crew['id']}/stop", headers=headers)
                passed = passed and stopped.status_code == 200 and not stopped.json()["is_running"]
                item = {"template": template.id, "passed": passed,
                        "status": result.get("status") if result else "timeout",
                        "model_calls": result.get("model_calls") if result else None,
                        "error": result.get("error") if result else "No work result"}
                report["workers"].append(item)
                print(f"{template.name}: {'PASS' if passed else 'FAIL'}", flush=True)
                if not passed:
                    item["model_output"] = last_output.get("text")
                    print(item["error"], flush=True)
                assert client.delete(f"/api/v1/crew/{crew['id']}", headers=headers).status_code == 204
    (build / "worker-verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0 if all(item["passed"] for item in report["workers"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
