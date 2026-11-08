# ruff: noqa: F811 - `planning` is a fixture, imported and then named as a parameter
"""Mavo's shared memory: "my mom is Sita", told to one agent, is known by every agent, the chat
and one-time tasks, and can be seen, corrected and removed."""

from mavo.coordinator_client import CoordinatorClient
from mavo.coordinator_contract import GatewayTurn, PlannerContext
from tests.test_crew_ask import news_crew, sorter
from tests.test_planner import planning, turn  # noqa: F401
from tests.test_scenarios import checkpoint, decide, run_work


def second_agent(runtime, first: str, name: str) -> str:
    """Another agent on the same model connection as the first."""
    from mavo.database import CrewInstance

    with runtime.factory() as session:
        crew = session.get(CrewInstance, first)
        copy = CrewInstance(
            **{
                c.name: getattr(crew, c.name)
                for c in CrewInstance.__table__.columns
                if c.name != "id"
            }
            | {"name": name}
        )
        session.add(copy)
        session.commit()
        return copy.id


def memory(client, auth):
    return client.get("/api/v1/memory", headers=auth).json()


def test_a_fact_told_to_one_agent_is_known_by_every_agent(planning):
    client, auth, runtime, coordinator = planning
    told_to = news_crew(client, auth, runtime, name="Lunch Reminder")
    other = second_agent(runtime, told_to, "Birthday Watch")
    calls = len(coordinator.calls)
    sorter(
        runtime,
        {
            "kind": "fact",
            "facts": ["My mom is Sita"],
            "facts_replace": [],
            "ack": "Got it, I'll remember your mom is Sita.",
        },
    )
    item = client.post(
        f"/api/v1/crew/{told_to}/asks", headers=auth, json={"question": "my mom is sita"}
    ).json()
    assert item["kind"] == "fact" and item["answer"] == "Got it, I'll remember your mom is Sita."
    assert len(coordinator.calls) == calls  # nothing more to answer
    [fact] = memory(client, auth)
    assert fact["text"] == "My mom is Sita" and fact["source"] == "Lunch Reminder"
    # The other agent's next review knows it.
    chat = run_work(runtime, other, checkpoint("Nothing new."))
    prompt = chat.await_args_list[0].args[2][0]["content"]
    assert "shared with every agent" in prompt and "- My mom is Sita" in prompt
    # So does the chat planner.
    decide(planning, reply="Okay.")
    turn(planning, "remind my mom to call me")
    assert coordinator.calls[-1].context.memory == ["My mom is Sita"]


def test_a_correction_replaces_the_old_fact_and_a_question_that_tells_one_is_still_answered(
    planning,
):
    client, auth, runtime, _ = planning
    crew = news_crew(client, auth, runtime)
    sorter(runtime, {"kind": "fact", "facts": ["My mom is Sita"], "ack": "Noted."})
    client.post(f"/api/v1/crew/{crew}/asks", headers=auth, json={"question": "my mom is sita"})
    sorter(runtime, {"kind": "question", "facts": ["My mom is Gita"], "facts_replace": [1]})
    decide(planning, reply="Here is the news for her.")
    item = client.post(
        f"/api/v1/crew/{crew}/asks",
        headers=auth,
        json={"question": "actually mom is gita, what news would she like?"},
    ).json()
    assert item["kind"] == "question" and item["answer"] == "Here is the news for her."
    assert [f["text"] for f in memory(client, auth)] == ["My mom is Gita"]


def test_the_chat_remembers_what_the_person_tells_it(planning):
    client, auth, _, _ = planning
    decide(planning, reply="Nice to know.", remember=["I work at Infosys", "I work at Infosys."])
    turn(planning, "fyi i work at infosys")
    [fact] = memory(client, auth)
    assert fact["text"] == "I work at Infosys" and fact["source"] == "Chat"


def test_memory_can_be_added_by_hand_and_removed(planning):
    client, auth, _, _ = planning
    listed = client.post("/api/v1/memory", headers=auth, json={"text": "My dog is Bruno"}).json()
    assert [f["text"] for f in listed] == ["My dog is Bruno"] and listed[0]["source"] == "you"
    assert client.delete(f"/api/v1/memory/{listed[0]['id']}", headers=auth).json() == []
    assert client.delete("/api/v1/memory/nope", headers=auth).status_code == 404


def test_an_empty_memory_is_left_out_so_an_older_gateway_still_accepts_the_turn():
    import asyncio

    import httpx

    sent = []

    def handler(request):
        import json

        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"decision": {"reply": "Hi."}, "model": "m"})

    class Firebase:
        credentials = type("C", (), {"get": staticmethod(lambda key: '{"id_token": "t"}')})()

        async def refresh(self):
            return None

        def status(self):
            return {"signed_in": True}

    client = CoordinatorClient.__new__(CoordinatorClient)
    client.url, client.firebase, client.transport = (
        "http://gw",
        Firebase(),
        httpx.MockTransport(handler),
    )
    for memory_now in ([], ["My mom is Sita"]):
        payload = GatewayTurn(
            request_id="00000000-0000-0000-0000-000000000000",
            messages=[{"role": "user", "content": "hi"}],
            context=PlannerContext(local_time="now", memory=memory_now),
        )
        asyncio.run(client.complete(payload))
    assert "memory" not in sent[0]["context"]
    assert sent[1]["context"]["memory"] == ["My mom is Sita"]
