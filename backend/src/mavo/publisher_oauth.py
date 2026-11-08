"""Bounded Google token exchange on the publisher, never a general HTTP proxy.

Bootstrap cannot require a Firebase login that this exchange is needed to obtain.
Google's one-use code plus PKCE (or a private refresh token) is the proof. The
publisher supplies only its allowlisted application's credential. User tokens
are returned over TLS/loopback and never persisted in the publisher database.
"""

import asyncio
import hashlib
import re
import sqlite3
import time
from typing import Literal
from urllib.parse import urlsplit

import httpx
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from mavo.google_permissions import BASE, CAPABILITIES
from mavo.google_token_errors import token_error

PATH = "/v1/google/token"
NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
ALLOWED_SCOPES = {
    "openid",
    "email",
    "profile",
    BASE + "userinfo.email",
    BASE + "userinfo.profile",
} | {BASE + item[2] for item in CAPABILITIES.values()}


class TokenExchange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    grant_type: Literal["authorization_code", "refresh_token"]
    client_id: str = Field(
        pattern=r"^[A-Za-z0-9_-]+\.apps\.googleusercontent\.com$", max_length=255
    )
    code: SecretStr | None = Field(default=None, min_length=1, max_length=8192, repr=False)
    code_verifier: SecretStr | None = Field(default=None, min_length=43, max_length=128, repr=False)
    redirect_uri: str | None = Field(default=None, max_length=255)
    refresh_token: SecretStr | None = Field(default=None, min_length=1, max_length=8192, repr=False)

    @model_validator(mode="after")
    def grant_fields(self):
        if self.grant_type == "authorization_code":
            if (
                not self.code
                or not self.code_verifier
                or not self.redirect_uri
                or self.refresh_token
            ):
                raise ValueError("Authorization code, PKCE and loopback redirect required")
            if not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", self.code_verifier.get_secret_value()):
                raise ValueError("Invalid PKCE verifier")
            uri = urlsplit(self.redirect_uri)
            if (
                uri.scheme != "http"
                or uri.hostname not in {"127.0.0.1", "::1"}
                or not uri.port
                or uri.path != "/oauth/callback"
                or uri.username
                or uri.password
                or uri.query
                or uri.fragment
            ):
                raise ValueError("Use the native app's loopback callback")
        elif not self.refresh_token or self.code or self.code_verifier or self.redirect_uri:
            raise ValueError("A refresh grant accepts only the private refresh token")
        return self

    def upstream(self, secret):
        data = {"grant_type": self.grant_type, "client_id": self.client_id, "client_secret": secret}
        if self.grant_type == "authorization_code":
            data.update(
                code=self.code.get_secret_value(),
                code_verifier=self.code_verifier.get_secret_value(),
                redirect_uri=self.redirect_uri,
            )
        else:
            data["refresh_token"] = self.refresh_token.get_secret_value()
        return data


def failure(code, status=400):
    return JSONResponse({"error": code}, status_code=status, headers=NO_STORE)


def install_google_token_broker(app, settings, database, transport):
    with sqlite3.connect(database) as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS oauth_usage (bucket TEXT NOT NULL, "
            "window INTEGER NOT NULL, requests INTEGER NOT NULL, PRIMARY KEY(bucket,window))"
        )
    semaphore = asyncio.Semaphore(4)

    def reserve(host):
        now = int(time.time())
        minute, day = now // 60, now // 86400
        address = hashlib.sha256(host.encode()).hexdigest()
        limits = [
            ("minute:" + address, minute, settings.oauth_per_ip_minute),
            ("day:" + address, day, settings.oauth_per_ip_day),
            ("global", day, settings.oauth_global_day),
        ]
        with sqlite3.connect(database, timeout=5) as db:
            db.execute("BEGIN IMMEDIATE")
            for bucket, window, limit in limits:
                row = db.execute(
                    "SELECT requests FROM oauth_usage WHERE bucket=? AND window=?", (bucket, window)
                ).fetchone()
                if row and row[0] >= limit:
                    return False
            for bucket, window, _ in limits:
                db.execute(
                    "INSERT INTO oauth_usage VALUES (?,?,1) ON CONFLICT(bucket,window) "
                    "DO UPDATE SET requests=requests+1",
                    (bucket, window),
                )
            db.execute(
                "DELETE FROM oauth_usage WHERE (bucket LIKE 'minute:%' AND window<?) "
                "OR (bucket NOT LIKE 'minute:%' AND window<?)",
                (minute - 1440, day - 1),
            )
        return True

    @app.middleware("http")
    async def bound_oauth_requests(request, call_next):
        if request.url.path == PATH:
            if request.headers.get("origin"):
                return failure("access_denied", 403)
            if request.method == "POST":
                length = request.headers.get("content-length", "")
                if request.headers.get("transfer-encoding") or not length.isdigit():
                    return failure("invalid_request", 411)
                if len(length) > 6 or int(length) > 16384:
                    return failure("invalid_request", 413)
                if request.headers.get("content-type", "").split(";")[0] != "application/json":
                    return failure("invalid_request", 415)
                host = request.client.host if request.client else "unknown"
                if not reserve(host):
                    return failure("oauth_rate_limited", 429)
        response = await call_next(request)
        if request.url.path == PATH:
            response.headers.update(NO_STORE)
        return response

    @app.exception_handler(RequestValidationError)
    async def safe_validation_error(request, exc):
        if request.url.path == PATH:
            return failure("invalid_request", 422)
        return await request_validation_exception_handler(request, exc)

    @app.post(PATH)
    async def exchange(payload: TokenExchange):
        candidates = {
            settings.google_client_id: settings.google_client_secret,
        }
        if settings.firebase_google_client_id and settings.firebase_google_client_secret:
            candidates[settings.firebase_google_client_id] = settings.firebase_google_client_secret
        secret = candidates.get(payload.client_id)
        if not secret:
            return failure("publisher_setup_required", 503)
        try:
            async with asyncio.timeout(20), semaphore:
                async with httpx.AsyncClient(
                    transport=transport, timeout=15, follow_redirects=False
                ) as client:
                    response = await client.post(
                        "https://oauth2.googleapis.com/token",
                        data=payload.upstream(secret.get_secret_value()),
                    )
            if response.is_error:
                return failure(token_error(response), 503 if response.status_code >= 500 else 400)
            if response.is_redirect:
                return failure("google_unavailable", 503)
            data = response.json()
            if not isinstance(data, dict) or not isinstance(data.get("access_token"), str):
                return failure("unexpected_response", 502)
            scopes = data.get("scope")
            if scopes is not None and (
                not isinstance(scopes, str) or not set(scopes.split()) <= ALLOWED_SCOPES
            ):
                return failure("unsupported_permission")
            if payload.grant_type == "authorization_code" and not scopes:
                return failure("unexpected_response", 502)
            result = {}
            for name in ("access_token", "refresh_token", "id_token", "scope", "token_type"):
                if name in data:
                    if not isinstance(data[name], str) or len(data[name]) > 16384:
                        return failure("unexpected_response", 502)
                    result[name] = data[name]
            expires = data.get("expires_in", 3600)
            if not isinstance(expires, int) or not 1 <= expires <= 86400:
                return failure("unexpected_response", 502)
            return JSONResponse({**result, "expires_in": expires}, headers=NO_STORE)
        except (httpx.HTTPError, ValueError, TypeError, TimeoutError):
            return failure("google_unavailable", 503)
