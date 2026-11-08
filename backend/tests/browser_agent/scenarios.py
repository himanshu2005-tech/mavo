# ruff: noqa: E501, F841
"""What a Crew does in a browser, as scripted scenarios with a known right answer.

Each scenario returns a Result. The scripted agent chooses elements by their visible name from
the page read, exactly as a model would, so a pass means the page read gave enough to act on."""

import re
from dataclasses import dataclass, field

from . import sites
from .harness import Web, agent, rows_of


@dataclass
class Result:
    name: str
    passed: bool
    detail: str
    calls: int = 0
    reads: int = 0
    seconds: float = 0.0
    extra: dict = field(default_factory=dict)


def web_with(**hosts):
    web = Web()
    for host, handler in hosts.items():
        web.site(host.replace("_", "."), handler)
    return web


def result(name, ok, detail, a=None, **extra):
    return Result(
        name,
        bool(ok),
        detail,
        a.calls if a else 0,
        a.reads if a else 0,
        a.seconds if a else 0.0,
        extra,
    )


# ─────────────────────────────── placements ───────────────────────────────


async def collect_open_jobs(a, read):
    """Scan every page of the table and keep the rows that are open for applications."""
    seen, open_rows = 0, []
    for _ in range(40):
        rows = rows_of(read)
        seen += len(rows)
        open_rows += [r for r in rows if r[2] == "Open For Applications"]
        try:
            nxt = a.find(read, "Next", "button")
        except LookupError:
            break
        if nxt.get("disabled"):
            break
        await a.do("browser.click", ref=nxt["ref"])
        read = await a.read()
    return seen, open_rows


async def placements_full_scan():
    portal = sites.Portal()
    async with agent(web_with(placements_test=portal), domains=["placements.test"]) as (a, _):
        await a.open("https://placements.test/jobs")
        read = await a.read()
        first = f"first read shows {read['text'].split('Jobs Listed')[0].split()[-1]} jobs"
        seen, found = await collect_open_jobs(a, read)
        truth = {j["title"] for j in portal.open_jobs()}
        got = {r[0] for r in found}
        ok = got == truth and seen == len(portal.jobs)
        return result(
            "Placements: scan every page, list open jobs",
            ok,
            f"{first}; scanned {seen}/108 rows, found {len(got)}/{len(truth)} open",
            a,
        )


async def placements_per_page():
    portal = sites.Portal()
    async with agent(web_with(placements_test=portal), domains=["placements.test"]) as (a, _):
        await a.open("https://placements.test/jobs")
        read = await a.read()
        await a.select(read, "Per page", "50")
        read = await a.read()
        seen, found = await collect_open_jobs(a, read)
        ok = seen == 108 and len(found) == len(portal.open_jobs()) and a.reads <= 5
        return result(
            "Placements: use the page-size control (fewer pages)",
            ok,
            f"{seen} rows in {a.reads} reads",
            a,
        )


async def placements_slow_fill():
    portal = sites.Portal(fill_delay_ms=7000)
    async with agent(web_with(placements_test=portal), domains=["placements.test"]) as (a, _):
        await a.open("https://placements.test/jobs")
        read = await a.read()
        ok = "108 Jobs Listed" in read["text"] and len(rows_of(read)) == 20
        return result(
            "Placements: table fills in after 7 seconds",
            ok,
            f"first read: {len(rows_of(read))} rows, waited {read['waited_seconds']}s",
            a,
        )


async def placements_logged_out():
    async with agent(
        web_with(placements_test=sites.Portal(logged_in=False)), domains=["placements.test"]
    ) as (a, _):
        opened = await a.open("https://placements.test/jobs")
        read = await a.read()
        wall = read.get("sign_in_required")
        return result(
            "Placements: logged out is a sign-in wall, not 'no jobs'",
            opened.get("sign_in_required") and wall and wall["kind"] == "login",
            f"wall={wall}",
            a,
        )


async def placements_human_check_first():
    async with agent(
        web_with(placements_test=sites.Portal(logged_in=False, signed_out="captcha")),
        domains=["placements.test"],
    ) as (a, _):
        await a.open("https://placements.test/jobs")
        wall = (await a.read()).get("sign_in_required") or {}
        return result(
            "Placements: human check before login opens at the check page",
            wall.get("kind") == "captcha"
            and wall.get("login_url") == "https://placements.test/captcha",
            f"wall kind={wall.get('kind')}; sign-in window would open at {wall.get('login_url')}",
            a,
        )


