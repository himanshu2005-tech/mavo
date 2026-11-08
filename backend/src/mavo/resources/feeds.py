"""Curated public RSS/Atom metadata; no keys, arbitrary URLs or executable content."""

import asyncio
import ipaddress
import time
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select, text

from mavo.database import CrewInstance, WorkspaceSetting, utcnow
from mavo.google_auth import GoogleConnectionError
from mavo.resources.feed_catalog import FEEDS

MAX_FEED_BYTES = 2 * 1024 * 1024


class FeedError(GoogleConnectionError):
    pass


def safe_link(value):
    try:
        uri = urlsplit(value)
        if uri.scheme != "https" or not uri.hostname or uri.username or uri.password:
            return ""
        if uri.hostname == "localhost" or uri.hostname.endswith(
            (".local", ".internal", ".localhost")
        ):
            return ""
        try:
            if not ipaddress.ip_address(uri.hostname).is_global:
                return ""
        except ValueError:
            pass
        return value[:2048]
    except ValueError:
        return ""


def parse_feed(raw):
    if (
        not raw
        or len(raw) > MAX_FEED_BYTES
        or b"\x00" in raw
        or any(marker in raw.upper() for marker in (b"<!DOCTYPE", b"<!ENTITY"))
    ):
        raise FeedError("This feed exceeds safe XML limits or contains unsupported declarations")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise FeedError("The publisher did not return valid RSS or Atom XML") from exc
    items, seen = [], set()
    for count, node in enumerate(root.iter()):
        if count > 10000:
            raise FeedError("This feed exceeds Mavo's XML element limit")
        if node.tag.rsplit("}", 1)[-1] not in {"item", "entry"}:
            continue
        fields = {}
        for child in node:
            name = child.tag.rsplit("}", 1)[-1]
            if name in {"title", "guid", "id", "pubDate", "published", "updated"}:
                fields[name] = "".join(child.itertext()).strip()[:1000]
            elif name == "link" and child.attrib.get("rel", "alternate") == "alternate":
                fields["link"] = safe_link(child.attrib.get("href") or child.text or "")
        identifier = fields.get("guid") or fields.get("id") or fields.get("link")
        if not identifier or identifier in seen:
            continue
        seen.add(identifier)
        items.append(
            {
                "id": identifier[:1000],
                "title": fields.get("title", "Untitled"),
                "url": fields.get("link", ""),
                "published": fields.get("pubDate")
                or fields.get("published")
                or fields.get("updated", ""),
            }
        )
        if len(items) == 20:
            break
    if root.tag.rsplit("}", 1)[-1].lower() not in {"rss", "feed", "rdf"}:
        raise FeedError("The publisher did not return an RSS or Atom feed")
    return items


class PublicFeeds:
    def __init__(self, factory, transport=None):
        self.factory, self.transport = factory, transport
        self.cache = {}
        self.locks = {key: asyncio.Lock() for key in FEEDS}

    def selected(self):
        with self.factory() as session:
            row = session.get(WorkspaceSetting, "public-feeds")
            return [key for key in (row.value.get("sources", []) if row else []) if key in FEEDS]

    def status(self):
        sources = self.selected()
        return {
            "provider": "feeds",
            "name": "Public feeds",
            "configured": True,
            "connected": bool(sources),
            "email": None,
            "capabilities": [f"feeds:{key}:read" for key in sources],
            "available_capabilities": [f"feeds:{key}:read" for key in FEEDS],
            "sources": sources,
            "available_sources": [
                {"id": key, "name": value[0], "category": value[2]} for key, value in FEEDS.items()
            ],
        }

    def configure(self, sources, on_revoke=None):
        from mavo.crew_workers import stop_crew

        if any(key not in FEEDS for key in sources):
            raise FeedError("Choose one of the supported public feeds")
        with self.factory() as session:
            session.execute(text("BEGIN IMMEDIATE"))
            row = session.get(WorkspaceSetting, "public-feeds")
            previous = row.value.get("sources", []) if row else []
            removed = {f"feeds:{key}:read" for key in previous if key not in sources}
            value = {"sources": sorted(set(sources))}
            if row:
                row.value = value
            else:
                session.add(WorkspaceSetting(key="public-feeds", value=value))
            for crew in session.scalars(select(CrewInstance)):
                if removed.intersection(crew.permissions):
                    stop_crew(session, crew)
                    crew.permissions = [p for p in crew.permissions if p not in removed]
                    crew.checkpoint = None
                    crew.observation = {}
                    crew.last_runtime_error = (
                        "A public feed was disabled. Review its resource access."
                    )
                    if on_revoke:
                        on_revoke(crew.id)
            session.commit()
        self.cache.clear()

    async def read(self, source):
        if source not in self.selected():
            raise FeedError("Enable this public feed in Resources first")
        async with self.locks[source]:
            if source not in self.selected():
                raise FeedError("Public feed access changed during collection")
            cached = self.cache.get(source)
            if cached and cached[0] > time.monotonic():
                return cached[1]
            try:
                async with (
                    asyncio.timeout(15),
                    httpx.AsyncClient(
                        transport=self.transport, timeout=15, follow_redirects=False
                    ) as client,
                ):
                    async with client.stream(
                        "GET",
                        FEEDS[source][1],
                        headers={
                            "Accept": "application/rss+xml, application/atom+xml, application/xml"
                        },
                    ) as response:
                        response.raise_for_status()
                        if response.is_redirect:
                            raise FeedError(
                                "This feed redirected; Mavo did not follow an unapproved URL"
                            )
                        raw = bytearray()
                        async for chunk in response.aiter_bytes():
                            raw.extend(chunk)
                            if len(raw) > MAX_FEED_BYTES:
                                raise FeedError("This feed exceeds Mavo's 2 MB limit")
                items = parse_feed(bytes(raw))
            except (httpx.HTTPError, TimeoutError) as exc:
                raise FeedError(
                    "The public feed could not be read. No model result was fabricated."
                ) from exc
            if source not in self.selected():
                raise FeedError("Public feed access changed during collection")
            result = {
                "provider": "feeds",
                "resource": source,
                "items": items,
                "fetched_at": utcnow().isoformat(),
                "untrusted": True,
                "coverage": "Up to 20 item titles, links and dates; not full articles",
            }
            self.cache[source] = (time.monotonic() + 900, result)
            return result
