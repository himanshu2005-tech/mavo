import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException

from mavo.database import Conversation, CoordinatorSession, CoordinatorTurn, CrewInstance, utcnow
from mavo.schemas import CoordinatorDecision, CoordinatorRequest
from tests.test_planner import Coordinator, mail_decision


@pytest.fixture
def chats(client, auth):
    runtime = client.app.state.crew_runtime
    coordinator = Coordinator()
    runtime.planner.coordinator = coordinator
    return client, auth, runtime, coordinator


def create(env):
    client, auth, _, _ = env
    response = client.post("/api/v1/core/conversations", headers=auth)
    assert response.status_code == 201, response.text
    return response.json()


def send(env, chat, message, request_id=None):
    client, auth, _, _ = env
    return client.post(
        "/api/v1/core/turn",
        headers=auth,
        json={
            "message": message,
            "session_id": chat["session_id"],
            "conversation_id": chat["id"],
            "request_id": request_id or str(uuid4()),
        },
    )


def test_new_chats_are_persisted_without_model_calls(chats):
    client, auth, runtime, coordinator = chats
    first, second = create(chats), create(chats)
    assert first["id"] != second["id"]
    assert first["session_id"] != second["session_id"]
    assert first["title"] == "New chat" and first["messages"] == []
    assert coordinator.calls == []
    saved = client.get("/api/v1/core/conversations", headers=auth).json()
    assert {c["id"] for c in saved} == {first["id"], second["id"]}
    assert client.get("/api/v1/core/conversation", headers=auth).json()["conversation"] is None
    with runtime.factory() as session:
        assert session.query(CoordinatorSession).count() == 2


def test_messages_and_titles_are_isolated_and_latest_active_chat_is_first(chats):
    client, auth, _, coordinator = chats
    first, second = create(chats), create(chats)
    a = send(chats, first, "Remember apples").json()
    b = send(chats, second, "Discuss oranges").json()
    assert a["conversation"]["title"] == "Remember apples"
    assert b["conversation"]["title"] == "Discuss oranges"
    assert [m.content for m in coordinator.calls[-1].messages] == ["Discuss oranges"]
    a = send(chats, first, "Continue apples").json()
    assert [m.content for m in coordinator.calls[-1].messages] == [
        "Remember apples",
        "Hello",
        "Continue apples",
    ]
    assert len(a["conversation"]["messages"]) == 4
    assert len(b["conversation"]["messages"]) == 2
    saved = client.get("/api/v1/core/conversations", headers=auth).json()
    assert saved[0]["id"] == first["id"]
    assert client.get("/api/v1/conversations/" + first["id"], headers=auth).status_code == 200


def test_crew_questionnaires_do_not_follow_the_user_into_another_chat(chats):
    client, auth, _, coordinator = chats
    first, second = create(chats), create(chats)
    coordinator.decision = mail_decision(questions=["Which senders?"])
    draft = send(chats, first, "Watch my college email").json()["draft"]
    coordinator.decision = CoordinatorDecision(reply="Hello")
    unrelated = send(chats, second, "Hi").json()
    assert unrelated["crew"] is None and unrelated["draft"] is None
    assert coordinator.calls[-1].context.current_crew_id is None
    for chat, expected in [(first, draft["id"]), (second, None)]:
        history = client.get(
            "/api/v1/core/conversation", params={"conversation_id": chat["id"]}, headers=auth
        ).json()
        assert history["planning_crew_id"] == expected
        assert [d["id"] for d in history["setup_drafts"]] == ([expected] if expected else [])
    send(chats, first, "Only my teachers")
    assert coordinator.calls[-1].context.current_crew_id == draft["id"]
    assert client.get("/api/v1/crew", headers=auth).json() == []


def test_rename_is_validated_and_survives_further_messages(chats):
    client, auth, _, _ = chats
    chat = create(chats)
    endpoint = "/api/v1/conversations/" + chat["id"]
    for title in [" ", "x" * 161]:
        assert client.patch(endpoint, headers=auth, json={"title": title}).status_code == 422
    renamed = client.patch(endpoint, headers=auth, json={"title": "  College plans  "}).json()
    assert renamed["title"] == "College plans" and renamed["session_id"] == chat["session_id"]
    assert send(chats, chat, "A question").json()["conversation"]["title"] == "College plans"


