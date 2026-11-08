"""The resource pool: every resource Mavo can use, the reads and actions behind it, and the
user's own access level for each one.

A resource is a *source of data and a set of actions* (Gmail, Calendar, GitHub, the browser...).
Connecting an account makes its reads and actions *available*; the access level the user picks
here is a ceiling on top of that:

    off    nothing from this resource is used by any Crew
    read   Crew may read it, but none of its actions can run
    write  Crew may also act through it, still subject to Actions rules and per-Crew grants

The ceiling is enforced where it matters (action checks and Crew setup), never only in the UI.
Models can see the pool and propose work that fits it; they cannot change a level.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from mavo.connectors.catalog import ACTIONS as CONNECTOR_ACTIONS
from mavo.database import WorkspaceSetting
from mavo.resource_access import DefaultResourceAccess, read_access, save_access
from mavo.resources.feed_catalog import FEEDS

LEVELS = ("off", "read", "write")
SETTING = "pool-access"

# id -> (name, group, kind, description, read capabilities, supports actions)
RESOURCES = {
    "weather": (
        "Weather",
        "Built in",
        "toggle",
        "Forecasts for any destination.",
        ["weather:read"],
        False,
    ),
    "location": (
        "Location",
        "Built in",
        "toggle",
        "This computer's location, only when allowed.",
        [],
        False,
    ),
    "open_data": (
        "Open data",
        "Built in",
        "local",
        "Free public services with no account or key: places, routes, news, rates, holidays.",
        ["open_data:read"],
        False,
    ),
    "device": (
        "This computer",
        "Built in",
        "local",
        "CPU, memory, battery and storage readings.",
        ["device:read"],
        False,
    ),
    "gmail": (
        "Gmail",
        "Google",
        "google",
        "Inbox headers, and message text if allowed.",
        ["gmail:read", "gmail:body"],
        True,
    ),
    "calendar": ("Calendar", "Google", "google", "Your primary calendar.", ["calendar:read"], True),
    "tasks": ("Tasks", "Google", "google", "Your default task list.", ["tasks:read"], True),
    "drive": (
        "Drive",
        "Google",
        "google",
        "File names, types and links. Never contents.",
        ["drive:read"],
        False,
    ),
    "contacts": (
        "Contacts",
        "Google",
        "google",
        "Names, emails and phone numbers.",
        ["contacts:read"],
        False,
    ),
    "youtube": (
        "YouTube",
        "Google",
        "google",
        "Your subscriptions' new videos and the videos you liked. Read only.",
        ["youtube:read"],
        False,
    ),
    "github": (
        "GitHub",
        "Developer",
        "github",
        "Selected repositories, issues, pull requests and CI.",
        ["github:repo:read", "github:issues:read", "github:pulls:read", "github:workflows:read"],
        True,
    ),
    "feeds": (
        "Public feeds",
        "Developer",
        "feeds",
        "Fixed public RSS sources.",
        [f"feeds:{key}:read" for key in FEEDS],
        False,
    ),
    "browser": (
        "Browser",
        "This computer",
        "local",
        "Mavo's own private browser window, never your personal profile.",
        [],
        True,
    ),
    "terminal": (
        "Terminal",
        "This computer",
        "local",
        "Bounded commands inside folders you choose.",
        [],
        True,
    ),
    "windows": (
        "Windows apps",
        "This computer",
        "local",
        "Find, focus and type into approved apps; lock, volume, music keys and speaking aloud.",
        [],
        True,
    ),
    "notes": (
        "Notes",
        "This computer",
        "local",
        "Markdown notes in folders you choose, such as an Obsidian vault. Never overwritten.",
        [],
        True,
    ),
    "files": (
        "Files",
        "This computer",
        "local",
        "List, move and back up files in folders you choose. Nothing is deleted or overwritten.",
        [],
        True,
    ),
}
ACTION_RESOURCE = {
    "notes": "notes",
    "files": "files",
    "gmail": "gmail",
    "calendar": "calendar",
    "tasks": "tasks",
    "github": "github",
    "browser": "browser",
    "terminal": "terminal",
    "windows": "windows",
}
# Everything the pool lists but Mavo cannot run yet. Never presented as connected.
COMMUNITY = [
    {
        "id": "ntfy",
        "name": "ntfy",
        "license": "Apache-2.0",
        "home": "https://ntfy.sh",
        "adds": "Push the same reminders to your phone.",
        "data_sent": "The reminder text, to a topic you choose",
    },
]
COMMUNITY_NOTE = (
    "Not installed. Nothing is contacted, and nothing is added, until it is built into Mavo "
    "and you switch it on."
)


def operations():
    from mavo.action_rules import CATALOG

    return CATALOG


def resource_for_operation(operation):
    if operation in CONNECTOR_ACTIONS:
        return CONNECTOR_ACTIONS[operation][0]
    return ACTION_RESOURCE.get(operation.split(".")[0])


def resource_for_capability(capability):
    head = capability.split(":")[0]
    if head == "google":
        head = capability.split(":")[1]
    return head if head in RESOURCES else None


def is_write_capability(capability):
    return capability.endswith((":send", ":modify", ":write"))


def _levels(session):
    row = session.get(WorkspaceSetting, SETTING)
    return dict(row.value) if row else {}


def level(session, resource):
    """Weather and Location keep their own flags; everything else defaults to its prior
    behaviour (write) so existing Crew keep working until the user lowers a level."""
    if resource in {"weather", "location"}:
        return "read" if getattr(read_access(session), resource) else "off"
    value = _levels(session).get(resource, "write")
    if value not in LEVELS:
        return "off"
    if value == "write" and not RESOURCES[resource][5]:
        return "read"
    return value


def set_level(session, resource, value, weather=None):
    if resource not in RESOURCES:
        raise HTTPException(404, "Unknown resource")
    if value not in LEVELS:
        raise HTTPException(400, "Choose off, read or write")
    if value == "write" and not RESOURCES[resource][5]:
        raise HTTPException(400, RESOURCES[resource][0] + " has no actions; it can only be read.")
    if resource in {"weather", "location"}:
        current = read_access(session)
        save_access(
            session,
            DefaultResourceAccess(**{**current.model_dump(), resource: value != "off"}),
            weather,
        )
        return
    row = session.get(WorkspaceSetting, SETTING)
    if row:
        row.value = {**row.value, resource: value}
    else:
        session.add(WorkspaceSetting(key=SETTING, value={resource: value}))
    session.commit()


def ceiling_error(session, *, operation=None, capability=None):
    """Why the user's access level blocks this, or None. Used by every enforcement point."""
    resource = (
        resource_for_operation(operation)
        if operation
        else resource_for_capability(capability or "")
    )
    if not resource:
        return None
    current = level(session, resource)
    name = RESOURCES[resource][0]
    if current == "off":
        return f"{name} is off. Turn it on in Resources."
    if (operation or is_write_capability(capability or "")) and current != "write":
        return f"{name} is read only. Allow actions for it in Resources."
    return None


