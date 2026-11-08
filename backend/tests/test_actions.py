import asyncio
import base64
import json
import time
from datetime import timedelta
from email import message_from_bytes

import httpx
import pytest
from pydantic import ValidationError

from mavo.database import ActionRequest, CrewInstance, GoogleAccount, utcnow
from mavo.google_actions import ActionDraft, ActionPolicyError
from mavo.google_auth import SCOPES
from mavo.google_permissions import BASE, CAPABILITIES


@pytest.fixture
def action_env(client, auth):
    runtime = client.app.state.crew_runtime
    engine = runtime.actions
    scopes = list(set(SCOPES.values()) | {BASE + item[2] for item in CAPABILITIES.values()})
    engine.auth.credentials.set(
        "google-tokens",
        json.dumps(
            {
                "subject": "user-1",
                "access_token": "test-access",
                "refresh_token": "test-refresh",
                "expires_at": time.time() + 3600,
                "scopes": scopes,
            }
        ),
    )
    with engine.factory() as session:
        session.add(
            GoogleAccount(
                id="google",
                subject="user-1",
                email="owner@example.com",
                name="Owner",
                scopes=scopes,
            )
        )
        session.commit()
    state = {"writes": [], "items": {}, "fail": None, "conflict": False}

    async def handler(request):
        path = request.url.path
        if request.method == "GET":
            item = state["items"].get(path)
            return httpx.Response(200, json=item) if item else httpx.Response(404)
        state["writes"].append(request)
        await asyncio.sleep(0.01)
        if state["fail"]:
            return httpx.Response(state["fail"], json={"secret": "MUST_NOT_LEAK"})
        if state["conflict"]:
            return httpx.Response(412)
        if path.endswith("/messages/send"):
            return httpx.Response(200, json={"id": "sent-1"})
        if "/messages/" in path:
            return httpx.Response(
                200, json={"id": "mail-1", "labelIds": ["TRASH"] if path.endswith("/trash") else []}
            )
        data = json.loads(request.content) if request.content else {}
        if request.method == "POST":
            item_id = data.get("id", "task-1")
            state["items"][path + "/" + item_id] = {**data, "id": item_id}
        elif request.method == "DELETE":
            state["items"].pop(path, None)
            return httpx.Response(204)
        else:
            item_id = path.rsplit("/", 1)[-1]
            state["items"][path] = {**state["items"].get(path, {}), **data, "id": item_id}
        return httpx.Response(
            200,
            json=state["items"][path + "/" + item_id]
            if request.method == "POST"
            else state["items"][path],
        )

    for operation in engine.models:
        if operation.split(".")[0] in {"gmail", "calendar", "tasks"}:
            response = client.put(
                "/api/v1/action-policies",
                headers=auth,
                json={"operation": operation, "rule": {"enabled": True, "mode": "unrestricted"}},
            )
            assert response.status_code == 200
    engine.auth.transport = httpx.MockTransport(handler)
    return client, auth, engine, state


