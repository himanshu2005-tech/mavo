from __future__ import annotations

import httpx
import pytest

from mavo.credentials import MemoryCredentialStore
from mavo.model_router import ModelProviderError, ModelRouter
from mavo.ollama import OllamaClient


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
