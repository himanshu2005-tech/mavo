from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from mavo.crew_templates import CREW_TEMPLATES


def create_route(
    client: TestClient,
    auth: dict[str, str],
    *,
    name: str = "Local test",
    provider: str = "ollama",
    model: str = "qwen-test:latest",
) -> dict[str, object]:
    payload = {"name": name, "provider": provider, "model": model}
    if provider != "ollama":
        payload["api_key"] = "sk-test-not-a-real-secret"
    response = client.post("/api/v1/routes", headers=auth, json=payload)
    assert response.status_code == 201
    return response.json()


def test_health_is_minimal_and_public(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_cloud_fallback_is_explicit_validated_and_revocable(client, auth):
    local = create_route(client, auth)
    cloud = create_route(client, auth, name="Cloud fallback", provider="airouter", model="test")
    crew = client.post(
        "/api/v1/crew",
        headers=auth,
        json={
            "template_id": "research",
            "name": "Private notes",
            "responsibilities": "Summarise notes",
            "autonomy_mode": "reactive",
            "route_id": local["id"],
        },
    ).json()
    path = f"/api/v1/crew/{crew['id']}"
    assert crew["fallback_route_id"] is None
    invalid = client.patch(path, headers=auth, json={"fallback_route_id": local["id"]})
    assert invalid.status_code == 400
    enabled = client.patch(path, headers=auth, json={"fallback_route_id": cloud["id"]})
    assert enabled.status_code == 200
    assert enabled.json()["fallback_route_id"] == cloud["id"]
    assert (
        client.patch(path, headers=auth, json={"fallback_route_id": "missing"}).status_code == 404
    )
    disabled = client.patch(path, headers=auth, json={"fallback_route_id": None})
    assert disabled.json()["fallback_route_id"] is None
    # Moving to a cloud primary clears the old fallback instead of creating a cloud chain.
    client.patch(path, headers=auth, json={"fallback_route_id": cloud["id"]})
    moved = client.patch(path, headers=auth, json={"route_id": cloud["id"]})
    assert moved.json()["fallback_route_id"] is None


def test_protected_endpoints_reject_missing_token(client: TestClient) -> None:
    response = client.get("/api/v1/status")
    assert response.status_code == 401


def test_desktop_origin_can_preflight_provider_changes(client: TestClient) -> None:
    for method in ("PUT", "PATCH", "DELETE"):
        response = client.options(
            "/api/v1/providers/airouter",
            headers={
                "Origin": "http://localhost:1420",
                "Access-Control-Request-Method": method,
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        assert response.status_code == 200
        allow_methods = response.headers["access-control-allow-methods"]
        allowed = {item.strip() for item in allow_methods.split(",")}
        assert method in allowed


def test_status_reports_real_model_discovery(client: TestClient, auth: dict[str, str]) -> None:
    response = client.get("/api/v1/status", headers=auth)
    assert response.status_code == 200
    body = response.json()
    assert body["database"] == "connected"
    ollama = next(provider for provider in body["providers"] if provider["id"] == "ollama")
    assert ollama["connected"] is True
    assert ollama["models"][0]["name"] == "qwen-test:latest"
    assert (
        next(provider for provider in body["providers"] if provider["id"] == "openai")["configured"]
        is False
    )


def test_normal_chat_never_falls_back_to_an_agent_key(client, auth):
    from unittest.mock import AsyncMock
    from uuid import uuid4

    create_route(client, auth)
    runtime = client.app.state.crew_runtime
    runtime.router.chat = AsyncMock(side_effect=AssertionError("Agent key used for Core"))
    response = client.post(
        "/api/v1/core/turn", headers=auth, json={"message": "Hello", "request_id": str(uuid4())}
    )
    assert response.status_code == 503
    runtime.router.chat.assert_not_called()


def test_chat_validates_model_name(client: TestClient, auth: dict[str, str]) -> None:
    response = client.post(
        "/api/v1/routes",
        headers=auth,
        json={"name": "Unsafe", "provider": "ollama", "model": "bad\nmodel"},
    )
    assert response.status_code == 422


def test_failed_coordinator_turn_does_not_save_a_user_only_conversation(client, auth):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from mavo.coordinator_client import CoordinatorUnavailable

    client.app.state.crew_runtime.planner.coordinator = SimpleNamespace(
        complete=AsyncMock(side_effect=CoordinatorUnavailable("Unavailable"))
    )
    response = client.post(
        "/api/v1/core/turn", headers=auth, json={"message": "Hello", "request_id": str(uuid4())}
    )
    assert response.status_code == 503
    assert client.get("/api/v1/conversations", headers=auth).json() == []


def test_cloud_provider_configuration_never_returns_key(
    client: TestClient, auth: dict[str, str]
) -> None:
    secret = "sk-test-not-a-real-secret"
    response = client.put(
        "/api/v1/providers/openai",
        headers=auth,
        json={"api_key": secret, "default_model": "gpt-test"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "provider": "openai",
        "default_model": "gpt-test",
        "has_credential": True,
    }
    assert secret not in response.text

    status = client.get("/api/v1/status", headers=auth).json()
    openai = next(provider for provider in status["providers"] if provider["id"] == "openai")
    assert openai["connected"] is True
    assert openai["models"][0]["name"] == "gpt-test"


def test_agent_work_uses_its_own_cloud_connection(client, auth):
    route = create_route(client, auth, name="OpenAI agent", provider="openai", model="gpt-test")
    crew = client.post(
        "/api/v1/crew",
        headers=auth,
        json={
            "name": "Cloud agent",
            "template_id": "research",
            "responsibilities": "Help with research",
            "route_id": route["id"],
        },
    ).json()
    response = client.post(
        f"/api/v1/crew/{crew['id']}/run", headers=auth, json={"message": "Give a short outline"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["result"] == "Cloud response."
    assert response.json()["provider"] == "openai"
    assert client.get("/api/v1/conversations", headers=auth).json() == []


def test_disconnect_removes_cloud_provider(client: TestClient, auth: dict[str, str]) -> None:
    client.put(
        "/api/v1/providers/openai",
        headers=auth,
        json={"api_key": "sk-test-not-a-real-secret", "default_model": "gpt-test"},
    )
    assert client.delete("/api/v1/providers/openai", headers=auth).status_code == 204
    status = client.get("/api/v1/status", headers=auth).json()
    openai = next(provider for provider in status["providers"] if provider["id"] == "openai")
    assert openai["configured"] is False


def test_crew_templates_are_valid_and_complete(client: TestClient, auth: dict[str, str]) -> None:
    response = client.get("/api/v1/crew/templates", headers=auth)
    assert response.status_code == 200
    templates = response.json()
    assert len(templates) == 15
    assert {item["name"] for item in templates} >= {"Schedule Crew", "Computer Crew"}
    assert all(
        item["implementation_status"] in {"manual_assistance", "daily_reminders"}
        for item in templates
    )


def test_crew_crud_is_persisted_and_bounded(client: TestClient, auth: dict[str, str]) -> None:
    route = create_route(client, auth, name="Crew OpenAI", provider="openai", model="gpt-test")
    response = client.post(
        "/api/v1/crew",
        headers=auth,
        json={
            "template_id": "schedule",
            "name": "College Scheduler",
            "responsibilities": "Review my college deadlines.",
            "autonomy_mode": "proactive",
            "route_id": route["id"],
            "max_model_calls": 4,
        },
    )
    assert response.status_code == 201
    crew = response.json()
    assert crew["permissions"] == ["model:invoke"]
    assert crew["resources"] == []
    assert crew["triggers"] == []

    duplicate = client.post(
        "/api/v1/crew",
        headers=auth,
        json={
            "template_id": "schedule",
            "name": "college scheduler",
            "responsibilities": "A separate duplicate.",
            "route_id": route["id"],
        },
    )
    assert duplicate.status_code == 409

    updated = client.patch(f"/api/v1/crew/{crew['id']}", headers=auth, json={"status": "paused"})
    assert updated.status_code == 200
    assert updated.json()["status"] == "paused"
    assert len(client.get("/api/v1/crew", headers=auth).json()) == 1
    assert client.delete(f"/api/v1/crew/{crew['id']}", headers=auth).status_code == 204
    assert client.get("/api/v1/crew", headers=auth).json() == []


def test_crew_rejects_unbounded_budget(client: TestClient, auth: dict[str, str]) -> None:
    response = client.post(
        "/api/v1/crew",
        headers=auth,
        json={
            "template_id": "computer",
            "name": "Laptop Monitor",
            "responsibilities": "Review device health.",
            "route_id": "some-route",
            "max_model_calls": 51,
        },
    )
    assert response.status_code == 422


def test_named_routes_support_multiple_keys_without_returning_them(
    client: TestClient, auth: dict[str, str]
) -> None:
    first = create_route(client, auth, name="OpenAI fast", provider="openai", model="gpt-test")
    second = create_route(
        client, auth, name="OpenAI careful", provider="openai", model="gpt-reasoning"
    )
    routes = client.get("/api/v1/routes", headers=auth)
    assert routes.status_code == 200
    assert {route["name"] for route in routes.json()} == {"OpenAI fast", "OpenAI careful"}
    assert "sk-test" not in routes.text

    updated = client.patch(
        f"/api/v1/routes/{first['id']}",
        headers=auth,
        json={"name": "OpenAI quick", "model": "gpt-new"},
    )
    assert updated.status_code == 200
    assert updated.json()["has_credential"] is True
    assert client.delete(f"/api/v1/routes/{second['id']}", headers=auth).status_code == 204


def test_weather_resource_is_live_shaped_and_cached(
    client: TestClient, auth: dict[str, str]
) -> None:
    client.put("/api/v1/resources/access", headers=auth, json={"weather": True})
    first = client.get("/api/v1/resources/weather?location=New%20Delhi", headers=auth)
    second = client.get("/api/v1/resources/weather?location=New%20Delhi", headers=auth)
    assert first.status_code == 200
    assert first.json()["provider"] == "open-meteo"
    assert first.json()["current"]["temperature_c"] == 28.0
    assert first.json()["cached"] is False
    assert second.json()["cached"] is True


def test_schedule_crew_parses_and_deduplicates_daily_reminder(
    client: TestClient, auth: dict[str, str]
) -> None:
    route = create_route(client, auth, name="Reminder route")
    created = client.post(
        "/api/v1/crew",
        headers=auth,
        json={
            "template_id": "schedule",
            "name": "Watch Wear Reminder",
            "responsibilities": "Remind me to wear my Apple Watch every day at 10PM",
            "autonomy_mode": "proactive",
            "route_id": route["id"],
            "max_model_calls": 5,
        },
    )
    assert created.status_code == 201
    crew = created.json()
    assert crew["schedule_time"] == "22:00"

    client.patch(f"/api/v1/crew/{crew['id']}", headers=auth, json={"schedule_time": "00:00"})
    assert client.get("/api/v1/reminders/due", headers=auth).json() == []
    assert client.post(f"/api/v1/crew/{crew['id']}/start", headers=auth).status_code == 200
    first = client.get("/api/v1/reminders/due", headers=auth).json()
    second = client.get("/api/v1/reminders/due", headers=auth).json()
    assert len(first) == 1
    assert len(second) == 1
    assert first[0]["message"].startswith("Remind me to wear")
    assert (
        client.post(f"/api/v1/reminders/{first[0]['id']}/dismiss", headers=auth).status_code == 204
    )
    assert client.get("/api/v1/reminders/due", headers=auth).json() == []


@pytest.mark.parametrize("template_id", [item.id for item in CREW_TEMPLATES])
def test_every_crew_can_run_and_keeps_its_own_history(client, auth, template_id):
    route = create_route(client, auth)
    ids = []
    for suffix in ("one", "two"):
        response = client.post(
            "/api/v1/crew",
            headers=auth,
            json={
                "template_id": template_id,
                "name": f"{template_id} {suffix}",
                "responsibilities": "Help with a bounded task.",
                "route_id": route["id"],
                "max_model_calls": 1,
            },
        )
        assert response.status_code == 201
        ids.append(response.json()["id"])
    response = client.post(
        f"/api/v1/crew/{ids[0]}/run",
        headers=auth,
        json={"message": "Suggest one useful next step."},
    )
    assert response.status_code == 200
    run = response.json()
    assert run["status"] == "completed"
    assert run["model_calls"] == 1
    assert run["result"] == "A real model response."
    assert len(client.get(f"/api/v1/crew-runs?crew_id={ids[0]}", headers=auth).json()) == 1
    assert client.get(f"/api/v1/crew-runs?crew_id={ids[1]}", headers=auth).json() == []
    client.patch(f"/api/v1/crew/{ids[0]}", headers=auth, json={"status": "paused"})
    assert (
        client.post(
            f"/api/v1/crew/{ids[0]}/run", headers=auth, json={"message": "Do more."}
        ).status_code
        == 409
    )


def test_failed_session_and_route_test_are_truthful(client, auth):
    route = create_route(client, auth, model="qwen-fail:latest")
    crew = client.post(
        "/api/v1/crew",
        headers=auth,
        json={
            "template_id": "coding",
            "name": "Failure case",
            "responsibilities": "Help with code.",
            "route_id": route["id"],
        },
    ).json()
    run = client.post(
        f"/api/v1/crew/{crew['id']}/run", headers=auth, json={"message": "Review this idea."}
    ).json()
    assert run["status"] == "failed"
    assert run["error"] and run["result"] is None
    assert client.post(f"/api/v1/routes/{route['id']}/test", headers=auth).status_code == 503
    assert (
        client.post(f"/api/v1/crew/{crew['id']}/run", json={"message": "Unauthorized"}).status_code
        == 401
    )


def test_paused_reminders_are_not_delivered(client, auth):
    route = create_route(client, auth)
    crew = client.post(
        "/api/v1/crew",
        headers=auth,
        json={
            "template_id": "schedule",
            "name": "Paused reminder",
            "responsibilities": "A daily reminder",
            "route_id": route["id"],
            "schedule_time": "00:00",
        },
    ).json()
    client.post(f"/api/v1/crew/{crew['id']}/start", headers=auth)
    assert len(client.get("/api/v1/reminders/due", headers=auth).json()) == 1
    client.patch(f"/api/v1/crew/{crew['id']}", headers=auth, json={"status": "paused"})
    assert client.get("/api/v1/reminders/due", headers=auth).json() == []


def test_updates_reject_null_and_route_changes_reach_assigned_crew(client, auth):
    route = create_route(client, auth)
    crew = client.post(
        "/api/v1/crew",
        headers=auth,
        json={
            "template_id": "schedule",
            "name": "Route test",
            "responsibilities": "Plan",
            "route_id": route["id"],
        },
    ).json()
    assert (
        client.patch(
            f"/api/v1/routes/{route['id']}", headers=auth, json={"model": None}
        ).status_code
        == 422
    )
    assert (
        client.patch(f"/api/v1/crew/{crew['id']}", headers=auth, json={"name": None}).status_code
        == 422
    )
    assert (
        client.patch(
            f"/api/v1/routes/{route['id']}", headers=auth, json={"model": "new-model"}
        ).status_code
        == 200
    )
    assert client.get("/api/v1/crew", headers=auth).json()[0]["model"] == "new-model"


def test_delete_conversation_removes_messages(client, auth):
    from sqlalchemy import func, select

    from mavo.database import Conversation, Message

    factory = client.app.state.crew_runtime.factory
    with factory() as session:
        conversation = Conversation(title="Saved history")
        session.add(conversation)
        session.flush()
        conversation_id = conversation.id
        session.add_all(
            [
                Message(conversation_id=conversation_id, role="user", content="Hello"),
                Message(conversation_id=conversation_id, role="assistant", content="Hi"),
            ]
        )
        session.commit()
    assert (
        client.delete(f"/api/v1/conversations/{conversation_id}", headers=auth).status_code == 204
    )
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Message)) == 0


def test_run_stop_are_explicit_and_stop_cancels_queued_reminders(client, auth):
    route = create_route(client, auth)
    crew = client.post(
        "/api/v1/crew",
        headers=auth,
        json={
            "template_id": "schedule",
            "name": "Explicit worker",
            "responsibilities": "Remind me every day at 12AM",
            "route_id": route["id"],
        },
    ).json()
    assert not crew["is_running"] and crew["runtime_state"] == "stopped"
    assert client.get("/api/v1/reminders/due", headers=auth).json() == []
    first = client.post(f"/api/v1/crew/{crew['id']}/start", headers=auth).json()
    again = client.post(f"/api/v1/crew/{crew['id']}/start", headers=auth).json()
    assert first["is_running"] and again["next_run_at"] == first["next_run_at"]
    assert len(client.get("/api/v1/reminders/due", headers=auth).json()) == 1
    stopped = client.post(f"/api/v1/crew/{crew['id']}/stop", headers=auth).json()
    assert not stopped["is_running"] and stopped["next_run_at"] is None
    assert client.get("/api/v1/reminders/due", headers=auth).json() == []
    assert client.get("/api/v1/reminders", headers=auth).json()[0]["status"] == "cancelled"
    assert client.post(f"/api/v1/crew/{crew['id']}/check", headers=auth).status_code == 409


def test_delete_running_crew_stops_it_and_removes_scoped_policy_and_reminders(client, auth):
    route = create_route(client, auth)
    crew = client.post(
        "/api/v1/crew",
        headers=auth,
        json={
            "template_id": "schedule",
            "name": "Delete safely",
            "responsibilities": "Remind me at 12AM",
            "route_id": route["id"],
        },
    ).json()
    client.put(
        "/api/v1/action-policies",
        headers=auth,
        json={
            "operation": "browser.open",
            "crew_id": crew["id"],
            "rule": {"enabled": True, "mode": "unrestricted"},
        },
    )
    assert client.post(f"/api/v1/crew/{crew['id']}/start", headers=auth).json()["is_running"]
    assert client.delete(f"/api/v1/crew/{crew['id']}", headers=auth).status_code == 204
    assert client.get("/api/v1/crew", headers=auth).json() == []
    assert client.get("/api/v1/reminders", headers=auth).json() == []
    assert client.post(f"/api/v1/crew/{crew['id']}/start", headers=auth).status_code == 404
    from mavo.database import WorkspaceSetting

    with client.app.state.crew_runtime.factory() as session:
        assert session.get(WorkspaceSetting, "action:" + crew["id"] + ":browser.open") is None


def test_editing_a_text_crew_does_not_require_device_access(client, auth):
    route = create_route(client, auth)
    crew = client.post(
        "/api/v1/crew",
        headers=auth,
        json={
            "template_id": "research",
            "name": "Text worker",
            "responsibilities": "Research",
            "route_id": route["id"],
        },
    ).json()
    changed = client.patch(
        f"/api/v1/crew/{crew['id']}",
        headers=auth,
        json={
            "name": "Edited worker",
            "allow_device_metrics": False,
        },
    )
    assert changed.status_code == 200
    assert changed.json()["permissions"] == ["model:invoke"]


def test_device_permission_is_explicit_and_can_be_revoked(client, auth):
    route = create_route(client, auth)
    response = client.post(
        "/api/v1/crew",
        headers=auth,
        json={
            "template_id": "computer",
            "name": "Computer",
            "responsibilities": "Check device",
            "route_id": route["id"],
            "allow_device_metrics": True,
        },
    )
    assert response.status_code == 201
    crew = response.json()
    assert "device:read" in crew["permissions"]
    revoked = client.patch(
        f"/api/v1/crew/{crew['id']}", headers=auth, json={"allow_device_metrics": False}
    )
    assert "device:read" not in revoked.json()["permissions"]
    assert (
        client.post(
            "/api/v1/crew",
            headers=auth,
            json={
                "template_id": "research",
                "name": "Wrong scope",
                "responsibilities": "Research",
                "route_id": route["id"],
                "allow_device_metrics": True,
            },
        ).status_code
        == 400
    )
