import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest

from mavo.database import CrewInstance
from mavo.resources.feeds import FEEDS, FeedError, PublicFeeds, parse_feed, safe_link
from tests.test_connectors import make_crew

RSS = b"""<rss version="2.0"><channel><title>Fixture</title><item><guid>1</guid>
<title>A new paper</title><link>https://example.org/paper</link><pubDate>Today</pubDate>
<description>PRIVATE_ARTICLE_BODY</description></item><item><guid>1</guid>
<title>Duplicate</title></item></channel></rss>"""


def test_rss_and_atom_are_metadata_only_deduplicated_and_bounded():
    rows = parse_feed(RSS)
    assert len(rows) == 1 and rows[0]["id"] == "1"
    assert "PRIVATE_ARTICLE_BODY" not in json.dumps(rows)
    atom = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>2</id><title>Atom</title>
    <link href="https://example.org/2"/><updated>Now</updated><content>PRIVATE</content>
    </entry></feed>"""
    assert parse_feed(atom)[0]["url"] == "https://example.org/2"
    assert "PRIVATE" not in json.dumps(parse_feed(atom))
    items = "".join(f"<item><guid>{i}</guid><title>Title</title></item>" for i in range(40))
    assert len(parse_feed(f"<rss><channel>{items}</channel></rss>".encode())) == 20


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"<html>not a feed</html>",
        b"<rss>",
        b"x" * (2 * 1024 * 1024 + 1),
        b'<!DOCTYPE rss [<!ENTITY e SYSTEM "file:///secret">]><rss>&e;</rss>',
        "<rss/>".encode("utf-16"),
    ],
    ids=["empty", "html", "truncated", "oversized", "entity", "utf16"],
)
def test_unsafe_or_invalid_feed_is_rejected(raw):
    with pytest.raises(FeedError):
        parse_feed(raw)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "file:///secret",
        "https://localhost/a",
        "https://127.0.0.1",
        "https://169.254.169.254",
        "https://[::1]",
        "https://foo.local",
        "https://user:pass@example.com",
    ],
)
def test_private_or_credential_links_are_not_presented(url):
    assert safe_link(url) == ""


def test_feed_connection_is_explicit_persistent_and_has_no_credential_field(client, auth):
    hub = client.app.state.crew_runtime.connectors
    assert not hub.status("feeds")["connected"]
    assert client.get("/api/v1/connectors/feeds/arxiv-ai", headers=auth).status_code == 409
    for payload in (
        {"sources": ["https://evil.example"]},
        {"sources": ["bbc-tech"], "token": "not-supported"},
        {"sources": ["bbc-tech"], "connector_capabilities": ["github:repo:read"]},
    ):
        assert (
            client.post("/api/v1/connectors/feeds/connect", headers=auth, json=payload).status_code
            == 422
        )
    response = client.post(
        "/api/v1/connectors/feeds/connect", headers=auth, json={"sources": ["bbc-tech"]}
    )
    assert response.status_code == 200
    assert response.json()["capabilities"] == ["feeds:bbc-tech:read"]
    assert PublicFeeds(hub.factory).selected() == ["bbc-tech"]
    assert client.post("/api/v1/connectors/feeds/connect", json={"sources": []}).status_code == 401


def test_fixed_url_caching_and_revocation(client, auth):
    feeds = client.app.state.crew_runtime.connectors.feeds
    requests = []

    def handler(request):
        requests.append(request)
        assert str(request.url) == FEEDS["bbc-tech"][1] and request.method == "GET"
        assert "authorization" not in request.headers
        return httpx.Response(200, content=RSS)

    feeds.transport = httpx.MockTransport(handler)
    feeds.configure(["bbc-tech"])
    first = client.portal.call(feeds.read, "bbc-tech")
    assert first["untrusted"] and first["items"][0]["title"] == "A new paper"
    assert client.portal.call(feeds.read, "bbc-tech") == first and len(requests) == 1
    feeds.configure([])
    with pytest.raises(FeedError):
        client.portal.call(feeds.read, "bbc-tech")
    assert len(requests) == 1


@pytest.mark.parametrize("status", [301, 403, 429, 503])
def test_feed_network_failures_are_not_fabricated_or_redirected(client, status):
    feeds = client.app.state.crew_runtime.connectors.feeds
    feeds.configure(["bbc-tech"])
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, headers={"Location": "http://127.0.0.1/private"})

    feeds.transport = httpx.MockTransport(handler)
    with pytest.raises(FeedError):
        client.portal.call(feeds.read, "bbc-tech")
    assert len(requests) == 1 and not feeds.cache


def test_worker_gets_only_its_feed_and_revocation_stops_it(client, auth):
    runtime = client.app.state.crew_runtime
    feeds = runtime.connectors.feeds
    feeds.configure(["bbc-tech", "arxiv-ai"])
    feeds.read = AsyncMock(
        return_value={"items": [{"title": "Authorized feed title"}], "untrusted": True}
    )
    crew = make_crew(client, auth, ["feeds:bbc-tech:read"])
    runtime.router.chat = AsyncMock(
        return_value=json.dumps(
            {"state": "wait_for_next_review", "summary": "Reviewed", "next_step": "Later"}
        )
    )
    client.portal.call(runtime.work, crew["id"])
    feeds.read.assert_awaited_once_with("bbc-tech")
    assert "Authorized feed title" in runtime.router.chat.await_args.args[2][0]["content"]
    feeds.configure(["arxiv-ai"])
    with runtime.factory() as session:
        saved = session.get(CrewInstance, crew["id"])
        assert not saved.is_running and "feeds:bbc-tech:read" not in saved.permissions
        assert saved.checkpoint is None and saved.observation == {}


def test_disabled_feed_worker_never_spends_model_credits(client, auth):
    runtime = client.app.state.crew_runtime
    crew = make_crew(client, auth, ["feeds:bbc-tech:read"])
    runtime.router.chat = AsyncMock()
    client.portal.call(runtime.work, crew["id"])
    runtime.router.chat.assert_not_awaited()
    with runtime.factory() as session:
        assert session.get(CrewInstance, crew["id"]).daily_calls_used == 0


def test_revoke_during_collection_discards_inflight_result(client):
    feeds = client.app.state.crew_runtime.connectors.feeds
    feeds.configure(["bbc-tech"])

    def handler(_):
        feeds.configure([])
        return httpx.Response(200, content=RSS)

    feeds.transport = httpx.MockTransport(handler)
    with pytest.raises(FeedError, match="access changed"):
        client.portal.call(feeds.read, "bbc-tech")
    assert not feeds.cache


def test_revocation_while_waiting_for_cache_lock_is_rechecked(client):
    feeds = client.app.state.crew_runtime.connectors.feeds
    feeds.configure(["bbc-tech"])

    async def scenario():
        async with feeds.locks["bbc-tech"]:
            pending = asyncio.create_task(feeds.read("bbc-tech"))
            await asyncio.sleep(0)
            feeds.configure([])
        with pytest.raises(FeedError, match="access changed"):
            await pending

    client.portal.call(scenario)
