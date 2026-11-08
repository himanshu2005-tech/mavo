from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from mavo.database import CoordinatorSession, CoordinatorTurn, CrewInstance
from mavo.schemas import CoordinatorDecision


def test_mail_draft_requires_read_access_and_does_not_collect_device_metrics():
    from mavo.schemas import CrewDraft

    draft = CrewDraft(
        template_id="communication",
        name="College mail",
        responsibilities="Read deadlines",
        trigger_type="gmail_message",
        requested_device_metrics=True,
        requested_google_capabilities=["gmail:body"],
    )
    assert draft.requested_google_capabilities == ["gmail:body", "gmail:read"]
    assert not draft.requested_device_metrics


def test_weather_setup_needs_both_global_and_crew_access_not_device_location(planning):
    client, auth, runtime, coordinator = planning
    route = client.post(
        "/api/v1/routes",
        headers=auth,
        json={
            "name": "Travel model",
            "provider": "ollama",
            "model": "test",
        },
    ).json()
    coordinator.decision = CoordinatorDecision.model_validate(
        {
            "reply": "I created a destination weather helper.",
            "crew_proposal": {
                "template_id": "travel",
                "name": "Travel weather",
                "responsibilities": "Watch rain in Chennai before my flight.",
                "trigger_type": "interval",
                "requested_weather": True,
            },
        }
    )
    crew = turn(planning, "Watch destination weather")["draft"]
    assert "weather:read" not in crew["permissions"] and not crew["is_running"]
    saved = client.post(
        f"/api/v1/crew/{crew['id']}/access",
        headers=auth,
        json={
            "route_id": route["id"],
            "weather": True,
        },
    ).json()
    assert "weather:read" in saved["permissions"] and not saved["is_running"]
    client.put("/api/v1/resources/access", headers=auth, json={"weather": True, "location": False})
    runtime.planner.next_reconcile = 0
    runtime.planner.reconcile_pending()
    saved = client.get("/api/v1/crew", headers=auth).json()[0]
    assert saved["is_running"] and "device:read" not in saved["permissions"]
    client.put("/api/v1/resources/access", headers=auth, json={"weather": False})
    assert not client.get("/api/v1/crew", headers=auth).json()[0]["is_running"]


class Coordinator:
    def __init__(self):
        self.calls = []
        self.decision = CoordinatorDecision(reply="Hello")

    async def status(self):
        return {
            "configured": True,
            "connected": True,
            "provider": "AIRouter",
            "model": "minimax-test",
            "error": None,
        }

    async def complete(self, payload):
        self.calls.append(payload)
        return self.decision, "minimax-test"


@pytest.fixture
def planning(client, auth):
    runtime = client.app.state.crew_runtime
    coordinator = Coordinator()
    runtime.coordinator = runtime.planner.coordinator = coordinator
    runtime.router.chat = AsyncMock(side_effect=AssertionError("Agent key used by coordinator"))
    return client, auth, runtime, coordinator


