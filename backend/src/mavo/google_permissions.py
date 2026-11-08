"""Publisher-owned capability catalog. Models can suggest these names, never grant them."""

from typing import Literal

GoogleCapability = Literal[
    "gmail:read",
    "gmail:body",
    "gmail:send",
    "gmail:modify",
    "calendar:read",
    "calendar:write",
    "tasks:read",
    "tasks:write",
    "drive:read",
    "contacts:read",
    "youtube:read",
]
BASE = "https://www.googleapis.com/auth/"
CAPABILITIES = {
    "gmail:read": ("Gmail", "Read inbox headers", "gmail.metadata"),
    "gmail:body": ("Gmail", "Read email contents", "gmail.readonly"),
    "gmail:send": ("Gmail", "Send email under saved Actions policy", "gmail.send"),
    "gmail:modify": (
        "Gmail",
        "Draft, archive, mark read, or move to trash under saved Actions policy",
        "gmail.modify",
    ),
    "calendar:read": ("Calendar", "Read upcoming events", "calendar.readonly"),
    "calendar:write": (
        "Calendar",
        "Create, edit, or delete events under saved Actions policy",
        "calendar.events",
    ),
    "tasks:read": ("Tasks", "Read tasks", "tasks.readonly"),
    "tasks:write": (
        "Tasks",
        "Create, edit, complete, or delete tasks under saved Actions policy",
        "tasks",
    ),
    "drive:read": (
        "Drive",
        "Read file names, types and links (not contents)",
        "drive.metadata.readonly",
    ),
    "contacts:read": (
        "Contacts",
        "Read contact names, email addresses and phone numbers",
        "contacts.readonly",
    ),
    "youtube:read": (
        "YouTube",
        "See your subscriptions, their new videos and the videos you liked",
        "youtube.readonly",
    ),
}

GOOGLE_SERVICES = ("gmail", "calendar", "tasks", "drive", "contacts", "youtube")


def granted_capabilities(scopes):
    granted = set(scopes)
    result = {key for key, (_, _, scope) in CAPABILITIES.items() if BASE + scope in granted}
    if BASE + "gmail.modify" in granted:
        result.update(("gmail:read", "gmail:body", "gmail:send"))
    if BASE + "gmail.readonly" in granted:
        result.add("gmail:read")
    if BASE + "tasks" in granted:
        result.add("tasks:read")
    return sorted(result)


def catalog():
    return [
        {
            "id": key,
            "service": service,
            "label": label,
            "write": key.endswith((":send", ":modify", ":write")),
            "scope": BASE + scope,
        }
        for key, (service, label, scope) in CAPABILITIES.items()
    ]
