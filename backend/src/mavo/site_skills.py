"""Built-in skills: a site's well-known job, done by Mavo's own code, with no model.

A model driving an unfamiliar page click by click is slow, costs a model call for every look, and
(with a small model) often loses its way: asked to build a Microsoft Form it clicked and re-read
for ten minutes and never typed a word. For the jobs people ask for most, Mavo knows the page.

A skill works in Mavo's own browser exactly as an agent would: the same site limits, the same
sign-in session, and only if the person has turned the actions it uses on in their default
Actions. It checks its own work by reading the page back before it reports, and when the site is
not what it expected it stops with a plain reason instead of guessing.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SkillStop(Exception):  # noqa: N818 - reads as a state
    """The site was not what the skill expected (it changed, or a step never showed). Nothing is
    guessed: the reason is the message."""


class SkillNeedsYou(Exception):  # noqa: N818
    """Only the person can go on from here: a sign-in, usually."""


@dataclass(frozen=True)
class Skill:
    id: str
    does: str  # one line for the planner
    params: str  # the parameters, as the planner is shown them
    sites: tuple[str, ...]
    actions: tuple[str, ...]  # what the person must have turned on in their default Actions
    model: type[BaseModel]
    run: Callable[..., Awaitable[str]]
    reads_steps: bool = False  # given the output of the steps it uses (`inputs`)


# ---- Microsoft Forms -----------------------------------------------------------------------------


class FormParams(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=200)
    questions: list[str] = Field(min_length=1, max_length=20)

    @field_validator("questions", mode="before")
    @classmethod
    def clean(cls, values):
        if not isinstance(values, list):
            return values
        return [str(v).strip()[:200] for v in values if isinstance(v, str | int) and str(v).strip()]


HOME_BUTTON = re.compile(r"^(Create a new form|New Form)$", re.I)
SIGN_IN_FIELD = "input[type=password], input[name=loginfmt], input[type=email]"
ACCOUNT_TILE = '[role=button][data-test-id*="@"]:not([data-test-id$="-menu-dots"])'
LOGIN_HOSTS = ("login.microsoftonline.com", "login.live.com", "login.microsoft.com")
NOISE = re.compile(r"^(Dismiss|Got it|Skip|Maybe later|No thanks)$", re.I)


async def shown(locator, what, seconds=25):
    from playwright.async_api import TimeoutError as PlaywrightTimeout

    try:
        await locator.first.wait_for(state="visible", timeout=seconds * 1000)
    except PlaywrightTimeout:
        raise SkillStop(
            f"Microsoft Forms did not show {what}. Its page may have changed, or it is slow."
        ) from None
    return locator.first


async def type_into(box, text):
    """Type as a person does, key by key. Setting the text in one go (Playwright's fill) shows it on
    the page but the page's own editor never hears about it, so it is never saved: a Microsoft Form
    built that way came back from Microsoft as "Untitled form" with two empty questions."""
    await box.click()
    await box.press("Control+A")
    await box.press_sequentially(text, delay=25)


async def reach_forms_home(desktop):
    """Open Forms, getting through Microsoft's sign-in from the saved session: the account list
    ("Pick an account") is answered with the account that is already signed in, and "Stay signed
    in?" with Yes. A real sign-in form (a password or email box) is the person's to fill."""
    from playwright.async_api import TimeoutError as PlaywrightTimeout

    account = None
    for _ in range(45):
        desktop.check()
        page = desktop.page
        if page is None or page.is_closed():
            raise SkillStop("Mavo's browser closed while Microsoft Forms was opening.")
        host = urlsplit(page.url).hostname or ""
        try:
            if await page.get_by_role("button", name=HOME_BUTTON).first.is_visible():
                return account  # known only when Microsoft asked which account to use
            if host in LOGIN_HOSTS:
                if await page.locator(SIGN_IN_FIELD).first.is_visible():
                    raise SkillNeedsYou("Microsoft needs you to sign in to Forms.")
                tiles = page.locator(ACCOUNT_TILE)
                if await tiles.count():
                    account = await tiles.first.get_attribute("data-test-id")
                    await tiles.first.click()
                else:
                    stay = page.get_by_role("button", name="Yes", exact=True)
                    if await stay.count() and await stay.first.is_visible():
                        await stay.first.click()
        except PlaywrightTimeout:
            pass
        await asyncio.sleep(1)
    raise SkillStop("Microsoft Forms did not open. Its page may have changed, or it is slow.")


async def question_titles(page):
    """The questions as the designer now shows them, by number."""
    found = await page.evaluate(
        """() => {
          const out = {};
          for (const el of document.querySelectorAll('[aria-label]')) {
            const label = el.getAttribute('aria-label') || '';
            let m = label.match(/^(\\d+)\\. Question title (.*?)(?: \\w+ lines? text)?\\.?$/);
            if (m) out[m[1]] = m[2].trim();
            m = label.match(/^Question title (\\d+)\\b/);
            if (m && el.getAttribute('role') === 'textbox') out[m[1]] = (el.innerText || '').trim();
          }
          return out;
        }"""
    )
    return [found[key] for key in sorted(found, key=int)]


async def form_title(page):
    return await page.evaluate(
        """() => {
          const box = document.querySelector('[role=textbox][aria-label="Form title"]');
          if (box) return (box.innerText || '').trim();
          const el = [...document.querySelectorAll('[aria-label]')].find(
            e => (e.getAttribute('aria-label') || '').startsWith('Form title '));
          return el ? el.getAttribute('aria-label').slice('Form title '.length).trim() : '';
        }"""
    )


async def build_form(desktop, page, params: FormParams):
    """In the form designer: title the form, add each question as short text, read it back, and
    open Collect responses. Returns the link people answer the form at, and the dialog's text."""
    title_button = await shown(
        page.get_by_role("button", name=re.compile(r"^Form title")), "the form designer", 30
    )
    for banner in await page.get_by_role("button", name=NOISE).all():
        if await banner.is_visible():
            await banner.click()
    await title_button.click()
    box = await shown(page.get_by_role("textbox", name="Form title"), "the title box", 10)
    await type_into(box, params.title)
    await asyncio.sleep(0.8)
    for number, question in enumerate(params.questions):
        desktop.check()
        quick = page.get_by_role("button", name=re.compile(r"^Quick start with"))
        if number == 0 and await quick.count():
            await quick.first.click()
        else:
            add = page.get_by_role("button", name="Add new question")
            await (await shown(add, "Add new question", 10)).click()
        text_type = page.get_by_role("button", name="Text", exact=True)
        await (await shown(text_type, "the Text question type", 10)).click()
        field = page.get_by_role("textbox", name=re.compile(r"^Question title")).last
        await type_into(await shown(field, "the question box", 10), question)
        await asyncio.sleep(0.8)
    # Give the page time to save, then read the form back from what is SAVED: after a reload.
    # What the page shows before that is only what was typed, not what Microsoft kept.
    try:
        await page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:  # noqa: S110 - a busy page is not a failed save; the reload tells
        pass
    await asyncio.sleep(3)
    await page.reload(wait_until="domcontentloaded")
    await shown(page.get_by_role("button", name=re.compile(r"^Form title")), "the saved form", 40)
    await asyncio.sleep(2)
    got_title, got_questions = await form_title(page), await question_titles(page)
    if got_title != params.title or got_questions != params.questions:
        raise SkillStop(
            "Microsoft Forms did not keep what was typed: reloaded, the form has the title "
            f'"{got_title}" and the questions {got_questions}, not "{params.title}" and '
            f"{params.questions}."
        )
    collect = page.get_by_role("button", name="Collect responses")
    await (await shown(collect, "Collect responses", 10)).click()
    link_box = await shown(
        page.get_by_label("Link for Collecting Responses"), "the form's link", 20
    )
    link = (await link_box.input_value()).strip()
    host = urlsplit(link).hostname or ""
    if urlsplit(link).scheme != "https" or not host.endswith(("cloud.microsoft", "office.com")):
        raise SkillStop("Microsoft Forms did not give a link to the form.")
    dialog = await page.evaluate(
        "() => [...document.querySelectorAll('[role=dialog]')]"
        ".map(d => d.innerText || '').join('\\n')"
    )
    return link, dialog


def report(link: str, dialog: str, account: str | None) -> str:
    """The link, then at most one short note on what the person may not expect."""
    notes = []
    if account:
        notes.append(f"Made in the account {account}.")
    org = re.search(r"Only people in (.+?) can respond", dialog)
    if org:
        notes.append(
            f"Only people in {org.group(1).strip()} can respond (Microsoft's default); change it "
            "under Collect responses."
        )
    return link + ("\n" + " ".join(notes) if notes else "")


async def forms_create(desktop, rule, owner, params: FormParams) -> str:
    """Create a blank Microsoft Form, title it, add each question as short text, read it back, and
    return the link people use to answer it."""
    await desktop.browser("browser.open", {"url": "https://forms.office.com"}, rule, owner)
    account = await reach_forms_home(desktop)
    await desktop.page.get_by_role("button", name=HOME_BUTTON).first.click()
    # The designer opens in a new tab, or in this one.
    for _ in range(30):
        desktop.check()
        await desktop.follow_new_tab()
        if await desktop.page.get_by_role("button", name=re.compile(r"^Form title")).count():
            break
        await asyncio.sleep(1)
    link, dialog = await build_form(desktop, desktop.page, params)
    return report(link, dialog, account)


# ---- The web, read by Mavo itself ------------------------------------------------------------


class WebParams(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    query: str = Field(min_length=2, max_length=300)
    pages: int = Field(default=3, ge=1, le=4)


def readable(text: str, size: int = 2200) -> str:
    """A page's prose without its menus: lines long enough to be sentences."""
    lines = [line.strip() for line in (text or "").splitlines()]
    prose = [line for line in lines if len(line) >= 60]
    return re.sub(r"\s+", " ", " ".join(prose))[:size]


class _Text(HTMLParser):
    """A page's visible text, one line per block, without scripts, styles or menus."""

    SKIP = {"script", "style", "noscript", "svg", "nav", "header", "footer", "form", "aside"}
    BLOCK = {"p", "div", "li", "h1", "h2", "h3", "h4", "tr", "br", "section", "article"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skipping = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skipping += 1
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skipping:
            self.skipping -= 1
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skipping:
            self.out.append(data)


def page_text(html: str) -> str:
    parser = _Text()
    try:
        parser.feed(html)
    except Exception:  # noqa: S110 - a broken page gives what was read so far
        pass
    return "".join(parser.out)


async def public_https(url: str) -> bool:
    """Only an https address on the public internet: never this computer or the local network."""
    import ipaddress
    import socket

    split = urlsplit(url)
    if split.scheme != "https" or not split.hostname or split.username or split.password:
        return False
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, split.hostname, 443)
    except OSError:
        return False
    return bool(infos) and all(ipaddress.ip_address(i[4][0]).is_global for i in infos)


async def fetch_pages(urls: list[str]) -> dict[str, str]:
    """The readable text of several public pages at once (url -> text; a page that refused, or
    gave too little text, is left out for the browser to try)."""
    import httpx

    async def guard(request):
        if not await public_https(str(request.url)):
            raise httpx.RequestError("Not a public https address", request=request)

    async def one(client, url):
        try:
            async with client.stream("GET", url) as response:
                if response.status_code != 200 or "html" not in response.headers.get(
                    "content-type", ""
                ):
                    return url, ""
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 3 * 1024 * 1024:
                        break
            return url, readable(page_text(body.decode(response.encoding or "utf-8", "replace")))
        except Exception:
            return url, ""

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like "
        "Gecko) Chrome/140.0 Safari/537.36",
        "Accept-Language": "en-IN,en;q=0.9",
    }
    async with httpx.AsyncClient(
        timeout=12, follow_redirects=True, headers=headers, event_hooks={"request": [guard]}
    ) as client:
        found = await asyncio.gather(*(one(client, u) for u in urls))
    return {url: text for url, text in found if len(text) >= 300}


async def web_read(desktop, rule, owner, params: WebParams) -> str:
    """Search the web in Mavo's browser, open the best results and read them. No model: a small
    model driving a browser searched 26 times and never opened a result. Returns the results
    and what the pages say, for a writer to answer from with sources."""
    import json

    found = await desktop.browser("browser.search", {"query": params.query}, rule, owner)
    results = (found or {}).get("results") or []
    if not results:
        raise SkillStop("The web search returned no results. Try other words.")
    # The pages at once, as plain requests; only a page that refused goes to the browser (one at a
    # time, and slow: 6 to 29 seconds a page).
    candidates = results[: params.pages + 3]
    if rule.get("mode") == "restricted":
        # Browser limited to some sites: the quick fetch keeps to them too.
        from mavo.desktop_actions import host_allowed, with_companions

        allowed = with_companions(rule.get("domains") or [])
        candidates = [r for r in candidates if host_allowed(r["url"], allowed)]
    fetched = await fetch_pages([r["url"] for r in candidates])
    # Pages already read come first; the browser is only for what is still missing.
    candidates.sort(key=lambda r: r["url"] not in fetched)
    pages = []
    for result in candidates:
        if len(pages) >= params.pages:
            break
        if result["url"] in fetched:
            pages.append(
                {
                    "n": len(pages) + 1,
                    "title": result["title"],
                    "url": result["url"],
                    "text": fetched[result["url"]],
                }
            )
            continue
        desktop.check()
        try:
            opened = await desktop.browser(
                "browser.open", {"url": result["url"]}, rule, owner, limit=40
            )
            if (opened or {}).get("sign_in_required"):
                continue
            # The page's text straight away: a full read waits for the page again and
            # snapshots every control, which made three pages take a minute and a half.
            raw = await desktop.page.locator("body").inner_text(timeout=8000)
        except Exception:  # noqa: S112 - a page that will not open is skipped, the next is read
            continue
        text = readable(raw)
        if len(text) >= 300:
            pages.append(
                {"n": len(pages) + 1, "title": result["title"], "url": result["url"], "text": text}
            )
    return json.dumps(
        {
            "query": params.query,
            "pages_read": pages,
            "other_results": [
                {"title": r["title"], "url": r["url"], "snippet": r.get("snippet", "")}
                for r in results[:8]
                if r["url"] not in {p["url"] for p in pages}
            ],
        },
        ensure_ascii=False,
    )


class GatherParams(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    query: str = Field(min_length=1, max_length=300)
    sources: list[str] = Field(min_length=1, max_length=4)

    @field_validator("sources")
    @classmethod
    def known(cls, values):
        from mavo.resources.open_data import SOURCES

        kept = [v for v in values if v in SOURCES]
        if not kept:
            raise ValueError("Unknown open data sources")
        return kept


async def gather(desktop, rule, owner, params: GatherParams) -> str:
    """Look the same words up in several open data sources at once. No model, no browser."""
    import json

    from mavo.resources.open_data import Lookup, look_up

    found = await asyncio.gather(
        *(look_up(Lookup(source=s, query=params.query)) for s in params.sources)
    )
    if all(f["status"] != "ok" for f in found):
        reasons = "; ".join(f["result"].get("error", "") for f in found)
        raise SkillStop("Nothing came back: " + reasons)
    return json.dumps(found, ensure_ascii=False, default=str)


class TeamParams(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    request: str = Field(min_length=3, max_length=2000)
    name: str | None = Field(default=None, max_length=60)
    project: str | None = Field(default=None, max_length=48)  # set when the plan is made
    framework: str | None = Field(default=None, pattern=r"^(react|vue)$")


async def expanded_elsewhere(*_args, **_kwargs) -> str:
    raise SkillStop("team.build is run by the task runner's build team (team_build.BuildTeam).")


def build_skills() -> dict[str, Skill]:
    from mavo.team_build import ProjectParams, app_check, project_save

    return {
        "team.build": Skill(
            id="team.build",
            does="BUILDS SOFTWARE with a team a lead picks for the job (builders, reviewers, a "
            "fixer, and Mavo's Tester), working in rounds until it works: an app, a page, a "
            "tool, a game, a script. Use it, as the ONLY step, whenever the person "
            "wants something built or made that is software",
            params='{"request": what to build, in the person\'s words plus any details they gave, '
            '"name": a short name such as "Todo List"}',
            sites=(),
            actions=("project.create", "project.write", "project.read"),
            model=TeamParams,
            run=expanded_elsewhere,
        ),
        "project.save": Skill(
            id="project.save",
            does="saves files written as labelled blocks by the step it uses into a project "
            "(part of team.build)",
            params='{"project": folder name}',
            sites=(),
            actions=("project.create", "project.write"),
            model=ProjectParams,
            run=project_save,
            reads_steps=True,
        ),
        "app.check": Skill(
            id="app.check",
            does="tests what a project holds in a headless browser (part of team.build)",
            params='{"project": folder name}',
            sites=(),
            actions=("project.read",),
            model=ProjectParams,
            run=app_check,
        ),
    }


SKILLS: dict[str, Skill] = {
    "web.read": Skill(
        id="web.read",
        does="searches the web in Mavo's browser, opens the best results and reads them; give "
        "its output to a writer helper that answers with sources",
        params='{"query": the search words, "pages": 3}',
        sites=(),
        actions=("browser.search", "browser.open", "browser.read"),
        model=WebParams,
        run=web_read,
    ),
    "open_data.gather": Skill(
        id="open_data.gather",
        does="looks the same words up in up to 4 open data sources at once (for example news, "
        "wikipedia, arxiv); give its output to a writer helper",
        params='{"query": the words, "sources": ["news", "wikipedia"]}',
        sites=(),
        actions=(),
        model=GatherParams,
        run=gather,
    ),
    "microsoft_forms.create": Skill(
        id="microsoft_forms.create",
        does="creates a Microsoft Form, titles it and adds short-answer questions, and returns "
        "the link people answer it at",
        params='{"title": the form title, "questions": ["Name", "Email"] (short-answer questions)}',
        sites=("forms.office.com",),
        actions=("browser.open", "browser.click", "browser.fill"),
        model=FormParams,
        run=forms_create,
    ),
}
SKILLS.update(build_skills())