def propose(env, operation, arguments):
    client, auth, _, _ = env
    response = client.post(
        "/api/v1/actions", headers=auth, json={"operation": operation, "arguments": arguments}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_allowed_send_runs_once_without_approval_and_records_actual_sender(action_env):
    client, auth, engine, state = action_env
    action = propose(
        action_env,
        "gmail.send",
        {"to": "recipient@example.com", "subject": "Hello", "body": "An authorized message"},
    )
    result = next(
        a for a in client.get("/api/v1/actions", headers=auth).json() if a["id"] == action
    )
    assert result["status"] == "completed" and result["approved_by"] == "access"
    raw = json.loads(state["writes"][0].content)["raw"]
    message = message_from_bytes(base64.urlsafe_b64decode(raw))
    assert message["From"] == "owner@example.com" and message["To"] == "recipient@example.com"
    assert client.post(f"/api/v1/actions/{action}/approve", headers=auth).status_code == 410
    assert client.portal.call(engine.maybe_execute, action)["status"] == "completed"
    assert len(state["writes"]) == 1


@pytest.mark.parametrize(
    "operation,arguments",
    [
        ("gmail.archive", {"message_id": "mail-1"}),
        ("gmail.mark_read", {"message_id": "mail-1"}),
        ("gmail.trash", {"message_id": "mail-1"}),
        (
            "calendar.create",
            {
                "summary": "Study",
                "start": "2026-11-02T10:00:00+05:30",
                "end": "2026-11-02T11:00:00+05:30",
            },
        ),
        (
            "calendar.update",
            {
                "item_id": "event-1",
                "etag": '"v1"',
                "summary": "Study",
                "start": "2026-11-02T10:00:00+05:30",
                "end": "2026-11-02T11:00:00+05:30",
            },
        ),
        ("calendar.delete", {"item_id": "event-1", "etag": '"v1"'}),
        ("tasks.create", {"title": "Study", "due": "2026-11-02"}),
        ("tasks.update", {"item_id": "task-1", "etag": '"v1"', "title": "Study"}),
        ("tasks.complete", {"item_id": "task-1", "etag": '"v1"'}),
        ("tasks.delete", {"item_id": "task-1", "etag": '"v1"'}),
    ],
)
def test_supported_actions_apply_and_verify(action_env, operation, arguments):
    client, auth, _, state = action_env
    if "item_id" in arguments:
        base = (
            "/calendar/v3/calendars/primary/events/"
            if operation.startswith("calendar.")
            else "/tasks/v1/lists/@default/tasks/"
        )
        state["items"][base + arguments["item_id"]] = {
            "id": arguments["item_id"],
            "etag": arguments["etag"],
            "notes": "Keep these notes",
        }
    action = propose(action_env, operation, arguments)
    result = next(
        a for a in client.get("/api/v1/actions", headers=auth).json() if a["id"] == action
    )
    assert result["status"] == "completed", result
    assert len(state["writes"]) == 1
    if "etag" in arguments:
        assert state["writes"][0].headers["If-Match"] == arguments["etag"]
    if operation == "tasks.update" and "notes" not in arguments:
        assert "notes" not in json.loads(state["writes"][0].content)


def test_crew_grant_and_identity_cannot_escalate_after_delete(action_env):
    client, auth, engine, state = action_env
    with engine.factory() as session:
        session.add(
            CrewInstance(
                id="crew-1",
                name="Scoped",
                template_id="communication",
                provider="ollama",
                model="test",
                responsibilities="Mailbox",
                permissions=["model:invoke", "google:gmail:modify"],
                runtime_generation=1,
            )
        )
        session.commit()
    client.put(
        "/api/v1/action-policies",
        headers=auth,
        json={
            "operation": "gmail.send",
            "crew_id": "crew-1",
            "rule": {"enabled": True, "mode": "unrestricted"},
        },
    )
    with engine.factory() as session:
        crew = session.get(CrewInstance, "crew-1")
        generation = crew.runtime_generation
    draft = ActionDraft(
        operation="gmail.send",
        arguments={"to": "recipient@example.com", "subject": "Hi", "body": "Test"},
    )
    with pytest.raises(ActionPolicyError):
        engine.propose(draft, "crew-1", generation)
    with engine.factory() as session:
        crew = session.get(CrewInstance, "crew-1")
        crew.permissions = ["model:invoke", "google:gmail:send"]
        session.commit()
    queued = engine.propose(draft, "crew-1", generation)
    assert engine.propose(draft, "crew-1", generation)["id"] == queued["id"]
    with engine.factory() as session:
        session.delete(session.get(CrewInstance, "crew-1"))
        session.commit()
    with pytest.raises(ActionPolicyError):
        client.portal.call(engine.maybe_execute, queued["id"])
    assert not state["writes"]


@pytest.mark.parametrize("status,expected", [(403, "failed"), (412, "failed"), (503, "unknown")])
def test_errors_are_recorded_without_automatic_replay(action_env, status, expected):
    client, auth, engine, state = action_env
    state["fail"] = status
    action = propose(action_env, "tasks.create", {"title": "A task"})
    result = client.portal.call(engine.maybe_execute, action)
    assert result["status"] == expected and "MUST_NOT_LEAK" not in str(result)
    assert len(state["writes"]) == 1


def test_missing_google_scope_blocks_and_disabling_access_denies(action_env):
    client, auth, engine, state = action_env
    assert client.get("/api/v1/actions").status_code == 401
    tokens = json.loads(engine.auth.credentials.get("google-tokens"))
    tokens["scopes"] = [SCOPES["gmail"]]
    engine.auth.credentials.set("google-tokens", json.dumps(tokens))
    payload = {
        "operation": "gmail.send",
        "arguments": {"to": "r@example.com", "subject": "Hi", "body": "Hello"},
    }
    assert client.post("/api/v1/actions", headers=auth, json=payload).status_code == 409
    assert client.get("/api/v1/actions", headers=auth).json()[0]["status"] == "blocked"
    client.put(
        "/api/v1/action-policies",
        headers=auth,
        json={"operation": "gmail.send", "rule": {"enabled": False}},
    )
    assert client.post("/api/v1/actions", headers=auth, json=payload).status_code == 409
    assert not state["writes"]


def test_hostile_arguments_are_rejected():
    for operation, args in [
        (
            "gmail.send",
            {"to": "victim@example.com\r\nBcc: s@example.com", "subject": "Hi", "body": "Test"},
        ),
        ("calendar.delete", {"item_id": "x", "etag": "x\r\ninjected"}),
        (
            "calendar.create",
            {"summary": "x", "start": "2026-01-01T10:00:00", "end": "2026-01-01T11:00:00"},
        ),
        ("shell.execute", {"command": "anything"}),
    ]:
        with pytest.raises(ValidationError):
            ActionDraft.model_validate({"operation": operation, "arguments": args})


def test_concurrent_execution_claims_one_write(action_env):
    client, _, engine, state = action_env
    queued = engine.propose(ActionDraft(operation="tasks.create", arguments={"title": "One task"}))

    async def run():
        return await asyncio.gather(
            engine.maybe_execute(queued["id"]),
            engine.maybe_execute(queued["id"]),
            return_exceptions=True,
        )

    results = client.portal.call(run)
    assert any(isinstance(r, dict) and r["status"] == "completed" for r in results)
    assert len(state["writes"]) == 1


def test_expired_execution_is_unknown_and_not_replayed(action_env):
    client, auth, engine, state = action_env
    queued = engine.propose(ActionDraft(operation="tasks.create", arguments={"title": "A task"}))
    with engine.factory() as session:
        row = session.get(ActionRequest, queued["id"])
        row.status = "executing"
        row.lease_until = utcnow() - timedelta(seconds=1)
        session.commit()
    assert client.get("/api/v1/actions", headers=auth).json()[0]["status"] == "unknown"
    assert client.portal.call(engine.maybe_execute, queued["id"])["status"] == "unknown"
    assert not state["writes"]
