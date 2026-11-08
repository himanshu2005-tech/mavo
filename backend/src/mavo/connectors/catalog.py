"""Server-owned connector/action metadata. A manifest is not an account connection."""

from pydantic import BaseModel, Field, field_validator

from mavo.resources.feed_catalog import FEEDS

CAPABILITIES = {
    "github:repo:read": ("GitHub", "Read selected repositories", ""),
    "github:issues:read": ("GitHub", "Read issues in selected repositories", ""),
    "github:pulls:read": ("GitHub", "Read pull requests in selected repositories", ""),
    "github:workflows:read": ("GitHub", "Read workflow and CI run status", ""),
    "github:issues:write": ("GitHub", "Create issues and comments", ""),
    **{
        f"feeds:{key}:read": ("Public feeds", f"Read {value[0]} titles and links", "")
        for key, value in FEEDS.items()
    },
}
# operation -> provider, capability suffix, group, label, consequential
ACTIONS = {
    "github.create_issue": ("github", "issues:write", "GitHub", "Create issue", True),
    "github.comment": ("github", "issues:write", "GitHub", "Comment on issue", True),
}


def validate_capabilities(values):
    if any(value not in CAPABILITIES for value in values):
        raise ValueError("Unsupported connector capability")
    result = set(values)
    for key in list(result):
        if key.endswith(":body"):
            result.add(key.rsplit(":", 1)[0] + ":read")
    return sorted(result)


def provider_for(operation):
    if operation in ACTIONS:
        return ACTIONS[operation][0]
    return (
        "local"
        if operation.split(".")[0]
        in {"browser", "terminal", "windows", "project", "helper", "notes", "files"}
        else "google"
    )


def action_grant(operation):
    if operation in ACTIONS:
        provider, capability, *_ = ACTIONS[operation]
        return provider + ":" + capability
    service, verb = operation.split(".")
    if provider_for(operation) == "local":
        return "action:" + operation
    capability = "send" if verb == "send" else "modify" if service == "gmail" else "write"
    return f"google:{service}:{capability}"


class ConnectorAccess(BaseModel):
    connector_capabilities: list[str] = Field(default_factory=list, max_length=16)

    @field_validator("connector_capabilities")
    @classmethod
    def known_capabilities(cls, values):
        return validate_capabilities(values)
