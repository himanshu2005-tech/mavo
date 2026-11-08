"""Read-only Drive metadata and People contacts. No downloads or write endpoints."""

from datetime import UTC, datetime

from mavo.database import WorkspaceSetting
from mavo.google_auth import GoogleConnectionError

SERVICES = {"drive", "contacts"}


def reserve_read(factory, resource):
    """Small persistent per-device ceiling; not a substitute for project billing controls."""
    key = "google-extension-budget-" + resource
    today = datetime.now(UTC).date().isoformat()
    with factory() as session:
        # Force a write transaction before inspecting the JSON counter.
        from sqlalchemy import text

        session.execute(text("BEGIN IMMEDIATE"))
        row = session.get(WorkspaceSetting, key)
        value = row.value if row and row.value.get("day") == today else {"day": today, "calls": 0}
        if value["calls"] >= 200:
            raise GoogleConnectionError(
                f"Google {resource} reached Mavo's 200-request daily safety limit. Try tomorrow."
            )
        value = {"day": today, "calls": value["calls"] + 1}
        if row:
            row.value = value
        else:
            session.add(WorkspaceSetting(key=key, value=value))
        session.commit()


async def fetch_metadata(pool, resource):
    if resource == "drive":
        params = {
            "q": "trashed = false",
            "spaces": "drive",
            "pageSize": 100,
            "orderBy": "modifiedTime desc",
            "fields": (
                "nextPageToken,incompleteSearch,files(id,name,mimeType,modifiedTime,webViewLink)"
            ),
        }
        data = await pool.get("drive", "/files", params)
        rows = data.get("files", [])
        if not isinstance(rows, list):
            raise GoogleConnectionError("Google returned invalid Drive metadata")
        return [
            {
                k: str(item.get(k, ""))[:2000]
                for k in ("id", "name", "mimeType", "modifiedTime", "webViewLink")
            }
            for item in rows[:100]
            if isinstance(item, dict) and item.get("id")
        ]
    data = await pool.get(
        "contacts",
        "/people/me/connections",
        {
            "pageSize": 100,
            "personFields": "names,emailAddresses,phoneNumbers",
            "sources": "READ_SOURCE_TYPE_CONTACT",
            "sortOrder": "FIRST_NAME_ASCENDING",
            "fields": (
                "nextPageToken,connections(resourceName,names(displayName),"
                "emailAddresses(value),phoneNumbers(value))"
            ),
        },
    )
    rows = data.get("connections", [])
    if not isinstance(rows, list):
        raise GoogleConnectionError("Google returned invalid Contacts data")
    for item in rows[:100]:
        if not isinstance(item, dict):
            raise GoogleConnectionError("Google returned invalid Contacts data")
        for key in ("names", "emailAddresses", "phoneNumbers"):
            values = item.get(key, [])
            if not isinstance(values, list) or any(not isinstance(v, dict) for v in values):
                raise GoogleConnectionError("Google returned invalid Contacts data")
    return [
        {
            "id": str(item.get("resourceName", ""))[:255],
            "name": str(next(iter(item.get("names", [])), {}).get("displayName", ""))[:500],
            "emails": [
                str(v.get("value", ""))[:254]
                for v in item.get("emailAddresses", [])[:3]
                if isinstance(v, dict)
            ],
            "phones": [
                str(v.get("value", ""))[:64]
                for v in item.get("phoneNumbers", [])[:3]
                if isinstance(v, dict)
            ],
        }
        for item in rows[:100]
        if isinstance(item, dict) and item.get("resourceName")
    ]
