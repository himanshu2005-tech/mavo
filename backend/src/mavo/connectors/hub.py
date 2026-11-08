"""Optional connected accounts, bounded reads, and strict provider boundaries."""

import asyncio
import json
import time

import httpx

from mavo.connectors.catalog import (
    ACTIONS,
    CAPABILITIES,
    action_grant,
    provider_for,
    validate_capabilities,
)
from mavo.database import ActionRequest, CrewInstance, CrewRun, WorkspaceSetting, utcnow
from mavo.google_auth import GoogleConnectionError
from mavo.google_permissions import CAPABILITIES as GOOGLE_CAPABILITIES
from mavo.resources.feeds import PublicFeeds

GITHUB_READS = {
    "repo": (
        "",
        ("id", "full_name", "html_url", "default_branch", "open_issues_count", "pushed_at"),
    ),
    "issues": ("/issues", ("id", "number", "title", "state", "updated_at", "html_url")),
    "pulls": ("/pulls", ("id", "number", "title", "state", "draft", "updated_at", "html_url")),
    "workflows": (
        "/actions/runs",
        (
            "id",
            "name",
            "display_title",
            "status",
            "conclusion",
            "html_url",
            "run_number",
            "run_attempt",
            "head_branch",
            "head_sha",
            "event",
            "updated_at",
        ),
    ),
}


class ConnectorError(GoogleConnectionError):
    """Caller must stop rather than invent a result."""


class ConnectorRejected(ConnectorError):
    """The provider definitively rejected a request; no replay is authorized."""


