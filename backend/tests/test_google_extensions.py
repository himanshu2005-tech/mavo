import json
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select

from mavo.database import GoogleAccount, ResourceEvent, WorkspaceSetting
from mavo.google_auth import GoogleConnectionError
from mavo.google_permissions import BASE, granted_capabilities
from mavo.resources.google import GoogleResourcePool
from mavo.resources.google_extended import reserve_read
from mavo.schemas import CrewCreateRequest, GoogleSignInRequest
from tests.test_google import complete
from tests.test_google import google_env as base_google_env

google_env = base_google_env


def test_extension_scopes_are_explicit_and_read_only():
    assert granted_capabilities([BASE + "gmail.metadata"]) == ["gmail:read"]
    assert granted_capabilities([BASE + "drive.metadata.readonly"]) == ["drive:read"]
    assert granted_capabilities([BASE + "contacts.readonly"]) == ["contacts:read"]
    request = GoogleSignInRequest(resources=["gmail", "calendar", "tasks", "drive", "contacts"])
    assert len(request.resources) == 5
    with pytest.raises(ValueError):
        CrewCreateRequest(
            name="Bad",
            template_id="research",
            responsibilities="Review files",
            route_id="fixture",
            google_capabilities=["drive:write"],
        )


@pytest.mark.asyncio
async def test_incremental_consent_preserves_old_grants_without_granting_every_service(google_env):
    auth, _, _, _ = google_env
    await complete(auth, ["gmail"])
    query, response = await complete(auth, ["drive"])
    assert response.status_code == 200
    assert set(auth.status()["resources"]) == {"gmail", "drive"}
    assert BASE + "drive.metadata.readonly" in query["scope"][0]
    assert "contacts" not in query["scope"][0]
    await auth.close()


@pytest.mark.parametrize("resource", ["drive", "contacts"])
@pytest.mark.asyncio
async def test_extension_snapshots_are_bounded_cached_and_have_no_contents(google_env, resource):
    auth, credentials, factory, _ = google_env
    await complete(auth, [resource])
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "GET"
        if resource == "drive":
            assert request.url.path == "/drive/v3/files"
            assert request.url.params["pageSize"] == "100"
            assert request.url.params["q"] == "trashed = false"
            assert "description" not in request.url.params["fields"]
            return httpx.Response(
                200,
                json={
                    "files": [
                        {
                            "id": str(i),
                            "name": "File",
                            "mimeType": "text/plain",
                            "description": "PRIVATE_BODY",
                            "content": "PRIVATE_BODY",
                        }
                        for i in range(120)
                    ],
                    "nextPageToken": "not-followed",
                },
            )
        assert request.url.path == "/v1/people/me/connections"
        assert request.url.params["sources"] == "READ_SOURCE_TYPE_CONTACT"
        assert request.url.params["personFields"] == "names,emailAddresses,phoneNumbers"
        return httpx.Response(
            200,
            json={
                "connections": [
                    {
                        "resourceName": f"people/{i}",
                        "names": [{"displayName": "Name"}],
                        "emailAddresses": [{"value": "name@example.test"}] * 5,
                        "phoneNumbers": [{"value": "+100"}] * 5,
                        "biographies": [{"value": "PRIVATE_BODY"}],
                    }
                    for i in range(120)
                ]
            },
        )

    pool = GoogleResourcePool(auth)
    pool.transport = httpx.MockTransport(handler)
    first = await pool.read(resource)
    assert len(first["items"]) == 100 and "PRIVATE_BODY" not in json.dumps(first)
    if resource == "contacts":
        assert len(first["items"][0]["emails"]) == 3
        assert len(first["items"][0]["phones"]) == 3
    assert await pool.read(resource) == first and len(requests) == 1
    with factory() as session:
        assert (
            session.get(WorkspaceSetting, "google-extension-budget-" + resource).value["calls"] == 1
        )
        assert not list(session.scalars(select(ResourceEvent)))
    tokens = json.loads(credentials.get("google-tokens"))
    tokens["scopes"] = []
    credentials.set("google-tokens", json.dumps(tokens))
    with pytest.raises(GoogleConnectionError, match="Connect Google"):
        await pool.read(resource)
    assert len(requests) == 1
    await auth.close()


@pytest.mark.asyncio
async def test_no_grant_means_no_external_request_or_daily_budget(google_env):
    auth, _, factory, _ = google_env
    await complete(auth, ["gmail"])
    pool = GoogleResourcePool(auth)
    pool.transport = httpx.MockTransport(lambda _: pytest.fail("Unauthorized network call"))
    for resource in ("drive", "contacts"):
        with pytest.raises(GoogleConnectionError):
            await pool.read(resource)
        with factory() as session:
            assert session.get(WorkspaceSetting, "google-extension-budget-" + resource) is None
    await auth.close()


