"""Provider failures are errors, never invented empty resources. No real credentials/calls."""

import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from mavo.google_auth import GoogleConnectionError
from mavo.resources.google import GoogleResourcePool, valid_items
from mavo.resources.google_extended import fetch_metadata
from mavo.resources.mail_text import message_text
from mavo.resources.weather import OpenMeteoWeatherAdapter, WeatherResourceError


def part(text, mime="text/plain", **extra):
    return {
        "mimeType": mime,
        "body": {"data": base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")},
        **extra,
    }


def test_html_only_mail_is_readable_without_remote_requests_or_active_content():
    text, kind = message_text(
        part(
            "<html><head><title>hidden</title></head><body><script>steal()</script>"
            "<style>secret</style><p>Collect relatives &amp; luggage</p><br>tomorrow 1PM"
            '<img src="https://tracker.example/pixel">'
            '<a href="javascript:bad()">JBS bus stand</a></body></html>',
            "text/html",
        )
    )
    assert kind == "html" and "Collect relatives & luggage" in text
    assert "tomorrow 1PM" in text and "JBS bus stand" in text
    assert not any(
        s in text for s in ["steal", "secret", "hidden", "tracker.example", "javascript:"]
    )


def test_multipart_prefers_plain_preserves_order_and_never_loads_attachments():
    text, kind = message_text(
        {
            "parts": [
                part("First"),
                part("HTML duplicate", "text/html"),
                part("Second"),
                part("PRIVATE ATTACHMENT", filename="file.txt"),
                {"mimeType": "text/plain", "body": {"attachmentId": "never-downloaded"}},
            ]
        }
    )
    assert kind == "plain" and text == "First\nSecond"


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {"parts": None},
        {"parts": [None]},
        {"mimeType": "text/plain", "body": None},
        {"mimeType": "text/plain", "body": {"data": "!!invalid!!"}},
        {"mimeType": "text/plain", "body": {"data": 42}},
    ],
)
def test_malformed_mime_is_a_named_error(payload):
    with pytest.raises(GoogleConnectionError):
        message_text(payload)


def test_mime_extraction_is_bounded_and_empty_is_explicit():
    text, kind = message_text(part("x" * 100000))
    assert len(text) == 24000 and kind == "plain"
    assert message_text({}) == ("", "none")


@pytest.mark.asyncio
async def test_message_body_declares_availability_and_requires_body_scope():
    pool = object.__new__(GoogleResourcePool)
    pool.get = AsyncMock(
        return_value={"id": "mail", "payload": part("<p>Meet at 1PM</p>", "text/html")}
    )
    result = await pool.message_body("mail")
    assert result["body_available"] and result["body_format"] == "html"
    assert "Meet at 1PM" in result["body"]
    assert pool.get.await_args.kwargs["capability"] == "body"


@pytest.mark.asyncio
async def test_wrong_message_body_is_not_accepted_as_the_requested_mail():
    pool = object.__new__(GoogleResourcePool)
    pool.get = AsyncMock(return_value={"id": "other", "payload": part("Another person's message")})
    with pytest.raises(GoogleConnectionError, match="different message"):
        await pool.message_body("requested")


@pytest.mark.parametrize(
    "payload,labels",
    [
        (None, []),
        ({"headers": None}, []),
        ({"headers": [None]}, []),
        ({"headers": [{"name": "Subject", "value": 5}]}, []),
        ({"headers": []}, None),
        ({"headers": []}, [None]),
    ],
)
@pytest.mark.asyncio
async def test_malformed_mail_headers_fail_without_consuming_history(payload, labels):
    pool = object.__new__(GoogleResourcePool)
    pool.get = AsyncMock(return_value={"id": "mail", "payload": payload, "labelIds": labels})
    with pytest.raises(GoogleConnectionError, match="invalid inbox headers"):
        await pool.mail_metadata("mail")


@pytest.mark.parametrize("rows", [None, "not a list", [None], [{}], [{"id": 5}], [{"id": ""}]])
def test_google_item_shape_errors_are_not_empty_resources(rows):
    with pytest.raises(GoogleConnectionError):
        valid_items({"items": rows}, "Tasks")


@pytest.mark.parametrize("service", ["calendar", "tasks"])
@pytest.mark.asyncio
async def test_calendar_tasks_malformed_items_and_pagination_limits(service):
    pool = object.__new__(GoogleResourcePool)
    pool.get = AsyncMock(return_value={"items": [None]})
    method = pool.fetch_calendar if service == "calendar" else pool.fetch_tasks
    with pytest.raises(GoogleConnectionError, match="invalid"):
        await method()
    pool.get = AsyncMock(return_value={"items": [{"id": "item"}], "nextPageToken": "again"})
    with pytest.raises(GoogleConnectionError, match="500"):
        await method()
    assert pool.get.await_count == 5


@pytest.mark.parametrize(
    "service,rows",
    [
        ("drive", None),
        ("drive", [None]),
        ("drive", [{}]),
        ("contacts", None),
        ("contacts", [None]),
        ("contacts", [{}]),
        ("contacts", [{"resourceName": "people/one", "names": None}]),
        ("contacts", [{"resourceName": "people/one", "emailAddresses": [None]}]),
    ],
)
@pytest.mark.asyncio
async def test_drive_contacts_invalid_data_never_becomes_a_successful_empty_list(service, rows):
    key = "files" if service == "drive" else "connections"
    pool = SimpleNamespace(get=AsyncMock(return_value={key: rows}))
    with pytest.raises(GoogleConnectionError):
        await fetch_metadata(pool, service)


@pytest.mark.asyncio
async def test_contacts_normalization_is_read_only_and_keeps_documented_fields():
    pool = SimpleNamespace(
        get=AsyncMock(
            return_value={
                "connections": [
                    {
                        "resourceName": "people/one",
                        "names": [{"displayName": "Contact"}],
                        "emailAddresses": [{"value": "contact@example.com"}],
                        "phoneNumbers": [{"value": "+910000000000"}],
                        "biographies": [{"value": "Not requested"}],
                    }
                ]
            }
        )
    )
    result = await fetch_metadata(pool, "contacts")
    assert result == [
        {
            "id": "people/one",
            "name": "Contact",
            "emails": ["contact@example.com"],
            "phones": ["+910000000000"],
        }
    ]
    assert pool.get.await_args.args[1] == "/people/me/connections"


@pytest.mark.parametrize(
    "payload", [None, [], {"results": None}, {"results": [None]}, {"results": [{}]}]
)
@pytest.mark.asyncio
async def test_invalid_weather_geocoding_does_not_crash_or_guess_a_place(payload):
    adapter = OpenMeteoWeatherAdapter(
        httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
    )
    with pytest.raises(WeatherResourceError):
        await adapter.search("Hyderabad")
    assert not adapter.cache


@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
@pytest.mark.asyncio
async def test_weather_provider_outages_are_not_fabricated_forecasts(status):
    adapter = OpenMeteoWeatherAdapter(httpx.MockTransport(lambda r: httpx.Response(status)))
    with pytest.raises(WeatherResourceError):
        await adapter.read("Chennai, India")
    assert not adapter.cache
