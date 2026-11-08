"""Real read-only device automation and durable reminders in an isolated workspace."""

import json
import secrets
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient
from mavo.config import Settings
from mavo.main import create_app


def main():
    root = Path(__file__).resolve().parents[1]
    token = secrets.token_urlsafe(32)
    with tempfile.TemporaryDirectory(
        prefix="automation-examples-", dir=root / ".build"
    ) as directory:
        app = create_app(
            Settings(
                _env_file=None,
                data_dir=Path(directory),
                workers_enabled=False,
                require_login=False,
                api_token=token,
            )
        )
        try:
            with TestClient(app) as client:
                headers = {"Authorization": f"Bearer {token}"}
                route = client.post(
                    "/api/v1/routes",
                    headers=headers,
                    json={
                        "name": "Unused local route",
                        "provider": "ollama",
                        "model": "unused",
                    },
                ).json()

                def create(name, **options):
                    response = client.post(
                        "/api/v1/crew",
                        headers=headers,
                        json={
                            "name": name,
                            "template_id": "computer",
                            "route_id": route["id"],
                            "responsibilities": "Read-only automation example",
                            **options,
                        },
                    )
                    assert response.status_code == 201, response.text
                    crew = response.json()
                    assert (
                        client.post(
                            f"/api/v1/crew/{crew['id']}/start", headers=headers
                        ).status_code
                        == 200
                    )
                    return crew

                drive = create(
                    "Example: C drive report",
                    review_interval_minutes=5,
                    allow_device_metrics=True,
                    allow_notifications=True,
                    device_monitor={
                        "kind": "storage",
                        "drive": "C:",
                        "notifications": "each_check",
                    },
                )
                runtime = app.state.crew_runtime
                client.portal.call(runtime.work, drive["id"])
                due = client.get("/api/v1/reminders/due", headers=headers).json()
                report = next(r for r in due if r["crew_id"] == drive["id"])
                assert "GB free" in report["message"] and "% free" in report["message"]
                assert (
                    client.post(
                        f"/api/v1/reminders/{report['id']}/dismiss", headers=headers
                    ).status_code
                    == 204
                )
                assert not client.get("/api/v1/reminders/due", headers=headers).json()
                client.portal.call(runtime.work, drive["id"])
                assert client.get("/api/v1/reminders/due", headers=headers).json()
                health = create(
                    "Example: quiet computer health",
                    allow_device_metrics=True,
                    device_monitor={"kind": "system", "notifications": "alerts"},
                )
                client.portal.call(runtime.work, health["id"])
                reminder = create(
                    "Example: daily watch reminder",
                    template_id="schedule",
                    trigger_type="daily",
                    schedule_time="00:00",
                )
                due = client.get("/api/v1/reminders/due", headers=headers).json()
                assert sum(r["crew_id"] == reminder["id"] for r in due) == 1
                again = client.get("/api/v1/reminders/due", headers=headers).json()
                assert len(due) == len(again)
                denied = create(
                    "Example: denied device access", allow_device_metrics=False
                )
                client.portal.call(runtime.work, denied["id"])
                crews = client.get("/api/v1/crew", headers=headers).json()
                assert (
                    next(c for c in crews if c["id"] == denied["id"])["runtime_state"]
                    == "needs_input"
                )
                runs = client.get("/api/v1/crew-runs", headers=headers).json()
                assert all(r["model_calls"] == 0 for r in runs)
                print(
                    json.dumps(
                        {
                            "workspace": "isolated",
                            "real_device_reads": True,
                            "examples": [
                                "C drive free-space reports + dismiss/redelivery",
                                "Quiet computer health",
                                "Daily reminder + deduplication",
                                "Denied device access",
                            ],
                            "model_calls": 0,
                            "external_writes": 0,
                        }
                    )
                )
        finally:
            app.state.crew_runtime.factory.kw["bind"].dispose()


if __name__ == "__main__":
    main()