async def placements_session_timed_out():
    async with agent(
        web_with(placements_test=sites.Portal(logged_in=False, signed_out="expired")),
        domains=["placements.test"],
    ) as (a, _):
        await a.open("https://placements.test/jobs")
        wall = (await a.read()).get("sign_in_required") or {}
        return result(
            "Placements: 'session timed out' is a sign-in wall with a login link",
            wall.get("kind") == "login"
            and wall.get("login_url") == "https://placements.test/login",
            f"wall kind={wall.get('kind')}; sign-in window would open at {wall.get('login_url')}",
            a,
        )


# ─────────────────────────────── forms ───────────────────────────────

APPLICATION = {
    "name": "Asha Rao",
    "email": "asha@example.com",
    "phone": "9876543210",
    "branch": "CSE",
    "cgpa": "8.4",
    "grad": "2027-05-30",
    "cover": "I build reliable software.",
}


async def fill_application(a, read):
    await a.fill(read, "Full name", APPLICATION["name"])
    await a.fill(read, "Email address", APPLICATION["email"])
    await a.fill(read, "Phone", APPLICATION["phone"])
    await a.select(read, "Branch", APPLICATION["branch"])
    await a.click(read, "Female", "radio")
    await a.click(read, "Python", "checkbox")
    await a.click(read, "SQL", "checkbox")
    await a.fill(read, "CGPA", APPLICATION["cgpa"])
    await a.fill(read, "Graduation date", APPLICATION["grad"])
    await a.fill(read, "Cover letter", APPLICATION["cover"])
    await a.click(read, "I confirm", "checkbox")


async def form_fill_and_submit():
    forms = sites.Forms()
    async with agent(web_with(forms_test=forms), domains=["forms.test"]) as (a, _):
        await a.open("https://forms.test/apply")
        read = await a.read()
        await fill_application(a, read)
        await a.click(read, "Submit application", "button")
        done = await a.read()
        got = forms.submissions[0] if forms.submissions else {}
        expected = {
            **APPLICATION,
            "gender": "female",
            "skills": ["python", "sql"],
            "consent": "yes",
            "resume": "",
            "pw": "",
        }
        ok = "Application received" in done["text"] and got == expected
        return result(
            "Form: fill 11 fields of every kind and submit",
            ok,
            f"server received {len(got)} fields; match={got == expected}",
            a,
        )


async def form_recovers_from_errors():
    forms = sites.Forms()
    async with agent(web_with(forms_test=forms), domains=["forms.test"]) as (a, _):
        await a.open("https://forms.test/apply")
        read = await a.read()
        await a.click(read, "Submit application", "button")
        errors = await a.read()
        listed = [e["name"] for e in errors["elements"]]
        three = (
            errors["text"].count("required")
            + errors["text"].count("valid")
            + errors["text"].count("must")
        )
        await a.click(errors, "Back to the form", "link")
        read = await a.read()
        await fill_application(a, read)
        await a.click(read, "Submit application", "button")
        done = await a.read()
        ok = three == 3 and "Application received" in done["text"] and len(forms.submissions) == 1
        return result(
            "Form: submit empty, read the errors, fix and resubmit",
            ok,
            f"{three} errors shown, then accepted",
            a,
        )


async def form_never_types_secrets():
    async with agent(web_with(forms_test=sites.Forms()), domains=["forms.test"]) as (a, _):
        await a.open("https://forms.test/apply")
        read = await a.read()
        pw = a.find(read, "Portal password", "textbox")
        blocked = []
        for ref in (pw["ref"], a.find(read, "Resume", "textbox")["ref"]):
            try:
                await a.do("browser.fill", ref=ref, text="secret")
            except ValueError as exc:
                blocked.append(str(exc))
        leaked = "value" in pw
        return result(
            "Form: passwords and file pickers are never filled",
            len(blocked) == 2 and not leaked,
            f"refused {len(blocked)}/2; password value exposed to model: {leaked}",
            a,
        )


