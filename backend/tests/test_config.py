import json

import pytest

from mavo.config import Settings
from mavo.publisher import publisher_google_config, write_public_config


def clear_google_env(monkeypatch):
    for name in (
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
        "MAVO_GOOGLE_CLIENT_ID",
        "MAVO_GOOGLE_CLIENT_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)


def test_google_dotenv_aliases_keep_secret_private(tmp_path, monkeypatch):
    clear_google_env(monkeypatch)
    path = tmp_path / ".env"
    path.write_text(
        "GOOGLE_CLIENT_ID=local.apps.googleusercontent.com\n"
        'GOOGLE_CLIENT_SECRET="local-private-secret"\n'
    )
    settings = Settings(_env_file=path)
    assert settings.google_client_id == "local.apps.googleusercontent.com"
    assert settings.google_client_secret.get_secret_value() == "local-private-secret"
    assert "local-private-secret" not in repr(settings)
    assert "google_client_secret" not in settings.model_dump()
    config = publisher_google_config(settings.google_client_id, settings.google_client_secret)
    assert config["client_secret"] == "local-private-secret"


def test_prefixed_environment_wins_over_dotenv_alias(tmp_path, monkeypatch):
    clear_google_env(monkeypatch)
    path = tmp_path / ".env"
    path.write_text(
        "GOOGLE_CLIENT_ID=file.apps.googleusercontent.com\nGOOGLE_CLIENT_SECRET=file-secret\n"
    )
    monkeypatch.setenv("MAVO_GOOGLE_CLIENT_ID", "environment.apps.googleusercontent.com")
    monkeypatch.setenv("MAVO_GOOGLE_CLIENT_SECRET", "environment-secret")
    settings = Settings(_env_file=path)
    assert settings.google_client_id == "environment.apps.googleusercontent.com"
    assert settings.google_client_secret.get_secret_value() == "environment-secret"


def test_public_bundle_excludes_client_secret(tmp_path, monkeypatch):
    monkeypatch.setattr("mavo.firebase_auth.load_firebase_config", lambda: {})
    clear_google_env(monkeypatch)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "publisher.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "must-never-be-bundled")
    monkeypatch.setenv("MAVO_MICROSOFT_CLIENT_ID", "ignored-retired-application")
    monkeypatch.setenv("MAVO_CORE_GATEWAY_URL", "https://core.example.test")
    monkeypatch.setenv("MAVO_GITHUB_CLIENT_ID", "Iv1.bundle-fixture")
    monkeypatch.setenv("MAVO_GITHUB_APP_SLUG", "mavo-fixture")
    output = tmp_path / "publisher.json"
    write_public_config(output)
    assert json.loads(output.read_text()) == {
        "google_client_id": "publisher.apps.googleusercontent.com",
        "core_gateway_url": "https://core.example.test",
        "github": {"client_id": "Iv1.bundle-fixture", "app_slug": "mavo-fixture"},
    }
    assert "must-never-be-bundled" not in output.read_text()


def test_packaged_google_configuration_uses_public_gateway_not_local_secret(tmp_path, monkeypatch):
    monkeypatch.setattr("sys._MEIPASS", str(tmp_path), raising=False)
    (tmp_path / "publisher.json").write_text(
        json.dumps(
            {
                "google_client_id": "publisher.apps.googleusercontent.com",
                "core_gateway_url": "https://core.example.test",
            }
        )
    )
    configuration = publisher_google_config()
    assert configuration["oauth_gateway_url"] == "https://core.example.test"
    assert configuration["client_secret"] == ""
    with pytest.raises(ValueError, match="HTTPS"):
        publisher_google_config(gateway_url="http://arbitrary.example")


def test_release_bundle_rejects_local_coordinator_and_missing_identity(tmp_path, monkeypatch):
    clear_google_env(monkeypatch)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "publisher.apps.googleusercontent.com")
    monkeypatch.setattr(
        "mavo.firebase_auth.load_firebase_config",
        lambda: {"apiKey": "public-api-key", "projectId": "firebase-project"},
    )
    output = tmp_path / "publisher.json"
    monkeypatch.setenv("MAVO_CORE_GATEWAY_URL", "http://127.0.0.1:8741")
    with pytest.raises(ValueError, match="Release requires"):
        write_public_config(output, release=True)
    assert not output.exists()
    monkeypatch.setenv("MAVO_CORE_GATEWAY_URL", "https://core.example.test")
    write_public_config(output, release=True)
    assert json.loads(output.read_text())["core_gateway_url"] == "https://core.example.test"
    monkeypatch.setattr("mavo.firebase_auth.load_firebase_config", lambda: {})
    with pytest.raises(ValueError, match="Release requires"):
        write_public_config(output, release=True)
