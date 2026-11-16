"""Conversation isolation over the real authenticated event API, synthetic data only."""

from mavo.database import Conversation
from mavo.orchestration import event


def test_chat_excludes_global_and_other_chats_even_when_pending(client, auth):
    runtime = client.app.state.crew_runtime
    with runtime.factory() as session:
        session.add_all(
            [
                Conversation(id="day-chat", title="Daily planner"),
                Conversation(id="other-chat", title="Other task"),
            ]
        )
        session.flush()
        for key, chat in [
            ("global-news", None),
            ("day-question", "day-chat"),
            ("other-question", "other-chat"),
        ]:
            event(
                session,
                key,
                "question",
                "core",
                {"summary": key},
                conversation_id=chat,
                pending=True,
            )
        session.commit()
    own = client.get("/api/v1/orchestration/events?conversation_id=day-chat", headers=auth)
    assert own.status_code == 200
    assert [item["payload"]["summary"] for item in own.json()] == ["day-question"]
    assert own.json()[0]["conversation_id"] == "day-chat"
    # The workspace endpoint still exposes pending requests for Updates and Crew grants.
    assert len(client.get("/api/v1/orchestration/events", headers=auth).json()) == 3
    assert (
        client.get("/api/v1/orchestration/events?conversation_id=missing", headers=auth).status_code
        == 404
    )
    assert client.get("/api/v1/orchestration/events?conversation_id=day-chat").status_code == 401
