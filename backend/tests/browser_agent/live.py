# ruff: noqa: E501, E731
# ruff: noqa: E501, F841
"""The same scripted agent, on real websites, through Mavo's real browser launch (offscreen
window, scope-enforcing proxy, saved-login fill). Needs the internet.

    python -m tests.browser_agent.live            # public sites
    python -m tests.browser_agent.live --profile <your automation profile folder>   # + the portal

Nothing here signs in anywhere except the public demo account that the-internet.herokuapp.com
prints on its own login page."""

import asyncio
import re
import shutil
import sys
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from mavo.desktop_actions import DesktopActions

from .harness import Agent, rows_of
from .report import table
from .scenarios import Result, result


@asynccontextmanager
async def live(domains, *, root=None, login=None):
    desktop = DesktopActions(root or Path(tempfile.mkdtemp()))
    desktop.guard = lambda: True
    desktop.login_lookup = login
    rule = {"enabled": True, "mode": "restricted", "domains": domains, "revision": "live"}
    try:
        yield Agent(desktop, rule), desktop
    finally:
        await desktop.close()


async def httpbin_order_form():
    async with live(["httpbin.org"]) as (a, _):
        await a.open("https://httpbin.org/forms/post")
        read = await a.read()
        await a.fill(read, "Customer name", "Asha Rao")
        await a.fill(read, "Telephone", "9876543210")
        await a.fill(read, "E-mail address", "asha@example.com")
        await a.click(read, "Medium", "radio")
        await a.click(read, "Bacon", "checkbox")
        await a.click(read, "Extra Cheese", "checkbox")
        await a.fill(read, "Preferred delivery time", "18:30")
        await a.fill(read, "Delivery instructions", "Ring twice")
        await a.click(read, "Submit order", "button")
        done = await a.read()
        text = re.sub(r"\s+", " ", done["text"])
        want = [
            '"custname": "Asha Rao"',
            '"custtel": "9876543210"',
            '"custemail": "asha@example.com"',
            '"size": "medium"',
            '"bacon"',
            '"cheese"',
            '"delivery": "18:30"',
            '"comments": "Ring twice"',
        ]
        missing = [w for w in want if w not in text]
        return result(
            "Live form: httpbin pizza order, 8 fields incl. radio/checkbox/time",
            not missing,
            "server echoed everything" if not missing else f"missing {missing}",
            a,
        )


async def selenium_web_form():
    async with live(["www.selenium.dev"]) as (a, _):
        await a.open("https://www.selenium.dev/selenium/web/web-form.html")
        read = await a.read()
        await a.fill(read, "Text input", "Mavo test")
        await a.fill(read, "Textarea", "Line one")
        await a.select(read, "Dropdown (select)", "Two")
        await a.click(read, "Default checkbox", "checkbox")
        await a.click(read, "Default radio", "radio")
        await a.fill(read, "Date picker", "2026-11-15")
        try:
            await a.do("browser.fill", ref=a.find(read, "Password", "textbox")["ref"], text="x")
            refused = False
        except ValueError:
            refused = True
        await a.click(read, "Submit", "button")
        done = await a.read()
        ok = "Received!" in done["text"] and "Form submitted" in done["text"] and refused
        return result(
            "Live form: selenium test form (text, select, check, radio, date), password refused",
            ok,
            f"password refused={refused}; confirmation shown={'Received!' in done['text']}",
            a,
        )


async def wikipedia_search():
    async with live(["en.wikipedia.org"]) as (a, _):
        await a.open("https://en.wikipedia.org/wiki/Main_Page")
        read = await a.read()
        await a.do(
            "browser.fill", ref=a.find(read, "Search Wikipedia", "textbox")["ref"], text="Chennai"
        )
        await a.do("browser.press", key="Enter")
        done = await a.read()
        ok = (
            "Chennai" in " ".join(done.get("view", "").split("HEADINGS: ")[-1].split("|")[:2])
            and "population" in done["text"].lower()
        )
        return result(
            "Live search: Wikipedia box, Enter, read the article", ok, f"landed on {done['url']}", a
        )


async def saved_login_autofill():
    login = lambda host: (
        ("tomsmith", "SuperSecretPassword!") if host == "the-internet.herokuapp.com" else None
    )
    async with live(["the-internet.herokuapp.com"], login=login) as (a, _):
        opened = await a.open("https://the-internet.herokuapp.com/login")
        read = await a.read()
        signed_in = "secure area" in read["text"].lower() and opened.get(
            "signed_in_with_saved_login"
        )
        await a.click(read, "Logout", "link")
        out = await a.read()
        return result(
            "Live login: saved login fills the form, password never shown",
            signed_in and "logged out" in out["text"].lower(),
            f"signed in by Mavo={bool(signed_in)}; password in read: {'SuperSecret' in read['view']}",
            a,
        )


