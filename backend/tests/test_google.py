import asyncio
import hashlib
import json
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import select

from mavo.credentials import MemoryCredentialStore
from mavo.crew_workers import CrewWorkerRuntime
from mavo.database import (
    CrewInstance,
    CrewRun,
    GoogleAccount,
    ModelRoute,
    ResourceEvent,
    build_session_factory,
    utcnow,
)
from mavo.google_auth import GoogleAuth, GoogleConnectionError
from mavo.migrations import run_migrations
from mavo.resources.google import GoogleResourcePool
from tests.test_crew_workers import Router

CLIENT = "123-desktop.apps.googleusercontent.com"


@pytest.fixture
def google_env(tmp_path):
    url = f"sqlite:///{(tmp_path / 'google.db').as_posix()}"
    run_migrations(url)
    factory = build_session_factory(url)
    credentials = MemoryCredentialStore()
    credentials.set(
        "google-client", json.dumps({"client_id": CLIENT, "client_secret": "private-client"})
    )
    state = {"bad_nonce": False, "bad_audience": False, "token_requests": [], "stage": 0}
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
    public.update(kid="test-key", alg="RS256")
    auth = None

    async def handler(request):
        path = request.url.path
        if path == "/token":
            fields = parse_qs(request.content.decode())
            state["token_requests"].append(fields)
            if fields["grant_type"] == ["refresh_token"]:
                return httpx.Response(200, json={"access_token": "new-access", "expires_in": 3600})
            flow = dict(auth.pending)
            if state.get("token_error"):
                return httpx.Response(
                    400,
                    json={
                        "error": state["token_error"],
                        "error_description": state.get(
                            "token_error_description", "PRIVATE_PROVIDER_RESPONSE_MUST_NOT_LEAK"
                        ),
                    },
                )
            if state.get("token_entered"):
                state["token_entered"].set()
                await state["token_gate"].wait()
            claims = {
                "iss": "https://accounts.google.com",
                "aud": "wrong-client" if state["bad_audience"] else CLIENT,
                "sub": state.get("subject", "google-user-1"),
                "email": "test@example.com",
                "name": "Test User",
                "email_verified": True,
                "iat": int(time.time()),
                "exp": int(time.time()) + 3600,
                "nonce": "wrong-nonce" if state["bad_nonce"] else flow["nonce"],
            }
            if state.get("expired"):
                claims.update(iat=int(time.time()) - 3600, exp=int(time.time()) - 120)
            if state.get("issued_ahead_seconds"):
                claims["iat"] = int(time.time()) + state["issued_ahead_seconds"]
            token = jwt.encode(claims, private, algorithm="RS256", headers={"kid": "test-key"})
            result = {
                "id_token": token,
                "access_token": "test-access",
                "refresh_token": "test-refresh",
                "expires_in": 3600,
                "scope": " ".join(state.get("granted_scopes", flow["scopes"])),
            }
            if state.get("omit_refresh"):
                result.pop("refresh_token")
            if state.get("omit_identity"):
                result.pop("id_token")
            return httpx.Response(200, json=result)
        if path == "/oauth2/v3/certs":
            return httpx.Response(200, json={"keys": [public]})
        if path.endswith("/profile"):
            return httpx.Response(200, json={"historyId": "100"})
        if path in {"/calendar/v3/calendars/primary", "/tasks/v1/users/@me/lists"}:
            if state.get("calendar_disabled") and "calendar" in path:
                return httpx.Response(
                    403,
                    json={
                        "error": {
                            "details": [{"reason": "SERVICE_DISABLED"}],
                            "message": "PRIVATE_PROVIDER_RESPONSE_MUST_NOT_LEAK",
                        }
                    },
                )
            return httpx.Response(200, json={"id": "test-only", "items": []})
        if path.endswith("/history"):
            if state["stage"] == 0:
                return httpx.Response(200, json={"historyId": "100"})
            return httpx.Response(
                200,
                json={
                    "historyId": "101",
                    "history": [
                        {
                            "id": "101",
                            "messagesAdded": [{"message": {"id": "new", "labelIds": ["INBOX"]}}],
                        }
                    ],
                },
            )
        if path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": "old"}]})
        if "/messages/" in path:
            assert request.url.params.get("format") == "metadata"
            message_id = path.rsplit("/", 1)[-1]
            return httpx.Response(
                200,
                json={
                    "id": message_id,
                    "labelIds": ["INBOX"],
                    "internalDate": "1",
                    "payload": {
                        "headers": [
                            {"name": "From", "value": "admissions@college.edu"},
                            {"name": "Subject", "value": "Deadline reminder"},
                        ]
                    },
                },
            )
        if path.endswith("/events"):
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "event-1",
                            "etag": str(state["stage"]),
                            "summary": "Study session",
                            "start": {"dateTime": "2026-11-02T10:00:00+05:30"},
                        }
                    ]
                },
            )
        if path.endswith("/tasks"):
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "task-1",
                            "etag": str(state["stage"]),
                            "title": "Study",
                            "status": "needsAction",
                        }
                    ]
                },
            )
        return httpx.Response(404)

    auth = GoogleAuth(factory, credentials, httpx.MockTransport(handler))
    yield auth, credentials, factory, state
    factory.kw["bind"].dispose()


