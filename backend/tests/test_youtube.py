"""YouTube through the Google sign-in: read only, two quota calls a refresh, and each channel's
new uploads from its public feed (no quota). Nothing is watched, liked or changed."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from mavo.google_auth import SCOPES
from mavo.google_permissions import BASE, CAPABILITIES, GOOGLE_SERVICES, granted_capabilities
from mavo.resource_pool import RESOURCES
from mavo.resources.google import GoogleResourcePool, parse_feed
from tests.test_google import complete
from tests.test_google import google_env as base_google_env

google_env = base_google_env

CHANNEL_A, CHANNEL_B, CHANNEL_DOWN = "UC" + "a" * 22, "UC" + "b" * 22, "UC" + "c" * 22


def feed(*videos):
    entries = "".join(
        f"<entry><yt:videoId>{vid}</yt:videoId><title>{title}</title><author><name>{name}</name></author>"
        f"<published>{when}</published></entry>"
        for vid, title, name, when in videos
    )
    return f'<?xml version="1.0"?><feed xmlns:yt="http://www.youtube.com/xml/schemas/2015">{entries}</feed>'


def test_youtube_is_one_more_read_only_google_service():
    assert "youtube" in GOOGLE_SERVICES and SCOPES["youtube"] == BASE + "youtube.readonly"
    assert CAPABILITIES["youtube:read"][2] == "youtube.readonly"
    assert granted_capabilities([BASE + "youtube.readonly"]) == ["youtube:read"]
    assert RESOURCES["youtube"][5] is False  # nothing to act on: read only


def test_a_feed_is_read_for_its_fields_only():
    text = feed(
        ("dQw4w9WgXcQ", "Ships &amp; Planes", "Chan", "2026-11-05T10:00:00+00:00"),
        ("<bad>", "x", "y", "z"),
    )
    assert parse_feed(text) == [
        {
            "id": "dQw4w9WgXcQ",
            "kind": "upload",
            "title": "Ships & Planes",
            "channel": "Chan",
            "published": "2026-11-05T10:00:00+00:00",
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        }
    ]


@pytest.mark.asyncio
async def test_new_videos_from_subscriptions_and_likes_for_two_quota_calls(google_env):
    auth, _, _, _ = google_env
    await complete(auth, ["youtube"])
    now = datetime.now(UTC)
    fresh, older, old = (
        (now - timedelta(hours=3)).isoformat(),
        (now - timedelta(days=2)).isoformat(),
        (now - timedelta(days=30)).isoformat(),
    )
    api_calls, feed_calls = [], []

    def handler(request):
        if request.url.host == "www.youtube.com":
            feed_calls.append(request.url.params["channel_id"])
            channel = request.url.params["channel_id"]
            if channel == CHANNEL_DOWN:
                return httpx.Response(500)
            if channel == CHANNEL_A:
                return httpx.Response(
                    200,
                    text=feed(
                        ("aaaaaaaaaa1", "New from A", "Channel A", fresh),
                        ("aaaaaaaaaa2", "Old from A", "Channel A", old),
                    ),
                )
            return httpx.Response(200, text=feed(("bbbbbbbbbb1", "From B", "Channel B", older)))
        api_calls.append(request.url.path)
        assert request.method == "GET"
        if request.url.path == "/youtube/v3/subscriptions":
            assert request.url.params["mine"] == "true"
            return httpx.Response(
                200,
                json={
                    "items": [
                        {"snippet": {"title": t, "resourceId": {"channelId": c}}}
                        for c, t in [
                            (CHANNEL_A, "Channel A"),
                            (CHANNEL_B, "Channel B"),
                            (CHANNEL_DOWN, "Down"),
                            ("not-a-channel", "Bad"),
                        ]
                    ]
                },
            )
        assert request.url.path == "/youtube/v3/videos" and request.url.params["myRating"] == "like"
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "llllllllll1",
                        "snippet": {"title": "Liked one", "channelTitle": "C", "publishedAt": old},
                    }
                ]
            },
        )

    pool = GoogleResourcePool(auth)
    pool.transport = httpx.MockTransport(handler)
    result = await pool.read("youtube")
    titles = [(v["kind"], v["title"]) for v in result["items"]]
    assert titles == [
        ("upload", "New from A"),
        ("upload", "From B"),
        ("liked", "Liked one"),
    ]  # newest first; a month old is left out
    assert sorted(api_calls) == [
        "/youtube/v3/subscriptions",
        "/youtube/v3/videos",
    ]  # two quota calls, whatever the channel count
    assert sorted(feed_calls) == sorted(
        [CHANNEL_A, CHANNEL_B, CHANNEL_DOWN]
    )  # a bad id is never fetched; a down feed is skipped
    assert await pool.read("youtube") == result and len(api_calls) == 2  # cached
    await auth.close()