async def airouter_public():
    async with live(["www.airouter.in"]) as (a, d):
        await a.open("https://www.airouter.in/")
        reached = []
        for goal in ("pricing", "docs", "models"):
            await a.open("https://www.airouter.in/")
            nav = await a.do("browser.navigate", goal=goal)
            reached.append((goal, nav["navigation"]["reached"], d.page.url.rsplit("/", 1)[-1]))
        wall = (await a.open("https://www.airouter.in/settings/credits")).get("sign_in_required")
        ok = all(r[1] for r in reached) and wall and wall["kind"] == "login"
        return result(
            "Live navigation: AIRouter pages by name; logged-out credits = sign-in wall",
            ok,
            f"{reached}; credits wall={bool(wall)}",
            a,
        )


async def github_pricing():
    async with live(["github.com"]) as (a, d):
        await a.open("https://github.com/")
        nav = await a.do("browser.navigate", goal="pricing")
        read = await a.read()
        ok = nav["navigation"]["reached"] and "/pricing" in d.page.url and "$" in read["text"]
        return result(
            "Live navigation: GitHub home to its pricing page",
            ok,
            f"{nav['navigation']['steps']} then {d.page.url}",
            a,
        )


async def hacker_news_pages():
    async with live(["news.ycombinator.com"]) as (a, _):
        await a.open("https://news.ycombinator.com/")
        first = await a.read()
        top = re.findall(r"\n(\d+)\.\s", "\n" + first["text"])
        await a.click(first, "More", "link")
        second = await a.read()
        again = re.findall(r"\n(\d+)\.\s", "\n" + second["text"])
        ok = top and again and int(again[0]) > int(top[-1])
        return result(
            "Live pagination: Hacker News 'More' goes to the next 30",
            ok,
            f"page 1 ranks {top[0]}-{top[-1]}, page 2 starts at {again[0] if again else '?'}",
            a,
        )


async def placements_portal(profile_root):
    async with live(["placements.haveloc.com"], root=profile_root) as (a, _):
        await a.open("https://placements.haveloc.com/jobs")
        read = await a.read()
        total = int(re.search(r"(\d+) Jobs Listed", read["text"]).group(1))
        seen, open_rows, pages = 0, [], 0
        for _ in range(30):
            rows = rows_of(read)
            seen += len(rows)
            pages += 1
            open_rows += [r for r in rows if "open for applications" in " ".join(r).lower()]
            try:
                nxt = a.find(read, "Next", "button")
            except LookupError:
                try:
                    nxt = a.find(read, "Next", "link")
                except LookupError:
                    break
            if nxt.get("disabled"):
                break
            await a.do("browser.click", ref=nxt["ref"])
            read = await a.read()
        names = [r[0][:34] for r in open_rows]
        return result(
            "Live portal: scan every page of the placements jobs table",
            seen == total and total > 0,
            f"{seen}/{total} rows over {pages} pages; open for applications: {len(open_rows)} {names[:3]}",
            a,
        )


async def run_live(profile=None):
    scenarios = [
        httpbin_order_form,
        selenium_web_form,
        wikipedia_search,
        saved_login_autofill,
        airouter_public,
        github_pricing,
        hacker_news_pages,
    ]
    results = []
    copy = None
    if profile:
        copy = Path(tempfile.mkdtemp())
        shutil.copytree(Path(profile) / "browser", copy / "browser")
    for scenario in scenarios + ([lambda: placements_portal(copy)] if copy else []):
        try:
            results.append(await asyncio.wait_for(scenario(), timeout=150))
            print(table(results[-1:]).splitlines()[0], flush=True)
        except Exception as exc:
            name = getattr(scenario, "__name__", "scenario")
            results.append(
                Result(
                    name,
                    False,
                    f"{type(exc).__name__}: {(str(exc).splitlines() or ['timed out'])[0][:150]}",
                )
            )
    if copy:
        shutil.rmtree(copy, ignore_errors=True)
    return results


if __name__ == "__main__":
    profile = sys.argv[sys.argv.index("--profile") + 1] if "--profile" in sys.argv else None
    out = asyncio.run(run_live(profile))
    print(table(out))
    sys.exit(0 if all(r.passed for r in out) else 1)
