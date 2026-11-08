from __future__ import annotations

import secrets
from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MAVO_",
        env_file=(
            Path(__file__).resolve().parents[3] / ".env",
            Path(__file__).resolve().parents[2] / ".env",
            ".env",
        ),
        extra="ignore",
        populate_by_name=True,
    )

    api_token: str = Field(default_factory=lambda: secrets.token_urlsafe(32), min_length=24)
    host: str = "127.0.0.1"
    port: int = Field(default=8321, ge=1, le=65535)
    data_dir: Path = Path.home() / "AppData" / "Local" / "Mavo"
    ollama_url: str = "http://127.0.0.1:11434"
    # How much a local model may read at once (tokens). 8k fits a 3-4B model
    # on a 4 GB graphics card;
    # raise it with MAVO_OLLAMA_CONTEXT on a bigger one.
    ollama_context: int = Field(default=8192, ge=2048, le=131072)
    log_level: str = "INFO"
    workers_enabled: bool = True
    automation_dir: Path | None = None
    require_login: bool = True
    core_gateway_url: str | None = None
    github_client_id: str | None = Field(
        default=None, validation_alias=AliasChoices("MAVO_GITHUB_CLIENT_ID", "GITHUB_CLIENT_ID")
    )
    github_app_slug: str | None = Field(
        default=None, validation_alias=AliasChoices("MAVO_GITHUB_APP_SLUG", "GITHUB_APP_SLUG")
    )
    google_client_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("MAVO_GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_ID"),
    )
    google_client_secret: SecretStr | None = Field(
        default=None,
        repr=False,
        exclude=True,
        validation_alias=AliasChoices("MAVO_GOOGLE_CLIENT_SECRET", "GOOGLE_CLIENT_SECRET"),
    )
    allow_google_developer_setup: bool = False
    firebase_google_client_id: str | None = None
    firebase_google_client_secret: SecretStr | None = Field(default=None, repr=False, exclude=True)
    allowed_origins: tuple[str, ...] = (
        "http://localhost:1420",
        "http://127.0.0.1:1420",
        "http://tauri.localhost",
        "https://tauri.localhost",
    )

    @field_validator("host")
    @classmethod
    def loopback_only(cls, value: str) -> str:
        if value not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Mavo backend must bind to a loopback interface")
        return value

    @field_validator("ollama_url")
    @classmethod
    def local_ollama_only(cls, value: str) -> str:
        from urllib.parse import urlparse

        parsed = urlparse(value)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Ollama URL must use HTTP on a loopback host")
        return value.rstrip("/")

    @property
    def database_url(self) -> str:
        return f"sqlite:///{(self.data_dir / 'mavo.db').as_posix()}"
