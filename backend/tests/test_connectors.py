import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from mavo.database import ActionRequest, CrewInstance, GoogleAccount, WorkspaceSetting


@pytest.fixture
def connected_env(client, auth):
    runtime = client.app.state.crew_runtime
    hub = runtime.connectors
    state = {"requests": [], "writes": [], "items": {}, "fail": None}

    def handler(request):
        state["requests"].append(request)
        assert request.url.host == "api.github.com"
        path = request.url.path
        if path == "/user":
            return httpx.Response(200, json={"id": 42, "login": "fixture"})
        if request.method == "GET":
            if path in state["items"]:
                return httpx.Response(200, json=state["items"][path])
            if path == "/repos/owner/repo":
                return httpx.Response(200, json={"id": 1, "full_name": "owner/repo"})
            if path == "/repos/owner/repo/issues":
                return httpx.Response(
                    200,
                    json=[
                        {"id": 5, "number": 1, "title": "Fixture issue", "state": "open"},
                        {"id": 6, "number": 2, "title": "Not an issue", "pull_request": {}},
                    ],
                )
            if path == "/repos/owner/repo/pulls":
                return httpx.Response(
                    200,
                    json=[
                        {
                            "id": 12,
                            "number": 8,
                            "title": "Fix build",
                            "state": "open",
                            "draft": False,
                            "html_url": "https://github.com/owner/repo/pull/8",
                            "body": "PRIVATE_RAW_DIFF",
                            "diff_url": "https://untrusted.example/patch",
                        }
                    ],
                )
            if path == "/repos/owner/repo/actions/runs":
                return httpx.Response(
                    200,
                    json={
                        "workflow_runs": [
                            {
                                "id": 21,
                                "name": "CI",
                                "status": "completed",
                                "conclusion": "failure",
                                "html_url": "https://github.com/owner/repo/actions/runs/21",
                                "head_branch": "main",
                                "logs": "PRIVATE_JOB_LOG",
                                "artifacts_url": "https://untrusted.example/download",
                            }
                        ]
                    },
                )
            return httpx.Response(404)
        state["writes"].append(request)
        if state["fail"]:
            return httpx.Response(state["fail"], json={"error": "SECRET MUST NOT LEAK"})
        payload = json.loads(request.content)
        if path == "/repos/owner/repo/issues":
            result = {"id": 7, "number": 3, **payload}
            state["items"][path + "/3"] = result
        elif path.endswith("/comments"):
            result = {"id": 9, **payload}
            state["items"]["/repos/owner/repo/issues/comments/9"] = result
        else:
            raise AssertionError("Unexpected write")
        return httpx.Response(201, json=result)

    hub.transport = httpx.MockTransport(handler)

    def seed(provider, caps):
        hub.save(
            provider,
            {
                "subject": "42",
                "email": "fixture",
                "capabilities": caps,
                "repositories": ["owner/repo"],
            },
            {"access_token": "fixture-access"},
        )

    return client, auth, runtime, hub, state, seed


@pytest.mark.parametrize(
    "resource,path",
    [
        ("pulls", "/repos/owner/repo/pulls"),
        ("workflows", "/repos/owner/repo/actions/runs"),
    ],
)
def test_new_github_read_plugins_have_separate_grants_bounded_data_and_no_writes(
    connected_env,
    resource,
    path,
):
    client, auth, _, hub, state, seed = connected_env
    seed("github", [f"github:{resource}:read"])
    response = client.get(f"/api/v1/connectors/github/{resource}", headers=auth)
    assert response.status_code == 200
    snapshot = response.json()
    assert snapshot["untrusted"] and snapshot["resource"] == resource
    assert snapshot["items"][0]["repository"] == "owner/repo"
    assert "PRIVATE_" not in json.dumps(snapshot) and "untrusted.example" not in json.dumps(
        snapshot
    )
    assert state["requests"][0].url.path == path
    assert state["requests"][0].url.params["per_page"] == "20"
    assert client.get(f"/api/v1/connectors/github/{resource}", headers=auth).json() == snapshot
    assert len(state["requests"]) == 1 and not state["writes"]
    assert "github.create_issue" not in {
        a["operation"]
        for a in client.get("/api/v1/action-policies", headers=auth).json()["actions"]
    }
    assert hub.status("github")["capabilities"] == [f"github:{resource}:read"]


