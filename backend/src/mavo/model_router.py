from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass

import httpx

from mavo.credentials import CredentialStore
from mavo.ollama import OllamaClient, OllamaUnavailable
from mavo.runtime_activity import ActivityFeed
from mavo.schemas import ModelInfo, ProviderId, ProviderStatus


class ModelProviderError(RuntimeError):
    pass


# Room for one reply. A short answer needs little; a whole file written by the build team does
# not fit in 4096 tokens: a Pomodoro timer's index.html was cut off mid-line at 14,000 characters.
# Set for the duration of a call that writes files (team_build), and read here.
LONG_REPLY: ContextVar[bool] = ContextVar("long_reply", default=False)


def reply_tokens() -> int:
    return 16000 if LONG_REPLY.get() else 4096


def reply_seconds() -> float:
    return 300.0 if LONG_REPLY.get() else 120.0


@dataclass(frozen=True)
class ProviderDefinition:
    id: ProviderId
    name: str
    base_url: str


PROVIDERS: dict[ProviderId, ProviderDefinition] = {
    "openai": ProviderDefinition("openai", "OpenAI", "https://api.openai.com/v1"),
    "anthropic": ProviderDefinition("anthropic", "Anthropic", "https://api.anthropic.com/v1"),
    "openrouter": ProviderDefinition("openrouter", "OpenRouter", "https://openrouter.ai/api/v1"),
    "airouter": ProviderDefinition("airouter", "AIRouter", "https://api.airouter.in/v1"),
}