async def complete(auth, resources=()):
    started = await auth.start(list(resources))
    query = parse_qs(urlsplit(started["authorization_url"]).query)
    async with httpx.AsyncClient() as client:
        response = await client.get(
            query["redirect_uri"][0], params={"state": query["state"][0], "code": "test-code"}
        )
    return query, response


@pytest.mark.asyncio
async def test_missing_desktop_secret_has_a_publisher_diagnostic_not_a_generic_retry(google_env):
    auth, _, _, state = google_env
    state.update(token_error="invalid_request", token_error_description="client_secret is missing.")
    _, response = await complete(auth, ["gmail"])
    assert response.status_code == 400
    assert auth.status()["error_code"] == "missing_client_secret"
    assert "publisher" in auth.status()["error"].lower()
    assert len(state["token_requests"]) == 1
    await auth.close()


@pytest.mark.asyncio
async def test_packaged_google_flow_and_refresh_work_through_publisher_without_desktop_secret(
    google_env,
    tmp_path,
):
    from mavo.publisher_gateway import GatewaySettings, create_gateway

    auth, credentials, _, state = google_env
    upstream = auth.transport
    gateway = create_gateway(
        GatewaySettings(
            _env_file=None,
            data_dir=tmp_path / "publisher",
            firebase_project_id="test-project",
            google_client_id=CLIENT,
            google_client_secret="private-client",
        ),
        upstream,
    )
    broker = httpx.ASGITransport(app=gateway)
    wire = []

    async def route(request):
        if request.url.host == "core.example.test":
            wire.append(json.loads(request.content))
            return await broker.handle_async_request(request)
        return await upstream.handle_async_request(request)

    auth.transport = httpx.MockTransport(route)
    auth.publisher_config = {"client_id": CLIENT, "oauth_gateway_url": "https://core.example.test"}
    _, response = await complete(auth, ["gmail"])
    assert response.status_code == 200 and auth.status()["resources"] == ["gmail"]
    tokens = json.loads(credentials.get("google-tokens"))
    tokens["expires_at"] = 0
    credentials.set("google-tokens", json.dumps(tokens))
    assert await auth.access_token("gmail") == "new-access"
    assert len(wire) == 2 and all("client_secret" not in payload for payload in wire)
    assert all(fields["client_secret"] == ["private-client"] for fields in state["token_requests"])
    assert credentials.get("google-client") is not None  # Publisher config takes precedence.
    await auth.close()


@pytest.mark.asyncio
async def test_google_uses_system_browser_pkce_and_verifies_signed_identity(google_env):
    auth, credentials, _, state = google_env
    query, response = await complete(auth)
    assert response.status_code == 200
    assert query["redirect_uri"][0].startswith("http://127.0.0.1:")
    assert query["code_challenge_method"] == ["S256"]
    assert set(query["scope"][0].split()) == {"openid", "email", "profile"}
    verifier = state["token_requests"][0]["code_verifier"][0]
    import base64

    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    assert query["code_challenge"] == [expected]
    status = auth.status()
    assert status["signed_in"] and status["resources"] == []
    assert "test-access" not in json.dumps(status) and "private-client" not in json.dumps(status)
    assert credentials.get("google-tokens")
    await auth.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["bad_nonce", "bad_audience"])
async def test_google_rejects_invalid_identity_without_saving_tokens(google_env, failure):
    auth, credentials, _, state = google_env
    state[failure] = True
    _, response = await complete(auth)
    assert response.status_code == 400
    assert not credentials.get("google-tokens")
    assert not auth.status()["signed_in"] and auth.status()["phase"] == "error"
    await auth.close()


