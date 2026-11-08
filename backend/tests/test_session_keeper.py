"""Session-only sign-ins are kept when the sign-in window closes (browser channel faked)."""

import asyncio
import json
import threading
from http import HTTPStatus

import websockets

from mavo import session_keeper
from mavo.session_keeper import keep_session_cookies, lasting

COOKIES = [
    {
        "name": "sid",
        "value": "1",
        "domain": "portal.example.com",
        "path": "/",
        "secure": True,
        "httpOnly": True,
        "sameSite": "Lax",
        "session": True,
        "expires": -1,
    },
    {
        "name": "theme",
        "value": "dark",
        "domain": ".example.com",
        "path": "/",
        "secure": False,
        "httpOnly": False,
        "session": False,
        "expires": 1999999999,
    },
    {
        "name": "csrf",
        "value": "x",
        "domain": "portal.example.com",
        "path": "/app",
        "secure": True,
        "httpOnly": False,
        "sameSite": "Strict",
        "session": True,
        "expires": -1,
    },
]


class FakeBrowser:
    """Answers /json/version and the three debug commands, in its own thread like a real browser."""

    def __init__(self, cookies):
        self.cookies, self.received, self.port = cookies, [], session_keeper.free_port()
        self.ready, self.loop, self.server = threading.Event(), None, None

    def _run(self):
        async def handler(connection):
            async for raw in connection:
                message = json.loads(raw)
                self.received.append((message["method"], message.get("params")))
                result = (
                    {"cookies": self.cookies} if message["method"] == "Storage.getCookies" else {}
                )
                await connection.send(json.dumps({"id": message["id"], "result": result}))

        def answer(connection, request):
            if request.path == "/json/version":
                url = f"ws://127.0.0.1:{self.port}/devtools/browser/x"
                return connection.respond(HTTPStatus.OK, json.dumps({"webSocketDebuggerUrl": url}))
            return None

        async def main():
            self.server = await websockets.serve(
                handler, "127.0.0.1", self.port, process_request=answer
            )
            self.ready.set()
            await self.server.wait_closed()

        self.loop = asyncio.new_event_loop()
        self.loop.run_until_complete(main())

    def __enter__(self):
        threading.Thread(target=self._run, daemon=True).start()
        assert self.ready.wait(5)
        return self

    def __exit__(self, *args):
        self.loop.call_soon_threadsafe(self.server.close)

    def methods(self):
        return [method for method, _ in self.received]


def test_a_session_cookie_is_rewritten_with_an_expiry_and_nothing_else_changes():
    kept = lasting(COOKIES[0], now=1_000.0, days=30)
    assert kept == {
        "name": "sid",
        "value": "1",
        "domain": "portal.example.com",
        "path": "/",
        "secure": True,
        "httpOnly": True,
        "expires": 1_000.0 + 30 * 86400,
        "sameSite": "Lax",
    }
    odd = lasting(
        {"name": "a", "value": "b", "domain": "x.com", "sameSite": "Unspecified"}, now=0.0
    )
    assert "sameSite" not in odd and odd["path"] == "/"


def test_only_session_cookies_are_made_to_last_and_the_browser_can_be_closed_through_the_channel():
    with FakeBrowser(COOKIES) as browser:
        assert asyncio.run(keep_session_cookies(browser.port)) == 2
        written = next(p for m, p in browser.received if m == "Storage.setCookies")["cookies"]
        assert {c["name"] for c in written} == {"sid", "csrf"}
        assert all(c["expires"] > 1_700_000_000 for c in written)
        assert "Browser.close" not in browser.methods()
        asyncio.run(keep_session_cookies(browser.port, close=True))
        assert browser.methods()[-1] == "Browser.close"


def test_nothing_is_written_when_there_are_no_session_cookies():
    lasting_only = [dict(c, session=False) for c in COOKIES]
    with FakeBrowser(lasting_only) as browser:
        assert asyncio.run(keep_session_cookies(browser.port)) == 0
        assert "Storage.setCookies" not in browser.methods()


def test_the_debug_port_is_a_free_local_port_each_time():
    ports = {session_keeper.free_port() for _ in range(5)}
    assert all(1024 < p < 65536 for p in ports)
