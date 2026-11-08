import sqlite3
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient

from mavo.publisher_gateway import GatewaySettings, create_gateway

CLIENT = "publisher-desktop.apps.googleusercontent.com"
CODE = "private-fixture-authorization-code"
REFRESH = "private-fixture-refresh-token"
SECRET = "publisher-only-fixture-secret"


@pytest.fixture
def broker(tmp_path):
    state = {"requests": [], "error": None, "scope": "openid email profile"}

    def google(request):
        assert request.url == "https://oauth2.googleapis.com/token"
        form = parse_qs(request.content.decode())
        assert form["client_secret"] == [SECRET]
        state["requests"].append(form)
        if state["error"]:
            return httpx.Response(400, json={"error": state["error"], "error_description": SECRET})
        return httpx.Response(
            200,
            json={
                "access_token": "private-fixture-access-token",
                "refresh_token": REFRESH,
                "id_token": "fixture-id-token",
                "expires_in": 3600,
                "scope": state["scope"],
                "client_secret": SECRET,
            },
        )

    settings = GatewaySettings(
        _env_file=None,
        data_dir=tmp_path,
        firebase_project_id="test-project",
        google_client_id=CLIENT,
        google_client_secret=SECRET,
        oauth_per_ip_minute=3,
    )
    app = create_gateway(settings, httpx.MockTransport(google))
    with TestClient(app) as client:
        yield client, state, settings


def code_request(**changes):
    return {
        "grant_type": "authorization_code",
        "client_id": CLIENT,
        "code": CODE,
        "code_verifier": "v" * 64,
        "redirect_uri": "http://127.0.0.1:49199/oauth/callback",
        **changes,
    }


@pytest.mark.parametrize("suffix", ["drive.metadata.readonly", "contacts.readonly"])
def test_broker_supports_only_implemented_google_extension_scopes(broker, suffix):
    client, state, _ = broker
    state["scope"] = "openid email https://www.googleapis.com/auth/" + suffix
    response = client.post("/v1/google/token", json=code_request())
    assert response.status_code == 200 and suffix in response.json()["scope"]
    assert SECRET not in response.text


@pytest.mark.parametrize("suffix", ["drive", "drive.readonly", "contacts"])
def test_broker_rejects_unimplemented_file_content_and_contact_write_scopes(broker, suffix):
    client, state, _ = broker
    state["scope"] = "openid https://www.googleapis.com/auth/" + suffix
    response = client.post("/v1/google/token", json=code_request())
    assert response.status_code == 400 and response.json()["error"] == "unsupported_permission"
    assert "access_token" not in response.json() and SECRET not in response.text


def test_publisher_exchange_uses_server_credential_without_persisting_tokens(broker):
    client, state, settings = broker
    response = client.post("/v1/google/token", json=code_request())
    assert response.status_code == 200 and response.json()["refresh_token"] == REFRESH
    assert SECRET not in response.text and CODE not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert "access-control-allow-origin" not in response.headers
    assert state["requests"][0]["code_verifier"] == ["v" * 64]
    with sqlite3.connect(settings.data_dir / "gateway.db") as db:
        dump = "\n".join(db.iterdump())
        assert all(
            value not in dump for value in (CODE, REFRESH, SECRET, "private-fixture-access-token")
        )


def test_refresh_returns_rotated_tokens_without_requiring_a_firebase_login(broker):
    client, state, _ = broker
    response = client.post(
        "/v1/google/token",
        json={
            "grant_type": "refresh_token",
            "client_id": CLIENT,
            "refresh_token": REFRESH,
        },
    )
    assert response.status_code == 200
    assert state["requests"][0]["refresh_token"] == [REFRESH]
    assert "code_verifier" not in state["requests"][0]


