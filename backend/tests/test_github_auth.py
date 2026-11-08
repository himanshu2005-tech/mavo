import asyncio
import json
import time
from unittest.mock import AsyncMock
from urllib.parse import parse_qs

import httpx
import pytest

from mavo.connectors.hub import ConnectorError
from mavo.database import CrewInstance
from tests.test_connectors import make_crew


@pytest.fixture
def github(client, auth):
    runtime = client.app.state.crew_runtime
    hub, connection = runtime.connectors, runtime.connectors.github_auth
    connection.config = {"client_id": "Iv1.fixture", "app_slug": "mavo-fixture"}
    state = {
        "calls": [],
        "polls": [],
        "intervals": [],
        "user": 42,
        "permissions": {
            "metadata": "read",
            "issues": "write",
            "pull_requests": "read",
            "actions": "read",
        },
        "repos": [{"full_name": "owner/repo"}, {"full_name": "owner/other"}],
    }

    def handler(request):
        state["calls"].append(request)
        if request.url.host == "github.com":
            fields = parse_qs(request.content.decode())
            assert fields["client_id"] == ["Iv1.fixture"] and "client_secret" not in fields
            if request.url.path == "/login/device/code":
                return httpx.Response(
                    200,
                    json={
                        "device_code": "PRIVATE_DEVICE",
                        "user_code": "ABCD-EFGH",
                        "verification_uri": "https://github.com/login/device",
                        "expires_in": 900,
                        "interval": 5,
                        **state.get("device", {}),
                    },
                )
            state["polls"].append(fields)
            if state.get("poll_results"):
                return httpx.Response(200, json=state["poll_results"].pop(0))
            return httpx.Response(
                200,
                json={
                    "access_token": "ghu_PRIVATE_USER",
                    "refresh_token": "ghr_PRIVATE_REFRESH",
                    "expires_in": 28800,
                    "refresh_token_expires_in": 15897600,
                },
            )
        assert request.url.host == "api.github.com"
        if state.get("api_error"):
            return httpx.Response(state["api_error"], json={"message": "PRIVATE_PROVIDER_ERROR"})
        if request.url.path == "/user":
            return httpx.Response(200, json={"id": state["user"], "login": "fixture"})
        if request.url.path == "/user/installations":
            return httpx.Response(
                200,
                json={
                    "installations": [{"id": 1, "permissions": state["permissions"]}]
                    if state.get("installed", True)
                    else []
                },
            )
        if request.url.path == "/user/installations/1/repositories":
            return httpx.Response(200, json={"repositories": state["repos"]})
        if request.url.path == "/repos/owner/repo":
            return httpx.Response(200, json={"id": 1, "full_name": "owner/repo"})
        raise AssertionError("Unexpected provider endpoint")

    async def sleep(seconds):
        state["intervals"].append(seconds)
        if seconds >= 300:
            await asyncio.Future()
        else:
            await asyncio.sleep(0)

    connection.sleep = sleep
    hub.transport = httpx.MockTransport(handler)
    return client, auth, hub, connection, state, runtime


async def phase(connection, expected):
    for _ in range(200):
        if connection.status()["phase"] == expected:
            return
        await asyncio.sleep(0)
    raise AssertionError(
        "GitHub phase did not become " + expected + ": " + str(connection.status())
    )


def authorize(github):
    client, auth, _, connection, _, _ = github
    response = client.post("/api/v1/connectors/github/start", headers=auth)
    assert response.status_code == 200
    client.portal.call(phase, connection, "select_repositories")


def test_publisher_configuration_is_required_and_endpoint_is_protected(client, auth):
    connection = client.app.state.crew_runtime.connectors.github_auth
    connection.config = {}
    assert client.post("/api/v1/connectors/github/start").status_code == 401
    assert client.post("/api/v1/connectors/github/start", headers=auth).status_code == 409
    assert not client.get("/api/v1/connectors/github/auth", headers=auth).json()["configured"]


def test_device_authorization_commits_only_after_repository_review_and_tokens_never_reach_ui(
    github,
):
    client, auth, hub, connection, state, _ = github
    authorize(github)
    status = client.get("/api/v1/connectors/github/auth", headers=auth)
    assert "PRIVATE" not in status.text and not hub.status("github")["connected"]
    assert [r["full_name"] for r in status.json()["repositories"]] == ["owner/repo", "owner/other"]
    response = client.post(
        "/api/v1/connectors/github/authorize",
        headers=auth,
        json={"repositories": ["owner/repo"], "connector_capabilities": ["github:repo:read"]},
    )
    assert response.status_code == 200 and response.json()["auth_mode"] == "github_app"
    assert response.json()["repositories"] == ["owner/repo"] and "PRIVATE" not in response.text
    tokens = json.loads(hub.credentials.get("connector-github-tokens"))
    assert tokens["refresh_token"] == "ghr_PRIVATE_REFRESH"
    assert "PRIVATE" not in json.dumps(hub.account("github"))
    snapshot = client.get("/api/v1/connectors/github/repo", headers=auth)
    assert snapshot.status_code == 200 and snapshot.json()["items"][0]["full_name"] == "owner/repo"
    assert state["intervals"][0] == 5
    assert connection.status()["phase"] == "complete"