async def wizard_dependent_fields():
    async with agent(web_with(wizard_test=sites.wizard), domains=["wizard.test"]) as (a, _):
        await a.open("https://wizard.test/")
        read = await a.read()
        stale_next = a.find(read, "Next", "button")["ref"]
        await a.select(read, "State", "Tamil Nadu")
        read = await a.read()
        await a.click(read, "Next", "button")
        read = await a.read()
        cities = a.find(read, "City", "select")["options"]
        await a.select(read, "City", "Chennai")
        read = await a.read()
        await a.click(read, "Next", "button")
        read = await a.read()
        review = "Tamil Nadu / Chennai" in read["text"]
        await a.click(read, "Confirm booking", "button")
        done = await a.read()
        try:
            await a.do("browser.click", ref=stale_next)
            stale = "no error"
        except ValueError as exc:
            stale = "refused with a clear message" if "gone" in str(exc) else str(exc)
        ok = (
            "Booked: Tamil Nadu / Chennai" in done["text"]
            and review
            and "Chennai" in cities
            and "Bengaluru" not in cities
            and stale.startswith("refused")
        )
        return result(
            "Wizard: dependent dropdown across 3 steps, stale ref refused",
            ok,
            f"cities after choosing state: {cities}; stale ref: {stale}",
            a,
        )


# ─────────────────────────────── widgets and obstacles ───────────────────────────────


async def shop_widgets():
    async with agent(web_with(shop_test=sites.shop), domains=["shop.test"]) as (a, _):
        await a.open("https://shop.test/")
        read = await a.read()
        names = {e["name"] for e in read["elements"]}
        # Everything behind the cookie wall is still listed, and clicking through it fails clearly.
        try:
            await a.click(read, "Add to cart", "button")
            blocked = "click went through"
        except ValueError as exc:
            blocked = "refused: " + str(exc)[:60]
        await a.click(read, "Accept all", "button")
        read = await a.read()
        await a.click(read, "Add to cart", "button")
        await a.click(read, "Size", "button")
        read = await a.read()
        await a.click(read, "M", "option")
        read = await a.read()
        shadow_found = "Coupon code" in {e["name"] for e in read["elements"]}
        if shadow_found:
            await a.do(
                "browser.fill", ref=a.find(read, "Coupon code", "textbox")["ref"], text="SAVE10"
            )
            await a.click(read, "Apply coupon", "button")
        final = await a.read()
        text = final["text"]
        ok = (
            blocked.startswith("refused")
            and "1 in cart" in text
            and "Size: M" in text
            and shadow_found
            and "Coupon applied: 10% off" in text
        )
        return result(
            "Shop: cookie wall, custom dropdown, shadow-DOM coupon",
            ok,
            f"blocked click -> {blocked}; shadow DOM found={shadow_found}; saw {len(names)} elements",
            a,
        )


async def hidden_menu_credits(balance="$0.15", expect_alert=True):
    account = sites.Account(balance)
    async with agent(web_with(app_test=account), domains=["app.test"]) as (a, d):
        await a.open("https://app.test/")
        nav = await a.do("browser.navigate", goal="credits")
        read = await a.read()
        amount = float(re.search(r"\$([0-9.]+)", read["text"]).group(1))
        alert = amount < 0.20
        title = await d.page.title()
        ok = nav["navigation"]["reached"] and alert == expect_alert and title == "Credits"
        steps = [list(s)[0] for s in nav["navigation"]["steps"]]
        return result(
            f"Credit watch: find Credits in the account menu ({balance})",
            ok,
            f"steps {steps}; balance ${amount}; alert={alert}; destructive controls untouched={title != 'ACCOUNT DELETED'}",
            a,
        )


async def credit_watch_logged_out():
    account = sites.Account(logged_in=False)
    async with agent(web_with(app_test=account), domains=["app.test"]) as (a, _):
        opened = await a.open("https://app.test/billing/credits")
        wall = opened.get("sign_in_required")
        return result(
            "Credit watch: logged out is reported, not read as empty",
            wall and wall["kind"] == "login",
            f"wall={wall}",
            a,
        )


async def lazy_list():
    async with agent(web_with(feed_test=sites.feed), domains=["feed.test"]) as (a, _):
        await a.open("https://feed.test/")
        plain = await a.read()
        loaded = await a.read(scroll=True)
        ok = "Item 50" in loaded["text"] and "Item 50" not in plain["text"]
        return result(
            "Lazy list: scrolling loads everything",
            ok,
            f"without scroll {plain['text'].count('Item ')} items; with scroll {loaded['text'].count('Item ')}",
            a,
        )


async def slow_site():
    async with agent(web_with(slow_test=sites.slow), domains=["slow.test"]) as (a, _):
        await a.open("https://slow.test/")
        read = await a.read()
        return result(
            "Slow site: waits for a 6 second response",
            "The slow page loaded" in read["text"],
            f"{a.seconds}s",
            a,
        )