@pytest.mark.parametrize("resource", ["pulls", "workflows"])
def test_existing_github_account_does_not_implicitly_grant_new_plugins(connected_env, resource):
    client, auth, _, _, state, seed = connected_env
    seed("github", ["github:repo:read", "github:issues:read"])
    response = client.get(f"/api/v1/connectors/github/{resource}", headers=auth)
    assert response.status_code == 409 and not state["requests"]


def test_worker_sees_only_authorized_pull_requests_not_connected_ci(connected_env):
    client, auth, runtime, _, state, seed = connected_env
    seed("github", ["github:pulls:read", "github:workflows:read"])
    crew = make_crew(client, auth, ["github:pulls:read"])
    runtime.router.chat = AsyncMock(
        return_value=json.dumps(
            {
                "state": "wait_for_next_review",
                "summary": "PR review",
                "next_step": "Review later",
            }
        )
    )
    client.portal.call(runtime.work, crew["id"])
    prompt = runtime.router.chat.await_args.args[2][0]["content"]
    assert "Fix build" in prompt and "PRIVATE_RAW_DIFF" not in prompt
    assert not any(r.url.path.endswith("/actions/runs") for r in state["requests"])


def test_invalid_ci_response_fails_instead_of_claiming_a_successful_sync(connected_env):
    client, auth, _, hub, _, seed = connected_env
    seed("github", ["github:workflows:read"])
    hub.transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"workflow_runs": None})
    )
    response = client.get("/api/v1/connectors/github/workflows", headers=auth)
    assert response.status_code == 409 and not hub.cache


def test_disconnected_actions_are_local_only_and_manifest_does_not_grant_access(client, auth):
    visible = client.get("/api/v1/action-policies", headers=auth).json()["actions"]
    assert visible and all(a["provider"] == "local" for a in visible)
    manifest = client.get("/api/v1/action-policies?include_unavailable=true", headers=auth).json()[
        "actions"
    ]
    assert any(a["operation"] == "github.create_issue" and not a["available"] for a in manifest)
    assert not any(
        a["operation"].startswith(("outlook.", "mscalendar.", "mstasks.")) for a in manifest
    )
    assert (
        client.post(
            "/api/v1/actions",
            headers=auth,
            json={
                "operation": "github.create_issue",
                "arguments": {"repository": "owner/repo", "title": "Fixture"},
            },
        ).status_code
        == 409
    )
    providers = client.get("/api/v1/connectors", headers=auth).json()
    assert {p["provider"] for p in providers} == {"google", "github", "feeds"}
    assert all(not p["connected"] for p in providers)
    assert client.get("/api/v1/connectors").status_code == 401


@pytest.mark.parametrize(
    "operation,args",
    [
        (
            "github.create_issue",
            {"repository": "owner/repo", "title": "Fixture", "body": "Fixture"},
        ),
        ("github.comment", {"repository": "owner/repo", "number": 3, "body": "Fixture"}),
    ],
)
def test_provider_actions_use_real_policy_executor_and_verified_contract(
    connected_env, operation, args
):
    client, auth, _, _, state, seed = connected_env
    seed("github", ["github:issues:write"])
    assert (
        client.put(
            "/api/v1/action-policies",
            headers=auth,
            json={
                "operation": operation,
                "rule": {"enabled": True, "mode": "unrestricted"},
            },
        ).status_code
        == 200
    )
    response = client.post(
        "/api/v1/actions", headers=auth, json={"operation": operation, "arguments": args}
    )
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "completed", response.text
    assert response.json()["result"]["verification"] == "read_back_verified"
    assert len(state["writes"]) == 1