def test_app_connection_is_immediately_available_to_explicitly_authorized_crew(github):
    client, auth, _, _, _, runtime = github
    authorize(github)
    client.post(
        "/api/v1/connectors/github/authorize",
        headers=auth,
        json={"repositories": ["owner/repo"], "connector_capabilities": ["github:repo:read"]},
    )
    crew = make_crew(client, auth, ["github:repo:read"])
    runtime.router.chat = AsyncMock(
        return_value=json.dumps(
            {
                "state": "wait_for_next_review",
                "summary": "Repository verified",
                "next_step": "Review later",
            }
        )
    )
    client.portal.call(runtime.work, crew["id"])
    assert "owner/repo" in runtime.router.chat.await_args.args[2][0]["content"]


@pytest.mark.parametrize(
    "changes",
    [
        {"verification_uri": "https://evil.example"},
        {"interval": 0},
        {"expires_in": 99999},
        {"user_code": "<script>"},
        {"device_code": ""},
    ],
)
def test_malformed_device_response_never_opens_untrusted_urls_or_saves_tokens(github, changes):
    client, auth, hub, _, state, _ = github
    state["device"] = changes
    assert client.post("/api/v1/connectors/github/start", headers=auth).status_code == 409
    assert not hub.credentials.get("connector-github-tokens")


def test_pending_and_slow_down_are_polled_at_provider_intervals(github):
    _, _, _, connection, state, _ = github
    state["poll_results"] = [
        {"error": "authorization_pending"},
        {"error": "slow_down", "interval": 15},
    ]
    authorize(github)
    assert state["intervals"][:3] == [5, 5, 15]
    assert len(state["polls"]) == 3 and connection.status()["phase"] == "select_repositories"


@pytest.mark.parametrize("error", ["access_denied", "expired_token", "unverified_user_email"])
def test_declined_expired_or_failed_signin_does_not_replace_existing_account(github, error):
    client, auth, hub, connection, state, _ = github
    hub.save(
        "github",
        {
            "subject": "42",
            "email": "fixture",
            "capabilities": ["github:repo:read"],
            "repositories": ["owner/repo"],
        },
        {"access_token": "old-private-token"},
    )
    state["poll_results"] = [{"error": error, "error_description": "PRIVATE_RESPONSE"}]
    assert client.post("/api/v1/connectors/github/start", headers=auth).status_code == 200
    client.portal.call(phase, connection, "error")
    assert hub.status("github")["connected"]
    assert "old-private-token" in hub.credentials.get("connector-github-tokens")
    assert "PRIVATE" not in json.dumps(connection.status())


def test_cancel_during_discovery_discards_late_authorization(github):
    client, auth, hub, connection, _, _ = github
    original = connection.discover

    async def cancelled(token):
        await connection.close()
        return await original(token)

    connection.discover = cancelled
    client.post("/api/v1/connectors/github/start", headers=auth)
    client.portal.call(phase, connection, "idle")
    assert not hub.status("github")["connected"]


def test_repository_and_permission_selection_cannot_exceed_current_github_grants(github):
    client, auth, hub, _, state, _ = github
    authorize(github)
    for payload in (
        {"repositories": ["not/shared"], "connector_capabilities": ["github:repo:read"]},
        {"repositories": ["owner/repo"], "connector_capabilities": ["feeds:bbc-tech:read"]},
        {"repositories": ["owner/repo"], "connector_capabilities": []},
    ):
        assert (
            client.post(
                "/api/v1/connectors/github/authorize", headers=auth, json=payload
            ).status_code
            == 409
        )
    state["permissions"]["issues"] = "read"
    assert (
        client.post(
            "/api/v1/connectors/github/authorize",
            headers=auth,
            json={
                "repositories": ["owner/repo"],
                "connector_capabilities": ["github:issues:write"],
            },
        ).status_code
        == 409
    )
    state["repos"] = []
    assert (
        client.post(
            "/api/v1/connectors/github/authorize",
            headers=auth,
            json={"repositories": ["owner/repo"], "connector_capabilities": ["github:repo:read"]},
        ).status_code
        == 409
    )
    assert not hub.status("github")["connected"]


def test_installation_after_authorization_can_be_reloaded_without_tokens_or_app_keys(github):
    client, auth, _, connection, state, _ = github
    state["installed"] = False
    authorize(github)
    assert not connection.status()["repositories"]
    state["installed"] = True
    response = client.post("/api/v1/connectors/github/repositories", headers=auth)
    assert response.status_code == 200 and len(response.json()["repositories"]) == 2
    assert "PRIVATE" not in response.text


