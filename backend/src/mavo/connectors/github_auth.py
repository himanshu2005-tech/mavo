"""GitHub App device authorization for public desktop clients; no shared secret.

Only the user code reaches the UI. Device codes and uncommitted tokens stay in
memory; selected-repository access is committed to DPAPI after explicit review.
"""

import asyncio
import secrets
import time

import httpx

from mavo.connectors.hub import ConnectorError

ERRORS = {
    "access_denied": "GitHub authorization was declined. Your existing connection is unchanged.",
    "expired_token": "GitHub sign-in expired. Start again.",
    "incorrect_client_credentials": "The publisher must check Mavo's GitHub App client ID.",
    "device_flow_disabled": "The publisher must enable Device Flow in the GitHub App settings.",
    "bad_refresh_token": "GitHub authorization expired or was revoked. Connect again.",
}


def capabilities(permissions):
    result = {"github:repo:read"}
    for name, resource in (
        ("issues", "issues"),
        ("pull_requests", "pulls"),
        ("actions", "workflows"),
    ):
        if permissions.get(name) in {"read", "write"}:
            result.add(f"github:{resource}:read")
    if permissions.get("issues") == "write":
        result.add("github:issues:write")
    return sorted(result)


class GitHubConnection:
    def __init__(self, hub, config):
        self.hub, self.config = hub, config
        self.pending = None
        self.task = None
        self.lock, self.refresh_lock = asyncio.Lock(), asyncio.Lock()
        self.session_guard = lambda: True
        self.sleep = asyncio.sleep

    def current(self, attempt):
        return self.pending is attempt and self.session_guard()

    def status(self):
        attempt = self.pending or {}
        return {
            "configured": bool(self.config),
            "phase": attempt.get("phase", "idle"),
            "user_code": attempt.get("user_code") if attempt.get("phase") == "pending" else None,
            "verification_uri": "https://github.com/login/device",
            "installation_url": (
                "https://github.com/apps/" + self.config["app_slug"] + "/installations/new"
                if self.config
                else None
            ),
            "repositories": attempt.get("repositories", []),
            "coverage": "First 100 repositories across up to 5 app installations; select up to 10.",
            "error": attempt.get("error"),
            "simple": attempt.get("simple", False),
        }

    async def close(self):
        self.pending = None
        task, self.task = self.task, None
        if task and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def post(self, path, data):
        """One GitHub call, retried briefly on network errors, rate limits and 5xx. A definite
        client error is never retried, and the message says what actually went wrong."""
        reason = "the network"
        for attempt in range(3):
            if attempt:
                await self.sleep(1.5 * attempt)
            try:
                async with (
                    asyncio.timeout(20),
                    httpx.AsyncClient(
                        transport=self.hub.transport, timeout=15, follow_redirects=False
                    ) as client,
                ):
                    response = await client.post(
                        "https://github.com" + path,
                        data=data,
                        headers={"Accept": "application/json"},
                    )
            except (httpx.HTTPError, TimeoutError):
                reason = "the network"
                continue
            if response.status_code == 429 or response.status_code >= 500:
                reason = f"GitHub ({response.status_code})"
                continue
            if response.is_error or response.is_redirect or len(response.content) > 32768:
                raise ConnectorError(
                    f"GitHub refused the sign-in request ({response.status_code}). "
                    "The publisher should check Mavo's GitHub App."
                )
            try:
                value = response.json()
                if not isinstance(value, dict) or (
                    "error" in value and not isinstance(value["error"], str)
                ):
                    raise ValueError()
            except ValueError as exc:
                raise ConnectorError(
                    "GitHub returned an invalid sign-in response. Try again."
                ) from exc
            return value
        raise ConnectorError(
            f"GitHub sign-in could not be reached ({reason}). Check your connection and try again."
        )

    def require_config(self):
        if not self.config:
            raise ConnectorError(
                "GitHub sign-in is unavailable until the publisher configures Mavo's GitHub App."
            )
        if not self.session_guard():
            raise ConnectorError("Sign in to Mavo first.")

    async def start(self, simple=False):
        import re

        async with self.lock:
            self.require_config()
            await self.close()
            attempt = {"phase": "starting", "simple": simple}
            self.pending = attempt
            try:
                data = await self.post(
                    "/login/device/code", {"client_id": self.config["client_id"]}
                )
                if data.get("error"):
                    raise ConnectorError(
                        ERRORS.get(data["error"], "GitHub sign-in is unavailable.")
                    )
                code, device = data.get("user_code", ""), data.get("device_code", "")
                if (
                    not isinstance(code, str)
                    or not re.fullmatch(r"[A-Z0-9-]{4,16}", code)
                    or not isinstance(device, str)
                    or not 1 <= len(device) <= 1024
                    or data.get("verification_uri") != "https://github.com/login/device"
                    or not isinstance(data.get("expires_in"), int)
                    or not 1 <= data["expires_in"] <= 900
                    or not isinstance(data.get("interval", 5), int)
                    or not 1 <= data.get("interval", 5) <= 900
                ):
                    raise ConnectorError("GitHub returned an invalid sign-in response.")
                if not self.current(attempt):
                    raise ConnectorError("GitHub sign-in was cancelled.")
                attempt.update(
                    phase="pending",
                    user_code=code,
                    device_code=device,
                    deadline=time.monotonic() + data["expires_in"],
                    interval=max(5, data.get("interval", 5)),
                )
                self.task = asyncio.create_task(self.poll(attempt))
                return self.status()
            except ConnectorError as exc:
                if self.current(attempt):
                    attempt.update(phase="error", error=str(exc))
                raise

    @staticmethod
    def token_data(data, client_id):
        token = data.get("access_token")
        if not isinstance(token, str) or not token.startswith("ghu_") or len(token) > 1024:
            raise ConnectorError("GitHub did not return a GitHub App user token.")
        result = {"access_token": token, "client_id": client_id, "auth_mode": "github_app"}
        if "expires_in" in data:
            expires, refresh_expires, refresh = (
                data["expires_in"],
                data.get("refresh_token_expires_in"),
                data.get("refresh_token"),
            )
            if (
                not isinstance(expires, int)
                or not 60 <= expires <= 86400
                or not isinstance(refresh_expires, int)
                or not 60 <= refresh_expires <= 31536000
                or not isinstance(refresh, str)
                or not refresh.startswith("ghr_")
                or len(refresh) > 1024
            ):
                raise ConnectorError("GitHub returned invalid token expiry information.")
            result.update(
                expires_at=time.time() + expires,
                refresh_token=refresh,
                refresh_expires_at=time.time() + refresh_expires,
            )
        return result

    async def discover(self, token):
        try:
            return await asyncio.wait_for(self._discover(token), 45)
        except (TimeoutError, ValueError, TypeError, KeyError) as exc:
            raise ConnectorError("GitHub repository discovery failed. Try again.") from exc

    async def _discover(self, token):
        from mavo.connectors.actions import repository_name

        identity = (await self.hub.request("github", "GET", "/user", token=token)).json()
        if not isinstance(identity, dict) or not isinstance(identity.get("id"), int):
            raise ConnectorError("GitHub identity could not be verified.")
        old = self.hub.account("github")
        if old and old["subject"] != str(identity["id"]):
            raise ConnectorError("Disconnect the existing GitHub account before choosing another.")
        data = (
            await self.hub.request(
                "github", "GET", "/user/installations", token=token, params={"per_page": 5}
            )
        ).json()
        if not isinstance(data, dict) or not isinstance(data.get("installations"), list):
            raise ConnectorError("GitHub returned invalid installation data.")
        repositories = {}
        for installation in data["installations"][:5]:
            if (
                not isinstance(installation, dict)
                or not isinstance(installation.get("id"), int)
                or not isinstance(installation.get("permissions", {}), dict)
            ):
                raise ConnectorError("GitHub returned invalid installation data.")
            if installation.get("suspended_at"):
                continue
            items = (
                await self.hub.request(
                    "github",
                    "GET",
                    f"/user/installations/{installation['id']}/repositories",
                    token=token,
                    params={"per_page": 100},
                )
            ).json()
            if not isinstance(items, dict) or not isinstance(items.get("repositories"), list):
                raise ConnectorError("GitHub returned invalid repository data.")
            for row in items["repositories"][:100]:
                if len(repositories) >= 100:
                    break
                if not isinstance(row, dict) or not isinstance(row.get("full_name"), str):
                    raise ConnectorError("GitHub returned invalid repository data.")
                name = repository_name(row["full_name"])
                repositories[name] = {
                    "full_name": name,
                    "capabilities": capabilities(installation.get("permissions", {})),
                }
        return (
            str(identity["id"]),
            str(identity.get("login", ""))[:100],
            list(repositories.values()),
        )

    async def poll(self, attempt):
        try:
            while self.current(attempt) and time.monotonic() < attempt["deadline"]:
                await self.sleep(attempt["interval"])
                if not self.current(attempt):
                    return
                if time.monotonic() >= attempt["deadline"]:
                    break
                data = await self.post(
                    "/login/oauth/access_token",
                    {
                        "client_id": self.config["client_id"],
                        "device_code": attempt["device_code"],
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    },
                )
                if data.get("error") == "authorization_pending":
                    continue
                if data.get("error") == "slow_down":
                    interval = data.get("interval", attempt["interval"] + 5)
                    if not isinstance(interval, int) or not 1 <= interval <= 900:
                        raise ConnectorError("GitHub returned an invalid sign-in interval.")
                    attempt["interval"] = max(interval, attempt["interval"] + 5)
                    continue
                if data.get("error"):
                    raise ConnectorError(
                        ERRORS.get(data["error"], "GitHub sign-in failed. Start again.")
                    )
                tokens = self.token_data(data, self.config["client_id"])
                if self.current(attempt):
                    attempt["phase"] = "exchanging"
                subject, login, repos = await self.discover(tokens["access_token"])
                if not self.current(attempt):
                    return
                simple = attempt.get("simple", False)
                attempt.clear()
                attempt.update(
                    phase="select_repositories",
                    tokens=tokens,
                    subject=subject,
                    login=login,
                    repositories=repos,
                    deadline=time.monotonic() + 300,
                    simple=simple,
                )
                if simple:
                    while self.current(attempt) and time.monotonic() < attempt["deadline"]:
                        if repos:
                            if len(repos) <= 10:
                                self.commit_simple(attempt, subject, login, repos)
                                return
                            break
                        attempt["phase"] = "installing"
                        await self.sleep(15)
                        if not self.current(attempt):
                            return
                        if time.monotonic() >= attempt["deadline"]:
                            break
                        subject, login, repos = await self.discover(tokens["access_token"])
                        if self.current(attempt):
                            attempt["repositories"] = repos
                            attempt["phase"] = "select_repositories"
                    if time.monotonic() >= attempt["deadline"]:
                        raise ConnectorError(ERRORS["expired_token"])
                await self.sleep(300)
                break
            if self.current(attempt):
                attempt.clear()
                attempt.update(phase="error", error=ERRORS["expired_token"])
        except ConnectorError as exc:
            if self.current(attempt):
                attempt.clear()
                attempt.update(phase="error", error=str(exc))
        except asyncio.CancelledError:
            raise
        except Exception:
            # Never log device codes, token responses or provider body text.
            if self.current(attempt):
                attempt.clear()
                attempt.update(phase="error", error="GitHub sign-in failed. Start again.")

    def commit_simple(self, attempt, subject, login, rows):
        if not self.current(attempt) or subject != attempt["subject"]:
            raise ConnectorError("GitHub connection changed. Start again.")
        common = sorted(set.intersection(*(set(row["capabilities"]) for row in rows)))
        self.hub.save(
            "github",
            {
                "subject": subject,
                "email": login,
                "auth_mode": "github_app",
                "client_id": self.config["client_id"],
                "capabilities": common,
                "repositories": sorted({r["full_name"] for r in rows}),
                "revision": secrets.token_hex(16),
            },
            attempt["tokens"],
        )
        attempt.clear()
        attempt.update(phase="complete", simple=True)

    async def review(self):
        async with self.lock:
            self.require_config()
            old = self.hub.account("github")
            if not old or old.get("auth_mode") != "github_app":
                raise ConnectorError("Connect through GitHub first.")
            token = await self.hub.access_token("github", None)
            await self.close()
            attempt = {"phase": "exchanging"}
            self.pending = attempt
            try:
                subject, login, repos = await self.discover(token)
                if not self.current(attempt):
                    raise ConnectorError("GitHub access review was cancelled.")
                from json import loads

                tokens = loads(self.hub.credentials.get("connector-github-tokens") or "{}")
                if tokens.get("access_token") != token:
                    raise ConnectorError("GitHub connection changed. Start again.")
                attempt.update(
                    phase="select_repositories",
                    subject=subject,
                    login=login,
                    repositories=repos,
                    tokens=tokens,
                    deadline=time.monotonic() + 300,
                )
                self.task = asyncio.create_task(self.expire_review(attempt))
                return self.status()
            except ConnectorError as exc:
                if self.current(attempt):
                    attempt.clear()
                    attempt.update(phase="error", error=str(exc))
                raise

    async def expire_review(self, attempt):
        await self.sleep(300)
        if self.current(attempt):
            attempt.clear()
            attempt.update(phase="error", error=ERRORS["expired_token"])

    async def reload(self):
        async with self.lock:
            attempt = self.pending
            if not attempt or attempt.get("phase") != "select_repositories":
                raise ConnectorError("Start GitHub sign-in again.")
            subject, _, repos = await self.discover(attempt["tokens"]["access_token"])
            if not self.current(attempt) or subject != attempt["subject"]:
                raise ConnectorError("GitHub connection changed. Start again.")
            attempt["repositories"] = repos
            return self.status()

    async def finish(self, repositories, requested):
        from mavo.connectors.catalog import validate_capabilities

        async with self.lock:
            self.require_config()
            attempt = self.pending
            if (
                not attempt
                or attempt.get("phase") != "select_repositories"
                or time.monotonic() >= attempt["deadline"]
            ):
                raise ConnectorError("Start GitHub sign-in again.")
            requested = validate_capabilities(requested)
            subject, login, rows = await self.discover(attempt["tokens"]["access_token"])
            allowed = {r["full_name"]: set(r["capabilities"]) for r in rows}
            if (
                not repositories
                or len(repositories) > 10
                or any(r not in allowed for r in repositories)
                or not requested
                or any(not c.startswith("github:") for c in requested)
                or not set(requested) <= set.intersection(*(allowed[r] for r in repositories))
            ):
                raise ConnectorError(
                    "Choose installed repositories and access GitHub granted to all of them."
                )
            if not self.current(attempt) or subject != attempt["subject"]:
                raise ConnectorError("GitHub sign-in was cancelled or the account changed.")
            self.hub.save(
                "github",
                {
                    "subject": subject,
                    "email": login,
                    "auth_mode": "github_app",
                    "client_id": self.config["client_id"],
                    "capabilities": requested,
                    "repositories": sorted(set(repositories)),
                    "revision": secrets.token_hex(16),
                },
                attempt["tokens"],
            )
            await self.close()
            self.pending = {"phase": "complete"}
            return self.hub.status("github")

    async def refresh(self):
        import json

        async with self.refresh_lock:
            raw = self.hub.credentials.get("connector-github-tokens") or "{}"
            tokens = json.loads(raw)
            if tokens.get("auth_mode") != "github_app":
                return tokens.get("access_token")
            if tokens.get("expires_at", float("inf")) > time.time() + 60:
                return tokens["access_token"]
            if (
                not self.config
                or tokens.get("client_id") != self.config["client_id"]
                or tokens.get("refresh_expires_at", 0) <= time.time()
            ):
                self.hub.invalidate("github")
                raise ConnectorError("GitHub authorization expired. Connect again.")
            data = await self.post(
                "/login/oauth/access_token",
                {
                    "client_id": self.config["client_id"],
                    "grant_type": "refresh_token",
                    "refresh_token": tokens["refresh_token"],
                },
            )
            if data.get("error"):
                self.hub.invalidate("github")
                raise ConnectorError(ERRORS.get(data["error"], "Reconnect GitHub authorization."))
            replacement = self.token_data(data, self.config["client_id"])
            if self.hub.credentials.get("connector-github-tokens") != raw:
                raise ConnectorError("GitHub was disconnected or changed during refresh.")
            self.hub.credentials.set("connector-github-tokens", json.dumps(replacement))
            return replacement["access_token"]
