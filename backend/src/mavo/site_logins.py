# ruff: noqa: E501
"""Saved website logins. The user types a password into Mavo itself; Mavo stores it with Windows
DPAPI and fills the sign-in form with ordinary code when its browser reaches that exact site.

Nothing here is ever sent to a model, returned by an API, or readable by a Crew: the API lists
sites and a masked username only, and `lookup` is called by the browser tool, not by any prompt."""

import hashlib
import ipaddress
import json
import re
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from mavo.database import WorkspaceSetting, utcnow

SETTING = "site-logins"
HOST = re.compile(
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+"
)


def normalize_host(value):
    text = value.strip().lower()
    host = (urlsplit(text if "//" in text else "//" + text).hostname or "").removeprefix("www.")
    if not HOST.fullmatch(host) or host.endswith((".local", ".localhost", ".internal")):
        raise ValueError("Enter a website such as portal.example.com")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return host
    raise ValueError("Use a website name, not an IP address")


def hint(username):
    return username[:2] + "•" * min(6, max(1, len(username) - 2)) if len(username) > 2 else "••"


class SiteLogins:
    def __init__(self, factory, credentials):
        self.factory, self.credentials = factory, credentials

    @staticmethod
    def key(host):
        return "login-" + hashlib.sha256(host.encode()).hexdigest()[:24]

    def listing(self):
        with self.factory() as session:
            row = session.get(WorkspaceSetting, SETTING)
            return sorted(
                ({"host": host, **meta} for host, meta in (row.value if row else {}).items()),
                key=lambda item: item["host"],
            )

    def save(self, site, username, password):
        host = normalize_host(site)
        self.credentials.set(self.key(host), json.dumps({"u": username, "p": password}))
        with self.factory() as session:
            row = session.get(WorkspaceSetting, SETTING)
            value = {
                **(row.value if row else {}),
                host: {"username_hint": hint(username), "saved_at": utcnow().isoformat()},
            }
            if row:
                row.value = value
            else:
                session.add(WorkspaceSetting(key=SETTING, value=value))
            session.commit()
        return host

    def delete(self, host):
        host = normalize_host(host)
        self.credentials.delete(self.key(host))
        with self.factory() as session:
            row = session.get(WorkspaceSetting, SETTING)
            if row and host in row.value:
                row.value = {k: v for k, v in row.value.items() if k != host}
                session.commit()

    def lookup(self, hostname):
        """(username, password) for this exact site or one of its subdomains. Browser tool only."""
        hostname = (hostname or "").lower()
        with self.factory() as session:
            row = session.get(WorkspaceSetting, SETTING)
            hosts = list(row.value) if row else []
        match = next((h for h in hosts if hostname == h or hostname.endswith("." + h)), None)
        if not match:
            return None
        raw = self.credentials.get(self.key(match))
        if not raw:
            return None
        data = json.loads(raw)
        return data["u"], data["p"]


class LoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    site: str = Field(min_length=3, max_length=300)
    username: str = Field(min_length=1, max_length=300)
    password: SecretStr = Field(min_length=1, max_length=1000)


def build_logins_api(logins):
    router = APIRouter()

    @router.get("/browser/logins")
    def listing():
        return logins.listing()

    @router.put("/browser/logins")
    def save(payload: LoginIn):
        try:
            host = logins.save(payload.site, payload.username, payload.password.get_secret_value())
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return next(item for item in logins.listing() if item["host"] == host)

    @router.delete("/browser/logins/{host}", status_code=204)
    def remove(host: str):
        try:
            logins.delete(host)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    return router