async def flaky_error_recovers():
    async with agent(web_with(flaky_test=sites.Flaky()), domains=["flaky.test"]) as (a, _):
        await a.open("https://flaky.test/")
        read = await a.read()
        ok = (
            "3 Jobs Listed" in read["text"]
            and len(rows_of(read)) == 3
            and not read.get("site_error")
        )
        return result(
            "Flaky site: 'Failed to load' is retried automatically",
            ok,
            f"rows={len(rows_of(read))}",
            a,
        )


async def tabs_and_accordion():
    async with agent(web_with(tabs_test=sites.tabs), domains=["tabs.test"]) as (a, _):
        await a.open("https://tabs.test/")
        read = await a.read()
        before = "Deadline: 3 Nov" in read["text"] or "Hidden details" in read["text"]
        await a.click(read, "Deadlines", "tab")
        await a.click(read, "More info", "button")
        read = await a.read()
        expanded = a.find(read, "More info", "button")["expanded"]
        ok = (
            not before
            and "Deadline: 3 Nov 2026 at 5 PM" in read["text"]
            and "Hidden details" in read["text"]
            and expanded is True
        )
        return result(
            "Tabs and accordion: reveal hidden content",
            ok,
            f"hidden before: {before}; accordion expanded flag: {expanded}",
            a,
        )


async def same_tab_links():
    web = web_with(links_test=sites.external_links)
    async with agent(web, domains=["links.test"]) as (a, d):
        await a.open("https://links.test/")
        read = await a.read()
        await a.click(read, "Inside", "link")
        pages = len(d.context.pages)
        return result("Links: stays in one tab", pages == 1, f"open tabs: {pages}", a)


async def iframe_content():
    async with agent(web_with(frames_test=sites.frames), domains=["frames.test"]) as (a, _):
        await a.open("https://frames.test/")
        read = await a.read()
        names = {e["name"] for e in read["elements"]}
        found = {"Card number", "Pay now"} <= names
        return result(
            "Iframe: elements inside a frame are visible and usable",
            found,
            f"elements seen: {sorted(names)}",
            a,
        )


async def hover_menu():
    async with agent(web_with(menu_test=sites.hover_menu), domains=["menu.test"]) as (a, d):
        await a.open("https://menu.test/")
        nav = await a.do("browser.navigate", goal="billing")
        return result(
            "Hover-only menu: Billing appears only under Account",
            nav["navigation"]["reached"] and d.page.url.endswith("/account/billing"),
            f"steps {nav['navigation']['steps']}",
            a,
        )


async def search_enter_and_wait():
    async with agent(web_with(search_test=sites.search_site), domains=["search.test"]) as (a, _):
        await a.open("https://search.test/")
        read = await a.read()
        await a.do("browser.fill", ref=a.find(read, "Query", "textbox")["ref"], text="placements")
        await a.do("browser.press", key="Enter")
        await a.do("browser.wait", seconds=5, text="3 matches")
        done = await a.read()
        return result(
            "Search box: type, press Enter, wait for results",
            "Results for placements: 3 matches" in done["text"],
            f"{a.seconds}s",
            a,
        )


async def confirmation_dialogs():
    site = sites.Confirming()
    async with agent(web_with(confirm_test=site), domains=["confirm.test"]) as (a, d):
        await a.open("https://confirm.test/")
        read = await a.read()
        await a.click(read, "Close account", "button")
        title = await d.page.title()
        sent = await a.click(read, "Send application", "button")
        after = await a.read()
        dialogs = (sent.get("dialogs") or []) + (after.get("dialogs") or [])
        ok = site.sent == 1 and "Sent" in after["text"] and title != "DELETED"
        return result(
            "Dialogs: 'Are you sure?' is accepted, 'Delete account?' is not",
            ok,
            f"form sent={site.sent}; account deleted={title == 'DELETED'}; reported {len(dialogs)} dialog(s)",
            a,
        )


SCENARIOS = [
    placements_full_scan,
    placements_per_page,
    placements_slow_fill,
    placements_logged_out,
    placements_human_check_first,
    placements_session_timed_out,
    form_fill_and_submit,
    form_recovers_from_errors,
    form_never_types_secrets,
    wizard_dependent_fields,
    shop_widgets,
    hidden_menu_credits,
    credit_watch_logged_out,
    lazy_list,
    slow_site,
    flaky_error_recovers,
    tabs_and_accordion,
    same_tab_links,
    iframe_content,
    hover_menu,
    search_enter_and_wait,
    confirmation_dialogs,
]
