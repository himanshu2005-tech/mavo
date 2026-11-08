"""Public OAuth application identity supplied once by the release publisher."""

import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import SecretStr


def publisher_core_url(explicit=None):
    if not explicit:
        root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
        file = root / "publisher.json"
        explicit = (
            json.loads(file.read_text(encoding="utf-8-sig")).get("core_gateway_url")
            if file.exists()
            else None
        )
    if not explicit:
        return None
    parsed = urlsplit(explicit)
    if (
        parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or not parsed.hostname
        or (
            parsed.scheme != "https"
            and not (
                parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
            )
        )
    ):
        raise ValueError("Core gateway must use HTTPS or a development loopback address")
    return explicit.rstrip("/")


def publisher_google_config(
    client_id: str | None = None,
    client_secret: SecretStr | None = None,
    gateway_url: str | None = None,
) -> dict:
    if not client_id:
        root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
        path = root / "publisher.json"
        if path.exists():
            client_id = json.loads(path.read_text(encoding="utf-8-sig")).get("google_client_id")
    if not client_id:
        return {}
    if not re.fullmatch(r"[A-Za-z0-9_-]+\.apps\.googleusercontent\.com", client_id):
        raise ValueError("Publisher Google client ID must be a Google Desktop OAuth client ID")
    return {
        "client_id": client_id,
        "client_secret": client_secret.get_secret_value() if client_secret else "",
        "publisher_managed": True,
        "oauth_gateway_url": publisher_core_url(gateway_url),
    }


def publisher_github_config(client_id=None, app_slug=None):
    if not client_id and not app_slug:
        root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
        file = root / "publisher.json"
        value = json.loads(file.read_text(encoding="utf-8-sig")) if file.exists() else {}
        config = value.get("github", {})
        client_id, app_slug = config.get("client_id"), config.get("app_slug")
    if not client_id or not app_slug:
        return {}
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", client_id) or not re.fullmatch(
        r"[a-z0-9][a-z0-9-]{0,99}", app_slug
    ):
        raise ValueError("Invalid publisher GitHub App client ID or slug")
    return {"client_id": client_id, "app_slug": app_slug}


def write_public_config(output: Path, release: bool = False) -> None:
    from mavo.config import Settings

    settings = Settings()
    from mavo.firebase_auth import load_firebase_config

    config = publisher_google_config(settings.google_client_id)
    firebase = load_firebase_config()
    if settings.firebase_google_client_id:
        firebase["googleDesktopClientId"] = settings.firebase_google_client_id
    public = {"google_client_id": config.get("client_id")}
    github = publisher_github_config(settings.github_client_id, settings.github_app_slug)
    if github:
        public["github"] = github
    if settings.core_gateway_url:
        public["core_gateway_url"] = publisher_core_url(settings.core_gateway_url)
    if firebase.get("apiKey") and firebase.get("projectId"):
        public["firebase"] = firebase
    if release:
        url = urlsplit(public.get("core_gateway_url") or "")
        if (
            url.scheme != "https"
            or url.hostname in {"localhost", "127.0.0.1", "::1"}
            or not public.get("firebase")
            or not public.get("google_client_id")
        ):
            raise ValueError(
                "Release requires a public HTTPS Core gateway, Firebase config, "
                "and publisher Google client ID. Loopback is development only."
            )
    output.write_text(json.dumps(public), encoding="utf-8")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Write public OAuth application identity only")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--release", action="store_true")
    args = parser.parse_args()
    write_public_config(args.output, args.release)
