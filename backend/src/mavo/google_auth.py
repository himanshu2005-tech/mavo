from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import secrets
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import jwt
from sqlalchemy import delete, select, update

from mavo.credentials import CredentialError, CredentialStore
from mavo.database import (
    CrewInstance,
    CrewRun,
    GoogleAccount,
    ResourceEvent,
    WorkspaceSetting,
    utcnow,
)
from mavo.google_errors import GoogleSignInError
from mavo.google_permissions import BASE, CAPABILITIES, granted_capabilities
from mavo.google_token_errors import token_error

logger = logging.getLogger("mavo.google")

SCOPES = {
    "gmail": "https://www.googleapis.com/auth/gmail.metadata",
    "calendar": "https://www.googleapis.com/auth/calendar.readonly",
    "tasks": "https://www.googleapis.com/auth/tasks.readonly",
    "drive": "https://www.googleapis.com/auth/drive.metadata.readonly",
    "contacts": "https://www.googleapis.com/auth/contacts.readonly",
    "youtube": "https://www.googleapis.com/auth/youtube.readonly",
}
IDENTITY_SCOPES = {"openid", "email", "profile"}


class GoogleConnectionError(RuntimeError):
    pass


class GoogleAuth:
    def __init__(
        self,
        factory,
        credentials: CredentialStore,
        transport=None,
        publisher_config=None,
        allow_developer_setup=False,
        identity_callback=None,
    ):
        self.factory = factory
        self.credentials = credentials
        self.transport = transport
        self.publisher_config = publisher_config or {}
        self.allow_developer_setup = allow_developer_setup
        self.identity_callback = identity_callback
        self.server = None
        self.pending = None
        self.timeout_task = None
        self.refresh_lock = asyncio.Lock()
        self.start_lock = asyncio.Lock()
        self.callback_tasks: set[asyncio.Task] = set()
        self.keys = None
        self.keys_expiry = 0

    def configuration(self) -> dict:
        if self.publisher_config:
            return self.publisher_config
        value = self.credentials.get("google-client")
        return json.loads(value) if value else {}

    async def close(self):
        if self.pending and self.pending["phase"] in {"pending", "exchanging"}:
            self.pending["phase"] = "error"
            self.pending["error"] = "Google sign-in was cancelled. Try again."
        timeout, self.timeout_task = self.timeout_task, None
        if timeout and timeout is not asyncio.current_task():
            timeout.cancel()
        server, self.server = self.server, None
        # Python 3.12 waits for active connections in wait_closed(). Cancel owned
        # callbacks first so a pending token request cannot block Cancel/Retry.
        for task in tuple(self.callback_tasks):
            if task is not asyncio.current_task():
                task.cancel()
        if server:
            server.close()
            await server.wait_closed()

    async def cancel(self):
        async with self.start_lock:
            await self.close()
            self.pending = None

    def status(self) -> dict:
        from mavo.connection_revocation import read_receipt

        config = self.configuration()
        tokens = json.loads(self.credentials.get("google-tokens") or "{}")
        with self.factory() as session:
            account = session.get(GoogleAccount, "google")
            signed_in = account is not None and tokens.get("subject") == account.subject
            capabilities = (
                granted_capabilities(set(account.scopes) & set(tokens.get("scopes", [])))
                if signed_in
                else []
            )
            return {
                "revocation": read_receipt(session, "google"),
                "configured": bool(config.get("client_id")),
                "publisher_managed": bool(self.publisher_config),
                "developer_setup_allowed": self.allow_developer_setup and not self.publisher_config,
                "client_id": config.get("client_id", ""),
                "has_client_secret": bool(config.get("client_secret")),
                "signed_in": signed_in,
                "name": account.name if account else None,
                "email": account.email if account else None,
                "resources": [
                    key for key, scope in SCOPES.items() if f"{key}:read" in capabilities
                ],
                "capabilities": capabilities,
                "phase": self.pending["phase"] if self.pending else "idle",
                "error": self.pending.get("error") if self.pending else None,
                "error_code": self.pending.get("error_code") if self.pending else None,
                "missing_resources": self.pending.get("missing_resources", [])
                if self.pending
                else [],
                "missing_capabilities": self.pending.get("missing_capabilities", [])
                if self.pending
                else [],
            }

    async def configure(self, client_id: str, secret: str | None):
        if self.publisher_config or not self.allow_developer_setup:
            raise GoogleConnectionError("Google application setup is managed by the Mavo publisher")
        previous = self.configuration()
        if previous.get("client_id") and previous["client_id"] != client_id:
            with self.factory() as session:
                if session.get(GoogleAccount, "google"):
                    raise GoogleConnectionError(
                        "Disconnect the account before changing OAuth client"
                    )
        await self.close()
        self.pending = None
        self.credentials.set(
            "google-client",
            json.dumps(
                {
                    "client_id": client_id,
                    "client_secret": secret
                    if secret is not None
                    else previous.get("client_secret", ""),
                }
            ),
        )

    async def start(self, resources: list[str], capabilities: list[str] | None = None) -> dict:
        async with self.start_lock:
            return await self._start(resources, capabilities or [])

    async def _start(self, resources: list[str], capabilities: list[str]) -> dict:
        config = self.configuration()
        if not config.get("client_id"):
            raise GoogleConnectionError(
                "Google connection is unavailable in this build. "
                "The Mavo publisher must configure it."
            )
        await self.close()
        scopes = set(IDENTITY_SCOPES)
        scopes.update(SCOPES[key] for key in resources)
        scopes.update(BASE + CAPABILITIES[key][2] for key in capabilities)
        account_hint = None
        # Installed clients re-consent to the explicit union; they do not rely on
        # Google's web-only incremental authorization behavior.
        with self.factory() as session:
            account = session.get(GoogleAccount, "google")
            if account:
                account_hint = account.email
            if account and not self.identity_callback:
                known = set(SCOPES.values()) | {BASE + item[2] for item in CAPABILITIES.values()}
                scopes.update(scope for scope in account.scopes if scope in known)
                account_hint = account.email
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(
            b"="
        )
        self.pending = {
            "state": secrets.token_urlsafe(32),
            "nonce": secrets.token_urlsafe(32),
            "verifier": verifier,
            "phase": "pending",
            "scopes": sorted(scopes),
            "requested_resources": [key for key, scope in SCOPES.items() if scope in scopes],
            "requested_capabilities": capabilities,
        }
        self.server = await asyncio.start_server(
            self.callback, "127.0.0.1", 0, limit=16384, backlog=8
        )
        port = self.server.sockets[0].getsockname()[1]
        self.pending["redirect_uri"] = f"http://127.0.0.1:{port}/oauth/callback"
        url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(
            {
                "client_id": config["client_id"],
                "redirect_uri": self.pending["redirect_uri"],
                "response_type": "code",
                "scope": " ".join(sorted(scopes)),
                "state": self.pending["state"],
                "nonce": self.pending["nonce"],
                "code_challenge": challenge.decode(),
                "code_challenge_method": "S256",
                "access_type": "offline",
                "prompt": "consent select_account",
                **({"login_hint": account_hint} if account_hint else {}),
            }
        )
        self.timeout_task = asyncio.create_task(self.expire(self.pending["state"]))
        return {"authorization_url": url, "resources": resources}

    async def expire(self, state: str):
        await asyncio.sleep(300)
        if self.pending and self.pending["state"] == state and self.pending["phase"] == "pending":
            self.pending["phase"] = "error"
            self.pending["error"] = "Google sign-in expired. Try again."
            await self.close()

    async def callback(self, reader, writer):
        task = asyncio.current_task()
        self.callback_tasks.add(task)
        status, message = "400 Bad Request", "Invalid sign-in response. Return to Mavo."
        finish = False
        attempt = None
        try:
            raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
            first = raw.decode("ascii").split("\r\n", 1)[0].split(" ")
            parsed = urlsplit(first[1]) if len(first) == 3 and first[0] == "GET" else None
            query = parse_qs(parsed.query) if parsed and parsed.path == "/oauth/callback" else {}
            state = query.get("state", [""])
            valid = (
                self.pending
                and self.pending["phase"] == "pending"
                and len(state) == 1
                and secrets.compare_digest(state[0], self.pending["state"])
            )
            if valid:
                finish = True
                attempt = self.pending
                self.pending["phase"] = "exchanging"
                code = query.get("code", [])
                if query.get("error"):
                    raise GoogleSignInError("consent_denied")
                if len(code) != 1 or not code[0]:
                    raise GoogleSignInError("unexpected_response")
                await self.exchange(code[0])
                if self.pending is attempt:
                    self.pending["phase"] = "complete"
                status, message = (
                    "200 OK",
                    "You are signed in. You can close this tab and return to Mavo.",
                )
        except (
            GoogleSignInError,
            GoogleConnectionError,
            CredentialError,
            httpx.HTTPError,
            ValueError,
            KeyError,
            TypeError,
            jwt.PyJWTError,
        ) as exc:
            if finish and self.pending is attempt:
                if isinstance(exc, GoogleSignInError):
                    failure = exc
                elif isinstance(exc, jwt.ExpiredSignatureError):
                    failure = GoogleSignInError("identity_expired")
                elif isinstance(exc, jwt.ImmatureSignatureError):
                    failure = GoogleSignInError("clock_skew")
                elif isinstance(exc, jwt.InvalidAudienceError):
                    failure = GoogleSignInError("client_mismatch")
                elif isinstance(exc, jwt.PyJWTError):
                    failure = GoogleSignInError("invalid_identity")
                elif isinstance(exc, CredentialError):
                    failure = GoogleSignInError("credential_storage")
                elif isinstance(exc, httpx.HTTPError):
                    failure = GoogleSignInError("network_error")
                else:
                    failure = GoogleSignInError("unexpected_response")
                self.pending["phase"] = "error"
                self.pending["error"] = str(failure)
                self.pending["error_code"] = failure.code
                logger.warning("Google sign-in failed: %s", failure.code)
                message = str(failure)
        except (TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, UnicodeError):
            pass
        finally:
            body = (
                "<!doctype html><meta charset=utf-8><title>Mavo</title><p>" + message + "</p>"
            ).encode()
            writer.write(
                (
                    f"HTTP/1.1 {status}\r\nContent-Type: text/html; charset=utf-8\r\n"
                    f"Content-Length: {len(body)}\r\nCache-Control: no-store\r\n"
                    "Referrer-Policy: no-referrer\r\n"
                    "Content-Security-Policy: default-src 'none'\r\n"
                    "Connection: close\r\n\r\n"
                ).encode()
                + body
            )
            try:
                await writer.drain()
            except ConnectionError:
                pass
            writer.close()
            self.callback_tasks.discard(task)
            if finish and self.pending is attempt:
                await self.close()

    async def exchange(self, code: str):
        pending = self.pending
        config = self.configuration()
        payload = {
            "client_id": config["client_id"],
            "code": code,
            "code_verifier": pending["verifier"],
            "redirect_uri": pending["redirect_uri"],
            "grant_type": "authorization_code",
        }
        async with httpx.AsyncClient(transport=self.transport, timeout=20) as client:
            response = await self.token_request(client, config, payload)
            if response.is_error:
                raise GoogleSignInError(token_error(response))
            response.raise_for_status()
            tokens = response.json()
            raw_id = tokens.get("id_token", "")
            if not raw_id or not tokens.get("access_token"):
                raise GoogleSignInError("missing_identity")
            header = jwt.get_unverified_header(raw_id)
            if header.get("alg") != "RS256":
                raise GoogleSignInError("invalid_identity")
            if (
                self.keys is None
                or self.keys_expiry < time.time()
                or not any(key.get("kid") == header.get("kid") for key in self.keys)
            ):
                certs = await client.get("https://www.googleapis.com/oauth2/v3/certs")
                certs.raise_for_status()
                self.keys = certs.json()["keys"]
                self.keys_expiry = time.time() + 3600
            key = next((key for key in self.keys if key.get("kid") == header.get("kid")), None)
            if not key:
                raise GoogleSignInError("invalid_identity")
            claims = jwt.decode(
                raw_id,
                jwt.PyJWK.from_dict(key).key,
                algorithms=["RS256"],
                audience=config["client_id"],
                leeway=60,
                options={"require": ["exp", "iat", "sub", "iss", "aud", "nonce"]},
            )
            if claims["iss"] not in {"accounts.google.com", "https://accounts.google.com"}:
                raise GoogleSignInError("invalid_identity")
            if not secrets.compare_digest(str(claims["nonce"]), pending["nonce"]):
                raise GoogleSignInError("nonce_mismatch")
            if claims.get("email_verified") not in (True, "true"):
                raise GoogleSignInError("email_unverified")
        scopes = tokens.get("scope", " ".join(pending["scopes"])).split()
        if self.pending is not pending or pending["phase"] != "exchanging":
            raise GoogleSignInError("cancelled")
        if self.identity_callback:
            await self.identity_callback(raw_id, claims, tokens["access_token"])
            return
        with self.factory() as session:
            previous = session.get(GoogleAccount, "google")
            if previous and previous.subject != claims["sub"]:
                raise GoogleSignInError("account_mismatch")
            old = json.loads(self.credentials.get("google-tokens") or "{}")
            refresh = tokens.get("refresh_token") or old.get("refresh_token")
            self.credentials.set(
                "google-tokens",
                json.dumps(
                    {
                        "access_token": tokens["access_token"],
                        "refresh_token": refresh,
                        "expires_at": time.time() + int(tokens.get("expires_in", 3600)),
                        "subject": claims["sub"],
                        "scopes": scopes,
                    }
                ),
            )
            if previous is None:
                previous = GoogleAccount(
                    id="google",
                    subject=claims["sub"],
                    email=claims.get("email", ""),
                    name=claims.get("name", ""),
                    scopes=scopes,
                )
                session.add(previous)
            else:
                previous.scopes = scopes
                previous.email = claims.get("email", "")
                previous.name = claims.get("name", "")
                # Extending consent for the same account preserves existing sync cursors.
                previous.connected_at = utcnow()
            session.execute(
                delete(WorkspaceSetting).where(
                    WorkspaceSetting.key == "connection-revocation-google"
                )
            )
            session.commit()
        pending["missing_resources"] = [
            key
            for key in pending["requested_resources"]
            if f"{key}:read" not in granted_capabilities(scopes)
        ]
        pending["missing_capabilities"] = [
            key
            for key in capabilities_requested(pending)
            if key not in granted_capabilities(scopes)
        ]

    async def access_token(self, resource: str, capability: str = "read") -> str:
        async with self.refresh_lock:
            tokens = json.loads(self.credentials.get("google-tokens") or "{}")
            required = f"{resource}:{capability}"
            if required not in granted_capabilities(tokens.get("scopes", [])):
                raise GoogleConnectionError(f"Connect Google {resource} in Settings first")
            if tokens.get("expires_at", 0) > time.time() + 60:
                return tokens["access_token"]
            config = self.configuration()
            if not tokens.get("refresh_token"):
                raise GoogleConnectionError("Google authorization expired. Sign in again.")
            payload = {
                "client_id": config.get("client_id", ""),
                "refresh_token": tokens["refresh_token"],
                "grant_type": "refresh_token",
            }
            try:
                async with httpx.AsyncClient(transport=self.transport, timeout=20) as client:
                    response = await self.token_request(client, config, payload)
                    if response.is_error:
                        raise GoogleSignInError(token_error(response))
                    response.raise_for_status()
                    data = response.json()
                tokens.update(
                    access_token=data["access_token"],
                    expires_at=time.time() + int(data.get("expires_in", 3600)),
                )
                if data.get("scope"):
                    tokens["scopes"] = data["scope"].split()
                if data.get("refresh_token"):
                    tokens["refresh_token"] = data["refresh_token"]
                current = json.loads(self.credentials.get("google-tokens") or "{}")
                if current.get("subject") != tokens.get("subject"):
                    raise GoogleConnectionError("Google was disconnected during refresh")
                if required not in granted_capabilities(tokens.get("scopes", [])):
                    raise GoogleConnectionError("The requested Google permission was revoked")
                self.credentials.set("google-tokens", json.dumps(tokens))
                return tokens["access_token"]
            except GoogleSignInError as exc:
                raise GoogleConnectionError(str(exc)) from exc
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                raise GoogleConnectionError(
                    "Google access expired or was revoked. Reconnect in Settings."
                ) from exc

    @staticmethod
    async def token_request(client, config, payload):
        if config.get("client_secret"):
            return await client.post(
                "https://oauth2.googleapis.com/token",
                data={**payload, "client_secret": config["client_secret"]},
            )
        if config.get("oauth_gateway_url"):
            from mavo.publisher import publisher_core_url

            base = publisher_core_url(config["oauth_gateway_url"])
            return await client.post(base + "/v1/google/token", json=payload)
        return await client.post("https://oauth2.googleapis.com/token", data=payload)

    async def revoke(self):
        from mavo.connection_revocation import read_receipt, save_receipt

        async with self.start_lock:
            tokens = json.loads(self.credentials.get("google-tokens") or "{}")
            token = tokens.get("refresh_token") or tokens.get("access_token")
            with self.factory() as session:
                previous = read_receipt(session, "google")
            await self.disconnect()
            if not token and previous:
                return save_receipt(
                    self.factory,
                    "google",
                    previous["remote_revoked"],
                    previous["message"],
                    previous["requires_provider_action"],
                )
            revoked = False
            if token:
                try:
                    async with (
                        asyncio.timeout(15),
                        httpx.AsyncClient(
                            transport=self.transport, timeout=10, follow_redirects=False
                        ) as client,
                    ):
                        response = await client.post(
                            "https://oauth2.googleapis.com/revoke", data={"token": token}
                        )
                    revoked = response.status_code == 200
                except (httpx.HTTPError, TimeoutError):
                    revoked = False
            message = (
                "Google access revoked. Mavo's saved connection and synced data were removed."
                if revoked
                else "Disconnected from Mavo. Review Google's app access to finish revoking."
            )
            return save_receipt(self.factory, "google", revoked, message, not revoked)

    async def disconnect(self):
        from mavo.crew_workers import stop_crew

        await self.close()
        self.credentials.delete("google-tokens")
        self.pending = None
        with self.factory() as session:
            crews = session.scalars(select(CrewInstance)).all()
            for crew in crews:
                if any(p.startswith("google:") for p in [*crew.resources, *crew.permissions]):
                    stop_crew(session, crew)
                    crew.permissions = [p for p in crew.permissions if not p.startswith("google:")]
                    crew.resources = [r for r in crew.resources if not r.startswith("google:")]
                    crew.checkpoint = None
                    crew.observation = {}
                    crew.last_runtime_error = "Google was disconnected. Reconnect and select Run."
                    session.execute(
                        update(CrewRun)
                        .where(
                            CrewRun.crew_id == crew.id,
                        )
                        .values(
                            result=None,
                            prompt="Google data removed after disconnection.",
                            error="Google was disconnected.",
                            status="cancelled",
                            finished_at=utcnow(),
                        )
                    )
            session.execute(delete(ResourceEvent).where(ResourceEvent.resource.in_(list(SCOPES))))
            session.execute(delete(GoogleAccount))
            session.execute(
                delete(WorkspaceSetting).where(
                    WorkspaceSetting.key == "connection-revocation-google"
                )
            )
            from mavo.database import ActionRequest

            session.execute(
                update(ActionRequest)
                .where(
                    ActionRequest.status == "pending",
                    ActionRequest.operation.in_(
                        [
                            "gmail.send",
                            "gmail.draft",
                            "gmail.archive",
                            "gmail.mark_read",
                            "gmail.trash",
                            "calendar.create",
                            "calendar.update",
                            "calendar.delete",
                            "tasks.create",
                            "tasks.update",
                            "tasks.complete",
                            "tasks.delete",
                        ]
                    ),
                )
                .values(
                    status="rejected", error="Google was disconnected; this action was cancelled."
                )
            )
            session.execute(
                update(ActionRequest)
                .where(
                    ActionRequest.operation.in_(
                        [
                            "gmail.send",
                            "gmail.draft",
                            "gmail.archive",
                            "gmail.mark_read",
                            "gmail.trash",
                            "calendar.create",
                            "calendar.update",
                            "calendar.delete",
                            "tasks.create",
                            "tasks.update",
                            "tasks.complete",
                            "tasks.delete",
                        ]
                    )
                )
                .values(arguments={}, result=None, target_summary=None)
            )
            session.commit()


def capabilities_requested(pending):
    return pending.get("requested_capabilities", [])
