"""Core lifecycle with fake processes/health. No ports, accounts or inference used."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from mavo.coordinator_client import CoordinatorClient
from mavo.dev_core import BACKOFF, DevCore, manages_url


class Child:
    def __init__(self):
        self.code = None

    def poll(self):
        return self.code


def manager():
    now = [100.0]
    children = []

    def spawn():
        child = Child()
        children.append(child)
        return child

    m = DevCore(
        "http://127.0.0.1:8741",
        True,
        probe=AsyncMock(return_value=False),
        spawn=spawn,
        occupied=lambda: False,
        stop=Mock(),
        clock=lambda: now[0],
    )
    return m, now, children


def test_management_requires_explicit_opt_in_and_the_fixed_development_loopback():
    assert manages_url("http://127.0.0.1:8741", True)
    for url in [
        None,
        "https://core.example",
        "http://127.0.0.1:8742",
        "http://user:secret@127.0.0.1:8741",
        "http://127.0.0.1:8741/path",
        "http://127.0.0.1:not-a-port",
    ]:
        assert not manages_url(url, True)
    assert not manages_url("http://127.0.0.1:8741", False)


@pytest.mark.asyncio
async def test_start_wait_ready_and_only_stop_the_owned_process():
    m, _, children = manager()
    await m.tick()
    assert len(children) == 1 and m.state == "starting"
    m.probe.return_value = True
    await m.tick()
    assert m.state == "ready"
    await m.close()
    m.stop_child.assert_called_once_with(children[0])


@pytest.mark.asyncio
async def test_existing_core_is_used_and_never_stopped_on_app_shutdown():
    m, _, children = manager()
    m.probe.return_value = True
    await m.tick()
    await m.close()
    assert children == [] and m.state == "ready"
    m.stop_child.assert_not_called()


@pytest.mark.asyncio
async def test_crash_recovery_has_backoff_without_any_model_request():
    m, now, children = manager()
    await m.tick()
    children[0].code = 1
    now[0] += 1
    await m.tick()
    assert len(children) == 1 and m.state == "recovering"
    now[0] += BACKOFF[0]
    await m.tick()
    assert len(children) == 2 and m.state == "starting"


@pytest.mark.asyncio
async def test_restart_ceiling_holds_until_explicit_retry():
    m, now, children = manager()
    for attempt in range(len(BACKOFF)):
        await m.tick()
        assert len(children) == attempt + 1
        children[-1].code = 1
        now[0] += 1
        await m.tick()
        now[0] += BACKOFF[attempt]
    await m.tick()
    assert len(children) == 4 and m.state == "failed"
    m.retry()
    await m.tick()
    assert len(children) == 5


@pytest.mark.asyncio
async def test_wrong_port_owner_is_never_killed_or_replaced():
    m, _, children = manager()
    m.occupied = lambda: True
    await m.tick()
    m.retry()
    await m.tick()
    await m.close()
    assert m.state == "port_conflict" and children == []
    m.stop_child.assert_not_called()


@pytest.mark.asyncio
async def test_hung_boot_stops_only_owned_child_before_retry():
    m, now, children = manager()
    await m.tick()
    now[0] += 26
    await m.tick()
    m.stop_child.assert_called_once_with(children[0])
    assert m.child is None and len(children) == 1


@pytest.mark.asyncio
async def test_one_slow_health_check_does_not_terminate_a_live_model_request():
    m, now, children = manager()
    await m.tick()
    m.probe.return_value = True
    now[0] += 40
    await m.tick()
    m.probe.return_value = False
    await m.tick()
    assert m.state == "recovering" and m.child is children[0]
    m.stop_child.assert_not_called()
    m.probe.return_value = True
    now[0] += 2
    await m.tick()
    assert m.state == "ready"
    m.stop_child.assert_not_called()


@pytest.mark.asyncio
async def test_disabled_manager_never_probes_or_launches():
    m, _, children = manager()
    m.enabled = False
    m.start()
    await m.tick()
    await m.close()
    m.probe.assert_not_called()
    assert m.task is None and children == []


@pytest.mark.asyncio
async def test_health_failure_is_not_cached_and_explicit_check_bypasses_success_cache():
    calls = []

    def service(request):
        calls.append(request.url.path)
        if len(calls) == 1:
            raise httpx.ConnectError("offline")
        return httpx.Response(
            200, json={"service": "mavo-core", "configured": True, "model": "fixture"}
        )

    client = CoordinatorClient(
        SimpleNamespace(core_gateway_url="http://127.0.0.1:8741"),
        None,
        httpx.MockTransport(service),
    )
    assert not (await client.status())["connected"]
    assert (await client.status())["connected"]
    assert (await client.status())["connected"] and len(calls) == 2
    client.invalidate_status()
    assert (await client.status())["connected"] and len(calls) == 3
    assert calls == ["/health"] * 3


@pytest.mark.asyncio
async def test_managed_warmup_and_failure_explain_the_actual_layer():
    def offline(request):
        raise httpx.ConnectError("private upstream error")

    client = CoordinatorClient(
        SimpleNamespace(core_gateway_url="http://127.0.0.1:8741"),
        None,
        httpx.MockTransport(offline),
    )
    client.lifecycle = SimpleNamespace(enabled=True, state="starting", retry=Mock())
    assert (await client.status())["state"] == "starting"
    client.lifecycle.state = "failed"
    assert "could not start" in (await client.status())["error"]
    client.invalidate_status()
    client.lifecycle.retry.assert_called_once()


def test_explicit_health_refresh_reaches_coordinator_without_inference(client, auth):
    runtime = client.app.state.crew_runtime
    runtime.planner.coordinator = SimpleNamespace(
        invalidate_status=Mock(),
        status=AsyncMock(
            return_value={
                "configured": True,
                "connected": True,
                "provider": "Mavo Core",
                "model": "fixture",
                "error": None,
            }
        ),
    )
    result = client.get("/api/v1/core/status?refresh=true", headers=auth)
    assert result.status_code == 200 and result.json()["connected"]
    runtime.planner.coordinator.invalidate_status.assert_called_once()
    runtime.planner.coordinator.status.assert_awaited_once()


def test_runtime_does_not_launch_development_core_by_default(client):
    assert not client.app.state.dev_core.enabled
    assert client.app.state.dev_core.task is None


def test_backend_lifespan_owns_opted_in_manager_without_spawning_in_test(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from mavo.config import Settings
    from mavo.credentials import MemoryCredentialStore
    from mavo.main import create_app

    lifecycle = SimpleNamespace(start=Mock(), close=AsyncMock())
    constructor = Mock(return_value=lifecycle)
    monkeypatch.setattr("mavo.dev_core.DevCore", constructor)
    app = create_app(
        Settings(
            data_dir=tmp_path,
            require_login=False,
            workers_enabled=False,
            dev_manage_core=True,
            core_gateway_url="http://127.0.0.1:8741",
        ),
        credentials=MemoryCredentialStore(),
    )
    with TestClient(app):
        lifecycle.start.assert_called_once()
    lifecycle.close.assert_awaited_once()
    assert constructor.call_args.args == ("http://127.0.0.1:8741", True)
