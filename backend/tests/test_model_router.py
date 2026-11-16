from __future__ import annotations

import asyncio

import httpx
import pytest

from mavo.credentials import MemoryCredentialStore
from mavo.model_router import ModelProviderError, ModelRouter
from mavo.ollama import OllamaClient


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "finish,content,reasoning,error,requests",
    [
        ("stop", '{"summary":"Test"}', None, None, 1),
        # Cut off, or only thinking: ONE retry with more room, then a plain error (never more).
        ("length", '{"summary":"cut', None, "wrote no answer twice", 2),
        ("stop", "", "Private reasoning", "wrote no answer twice", 2),
    ],
)
async def test_worker_json_mode_is_scoped_and_retries_a_lost_answer_once(
    finish, content, reasoning, error, requests
):
    import json

    from mavo.model_router import WORKER_SCHEMA

    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": finish,
                        "message": {"content": content, "reasoning": reasoning},
                    }
                ]
            },
        )

    credentials = MemoryCredentialStore()
    credentials.set("airouter", "fixture-key")
    router = ModelRouter(
        OllamaClient("http://127.0.0.1:11434"), credentials, httpx.MockTransport(handler)
    )
    token = WORKER_SCHEMA.set({"type": "object"})
    try:
        if error:
            with pytest.raises(ModelProviderError, match=error):
                await router.chat("airouter", "fixture", [], "Return JSON")
        else:
            assert await router.chat("airouter", "fixture", [], "Return JSON") == content
    finally:
        WORKER_SCHEMA.reset(token)
    assert len(sent) == requests and sent[0]["response_format"] == {"type": "json_object"}
    assert all(body["response_format"] == {"type": "json_object"} for body in sent)
    assert WORKER_SCHEMA.get() is None


@pytest.mark.asyncio
async def test_a_thinking_model_that_answers_on_its_retry_is_not_an_error():
    import json

    from mavo.model_router import WORKER_SCHEMA

    replies = iter([{"content": "", "reasoning": "thinking..."}, {"content": '{"summary":"ok"}'}])

    def handler(request):
        return httpx.Response(
            200, json={"choices": [{"finish_reason": "stop", "message": next(replies)}]}
        )

    credentials = MemoryCredentialStore()
    credentials.set("airouter", "fixture-key")
    router = ModelRouter(
        OllamaClient("http://127.0.0.1:11434"), credentials, httpx.MockTransport(handler)
    )
    token = WORKER_SCHEMA.set({"type": "object"})
    try:
        assert json.loads(await router.chat("airouter", "fixture", [], "Return JSON")) == {
            "summary": "ok"
        }
    finally:
        WORKER_SCHEMA.reset(token)


@pytest.mark.asyncio
async def test_model_dispatch_has_a_shared_concurrency_and_output_ceiling():
    from mavo.model_router import reply_tokens

    router = ModelRouter(
        OllamaClient("http://127.0.0.1:11434"),
        MemoryCredentialStore(),
        max_concurrent_calls=2,
        max_output_tokens=1024,
    )
    active, peak = 0, 0

    async def local(*args):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        assert reply_tokens() == 1024
        await asyncio.sleep(0.01)
        active -= 1
        return "Fixture result"

    router._chat = local
    await asyncio.gather(*(router.chat("ollama", "fixture", [], "System") for _ in range(8)))
    assert peak == 2


