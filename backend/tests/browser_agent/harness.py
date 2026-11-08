# ruff: noqa: E501, E731
"""A real Chromium page wired to Mavo's real browser tool, talking to fake https websites.

Everything the browser agent does in production (read, snapshot, click by ref, fill, select,
press, scroll, wait, navigate, sign-in detection) runs unchanged. Only the network is replaced,
so scenarios are deterministic and can model sites that misbehave: slow, flaky, logged-out,
lazy, paginated, with overlays and shadow DOM."""

import asyncio
import re
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import mavo.desktop_actions as tool
from mavo.desktop_actions import DesktopActions, installed_browsers


class Web:
    """host -> handler(method, path, query, body) returning (status, content_type, body[, delay])."""

    def __init__(self):
        self.sites, self.requests = {}, []

    def site(self, host, handler):
        self.sites[host] = handler

    async def handle(self, route):
        request = route.request
        url = urlsplit(request.url)
        self.requests.append((request.method, url.hostname, url.path))
        handler = self.sites.get(url.hostname)
        if not handler:
            return await route.abort()
        result = handler(request.method, url.path, parse_qs(url.query), request.post_data or "")
        if result is None:
            return await route.abort()
        status, kind, body, *delay = result
        if delay:
            await asyncio.sleep(delay[0])
        await route.fulfill(status=status, content_type=kind, body=body)


class Agent:
    """The scripted stand-in for a model: it calls the same tool, with the same arguments."""

    def __init__(self, desktop, rule):
        self.desktop, self.rule = desktop, rule
        self.calls, self.reads, self.started = 0, 0, time.monotonic()

    async def do(self, operation, **args):
        self.calls += 1
        return await self.desktop.browser(operation, args, self.rule, "agent")

    async def open(self, url):
        return await self.do("browser.open", url=url)

    async def read(self, **args):
        self.reads += 1
        return await self.do("browser.read", **args)

    @staticmethod
    def find(read, name, role=None):
        """The element a model would pick: by visible name (case-insensitive), optionally by role."""
        wanted = name.lower()
        for item in read["elements"]:
            if (role is None or item["role"] == role) and wanted in item["name"].lower():
                return item
        raise LookupError(f"No {role or 'element'} named {name!r} on {read['url']}")

    async def click(self, read, name, role=None):
        return await self.do("browser.click", ref=self.find(read, name, role)["ref"])

    async def fill(self, read, name, text):
        return await self.do("browser.fill", ref=self.find(read, name, "textbox")["ref"], text=text)

    async def select(self, read, name, option):
        return await self.do(
            "browser.select", ref=self.find(read, name, "select")["ref"], option=option
        )

    @property
    def seconds(self):
        return round(time.monotonic() - self.started, 1)


@asynccontextmanager
async def agent(web, *, domains=None, unrestricted=False, monkeypatch=None):
    from playwright.async_api import async_playwright

    installed = installed_browsers()
    if not installed:
        raise RuntimeError("No Chromium browser installed")

    async def no_dns(_host):
        return None

    original = tool.public_host
    tool.public_host = no_dns  # fake hosts do not exist in DNS
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                executable_path=next(iter(installed.values())), headless=True
            )
            context = await browser.new_context(viewport={"width": 1366, "height": 900})
            await context.route("https://**/*", web.handle)
            page = await context.new_page()
            page.set_default_timeout(15000)
            context.on(
                "page", lambda extra: asyncio.create_task(extra.close()) if extra != page else None
            )
            desktop = DesktopActions(Path(tempfile.mkdtemp()))
            desktop.context, desktop.page, desktop.browser_owner = context, page, "agent"
            desktop.watch_dialogs(page)
            rule = {
                "enabled": True,
                "mode": "unrestricted" if unrestricted else "restricted",
                "domains": domains or [],
                "revision": "t",
            }
            try:
                yield Agent(desktop, rule), desktop
            finally:
                await browser.close()
    finally:
        tool.public_host = original


def run(coro):
    return asyncio.run(coro)


def rows_of(read, table=0):
    return read["tables"][table]["rows"] if read["tables"] else []


NUMBER = re.compile(r"\d+")