@pytest.mark.parametrize(
    "code,expected", [(401, "failed"), (403, "failed"), (429, "failed"), (500, "unknown")]
)
def test_provider_rejections_and_uncertain_writes_never_replay(connected_env, code, expected):
    client, auth, runtime, hub, state, seed = connected_env
    seed("github", ["github:issues:write"])
    state["fail"] = code
    client.put(
        "/api/v1/action-policies",
        headers=auth,
        json={
            "operation": "github.create_issue",
            "rule": {"enabled": True, "mode": "unrestricted"},
        },
    )
    row = client.post(
        "/api/v1/actions",
        headers=auth,
        json={
            "operation": "github.create_issue",
            "arguments": {"repository": "owner/repo", "title": "Fixture"},
        },
    ).json()
    assert row["status"] == expected and "SECRET" not in json.dumps(row)
    client.portal.call(runtime.actions.maybe_execute, row["id"])
    assert len(state["writes"]) == 1
    if code in {401, 403}:
        assert "github:issues:write" not in hub.status("github")["capabilities"]
        assert "github.create_issue" not in {
            a["operation"]
            for a in client.get("/api/v1/action-policies", headers=auth).json()["actions"]
        }


def test_github_connect_validates_identity_repositories_and_hides_token(connected_env):
    client, auth, _, hub, state, _ = connected_env
    response = client.post(
        "/api/v1/connectors/github/connect",
        headers=auth,
        json={
            "token": "fixture-token",
            "repositories": ["owner/repo"],
            "connector_capabilities": ["github:repo:read", "github:issues:read"],
        },
    )
    assert response.status_code == 200 and response.json()["connected"]
    assert "fixture-token" not in response.text
    assert [r.url.path for r in state["requests"]] == ["/user", "/repos/owner/repo"]
    first = client.portal.call(hub.read, "github", "issues")
    second = client.portal.call(hub.read, "github", "issues")
    assert first == second and first["untrusted"]
    assert len(first["items"]) == 1
    assert first["items"][0]["title"] == "Fixture issue"
    assert len(state["requests"]) == 3
    assert not state["writes"]


def test_failed_github_upgrade_preserves_existing_connection(connected_env):
    client, auth, _, hub, _, seed = connected_env
    seed("github", ["github:repo:read"])
    hub.transport = httpx.MockTransport(lambda request: httpx.Response(401))
    response = client.post(
        "/api/v1/connectors/github/connect",
        headers=auth,
        json={
            "token": "new-invalid-token",
            "repositories": ["owner/repo"],
            "connector_capabilities": ["github:issues:write"],
        },
    )
    assert response.status_code == 409
    assert hub.status("github")["capabilities"] == ["github:repo:read"]


def test_connected_accounts_do_not_implicitly_grant_crew_access(connected_env):
    from mavo.google_actions import ActionDraft, ActionPolicyError

    client, auth, runtime, _, state, seed = connected_env
    seed("github", ["github:issues:write"])
    crew = make_crew(client, auth, [])
    client.put(
        "/api/v1/action-policies",
        headers=auth,
        json={
            "operation": "github.create_issue",
            "crew_id": crew["id"],
            "rule": {"enabled": True, "mode": "unrestricted"},
        },
    )
    with runtime.factory() as session:
        generation = session.get(CrewInstance, crew["id"]).runtime_generation
    with pytest.raises(ActionPolicyError):
        runtime.actions.propose(
            ActionDraft(
                operation="github.create_issue",
                arguments={"repository": "owner/repo", "title": "No grant"},
            ),
            crew["id"],
            generation,
        )
    assert not state["writes"]


def test_disconnecting_google_does_not_redact_github_history(connected_env):
    client, auth, runtime, hub, _, seed = connected_env
    seed("github", ["github:issues:write"])
    client.put(
        "/api/v1/action-policies",
        headers=auth,
        json={
            "operation": "github.create_issue",
            "rule": {"enabled": True, "mode": "unrestricted"},
        },
    )
    row = client.post(
        "/api/v1/actions",
        headers=auth,
        json={
            "operation": "github.create_issue",
            "arguments": {"repository": "owner/repo", "title": "Keep"},
        },
    ).json()
    client.portal.call(hub.google.disconnect)
    with runtime.factory() as session:
        assert session.get(ActionRequest, row["id"]).arguments["title"] == "Keep"
    assert hub.status("github")["connected"]


