from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from mavo.connectors.actions import repository_name
from mavo.connectors.catalog import ConnectorAccess
from mavo.connectors.hub import ConnectorError
from mavo.resources.feed_catalog import FEEDS


class GitHubConnect(ConnectorAccess):
    model_config = ConfigDict(extra="forbid")
    token: SecretStr = Field(min_length=10, max_length=512)
    repositories: list[str] = Field(min_length=1, max_length=10)

    @field_validator("repositories")
    @classmethod
    def repositories_valid(cls, values):
        return list(dict.fromkeys(repository_name(v.strip()) for v in values))


class FeedConnect(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sources: list[str] = Field(max_length=len(FEEDS))

    @field_validator("sources")
    @classmethod
    def known_sources(cls, values):
        if any(value not in FEEDS for value in values):
            raise ValueError("Choose supported public feeds")
        return list(dict.fromkeys(values))


class GitHubSelection(ConnectorAccess):
    model_config = ConfigDict(extra="forbid")
    repositories: list[str] = Field(min_length=1, max_length=10)

    @field_validator("repositories")
    @classmethod
    def known_repositories(cls, values):
        return list(dict.fromkeys(repository_name(v) for v in values))


class GitHubStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    simple: bool = False


def build_connector_api(hub):
    router = APIRouter()

    @router.get("/connectors")
    def listing():
        return [hub.status(provider) for provider in ("google", "github", "feeds")]

    @router.post("/connectors/feeds/connect")
    def feeds(payload: FeedConnect):
        hub.feeds.configure(payload.sources, hub.on_disconnect)
        return hub.status("feeds")

    @router.post("/connectors/github/connect")
    async def github(payload: GitHubConnect):
        try:
            await hub.connect_github(
                payload.token.get_secret_value(),
                payload.repositories,
                payload.connector_capabilities,
            )
            return hub.status("github")
        except ConnectorError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/connectors/github/auth")
    def github_auth_status():
        return hub.github_auth.status()

    @router.post("/connectors/github/start")
    async def github_start(payload: GitHubStart | None = None):
        try:
            return await hub.github_auth.start(simple=payload.simple if payload else False)
        except ConnectorError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/connectors/github/revoke")
    async def github_revoke():
        return await hub.revoke_github()

    @router.post("/connectors/github/revocation/confirm")
    def github_revoke_confirm():
        from mavo.connection_revocation import save_receipt

        if hub.status("github")["connected"]:
            raise HTTPException(409, "GitHub is connected; revoke access first")
        return save_receipt(
            hub.factory,
            "github",
            False,
            "Disconnected. You confirmed that Mavo's GitHub authorization was revoked.",
            False,
        )

    @router.post("/connectors/github/cancel")
    async def github_cancel():
        await hub.github_auth.close()
        return hub.github_auth.status()

    @router.post("/connectors/github/review")
    async def github_review():
        try:
            return await hub.github_auth.review()
        except ConnectorError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/connectors/github/repositories")
    async def github_repositories():
        try:
            return await hub.github_auth.reload()
        except ConnectorError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/connectors/github/authorize")
    async def github_authorize(payload: GitHubSelection):
        try:
            return await hub.github_auth.finish(
                payload.repositories, payload.connector_capabilities
            )
        except ConnectorError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.delete("/connectors/{provider}", status_code=204)
    async def disconnect(provider: str):
        if provider not in {"github", "feeds"}:
            raise HTTPException(404, "Unknown connector")
        await hub.disconnect(provider)

    @router.get("/connectors/{provider}/{resource}")
    async def read(provider: str, resource: str):
        if provider not in {"github", "feeds"}:
            raise HTTPException(404, "Unknown connector")
        try:
            return await hub.read(provider, resource)
        except ConnectorError as exc:
            raise HTTPException(409, str(exc)) from exc

    return router
