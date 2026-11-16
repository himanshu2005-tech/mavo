from __future__ import annotations

import asyncio
import hashlib
import html
import json
import re
import time
from datetime import UTC, date, datetime, timedelta
from urllib.parse import quote

import httpx
from sqlalchemy.exc import IntegrityError

from mavo.database import GoogleAccount, ResourceEvent
from mavo.google_auth import GoogleAuth, GoogleConnectionError
from mavo.google_permissions import GOOGLE_SERVICES
from mavo.resources.google_extended import SERVICES, fetch_metadata, reserve_read
from mavo.resources.mail_text import message_text

EVENT_RESOURCES = {
    "gmail_message": "gmail",
    "calendar_changed": "calendar",
    "tasks_changed": "tasks",
}


def valid_items(data, name):
    rows = data.get("items", [])
    if not isinstance(rows, list) or any(
        not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]
        for item in rows
    ):
        raise GoogleConnectionError(f"Google returned invalid {name} items")
    return rows


CALENDAR_DAYS = 30  # how far ahead a calendar read goes; further is read when asked for
YT_FEED = "https://www.youtube.com/feeds/videos.xml"
YT_CHANNEL = re.compile(r"UC[A-Za-z0-9_-]{22}")
YT_VIDEO = re.compile(r"[A-Za-z0-9_-]{11}")
_ENTRY = re.compile(r"<entry>(.*?)</entry>", re.S)


def _field(entry, tag):
    found = re.search(rf"<{tag}>(.*?)</{tag}>", entry, re.S)
    return html.unescape(found.group(1).strip()) if found else ""


def parse_feed(text, channel_title=""):
    """A channel's public feed, read for its few fixed fields only. Plain matching, not an XML
    parser: the feed is untrusted, and nothing in it is expanded or followed."""
    videos = []
    for entry in _ENTRY.findall(text[:2_000_000])[:15]:
        vid = _field(entry, "yt:videoId")
        if not YT_VIDEO.fullmatch(vid):
            continue
        videos.append(
            {
                "id": vid,
                "kind": "upload",
                "title": _field(entry, "title")[:300],
                "channel": (_field(entry, "name") or channel_title)[:200],
                "published": _field(entry, "published")[:40],
                "url": "https://www.youtube.com/watch?v=" + vid,
            }
        )
    return videos


class GmailHistoryExpired(GoogleConnectionError):
    """Gmail keeps its change history about a week: older markers are gone. Not a setup problem."""


