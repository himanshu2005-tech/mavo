"""Verify saved Google grants with minimal API probes; never print credentials or user content."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from mavo.config import Settings
from mavo.credentials import DpapiCredentialStore
from mavo.database import build_session_factory
from mavo.google_auth import GoogleAuth
from mavo.publisher import publisher_google_config
from mavo.resources.google import GoogleResourcePool

ROOT = Path(__file__).resolve().parents[1]


async def check():
    settings = Settings()
    data = Path(os.environ["LOCALAPPDATA"]) / "dev.mavo.desktop"
    database = data / "mavo.db"
    if not database.exists():
        raise RuntimeError("Launch Mavo and connect your Google account first.")
    factory = build_session_factory(f"sqlite:///{database.as_posix()}")
    auth = GoogleAuth(factory, DpapiCredentialStore(data), publisher_config=publisher_google_config(
        settings.google_client_id, settings.google_client_secret,
    ))
    try:
        return await GoogleResourcePool(auth).check_connections()
    finally:
        await auth.close()
        factory.kw["bind"].dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-all", action="store_true")
    args = parser.parse_args()
    result = asyncio.run(check())
    (ROOT / ".build").mkdir(exist_ok=True)
    (ROOT / ".build" / "google-connection-check.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8",
    )
    print(json.dumps(result, indent=2))
    return int(args.require_all and any(s["status"] != "connected" for s in result["services"]))


if __name__ == "__main__":
    raise SystemExit(main())
