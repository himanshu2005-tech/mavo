import asyncio

import pytest

from mavo.browser_proxy import PublicBrowserProxy


@pytest.mark.asyncio
async def test_unrestricted_still_rejects_private_network_and_other_ports(monkeypatch):
    proxy = PublicBrowserProxy(None)

    async def private(*args, **kwargs):
        return [(2, 1, 6, "", ("192.168.0.1", 443))]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", private)
    for authority in (
        "example.com:443",
        "localhost:443",
        "example.com:80",
        "user:secret@example.com:443",
    ):
        with pytest.raises(ValueError):
            await proxy.destination(authority)


@pytest.mark.asyncio
async def test_restricted_host_and_resolved_address_are_enforced(monkeypatch):
    proxy = PublicBrowserProxy(["example.com"])
    calls = []

    async def public(host, *args, **kwargs):
        calls.append(host)
        return [(2, 1, 6, "", ("93.184.215.14", 443))]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", public)
    with pytest.raises(ValueError):
        await proxy.destination("example.com.evil.test:443")
    assert not calls
    assert await proxy.destination("example.com:443") == ("93.184.215.14", 443)
    assert calls == ["example.com"]


@pytest.mark.asyncio
async def test_local_proxy_requires_unguessable_credentials_and_closes():
    proxy = PublicBrowserProxy(None)
    settings = await proxy.start()
    port = proxy.server.sockets[0].getsockname()[1]
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"CONNECT example.com:443 HTTP/1.1\r\n\r\n")
        await writer.drain()
        response = await reader.read(4096)
        assert response.startswith(b"HTTP/1.1 407")
        assert settings["password"].encode() not in response
        writer.close()
    finally:
        await proxy.close()
    with pytest.raises(OSError):
        await asyncio.open_connection("127.0.0.1", port)
