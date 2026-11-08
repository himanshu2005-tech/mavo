"""Keep "signed in for this session" cookies when the sign-in window closes.

A browser throws away session cookies (the ones with no expiry date) when its window is closed the
normal way, and many portals sign you in with exactly those. So while the person is signing in,
Mavo copies each session cookie into an ordinary one with an expiry, through the browser's own debug
channel. That channel is the browser's, not a page's: nothing is attached to the sign-in page, so
the sites (Google included) see an ordinary browser. The server still decides how long a sign-in
lasts; this only stops closing the window from ending it.

The debug channel listens on this computer only, on a random port, only while the window is open."""

import asyncio
import json
import socket
import time
import urllib.request

import websockets

KEEP_DAYS = 30


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def browser_socket(port: int) -> str:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2) as response:  # noqa: S310 - loopback only
        return json.loads(response.read())["webSocketDebuggerUrl"]


async def _call(socket_, number: int, method: str, params: dict | None = None):
    await socket_.send(json.dumps({"id": number, "method": method, "params": params or {}}))
    while True:
        message = json.loads(await asyncio.wait_for(socket_.recv(), timeout=5))
        if message.get("id") == number:
            if "error" in message:
                raise RuntimeError(message["error"].get("message", "browser refused"))
            return message.get("result", {})


def lasting(cookie: dict, now: float, days: int = KEEP_DAYS) -> dict:
    """A session cookie, written again as one that expires in `days` days."""
    kept = {
        "name": cookie["name"],
        "value": cookie["value"],
        "domain": cookie["domain"],
        "path": cookie.get("path", "/"),
        "secure": bool(cookie.get("secure")),
        "httpOnly": bool(cookie.get("httpOnly")),
        "expires": now + days * 86400,
    }
    if cookie.get("sameSite") in {"Strict", "Lax", "None"}:
        kept["sameSite"] = cookie["sameSite"]
    return kept


async def keep_session_cookies(port: int, close: bool = False) -> int:
    """Make the browser's session cookies last. Returns how many were changed. With close=True the
    browser is then closed through the same channel, which also lets it save them."""
    async with websockets.connect(browser_socket(port), max_size=None, open_timeout=3) as socket_:
        found = (await _call(socket_, 1, "Storage.getCookies")).get("cookies", [])
        session = [c for c in found if c.get("session")]
        if session:
            now = time.time()
            await _call(
                socket_, 2, "Storage.setCookies", {"cookies": [lasting(c, now) for c in session]}
            )
        if close:
            try:
                await _call(socket_, 3, "Browser.close")
            except Exception:  # noqa: S110 - the browser may drop the connection as it exits
                pass
        return len(session)
