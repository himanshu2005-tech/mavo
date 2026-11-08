import json

import httpx
import pytest
from sqlalchemy import select

from mavo.connection_revocation import read_receipt
from mavo.database import CrewInstance, GoogleAccount, WorkspaceSetting
from tests.test_github_auth import github as github_fixture
from tests.test_github_auth import phase
from tests.test_google import complete
from tests.test_google import google_env as google_fixture

google_env = google_fixture
github = github_fixture


@pytest.mark.asyncio
async def test_google_revoke_calls_fixed_provider_endpoint_clears_local_data_and_preserves_firebase(
    google_env,
):
    auth, credentials, factory, _ = google_env
    await complete(auth, ["gmail", "drive", "contacts"])
    credentials.set("firebase-tokens", "preserve-login")
    calls = []

    def handler(request):
        calls.append(request)
        assert str(request.url) == "https://oauth2.googleapis.com/revoke"
        assert request.method == "POST" and not request.url.query
        assert b"test-refresh" in request.content
        assert credentials.get("google-tokens") is None
        return httpx.Response(200)

    auth.transport = httpx.MockTransport(handler)
    receipt = await auth.revoke()
    assert receipt["remote_revoked"] and not receipt["requires_provider_action"]
    assert not auth.status()["signed_in"] and not auth.status()["capabilities"]
    assert credentials.get("firebase-tokens") == "preserve-login"
    with factory() as session:
        assert session.get(GoogleAccount, "google") is None
        assert read_receipt(session, "google")["remote_revoked"]
    # Repeated revoke remains idempotent and doesn't replace confirmed revocation with an unknown.
    assert (await auth.revoke())["remote_revoked"] and len(calls) == 1
    assert "test-refresh" not in json.dumps(receipt)


@pytest.mark.parametrize("status", [400, 302, 503])
@pytest.mark.asyncio
async def test_google_remote_failure_does_not_keep_local_access_or_claim_remote_revocation(
    google_env, status
):
    auth, credentials, _, _ = google_env
    await complete(auth, ["gmail"])
    auth.transport = httpx.MockTransport(
        lambda _: httpx.Response(
            status, headers={"location": "https://evil.example"}, text="PRIVATE_RESPONSE"
        )
    )
    receipt = await auth.revoke()
    assert receipt["local_disconnected"] and receipt["requires_provider_action"]
    assert not receipt["remote_revoked"] and not credentials.get("google-tokens")
    assert "PRIVATE_RESPONSE" not in json.dumps(receipt)
    assert receipt["manage_url"] == "https://myaccount.google.com/connections"


@pytest.mark.asyncio
async def test_google_revoke_network_failure_still_disconnects(google_env):
    auth, credentials, _, _ = google_env
    await complete(auth, ["gmail"])

    def fail(_):
        raise httpx.ConnectError("PRIVATE_NETWORK_DETAILS")

    auth.transport = httpx.MockTransport(fail)
    receipt = await auth.revoke()
    assert receipt["requires_provider_action"] and not credentials.get("google-tokens")
    assert "PRIVATE_NETWORK_DETAILS" not in json.dumps(receipt)


@pytest.mark.asyncio
async def test_google_revoke_removes_grants_and_new_consent_clears_receipt(
    google_env,
):
    auth, _, factory, state = google_env
    await complete(auth, ["drive"])
    with factory() as session:
        crew = CrewInstance(
            name="Review",
            template_id="research",
            responsibilities="Read file names",
            permissions=["model:invoke", "google:drive:read"],
            resources=["google:drive"],
            provider="ollama",
            model="fixture",
            is_running=True,
        )
        session.add(crew)
        session.commit()
        crew_id = crew.id
    upstream = auth.transport
    auth.transport = httpx.MockTransport(lambda _: httpx.Response(200))
    await auth.revoke()
    with factory() as session:
        saved = session.get(CrewInstance, crew_id)
        assert (
            not saved.is_running and saved.permissions == ["model:invoke"] and saved.resources == []
        )
        assert saved.checkpoint is None
    assert auth.status()["revocation"]
    auth.transport = upstream
    state["stage"] = 0
    await complete(auth, ["drive"])
    assert auth.status()["signed_in"] and auth.status()["revocation"] is None
    with factory() as session:
        assert session.get(CrewInstance, crew_id).permissions == ["model:invoke"]