@pytest.mark.parametrize(
    "changes",
    [
        {"client_secret": "do-not-reflect-me"},
        {"code_verifier": "too-short"},
        {"redirect_uri": "https://example.com/oauth/callback"},
        {"redirect_uri": "http://localhost:49199/oauth/callback"},
        {"redirect_uri": "http://127.0.0.1:49199/other"},
        {"redirect_uri": "http://127.0.0.1:49199/oauth/callback?token=private"},
        {"redirect_uri": "http://user:password@127.0.0.1:49199/oauth/callback"},
        {"refresh_token": REFRESH},
        {"code": None},
    ],
)
def test_invalid_exchange_parameters_never_echo_private_inputs(broker, changes):
    client, state, _ = broker
    response = client.post("/v1/google/token", json=code_request(**changes))
    assert response.status_code == 422
    assert response.json() == {"error": "invalid_request"}
    assert all(value not in response.text for value in (CODE, REFRESH, SECRET, "do-not-reflect-me"))
    assert not state["requests"]


def test_unknown_oauth_client_cannot_use_the_publisher_credential(broker):
    client, state, _ = broker
    response = client.post(
        "/v1/google/token", json=code_request(client_id="other.apps.googleusercontent.com")
    )
    assert response.status_code == 503 and response.json()["error"] == "publisher_setup_required"
    assert not state["requests"]


def test_browser_origin_and_large_bodies_are_rejected_before_exchange(broker):
    client, state, _ = broker
    response = client.post(
        "/v1/google/token", json=code_request(), headers={"Origin": "https://attacker.example"}
    )
    assert response.status_code == 403
    response = client.post(
        "/v1/google/token", content="x" * 16385, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413
    response = client.post("/v1/google/token", content="x", headers={"Content-Type": "text/plain"})
    assert response.status_code == 415
    assert not state["requests"]


def test_connection_attempts_have_a_separate_durable_rate_limit(broker):
    client, state, _ = broker
    for _ in range(3):
        assert client.post("/v1/google/token", json=code_request()).status_code == 200
    limited = client.post("/v1/google/token", json=code_request())
    assert limited.status_code == 429 and limited.json()["error"] == "oauth_rate_limited"
    assert len(state["requests"]) == 3


def test_google_failure_is_sanitized_and_does_not_retry_a_consumed_code(broker):
    client, state, _ = broker
    state["error"] = "invalid_grant"
    response = client.post("/v1/google/token", json=code_request())
    assert response.json() == {"error": "invalid_grant"}
    assert len(state["requests"]) == 1 and SECRET not in response.text


def test_unsupported_returned_scopes_are_not_forwarded_to_the_desktop(broker):
    client, state, _ = broker
    state["scope"] = "https://www.googleapis.com/auth/drive"
    response = client.post("/v1/google/token", json=code_request())
    assert response.status_code == 400 and response.json()["error"] == "unsupported_permission"
    assert REFRESH not in response.text


def test_oauth_secrets_are_excluded_from_configuration_dumps(broker):
    _, _, settings = broker
    assert SECRET not in repr(settings)
    assert "google_client_secret" not in settings.model_dump()


def test_empty_publisher_example_fields_do_not_shadow_local_google_aliases(tmp_path, monkeypatch):
    for name in (
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
        "MAVO_GOOGLE_CLIENT_ID",
        "MAVO_GOOGLE_CLIENT_SECRET",
        "MAVO_PUBLISHER_GOOGLE_CLIENT_ID",
        "MAVO_PUBLISHER_GOOGLE_CLIENT_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    local = tmp_path / "local.env"
    local.write_text(
        "GOOGLE_CLIENT_ID=local.apps.googleusercontent.com\nGOOGLE_CLIENT_SECRET=private-local\n"
    )
    publisher = tmp_path / "publisher.env"
    publisher.write_text("MAVO_PUBLISHER_GOOGLE_CLIENT_ID=\nMAVO_PUBLISHER_GOOGLE_CLIENT_SECRET=\n")
    settings = GatewaySettings(_env_file=(local, publisher), firebase_project_id="test-project")
    assert settings.google_client_id == "local.apps.googleusercontent.com"
    assert settings.google_client_secret.get_secret_value() == "private-local"
