"""Deployable publisher-funded AIRouter gateway. Never bundle its key with the desktop."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sqlite3
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import httpx
import jwt
from cryptography import x509
from fastapi import FastAPI, Header, HTTPException
from pydantic import AliasChoices, Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from mavo.coordinator_contract import (
    GatewayTurn,
    clarification_from_invalid,
    coordinator_prompt,
    draft_refinement_hint,
    parse_decision,
)
from mavo.core_errors import CoreHTTPError, core_error_response
from mavo.credentials import DpapiCredentialStore
from mavo.workflow_write import WorkflowRequest, workflow_messages

logger = logging.getLogger("mavo.publisher")

# Only publisher-owned validation messages may enter diagnostic logs/storage.
# Do not persist model output, validation inputs or arbitrary exception strings.
VALIDATION_REASONS = {
    "Daily reminders deliver text only; use reviews for AI tools",
    "Daily reminder triggers cannot mix with other trigger types",
    "Multiple daily AI review times are not supported; use one review time",
    "Daily reminder triggers require Schedule Crew",
    "Crew requests more than the supported action count",
    "Agent requested an unsupported capability",
    "Agent requested an unsupported action",
    "Agent action set is unsupported or exceeds its operation limit",
    "Agent capability set exceeds its limit",
    "Agent keys must be unique and within the Crew limit",
    "Lead must belong to this Crew",
    "Agent dependency does not exist or references itself",
    "Review a configuration change separately from executing work",
    "A task runs now: it cannot also delegate, change a Crew or ask a question",
    "Use either one proposal or a batch, not both",
    "Ask questions first; propose a Crew batch once they are answered",
    "Batch proposals create new Crew; refine existing Crew one at a time",
    "Delegation needs an instruction and cannot also create a Crew",
}


def validation_issues(error):
    if not isinstance(error, ValidationError):
        return []
    result = []
    for issue in error.errors(include_input=False)[:8]:
        reason = issue["msg"].removeprefix("Value error, ")
        result.append(
            {
                "path": list(issue["loc"]),
                "type": issue["type"],
                "reason": reason if reason in VALIDATION_REASONS else None,
            }
        )
    return result


class GatewaySettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MAVO_PUBLISHER_",
        env_file=(Path(__file__).resolve().parents[3] / ".env", "publisher/.env"),
        extra="ignore",
        populate_by_name=True,
        env_ignore_empty=True,
    )
    data_dir: Path = Path("publisher/data")
    firebase_project_id: str = Field(min_length=6, max_length=64)
    # Mavo Core's own model key. MAVO_CORE_API_KEY is the name in publisher/.env; read only
    # under the prefixed name, the key there was ignored for an older stored one.
    api_key: SecretStr | None = Field(
        default=None,
        repr=False,
        exclude=True,
        validation_alias=AliasChoices("MAVO_PUBLISHER_API_KEY", "MAVO_CORE_API_KEY", "api_key"),
    )
    model: str = "minimax/minimax-m3.1-flash"
    reasoning_effort: Literal["low", "medium", "high", "xhigh", "max"] = "low"
    # 0 means no limit (the default while Mavo is being built); set a number to cap turns.
    user_daily_limit: int = Field(default=0, ge=0, le=10000)
    global_daily_limit: int = Field(default=0, ge=0, le=100000)
    user_minute_limit: int = Field(default=0, ge=0, le=1000)
    # Room for a whole crew design: each agent's instructions now say what it does in its edge
    # cases, and a design cut off at 4096 tokens was refused as "truncated".
    max_output_tokens: int = Field(default=8192, ge=512, le=16000)
    max_concurrency: int = Field(default=4, ge=1, le=32)
    google_client_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "MAVO_PUBLISHER_GOOGLE_CLIENT_ID",
            "MAVO_GOOGLE_CLIENT_ID",
            "GOOGLE_CLIENT_ID",
        ),
    )
    google_client_secret: SecretStr | None = Field(
        default=None,
        repr=False,
        exclude=True,
        validation_alias=AliasChoices(
            "MAVO_PUBLISHER_GOOGLE_CLIENT_SECRET",
            "MAVO_GOOGLE_CLIENT_SECRET",
            "GOOGLE_CLIENT_SECRET",
        ),
    )
    firebase_google_client_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "MAVO_PUBLISHER_FIREBASE_GOOGLE_CLIENT_ID",
            "MAVO_FIREBASE_GOOGLE_CLIENT_ID",
        ),
    )
    firebase_google_client_secret: SecretStr | None = Field(
        default=None,
        repr=False,
        exclude=True,
        validation_alias=AliasChoices(
            "MAVO_PUBLISHER_FIREBASE_GOOGLE_CLIENT_SECRET", "MAVO_FIREBASE_GOOGLE_CLIENT_SECRET"
        ),
    )
    oauth_per_ip_minute: int = Field(default=30, ge=1, le=300)
    oauth_per_ip_day: int = Field(default=2000, ge=1, le=100000)
    oauth_global_day: int = Field(default=20000, ge=1, le=1000000)


class FirebaseVerifier:
    def __init__(self, project, transport=None):
        self.project, self.transport = project, transport
        self.keys, self.expires = {}, 0
        self.lock = asyncio.Lock()

    async def verify(self, token):
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256":
                raise ValueError("Invalid algorithm")
            async with self.lock:
                if self.expires < time.time():
                    async with httpx.AsyncClient(transport=self.transport, timeout=15) as client:
                        response = await client.get(
                            "https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com"
                        )
                        response.raise_for_status()
                        self.keys = response.json()
                        self.expires = time.time() + 3600
                pem = self.keys[header["kid"]]
            key = x509.load_pem_x509_certificate(pem.encode()).public_key()
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                audience=self.project,
                issuer="https://securetoken.google.com/" + self.project,
                leeway=60,
                options={"require": ["sub", "exp", "iat", "auth_time"]},
            )
            if (
                not claims["sub"]
                or len(claims["sub"]) > 128
                or not claims.get("email_verified")
                or claims.get("firebase", {}).get("sign_in_provider") != "google.com"
                or claims["auth_time"] > time.time() + 60
            ):
                raise ValueError("Invalid identity")
            return claims["sub"]
        except (ValueError, KeyError, TypeError, jwt.PyJWTError):
            raise HTTPException(401, "Sign in to your Mavo account again.") from None
        except httpx.HTTPError:
            raise HTTPException(503, "Account verification is temporarily unavailable.") from None


PROVIDER_URL = "https://api.airouter.in/v1/chat/completions"
streams: set = set()  # streamed turns, kept until they end even if the app stops listening


async def streamed(client, headers: dict, body: dict, on_text) -> tuple[str, str | None]:
    """One provider reply, streamed: the "reply" field's text goes to on_text as it arrives."""
    from mavo.reply_stream import Relay

    relay, finish = Relay(on_text), None
    async with client.stream(
        "POST", PROVIDER_URL, headers=headers, json={**body, "stream": True}
    ) as response:
        if response.status_code >= 400:
            await response.aread()
            response.raise_for_status()
        async for line in response.aiter_lines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                choice = (json.loads(data).get("choices") or [{}])[0]
            except (ValueError, AttributeError, IndexError):
                continue
            finish = choice.get("finish_reason") or finish
            content = (choice.get("delta") or {}).get("content")
            if content:
                relay.feed(content)
    return relay.raw, finish