def test_worker_reads_only_crew_grants_not_every_connected_scope(connected_env):
    client, auth, runtime, _, state, seed = connected_env
    seed("github", ["github:repo:read", "github:issues:read"])
    crew = make_crew(client, auth, ["github:repo:read"])
    runtime.router.chat = AsyncMock(
        return_value=json.dumps(
            {
                "state": "wait_for_next_review",
                "summary": "Fixture summary",
                "next_step": "Review later",
            }
        )
    )
    client.portal.call(runtime.work, crew["id"])
    prompt = runtime.router.chat.await_args.args[2][0]["content"]
    assert "owner/repo" in prompt
    assert not any(r.url.path.endswith("/issues") for r in state["requests"])
    assert "github:repo" in prompt


def test_disconnected_worker_never_spends_a_model_call(connected_env):
    client, auth, runtime, _, state, _ = connected_env
    crew = make_crew(client, auth, ["github:repo:read"])
    runtime.router.chat = AsyncMock()
    client.portal.call(runtime.work, crew["id"])
    with runtime.factory() as session:
        saved = session.get(CrewInstance, crew["id"])
        assert saved.runtime_state == "needs_input" and saved.daily_calls_used == 0
    runtime.router.chat.assert_not_awaited()
    assert not state["requests"]


def test_disconnect_clears_only_github_grants_and_stops_crew(connected_env):
    client, auth, runtime, hub, _, seed = connected_env
    seed("github", ["github:repo:read"])
    crew = make_crew(client, auth, ["github:repo:read"])
    hub.credentials.set("google-tokens", "preserve-google")
    assert client.delete("/api/v1/connectors/github", headers=auth).status_code == 204
    assert not hub.status("github")["connected"]
    assert hub.credentials.get("google-tokens") == "preserve-google"
    with runtime.factory() as session:
        saved = session.get(CrewInstance, crew["id"])
        assert not saved.is_running and "github:repo:read" not in saved.permissions


@pytest.mark.parametrize(
    "path,method",
    [
        ("/api/v1/connectors/microsoft/start", "post"),
        ("/api/v1/connectors/microsoft/cancel", "post"),
        ("/api/v1/connectors/microsoft/mail", "get"),
        ("/api/v1/connectors/microsoft", "delete"),
    ],
)
def test_removed_connector_endpoints_are_unavailable(client, auth, path, method):
    assert getattr(client, method)(path, headers=auth).status_code in {404, 405}


def test_retired_capabilities_and_actions_are_rejected(client, auth):
    from pydantic import ValidationError

    from mavo.schemas import CrewDraft

    with pytest.raises(ValidationError):
        CrewDraft(
            template_id="communication",
            name="Old",
            responsibilities="Old",
            requested_connector_capabilities=["microsoft:mail:read"],
        )
    for operation in ("outlook.send", "outlook.draft", "mscalendar.create", "mstasks.create"):
        assert (
            client.post(
                "/api/v1/actions",
                headers=auth,
                json={
                    "operation": operation,
                    "arguments": {},
                },
            ).status_code
            == 422
        )
        assert (
            client.put(
                "/api/v1/action-policies",
                headers=auth,
                json={
                    "operation": operation,
                    "rule": {"enabled": True, "mode": "unrestricted"},
                },
            ).status_code
            == 422
        )


def test_invalid_github_scopes_and_repositories_are_rejected(client, auth):
    for capabilities, repositories in [
        (["github:admin:all"], ["owner/repo"]),
        (["github:repo:read"], ["../../private"]),
        (["microsoft:mail:read"], ["owner/repo"]),
    ]:
        assert (
            client.post(
                "/api/v1/connectors/github/connect",
                headers=auth,
                json={
                    "token": "fixture-token",
                    "repositories": repositories,
                    "connector_capabilities": capabilities,
                },
            ).status_code
            == 422
        )


