"""The optional daily-agent harness must be safe to replay without real credentials."""

import importlib.util
import json
import sys
from pathlib import Path

import httpx
import pytest


@pytest.fixture
def smoke():
    path = Path(__file__).resolve().parents[2] / "scripts" / "check-daily-agents.py"
    spec = importlib.util.spec_from_file_location("daily_agent_smoke", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_daily_crews_run_through_http_and_real_runtime_without_network_or_keys(
    smoke, tmp_path, monkeypatch
):
    monkeypatch.setattr(smoke, "ROOT", tmp_path)

    def no_credentials():
        raise AssertionError("Simulation must never read saved credentials")

    monkeypatch.setattr(smoke, "saved_route", no_credentials)
    monkeypatch.setattr(sys, "argv", ["check-daily-agents.py", "--simulate"])
    smoke.main()
    report = json.loads((tmp_path / ".build" / "daily-agents-simulated.json").read_text())
    assert report["airouter_requests"] == 0
    assert report["inference_requests"] == 5
    assert len(report["checks"]) == 4
    assert all(c["status"] == "passed" for c in report["checks"])
    assert report["external_writes"] == report["unexpected_tool_actions"] == 0
    assert report["checkpoint_diagnostics"][2]["quiet_summary"]
    assert not list((tmp_path / ".build").glob("daily-agents-*/mavo.db"))


@pytest.mark.asyncio
async def test_http_budget_counts_attempts_and_blocks_other_destinations(smoke, monkeypatch):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(503, json={"error": "Fixture unavailable"})

    monkeypatch.setattr(
        smoke.httpx, "AsyncHTTPTransport", lambda **kwargs: httpx.MockTransport(respond)
    )
    transport = smoke.BoundedInference(2)
    request = httpx.Request("POST", "https://api.airouter.in/v1/chat/completions")
    for _ in range(2):
        assert (await transport.handle_async_request(request)).status_code == 503
    with pytest.raises(RuntimeError, match="budget exhausted"):
        await transport.handle_async_request(request)
    assert transport.calls == len(calls) == 2  # Failed responses still cost an attempt.
    for url in ("https://example.org/api", "http://api.airouter.in/v1/chat/completions"):
        with pytest.raises(RuntimeError, match="non-inference"):
            await transport.handle_async_request(httpx.Request("POST", url))
    assert transport.calls == 2


def test_live_mode_requires_explicit_opt_in(smoke, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["check-daily-agents.py"])
    with pytest.raises(SystemExit) as error:
        smoke.main()
    assert error.value.code == 2
