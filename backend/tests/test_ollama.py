"""Mavo's local models: every request sets how much the model may read and asks for no thinking
out loud; an older Ollama is asked again without that switch; reasoning that leaks into the answer
is removed; and each way it can fail is said plainly, with what to do."""

import json

import httpx
import pytest

from mavo.config import Settings
from mavo.ollama import OllamaClient, OllamaUnavailable

MESSAGES = [{"role": "user", "content": "Pick a quote."}]


def client_with(handler, **kw):
    return OllamaClient("http://127.0.0.1:11434", transport=httpx.MockTransport(handler), **kw)


@pytest.mark.asyncio
async def test_every_request_sets_the_context_and_turns_thinking_off():
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": "Stay curious."}})

    assert await client_with(handler, context=8192).chat("qwen2.5:3b", MESSAGES) == "Stay curious."
    body = sent[0]
    assert (
        body["options"] == {"num_ctx": 8192}
        and body["think"] is False
        and body["keep_alive"] == "10m"
    )
    assert Settings().ollama_context == 8192


@pytest.mark.asyncio
async def test_an_older_ollama_is_asked_again_without_the_switch_and_reasoning_is_removed():
    sent = []

    def handler(request):
        body = json.loads(request.content)
        sent.append(body)
        if "think" in body:
            return httpx.Response(400, json={"error": 'unknown field "think"'})
        return httpx.Response(
            200, json={"message": {"content": "<think>pick something short</think>\nStay curious."}}
        )

    assert await client_with(handler).chat("qwen3:4b", MESSAGES) == "Stay curious."
    assert len(sent) == 2 and "think" not in sent[1] and sent[1]["options"]["num_ctx"] == 8192


@pytest.mark.parametrize(
    "response,said",
    [
        (
            httpx.Response(404, json={"error": "model 'qwen2.5:3b' not found"}),
            "isn't downloaded. In a terminal run: ollama pull qwen2.5:3b",
        ),
        (
            httpx.Response(200, json={"message": {"content": "<think>only reasoning</think>"}}),
            "answered with nothing",
        ),
    ],
)
@pytest.mark.asyncio
async def test_failures_are_said_plainly_with_what_to_do(response, said):
    with pytest.raises(OllamaUnavailable, match=said.replace("(", r"\(").replace(".", r"\.")):
        await client_with(lambda request: response).chat("qwen2.5:3b", MESSAGES)


@pytest.mark.asyncio
async def test_ollama_not_running_is_said_plainly():
    def refuse(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(OllamaUnavailable, match="Ollama is not running"):
        await client_with(refuse).chat("qwen2.5:3b", MESSAGES)