@pytest.mark.asyncio
async def test_persistent_daily_ceiling_blocks_http_and_applies_to_connection_probes(google_env):
    auth, _, factory, _ = google_env
    await complete(auth, ["drive", "contacts"])
    for resource in ("drive", "contacts"):
        for _ in range(200):
            reserve_read(factory, resource)
    pool = GoogleResourcePool(auth)
    pool.transport = httpx.MockTransport(lambda _: pytest.fail("Budget exceeded HTTP"))
    for resource in ("drive", "contacts"):
        with pytest.raises(GoogleConnectionError, match="daily safety limit"):
            await pool.read(resource)
    status = await pool.check_connections()
    assert all(
        s["status"] == "error" for s in status["services"] if s["resource"] in {"drive", "contacts"}
    )
    await auth.close()


@pytest.mark.parametrize("resource,api_name", [("drive", "Drive API"), ("contacts", "People API")])
@pytest.mark.asyncio
async def test_disabled_api_has_safe_specific_diagnostic(google_env, resource, api_name):
    auth, _, _, _ = google_env
    await complete(auth, [resource])
    pool = GoogleResourcePool(auth)
    pool.transport = httpx.MockTransport(
        lambda _: httpx.Response(
            403,
            json={
                "error": {"details": [{"reason": "SERVICE_DISABLED"}], "message": "PRIVATE_ERROR"}
            },
        )
    )
    with pytest.raises(GoogleConnectionError, match=api_name) as failure:
        await pool.read(resource)
    assert "PRIVATE_ERROR" not in str(failure.value)
    await auth.close()


@pytest.mark.asyncio
async def test_malformed_contacts_are_reported_not_silently_coerced(google_env):
    auth, _, _, _ = google_env
    await complete(auth, ["contacts"])
    pool = GoogleResourcePool(auth)
    pool.transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200, json={"connections": [{"resourceName": "people/1", "names": None}]}
        )
    )
    with pytest.raises(GoogleConnectionError, match="invalid Contacts"):
        await pool.read("contacts")
    await auth.close()


@pytest.mark.asyncio
async def test_account_change_during_collection_discards_snapshot(google_env):
    auth, _, factory, _ = google_env
    await complete(auth, ["drive"])

    def handler(_):
        with factory() as session:
            session.get(GoogleAccount, "google").subject = "replacement-account"
            session.commit()
        return httpx.Response(200, json={"files": [{"id": "private-file"}]})

    pool = GoogleResourcePool(auth)
    pool.transport = httpx.MockTransport(handler)
    with pytest.raises(GoogleConnectionError, match="connection changed"):
        await pool.read("drive")
    assert not pool.cache
    await auth.close()


def test_worker_sends_only_explicit_google_extension_grants(client, auth):
    runtime = client.app.state.crew_runtime
    pool = runtime.google_pool
    runtime.connectors.google.status = lambda: {
        "configured": True,
        "signed_in": True,
        "resources": ["drive", "contacts"],
        "capabilities": ["drive:read", "contacts:read"],
    }
    route = client.post(
        "/api/v1/routes",
        headers=auth,
        json={"name": "Fixture", "provider": "ollama", "model": "fixture"},
    ).json()
    crew = client.post(
        "/api/v1/crew",
        headers=auth,
        json={
            "name": "Metadata review",
            "template_id": "research",
            "route_id": route["id"],
            "responsibilities": "Review file names",
            "trigger_type": "interval",
            "google_capabilities": ["drive:read"],
        },
    ).json()
    assert client.post(f"/api/v1/crew/{crew['id']}/start", headers=auth).status_code == 200
    pool.read = AsyncMock(return_value={"items": [{"name": "Authorized file"}]})
    runtime.router.chat = AsyncMock(
        return_value=json.dumps(
            {"state": "wait_for_next_review", "summary": "Reviewed", "next_step": "Later"}
        )
    )
    client.portal.call(runtime.work, crew["id"])
    pool.read.assert_awaited_once_with("drive")
    with runtime.factory() as session:
        from mavo.database import CrewInstance

        assert runtime.router.chat.await_args, session.get(
            CrewInstance, crew["id"]
        ).last_runtime_error
    assert "Authorized file" in runtime.router.chat.await_args.args[2][0]["content"]
