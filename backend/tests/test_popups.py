"""A sign-up pop-up over a results page (Internshala's "Register" box) is closed as a person
would, by its close control only, never by "Sign up with Google". The page behind it is not a
sign-in wall, and typing into the search box behind it works."""

from tests.test_multi_agent import SITE_RULE, with_page

LISTINGS = "".join(
    f'<li><a href="/internship/{i}">Frontend Developer Intern {i} - Hyderabad - 15,000/month</a>'
    f"<p>{'React, JavaScript, HTML, CSS. ' * 8}</p></li>"
    for i in range(70)
)
NAG_PAGE = f"""<html><body>
<input id="q" aria-label="Search internships" placeholder="Search">
<ul>{LISTINGS}</ul>
<div id="nag" role="dialog" style="position:fixed;inset:0;z-index:1000;background:#fff">
  <h2>Register to apply</h2>
  <button id="google" onclick="document.title='SIGNED-UP'">Sign up with Google</button>
  <input type="password" aria-label="Password">
  <button id="x" aria-label="Close" onclick="document.getElementById('nag').remove()">×</button>
</div>
</body></html>"""


def test_a_sign_up_pop_up_is_closed_by_its_close_control_and_the_page_is_not_a_wall():
    async def work(desktop, page):
        closed = await desktop.clear_overlays()
        gone = await page.locator("#nag").count() == 0
        wall = await desktop.sign_in_wall()
        await desktop.browser("browser.fill", {"selector": "#q", "text": "react"}, SITE_RULE, "t")
        return closed, gone, wall, await page.title(), await page.locator("#q").input_value()

    closed, gone, wall, title, typed = with_page(NAG_PAGE, work)
    assert closed == "Close" and gone  # the × button, by its label
    assert title != "SIGNED-UP"  # never the sign-up button
    assert wall is None  # a full results page is not a login page
    assert typed == "react"  # and the search box behind it takes text


SMALL_LOGIN = """<html><body><h1>Sign in</h1><input type="email"><input type="password">
<button>Sign in</button></body></html>"""


def test_a_real_sign_in_page_is_still_a_wall_and_nothing_is_clicked_on_it():
    async def work(desktop, page):
        closed = await desktop.clear_overlays()
        return closed, await desktop.sign_in_wall()

    closed, wall = with_page(SMALL_LOGIN, work)
    assert closed is None and wall and wall["kind"] == "login"