@pytest.mark.asyncio
async def test_invalid_callback_state_cannot_consume_login(google_env):
    auth, _, _, state = google_env
    started = await auth.start([])
    query = parse_qs(urlsplit(started["authorization_url"]).query)
    async with httpx.AsyncClient() as client:
        bad = await client.get(query["redirect_uri"][0], params={"state": "wrong", "code": "bad"})
        assert bad.status_code == 400 and auth.status()["phase"] == "pending"
        assert state["token_requests"] == []
        good = await client.get(
            query["redirect_uri"][0], params={"state": query["state"][0], "code": "good"}
        )
        assert good.status_code == 200
    assert len(state["token_requests"]) == 1
    await auth.close()


@pytest.mark.asyncio
async def test_sign_in_does_not_grant_service_access_and_refresh_stays_private(google_env):
    auth, credentials, _, _ = google_env
    await complete(auth)
    with pytest.raises(GoogleConnectionError, match="Connect Google gmail"):
        await auth.access_token("gmail")
    await complete(auth, ["gmail"])
    assert auth.status()["resources"] == ["gmail"]
    tokens = json.loads(credentials.get("google-tokens"))
    tokens["expires_at"] = 0
    credentials.set("google-tokens", json.dumps(tokens))
    assert await auth.access_token("gmail") == "new-access"
    assert "new-access" not in json.dumps(auth.status())
    await auth.disconnect()
    assert credentials.get("google-tokens") is None


@pytest.mark.asyncio
async def test_resource_refresh_is_not_a_mail_event_and_history_is_deduplicated(google_env):
    auth, _, factory, state = google_env
    await complete(auth, ["gmail"])
    pool = GoogleResourcePool(auth)
    first = await pool.read("gmail")
    assert first["items"][0]["id"] == "old"
    with factory() as session:
        assert session.scalars(select(ResourceEvent)).all() == []
    state["stage"] = 1
    pool.clear()
    second = await pool.read("gmail")
    assert second["items"][0]["id"] == "new"
    pool.clear()
    await pool.read("gmail")
    with factory() as session:
        events = session.scalars(select(ResourceEvent)).all()
        assert len(events) == 1 and events[0].payload["subject"] == "Deadline reminder"
        assert "body" not in events[0].payload
        assert session.get(GoogleAccount, "google").cursors["gmail"]["history_id"] == "101"
    await auth.disconnect()
    with factory() as session:
        assert session.scalars(select(ResourceEvent)).all() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["calendar", "tasks"])
async def test_calendar_and_tasks_only_emit_changes_after_baseline(google_env, resource):
    auth, _, factory, state = google_env
    await complete(auth, [resource])
    pool = GoogleResourcePool(auth)
    await pool.read(resource)
    with factory() as session:
        assert session.scalars(select(ResourceEvent)).all() == []
    state["stage"] = 1
    pool.clear()
    await pool.read(resource)
    with factory() as session:
        assert len(session.scalars(select(ResourceEvent)).all()) == 1
    await auth.close()


@pytest.mark.asyncio
async def test_mail_worker_only_uses_matching_events_and_stops_after_revocation(google_env):
    auth, _, factory, state = google_env
    await complete(auth, ["gmail"])
    pool = GoogleResourcePool(auth)
    await pool.read("gmail")
    with factory() as session:
        session.add(ModelRoute(id="route", name="Mail route", provider="ollama", model="test"))
        session.flush()
        session.add(
            CrewInstance(
                id="mail-crew",
                name="College mail",
                template_id="communication",
                responsibilities="Review college deadline headers",
                provider="ollama",
                model="test",
                route_id="route",
                trigger_type="gmail_message",
                is_running=True,
                resources=["google:gmail"],
                permissions=["model:invoke", "google:gmail:read"],
                mail_sender="college.edu",
                mail_subject="deadline",
            )
        )
        session.commit()
    router = Router()
    runtime = CrewWorkerRuntime(factory, router, pool)
    await runtime.work("mail-crew")
    assert not router.calls
    state["stage"] = 1
    pool.clear()
    pending = await runtime.prepare_event("mail-crew")
    with factory() as session:
        session.get(CrewInstance, "mail-crew").runtime_generation += 1
        session.commit()
    assert runtime.claim("mail-crew", utcnow(), pending) is None
    await runtime.work("mail-crew")
    assert len(router.calls) == 1
    assert "Deadline reminder" in router.calls[0][0][0]["content"]
    assert "UNTRUSTED DATA" in router.calls[0][1]
    with factory() as session:
        run = session.scalar(select(CrewRun))
        crew = session.get(CrewInstance, "mail-crew")
        assert run.kind == "google_event" and run.status == "completed"
        assert crew.runtime_state == "waiting_event" and crew.event_cursor > 0
    pool.clear()
    await runtime.work("mail-crew")
    assert len(router.calls) == 1  # No inference on a resource refresh or duplicate domain event.
    with factory() as session:
        session.add(
            ResourceEvent(
                account_subject="google-user-1",
                resource="gmail",
                deduplication_key="unrelated-mail",
                payload={"sender": "shop.example", "subject": "Sale"},
            )
        )
        session.commit()
    await runtime.work("mail-crew")
    assert len(router.calls) == 1  # Sender/subject filters are deterministic.
    with factory() as session:
        crew = session.get(CrewInstance, "mail-crew")
        crew.permissions = ["model:invoke"]
        session.commit()
    await runtime.work("mail-crew")
    with factory() as session:
        assert session.get(CrewInstance, "mail-crew").runtime_state == "needs_input"
    assert len(router.calls) == 1
    await auth.disconnect()
    with factory() as session:
        assert not session.get(CrewInstance, "mail-crew").is_running
        assert session.scalar(select(CrewRun)).result is None