def test_retirement_cleanup_is_idempotent_and_preserves_other_connections(connected_env):
    from mavo.connectors.retired import remove_retired_connector

    client, auth, runtime, hub, _, seed = connected_env
    seed("github", ["github:repo:read"])
    crew = make_crew(client, auth, [])
    hub.credentials.set("connector-microsoft-tokens", "retired-secret")
    hub.credentials.set("google-tokens", "keep-google")
    with runtime.factory() as session:
        saved = session.get(CrewInstance, crew["id"])
        saved.permissions = ["model:invoke", "microsoft:mail:read"]
        saved.configuration_plan = {
            "draft": {
                "name": "Legacy draft",
                "requested_connector_capabilities": ["microsoft:mail:read"],
                "requested_actions": [
                    {"operation": "outlook.draft"},
                    {"operation": "browser.read"},
                ],
            }
        }
        session.add(WorkspaceSetting(key="connector-microsoft", value={"subject": "old"}))
        session.add(WorkspaceSetting(key="action:core:outlook.draft", value={"enabled": True}))
        session.add(WorkspaceSetting(key="action:core:browser.read", value={"enabled": False}))
        session.add(
            ActionRequest(
                account_subject="microsoft:old",
                operation="outlook.draft",
                arguments={},
                fingerprint="retired-fixture",
                status="pending",
            )
        )
        session.commit()
    remove_retired_connector(runtime.factory, hub.credentials)
    with runtime.factory() as session:
        saved = session.get(CrewInstance, crew["id"])
        generation = saved.runtime_generation
        assert not saved.is_running and saved.runtime_state == "needs_input"
        assert saved.permissions == ["model:invoke"]
        assert saved.configuration_plan["phase"] == "needs_input"
        assert saved.configuration_plan["draft"]["requested_actions"] == [
            {"operation": "browser.read"}
        ]
        assert session.get(WorkspaceSetting, "connector-microsoft") is None
        assert session.get(WorkspaceSetting, "action:core:outlook.draft") is None
        assert session.get(WorkspaceSetting, "action:core:browser.read") is not None
        assert (
            session.query(ActionRequest).filter_by(fingerprint="retired-fixture").one().status
            == "rejected"
        )
    remove_retired_connector(runtime.factory, hub.credentials)
    with runtime.factory() as session:
        assert session.get(CrewInstance, crew["id"]).runtime_generation == generation
    assert hub.credentials.get("connector-microsoft-tokens") is None
    assert hub.credentials.get("google-tokens") == "keep-google"
    assert hub.status("github")["connected"]


def test_read_only_google_connection_does_not_expose_write_actions(connected_env):
    client, auth, runtime, hub, _, _ = connected_env
    scopes = ["https://www.googleapis.com/auth/gmail.readonly"]
    hub.google.credentials.set(
        "google-tokens",
        json.dumps(
            {
                "subject": "g1",
                "scopes": scopes,
                "access_token": "fixture",
                "expires_at": time.time() + 3600,
            }
        ),
    )
    with runtime.factory() as session:
        session.add(
            GoogleAccount(
                id="google",
                subject="g1",
                email="fixture@example.com",
                name="Fixture",
                scopes=scopes,
            )
        )
        session.commit()
    assert hub.status("google")["connected"]
    assert all(
        a["provider"] == "local"
        for a in client.get("/api/v1/action-policies", headers=auth).json()["actions"]
    )


def test_github_repositories_remain_limited_even_without_custom_action_limits(connected_env):
    client, auth, _, _, state, seed = connected_env
    seed("github", ["github:issues:write"])
    client.put(
        "/api/v1/action-policies",
        headers=auth,
        json={
            "operation": "github.create_issue",
            "rule": {"enabled": True, "mode": "unrestricted"},
        },
    )
    response = client.post(
        "/api/v1/actions",
        headers=auth,
        json={
            "operation": "github.create_issue",
            "arguments": {"repository": "other/repo", "title": "Do not create"},
        },
    )
    assert response.status_code == 409 and not state["writes"]


def make_crew(client, auth, caps):
    route = client.post(
        "/api/v1/routes",
        headers=auth,
        json={"name": "Worker route", "provider": "ollama", "model": "fixture"},
    ).json()
    crew = client.post(
        "/api/v1/crew",
        headers=auth,
        json={
            "name": "Connected worker",
            "template_id": "communication",
            "responsibilities": "Read only authorized information",
            "route_id": route["id"],
            "trigger_type": "interval",
            "connector_capabilities": caps,
        },
    ).json()
    assert client.post(f"/api/v1/crew/{crew['id']}/start", headers=auth).status_code == 200
    return crew