class ConnectorHub:
    def __init__(self, factory, credentials, google, transport=None):
        self.factory, self.credentials, self.google = factory, credentials, google
        self.transport = transport
        self.cache = {}
        self.read_lock = asyncio.Lock()
        self.on_disconnect = None
        self.feeds = PublicFeeds(factory, transport)
        self.github_auth = None

    @staticmethod
    def key(provider):
        if provider != "github":
            raise ConnectorError("Unsupported connector")
        return "connector-" + provider

    def account(self, provider):
        with self.factory() as session:
            row = session.get(WorkspaceSetting, self.key(provider))
            return dict(row.value) if row else None

    def status(self, provider):
        from mavo.connection_revocation import read_receipt

        if provider == "feeds":
            return self.feeds.status()
        if provider == "google":
            status = self.google.status()
            return {
                "provider": "google",
                "name": "Google Workspace",
                "configured": status["configured"],
                "connected": status["signed_in"],
                "email": status.get("email"),
                "capabilities": ["google:" + c for c in status.get("capabilities", [])],
                "available_capabilities": ["google:" + c for c in GOOGLE_CAPABILITIES],
            }
        account = self.account(provider)
        with self.factory() as session:
            receipt = read_receipt(session, provider)
        connected = bool(
            account
            and not account.get("needs_reconnect")
            and self.credentials.get(self.key(provider) + "-tokens")
        )
        return {
            "provider": provider,
            "name": "GitHub",
            "configured": True,
            "connected": connected,
            "email": account.get("email") if account else None,
            "capabilities": account.get("capabilities", []) if connected else [],
            "available_capabilities": [
                key for key in CAPABILITIES if key.startswith(provider + ":")
            ],
            "repositories": account.get("repositories", []) if account else [],
            "auth_mode": account.get("auth_mode", "token") if account else None,
            "revocation": receipt,
        }

    def capability_available(self, operation):
        provider = provider_for(operation)
        return (
            provider == "local" or action_grant(operation) in self.status(provider)["capabilities"]
        )

    def subject(self, provider):
        if provider == "google":
            from mavo.database import GoogleAccount

            with self.factory() as session:
                account = session.get(GoogleAccount, "google")
                return account.subject if account else None
        account = self.account(provider)
        return provider + ":" + account["subject"] if account else None

    async def request(self, provider, method, path, *, token=None, capability=None, **options):
        self.key(provider)
        if not path.startswith("/") or path.startswith("//") or ".." in path.split("/"):
            raise ConnectorError("Invalid provider path")
        uses_saved_token = token is None
        if uses_saved_token:
            token = await self.access_token(provider, capability)
        headers = {
            "Authorization": "Bearer " + token,
            **options.pop("headers", {}),
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        try:
            async with httpx.AsyncClient(
                transport=self.transport, timeout=20, follow_redirects=False
            ) as client:
                response = await client.request(
                    method, "https://api.github.com" + path, headers=headers, **options
                )
        except httpx.HTTPError as exc:
            raise ConnectorError("GitHub could not be reached. No automatic write retry.") from exc
        if response.status_code in {401, 403}:
            if uses_saved_token:
                self.invalidate(provider, capability if response.status_code == 403 else None)
            raise ConnectorRejected(
                "GitHub permission is missing, expired, or blocked by account policy. "
                "Reconnect or review consent."
            )
        if response.status_code == 429:
            raise ConnectorRejected("GitHub rate limit reached. Try later.")
        if response.is_error or response.is_redirect:
            failure = (
                ConnectorRejected
                if 400 <= response.status_code < 500 and response.status_code != 408
                else ConnectorError
            )
            raise failure(f"GitHub request failed (HTTP {response.status_code}).")
        return response

    def invalidate(self, provider, capability=None):
        """Fence affected work when a provider rejects a saved permission."""
        from mavo.crew_workers import stop_crew

        with self.factory() as session:
            row = session.get(WorkspaceSetting, self.key(provider))
            if row:
                value = dict(row.value)
                if capability:
                    value["capabilities"] = [c for c in value["capabilities"] if c != capability]
                else:
                    value["needs_reconnect"] = True
                row.value = value
            for crew in session.query(CrewInstance).all():
                if any(
                    p == capability if capability else p.startswith(provider + ":")
                    for p in crew.permissions
                ):
                    stop_crew(session, crew)
                    crew.last_runtime_error = "Reconnect GitHub or review account permissions."
                    if self.on_disconnect:
                        self.on_disconnect(crew.id)
            session.commit()
        self.cache.clear()

    def save(self, provider, metadata, tokens):
        previous = self.account(provider)
        if previous and previous["subject"] != metadata["subject"]:
            raise ConnectorError("Disconnect the existing account before choosing another account.")
        key = self.key(provider)
        old = self.credentials.get(key + "-tokens")
        self.credentials.set(key + "-tokens", json.dumps(tokens))
        try:
            with self.factory() as session:
                from sqlalchemy import delete

                session.execute(
                    delete(WorkspaceSetting).where(
                        WorkspaceSetting.key == "connection-revocation-" + provider
                    )
                )
                row = session.get(WorkspaceSetting, key)
                if row:
                    row.value = metadata
                else:
                    session.add(WorkspaceSetting(key=key, value=metadata))
                if previous and (
                    previous.get("repositories") != metadata.get("repositories")
                    or previous.get("capabilities") != metadata.get("capabilities")
                ):
                    from mavo.crew_workers import stop_crew

                    for crew in session.query(CrewInstance).all():
                        if any(p.startswith("github:") for p in crew.permissions):
                            stop_crew(session, crew)
                            crew.checkpoint, crew.observation = None, {}
                            crew.last_runtime_error = (
                                "GitHub access changed. Review access and Run again."
                            )
                            if self.on_disconnect:
                                self.on_disconnect(crew.id)
                session.commit()
        except Exception:
            if old:
                self.credentials.set(key + "-tokens", old)
            else:
                self.credentials.delete(key + "-tokens")
            raise
        self.cache.clear()

    async def connect_github(self, token, repositories, capabilities):
        if self.github_auth:
            await self.github_auth.close()
        capabilities = validate_capabilities(capabilities)
        if any(not c.startswith("github:") for c in capabilities):
            raise ConnectorError("Only GitHub capabilities belong to a GitHub connection")
        identity = (await self.request("github", "GET", "/user", token=token)).json()
        for repository in repositories:
            await self.request("github", "GET", "/repos/" + repository, token=token)
        if not identity.get("id"):
            raise ConnectorError("GitHub identity could not be verified")
        self.save(
            "github",
            {
                "subject": str(identity["id"]),
                "email": identity.get("login", ""),
                "capabilities": capabilities,
                "repositories": repositories,
            },
            {"access_token": token},
        )

    async def access_token(self, provider, capability):
        status = self.status(provider)
        if not status["connected"] or (capability and capability not in status["capabilities"]):
            raise ConnectorError(f"Connect {provider.title()} and consent to {capability} first.")
        tokens = json.loads(self.credentials.get(self.key(provider) + "-tokens") or "{}")
        if not tokens.get("access_token"):
            self.invalidate(provider)
            raise ConnectorError("Reconnect GitHub: saved authorization is unavailable.")
        if tokens.get("auth_mode") == "github_app":
            if not self.github_auth:
                raise ConnectorError("GitHub App authorization is unavailable. Reconnect GitHub.")
            return await self.github_auth.refresh()
        return tokens["access_token"]

    async def read(self, provider, resource):
        if provider == "feeds":
            try:
                return await self.feeds.read(resource)
            except GoogleConnectionError as exc:
                raise ConnectorError(str(exc)) from exc
        self.key(provider)
        if resource not in GITHUB_READS:
            raise ConnectorError("Unsupported GitHub resource")
        try:
            return await asyncio.wait_for(self._read(provider, resource), 40)
        except TimeoutError as exc:
            raise ConnectorError("GitHub collection took too long. Try a narrower review.") from exc

    async def _read(self, provider, resource):
        capability = provider + ":" + resource + ":read"
        await self.access_token(provider, capability)
        account = self.account(provider)
        key = (provider, account["subject"], resource, json.dumps(account, sort_keys=True))
        async with self.read_lock:
            await self.access_token(provider, capability)
            if self.account(provider) != account:
                raise ConnectorError("Connector access changed during collection")
            cached = self.cache.get(key)
            if cached and cached[0] > time.monotonic():
                return cached[1]
            items = []
            suffix, fields = GITHUB_READS[resource]
            for repository in account.get("repositories", []):
                path = "/repos/" + repository + suffix
                data = (
                    await self.request(
                        provider, "GET", path, capability=capability, params={"per_page": 20}
                    )
                ).json()
                if resource == "repo":
                    if not isinstance(data, dict):
                        raise ConnectorError("GitHub returned invalid repository metadata")
                    items.append({"repository": repository, **{k: data.get(k) for k in fields}})
                else:
                    rows = (
                        data.get("workflow_runs")
                        if resource == "workflows" and isinstance(data, dict)
                        else data
                    )
                    if not isinstance(rows, list) or any(
                        not isinstance(item, dict) for item in rows[:20]
                    ):
                        raise ConnectorError("GitHub returned invalid resource data")
                    items.extend(
                        {"repository": repository, **{k: item.get(k) for k in fields}}
                        for item in rows[:20]
                        if resource != "issues" or "pull_request" not in item
                    )
            if (
                self.subject(provider) != provider + ":" + account["subject"]
                or capability not in self.status(provider)["capabilities"]
                or self.account(provider) != account
            ):
                raise ConnectorError("Connector access changed during collection")
            snapshot = {
                "provider": provider,
                "resource": resource,
                "items": items,
                "fetched_at": utcnow().isoformat(),
                "untrusted": True,
            }
            self.cache[key] = (time.monotonic() + 60, snapshot)
            return snapshot

    async def disconnect(self, provider):
        if provider == "feeds":
            self.feeds.configure([], self.on_disconnect)
            return
        if self.github_auth:
            await self.github_auth.close()
        self.credentials.delete(self.key(provider) + "-tokens")
        from sqlalchemy import delete, update

        from mavo.crew_workers import stop_crew
        from mavo.database import ReminderEvent

        with self.factory() as session:
            session.execute(
                delete(WorkspaceSetting).where(WorkspaceSetting.key == self.key(provider))
            )
            for crew in session.query(CrewInstance).all():
                if any(p.startswith(provider + ":") for p in crew.permissions):
                    stop_crew(session, crew)
                    crew.permissions = [
                        p for p in crew.permissions if not p.startswith(provider + ":")
                    ]
                    crew.checkpoint = None
                    crew.observation = {}
                    if self.on_disconnect:
                        self.on_disconnect(crew.id)
                    session.execute(
                        update(CrewRun).where(CrewRun.crew_id == crew.id).values(result=None)
                    )
                    session.execute(
                        update(ReminderEvent)
                        .where(ReminderEvent.crew_id == crew.id)
                        .values(message="Removed after connector disconnection.")
                    )
            operations = [op for op, entry in ACTIONS.items() if entry[0] == provider]
            session.execute(
                update(ActionRequest)
                .where(ActionRequest.operation.in_(operations))
                .values(
                    arguments={},
                    result=None,
                    target_summary=None,
                    status="redacted",
                    error="Connector disconnected.",
                    finished_at=utcnow(),
                )
            )
            session.commit()
        self.cache.clear()

    async def revoke_github(self):
        from mavo.connection_revocation import save_receipt

        await self.disconnect("github")
        return save_receipt(
            self.factory,
            "github",
            False,
            "Disconnected from Mavo. Revoke Mavo in GitHub's Authorized GitHub Apps "
            "to remove its grant too.",
            True,
        )
