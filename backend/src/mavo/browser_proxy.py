"""Private browser CONNECT broker: resolve once, connect to a public IP, preserve end-to-end TLS."""

import asyncio
import base64
import hmac
import ipaddress
import secrets
import socket
from urllib.parse import urlsplit

SECOND_LEVEL = {"co", "com", "ac", "org", "gov", "net", "edu"}


def site_of(host):
    """Approximate registrable domain: portal.example.com and api.example.com share example.com."""
    labels = host.lower().split(".")
    keep = 3 if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in SECOND_LEVEL else 2
    return ".".join(labels[-keep:])


def same_site(host, domains):
    """Subresources (scripts, APIs, images) may come from the allowed site's own subdomains.
    Navigations stay exact: a Crew can only *go* to the pages you listed."""
    return any(host == d or site_of(host) == site_of(d) for d in domains)


class PublicBrowserProxy:
    def __init__(self, domains, guard=None):
        self.domains, self.guard = domains, guard
        self.password = secrets.token_urlsafe(32)
        self.auth = "Basic " + base64.b64encode(("mavo:" + self.password).encode()).decode()
        self.server = None
        self.tasks = set()
        self.limit = asyncio.Semaphore(16)

    async def start(self):
        self.server = await asyncio.start_server(
            self.handle, "127.0.0.1", 0, limit=16384, backlog=16
        )
        port = self.server.sockets[0].getsockname()[1]
        return {"server": f"http://127.0.0.1:{port}", "username": "mavo", "password": self.password}

    async def close(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self.server = None
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*list(self.tasks), return_exceptions=True)

    async def destination(self, authority):
        parsed = urlsplit("//" + authority)
        if (
            not parsed.hostname
            or len(parsed.hostname) > 253
            or parsed.port != 443
            or parsed.username
            or parsed.password
            or parsed.path
        ):
            raise ValueError("HTTPS sites only")
        host = parsed.hostname.lower()
        if self.domains is not None and not same_site(host, self.domains):
            raise ValueError("Site outside access settings")
        if host in {"localhost", "localhost.localdomain"} or host.endswith(
            (".local", ".internal", ".localhost")
        ):
            raise ValueError("Private network")
        addresses = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise ValueError("Private network")
        address = next((a[4][0] for a in addresses if a[0] == socket.AF_INET), addresses[0][4][0])
        return address, 443

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        remote = None
        acquired = False
        established = False
        try:
            async with asyncio.timeout(10):
                header = (await reader.readuntil(b"\r\n\r\n")).decode("latin-1")
                lines = header.split("\r\n")
                parts = lines[0].split()
                headers = {
                    k.strip().lower(): v.strip()
                    for line in lines[1:]
                    if ":" in line
                    for k, v in [line.split(":", 1)]
                }
                if not hmac.compare_digest(headers.get("proxy-authorization", ""), self.auth):
                    writer.write(
                        b"HTTP/1.1 407 Proxy Authentication Required\r\n"
                        b'Proxy-Authenticate: Basic realm="Mavo"\r\n'
                        b"Content-Length: 0\r\nConnection: close\r\n\r\n"
                    )
                    await writer.drain()
                    return
                if len(parts) != 3 or parts[0] != "CONNECT":
                    raise ValueError("CONNECT required")
                if self.guard:
                    self.guard()
                address, port = await self.destination(parts[1])
                await self.limit.acquire()
                acquired = True
                upstream, remote = await asyncio.open_connection(address, port)
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
            established = True

            async def relay(source, target):
                total = 0
                while True:
                    async with asyncio.timeout(60):
                        data = await source.read(65536)
                    if not data:
                        return
                    if self.guard:
                        self.guard()
                    total += len(data)
                    if total > 32 * 1024 * 1024:
                        raise ValueError("Browser transfer limit")
                    target.write(data)
                    await target.drain()

            flows = [
                asyncio.create_task(relay(reader, remote)),
                asyncio.create_task(relay(upstream, writer)),
            ]
            try:
                await asyncio.wait(flows, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for flow in flows:
                    flow.cancel()
                await asyncio.gather(*flows, return_exceptions=True)
        except (
            ValueError,
            RuntimeError,
            OSError,
            TimeoutError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
        ):
            if not established and not writer.is_closing():
                writer.write(
                    b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                )
        finally:
            if remote:
                remote.close()
            if acquired:
                self.limit.release()
            writer.close()
            self.tasks.discard(task)
