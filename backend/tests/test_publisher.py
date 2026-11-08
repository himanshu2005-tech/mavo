import json

import pytest

from mavo.config import Settings
from mavo.google_auth import GoogleConnectionError
from mavo.publisher import publisher_google_config
from tests.test_google import google_env as google_fixture

google_env = google_fixture


def test_publisher_identity_loads_from_bundle_and_environment_wins(tmp_path, monkeypatch):
    monkeypatch.setattr("sys._MEIPASS", str(tmp_path), raising=False)
    assert publisher_google_config() == {}
    (tmp_path / "publisher.json").write_text(
        json.dumps({"google_client_id": "publisher.apps.googleusercontent.com"}),
        encoding="utf-8-sig",
    )
    assert publisher_google_config()["publisher_managed"]
    assert publisher_google_config("override.apps.googleusercontent.com")["client_id"].startswith(
        "override."
    )
    with pytest.raises(ValueError, match="Desktop OAuth"):
        publisher_google_config("https://unexpected.example")
    assert not Settings().allow_google_developer_setup


@pytest.mark.asyncio
async def test_publisher_oauth_cannot_be_replaced_by_user_configuration(google_env):
    auth, _, _, _ = google_env
    auth.publisher_config = publisher_google_config("publisher.apps.googleusercontent.com")
    auth.allow_developer_setup = True
    status = auth.status()
    assert status["configured"] and status["publisher_managed"]
    assert not status["developer_setup_allowed"]
    assert status["client_id"] == "publisher.apps.googleusercontent.com"
    with pytest.raises(GoogleConnectionError, match="managed by the Mavo publisher"):
        await auth.configure("other.apps.googleusercontent.com", "replacement")


@pytest.mark.asyncio
async def test_normal_build_disallows_developer_configuration(google_env):
    auth, _, _, _ = google_env
    with pytest.raises(GoogleConnectionError, match="publisher"):
        await auth.configure("other.apps.googleusercontent.com", None)