@pytest.mark.asyncio
async def test_queued_model_work_rechecks_authorization_before_sending_the_prompt():
    from mavo.model_router import BEFORE_DISPATCH

    router = ModelRouter(
        OllamaClient("http://127.0.0.1:11434"), MemoryCredentialStore(), max_concurrent_calls=1
    )
    entered, release = asyncio.Event(), asyncio.Event()
    seen = []

    async def local(*args):
        seen.append("sent")
        entered.set()
        await release.wait()
        return "Fixture result"

    router._chat = local
    first = asyncio.create_task(router.chat("ollama", "fixture", [], "System"))
    await entered.wait()

    def revoked():
        raise ModelProviderError("Permission revoked while waiting")

    token = BEFORE_DISPATCH.set(revoked)
    second = asyncio.create_task(router.chat("ollama", "fixture", [], "Private prompt"))
    BEFORE_DISPATCH.reset(token)
    release.set()
    await first
    with pytest.raises(ModelProviderError, match="revoked"):
        await second
    assert seen == ["sent"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "path", "response", "expected"),
    [
        (
            "anthropic",
            "/v1/messages",
            {"content": [{"type": "text", "text": "Claude response."}]},
            "Claude response.",
        ),
        (
            "openrouter",
            "/api/v1/chat/completions",
            {"choices": [{"message": {"content": "Router response."}}]},
            "Router response.",
        ),
        (
            "airouter",
            "/v1/chat/completions",
            {"choices": [{"message": {"content": "AIRouter response."}}]},
            "AIRouter response.",
        ),
    ],
)
async def test_routes_provider_specific_chat_contracts(
    provider: str, path: str, response: dict, expected: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == path
        if provider == "anthropic":
            assert request.headers["x-api-key"] == "test-credential"
            assert request.headers["anthropic-version"] == "2023-06-01"
        else:
            assert request.headers["authorization"] == "Bearer test-credential"
        return httpx.Response(200, json=response)

    credentials = MemoryCredentialStore()
    credentials.set(provider, "test-credential")
    router = ModelRouter(
        OllamaClient("http://127.0.0.1:11434"),
        credentials,
        httpx.MockTransport(handler),
    )

    result = await router.chat(
        provider,  # type: ignore[arg-type]
        "test-model",
        [{"role": "user", "content": "Hello"}],
        "System instruction",
    )

    assert result == expected


@pytest.mark.asyncio
async def test_thinking_models_are_asked_to_think_briefly_and_a_reply_of_only_thought_says_so():
    """DeepSeek V4 Flash spent a Coder's whole 16,000-token reply thinking and wrote nothing."""
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        sent.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": "", "reasoning": "x" * 500}, "finish_reason": "length"}
                ]
            },
        )

    credentials = MemoryCredentialStore()
    credentials.set("airouter", "test-credential")
    router = ModelRouter(
        OllamaClient("http://127.0.0.1:11434"), credentials, httpx.MockTransport(handler)
    )
    with pytest.raises(ModelProviderError, match="used its whole reply thinking"):
        await router.chat("airouter", "deepseek/deepseek-v4-flash", [], "Test")
    assert sent[0]["reasoning"] == {"effort": "low"}
    # It was asked once more, with a long reply's room and a nudge, before it gave up.
    assert len(sent) == 2 and sent[0]["max_tokens"] == 4096 and sent[1]["max_tokens"] == 16000
    assert "Think briefly" in sent[1]["messages"][0]["content"]


@pytest.mark.asyncio
async def test_a_reply_of_only_thought_is_asked_for_once_more_and_the_answer_comes_through():
    """A task's writer step failed outright: "used its whole reply thinking"."""
    replies = [
        {"content": "", "reasoning": "x" * 500},
        {"content": "The three problems.", "reasoning": "y"},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": replies.pop(0)}]})

    credentials = MemoryCredentialStore()
    credentials.set("airouter", "test-credential")
    router = ModelRouter(
        OllamaClient("http://127.0.0.1:11434"), credentials, httpx.MockTransport(handler)
    )
    assert (
        await router.chat("airouter", "deepseek/deepseek-v4-flash", [], "Test")
        == "The three problems."
    )


@pytest.mark.asyncio
async def test_invalid_provider_data_is_an_explicit_model_error():
    credentials = MemoryCredentialStore()
    credentials.set("airouter", "test-credential")
    router = ModelRouter(
        OllamaClient("http://127.0.0.1:11434"),
        credentials,
        httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "choices": [{"message": None}],
                },
            )
        ),
    )
    with pytest.raises(ModelProviderError, match="invalid data"):
        await router.chat("airouter", "test-model", [], "Test")


@pytest.mark.asyncio
async def test_provider_error_redacts_credential_and_explains_unsupported_model():
    credentials = MemoryCredentialStore()
    credentials.set("airouter", "test-credential")
    router = ModelRouter(
        OllamaClient("http://127.0.0.1:11434"),
        credentials,
        httpx.MockTransport(
            lambda _: httpx.Response(
                400,
                json={
                    "error": {"message": "Unsupported model; key test-credential"},
                },
            )
        ),
    )
    with pytest.raises(ModelProviderError) as error:
        await router.chat("airouter", "bad-model", [], "Test")
    assert "Unsupported model" in str(error.value)
    assert "test-credential" not in str(error.value)
