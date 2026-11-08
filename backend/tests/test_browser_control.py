# ruff: noqa: E501
"""Browser control: clicking and typing the way real web apps need, and searches that count.

Found in the user's own runs: an agent clicked a Microsoft Forms control that a tip covered, four
times, and was told only "timed out"; text set in one go into a web app's editor showed on the
page but was never saved; and a fare search ran 26 searches without opening one result."""

import pytest

from mavo.task_plan import TaskPlan
from tests.test_multi_agent import SITE_RULE, with_page

CONTROLS = """<html><body>
<button id="go" onclick="document.title='CLICKED'">Go</button>
<div id="tip" style="position:fixed;inset:0;background:#fff8" aria-label="Tip: try the new designer"></div>
<div id="box" role="textbox" contenteditable="true" aria-label="Question title"></div>
<input id="plain" aria-label="Plain field">
<script>
// The tip goes away on Escape, as such tips do.
document.addEventListener('keydown', e => { if (e.key === 'Escape') document.getElementById('tip').remove(); });
// The editor keeps only what is typed, key by key, like a real web app.
const box = document.getElementById('box');
box.addEventListener('keydown', () => setTimeout(() => { window.saved = box.innerText; }, 0));
</script></body></html>"""

STUCK = """<html><body>
<button id="go" onclick="document.title='CLICKED'">Go</button>
<div role="dialog" aria-label="Before you continue" style="position:fixed;inset:0;background:#fff">
  Before you continue <button>Accept</button></div>
</body></html>"""

SEARCH = """<html><body>
<div class="result result--ad"><a class="result__a" href="https://duckduckgo.com/y.js?ad=1">Cheap tickets AD</a></div>
<div class="result"><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Ferail.in%2Ftrains-between-stations%2Fhyderabad-hyb%2Fchennai-mas&rut=x">Hyderabad to Chennai trains</a>
  <a class="result__snippet">Sleeper   from Rs 395 on the Charminar Express.</a></div>
<div class="result"><a class="result__a" href="https://www.irctc.co.in/nget/train-search">IRCTC</a></div>
</body></html>"""


async def act(desktop, operation, **args):
    return await desktop.browser(operation, args, SITE_RULE, "t")


def test_a_click_under_a_tip_that_escape_closes_goes_through():
    async def work(desktop, page):
        await act(desktop, "browser.click", selector="#go")
        return await page.title()

    assert with_page(CONTROLS, work) == "CLICKED"


def test_a_click_under_a_dialog_says_what_is_in_the_way_not_just_timed_out():
    async def work(desktop, page):
        with pytest.raises(ValueError) as stopped:
            await act(desktop, "browser.click", selector="#go")
        return str(stopped.value), await page.title()

    message, title = with_page(STUCK, work)
    assert 'Something covers that element: "Before you continue' in message, message
    assert title != "CLICKED"  # nothing was clicked through the dialog


def test_text_typed_into_a_web_apps_editor_is_kept_and_a_plain_field_is_filled():
    async def work(desktop, page):
        await page.evaluate("document.getElementById('tip').remove()")
        await act(desktop, "browser.fill", selector="#box", text="Name")
        await act(desktop, "browser.fill", selector="#box", text="Email")  # replaces, not appends
        await act(desktop, "browser.fill", selector="#plain", text="hello")
        return await page.evaluate("window.saved"), await page.input_value("#plain")

    saved, plain = with_page(CONTROLS, work)
    assert saved == "Email" and plain == "hello"


def test_a_search_hands_back_its_results_with_their_real_addresses_and_no_ads():
    async def work(desktop, page):
        return await desktop.search_results()

    results = with_page(SEARCH, work)
    assert [r["url"] for r in results] == [
        "https://erail.in/trains-between-stations/hyderabad-hyb/chennai-mas",
        "https://www.irctc.co.in/nget/train-search",
    ]
    assert results[0]["title"] == "Hyderabad to Chennai trains"
    assert results[0]["snippet"] == "Sleeper from Rs 395 on the Charminar Express."


def test_an_agent_opens_one_page_per_step_so_none_is_opened_and_never_read(client, auth):
    """It opened four fare pages in one step; only the fourth was read."""
    from tests.test_tasks import (
        ANSWER,
        allow_browser,
        fake_site,
        plan,
        scripted,
        setup,
        start,
        wait,
    )
    from tests.test_tasks import step as task_step

    runtime = setup(client, auth)
    allow_browser(client, auth)
    visits = []
    fake_site(runtime, visits)
    four = {
        "state": "continue",
        "summary": "Opening the fare pages.",
        "next_step": "Read them",
        "actions": [
            {"operation": "browser.open", "arguments": {"url": f"https://forms.example.com/{n}"}}
            for n in range(4)
        ],
    }
    seen = scripted(runtime, crew=[four, ANSWER])
    done = wait(client, auth, start(client, auth, plan(task_step("s", "agent"), model_calls=8)))
    assert done["status"] == "done", done
    # The first page was opened and read in full; the others were left for the next step.
    assert [v[0] for v in visits] == ["browser.open", "browser.read"]
    assert visits[0][1]["url"].endswith("/0")
    assert "NOT RUN. One page per step" in seen["crew_prompts"][1]


def test_an_agent_that_searches_can_also_open_and_read_a_result():
    plan = {
        "title": "Fares",
        "deliver": "The fares",
        "final": "s",
        "steps": [
            {
                "id": "s",
                "kind": "agent",
                "title": "Search",
                "instructions": "Search for fares",
                "actions": ["browser.search", "browser.navigate"],
                "sites": ["html.duckduckgo.com"],
            }
        ],
    }
    actions = TaskPlan.model_validate(plan).steps[0].actions
    assert {"browser.search", "browser.open", "browser.read"} <= set(actions)
