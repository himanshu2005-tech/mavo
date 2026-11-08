import json
import time

import httpx

from mavo.coordinator_contract import GatewayTurn
from mavo.publisher import publisher_core_url
from mavo.schemas import CoordinatorDecision


class CoordinatorUnavailable(RuntimeError):
    pass


class CoordinatorClient:
    def __init__(self, settings, firebase, transport=None):
        self.url = publisher_core_url(settings.core_gateway_url)
        self.firebase, self.transport = firebase, transport
        self.cached = None
        self.checked_at = 0

    async def status(self):
        if not self.url:
            return {
                "configured": False,
                "connected": False,
                "provider": "AIRouter",
                "model": None,
                "error": "The publisher has not configured Mavo Core yet.",
            }
        if self.cached and time.monotonic() - self.checked_at < 15:
            return self.cached
        try:
            async with httpx.AsyncClient(transport=self.transport, timeout=5) as client:
                response = await client.get(self.url + "/health")
                response.raise_for_status()
                data = response.json()
                if data.get("service") != "mavo-core":
                    raise ValueError("Wrong service")
            self.cached = {
                "configured": bool(data.get("configured")),
                "connected": bool(data.get("configured")),
                "provider": "AIRouter",
                "model": str(data.get("model", ""))[:160],
                "error": None
                if data.get("configured")
                else "Mavo Core is awaiting publisher setup.",
            }
        except (httpx.HTTPError, ValueError):
            self.cached = {
                "configured": True,
                "connected": False,
                "provider": "AIRouter",
                "model": None,
                "error": "Mavo cannot reach its assistant service. Your Crew are unaffected.",
            }
        self.checked_at = time.monotonic()
        return self.cached

    async def complete(self, payload: GatewayTurn):
        if not self.url:
            raise CoordinatorUnavailable(
                "Mavo Core needs publisher setup. Agent keys are only for Crew."
            )
        await self.firebase.refresh()
        if not self.firebase.status()["signed_in"]:
            raise CoordinatorUnavailable("Sign in to your Mavo account again.")
        tokens = json.loads(self.firebase.credentials.get("firebase-tokens") or "{}")
        if not tokens.get("id_token"):
            raise CoordinatorUnavailable("Sign in to your Mavo account again.")
        try:
            async with httpx.AsyncClient(
                transport=self.transport, timeout=httpx.Timeout(200, connect=15)
            ) as client:
                body = payload.model_dump(mode="json")
                if not body["context"].get("memory"):
                    # Left out when empty: a gateway from before shared memory still accepts it.
                    body["context"].pop("memory", None)
                response = await client.post(
                    self.url + "/v1/core/turn",
                    headers={"Authorization": "Bearer " + tokens["id_token"]},
                    json=body,
                )
                response.raise_for_status()
                data = response.json()
            return CoordinatorDecision.model_validate(data["decision"]), str(
                data.get("model", "Minimax")
            )[:160]
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 504:
                raise CoordinatorUnavailable(
                    "Mavo Core's model took too long. Your saved Crew is unchanged. Try again."
                ) from None
            if exc.response.status_code == 429:
                raise CoordinatorUnavailable(
                    "Mavo Core's usage limit has been reached. Try again later."
                ) from None
            raise CoordinatorUnavailable(
                "Mavo Core could not finish this turn. Retry your message."
            ) from None
        except (httpx.HTTPError, ValueError, KeyError):
            raise CoordinatorUnavailable(
                "Mavo Core is temporarily unavailable. Retry this message shortly."
            ) from None
