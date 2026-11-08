from __future__ import annotations

import os
from collections.abc import Iterator

# Mavo's own browser is visible so a person can watch it; a test run must never open windows on
# their screen (a test page linking to evil.test once did), so tests run it hidden.
os.environ["MAVO_BROWSER_HIDDEN"] = "1"

import httpx
import pytest
from fastapi.testclient import TestClient

from mavo.config import Settings
from mavo.credentials import MemoryCredentialStore
from mavo.main import create_app
from mavo.ollama import OllamaClient

TOKEN = "test-token-with-at-least-24-characters"


@pytest.fixture
def client(tmp_path) -> Iterator[TestClient]:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "geocoding-api.open-meteo.com":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "name": "New Delhi",
                            "admin1": "Delhi",
                            "country": "India",
                            "latitude": 28.61,
                            "longitude": 77.21,
                        }
                    ]
                },
            )
        if request.url.host == "api.open-meteo.com":
            return httpx.Response(
                200,
                json={
                    "timezone": "Asia/Kolkata",
                    "current": {
                        "temperature_2m": 28.0,
                        "apparent_temperature": 29.0,
                        "precipitation": 0.0,
                        "weather_code": 1,
                        "wind_speed_10m": 8.0,
                        "is_day": 1,
                    },
                    "daily": {
                        "time": ["2026-10-15"],
                        "weather_code": [1],
                        "temperature_2m_max": [31.0],
                        "temperature_2m_min": [20.0],
                        "precipitation_probability_max": [10],
                        "sunrise": ["2026-10-15T06:20"],
                        "sunset": ["2026-10-15T17:50"],
                    },
                },
            )
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen-test:latest", "size": 42}]})
        if request.url.path == "/api/chat":
            if b"qwen-fail:latest" in request.content:
                return httpx.Response(500, json={"error": "simulated failure"})
            return httpx.Response(
                200, json={"message": {"role": "assistant", "content": "A real model response."}}
            )
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "gpt-test"}]})
        if request.url.path == "/v1/responses":
            return httpx.Response(
                200,
                json={
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": "Cloud response."}],
                        }
                    ]
                },
            )
        return httpx.Response(404)

    settings = Settings(
        _env_file=None,
        google_client_id=None,
        google_client_secret=None,
        api_token=TOKEN,
        data_dir=tmp_path,
        ollama_url="http://127.0.0.1:11434",
        workers_enabled=False,
        require_login=False,
        allow_google_developer_setup=True,
    )
    ollama = OllamaClient(settings.ollama_url, transport=httpx.MockTransport(handler))
    credentials = MemoryCredentialStore()
    with TestClient(
        create_app(settings, ollama, credentials, httpx.MockTransport(handler))
    ) as test_client:
        yield test_client


@pytest.fixture
def auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}
