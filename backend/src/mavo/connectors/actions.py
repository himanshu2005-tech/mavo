"""Typed optional-provider actions. No arbitrary URLs, shell, or executable plugins."""

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from mavo.connectors.catalog import action_grant, provider_for
from mavo.connectors.hub import ConnectorError


def repository_name(value):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value) or any(
        p in {".", ".."} for p in value.split("/")
    ):
        raise ValueError("Use an exact owner/repository name")
    return value


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Repository(Arguments):
    repository: str = Field(min_length=3, max_length=250)
    validate_repository = field_validator("repository")(repository_name)


class Issue(Repository):
    title: str = Field(min_length=1, max_length=250)
    body: str = Field(default="", max_length=16000)


class Comment(Repository):
    number: int = Field(ge=1, le=2147483647)
    body: str = Field(min_length=1, max_length=16000)


MODELS = {"github.create_issue": Issue, "github.comment": Comment}


async def perform(hub, action, guard):
    if action.operation not in MODELS:
        raise ConnectorError("Unsupported connector action")
    provider = provider_for(action.operation)
    capability = action_grant(action.operation)
    args = action.arguments

    async def request(method, path, **kwargs):
        guard()
        response = await hub.request(provider, method, path, capability=capability, **kwargs)
        guard()
        return response

    repository = args["repository"]
    if repository not in hub.account("github").get("repositories", []):
        raise ConnectorError("Repository is outside this connection's selected repositories")
    path = "/repos/" + repository + "/issues"
    if action.operation == "github.comment":
        path += f"/{args['number']}/comments"
    payload = {"body": args.get("body", "")}
    if action.operation == "github.create_issue":
        payload["title"] = args["title"]
    result = (await request("POST", path, json=payload)).json()
    if not result.get("id"):
        raise ConnectorError("GitHub returned no outcome identifier")
    observed = (
        await request(
            "GET",
            "/repos/"
            + repository
            + (
                f"/issues/comments/{result['id']}"
                if action.operation == "github.comment"
                else f"/issues/{result['number']}"
            ),
        )
    ).json()
    if any(observed.get(k) != v for k, v in payload.items()):
        raise ConnectorError("GitHub outcome did not match the request")
    hub.cache.clear()
    return {"id": result["id"], "url": result.get("html_url"), "verification": "read_back_verified"}