@pytest.mark.asyncio
async def test_add_all_services_preserves_gmail_and_sync_cursor(google_env):
    auth, credentials, factory, state = google_env
    await complete(auth, ["gmail"])
    with factory() as session:
        session.get(GoogleAccount, "google").cursors = {"gmail": {"history_id": "1234"}}
        session.commit()
    state["omit_refresh"] = True
    query, response = await complete(auth, ["gmail", "calendar", "tasks"])
    assert response.status_code == 200
    assert query["login_hint"] == ["test@example.com"]
    assert auth.status()["resources"] == ["gmail", "calendar", "tasks"]
    assert auth.status()["missing_resources"] == []
    assert json.loads(credentials.get("google-tokens"))["refresh_token"] == "test-refresh"
    with factory() as session:
        assert session.get(GoogleAccount, "google").cursors["gmail"]["history_id"] == "1234"
    assert all(not scope.endswith(("modify", "send")) for scope in query["scope"][0].split())
    await auth.close()


@pytest.mark.asyncio
async def test_partial_consent_only_reports_granted_services(google_env):
    auth, _, _, state = google_env
    state["granted_scopes"] = [
        "openid",
        "email",
        "profile",
        "https://www.googleapis.com/auth/gmail.metadata",
    ]
    _, response = await complete(auth, ["gmail", "calendar", "tasks"])
    assert response.status_code == 200
    status = auth.status()
    assert status["phase"] == "complete" and status["resources"] == ["gmail"]
    assert status["missing_resources"] == ["calendar", "tasks"]
    with pytest.raises(GoogleConnectionError, match="Connect Google calendar"):
        await auth.access_token("calendar")
    await auth.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code",
    ["invalid_client", "invalid_grant", "access_denied", "invalid_scope", "untrusted-secret-error"],
)
async def test_failed_scope_upgrade_keeps_gmail_and_has_safe_diagnostics(google_env, code, caplog):
    auth, credentials, _, state = google_env
    await complete(auth, ["gmail"])
    before = credentials.get("google-tokens")
    state["token_error"] = code
    _, response = await complete(auth, ["gmail", "calendar", "tasks"])
    assert response.status_code == 400
    status = auth.status()
    expected = "token_exchange_failed" if code == "untrusted-secret-error" else code
    assert status["error_code"] == expected
    assert status["resources"] == ["gmail"] and status["signed_in"]
    assert credentials.get("google-tokens") == before
    assert "PRIVATE_PROVIDER_RESPONSE" not in json.dumps(status) + response.text + caplog.text
    assert "test-refresh" not in json.dumps(status) + response.text + caplog.text
    assert "untrusted-secret-error" not in caplog.text
    await auth.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario, expected",
    [
        ("subject", "account_mismatch"),
        ("omit_identity", "missing_identity"),
        ("bad_nonce", "nonce_mismatch"),
        ("bad_audience", "client_mismatch"),
        ("expired", "identity_expired"),
    ],
)
async def test_identity_failures_do_not_replace_existing_account(google_env, scenario, expected):
    auth, credentials, _, state = google_env
    await complete(auth, ["gmail"])
    before = credentials.get("google-tokens")
    state[scenario] = "different-google-user" if scenario == "subject" else True
    _, response = await complete(auth, ["calendar"])
    assert response.status_code == 400 and auth.status()["error_code"] == expected
    assert credentials.get("google-tokens") == before
    assert auth.status()["resources"] == ["gmail"]
    await auth.close()