def test_google_draft_uses_drafts_endpoint_and_never_sends(connected_env):
    client, auth, runtime, hub, _, _ = connected_env
    scopes = ["https://www.googleapis.com/auth/gmail.modify"]
    hub.google.credentials.set(
        "google-tokens",
        json.dumps(
            {
                "subject": "g1",
                "scopes": scopes,
                "access_token": "fixture",
                "expires_at": time.time() + 3600,
            }
        ),
    )
    with runtime.factory() as session:
        session.add(
            GoogleAccount(
                id="google",
                subject="g1",
                email="fixture@example.com",
                name="Fixture",
                scopes=scopes,
            )
        )
        session.commit()
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.path.endswith("/drafts") and "message" in json.loads(request.content)
        return httpx.Response(200, json={"id": "draft-1", "message": {"id": "mail-1"}})

    runtime.actions.auth.transport = httpx.MockTransport(handler)
    client.put(
        "/api/v1/action-policies",
        headers=auth,
        json={"operation": "gmail.draft", "rule": {"enabled": True, "mode": "unrestricted"}},
    )
    response = client.post(
        "/api/v1/actions",
        headers=auth,
        json={
            "operation": "gmail.draft",
            "arguments": {"to": "friend@example.com", "subject": "Draft", "body": "Not sent"},
        },
    )
    assert response.json()["status"] == "completed"
    assert response.json()["result"]["verification"] == "draft_created_not_sent"
    assert len(calls) == 1


def test_draft_proposal_requires_matching_write_access_but_cannot_grant_it():
    from mavo.schemas import CrewDraft

    draft = CrewDraft(
        template_id="communication",
        name="Drafts",
        responsibilities="Prepare, do not send",
        requested_actions=[{"operation": "gmail.draft"}],
    )
    assert "gmail:modify" in draft.requested_google_capabilities
    assert "gmail:send" not in draft.requested_google_capabilities


def test_natural_language_github_draft_stays_unpublished_until_connection_and_access(
    connected_env,
):
    from mavo.schemas import CoordinatorDecision

    client, auth, runtime, _, _, seed = connected_env
    route = client.post(
        "/api/v1/routes",
        headers=auth,
        json={"name": "Agent", "provider": "ollama", "model": "fixture"},
    ).json()
    decision = CoordinatorDecision.model_validate(
        {
            "reply": "Review GitHub setup",
            "crew_proposal": {
                "name": "GitHub reader",
                "template_id": "communication",
                "responsibilities": "Review GitHub inbox headers only",
                "trigger_type": "interval",
                "route_id": route["id"],
                "requested_connector_capabilities": ["github:issues:read"],
            },
        }
    )
    runtime.planner.coordinator = SimpleNamespace(
        complete=AsyncMock(return_value=(decision, "fixture"))
    )
    response = client.post(
        "/api/v1/core/turn",
        headers=auth,
        json={"message": "Read GitHub, not Google", "request_id": str(uuid4())},
    )
    assert response.status_code == 200, response.text
    draft = response.json()["draft"]
    assert draft["permissions"] == ["model:invoke"] and response.json()["crew"] is None
    endpoint = f"/api/v1/crew/{draft['id']}/access"
    payload = {"route_id": route["id"], "connector_capabilities": ["github:issues:read"]}
    waiting = client.post(endpoint, headers=auth, json=payload)
    assert waiting.status_code == 200 and waiting.json()["status"] == "draft"
    assert client.get("/api/v1/crew", headers=auth).json() == []
    seed("github", ["github:issues:read"])
    ready = client.post(endpoint, headers=auth, json=payload)
    assert (
        ready.status_code == 200
        and ready.json()["status"] == "active"
        and ready.json()["is_running"]
    )
    assert not any(p.startswith("google:") for p in ready.json()["permissions"])