def turn(env, message, **kwargs):
    client, auth, _, _ = env
    response = client.post(
        "/api/v1/core/turn",
        headers=auth,
        json={"message": message, "request_id": str(uuid4()), **kwargs},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_the_reply_is_saved_after_the_message_it_answers(planning):
    """Saved together at the end of the turn, they tied, and a chat sometimes showed the reply
    above the message."""
    first = turn(planning, "I have nothing planned for today.")["conversation"]["messages"]
    second = turn(planning, "Then plan it.", conversation_id=None)["conversation"]["messages"]
    for said in (first, second):
        assert [m["role"] for m in said[-2:]] == ["user", "assistant"]
        assert said[-2]["created_at"] < said[-1]["created_at"]


def mail_decision(**changes):
    data = {
        "reply": "I set up your mail assistant.",
        "crew_proposal": {
            "template_id": "communication",
            "name": "Important mail",
            "responsibilities": "Highlight urgent college mail; ignore promotions.",
            "trigger_type": "gmail_message",
            "requested_google_capabilities": ["gmail:read", "gmail:body"],
            "requested_notifications": True,
        },
    }
    data.update(changes)
    return CoordinatorDecision.model_validate(data)


def test_core_works_without_any_agent_connection_and_does_not_fallback(planning):
    client, auth, runtime, coordinator = planning
    result = turn(planning, "Hello")
    assert result["model"] == "minimax-test" and result["crew"] is None
    assert client.get("/api/v1/routes", headers=auth).json() == []
    runtime.router.chat.assert_not_called()
    assert client.post("/api/v1/core/chat", headers=auth, json={}).status_code == 410
    assert client.post("/api/v1/chat", headers=auth, json={}).status_code == 410
    assert len(coordinator.calls) == 1


def test_incomplete_chat_setup_is_only_a_draft_and_cannot_be_activated(planning):
    client, auth, _, coordinator = planning
    coordinator.decision = mail_decision(questions=["Which senders and deadlines matter?"])
    result = turn(planning, "I need important emails without spam")
    assert result["crew"] is None
    crew = result["draft"]
    assert crew["configuration_plan"]["phase"] == "needs_input"
    assert crew["permissions"] == ["model:invoke"] and crew["provider"] == "unassigned"
    assert not crew["is_running"]
    assert client.get("/api/v1/crew", headers=auth).json() == []
    assert client.get("/api/v1/crew-drafts", headers=auth).json()[0]["id"] == crew["id"]
    assert crew["configuration_plan"]["draft"]["trigger_type"] == "gmail_message"
    assert crew["schedule_time"] is None
    assert client.post(f"/api/v1/crew/{crew['id']}/start", headers=auth).status_code == 409
    assert (
        client.patch(
            f"/api/v1/crew/{crew['id']}", headers=auth, json={"status": "active"}
        ).status_code
        == 409
    )


def test_retries_are_idempotent_and_followups_refine_same_crew(planning):
    client, auth, runtime, coordinator = planning
    coordinator.decision = mail_decision(questions=["What counts as important?"])
    request = {"message": "Create mail helper", "request_id": str(uuid4())}
    first = client.post("/api/v1/core/turn", headers=auth, json=request).json()
    duplicate = client.post("/api/v1/core/turn", headers=auth, json=request).json()
    assert first["draft"]["id"] == duplicate["draft"]["id"] and len(coordinator.calls) == 1
    coordinator.decision = mail_decision()
    refined = turn(planning, "Urgent college deadlines only", crew_id=first["draft"]["id"])
    assert refined["draft"]["id"] == first["draft"]["id"]
    assert refined["draft"]["configuration_plan"]["questions"] == []
    assert client.get("/api/v1/crew", headers=auth).json() == []
    assert coordinator.calls[-1].context.current_crew_id == first["draft"]["id"]
    assert not coordinator.calls[-1].context.available_crews
    assert len(coordinator.calls[-1].messages) == 3
    changed = client.post(
        "/api/v1/core/turn", headers=auth, json={**request, "message": "Different payload"}
    )
    assert changed.status_code == 409


def test_access_save_and_connection_readiness_start_event_worker(planning, monkeypatch):
    client, auth, runtime, coordinator = planning
    route = client.post(
        "/api/v1/routes",
        headers=auth,
        json={"name": "Agent only", "provider": "ollama", "model": "test"},
    ).json()
    coordinator.decision = mail_decision()
    created = turn(planning, "Make my college mail assistant")["draft"]
    assert created["route_id"] == route["id"] and not created["is_running"]
    body = {
        "capabilities": ["gmail:read"],
        "actions": {},
        "notifications": True,
        "route_id": route["id"],
    }
    saved = client.post(f"/api/v1/crew/{created['id']}/access", headers=auth, json=body)
    assert saved.status_code == 200 and not saved.json()["is_running"]
    assert "gmail:body" in saved.json()["configuration_plan"]["denied_resources"]
    coordinator.decision = mail_decision()
    coordinator.decision.crew_proposal.requested_google_capabilities = ["gmail:read"]
    turn(planning, "Use headers only", crew_id=created["id"])
    client.post(f"/api/v1/crew/{created['id']}/access", headers=auth, json=body)
    assert "gmail:body" in coordinator.calls[-1].context.denied_resources
    monkeypatch.setattr(runtime.planner.google, "status", lambda: {"capabilities": ["gmail:read"]})
    runtime.planner.next_reconcile = 0
    runtime.planner.reconcile_pending()
    active = client.get("/api/v1/crew", headers=auth).json()[0]
    assert active["is_running"] and active["trigger_type"] == "gmail_message"
    assert active["schedule_time"] is None and active["configuration_plan"]["phase"] == "ready"
    assert "notifications:show" in active["permissions"]
    runtime.router.chat.assert_not_called()


def test_stop_intent_survives_reconciliation(planning):
    client, auth, runtime, coordinator = planning
    client.post(
        "/api/v1/routes",
        headers=auth,
        json={"name": "Agent", "provider": "ollama", "model": "test"},
    )
    coordinator.decision = CoordinatorDecision.model_validate(
        {
            "reply": "Done",
            "crew_proposal": {
                "template_id": "research",
                "name": "Outline",
                "responsibilities": "Draft an outline",
                "trigger_type": "manual",
            },
        }
    )
    draft = turn(planning, "Create an outline helper")["draft"]
    assert client.get("/api/v1/crew", headers=auth).json() == []
    crew = client.post(f"/api/v1/crew/{draft['id']}/access", headers=auth, json={}).json()
    assert crew["is_running"]
    client.post(f"/api/v1/crew/{crew['id']}/stop", headers=auth)
    runtime.planner.next_reconcile = 0
    runtime.planner.reconcile_pending()
    assert not client.get("/api/v1/crew", headers=auth).json()[0]["is_running"]


def test_setup_question_pauses_existing_worker_without_creating_incomplete_plan(planning):
    client, auth, _, coordinator = planning
    route = client.post(
        "/api/v1/routes",
        headers=auth,
        json={"name": "Agent", "provider": "ollama", "model": "test"},
    ).json()
    crew = client.post(
        "/api/v1/crew",
        headers=auth,
        json={
            "name": "Research",
            "template_id": "research",
            "responsibilities": "Research help",
            "route_id": route["id"],
        },
    ).json()
    client.post(f"/api/v1/crew/{crew['id']}/start", headers=auth)
    coordinator.decision = CoordinatorDecision(
        reply="One detail first.", questions=["Which topic?"]
    )
    result = turn(planning, "Change this research task", crew_id=crew["id"])
    assert not result["crew"]["is_running"]
    assert result["crew"]["question"] == "Which topic?"
    assert result["crew"]["configuration_plan"] == {}


def test_existing_crew_receives_delegated_message_without_new_instance(planning):
    client, auth, runtime, coordinator = planning
    route = client.post(
        "/api/v1/routes",
        headers=auth,
        json={"name": "Agent", "provider": "ollama", "model": "test"},
    ).json()
    crew = client.post(
        "/api/v1/crew",
        headers=auth,
        json={
            "name": "Research",
            "template_id": "research",
            "responsibilities": "Research help",
            "route_id": route["id"],
        },
    ).json()
    coordinator.decision = CoordinatorDecision(
        reply="I passed that to your Crew.",
        delegate_to=crew["id"],
        instruction="Draft a literature review outline",
    )
    result = turn(planning, "Draft an outline")
    assert result["crew"]["id"] == crew["id"] and result["crew"]["is_running"]
    with runtime.factory() as session:
        assert session.get(CrewInstance, crew["id"]).observation["user_messages"] == [
            "Draft a literature review outline"
        ]
    assert len(client.get("/api/v1/crew", headers=auth).json()) == 1


def test_plans_and_dialogue_survive_service_recreation(planning):
    client, auth, runtime, coordinator = planning
    coordinator.decision = mail_decision(questions=["Who matters?"])
    session_id = str(uuid4())
    first = turn(planning, "Watch my inbox", session_id=session_id)
    from mavo.crew_planner import CrewPlanner

    runtime.planner = CrewPlanner(
        runtime, coordinator, runtime.planner.google, runtime.planner.credentials
    )
    coordinator.decision = mail_decision()
    second = client.portal.call(
        runtime.planner.turn,
        __import__("mavo.schemas", fromlist=["CoordinatorRequest"]).CoordinatorRequest(
            message="My teachers",
            session_id=session_id,
            crew_id=first["draft"]["id"],
            request_id=str(uuid4()),
        ),
    )
    assert first["draft"]["id"] == second["draft"]["id"]
    with runtime.factory() as session:
        assert len(session.get(CoordinatorSession, session_id).messages) == 4
        assert len(list(session.query(CoordinatorTurn))) == 2


def test_verify_only_publishes_after_questions_and_permissions_are_complete(planning, monkeypatch):
    client, auth, runtime, coordinator = planning
    client.post(
        "/api/v1/routes",
        headers=auth,
        json={"name": "Agent", "provider": "ollama", "model": "test"},
    )
    coordinator.decision = mail_decision(questions=["Which senders?"])
    draft = turn(planning, "Watch my college mail")["draft"]
    body = {"capabilities": ["gmail:read", "gmail:body"], "notifications": True}
    monkeypatch.setattr(
        runtime.planner.google, "status", lambda: {"capabilities": ["gmail:read", "gmail:body"]}
    )
    pending = client.post(f"/api/v1/crew/{draft['id']}/access", headers=auth, json=body)
    assert pending.status_code == 409
    assert "setup questions" in pending.json()["detail"]
    unchanged = client.get("/api/v1/crew-drafts", headers=auth).json()[0]
    assert unchanged["permissions"] == ["model:invoke"] and not unchanged["is_running"]
    assert client.get("/api/v1/crew", headers=auth).json() == []
    coordinator.decision = mail_decision()
    finished = turn(planning, "Only my lecturers", crew_id=draft["id"])
    assert finished["crew"] is None and finished["draft"]["status"] == "draft"
    endpoint = f"/api/v1/crew/{draft['id']}/access"
    published = client.post(endpoint, headers=auth, json=body).json()
    assert published["status"] == "active" and published["is_running"]
    assert published["configuration_plan"]["verified_at"]
    assert client.get("/api/v1/crew-drafts", headers=auth).json() == []
    assert len(client.get("/api/v1/crew", headers=auth).json()) == 1
    assert client.post(endpoint, headers=auth, json=body).status_code == 200
    assert len(client.get("/api/v1/crew", headers=auth).json()) == 1


def test_discarded_draft_cannot_be_resurrected_by_a_retried_chat_request(planning):
    client, auth, _, coordinator = planning
    coordinator.decision = mail_decision(questions=["Which inbox?"])
    request = {"message": "Create an inbox helper", "request_id": str(uuid4())}
    result = client.post("/api/v1/core/turn", headers=auth, json=request).json()
    draft = result["draft"]
    assert client.delete(f"/api/v1/crew/{draft['id']}", headers=auth).status_code == 204
    assert client.get("/api/v1/crew-drafts", headers=auth).json() == []
    retried = client.post("/api/v1/core/turn", headers=auth, json=request).json()
    assert retried["crew"] is None and retried["draft"] is None
    assert (
        client.post(f"/api/v1/crew/{draft['id']}/access", headers=auth, json={}).status_code == 404
    )


def test_requested_action_inherits_defaults_only_after_questions_and_keeps_crew_override(planning):
    client, auth, runtime, coordinator = planning
    client.put(
        "/api/v1/action-policies",
        headers=auth,
        json={
            "operation": "browser.open",
            "rule": {"enabled": True, "mode": "restricted", "domains": ["example.com"]},
        },
    )
    coordinator.decision = CoordinatorDecision.model_validate(
        {
            "reply": "Draft ready",
            "questions": ["Which site?"],
            "crew_proposal": {
                "template_id": "operator",
                "name": "Portal",
                "responsibilities": "Review example.com",
                "requested_actions": [{"operation": "browser.open", "domains": ["example.com"]}],
            },
        }
    )
    draft = turn(planning, "Create a portal watcher")["draft"]
    with runtime.factory() as s:
        assert not runtime.actions.rules.read(s, "browser.open", draft["id"])["enabled"]
    assert client.get("/api/v1/action-policies/browser.open/crew", headers=auth).json() == []
    coordinator.decision.questions = []
    draft = turn(planning, "Use example.com", crew_id=draft["id"])["draft"]
    assert "action:browser.open" in draft["permissions"]
    with runtime.factory() as s:
        rule = runtime.actions.rules.read(s, "browser.open", draft["id"])
        assert (
            rule["enabled"]
            and rule["domains"] == ["example.com"]
            and rule["origin"] == "saved-default"
        )
        assert not runtime.actions.rules.read(s, "browser.read", draft["id"])["enabled"]
    assignments = client.get("/api/v1/action-policies/browser.open/crew", headers=auth).json()
    assert assignments[0]["name"] == "Portal" and assignments[0]["authorized"]
    client.put(
        "/api/v1/action-policies",
        headers=auth,
        json={"operation": "browser.open", "crew_id": draft["id"], "rule": {"enabled": False}},
    )
    turn(planning, "Keep the same site", crew_id=draft["id"])
    with runtime.factory() as s:
        assert not runtime.actions.rules.read(s, "browser.open", draft["id"])["enabled"]


def test_inherited_action_never_expands_site_limits_or_silently_publishes(planning):
    client, auth, _, coordinator = planning
    client.post(
        "/api/v1/routes",
        headers=auth,
        json={"name": "Agent", "provider": "ollama", "model": "test"},
    )
    client.put(
        "/api/v1/action-policies",
        headers=auth,
        json={
            "operation": "browser.open",
            "rule": {"enabled": True, "mode": "restricted", "domains": ["example.com"]},
        },
    )
    coordinator.decision = CoordinatorDecision.model_validate(
        {
            "reply": "Draft",
            "crew_proposal": {
                "name": "Other portal",
                "template_id": "operator",
                "responsibilities": "Read another portal",
                "requested_actions": [{"operation": "browser.open", "domains": ["other.example"]}],
            },
        }
    )
    draft = turn(planning, "Watch another portal")["draft"]
    result = client.post(f"/api/v1/crew/{draft['id']}/access", headers=auth, json={}).json()
    assert result["status"] == "draft" and not result["is_running"]
    assert any("limits" in text for text in result["configuration_plan"]["missing"])
    assert client.get("/api/v1/crew", headers=auth).json() == []


def test_every_connected_capability_fits_in_the_chat_context():
    from mavo.connectors.catalog import CAPABILITIES
    from mavo.coordinator_contract import PlannerContext

    context = PlannerContext(
        local_time="2026-11-04T09:00:00+05:30",
        connected_connector_capabilities=list(CAPABILITIES),
    )
    assert len(context.connected_connector_capabilities) == len(CAPABILITIES) > 16


def test_a_chat_message_works_with_github_and_every_news_feed_connected(planning, monkeypatch):
    """A fixed limit of 16 once made every message fail with "The Crew setup changed" for a person
    who had connected GitHub and a dozen feeds (17 capabilities)."""
    from mavo.connectors.catalog import CAPABILITIES

    _, _, runtime, coordinator = planning
    everything = list(CAPABILITIES)
    monkeypatch.setattr(
        runtime.connectors,
        "status",
        lambda provider: {"capabilities": [c for c in everything if c.split(":")[0] == provider]},
    )
    coordinator.decision = CoordinatorDecision.model_validate({"reply": "Hello."})
    assert turn(planning, "Check my mails and tell me what is recent")["reply"]


def test_the_planner_is_told_two_timings_are_two_crew():
    """ "An inbox summary every 30 min and remind me instantly if anything is important" became one
    30-minute Crew: "instantly" can never be a timer, and a Crew has one trigger."""
    from mavo.coordinator_contract import PlannerContext, coordinator_prompt

    text = coordinator_prompt(PlannerContext(local_time="2026-11-06T10:00:00+05:30"))
    assert "TWO TIMINGS for the same thing" in text and "Never fold 'instantly'" in text


def test_a_job_now_can_come_with_the_crew_that_carry_it_on():
    """ "Suggest guests now, and tell me as soon as one replies": a task AND Crew in one reply was
    rejected twice and the turn failed. A misplaced target_crew_id inside a proposal did too."""
    from mavo.schemas import CoordinatorDecision

    crew = {
        "template_id": "communication",
        "name": "Guest Reply Watcher",
        "responsibilities": "Tell me when a guest replies.",
        "trigger_type": "gmail_message",
        "requested_google_capabilities": ["gmail:read"],
    }
    task = {
        "title": "Guest shortlist",
        "deliver": "A shortlist",
        "steps": [
            {"id": "w", "kind": "helper", "role": "writer", "title": "Write", "instructions": "x"}
        ],
    }
    both = CoordinatorDecision.model_validate(
        {
            "reply": "Started.",
            "task": task,
            "crew_proposals": [crew, {**crew, "name": "Recording Scheduler"}],
        }
    )
    assert both.task and len(both.crew_proposals) == 2
    moved = CoordinatorDecision.model_validate(
        {"reply": "Refined.", "crew_proposal": {**crew, "target_crew_id": "abc"}}
    )
    assert moved.target_crew_id == "abc"
    import pytest

    with pytest.raises(ValueError):  # a task still cannot come with a question
        CoordinatorDecision.model_validate(
            {"reply": "?", "task": task, "questions": ["Which one?"]}
        )
