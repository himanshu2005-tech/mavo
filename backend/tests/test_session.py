import json
import time

from fastapi.testclient import TestClient

from mavo.config import Settings
from mavo.credentials import MemoryCredentialStore
from mavo.database import CrewInstance
from mavo.main import create_app


def test_workspace_requires_firebase_login_and_signout_stops_work(tmp_path):
    credentials = MemoryCredentialStore()
    settings = Settings(
        _env_file=None,
        api_token="test-session-token-at-least-24-characters",
        data_dir=tmp_path,
        workers_enabled=False,
        require_login=True,
        google_client_id=None,
        google_client_secret=None,
    )
    app = create_app(settings, credentials=credentials)
    firebase = app.state.firebase
    firebase.config = {"projectId": "test-project", "apiKey": "test-public-api-key-value"}
    firebase.config_error = None
    headers = {"Authorization": "Bearer " + settings.api_token}
    with TestClient(app) as client:
        assert client.get("/api/v1/crew", headers=headers).status_code == 401
        assert client.get("/api/v1/conversations", headers=headers).status_code == 401
        assert client.get("/api/v1/actions", headers=headers).status_code == 401
        assert (
            client.post(
                "/api/v1/auth/google/start", headers=headers, json={"all_services": True}
            ).status_code
            == 401
        )
        assert client.get("/api/v1/auth/firebase", headers=headers).status_code == 200
        # The credential repository is written only after signature verification,
        # which is tested independently with signed Google and Firebase identities.
        credentials.set(
            "firebase-tokens",
            json.dumps(
                {
                    "project_id": "test-project",
                    "uid": "owner",
                    "refresh_token": "private-test-refresh",
                    "expires_at": time.time() + 3600,
                }
            ),
        )
        assert client.get("/api/v1/crew", headers=headers).status_code == 200
        with app.state.crew_runtime.factory() as session:
            session.add(
                CrewInstance(
                    id="running",
                    name="Worker",
                    template_id="research",
                    provider="ollama",
                    model="test",
                    responsibilities="Research",
                    is_running=True,
                )
            )
            session.commit()
        credentials.set("google-tokens", "keep-connector-authorization")
        assert client.delete("/api/v1/auth/firebase", headers=headers).status_code == 204
        assert credentials.get("google-tokens") == "keep-connector-authorization"
        assert client.get("/api/v1/crew", headers=headers).status_code == 401
        assert client.get("/api/v1/resources/access", headers=headers).status_code == 401
        with app.state.crew_runtime.factory() as session:
            assert not session.get(CrewInstance, "running").is_running
