"""Every scenario must pass: the browser agent's tool layer, against awkward fake websites."""

import asyncio

import pytest

from mavo.desktop_actions import installed_browsers

from .scenarios import SCENARIOS, hidden_menu_credits

pytestmark = pytest.mark.skipif(not installed_browsers(), reason="No Chromium browser installed")

CASES = [(scenario.__name__, scenario, ()) for scenario in SCENARIOS]
CASES.append(("hidden_menu_credits_healthy_balance", hidden_menu_credits, ("$3.40", False)))


@pytest.mark.parametrize("name,scenario,args", CASES, ids=[c[0] for c in CASES])
def test_scenario(name, scenario, args):
    outcome = asyncio.run(asyncio.wait_for(scenario(*args), timeout=120))
    assert outcome.passed, f"{outcome.name}: {outcome.detail}"