def test_account_mismatch_is_rejected_without_replacing_saved_connection(github):
    client, auth, hub, connection, state, _ = github
    hub.save(
        "github",
        {"subject": "99", "email": "previous", "repositories": [], "capabilities": []},
        {"access_token": "old-token"},
    )
    client.post("/api/v1/connectors/github/start", headers=auth)
    client.portal.call(phase, connection, "error")
    assert hub.account("github")["subject"] == "99" and len(state["calls"]) == 3


def test_expiring_tokens_refresh_once_under_concurrent_reads_and_rotate_privately(github):
    client, _, hub, connection, state, _ = github
    hub.save(
        "github",
        {
            "subject": "42",
            "email": "fixture",
            "repositories": ["owner/repo"],
            "capabilities": ["github:repo:read"],
            "auth_mode": "github_app",
        },
        {
            "auth_mode": "github_app",
            "client_id": "Iv1.fixture",
            "access_token": "ghu_old",
            "refresh_token": "ghr_old",
            "expires_at": 0,
            "refresh_expires_at": time.time() + 1000,
        },
    )

    async def concurrent():
        return await asyncio.gather(
            *(hub.access_token("github", "github:repo:read") for _ in range(4))
        )

    result = client.portal.call(concurrent)
    assert result == ["ghu_PRIVATE_USER"] * 4 and len(state["polls"]) == 1
    assert state["polls"][0]["grant_type"] == ["refresh_token"]
    assert (
        json.loads(hub.credentials.get("connector-github-tokens"))["refresh_token"]
        == "ghr_PRIVATE_REFRESH"
    )
    assert "PRIVATE" not in json.dumps(connection.status())


def test_revoked_refresh_blocks_work_and_never_retries_a_write(github):
    client, auth, hub, _, state, runtime = github
    hub.save(
        "github",
        {
            "subject": "42",
            "email": "fixture",
            "repositories": ["owner/repo"],
            "capabilities": ["github:repo:read"],
            "auth_mode": "github_app",
        },
        {
            "auth_mode": "github_app",
            "client_id": "Iv1.fixture",
            "access_token": "ghu_old",
            "refresh_token": "ghr_old",
            "expires_at": 0,
            "refresh_expires_at": time.time() + 1000,
        },
    )
    crew = make_crew(client, auth, ["github:repo:read"])
    state["poll_results"] = [{"error": "bad_refresh_token"}]
    with pytest.raises(ConnectorError):
        client.portal.call(hub.access_token, "github", "github:repo:read")
    assert not hub.status("github")["connected"]
    with runtime.factory() as session:
        assert not session.get(CrewInstance, crew["id"]).is_running


def test_disconnect_cancels_pending_signin_and_does_not_change_google(github):
    client, auth, hub, connection, _, _ = github
    authorize(github)
    hub.credentials.set("google-tokens", "preserved")
    assert client.delete("/api/v1/connectors/github", headers=auth).status_code == 204
    assert connection.status()["phase"] == "idle"
    assert hub.credentials.get("google-tokens") == "preserved"
    assert not hub.credentials.get("connector-github-tokens")


def test_saved_github_access_can_be_reviewed_without_reauthorizing(github):
    client, auth, _, _, state, _ = github
    authorize(github)
    client.post(
        "/api/v1/connectors/github/authorize",
        headers=auth,
        json={"repositories": ["owner/repo"], "connector_capabilities": ["github:repo:read"]},
    )
    polls = len(state["polls"])
    response = client.post("/api/v1/connectors/github/review", headers=auth)
    assert response.status_code == 200 and response.json()["phase"] == "select_repositories"
    assert len(state["polls"]) == polls and "PRIVATE" not in response.text


def test_changing_saved_repository_scope_fences_running_crew(github):
    client, auth, _, _, _, runtime = github
    authorize(github)
    client.post(
        "/api/v1/connectors/github/authorize",
        headers=auth,
        json={"repositories": ["owner/repo"], "connector_capabilities": ["github:repo:read"]},
    )
    crew = make_crew(client, auth, ["github:repo:read"])
    client.post("/api/v1/connectors/github/review", headers=auth)
    response = client.post(
        "/api/v1/connectors/github/authorize",
        headers=auth,
        json={"repositories": ["owner/other"], "connector_capabilities": ["github:repo:read"]},
    )
    assert response.status_code == 200
    with runtime.factory() as session:
        saved = session.get(CrewInstance, crew["id"])
        assert not saved.is_running and saved.checkpoint is None


def test_publisher_github_config_can_be_bundled_without_secrets(tmp_path, monkeypatch):
    from mavo.publisher import publisher_github_config

    monkeypatch.setattr("sys._MEIPASS", str(tmp_path), raising=False)
    (tmp_path / "publisher.json").write_text(
        json.dumps({"github": {"client_id": "Iv1.fixture", "app_slug": "mavo-fixture"}}),
        encoding="utf-8",
    )
    assert publisher_github_config() == {"client_id": "Iv1.fixture", "app_slug": "mavo-fixture"}
    with pytest.raises(ValueError):
        publisher_github_config("https://evil.example", "bad/slug")
    assert publisher_github_config("Iv1.new", "mavo-new")["client_id"] == "Iv1.new"
