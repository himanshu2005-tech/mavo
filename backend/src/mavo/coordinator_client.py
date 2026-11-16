import json
import time
from urllib.parse import urlsplit

import httpx

from mavo.coordinator_contract import GatewayTurn, parse_decision
from mavo.core_errors import PROBLEMS, problem
from mavo.credentials import CredentialError
from mavo.publisher import publisher_core_url


class CoordinatorUnavailable(RuntimeError):
    def __init__(self, message=None, *, code=None, request_id=None):
        self.problem = problem(code, request_id) if code else None
        super().__init__(
            message
            or (
                self.problem["message"] + " " + self.problem["action"]
                if self.problem
                else "Mavo Core could not complete this request."
            )
        )


class CoordinatorClient:
    def __init__(self, settings, firebase, transport=None):
        self.url = publisher_core_url(settings.core_gateway_url)
        self.firebase, self.transport = firebase, transport
        self.cached = None
        self.checked_at = 0
        self.lifecycle = None

    @property
    def local(self):
        return bool(self.url and urlsplit(self.url).hostname in {"127.0.0.1", "localhost", "::1"})

    def invalidate_status(self):
        self.cached = None
        if self.lifecycle:
            self.lifecycle.retry()

    async def status(self):
        if not self.url:
            return {
                "configured": False,
                "connected": False,
                "provider": "AIRouter",
                "model": None,
                "error": "The publisher has not configured Mavo Core yet.",
            }
        if self.cached and self.cached.get("connected") and time.monotonic() - self.checked_at < 5:
            return self.cached
        try:
            async with httpx.AsyncClient(
                transport=self.transport, timeout=5, trust_env=not self.local
            ) as client:
                response = await client.get(self.url + "/health")
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, dict) or data.get("service") != "mavo-core":
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
            state = self.lifecycle.state if self.lifecycle and self.lifecycle.enabled else None
            explanation = {
                "starting": "Starting Mavo Core… Your Crew can continue working.",
                "recovering": "Reconnecting Mavo Core… Your Crew can continue working.",
                "failed": (
                    "Mavo Core could not start. Check its local service log, "
                    "then retry the connection."
                ),
                "port_conflict": (
                    "Mavo Core’s local port is occupied by an unavailable or "
                    "different service. Mavo has not stopped it."
                ),
            }.get(state, "Mavo cannot reach its assistant service. Your Crew are unaffected.")
            self.cached = {
                "configured": True,
                "connected": False,
                "provider": "AIRouter",
                "model": None,
                "error": explanation,
                "state": state or "unreachable",
            }
        self.checked_at = time.monotonic()
        return self.cached

    async def streamed(self, client, headers, body, emit, request_id):
        """The turn from Core's streaming endpoint, its reply text sent to the chat as it is
        written. None from a Core without one (the plain request is used instead)."""
        async with client.stream(
            "POST", self.url + "/v1/core/turn/stream", headers=headers, json=body
        ) as response:
            if response.status_code in {404, 405}:
                return None
            if response.status_code >= 400:
                await response.aread()
                response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                event = json.loads(line[5:])
                if event.get("type") == "reply":
                    emit(str(event.get("text", "")))
                elif event.get("type") == "done":
                    return event["result"]
                elif event.get("type") == "error":
                    code = (event.get("error") or {}).get("code")
                    raise CoordinatorUnavailable(
                        code=code if code in PROBLEMS else "core_gateway_failed",
                        request_id=request_id,
                    )
        raise CoordinatorUnavailable(code="core_bad_response", request_id=request_id)

    async def workflow(self, request: dict) -> str:
        """Mavo Core writes a workflow (workflow_write.py): its JSON text, unchecked."""
        request_id = request["request_id"]
        if not self.url:
            raise CoordinatorUnavailable(code="core_not_configured", request_id=request_id)
        try:
            await self.firebase.refresh()
            tokens = json.loads(self.firebase.credentials.get("firebase-tokens") or "{}")
        except (ValueError, TypeError, KeyError, CredentialError):
            tokens = {}
        if not isinstance(tokens, dict) or not tokens.get("id_token"):
            raise CoordinatorUnavailable(code="core_sign_in_required", request_id=request_id)
        try:
            async with httpx.AsyncClient(
                transport=self.transport,
                timeout=httpx.Timeout(300, connect=15),
                trust_env=not self.local,
            ) as client:
                response = await client.post(
                    self.url + "/v1/core/workflow",
                    headers={"Authorization": "Bearer " + tokens["id_token"]},
                    json=request,
                )
                response.raise_for_status()
                return str(response.json()["text"])
        except httpx.HTTPStatusError as exc:
            code = {
                401: "core_sign_in_required",
                # a gateway from before workflows: restarting it gives it this endpoint
                404: "core_protocol_mismatch",
                405: "core_protocol_mismatch",
                429: "core_usage_limit",
                504: "core_timeout",
            }
            raise CoordinatorUnavailable(
                code=code.get(exc.response.status_code, "core_gateway_failed"),
                request_id=request_id,
            ) from None
        except (httpx.HTTPError, KeyError, ValueError):
            raise CoordinatorUnavailable(
                code="core_gateway_failed", request_id=request_id
            ) from None

    async def complete(self, payload: GatewayTurn):
        if not self.url:
            raise CoordinatorUnavailable(code="core_not_configured", request_id=payload.request_id)
        try:
            await self.firebase.refresh()
            if not self.firebase.status()["signed_in"]:
                raise CoordinatorUnavailable(
                    code="core_sign_in_required", request_id=payload.request_id
                )
            tokens = json.loads(self.firebase.credentials.get("firebase-tokens") or "{}")
            if not isinstance(tokens, dict):
                raise ValueError("Invalid session token record")
        except (ValueError, TypeError, KeyError, CredentialError):
            raise CoordinatorUnavailable(
                code="core_sign_in_required", request_id=payload.request_id
            ) from None
        if not tokens.get("id_token"):
            raise CoordinatorUnavailable(
                code="core_sign_in_required", request_id=payload.request_id
            )
        try:
            async with httpx.AsyncClient(
                transport=self.transport,
                timeout=httpx.Timeout(200, connect=15),
                trust_env=not self.local,
            ) as client:
                body = payload.model_dump(mode="json")
                if not body["context"].get("memory"):
                    # Left out when empty: a gateway from before shared memory still accepts it.
                    body["context"].pop("memory", None)
                headers = {"Authorization": "Bearer " + tokens["id_token"]}
                from mavo.reply_stream import LIVE

                emit = LIVE.get()
                data = (
                    await self.streamed(client, headers, body, emit, payload.request_id)
                    if emit
                    else None
                )
                if data is None:
                    response = await client.post(
                        self.url + "/v1/core/turn", headers=headers, json=body
                    )
                    response.raise_for_status()
                    data = response.json()
            return parse_decision(json.dumps(data["decision"])), str(data.get("model", "Minimax"))[
                :160
            ]
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            code = {
                401: "core_sign_in_required",
                403: "core_sign_in_required",
                409: "core_request_pending",
                422: "core_protocol_mismatch",
                429: "core_usage_limit",
                504: "core_timeout",
            }.get(status, "core_gateway_failed")
            try:
                upstream = exc.response.json().get("error", {})
                if isinstance(upstream, dict) and upstream.get("code") in PROBLEMS:
                    code = upstream["code"]
            except (ValueError, AttributeError, TypeError):
                pass  # Unknown HTML/proxy errors must not expose arbitrary response text.
            raise CoordinatorUnavailable(code=code, request_id=payload.request_id) from None
        except httpx.TimeoutException:
            raise CoordinatorUnavailable(
                code="core_timeout", request_id=payload.request_id
            ) from None
        except httpx.HTTPError:
            raise CoordinatorUnavailable(
                code="core_unreachable", request_id=payload.request_id
            ) from None
        except (ValueError, KeyError, TypeError, IndexError):
            raise CoordinatorUnavailable(
                code="core_bad_response", request_id=payload.request_id
            ) from None
