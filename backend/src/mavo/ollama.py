from __future__ import annotations

import re

import httpx

from mavo.schemas import ModelInfo, ProviderStatus

# A thinking model that writes its reasoning into the answer anyway (older Ollama, or a model that
# ignores the switch): the reasoning is not the answer.
THINKING = re.compile(r"<think>.*?</think>\s*", re.S | re.I)


class OllamaUnavailable(RuntimeError):
    pass


class OllamaClient:
    """Mavo's local models. Every request says how much it may read (the context window: Ollama's
    own default is small, and a longer agent prompt was silently cut, so the agent worked from
    half its instructions), asks for the answer without thinking out loud (a thinking model could
    spend its whole reply on reasoning and answer nothing), and keeps the model loaded a while,
    so an agent that runs often does not wait for it to load each time."""

    def __init__(
        self,
        base_url: str,
        timeout: float = 180.0,
        transport: httpx.AsyncBaseTransport | None = None,
        context: int = 8192,
        keep_alive: str = "10m",
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.transport = transport
        self.context = context
        self.keep_alive = keep_alive

    async def status(self) -> ProviderStatus:
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url, timeout=3.0, transport=self.transport
            ) as client:
                response = await client.get("/api/tags")
                response.raise_for_status()
                models = [
                    ModelInfo.model_validate(item) for item in response.json().get("models", [])
                ]
                return ProviderStatus(
                    id="ollama",
                    name="Ollama",
                    local=True,
                    configured=True,
                    connected=True,
                    url=self.base_url,
                    models=models,
                    default_model=models[0].name if models else None,
                )
        except (httpx.HTTPError, ValueError, TypeError):
            return ProviderStatus(
                id="ollama",
                name="Ollama",
                local=True,
                configured=True,
                connected=False,
                url=self.base_url,
                error="Ollama is not reachable. Start Ollama and try again.",
            )

    def request(self, model: str, messages: list[dict[str, str]], think: bool | None) -> dict:
        body = {
            "model": model,
            "messages": messages,
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": {"num_ctx": self.context},
        }
        if think is not None:
            body["think"] = think
        return body

    async def chat(self, model: str, messages: list[dict[str, str]]) -> str:
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url, timeout=self.timeout, transport=self.transport
            ) as client:
                response = await client.post("/api/chat", json=self.request(model, messages, False))
                if response.status_code == 400 and "think" in response.text.lower():
                    # An Ollama too old to know the switch (or a model that refuses it): ask
                    # again without it; any reasoning that comes back is taken out below.
                    response = await client.post(
                        "/api/chat", json=self.request(model, messages, None)
                    )
                if response.status_code == 404 or (
                    response.status_code >= 400 and "not found" in response.text.lower()
                ):
                    raise OllamaUnavailable(
                        f"The model {model} isn't downloaded. In a terminal "
                        f"run: ollama pull {model}"
                    )
                response.raise_for_status()
                content = response.json().get("message", {}).get("content")
                content = THINKING.sub("", content).strip() if isinstance(content, str) else ""
                if not content:
                    raise OllamaUnavailable(
                        f"{model} answered with nothing. Try again, or choose another model."
                    )
                return content
        except OllamaUnavailable:
            raise
        except httpx.ConnectError as exc:
            raise OllamaUnavailable(
                "Ollama is not running. Start Ollama (it runs in the system tray) and try again."
            ) from exc
        except httpx.TimeoutException as exc:
            raise OllamaUnavailable(
                f"{model} took longer than {int(self.timeout)} seconds. A smaller model, or one "
                "that fits in the graphics card's memory, answers faster."
            ) from exc
        except (httpx.HTTPError, ValueError, TypeError, AttributeError) as exc:
            raise OllamaUnavailable("Ollama could not complete the request") from exc