def _connection(runtime, resource):
    """(connected, granted read/write capability ids, note)."""
    kind = RESOURCES[resource][2]
    if kind == "local":
        return True, set(), None
    if kind == "toggle":
        return True, set(), None
    if kind == "google":
        status = runtime.connectors.google.status()
        granted = set(status.get("capabilities", []))
        return bool(status.get("signed_in")), granted, status.get("email")
    status = runtime.connectors.status(kind)
    granted = set(status.get("capabilities", []))
    return bool(status.get("connected")) or bool(granted), granted, status.get("email")


def _available(operation, kind, granted):
    if kind == "local":
        return True
    from mavo.connectors.catalog import action_grant

    grant = action_grant(operation)
    return (grant[7:] if grant.startswith("google:") else grant) in granted


def describe(session, runtime):
    from mavo.action_rules import CATALOG

    result = []
    for rid, (name, group, kind, description, reads, acts) in RESOURCES.items():
        connected, granted, account = _connection(runtime, rid)
        current = level(session, rid)
        ops = [
            {
                "operation": op,
                "label": label,
                "consequential": risk,
                "available": _available(op, kind, granted),
                "enabled_by_you": runtime.actions.rules.read(session, op)["enabled"],
            }
            for op, (_, label, risk) in CATALOG.items()
            if resource_for_operation(op) == rid
        ]
        usable_reads = [c for c in reads if kind in {"local", "toggle"} or c in granted]
        result.append(
            {
                "id": rid,
                "name": name,
                "group": group,
                "kind": kind,
                "description": description,
                "connected": connected if kind not in {"toggle"} else current != "off",
                "account": account if isinstance(account, str) else None,
                "level": current,
                "can_act": acts,
                "reads": reads,
                "readable": usable_reads,
                "actions": ops if acts else [],
            }
        )
    return result


def brief(session, runtime):
    """Small, secret-free view handed to the planning model."""
    return [
        {
            "id": item["id"],
            "name": item["name"],
            "connected": item["connected"],
            "level": item["level"],
            "readable": item["readable"],
            "actions": [a["operation"] for a in item["actions"] if a["available"]],
        }
        for item in describe(session, runtime)
    ]


class LevelChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    level: str


def build_pool_api(runtime, weather, get_session):
    from fastapi import Depends

    router = APIRouter()

    @router.get("/pool")
    def pool(session=Depends(get_session)):
        from mavo.playbooks import PLAYBOOKS
        from mavo.resources.open_data import SOURCES

        return {
            "resources": describe(session, runtime),
            "open_data": [
                {
                    "id": s.id,
                    "name": s.name,
                    "group": s.group,
                    "gives": s.gives,
                    "sends": s.sends,
                    "home": s.home,
                }
                for s in SOURCES.values()
            ],
            "community": [
                {**item, "status": "not_installed", "note": COMMUNITY_NOTE} for item in COMMUNITY
            ],
            "playbooks": PLAYBOOKS,
        }

    @router.put("/pool/{resource}")
    def change(resource: str, payload: LevelChange, session=Depends(get_session)):
        previous = level(session, resource) if resource in RESOURCES else None
        set_level(session, resource, payload.level, weather)
        if previous and LEVELS.index(payload.level) < LEVELS.index(previous):
            _release_crew(session, runtime, resource, payload.level)
        return next(item for item in describe(session, runtime) if item["id"] == resource)

    return router


def _release_crew(session, runtime, resource, new_level):
    """Lowering a level stops running Crew that rely on what was just taken away."""
    from sqlalchemy import select

    from mavo.crew_workers import stop_crew
    from mavo.database import CrewInstance

    for crew in session.scalars(select(CrewInstance).where(CrewInstance.is_running.is_(True))):
        draft = (crew.configuration_plan or {}).get("draft") or {}
        wanted = [
            *draft.get("requested_google_capabilities", []),
            *draft.get("requested_connector_capabilities", []),
        ]
        wanted += [action["operation"] for action in draft.get("requested_actions", [])]
        for item in wanted:
            op = item if "." in item else None
            blocked = ceiling_error(session, operation=op, capability=None if op else item)
            if (
                blocked
                and (resource_for_operation(item) if op else resource_for_capability(item))
                == resource
            ):
                stop_crew(session, crew)
                runtime.cancel(crew.id)
                crew.runtime_state = "needs_setup"
                crew.last_runtime_error = blocked
                break
    session.commit()
