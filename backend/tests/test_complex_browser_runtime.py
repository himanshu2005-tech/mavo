"""Real Chromium/runtime integration with deterministic inference; no credits or accounts."""

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from mavo.desktop_actions import installed_browsers


@pytest.fixture
def complex_check():
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "complex_browser_check", root / "scripts/check-complex-crew.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(not installed_browsers(), reason="No Chromium browser installed")
def test_three_agent_team_uses_real_browser_and_verified_handoffs(
    complex_check, tmp_path, monkeypatch
):
    support = complex_check.shared_support()

    def no_keys():
        raise AssertionError("Deterministic test must not read real credentials")

    monkeypatch.setattr(support, "saved_route", no_keys)
    monkeypatch.setattr(complex_check, "shared_support", lambda: support)
    monkeypatch.setattr(complex_check, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["check-complex-crew.py", "--simulate"])
    complex_check.main()
    report = json.loads((tmp_path / ".build/complex-crew-simulated.json").read_text())
    assert report["status"] == "passed" and report["task_status"] == "done"
    assert report["airouter_requests"] == 0 and report["inference_requests"] == 5
    assert len(report["agents"]) == 3
    assert report["agents"][0]["verified_sources"] == 2
    assert report["search_filter_checks"] == 1
    assert report["blocked_browser_requests"] == 0
    assert set(report["message_kinds"]) >= {"task_assignment", "result"}
    assert all(a["status"] == "completed" for a in report["actions"])
    assert not list((tmp_path / ".build").glob("complex-crew-*/mavo.db"))


@pytest.mark.asyncio
async def test_browser_fixture_rejects_external_hosts_and_non_get_requests(complex_check):
    fixture = complex_check.FixtureBrowser(None, "synthetic-worker")
    for method, url in (
        ("GET", "https://unapproved.test/collect"),
        ("POST", complex_check.BASE + "/results"),
    ):
        route = SimpleNamespace(request=SimpleNamespace(method=method, url=url), abort=AsyncMock())
        await fixture.respond(route)
        route.abort.assert_awaited_once()
    assert len(fixture.blocked) == 2 and not fixture.requests


def test_prepared_team_keeps_tools_restricted_to_discovery(complex_check):
    composition = complex_check.team()
    assert len(composition.agents) == 3
    assert sum(a.max_model_calls for a in composition.agents) == 6
    assert composition.limits.model_calls == 6
    assert not any(a.actions for a in composition.agents[1:])
    assert composition.agents[2].reviews == "prepare"
    assert composition.agents[0].verification == "listings"


def test_review_only_recheck_has_a_one_request_ceiling(complex_check, tmp_path, monkeypatch):
    support = complex_check.shared_support()
    monkeypatch.setattr(complex_check, "ROOT", tmp_path)
    complex_check.review_only(support, "fixture-model", "fixture-key", True)
    report = json.loads((tmp_path / ".build/review-contract-simulated.json").read_text())
    assert report["status"] == "passed"
    assert report["airouter_requests"] == 0
    assert len(report["checkpoint_diagnostics"]) == 1
    assert report["checkpoint_diagnostics"][0]["review_verdict"] == "pass"
    assert "not a full-team recheck" in report["workflow"]
