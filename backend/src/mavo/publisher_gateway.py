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

from mavo.coordinator_contract import GatewayTurn, coordinator_prompt, parse_decision
from mavo.credentials import DpapiCredentialStore

logger = logging.getLogger("mavo.publisher")


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
    api_key: SecretStr | None = Field(default=None, repr=False, exclude=True)
    model: str = "minimax/minimax-m3.1-flash"
    reasoning_effort: Literal["low", "medium", "high", "xhigh", "max"] = "low"
    # 0 means no limit (the default while Mavo is being built); set a number to cap turns.
    user_daily_limit: int = Field(default=0, ge=0, le=10000)
    global_daily_limit: int = Field(default=0, ge=0, le=100000)
    user_minute_limit: int = Field(default=0, ge=0, le=1000)
    max_output_tokens: int = Field(default=4096, ge=512, le=8192)
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
        if not authorization.startswith("Bearer ") or len(authorization) > 12000:
            raise HTTPException(401, "Mavo account sign-in is required.")
        uid = hashlib.sha256((await identity.verify(authorization[7:])).encode()).hexdigest()
        credential = key()
        if not credential:
            raise HTTPException(503, "Mavo Core is not configured by the publisher yet.")
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
                    raise HTTPException(409, "This request ID belongs to another turn.")
                if old[2] == "complete" and old[4] > now:
                    return json.loads(old[3])
                raise HTTPException(
                    409,
                    "This turn is already reserved. Use a new request after checking its outcome.",
                )
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
                raise HTTPException(
                    429, "Mavo Core's usage limit has been reached. Try again later."
                )
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
                    # One repair, like a Crew's own model gets: a reply that does not match the
                    # response schema is shown what was wrong and asked to correct it.
                    for attempt in range(2):
                        response = await client.post(
                            "https://api.airouter.in/v1/chat/completions",
                            headers={
                                "Authorization": "Bearer " + credential,
                                "Idempotency-Key": hashlib.sha256(
                                    (uid + payload.request_id + str(attempt)).encode()
                                ).hexdigest(),
                            },
                            json={
                                "model": settings.model,
                                "messages": messages,
                                "max_tokens": settings.max_output_tokens,
                                "temperature": 0.2,
                                "response_format": {"type": "json_object"},
                                "reasoning_effort": settings.reasoning_effort,
                            },
                        )
                        response.raise_for_status()
                        raw = response.json()["choices"][0]["message"]["content"]
                        try:
                            decision = parse_decision(raw)
                            break
                        except (ValidationError, ValueError) as invalid:
                            if attempt:
                                raise
                            logger.warning("Coordinator reply invalid; asking for a correction")
                            messages = [
                                *messages,
                                {"role": "assistant", "content": raw[:6000]},
                                {
                                    "role": "user",
                                    "content": "That reply was not valid for the response "
                                    f"schema ({describe(invalid)}). Reply again with ONLY the "
                                    "corrected JSON object.",
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
        except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError, TimeoutError) as exc:
            if isinstance(exc, ValidationError):
                logger.warning(
                    "Coordinator response schema rejected: %s",
                    [
                        (e["loc"], e["type"], e["msg"][:200])
                        for e in exc.errors(include_input=False)
                    ],
                )
            elif isinstance(exc, httpx.HTTPStatusError):
                logger.warning("Coordinator provider HTTP %s", exc.response.status_code)
            else:
                logger.warning(
                    "Coordinator turn failed (%s): %s", type(exc).__name__, str(exc)[:200]
                )
            with sqlite3.connect(database) as db:
                db.execute("UPDATE turns SET state='failed' WHERE id=?", (payload.request_id,))
                db.commit()
            if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
                raise HTTPException(
                    504, "Mavo Core's model took too long. Your saved Crew is unchanged. Try again."
                ) from None
            raise HTTPException(
                502, "Mavo Core could not produce a valid response. Your agents are unaffected."
            ) from None

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