class GoogleResourcePool:
    def __init__(self, auth: GoogleAuth):
        self.auth = auth
        self.factory = auth.factory
        self.transport = auth.transport
        self.locks = {key: asyncio.Lock() for key in GOOGLE_SERVICES}
        self.cache = {}

    async def check_connections(self):
        authorized = self.auth.status()["resources"]

        async def check(resource):
            if resource not in authorized:
                return {
                    "resource": resource,
                    "status": "not_authorized",
                    "detail": "Approve this service through Connect all Google services.",
                }
            probes = {
                "gmail": ("/users/me/profile", {"fields": "historyId"}),
                "calendar": ("/calendars/primary", {"fields": "id"}),
                "tasks": ("/users/@me/lists", {"maxResults": 1, "fields": "items/id"}),
                "drive": ("/files", {"pageSize": 1, "fields": "files/id", "q": "trashed = false"}),
                "contacts": (
                    "/people/me/connections",
                    {
                        "pageSize": 1,
                        "personFields": "names",
                        "fields": "connections/resourceName",
                        "sources": "READ_SOURCE_TYPE_CONTACT",
                    },
                ),
                "youtube": ("/channels", {"part": "id", "mine": "true", "fields": "items/id"}),
            }
            try:
                path, params = probes[resource]
                await self.get(resource, path, params)
                return {
                    "resource": resource,
                    "status": "connected",
                    "detail": "Google accepted an authenticated read request.",
                }
            except GoogleConnectionError as exc:
                return {"resource": resource, "status": "error", "detail": str(exc)}

        return {
            "checked_at": datetime.now(UTC).isoformat(),
            "services": await asyncio.gather(*(check(key) for key in self.locks)),
        }

    def clear(self):
        self.cache.clear()

    async def get(self, resource, path, params=None, capability="read"):
        token = await self.auth.access_token(resource, capability)
        bases = {
            "gmail": "https://gmail.googleapis.com/gmail/v1",
            "calendar": "https://www.googleapis.com/calendar/v3",
            "tasks": "https://tasks.googleapis.com/tasks/v1",
            "drive": "https://www.googleapis.com/drive/v3",
            "contacts": "https://people.googleapis.com/v1",
            "youtube": "https://www.googleapis.com/youtube/v3",
        }
        if resource not in bases:
            raise GoogleConnectionError("Unsupported Google resource")
        if resource in SERVICES:
            if (resource == "drive" and path != "/files") or (
                resource == "contacts" and path != "/people/me/connections"
            ):
                raise GoogleConnectionError("This connector exposes read-only metadata lists only")
            reserve_read(self.factory, resource)
        try:
            async with httpx.AsyncClient(
                transport=self.transport, timeout=20, headers={"Authorization": "Bearer " + token}
            ) as client:
                response = await client.get(bases[resource] + path, params=params)
                response.raise_for_status()
                result = response.json()
                if not isinstance(result, dict):
                    raise ValueError("Invalid resource")
                return result
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 403:
                try:
                    error = exc.response.json().get("error", {})
                    reasons = [d.get("reason") for d in error.get("details", [])]
                    reasons += [d.get("reason") for d in error.get("errors", [])]
                except (ValueError, AttributeError, TypeError):
                    reasons = []
                if {"SERVICE_DISABLED", "accessNotConfigured"}.intersection(reasons):
                    service = {
                        "gmail": "Gmail API",
                        "calendar": "Google Calendar API",
                        "tasks": "Google Tasks API",
                        "drive": "Google Drive API",
                        "contacts": "People API",
                        "youtube": "YouTube Data API v3",
                    }[resource]
                    raise GoogleConnectionError(
                        f"Enable {service} in the Google Cloud project that owns Mavo's client ID."
                    ) from exc
            if exc.response.status_code in {401, 403}:
                raise GoogleConnectionError(
                    "Google permission is missing or revoked. Check Settings and enabled APIs."
                ) from exc
            if exc.response.status_code == 404 and resource == "gmail" and "/history" in path:
                raise GmailHistoryExpired(
                    "Gmail's change history moved on; starting a fresh one."
                ) from exc
            raise GoogleConnectionError(
                f"Google {resource} request failed ({exc.response.status_code})"
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise GoogleConnectionError(
                f"Google {resource} is unavailable. Try again later."
            ) from exc

    async def mail_metadata(self, message_id):
        item = await self.get(
            "gmail",
            "/users/me/messages/" + quote(message_id, safe=""),
            {"format": "metadata", "metadataHeaders": ["From", "Subject", "Date", "Message-ID"]},
        )
        payload = item.get("payload", {})
        rows = payload.get("headers", []) if isinstance(payload, dict) else None
        labels = item.get("labelIds", [])
        if (
            item.get("id") != message_id
            or not isinstance(rows, list)
            or any(
                not isinstance(h, dict)
                or not isinstance(h.get("name"), str)
                or not isinstance(h.get("value"), str)
                for h in rows
            )
            or not isinstance(labels, list)
            or any(not isinstance(label, str) for label in labels)
        ):
            raise GoogleConnectionError("Google returned invalid inbox headers")
        headers = {h["name"].casefold(): h["value"] for h in rows}
        return {
            "id": item["id"],
            "thread_id": item.get("threadId"),
            "rfc_message_id": headers.get("message-id", "")[:512],
            "sender": headers.get("from", "")[:500],
            "subject": headers.get("subject", "")[:1000],
            "date": headers.get("date", "")[:100],
            "internal_date": item.get("internalDate", ""),
            "labels": labels,
        }

    async def message_body(self, message_id):
        item = await self.get(
            "gmail",
            "/users/me/messages/" + quote(message_id, safe=""),
            {"format": "full"},
            capability="body",
        )
        text, body_format = message_text(item.get("payload", {}))
        if item.get("id") != message_id:
            raise GoogleConnectionError("Google returned a different message than requested")
        return {
            "id": item["id"],
            "thread_id": item.get("threadId"),
            "body": text
            or "No readable body available. Attachments and remote content are not loaded.",
            "body_format": body_format,
            "body_available": bool(text),
        }

    async def fetch_gmail(self, cursor):
        events = []
        if not cursor.get("history_id"):
            profile = await self.get("gmail", "/users/me/profile")
            listing = await self.get(
                "gmail", "/users/me/messages", {"labelIds": "INBOX", "maxResults": 20}
            )
            semaphore = asyncio.Semaphore(4)

            async def read(item):
                async with semaphore:
                    return await self.mail_metadata(item["id"])

            items = await asyncio.gather(*(read(item) for item in listing.get("messages", [])))
            return items, {"history_id": str(profile["historyId"])}, events
        try:
            return await self._gmail_since(cursor)
        except GmailHistoryExpired:
            # Mavo was off longer than Gmail keeps history: start again from the inbox as it is
            # now, and treat what arrived since the last look as new, so nothing waits on a
            # reconnect and nothing seen before is reported twice.
            fresh, baseline, _ = await self.fetch_gmail({})
            if not cursor.get("snapshot"):
                # Nothing to compare against: start afresh without calling old mail new.
                return fresh, baseline, []
            seen = {item.get("id") for item in cursor.get("snapshot", [])}
            newest = max(
                (int(i.get("internal_date") or 0) for i in cursor.get("snapshot", [])), default=0
            )
            events = [
                (item["id"], item)
                for item in reversed(fresh)
                if item["id"] not in seen and int(item.get("internal_date") or 0) > newest
            ]
            return fresh, baseline, events

    async def _gmail_since(self, cursor):
        events = []
        history_id = str(cursor["history_id"])
        page = None
        items = list(cursor.get("snapshot", []))
        for _ in range(5):
            params = {
                "startHistoryId": history_id,
                "historyTypes": "messageAdded",
                "maxResults": 100,
            }
            if page:
                params["pageToken"] = page
            data = await self.get("gmail", "/users/me/history", params)
            for change in data.get("history", []):
                for added in change.get("messagesAdded", []):
                    message = added.get("message", {})
                    if "INBOX" not in message.get("labelIds", []):
                        continue
                    try:
                        item = await self.mail_metadata(message["id"])
                    except GoogleConnectionError:
                        # Do not move the history cursor past an unread message.
                        raise
                    events.append((item["id"], item))
                    items = [item, *[old for old in items if old["id"] != item["id"]]][:20]
            page = data.get("nextPageToken")
            if not page:
                history_id = str(data.get("historyId", history_id))
                break
        if page:
            history = data.get("history", [])
            if not history:
                raise GoogleConnectionError("Gmail history could not advance. Try again later.")
            history_id = str(history[-1]["id"])
        return items, {"history_id": history_id}, events

    async def fetch_calendar(self, start: datetime | None = None, end: datetime | None = None):
        """Events from now to CALENDAR_DAYS ahead, or between the two times given."""
        items = []
        page = None
        for _ in range(5):
            params = {
                "timeMin": (start or datetime.now(UTC)).isoformat(),
                "timeMax": (end or datetime.now(UTC) + timedelta(days=CALENDAR_DAYS)).isoformat(),
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": 100,
            }
            if page:
                params["pageToken"] = page
            data = await self.get("calendar", "/calendars/primary/events", params)
            rows = valid_items(data, "Calendar")
            for item in rows:
                items.append(
                    with_days(
                        {
                            key: item.get(key)
                            for key in (
                                "id",
                                "etag",
                                "summary",
                                "status",
                                "start",
                                "end",
                                "updated",
                                "location",
                            )
                        }
                    )
                )
            page = data.get("nextPageToken")
            if not page:
                return items + await self.other_calendars(params)
        raise GoogleConnectionError(
            "More than 500 events in the selected window. Narrow calendar support is required."
        )

    async def other_calendars(self, window):
        """The person's other calendars that are switched on in Google Calendar (holidays,
        shared or subscribed ones), so "is tomorrow a holiday?" has an answer. Each event says
        which calendar it is from and is read-only to Mavo; one that fails is left out."""
        try:
            listing = await self.get("calendar", "/users/me/calendarList", {"maxResults": 50})
        except GoogleConnectionError:
            return []
        extra = []
        for cal in [
            c
            for c in listing.get("items", [])
            if isinstance(c, dict)
            and c.get("selected")
            and not c.get("primary")
            and isinstance(c.get("id"), str)
        ][:6]:
            params = {k: v for k, v in window.items() if k != "pageToken"} | {"maxResults": 50}
            try:
                data = await self.get(
                    "calendar", "/calendars/" + quote(cal["id"], safe="") + "/events", params
                )
                rows = valid_items(data, "Calendar")
            except GoogleConnectionError:
                continue
            for item in rows:
                extra.append(
                    with_days(
                        {
                            **{
                                key: item.get(key)
                                for key in (
                                    "id",
                                    "etag",
                                    "summary",
                                    "status",
                                    "start",
                                    "end",
                                    "updated",
                                    "location",
                                )
                            },
                            "calendar": str(cal.get("summary") or "Other calendar")[:80],
                            "read_only": True,
                        }
                    )
                )
        return extra

    async def fetch_tasks(self):
        items = []
        page = None
        for _ in range(5):
            params = {"maxResults": 100, "showCompleted": "false"}
            if page:
                params["pageToken"] = page
            data = await self.get("tasks", "/lists/@default/tasks", params)
            items.extend(
                {
                    key: item.get(key)
                    for key in ("id", "etag", "title", "notes", "status", "due", "updated")
                }
                for item in valid_items(data, "Tasks")
            )
            page = data.get("nextPageToken")
            if not page:
                return items
        raise GoogleConnectionError("The default task list exceeds the supported 500-item window.")

    async def fetch_youtube(self):
        """New videos from the channels the person subscribes to, and what they liked.

        The API is used only for the two lists that need their account (a few quota units a
        refresh); each channel's new uploads come from YouTube's public per-channel feed, which
        uses no quota, so frequent refreshes stay well inside the free daily limit."""
        data = await self.get(
            "youtube",
            "/subscriptions",
            {
                "part": "snippet",
                "mine": "true",
                "maxResults": 50,
                "order": "relevance",
                "fields": "items/snippet(title,resourceId/channelId)",
            },
        )
        channels = []
        for item in data.get("items", []):
            snippet = item.get("snippet", {})
            channel = snippet.get("resourceId", {}).get("channelId")
            if isinstance(channel, str) and YT_CHANNEL.fullmatch(channel):
                channels.append((channel, str(snippet.get("title", ""))[:200]))
        liked = await self.get(
            "youtube",
            "/videos",
            {
                "part": "snippet",
                "myRating": "like",
                "maxResults": 10,
                "fields": "items(id,snippet(title,channelTitle,publishedAt))",
            },
        )
        since = (datetime.now(UTC) - timedelta(days=7)).isoformat()
        semaphore = asyncio.Semaphore(6)
        async with httpx.AsyncClient(
            transport=self.transport, timeout=15, follow_redirects=False
        ) as client:

            async def uploads(channel, title):
                async with semaphore:
                    try:
                        response = await client.get(YT_FEED, params={"channel_id": channel})
                    except httpx.HTTPError:
                        return []  # one channel's feed down: the rest still come through
                if response.status_code != 200 or len(response.content) > 2_000_000:
                    return []
                return [v for v in parse_feed(response.text, title) if v["published"] >= since][:3]

            found = await asyncio.gather(*(uploads(c, t) for c, t in channels))
        videos = sorted(
            (v for vs in found for v in vs), key=lambda v: v["published"], reverse=True
        )[:30]
        for item in liked.get("items", []):
            snippet = item.get("snippet", {})
            vid = item.get("id")
            if isinstance(vid, str) and YT_VIDEO.fullmatch(vid):
                videos.append(
                    {
                        "id": "liked:" + vid,
                        "kind": "liked",
                        "title": str(snippet.get("title", ""))[:300],
                        "channel": str(snippet.get("channelTitle", ""))[:200],
                        "published": str(snippet.get("publishedAt", ""))[:40],
                        "url": "https://www.youtube.com/watch?v=" + vid,
                    }
                )
        return videos

    async def calendar_between(self, start: datetime, end: datetime) -> list[dict]:
        """The person's events between two times, read now: for a meeting asked further ahead
        than a calendar read covers. Nothing is cached or remembered from it."""
        await self.auth.access_token("calendar")
        with self.factory() as session:
            if not session.get(GoogleAccount, "google"):
                raise GoogleConnectionError("Sign in to Google in Settings first")
        items = await self.fetch_calendar(start, end)
        return [
            {key: value[:2000] if isinstance(value, str) else value for key, value in item.items()}
            for item in items
        ]

    async def read(self, resource):
        if resource not in self.locks:
            raise GoogleConnectionError("Unsupported Google resource")
        # Authorization is checked even for cached reads.
        await self.auth.access_token(resource)
        async with self.locks[resource]:
            await self.auth.access_token(resource)
            with self.factory() as session:
                account = session.get(GoogleAccount, "google")
                if not account:
                    raise GoogleConnectionError("Sign in to Google in Settings first")
                subject = account.subject
                epoch = str(account.connected_at)
                cursor = dict(account.cursors.get(resource, {}))
            cached = self.cache.get(resource)
            if cached and cached[0] > time.monotonic() and cached[1:3] == (subject, epoch):
                return cached[3]
            if resource in SERVICES:
                items, next_cursor, events = await fetch_metadata(self, resource), {}, []
            elif resource == "youtube":
                items = await self.fetch_youtube()
                # A video not seen before is an event (a future "new video" trigger can use it).
                seen = cursor.get("seen")
                events = [
                    (item["id"], item)
                    for item in items
                    if seen is not None and item["id"] not in set(seen)
                ]
                next_cursor = {"seen": [item["id"] for item in items]}
            elif resource == "gmail":
                items, next_cursor, events = await self.fetch_gmail(cursor)
            else:
                items = await (
                    self.fetch_calendar() if resource == "calendar" else self.fetch_tasks()
                )
                hashes = {
                    item["id"]: hashlib.sha256(
                        json.dumps(item, sort_keys=True).encode()
                    ).hexdigest()
                    for item in items
                }
                old = cursor.get("hashes")
                events = [
                    (item["id"] + ":" + hashes[item["id"]], item)
                    for item in items
                    if old is not None and old.get(item["id"]) != hashes[item["id"]]
                ]
                next_cursor = {"hashes": hashes}
            # Bound untrusted text before persistence and later model context construction.
            items = [
                {
                    key: value[:2000] if isinstance(value, str) else value
                    for key, value in item.items()
                }
                for item in items
            ]
            events = [
                (
                    key,
                    {
                        field: value[:2000] if isinstance(value, str) else value
                        for field, value in payload.items()
                    },
                )
                for key, payload in events
            ]
            next_cursor["snapshot"] = items
            with self.factory() as session:
                account = session.get(GoogleAccount, "google")
                if not account or account.subject != subject or str(account.connected_at) != epoch:
                    raise GoogleConnectionError("Google connection changed during this refresh")
                for key, payload in events:
                    dedup = hashlib.sha256(f"{subject}:{resource}:{key}".encode()).hexdigest()
                    try:
                        with session.begin_nested():
                            session.add(
                                ResourceEvent(
                                    account_subject=subject,
                                    resource=resource,
                                    deduplication_key=dedup,
                                    payload=payload,
                                )
                            )
                            session.flush()
                    except IntegrityError:
                        pass  # Another refresh already persisted this exact domain event.
                account.cursors = {**account.cursors, resource: next_cursor}
                session.commit()
            result = {
                "resource": resource,
                "items": items,
                "fetched_at": datetime.now(UTC).isoformat(),
                "coverage": {
                    "gmail": "Inbox headers only",
                    "calendar": "Primary calendar, next 30 days",
                    "tasks": "Default task list, incomplete tasks",
                    "drive": "Up to 100 recently modified files; metadata only, no contents",
                    "contacts": (
                        "Up to 100 saved contacts; names/emails/phones only, "
                        "not directory or other contacts"
                    ),
                    "youtube": (
                        "Newest videos (past 7 days) from up to 50 channels you subscribe to, "
                        "and your 10 latest liked videos; titles, channels, dates and links"
                    ),
                }[resource],
            }
            ttl = 900 if resource in {"contacts", "youtube"} else 300 if resource == "drive" else 60
            self.cache[resource] = (time.monotonic() + ttl, subject, epoch, result)
            return result


def with_days(item: dict) -> dict:
    """An all-day event says which days it covers, plainly. Google gives its end as the day
    AFTER it ends ("end: 2026-11-16" for a holiday on the 15th); a reviewer read that as the
    holiday running into the 16th and refused a free 10 am slot on it."""
    start = (item.get("start") or {}).get("date") if isinstance(item.get("start"), dict) else None
    end = (item.get("end") or {}).get("date") if isinstance(item.get("end"), dict) else None
    if not start:
        return item
    try:
        first = date.fromisoformat(start)
        last = date.fromisoformat(end) - timedelta(days=1) if end else first
    except ValueError:
        return item
    last = max(first, last)
    days = first.isoformat() if last == first else f"{first.isoformat()} to {last.isoformat()}"
    return {**item, "all_day_on": days + " (all day; free outside these dates)"}