def test_request_idempotency_is_per_exact_chat_request(chats):
    _, _, _, coordinator = chats
    first, second = create(chats), create(chats)
    request_id = str(uuid4())
    first_reply = send(chats, first, "Hello", request_id).json()
    repeated = send(chats, first, "Hello", request_id).json()
    assert repeated["conversation"] == first_reply["conversation"]
    assert len(coordinator.calls) == 1
    assert send(chats, second, "Hello", request_id).status_code == 409


def test_delete_removes_only_that_chat_and_context_not_its_crew(chats):
    client, auth, runtime, coordinator = chats
    first, second = create(chats), create(chats)
    coordinator.decision = mail_decision(questions=["Which senders?"])
    draft = send(chats, first, "Email helper").json()["draft"]
    assert client.delete("/api/v1/conversations/" + first["id"], headers=auth).status_code == 204
    with runtime.factory() as session:
        assert session.get(Conversation, first["id"]) is None
        assert session.get(CoordinatorSession, first["session_id"]) is None
        assert session.query(CoordinatorTurn).count() == 0
        assert session.get(CrewInstance, draft["id"]) is not None
    assert [c["id"] for c in client.get("/api/v1/core/conversations", headers=auth).json()] == [
        second["id"]
    ]
    assert send(chats, first, "Do not resurrect deleted chat").status_code == 404
    assert (
        client.get(
            "/api/v1/core/conversation", params={"conversation_id": first["id"]}, headers=auth
        ).status_code
        == 404
    )


@pytest.mark.asyncio
async def test_other_chats_can_reply_while_one_is_busy_and_deletion_is_blocked(chats):
    client, auth, runtime, coordinator = chats
    first, second = create(chats), create(chats)
    entered, release = asyncio.Event(), asyncio.Event()

    async def complete(turn):
        if turn.messages[-1].content == "Slow request":
            entered.set()
            await release.wait()
        return CoordinatorDecision(reply="Done"), "fixture"

    coordinator.complete = complete
    pending = asyncio.create_task(
        runtime.planner.turn(
            CoordinatorRequest(
                message="Slow request",
                session_id=first["session_id"],
                conversation_id=first["id"],
                request_id=str(uuid4()),
            )
        )
    )
    await asyncio.wait_for(entered.wait(), 3)
    try:
        assert (
            client.delete("/api/v1/conversations/" + first["id"], headers=auth).status_code == 409
        )
        assert send(chats, first, "Duplicate in-flight request").status_code == 409
        other = send(chats, second, "Independent request")
        assert other.status_code == 200
        assert len(other.json()["conversation"]["messages"]) == 2
    finally:
        release.set()
    completed = await asyncio.wait_for(pending, 3)
    assert completed["conversation"]["id"] == first["id"]


@pytest.mark.asyncio
async def test_expired_chat_deleted_during_inference_cannot_create_a_crew(chats):
    client, auth, runtime, coordinator = chats
    chat = create(chats)
    entered, release = asyncio.Event(), asyncio.Event()

    async def complete(turn):
        entered.set()
        await release.wait()
        return mail_decision(), "fixture"

    coordinator.complete = complete
    pending = asyncio.create_task(
        runtime.planner.turn(
            CoordinatorRequest(
                message="Expired request",
                session_id=chat["session_id"],
                conversation_id=chat["id"],
                request_id=str(uuid4()),
            )
        )
    )
    await asyncio.wait_for(entered.wait(), 3)
    try:
        with runtime.factory() as session:
            session.get(CoordinatorSession, chat["session_id"]).lease_until = utcnow() - timedelta(
                seconds=1
            )
            session.commit()
        assert client.delete("/api/v1/conversations/" + chat["id"], headers=auth).status_code == 204
    finally:
        release.set()
    with pytest.raises(HTTPException) as rejected:
        await asyncio.wait_for(pending, 3)
    assert rejected.value.status_code == 409
    with runtime.factory() as session:
        assert session.query(CrewInstance).count() == 0


def test_chat_apis_require_authentication(client):
    assert client.get("/api/v1/core/conversations").status_code == 401
    assert client.post("/api/v1/core/conversations").status_code == 401