def create_gateway(settings: GatewaySettings, transport=None, verifier=None):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    database = settings.data_dir / "gateway.db"
    with sqlite3.connect(database) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute(
            "CREATE TABLE IF NOT EXISTS turns (id TEXT PRIMARY KEY, uid TEXT NOT NULL, "
            "fingerprint TEXT NOT NULL, day TEXT NOT NULL, state TEXT NOT NULL, "
            "response TEXT, expires REAL NOT NULL, created REAL NOT NULL)"
        )
        db.execute("CREATE INDEX IF NOT EXISTS turns_usage ON turns(day,uid)")
    identity = verifier or FirebaseVerifier(settings.firebase_project_id, transport)
    semaphore = asyncio.Semaphore(settings.max_concurrency)

    def key():
        if settings.api_key:
            return settings.api_key.get_secret_value()
        if os.name == "nt":
            return DpapiCredentialStore(settings.data_dir).get("publisher-airouter")
        return None

    @asynccontextmanager
    async def lifespan(app):
        yield

    app = FastAPI(
        title="Mavo coordinator gateway",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.verifier = identity
    app.add_exception_handler(CoreHTTPError, core_error_response)
    from mavo.publisher_oauth import install_google_token_broker

    install_google_token_broker(app, settings, database, transport)

    @app.get("/health")
    async def health():
        return {
            "service": "mavo-core",
            "configured": bool(key()),
            "model": settings.model,
            "google_oauth_configured": bool(
                settings.google_client_id and settings.google_client_secret
            ),
        }

    @app.post("/v1/core/turn")
    async def turn(payload: GatewayTurn, authorization: str = Header(default="")):
        return await handle(payload, authorization)

    @app.post("/v1/core/workflow")
    async def workflow(payload: WorkflowRequest, authorization: str = Header(default="")):
        """Mavo Core writes an agent's workflow (workflow_write.py): the app checks it, tests
        it on its scenarios, and asks again with what was wrong. The prompt is built here."""
        if not authorization.startswith("Bearer ") or len(authorization) > 12000:
            raise CoreHTTPError(401, "core_sign_in_required", payload.request_id)
        try:
            subject = await identity.verify(authorization[7:])
        except HTTPException as exc:
            raise CoreHTTPError(
                exc.status_code,
                "core_sign_in_required" if exc.status_code == 401 else "core_identity_unavailable",
                payload.request_id,
            ) from None
        uid = hashlib.sha256(subject.encode()).hexdigest()
        credential = key()
        if not credential:
            raise CoreHTTPError(503, "core_not_configured", payload.request_id)
        now = time.time()
        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        with sqlite3.connect(database, timeout=10) as db:
            count = db.execute(
                "SELECT COUNT(*) FROM turns WHERE day=? AND uid=?", (day, uid)
            ).fetchone()[0]
            recent = db.execute(
                "SELECT COUNT(*) FROM turns WHERE uid=? AND created>?", (uid, now - 60)
            ).fetchone()[0]
            if (settings.user_daily_limit and count >= settings.user_daily_limit) or (
                settings.user_minute_limit and recent >= settings.user_minute_limit
            ):
                raise CoreHTTPError(429, "core_usage_limit", payload.request_id)
            # counted with the person's turns; never answered twice for one request id
            if db.execute("SELECT 1 FROM turns WHERE id=?", (payload.request_id,)).fetchone():
                raise CoreHTTPError(409, "core_request_conflict", payload.request_id)
            db.execute(
                "INSERT INTO turns VALUES (?,?,?,?,?,?,?,?)",
                (payload.request_id, uid, "workflow", day, "complete", None, now + 600, now),
            )
            db.commit()
        body = {
            "model": settings.model,
            "messages": workflow_messages(payload),
            "max_tokens": 16000,  # a whole path and its scenarios
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
            "reasoning_effort": settings.reasoning_effort,
        }
        headers = {"Authorization": "Bearer " + credential}
        try:
            async with asyncio.timeout(290), semaphore:
                async with httpx.AsyncClient(
                    transport=transport, timeout=httpx.Timeout(285, connect=15)
                ) as client:
                    response = await client.post(PROVIDER_URL, headers=headers, json=body)
                    response.raise_for_status()
                    choice = response.json()["choices"][0]
        except TimeoutError:
            raise CoreHTTPError(504, "core_timeout", payload.request_id) from None
        except httpx.HTTPStatusError as exc:
            # The provider's own reason, for the publisher's log; never sent to the app.
            logger.warning(
                "Workflow request %s: provider answered %s: %s",
                payload.request_id,
                exc.response.status_code,
                exc.response.text[:300],
            )
            raise CoreHTTPError(502, "core_gateway_failed", payload.request_id) from None
        except (httpx.HTTPError, KeyError, TypeError, IndexError, ValueError) as exc:
            logger.warning("Workflow request %s failed: %r", payload.request_id, exc)
            raise CoreHTTPError(502, "core_bad_response", payload.request_id) from None
        raw = (choice.get("message") or {}).get("content")
        if choice.get("finish_reason") == "length":
            raise CoreHTTPError(502, "core_truncated_response", payload.request_id)
        if not isinstance(raw, str) or not raw.strip():
            raise CoreHTTPError(502, "core_empty_response", payload.request_id)
        return {"text": raw, "model": settings.model}

    @app.post("/v1/core/turn/stream")
    async def turn_stream(payload: GatewayTurn, authorization: str = Header(default="")):
        """The same turn, streamed: {type: reply, text} while the model writes the reply, then
        {type: done, result} (what /v1/core/turn returns) or {type: error, status, error}."""
        from fastapi.responses import StreamingResponse

        from mavo.reply_stream import sse

        queue: asyncio.Queue = asyncio.Queue()

        async def work():
            try:
                result = await handle(
                    payload,
                    authorization,
                    lambda text: queue.put_nowait({"type": "reply", "text": text}),
                )
                queue.put_nowait({"type": "done", "result": result})
            except CoreHTTPError as exc:
                queue.put_nowait({"type": "error", "status": exc.status_code, "error": exc.problem})
            except Exception:
                logger.exception("Streamed coordinator turn failed")
                failure = CoreHTTPError(502, "core_gateway_failed", payload.request_id)
                queue.put_nowait({"type": "error", "status": 502, "error": failure.problem})

        job = asyncio.create_task(work())
        streams.add(job)
        job.add_done_callback(streams.discard)

        async def events():
            while True:
                event = await queue.get()
                yield sse(event)
                if event["type"] != "reply":
                    break

        return StreamingResponse(events(), media_type="text/event-stream")

    async def handle(payload: GatewayTurn, authorization: str, on_text=None):
        if not authorization.startswith("Bearer ") or len(authorization) > 12000:
            raise CoreHTTPError(401, "core_sign_in_required", payload.request_id)
        try:
            subject = await identity.verify(authorization[7:])
        except HTTPException as exc:
            raise CoreHTTPError(
                exc.status_code,
                "core_sign_in_required" if exc.status_code == 401 else "core_identity_unavailable",
                payload.request_id,
            ) from None
        uid = hashlib.sha256(subject.encode()).hexdigest()
        credential = key()
        if not credential:
            raise CoreHTTPError(503, "core_not_configured", payload.request_id)
        fingerprint = hashlib.sha256(payload.model_dump_json().encode()).hexdigest()
        now = time.time()
        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        with sqlite3.connect(database, timeout=10) as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute(
                "SELECT uid,fingerprint,state,response,expires FROM turns WHERE id=?",
                (payload.request_id,),
            ).fetchone()
            if old:
                if old[0] != uid or old[1] != fingerprint:
                    raise CoreHTTPError(409, "core_request_conflict", payload.request_id)
                if old[2] == "complete" and old[4] > now:
                    return json.loads(old[3])
                if old[2] == "failed":
                    saved = json.loads(old[3]) if old[3] else {}
                    raise CoreHTTPError(
                        409,
                        saved.get("error", {}).get("code", "core_gateway_failed"),
                        payload.request_id,
                    )
                raise CoreHTTPError(409, "core_request_pending", payload.request_id)
            count = db.execute(
                "SELECT COUNT(*) FROM turns WHERE day=? AND uid=?", (day, uid)
            ).fetchone()[0]
            total = db.execute("SELECT COUNT(*) FROM turns WHERE day=?", (day,)).fetchone()[0]
            recent = db.execute(
                "SELECT COUNT(*) FROM turns WHERE uid=? AND created>?", (uid, now - 60)
            ).fetchone()[0]

            def over(used: int, limit: int) -> bool:
                return bool(limit) and used >= limit

            if (
                over(count, settings.user_daily_limit)
                or over(total, settings.global_daily_limit)
                or over(recent, settings.user_minute_limit)
            ):
                raise CoreHTTPError(429, "core_usage_limit", payload.request_id)
            db.execute(
                "INSERT INTO turns VALUES (?,?,?,?,?,?,?,?)",
                (payload.request_id, uid, fingerprint, day, "working", None, now + 600, now),
            )
            db.execute("UPDATE turns SET response=NULL WHERE expires<?", (now,))
            db.execute("DELETE FROM turns WHERE created<?", (now - 7 * 86400,))
            db.commit()
        try:
            messages = [
                {"role": "system", "content": coordinator_prompt(payload.context)},
                *[m.model_dump() for m in payload.messages],
            ]
            async with asyncio.timeout(190), semaphore:
                async with httpx.AsyncClient(
                    transport=transport, timeout=httpx.Timeout(185, connect=15)
                ) as client:
                    # Two repairs, each shown exactly what was wrong: with one, a design whose
                    # format was fixed still lacked a reviewer, and the chat said Core could not
                    # answer for a plan one step from done.
                    for attempt in range(3):
                        headers = {
                            "Authorization": "Bearer " + credential,
                            "Idempotency-Key": hashlib.sha256(
                                (uid + payload.request_id + str(attempt)).encode()
                            ).hexdigest(),
                        }
                        body = {
                            "model": settings.model,
                            "messages": messages,
                            "max_tokens": settings.max_output_tokens,
                            "temperature": 0.2,
                            "response_format": {"type": "json_object"},
                            "reasoning_effort": settings.reasoning_effort,
                        }
                        if on_text and not attempt:
                            # The first answer streams to the chat; a repair does not.
                            raw, finish = await streamed(client, headers, body, on_text)
                        else:
                            response = await client.post(PROVIDER_URL, headers=headers, json=body)
                            response.raise_for_status()
                            envelope = response.json()
                            try:
                                choice = envelope["choices"][0]
                                raw = choice["message"].get("content")
                                finish = choice.get("finish_reason")
                            except (KeyError, TypeError, IndexError, AttributeError):
                                raise CoreHTTPError(
                                    502, "core_bad_response", payload.request_id
                                ) from None
                        if finish == "length":
                            raise CoreHTTPError(502, "core_truncated_response", payload.request_id)
                        if not isinstance(raw, str) or not raw.strip():
                            raise CoreHTTPError(502, "core_empty_response", payload.request_id)
                        try:
                            decision = parse_decision(raw)
                            break
                        except (ValidationError, ValueError) as invalid:
                            if attempt == 2:
                                decision = clarification_from_invalid(raw, payload.context)
                                if decision is not None:
                                    logger.warning(
                                        "Rejected setup operations; retained validated "
                                        "clarification reference=%s",
                                        payload.request_id,
                                    )
                                    break
                                raise
                            logger.warning("Coordinator reply invalid; asking for a correction")
                            messages = [
                                *messages,
                                {"role": "assistant", "content": raw[:6000]},
                                {
                                    "role": "user",
                                    "content": "That reply was not valid for the response "
                                    f"schema ({describe(invalid)}). Reply again with ONLY the "
                                    "corrected JSON object.\n"
                                    + draft_refinement_hint(payload.context),
                                },
                            ]
            if decision.delegate_to and decision.delegate_to not in {
                crew.id for crew in payload.context.available_crews
            }:
                raise ValueError("Unexpected delegation target")
            if (
                decision.target_crew_id
                and decision.target_crew_id != payload.context.current_crew_id
            ):
                # A model asked for one thing ("every morning at 8, the AI news") pointed its
                # proposal at an existing Crew of the person's that this chat is not about, and
                # the whole message failed. The proposal stands as a new one: still a draft the
                # person approves; nothing existing is changed.
                decision.target_crew_id = None
                if decision.crew_proposal:
                    # Its words described changing that Crew; say what actually happens.
                    decision.reply = (
                        f"Here is a new Crew for that, {decision.crew_proposal.name}. Review it "
                        "below and add it."
                    )
            result = {
                "decision": decision.model_dump(mode="json"),
                "model": settings.model,
            }  # dates as text
            with sqlite3.connect(database) as db:
                db.execute(
                    "UPDATE turns SET state='complete',response=? WHERE id=?",
                    (json.dumps(result), payload.request_id),
                )
                db.commit()
            return result
        except (
            CoreHTTPError,
            httpx.HTTPError,
            ValueError,
            KeyError,
            TypeError,
            IndexError,
            TimeoutError,
        ) as exc:
            if isinstance(exc, ValidationError):
                logger.warning(
                    "Coordinator response schema rejected: %s",
                    validation_issues(exc),
                )
            elif isinstance(exc, httpx.HTTPStatusError):
                logger.warning("Coordinator provider HTTP %s", exc.response.status_code)
            else:
                logger.warning("Coordinator turn failed (%s)", type(exc).__name__)
            if isinstance(exc, CoreHTTPError):
                failure = exc
            elif isinstance(exc, (httpx.TimeoutException, TimeoutError)):
                failure = CoreHTTPError(504, "core_timeout", payload.request_id)
            elif isinstance(exc, httpx.HTTPStatusError):
                upstream = exc.response.status_code
                code = {
                    400: "core_provider_request",
                    422: "core_provider_request",
                    401: "core_provider_key",
                    403: "core_provider_key",
                    402: "core_provider_credits",
                    404: "core_model_unavailable",
                    429: "core_provider_rate_limit",
                }.get(upstream, "core_provider_unavailable")
                failure = CoreHTTPError(429 if upstream == 429 else 503, code, payload.request_id)
            elif isinstance(exc, httpx.HTTPError):
                failure = CoreHTTPError(503, "core_provider_unavailable", payload.request_id)
            else:
                failure = CoreHTTPError(502, "core_invalid_response", payload.request_id)
            logger.warning(
                "Coordinator failure code=%s reference=%s",
                failure.problem["code"],
                payload.request_id,
            )
            with sqlite3.connect(database) as db:
                db.execute(
                    "UPDATE turns SET state='failed',response=? WHERE id=?",
                    (
                        json.dumps(
                            {"error": failure.problem, "validation_issues": validation_issues(exc)}
                        ),
                        payload.request_id,
                    ),
                )
                db.commit()
            raise failure from None

    return app


def describe(invalid) -> str:
    """What was wrong with a reply, without echoing any of its content."""
    if isinstance(invalid, ValidationError):
        return "; ".join(
            ".".join(str(part) for part in e["loc"]) + ": " + e["msg"]
            for e in invalid.errors(include_input=False)[:3]
        )[:400]
    return str(invalid)[:200]


def app_factory():
    return create_gateway(GatewaySettings())


def cli():
    import uvicorn

    settings = GatewaySettings()
    uvicorn.run(
        create_gateway(settings), host="127.0.0.1", port=8741, access_log=False, proxy_headers=False
    )


if __name__ == "__main__":
    cli()
