"""Words appear as they are written: the chat's reply streams while the planner writes it, from
the person's own model or from Mavo Core, and nothing else ever streams into the chat."""

import json
from uuid import uuid4

import httpx
import pytest

from mavo.credentials import MemoryCredentialStore
from mavo.model_router import ModelRouter
from mavo.ollama import OllamaClient
from mavo.reply_stream import LIVE, Relay, partial_reply
from mavo.schemas import CoordinatorDecision
from tests.test_planner import Coordinator


def test_the_reply_field_is_read_from_json_that_is_still_arriving():
    assert partial_reply('{"questions": [], "rep') is None
    assert partial_reply('{"reply": "Hel') == "Hel"
    assert partial_reply('{"reply": "Line one\\nLine') == "Line one\nLine"
    assert partial_reply('{"reply": "Say \\"hi\\"", "task": null}') == 'Say "hi"'
    assert partial_reply('{"reply": "caf\\u00e9 and \\u00') == "café and "  # half an escape waits
    assert partial_reply('{"reply": "ends with \\') == "ends with "
    sent = []
    relay = Relay(sent.append)
    for chunk in ['{"re', 'ply": "Dr', "afted ", 'it", "x": 1}']:
        relay.feed(chunk)
    assert sent == ["Dr", "Drafted ", "Drafted it"]


@pytest.mark.asyncio
async def test_the_persons_own_model_streams_only_while_the_chat_listens():
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body.get("stream", False))
        if body.get("stream"):
            chunks = ['{"reply": "Plan', "ning your ", 'day", "questions": []}']
            lines = "".join(
                "data: " + json.dumps({"choices": [{"delta": {"content": c}}]}) + "\n\n"
                for c in chunks
            )
            return httpx.Response(200, text=lines + "data: [DONE]\n\n")
        return httpx.Response(200, json={"choices": [{"message": {"content": "plain"}}]})

    credentials = MemoryCredentialStore()
    credentials.set("airouter", "fixture-key")
    router = ModelRouter(
        OllamaClient("http://127.0.0.1:11434"), credentials, httpx.MockTransport(handler)
    )
    assert await router.chat("airouter", "fixture", [], "Plan") == "plain"  # nobody listening
    emitted = []
    live = LIVE.set(emitted.append)
    try:
        raw = await router.chat("airouter", "fixture", [], "Plan")
    finally:
        LIVE.reset(live)
    assert json.loads(raw)["reply"] == "Planning your day"
    assert emitted == ["Plan", "Planning your ", "Planning your day"]
    assert seen == [False, True]


class Streaming(Coordinator):
    """Mavo Core writing its reply in pieces, as the real one streams it."""

    async def complete(self, payload):
        emit = LIVE.get()
        assert emit, "the chat's planning call streams"
        for text in ("Hello", "Hello there"):
            emit(text)
        self.calls.append(payload)
        return CoordinatorDecision(reply="Hello there, all done"), "minimax-test"


def test_the_chat_receives_the_reply_as_it_is_written_then_the_saved_turn(client, auth):
    runtime = client.app.state.crew_runtime
    runtime.coordinator = runtime.planner.coordinator = Streaming()
    chat = client.post("/api/v1/core/conversations", headers=auth).json()
    body = {
        "message": "Say hello",
        "session_id": chat["session_id"],
        "conversation_id": chat["id"],
        "request_id": str(uuid4()),
    }
    with client.stream("POST", "/api/v1/core/turn/stream", headers=auth, json=body) as response:
        assert response.status_code == 200
        events = [
            json.loads(line[5:]) for line in response.iter_lines() if line.startswith("data:")
        ]
    assert [e["text"] for e in events if e["type"] == "reply"] == ["Hello", "Hello there"]
    done = events[-1]
    assert done["type"] == "done" and done["result"]["reply"] == "Hello there, all done"
    saved = client.get("/api/v1/core/conversations", headers=auth).json()
    assert saved[0]["messages"][-1]["content"] == "Hello there, all done"
    assert LIVE.get() is None  # nothing after the turn streams into the chat


def test_a_failed_streamed_turn_ends_with_the_same_error_as_a_plain_one(client, auth):
    from mavo.coordinator_client import CoordinatorUnavailable

    class Down(Coordinator):
        async def complete(self, payload):
            raise CoordinatorUnavailable(code="core_unreachable", request_id=payload.request_id)

    runtime = client.app.state.crew_runtime
    runtime.coordinator = runtime.planner.coordinator = Down()
    chat = client.post("/api/v1/core/conversations", headers=auth).json()
    body = {
        "message": "Say hello",
        "session_id": chat["session_id"],
        "conversation_id": chat["id"],
        "request_id": str(uuid4()),
    }
    with client.stream("POST", "/api/v1/core/turn/stream", headers=auth, json=body) as response:
        events = [
            json.loads(line[5:]) for line in response.iter_lines() if line.startswith("data:")
        ]
    assert events[-1]["type"] == "error" and events[-1]["status"] == 503
    assert events[-1]["error"]["code"] == "core_unreachable"