@pytest.mark.asyncio
async def test_denied_consent_can_be_cancelled_and_retried_without_losing_gmail(google_env):
    auth, credentials, _, _ = google_env
    await complete(auth, ["gmail"])
    before = credentials.get("google-tokens")
    url = (await auth.start(["calendar"]))["authorization_url"]
    query = parse_qs(urlsplit(url).query)
    async with httpx.AsyncClient() as client:
        response = await client.get(
            query["redirect_uri"][0],
            params={
                "state": query["state"][0],
                "error": "access_denied",
                "error_description": "PRIVATE_PROVIDER_RESPONSE_MUST_NOT_LEAK",
            },
        )
    assert response.status_code == 400 and auth.status()["error_code"] == "consent_denied"
    await auth.cancel()
    assert auth.status()["phase"] == "idle" and auth.status()["error"] is None
    assert credentials.get("google-tokens") == before
    _, response = await complete(auth, ["calendar"])
    assert response.status_code == 200 and auth.status()["resources"] == ["gmail", "calendar"]
    await auth.close()


@pytest.mark.asyncio
async def test_cancel_during_exchange_cannot_save_a_late_grant(google_env):
    auth, credentials, _, state = google_env
    await complete(auth, ["gmail"])
    before = credentials.get("google-tokens")
    state.update(token_entered=asyncio.Event(), token_gate=asyncio.Event())
    url = (await auth.start(["calendar"]))["authorization_url"]
    query = parse_qs(urlsplit(url).query)
    async with httpx.AsyncClient() as client:
        task = asyncio.create_task(
            client.get(
                query["redirect_uri"][0],
                params={
                    "state": query["state"][0],
                    "code": "late-test-code",
                },
            )
        )
        await asyncio.wait_for(state["token_entered"].wait(), timeout=3)
        await asyncio.wait_for(auth.cancel(), timeout=3)
        state["token_gate"].set()
        response = await task
    assert response.status_code == 400 and auth.status()["phase"] == "idle"
    assert credentials.get("google-tokens") == before


@pytest.mark.asyncio
async def test_concurrent_connection_requests_keep_state_and_callback_consistent(google_env):
    auth, _, _, _ = google_env
    first, second = await asyncio.gather(auth.start(["gmail"]), auth.start(["calendar"]))
    query = parse_qs(urlsplit(second["authorization_url"]).query)
    assert query["state"][0] == auth.pending["state"]
    assert parse_qs(urlsplit(first["authorization_url"]).query)["state"] != query["state"]
    async with httpx.AsyncClient() as client:
        response = await client.get(
            query["redirect_uri"][0],
            params={
                "state": query["state"][0],
                "code": "consistent-test-code",
            },
        )
    assert response.status_code == 200 and auth.status()["resources"] == ["calendar"]
    await auth.close()


@pytest.mark.asyncio
async def test_connection_checks_do_not_claim_missing_scopes_are_connected(google_env):
    auth, _, _, _ = google_env
    await complete(auth, ["gmail"])
    result = await GoogleResourcePool(auth).check_connections()
    statuses = {s["resource"]: s["status"] for s in result["services"]}
    assert statuses == {
        "gmail": "connected",
        "calendar": "not_authorized",
        "tasks": "not_authorized",
        "drive": "not_authorized",
        "contacts": "not_authorized",
        "youtube": "not_authorized",
    }
    await auth.close()


@pytest.mark.asyncio
async def test_calendar_api_disabled_is_reported_separately_from_consent(google_env):
    auth, _, _, state = google_env
    await complete(auth, ["gmail", "calendar", "tasks"])
    state["calendar_disabled"] = True
    result = await GoogleResourcePool(auth).check_connections()
    calendar = next(s for s in result["services"] if s["resource"] == "calendar")
    assert calendar["status"] == "error" and "Enable Google Calendar API" in calendar["detail"]
    assert "PRIVATE_PROVIDER_RESPONSE" not in str(result)
    assert auth.status()["resources"] == ["gmail", "calendar", "tasks"]
    await auth.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("skew, expected_status", [(2, 200), (30, 200), (120, 400)])
async def test_identity_time_allows_small_skew_but_rejects_large_offsets(
    google_env,
    skew,
    expected_status,
):
    auth, credentials, _, state = google_env
    await complete(auth, ["gmail"])
    before = credentials.get("google-tokens")
    state["issued_ahead_seconds"] = skew
    _, response = await complete(auth, ["gmail", "calendar", "tasks"])
    assert response.status_code == expected_status
    if expected_status == 200:
        assert auth.status()["resources"] == ["gmail", "calendar", "tasks"]
    else:
        assert auth.status()["error_code"] == "clock_skew"
        assert credentials.get("google-tokens") == before
    await auth.close()
