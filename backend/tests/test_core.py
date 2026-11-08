import json
from urllib.parse import parse_qs, urlsplit

from mavo.core import parse_core_output
from mavo.google_auth import IDENTITY_SCOPES, SCOPES
from mavo.schemas import CrewDraft


def test_fenced_json_preserves_markdown_fences_inside_reply_strings():
    raw = json.dumps(
        {
            "reply": "Review the proposal.\n```yaml\nname: Example\n```",
            "crew_proposal": {
                "template_id": "schedule",
                "name": "Watch reminder",
                "responsibilities": "Remind me to wear my watch",
                "trigger_type": "daily",
                "schedule_time": "22:00",
            },
        }
    )
    reply, proposal = parse_core_output("```json\n" + raw + "\n```")
    assert proposal is not None and proposal.name == "Watch reminder"
    assert "```yaml" in reply


def test_core_uses_one_conversation_without_deleting_previous_history(client, auth):
    from uuid import uuid4

    from tests.test_planner import Coordinator

    coordinator = Coordinator()
    client.app.state.crew_runtime.planner.coordinator = coordinator
    results = [
        client.post(
            "/api/v1/core/turn", headers=auth, json={"message": message, "request_id": str(uuid4())}
        ).json()
        for message in ("Hello", "Remember our discussion")
    ]
    assert results[0]["conversation"]["id"] == results[1]["conversation"]["id"]
    main = client.get("/api/v1/core/conversation", headers=auth).json()
    assert len(main["conversation"]["messages"]) == 4
    assert len(client.get("/api/v1/conversations", headers=auth).json()) == 1


def test_core_proposals_are_validated_and_cannot_grant_permissions():
    draft = {
        "template_id": "communication",
        "name": "College mail",
        "responsibilities": "Review college email headers",
        "trigger_type": "gmail_message",
        "requested_google_resources": ["gmail"],
    }
    reply, parsed = parse_core_output(
        json.dumps({"reply": "Review this configuration.", "crew_proposal": draft})
    )
    assert isinstance(parsed, CrewDraft) and parsed.schedule_time is None
    _, rejected = parse_core_output(
        json.dumps(
            {
                "reply": "Propose",
                "crew_proposal": {
                    **draft,
                    "permissions": ["shell:execute", "gmail:send"],
                },
            }
        )
    )
    assert rejected is None


def test_proposal_null_optional_limits_use_bounded_application_defaults():
    draft = {
        "template_id": "schedule",
        "name": "Watch reminder",
        "responsibilities": "Wear watch",
        "trigger_type": "daily",
        "schedule_time": "22:00",
        "review_interval_minutes": None,
        "daily_call_limit": None,
    }
    _, parsed = parse_core_output(json.dumps({"reply": "Review", "crew_proposal": draft}))
    assert parsed is not None and parsed.daily_call_limit == 12
    assert parsed.review_interval_minutes == 30 and parsed.requested_google_resources == []
    _, invalid = parse_core_output(
        json.dumps(
            {
                "reply": "Review",
                "crew_proposal": {
                    **draft,
                    "daily_call_limit": 1000000,
                },
            }
        )
    )
    assert invalid is None


def test_google_setup_and_resources_require_local_auth(client, auth):
    assert client.get("/api/v1/auth/google").status_code == 401
    status = client.get("/api/v1/auth/google", headers=auth).json()
    assert not status["configured"] and not status["signed_in"]
    assert (
        client.post("/api/v1/auth/google/start", headers=auth, json={"resources": []}).status_code
        == 409
    )
    assert client.get("/api/v1/resources/google/gmail", headers=auth).status_code == 409
    configured = client.put(
        "/api/v1/auth/google/client",
        headers=auth,
        json={
            "client_id": "123-desktop.apps.googleusercontent.com",
            "client_secret": "never-return-this",
        },
    )
    assert configured.status_code == 200 and "never-return-this" not in configured.text