class ModelRouter:
    def __init__(
        self,
        ollama: OllamaClient,
        credentials: CredentialStore,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.ollama = ollama
        self.credentials = credentials
        self.transport = transport
        self.activity = ActivityFeed()

    async def statuses(self, defaults: dict[str, str]) -> list[ProviderStatus]:
        ollama_status = await self.ollama.status()
        cloud_statuses = await asyncio.gather(
            *(self.cloud_status(provider, defaults.get(provider)) for provider in PROVIDERS)
        )
        return [ollama_status, *cloud_statuses]

    async def cloud_status(self, provider: ProviderId, default_model: str | None) -> ProviderStatus:
        definition = PROVIDERS[provider]
        credential = self.credentials.get(provider)
        if not credential:
            return ProviderStatus(
                id=provider,
                name=definition.name,
                configured=False,
                connected=False,
                url=definition.base_url,
                default_model=default_model,
            )
        try:
            models = await self._cloud_models(provider, credential)
            return ProviderStatus(
                id=provider,
                name=definition.name,
                configured=True,
                connected=True,
                url=definition.base_url,
                models=models,
                default_model=default_model,
            )
        except ModelProviderError as exc:
            return ProviderStatus(
                id=provider,
                name=definition.name,
                configured=True,
                connected=False,
                url=definition.base_url,
                default_model=default_model,
                error=str(exc),
            )

    async def chat(
        self,
        provider: ProviderId,
        model: str,
        messages: list[dict[str, str]],
        system: str,
        credential_id: str | None = None,
    ) -> str:
        with self.activity.model_call(provider, model, credential_id):
            return await self._chat(provider, model, messages, system, credential_id)

    async def _chat(
        self,
        provider: ProviderId,
        model: str,
        messages: list[dict[str, str]],
        system: str,
        credential_id: str | None = None,
    ) -> str:
        if provider == "ollama":
            try:
                return await self.ollama.chat(
                    model, [{"role": "system", "content": system}, *messages]
                )
            except OllamaUnavailable as exc:
                raise ModelProviderError(str(exc)) from exc

        credential = self.credentials.get(credential_id or provider)
        if not credential:
            raise ModelProviderError(f"Configure {PROVIDERS[provider].name} before using it")
        try:
            if provider == "openai":
                return await self._openai_chat(credential, model, messages, system)
            if provider == "anthropic":
                return await self._anthropic_chat(credential, model, messages, system)
            return await self._compatible_chat(provider, credential, model, messages, system)
        except (AttributeError, IndexError, TypeError) as exc:
            raise ModelProviderError(f"{PROVIDERS[provider].name} returned invalid data") from exc

    async def _cloud_models(self, provider: ProviderId, credential: str) -> list[ModelInfo]:
        definition = PROVIDERS[provider]
        headers = self._headers(provider, credential)
        try:
            async with httpx.AsyncClient(
                base_url=definition.base_url,
                headers=headers,
                timeout=10.0,
                transport=self.transport,
            ) as client:
                response = await client.get("/models")
                response.raise_for_status()
                items = response.json().get("data", [])
                models = [
                    ModelInfo(name=item["id"])
                    for item in items
                    if isinstance(item, dict) and isinstance(item.get("id"), str)
                ]
                return sorted(models, key=lambda item: item.name)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {401, 403}:
                raise ModelProviderError("The saved API key was rejected") from exc
            raise ModelProviderError(f"{definition.name} model discovery failed") from exc
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise ModelProviderError(f"{definition.name} is not reachable") from exc

    @staticmethod
    def _headers(provider: ProviderId, credential: str) -> dict[str, str]:
        if provider == "anthropic":
            return {"x-api-key": credential, "anthropic-version": "2023-06-01"}
        return {"Authorization": f"Bearer {credential}"}

    async def _openai_chat(
        self, credential: str, model: str, messages: list[dict[str, str]], system: str
    ) -> str:
        payload = {
            "model": model,
            "instructions": system,
            "input": messages,
            "store": False,
            "max_output_tokens": reply_tokens(),
        }
        data = await self._post("openai", credential, "/responses", payload)
        text_parts = [
            content.get("text", "")
            for output in data.get("output", [])
            if isinstance(output, dict) and output.get("type") == "message"
            for content in output.get("content", [])
            if isinstance(content, dict) and content.get("type") == "output_text"
        ]
        return self._require_text("".join(text_parts), "OpenAI")

    async def _anthropic_chat(
        self, credential: str, model: str, messages: list[dict[str, str]], system: str
    ) -> str:
        payload = {
            "model": model,
            "system": system,
            "messages": messages,
            "max_tokens": reply_tokens(),
        }
        data = await self._post("anthropic", credential, "/messages", payload)
        text_parts = [
            item.get("text", "")
            for item in data.get("content", [])
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        return self._require_text("".join(text_parts), "Anthropic")

    async def _compatible_chat(
        self,
        provider: ProviderId,
        credential: str,
        model: str,
        messages: list[dict[str, str]],
        system: str,
    ) -> str:
        payload = {
            "model": model,
            "max_tokens": reply_tokens(),
            "messages": [{"role": "system", "content": system}, *messages],
        }
        if provider in {"airouter", "openrouter"}:
            # Thinking models (DeepSeek V4 Flash) spent all 16,000 tokens of a Coder's reply
            # thinking and wrote nothing. Low effort: 1,024 tokens of thought, then the answer.
            payload["reasoning"] = {"effort": "low"}
        text = await self._compatible_reply(provider, credential, payload)
        if text is None:
            # It thought until the reply ran out (a writer given three research sources) and the
            # task step failed. Once more, with the room and time of a long reply and a nudge to
            # think briefly.
            again = {
                **payload,
                "max_tokens": max(reply_tokens(), 16000),
                "messages": [
                    {
                        "role": "system",
                        "content": system + "\n\nThink briefly, then write the complete answer: "
                        "the answer is what counts.",
                    },
                    *messages,
                ],
            }
            room = LONG_REPLY.set(True)
            try:
                text = await self._compatible_reply(provider, credential, again)
            finally:
                LONG_REPLY.reset(room)
            if text is None:
                raise ModelProviderError(
                    f"The model on {PROVIDERS[provider].name} used its whole reply thinking and "
                    "wrote no answer, twice. Try again, or choose a model that is not a thinking "
                    "model."
                )
        return self._require_text(text, PROVIDERS[provider].name)

    async def _compatible_reply(self, provider, credential, payload) -> str | None:
        """The reply's text; None when the model wrote only thinking."""
        data = await self._post(provider, credential, "/chat/completions", payload)
        choices = data.get("choices", [])
        message = choices[0].get("message", {}) if choices else {}
        text = message.get("content") or ""
        if not str(text).strip() and (message.get("reasoning") or message.get("reasoning_content")):
            return None
        return text

    async def _post(
        self, provider: ProviderId, credential: str, path: str, payload: dict[str, object]
    ) -> dict[str, object]:
        definition = PROVIDERS[provider]
        try:
            async with httpx.AsyncClient(
                base_url=definition.base_url,
                headers=self._headers(provider, credential),
                timeout=reply_seconds(),
                transport=self.transport,
            ) as client:
                response = await client.post(path, json=payload)
                response.raise_for_status()
                result = response.json()
                if not isinstance(result, dict):
                    raise ModelProviderError(f"{definition.name} returned an invalid response")
                return result
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {401, 403}:
                raise ModelProviderError("The provider rejected the saved API key") from exc
            detail = ""
            try:
                error = exc.response.json().get("error", {})
                message = error.get("message", "") if isinstance(error, dict) else ""
                if isinstance(message, str):
                    detail = ": " + message.replace(credential, "[redacted]")[:300]
            except (ValueError, AttributeError):
                pass
            raise ModelProviderError(
                f"{definition.name} could not complete the request "
                f"({exc.response.status_code}){detail}"
            ) from exc
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise ModelProviderError(f"{definition.name} is not reachable") from exc

    @staticmethod
    def _require_text(value: object, provider_name: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ModelProviderError(f"{provider_name} returned no text")
        return value.strip()