def test_revocation_endpoints_are_authenticated_and_github_reports_manual_remote_step(client, auth):
    for path in ("/api/v1/auth/google/revoke", "/api/v1/connectors/github/revoke"):
        assert client.post(path).status_code == 401
    receipt = client.post("/api/v1/connectors/github/revoke", headers=auth).json()
    assert receipt["local_disconnected"] and not receipt["remote_revoked"]
    assert receipt["requires_provider_action"]
    assert receipt["manage_url"] == "https://github.com/settings/apps/authorizations"
    response = client.post("/api/v1/connectors/github/revocation/confirm", headers=auth)
    assert response.status_code == 200 and not response.json()["requires_provider_action"]
    assert not response.json()[
        "remote_revoked"
    ]  # user confirmation is not a fabricated API receipt
    assert "confirmed" in response.json()["message"]


def test_simple_github_connect_uses_authorized_selection_without_an_extra_save(github):
    client, auth, hub, connection, _, _ = github
    response = client.post("/api/v1/connectors/github/start", headers=auth, json={"simple": True})
    assert response.status_code == 200
    client.portal.call(phase, connection, "complete")
    result = hub.status("github")
    assert result["connected"] and result["repositories"] == ["owner/other", "owner/repo"]
    assert "github:issues:write" in result["capabilities"]
    assert not client.get("/api/v1/crew", headers=auth).json()  # connecting never creates Crew


def test_simple_github_waits_for_installation_then_connects_without_refresh_or_save(github):
    client, auth, hub, connection, state, _ = github
    state["installed"] = False
    original_sleep = connection.sleep

    async def install_on_provider(seconds):
        if seconds == 15:
            assert connection.status()["phase"] == "installing"
            state["installed"] = True
        await original_sleep(seconds)

    connection.sleep = install_on_provider
    client.post("/api/v1/connectors/github/start", headers=auth, json={"simple": True})
    client.portal.call(phase, connection, "complete")
    assert hub.status("github")["connected"]


def test_simple_github_does_not_silently_choose_arbitrary_repositories_over_the_limit(github):
    client, auth, hub, connection, state, _ = github
    state["repos"] = [{"full_name": f"owner/repo{i}"} for i in range(11)]
    client.post("/api/v1/connectors/github/start", headers=auth, json={"simple": True})
    client.portal.call(phase, connection, "select_repositories")
    assert len(connection.status()["repositories"]) == 11 and not hub.status("github")["connected"]


def test_simple_github_cancellation_during_installation_cannot_commit(github):
    client, auth, hub, connection, state, _ = github
    state["installed"] = False
    original_sleep = connection.sleep

    async def cancel_installation(seconds):
        if seconds == 15:
            await connection.close()
        else:
            await original_sleep(seconds)

    connection.sleep = cancel_installation
    client.post("/api/v1/connectors/github/start", headers=auth, json={"simple": True})
    client.portal.call(phase, connection, "idle")
    assert not hub.status("github")["connected"]


def test_google_revoke_removes_snapshot_without_touching_other_provider_settings(client, auth):
    runtime = client.app.state.crew_runtime
    with runtime.factory() as session:
        session.add(WorkspaceSetting(key="unrelated", value={"keep": True}))
        session.commit()
    assert client.post("/api/v1/auth/google/revoke", headers=auth).status_code == 200
    with runtime.factory() as session:
        assert session.scalar(select(WorkspaceSetting).where(WorkspaceSetting.key == "unrelated"))