def test_mail_trigger_requires_explicit_grant_not_a_time(client, auth):
    route = client.post(
        "/api/v1/routes",
        headers=auth,
        json={
            "name": "Mail route",
            "provider": "ollama",
            "model": "qwen-test:latest",
        },
    ).json()
    payload = {
        "template_id": "communication",
        "name": "Mail monitor",
        "responsibilities": "Review college emails",
        "route_id": route["id"],
        "trigger_type": "gmail_message",
    }
    assert client.post("/api/v1/crew", headers=auth, json=payload).status_code == 400
    response = client.post(
        "/api/v1/crew", headers=auth, json={**payload, "google_resources": ["gmail"]}
    )
    assert response.status_code == 201
    crew = response.json()
    assert crew["schedule_time"] is None and not crew["is_running"]
    assert "google:gmail:read" in crew["permissions"]
    start = client.post(f"/api/v1/crew/{crew['id']}/start", headers=auth)
    assert start.status_code == 409 and "Google" in start.json()["detail"]


def test_core_creates_and_starts_a_ready_daily_crew_once(client, auth):
    from uuid import uuid4

    from mavo.schemas import CoordinatorDecision
    from tests.test_planner import Coordinator

    route = client.post(
        "/api/v1/routes",
        headers=auth,
        json={"name": "Agent", "provider": "ollama", "model": "test"},
    ).json()
    coordinator = Coordinator()
    coordinator.decision = CoordinatorDecision.model_validate(
        {
            "reply": "Set up your reminder.",
            "crew_proposal": {
                "template_id": "schedule",
                "name": "Watch reminder",
                "responsibilities": "Wear watch",
                "trigger_type": "daily",
                "schedule_time": "22:00",
                "route_id": route["id"],
            },
        }
    )
    client.app.state.crew_runtime.planner.coordinator = coordinator
    payload = {"message": "Remind me to wear my watch every night", "request_id": str(uuid4())}
    first = client.post("/api/v1/core/turn", headers=auth, json=payload)
    assert first.status_code == 200, first.text
    assert first.json()["crew"] is None
    draft = first.json()["draft"]
    assert not draft["is_running"] and client.get("/api/v1/crew", headers=auth).json() == []
    crew = client.post(f"/api/v1/crew/{draft['id']}/access", headers=auth, json={}).json()
    assert crew["is_running"] and crew["schedule_time"] == "22:00"
    assert crew["permissions"] == ["model:invoke"]
    again = client.post("/api/v1/core/turn", headers=auth, json=payload).json()
    assert again["crew"]["id"] == crew["id"] and len(coordinator.calls) == 1


def test_connect_all_requests_only_supported_read_services_and_can_cancel(client, auth):
    assert client.post("/api/v1/auth/google/check").status_code == 401
    assert client.post("/api/v1/auth/google/cancel").status_code == 401
    assert (
        client.put(
            "/api/v1/auth/google/client",
            headers=auth,
            json={
                "client_id": "123-desktop.apps.googleusercontent.com",
            },
        ).status_code
        == 200
    )
    response = client.post("/api/v1/auth/google/start", headers=auth, json={"all_services": True})
    assert response.status_code == 200
    assert response.json()["resources"] == [
        "gmail",
        "calendar",
        "tasks",
        "drive",
        "contacts",
        "youtube",
    ]
    query = parse_qs(urlsplit(response.json()["authorization_url"]).query)
    assert set(query["scope"][0].split()) == IDENTITY_SCOPES | set(SCOPES.values())
    pending = client.get("/api/v1/auth/google", headers=auth).json()
    assert pending["phase"] == "pending" and pending["resources"] == []
    cancelled = client.post("/api/v1/auth/google/cancel", headers=auth).json()
    assert cancelled["phase"] == "idle" and not cancelled["signed_in"]
    checks = client.post("/api/v1/auth/google/check", headers=auth).json()
    assert all(s["status"] == "not_authorized" for s in checks["services"])
    assert (
        client.post(
            "/api/v1/auth/google/start",
            headers=auth,
            json={
                "resources": ["sheets"],
            },
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/auth/google/start",
            headers=auth,
            json={
                "all_products": True,
            },
        ).status_code
        == 422
    )
