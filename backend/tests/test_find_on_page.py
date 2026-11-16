"""Find text on the page (Ctrl+F for an agent): only the visible matches come back, each with
the words around it and the ref of what to click there, and the first one is scrolled into view.
It is part of reading a page, so any agent that may read pages may use it: no new permission."""

from mavo.desktop_actions import BrowserRead
from mavo.site_search import guide
from tests.test_multi_agent import SITE_RULE, with_page

JOBS = "".join(
    f'<div class="card"><a href="/job/{i}">Frontend Intern {i}</a>'
    f"<p>Hyderabad - Stipend Rs {10000 + i * 1000}/month - React</p></div>"
    for i in range(3)
)
PAGE = f"""<html><body>
<p>{"Lots of other text. " * 400}</p>
{JOBS}
<div style="display:none">Stipend hidden: ignore me</div>
<script>var s = "Stipend in a script";</script>
</body></html>"""


def test_find_returns_only_visible_matches_with_their_text_and_what_to_click():
    async def work(desktop, page):
        return await desktop.browser("browser.read", {"find": "stipend"}, SITE_RULE, "t")

    found = with_page(PAGE, work)["find"]
    assert found["count"] == 3  # not the hidden one, not the script
    assert [m["snippet"] for m in found["matches"]] == [
        f"Frontend Intern {i} Hyderabad - Stipend Rs {10000 + i * 1000}/month - React"
        for i in range(3)
    ]
    assert all(m["ref"] for m in found["matches"])  # each with the link to act on


def test_nothing_found_says_so_plainly():
    async def work(desktop, page):
        return await desktop.browser("browser.read", {"find": "salary"}, SITE_RULE, "t")

    result = with_page(PAGE, work)
    assert result["find"]["count"] == 0 and "is not on this page" in result["note"]


def test_find_is_part_of_reading_and_agents_are_told_about_it():
    assert BrowserRead(find="Apply").find == "Apply" and BrowserRead().find is None
    assert "browser.read {find:'stipend'}" in guide({"example.org"})
