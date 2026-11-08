"""Exercise real AIRouter calls in an isolated workspace database; never print credentials."""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sqlite3
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from fastapi.testclient import TestClient
from mavo.config import Settings
from mavo.credentials import DpapiCredentialStore, MemoryCredentialStore
from mavo.crew_templates import CREW_TEMPLATES
from mavo.main import create_app


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", help="Test a specific model without changing the saved route")
    parser.add_argument("--list-models", action="store_true")
    parser.add_argument("--repair-route", action="store_true",
                        help="After a successful connection test, replace an unsupported free alias")
    args = parser.parse_args()
    data = Path(os.environ["LOCALAPPDATA"]) / "dev.mavo.desktop"
    with sqlite3.connect(f"{(data / 'mavo.db').as_uri()}?mode=ro", uri=True) as db:
        row = db.execute(
            "SELECT name, model, credential_id, id FROM model_routes "
            "WHERE provider='airouter' ORDER BY created_at LIMIT 1"
        ).fetchone()
    if not row:
        print("No saved AIRouter route. Add one in Mavo Settings first.")
        return 1
    key = DpapiCredentialStore(data).get(row[2])
    if not key:
        print("The saved AIRouter route has no credential.")
        return 1
    if args.list_models:
        import httpx
        from mavo.model_router import PROVIDERS
        response = httpx.get(
            PROVIDERS["airouter"].base_url + "/models",
            headers={"Authorization": f"Bearer {key}"}, timeout=20,
        )
        print("Discovery status:", response.status_code)
        if response.status_code == 200:
            print(json.dumps([item["id"] for item in response.json().get("data", [])], indent=2))
        return 0 if response.status_code == 200 else 1
    model = args.model or row[1]
    build = ROOT / ".build"
    build.mkdir(exist_ok=True)
    report: dict = {"provider": "airouter", "model": model, "checks": []}
    token = secrets.token_urlsafe(32)
    auth = {"Authorization": f"Bearer {token}"}
    with TemporaryDirectory(prefix="airouter-verification-", dir=build) as directory:
        settings = Settings(require_login=False, data_dir=Path(directory), api_token=token)
        with TestClient(create_app(settings, credentials=MemoryCredentialStore())) as client:
            route = client.post("/api/v1/routes", headers=auth, json={
                "name": "Verification AIRouter", "provider": "airouter",
                "model": model, "api_key": key,
            }).json()
            connection = client.post(f"/api/v1/routes/{route['id']}/test", headers=auth)
            report["connection"] = {"http_status": connection.status_code,
                                    "result": connection.json()}
            print(f"AIRouter connection: HTTP {connection.status_code}", flush=True)
            if connection.status_code != 200:
                print(connection.json().get("detail", "Connection failed"))
                (build / "airouter-verification.json").write_text(
                    json.dumps(report, indent=2), encoding="utf-8")
                return 1
            if args.repair_route:
                if row[1] != "airouter/free" or not args.model:
                    print("Repair only applies to the unsupported airouter/free alias.")
                    return 1
                backup_path = build / "before-airouter-route-repair.db"
                with sqlite3.connect(data / "mavo.db") as source:
                    with sqlite3.connect(backup_path) as backup:
                        source.backup(backup)
                    source.execute(
                        "UPDATE model_routes SET model=?, updated_at=CURRENT_TIMESTAMP "
                        "WHERE id=? AND model=?", (model, row[3], row[1]),
                    )
                    source.execute(
                        "UPDATE crew_instances SET model=?, updated_at=CURRENT_TIMESTAMP "
                        "WHERE route_id=?", (model, row[3]),
                    )
                    source.execute(
                        "UPDATE provider_configs SET default_model=? "
                        "WHERE provider='airouter' AND default_model=?", (model, row[1]),
                    )
                print(f"Repaired route {row[0]}: {row[1]} -> {model}")
                print(f"Database backup: {backup_path}")
                return 0
            for template in CREW_TEMPLATES:
                crew = client.post("/api/v1/crew", headers=auth, json={
                    "template_id": template.id, "name": f"Verify {template.name}",
                    "responsibilities": template.purpose, "route_id": route["id"],
                    "max_model_calls": 1,
                }).json()
                response = client.post(f"/api/v1/crew/{crew['id']}/run", headers=auth, json={
                    "message": "In one short sentence, suggest a first step for your specialty. "
                               "Do not claim access to any accounts, files, or devices.",
                })
                result = response.json()
                passed = response.status_code == 200 and result.get("status") == "completed"
                check = {"template": template.id, "passed": passed,
                         "http_status": response.status_code,
                         "response": result.get("result"), "error": result.get("error")}
                report["checks"].append(check)
                print(f"{template.name}: {'PASS' if passed else 'FAIL'}", flush=True)
                if not passed:
                    print(result.get("error") or result.get("detail"), flush=True)
                paused = client.patch(f"/api/v1/crew/{crew['id']}", headers=auth,
                                      json={"status": "paused"})
                assert paused.status_code == 200
                assert client.post(f"/api/v1/crew/{crew['id']}/run", headers=auth,
                                   json={"message": "Must not run"}).status_code == 409
                assert client.delete(f"/api/v1/crew/{crew['id']}", headers=auth).status_code == 204
    (build / "airouter-verification.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8")
    return 0 if all(x["passed"] for x in report["checks"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
