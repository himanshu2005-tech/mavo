# ruff: noqa: E501 - the plan's steps and the in-page test script read best on one line each
"""A software team for "build me a ...", picked for the job, that works until the app works.

Asked to build something ("a todo list app"), Mavo does not hand a small model a browser or a
terminal and hope. A lead (model) designs it and picks the team THIS job needs: one to three
builders who write the files in turn (a coder, then a UI designer...), up to two reviewers (a
security reviewer for logins, none for a tiny script) and a fixer. Then:

  builders write every file, Mavo saves them, the Tester (Mavo itself, no model) opens the app
  in a headless browser, catches every script error and tries it as a person would; the
  reviewers read it against the design and the test report; the fixer fixes what was found;
  saved and tested again ... round after round, until the test passes and no reviewer has a
  real problem left, a fix changes nothing (stuck), or the rounds run out (3; 1 for "quick",
  5 for "polished"). Out of rounds with problems left, the person is asked once whether to
  keep going; nobody is asked anything for a build that works.

Nothing is executed on the person's machine except the app's own page script inside a headless
browser tab; Python is only compiled (a syntax check), never run (running stays behind approval).
A web app is one self-contained index.html (inline CSS and JS: a project may hold no .js files,
which Windows would run on a double-click).
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

ROLES = {
    "architect": (
        "Role: software architect. Design the smallest complete thing that does what the person "
        "asked, done well. Write: GOAL (one line); FEATURES (the must-haves, numbered; include "
        "what a person expects without saying, e.g. a todo list adds, completes, deletes, keeps "
        "items after a reload, and handles an empty input); KIND: web (one self-contained "
        "index.html with inline CSS and JavaScript) for anything with a screen. If the person "
        "names React (or Vue), use it from the pinned CDN builds the coder is given, still in "
        "the one index.html; otherwise no external files at all. Or python (main.py, standard library only) for a script or command-line tool; "
        "FILES (each path and what it holds); UI (layout and states, clean and modern); TESTS "
        "(how to check each feature by hand). No code. Decide sensible details yourself (look, "
        "layout, names). Only if something essential cannot be decided without the person (what "
        "the thing is for, which of two very different things they mean), reply with exactly "
        "one line instead: QUESTION: <one short question>."
    ),
    "coder": (
        "Role: senior developer. Implement the design in INPUT completely and correctly: every "
        "feature, no placeholders, no TODOs, nothing left for later. "
    ),
    "reviewer": (
        "Role: code reviewer. Check the code in INPUT against the design and the test report: "
        "every feature present and working, bugs, crashes, edge cases (empty input, very long "
        "text, reload), and anything the test report says failed. List only real problems, "
        "most serious first, each with the exact fix. End with a line `VERDICT: pass` or "
        "`VERDICT: fix`."
    ),
    "debugger": (
        "Role: debugger. Fix every problem the review and the test report in INPUT found, "
        "without breaking anything else. If there is nothing to fix, reply exactly: NO CHANGES. "
        "Otherwise reply with each changed file in FULL as a fenced block whose first line "
        "names it (```file:index.html), then one line: CHANGES: what you fixed."
    ),
}
FILE_RULES = (
    "Write each file in full as a fenced block whose first line names it, exactly like:\n"
    "```file:index.html\n...the whole file...\n```\nWeb: one index.html, inline <style> and "
    "<script>, modern clean look, works when opened from disk, keeps data in localStorage, "
    "accessible labels on inputs and buttons. No external resources, except when the design "
    "says React or Vue: then exactly these, nothing else: "
    '<script src="https://unpkg.com/react@18.3.1/umd/react.production.min.js"></script> '
    '<script src="https://unpkg.com/react-dom@18.3.1/umd/react-dom.production.min.js">'
    "</script> "
    '<script src="https://unpkg.com/@babel/standalone@7.26.4/babel.min.js"></script> '
    'and your code in <script type="text/babel">; or for Vue '
    '<script src="https://unpkg.com/vue@3.5.13/dist/vue.global.prod.js"></script>. '
    "Python: standard library only. After the blocks, one line: NOTES: what you built."
)
ROLES["coder"] += FILE_RULES
BUILD_ROLES = set(ROLES)  # these write whole files: more room and time than a short answer
OUTPUT_CHARS = 60_000
TIMEOUT = 300


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "app"


# ---- the plan ------------------------------------------------------------------------------------


def team_plan(request: str, name: str | None = None) -> dict:
    """The whole team as a one-time task plan (task_plan.TaskPlan input)."""
    project = slug(name or request)
    ask = request.strip()[:1500]
    # "Create a basic react app": the team made plain HTML anyway. A named framework is now an
    # order to every builder, and the tester checks the page really uses it.
    named = FRAMEWORK.search(request)
    framework = named[1].lower() if named else None
    if framework:
        ask += (
            f"\n\nREQUIRED: the person asked for {framework.title()}. Build it with "
            f"{framework.title()} (the pinned CDN builds), never plain JavaScript."
        )
    rounds = rounds_for(request)
    return {
        "title": f"Build: {name or request}"[:80],
        "deliver": "Where it was built, how to open it, and what was tested",
        "final": "build",
        # The lead, up to 3 builders, and per round up to 2 reviewers and a fixer. The run's
        # budget is set exactly once the lead has picked the team.
        "model_calls": min(40, 5 + 3 * (rounds + 1)),
        "steps": [
            {
                "id": "build",
                "kind": "skill",
                "skill": "team.build",
                "title": "The team builds it",
                "instructions": ask,
                "params": {
                    "request": ask,
                    "name": name,
                    "project": project,
                    **({"framework": framework} if framework else {}),
                },
            }
        ],
    }


# "Just a quick draft" gets one round of fixes; "make it polished" five; anything else three.
QUICK = re.compile(r"\b(quick(?:ly)?|rough|draft|prototype|mvp|just a demo)\b", re.I)
THOROUGH = re.compile(
    r"\b(polish(?:ed)?|production|perfect|robust|thorough(?:ly)?|bullet-?proof|high[- ]quality)\b",
    re.I,
)
MORE_ROUNDS = 2  # what "Keep fixing" buys
BUILD_CEILING = 60


def rounds_for(request: str) -> int:
    if QUICK.search(request):
        return 1
    return 5 if THOROUGH.search(request) else 3


# ---- Mavo's own steps -----------------------------------------------------------------------------


FRAMEWORK = re.compile(r"\b(react|vue)(?:\.?js)?\b", re.I)
USES = {"react": "unpkg.com/react@18.3.1", "vue": "unpkg.com/vue@3.5.13"}


class ProjectParams(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    project: str = Field(min_length=1, max_length=48)
    framework: str | None = Field(default=None, pattern=r"^(react|vue)$")


def blocks_in(text: str) -> tuple[str, dict[str, str]]:
    from mavo.crew_workers import split_files

    return split_files(text or "")


# A change to a file that is already there, so a fixer of a big app sends a few lines, not the
# whole file again (a production-sized page rewritten in full ran out of reply room).
EDIT = re.compile(
    r"^[ \t]*EDIT[ \t]+`?([^\s`]+)`?[ \t]*\r?\n<<<<<<< FIND[ \t]*\r?\n(.*?)\r?\n=======[ \t]*\r?\n"
    r"(.*?)\r?\n?>>>>>>> REPLACE[ \t]*$",
    re.M | re.S,
)
EDIT_RULES = (
    "To change a file that already exists, do NOT write it again: send only the changes, each "
    "exactly as\nEDIT index.html\n<<<<<<< FIND\n<the lines now in the file, copied exactly "
    "from INPUT, enough of them to be unique>\n=======\n<the lines that replace them>\n"
    ">>>>>>> REPLACE\nas many as needed, each small. Write a whole file (```file:...```) only "
    "for a new file. "
)


def apply_edit(text: str, find: str, new: str) -> str | None:
    """The file with one change made, or None when what it should replace is not there. Exact
    first; then line by line ignoring indentation, which models often get wrong."""
    if find and find in text:
        return text.replace(find, new, 1)
    want = [line.strip() for line in find.strip("\n").split("\n")]
    if not any(want):
        return None
    lines = text.split("\n")
    for i in range(len(lines) - len(want) + 1):
        if [line.strip() for line in lines[i : i + len(want)]] == want:
            return "\n".join([*lines[:i], *new.strip("\n").split("\n"), *lines[i + len(want) :]])
    return None


async def save_edits(projects, rule, params, folder, edits, files, notes, cut) -> str:
    current = dict(files)
    missed: list[str] = []
    made = 0
    for path, find, new in edits:
        path = path.strip().lstrip("/\\")
        if path not in current:
            try:
                target = projects.locate(folder, path, must_exist=True)
                current[path] = target.read_text(encoding="utf-8")
            except (ValueError, OSError):
                missed.append(f"{path} does not exist")
                continue
        changed = apply_edit(current[path], find, new)
        if changed is None:
            missed.append(f"a FIND text is not in {path}: {find.strip()[:80]!r}")
            continue
        current[path], made = changed, made + 1
    for path, content in current.items():
        await asyncio.to_thread(
            projects.write, {"project": params.project, "path": path, "content": content}, rule
        )
    said = " ".join(
        line.strip() for line in notes.splitlines() if re.match(r"\s*(NOTES|CHANGES):", line)
    )
    out = f"Saved in {folder}: {made} change{'s' if made != 1 else ''} to {', '.join(current) or 'nothing'}."
    if missed:
        out += f" Could not apply {len(missed)}: " + "; ".join(missed[:4]) + "."
    if cut:
        out += " The reply was cut off, so its last change was not made."
    return out + (f" {said[:400]}" if said else "")


CODE_FILE = re.compile(r"\.(html?|jsx?|tsx?|css|py|json|vue|svelte)$", re.I)
# A line that stands in for code instead of being code: "...", "// ...", "<style>...</style>",
# "// rest of the code", "/* same as before */". A model short of room writes these, and the
# page they make is blank ("The page opened but shows nothing" for a 567-byte React app).
ELIDED_LINE = re.compile(
    r"^\s*(?:(?://|#|/\*|<!--)\s*)?(?:\.\.\.|\u2026)\s*(?:\*/|-->)?\s*$"
    r"|<(style|script)[^>]*>\s*(?:\.\.\.|\u2026)\s*</\1>"
    r"|(?://|#|/\*|<!--|\{/\*)\s*\.{0,3}\s*(?:rest of (?:the )?(?:code|file|styles?|component)|remaining (?:code|styles?)"
    r"|same as (?:before|above)|unchanged|existing code|code goes here|omitted for brevity|truncated|etc\.?\s*$)",
    re.I,
)


def elided(files: dict[str, str], edits: list | tuple = ()) -> str | None:
    """Where a reply left code out, said so a person (and the model) can see it; None when every
    file is written out in full. Text like "Loading..." inside the code is fine."""
    pieces = [(path, text) for path, text in files.items() if CODE_FILE.search(path)]
    pieces += [(edit[0], edit[2]) for edit in edits if CODE_FILE.search(edit[0])]
    for path, text in pieces:
        for number, line in enumerate(text.splitlines(), 1):
            if ELIDED_LINE.search(line):
                return f"{path} line {number} is {line.strip()[:60]!r}"
    return None


async def project_save(desktop, rule, owner, params: ProjectParams, inputs=None) -> str:
    """Save the files the Coder (or the Debugger) wrote, as labelled blocks, into the project."""
    from mavo.site_skills import SkillStop

    projects = desktop.projects
    latest = list((inputs or {}).values())[-1] if inputs else ""
    edits = EDIT.findall(latest)
    notes, files = blocks_in(EDIT.sub("", latest))
    made = await asyncio.to_thread(projects.create, {"name": params.project}, rule)
    folder = Path(made["folder"])
    cut_edit = latest.count("<<<<<<< FIND") > len(edits)
    gap = elided(files, edits)
    if gap:
        raise SkillStop(f"The reply left code out ({gap}), so nothing was saved.")
    if edits:
        return await save_edits(projects, rule, params, folder, edits, files, notes, cut_edit)
    if not files:
        # The whole page, fenced but not labelled (```html, or "file:index.html" written outside
        # the fence): it is plainly the app's index.html.
        whole = re.search(
            r"```[\w-]*[ \t]*\r?\n\s*(<!doctype html.*?</html>)\s*\r?\n```", latest, re.I | re.S
        )
        if whole:
            files = {"index.html": whole[1].replace("\r\n", "\n") + "\n"}
    if not files and (re.search(r"```\s*file\s*:", latest, re.I) or cut_edit):
        # A labelled file that never closes: the reply ran out before the end of the file.
        raise SkillStop(
            "The coder's file was cut off before its end (the reply ran out of room), so it was "
            "not saved. Try again; a shorter request helps."
        )
    if not files and "NO CHANGES" not in latest.upper() and "```" in latest:
        # Code that is neither an EDIT nor a labelled file: saying "no changes" would hide it.
        raise SkillStop(
            "The reply had code, but not as EDIT blocks or labelled files (```file:index.html), "
            "so nothing was saved."
        )
    if not files and "NO CHANGES" not in latest.upper() and re.search(r"(?m)^\s*CHANGES:", latest):
        # A real fixer wrote "CHANGES: refined the invalid input handling" and sent no change.
        raise SkillStop("The reply described changes but sent no EDIT blocks, so nothing changed.")
    if not files:
        if (
            "NO CHANGES" in latest.upper()
            or (folder / "index.html").exists()
            or list(folder.glob("*.py"))
        ):
            existing = [p.name for p in projects.files(folder)]
            if existing and "NO CHANGES" in latest.upper():
                return f"No changes needed. Project {folder} has: {', '.join(existing)}."
            if existing:
                # A plan, a list or an explanation, but no files and no EDIT blocks.
                raise SkillStop("The reply sent no files or EDIT blocks, so nothing changed.")
        raise SkillStop("The coder returned no files to save.")
    written = []
    for path, content in files.items():
        await asyncio.to_thread(
            projects.write, {"project": params.project, "path": path, "content": content}, rule
        )
        written.append(f"{path} ({len(content.encode()) // 1024 or 1} KB)")
    said = " ".join(
        line.strip() for line in notes.splitlines() if re.match(r"\s*(NOTES|CHANGES):", line)
    )
    return f"Saved in {folder}: {', '.join(written)}." + (f" {said[:400]}" if said else "")


# Every app: press each visible button once (a timer's Start, a game's Reset...) and see that
# nothing breaks. Script errors are caught by the page listeners.
PRESS_ALL = """async () => {
  const buttons = [...document.querySelectorAll('button, [role=button], input[type=button], input[type=submit]')]
    .filter(b => b.offsetParent !== null && !b.disabled).slice(0, 15);
  for (const b of buttons) { try { b.click(); } catch (e) {} await new Promise(r => setTimeout(r, 150)); }
  return buttons.length;
}"""

FRAMEWORKS = re.compile(
    r"^https://unpkg\.com/(react@18\.3\.1|react-dom@18\.3\.1|@babel/standalone@7\.26\.4|vue@3\.5\.13)/"
)


IS_LIST_APP = """() => {
  const field = [...document.querySelectorAll('input:not([type]), input[type=text], input[type=search], input[type=url], textarea')]
    .find(e => e.offsetParent !== null);
  const list = document.querySelector('ul, ol, [role=list], table tbody');
  const adds = [...document.querySelectorAll('button, [role=button], input[type=submit]')]
    .some(b => /^\\W*(add|create|\\+)/i.test((b.innerText || b.value || b.getAttribute('aria-label') || '').trim()));
  return !!field && (!!list || adds);
}"""


NUMERIC_FIELD = r"""e => {
  if (/^(decimal|numeric)$/.test(e.inputMode || '')) return true;
  const label = (e.labels && e.labels[0] ? e.labels[0].innerText : '') || '';
  const words = [e.name, e.id, e.getAttribute('aria-label'), label].join(' ');
  if (/\b(amount|price|cost|qty|quantity|total|sum|budget|count|age|hours|minutes)\b/i.test(words)) return true;
  return /^\s*[$€£₹]?\s*\d+([.,]\d+)?\s*$/.test(e.placeholder || '');
}"""


async def add_an_item(page) -> dict:
    """Add an item to a list app as a person does: real typing and keys (a React field ignores a
    value set by a script), Enter first, then the Add button."""
    text = "Mavo test item"
    if not await page.evaluate(IS_LIST_APP):
        return {"tried": False}
    # Fill the whole form as a person would (an expense needs an amount, not only a name): the
    # first text box gets the item, the others something sensible for their kind.
    sample = {
        "number": "42",
        "date": "2026-11-05",
        "email": "test@example.com",
        "tel": "9876543210",
        "url": "https://example.com",
    }
    boxes = page.locator("input:visible, textarea:visible, select:visible")
    field, named = None, False
    # A box that wants a web address (a news app's "Add feed") is given one; the item to look
    # for on the page is then that address.
    url_box = None
    # A search or filter box is not where things are added: an RSS reader's search box was typed
    # into and "Typing an item ... did NOT put it on the page" failed a working app. Skipped here,
    # and tested as a search instead (try_search).
    searches = 0
    for n in range(min(await boxes.count(), 8)):
        if await boxes.nth(n).evaluate(SEARCH_FIELD):
            await boxes.nth(n).evaluate("e => e.setAttribute('data-mavo-filled', '')")
            searches += 1
    for n in range(min(await boxes.count(), 8)):  # only when it is the first box to type in
        box = boxes.nth(n)
        if await box.get_attribute("data-mavo-filled") is not None:
            continue
        if await box.evaluate(TYPED_IN):
            url_box = box if await box.evaluate(URL_FIELD) else None
            break
    if url_box is not None:
        text = "https://example.com/mavo-test-feed.xml"
        await url_box.evaluate("e => e.setAttribute('data-mavo-filled', '')")
        await url_box.click()
        await url_box.press_sequentially(text, delay=5)
        field, named = url_box, True
    for n in range(min(await boxes.count(), 8)):
        box = boxes.nth(n)
        if await box.get_attribute("data-mavo-filled") is not None:
            continue
        kind = ((await box.get_attribute("type")) or "text").lower()
        tag = await box.evaluate("e => e.tagName")
        if tag == "SELECT":
            values = await box.evaluate("e => [...e.options].map(o => o.value).filter(Boolean)")
            if values:
                await box.select_option(values[0])
            continue
        if kind in {"checkbox", "radio", "button", "submit", "hidden", "file", "range", "color"}:
            continue
        if kind in sample:
            await box.fill(sample[kind])
            continue
        # A plain text box that wants a number (an amount with placeholder "25.50", inputmode
        # decimal, a label like Price or Qty) gets a number, as a person would type.
        if await box.evaluate(NUMERIC_FIELD):
            await box.click()
            await box.press_sequentially("42", delay=10)
            continue
        await box.click()
        await box.press_sequentially(text if not named else "Mavo note", delay=10)
        field, named = (field or box), True
    if field is None:
        return {"tried": False, "search": bool(searches)}
    # An added address may be shown as its site's name only.
    marks = [text, "example.com"] if url_box is not None else [text]

    async def shown() -> bool:
        body = await page.locator("body").inner_text()
        return any(mark in body for mark in marks)

    await field.press("Enter")
    await asyncio.sleep(0.4)
    if not await shown():
        add = page.get_by_role("button", name=re.compile(r"^\W*(add|create|\+)", re.I)).first
        if await add.count():
            await add.click()
            await asyncio.sleep(0.4)
    return {"tried": True, "added": await shown(), "marks": marks}


SEARCH_FIELD = r"""e => {
  if ((e.type || '').toLowerCase() === 'search' || e.getAttribute('role') === 'searchbox') return true;
  const label = (e.labels && e.labels[0] ? e.labels[0].innerText : '') || '';
  const words = [e.name, e.id, e.getAttribute('aria-label'), e.placeholder, label].join(' ');
  return /\b(search|filter|find|look ?up)\b/i.test(words);
}"""


async def try_search(page) -> str | None:
    """A search box, used as a person would: type a word from the first item and see the list
    narrow to the items that have it. None when the page has no search box."""
    boxes = page.locator("input:visible, textarea:visible")
    box = None
    for n in range(min(await boxes.count(), 8)):
        if await boxes.nth(n).evaluate(SEARCH_FIELD):
            box = boxes.nth(n)
            break
    if box is None:
        return None
    items = page.locator("li:visible, [role=listitem]:visible, article:visible")
    before = await items.count()
    if before < 2:
        return (
            "It has a search box; there were no items to search in the test (live data is "
            "not loaded there)."
        )
    texts = [t.lower() for t in await items.all_inner_texts()]
    word = next(
        (w for w in re.findall(r"[A-Za-z]{5,}", texts[0]) if sum(w in t for t in texts) < before),
        None,
    )
    if not word:
        return "It has a search box; the items were too alike to try it."
    page_before = await page.locator("body").inner_text()
    await box.click()
    await box.press_sequentially(word, delay=20)
    await asyncio.sleep(0.8)
    after = await items.count()
    page_after = await page.locator("body").inner_text()
    shown = [t.lower() for t in await items.all_inner_texts()]
    if after and after < before and all(word in t for t in shown):
        return f'Searching for "{word}" narrowed the list from {before} to {after}.'
    if page_after.replace(word, "") != page_before.replace(word, ""):
        # It searches something else on the page (an RSS reader's articles, not its list of
        # sources): the page reacted, so it is not a failure.
        return f'Searching for "{word}" changed the page (what it searches may not be loaded in the test).'
    return f'Searching for "{word}" did NOT change anything on the page ({before} items before and after).'


TYPED_IN = (
    "e => e.tagName === 'TEXTAREA' || /^(text|search|url|)$/i.test(e.getAttribute('type') || '')"
)
URL_FIELD = r"""e => {
  if ((e.type || '').toLowerCase() === 'url') return true;
  if (!/^(text|search|)$/i.test(e.getAttribute('type') || '')) return false;
  const label = (e.labels && e.labels[0] ? e.labels[0].innerText : '') || '';
  const words = [e.name, e.id, e.getAttribute('aria-label'), e.placeholder, label].join(' ');
  return /https?:\/\/|\b(url|feed|rss|link|website|web address)\b/i.test(words);
}"""


def allowed_in_test(url: str) -> bool:
    return url.startswith("file:") or bool(FRAMEWORKS.match(url))


PINNED = {"react": "18.3.1", "react-dom": "18.3.1", "@babel/standalone": "7.26.4", "vue": "3.5.13"}
CDN_BUILD = re.compile(
    r"^https://(?:unpkg\.com|cdn\.jsdelivr\.net/npm)/(@babel/standalone|react-dom|react|vue)@(\d+)[\w.-]*(/[^?#]*)?"
)


def pinned_build(url: str) -> str | None:
    """The pinned build to test with, for a framework the app loads at another version of the same
    major (a real build loaded Babel 7.24.7: blocked, the page was blank in the test, and five
    rounds of fixes went to "shows nothing" while it worked in a browser)."""
    found = CDN_BUILD.match(url)
    if not found or found[2] != PINNED[found[1]].split(".")[0]:
        return None
    return f"https://unpkg.com/{found[1]}@{PINNED[found[1]]}{found[3] or ''}"


async def check_web(page_path: Path) -> list[str]:
    from playwright.async_api import async_playwright

    from mavo.desktop_actions import installed_browsers

    browsers = installed_browsers()
    if not browsers:
        return ["Could not test: no Chromium browser (Edge or Chrome) is installed."]
    report, errors = [], []
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            executable_path=next(iter(browsers.values())), headless=True
        )
        try:
            page = await browser.new_page()
            held: list[str] = []  # what the app asked for on the internet, held back by the test
            page.on("pageerror", lambda e: errors.append(f"Script error: {str(e)[:300]}"))

            def console(m) -> None:
                # A request the test itself held back fails in the console; that is the test's
                # doing, not the app's (a news app fetching its feeds is not broken).
                if m.type != "error" or ((held or missing) and "net::ERR_FAILED" in m.text):
                    return
                errors.append(f"Console error: {m.text[:300]}")

            page.on("console", console)

            missing: list[str] = []  # scripts and styles the page needs that the test held back

            async def route(r) -> None:
                url = r.request.url
                if allowed_in_test(url):
                    await r.continue_()
                    return
                if pinned := pinned_build(url):
                    await r.continue_(url=pinned)
                    return
                if r.request.resource_type in {"script", "stylesheet"}:
                    missing.append(url)
                else:
                    held.append(url)
                await r.abort()

            # Nothing but the page itself and the pinned framework builds it may use (React, Vue
            # from unpkg): no other network, so a test never reaches anywhere else.
            await page.route("**/*", route)
            await page.goto(page_path.as_uri(), wait_until="load", timeout=30000)
            await asyncio.sleep(2.0)  # a React page compiles in the browser first
            visible = (await page.locator("body").inner_text(timeout=5000)).strip()
            report.append(
                "The page opened" + (" and shows content." if visible else " but shows nothing.")
            )
            smoke = await add_an_item(page)
            if smoke.get("tried"):
                report.append(
                    "Typing an item and pressing Enter (or Add) put it on the page."
                    if smoke.get("added")
                    else "Typing an item and pressing Enter or Add did NOT put it on the page."
                )
                if smoke.get("added"):
                    await page.reload(wait_until="load")
                    await asyncio.sleep(0.6)
                    body = await page.locator("body").inner_text()
                    kept = any(mark in body for mark in smoke["marks"])
                    report.append(
                        "After a reload the item is still there."
                        if kept
                        else "After a reload the item is gone (not saved)."
                    )
            await page.reload(wait_until="load")
            await asyncio.sleep(0.6)
            searched = await try_search(page)
            if searched:
                report.append(searched)
            # Then every button once, from a fresh page: a crash shows as a script error.
            await page.reload(wait_until="load")
            await asyncio.sleep(0.5)
            pressed = await page.evaluate(PRESS_ALL)
            await asyncio.sleep(1.2)
            if pressed:
                report.append(f"Pressed each of its {pressed} buttons once.")
            for url in dict.fromkeys(missing):
                report.insert(
                    0,
                    f"Its script {url[:140]} could NOT load in the test: only the pinned React, "
                    "ReactDOM, Babel and Vue builds can (see the coder's rules); load those or "
                    "inline it.",
                )
            if held:
                hosts = list(dict.fromkeys(urlsplit(u).hostname or u[:40] for u in held))
                report.append(
                    f"It asked the internet for live data ({', '.join(hosts[:3])}); the test keeps "
                    "the internet out, so that data was not tried. Its handling of a failed "
                    "request was."
                )
        finally:
            await browser.close()
    # The same error ten times is one problem.
    counted: dict[str, int] = {}
    for error in errors:
        counted[error] = counted.get(error, 0) + 1
    merged = [e if n == 1 else f"{e} ({n} times)" for e, n in counted.items()]
    return merged[:8] + report


def check_python(folder: Path) -> list[str]:
    """Compiled, never run: a syntax check only."""
    report = []
    for path in sorted(folder.rglob("*.py")):
        try:
            compile(path.read_text(encoding="utf-8"), str(path.name), "exec")
            report.append(f"{path.name}: compiles.")
        except SyntaxError as exc:
            report.append(f"{path.name}: SYNTAX ERROR line {exc.lineno}: {exc.msg}")
    return report


# A line of the test report that is a problem: "SYNTAX ERROR" and "Script error" alike; "NOT"
# only as the report writes it.
PROBLEM = re.compile(r"(?i:error)|NOT|nothing|gone")


async def app_check(desktop, rule, owner, params: ProjectParams, inputs=None) -> str:
    """Test what was built, with no model, and report it plainly."""
    from mavo.site_skills import SkillStop

    folder = await asyncio.to_thread(desktop.projects.project_dir, params.project, rule)
    pages = sorted(folder.rglob("index.html")) or sorted(folder.rglob("*.html"))
    lines: list[str] = []
    if pages:
        if params.framework:
            source = pages[0].read_text(encoding="utf-8", errors="replace")
            if USES[params.framework] not in source:
                lines.append(
                    f"You asked for {params.framework.title()}, but the app does NOT use it."
                )
        lines += await check_web(pages[0])
        opener = f"Open {pages[0]} in your browser to use it."
    elif list(folder.rglob("*.py")):
        lines += check_python(folder)
        opener = f"Run main.py in {folder} (Mavo asks before running anything)."
    else:
        raise SkillStop("There is nothing in the project to test yet.")
    problems = [line for line in lines if PROBLEM.search(line)]
    verdict = "TEST: problems found." if problems else "TEST: passed."
    return "\n".join([f"Built in {folder}. {opener}", verdict, *(f"- {line}" for line in lines)])


def report_json(value) -> str:
    return json.dumps(value, ensure_ascii=False)


# ---- the team, picked for the job, round after round ----------------------------------------------


LEAD = (
    "Role: tech lead. Plan the build and pick the team THIS job needs.\n"
    + ROLES["architect"].replace("Role: software architect. ", "First the design. ")
    + "\nThen a line TEAM: and one line per member, exactly as\n"
    "- builder | <role> | <their part>\n- reviewer | <role> | <what they check>\n"
    "- fixer | <role> | <how they fix>\n"
    "Pick only who this job needs: 1 to 3 builders, who work in turn (the first writes every "
    "file; later ones improve their part of it, e.g. a UI designer after a developer); 0 to 2 "
    "reviewers, each with a different focus (e.g. security for anything with logins or money, "
    "accessibility for a public page, none for a tiny script); exactly one fixer. A tester is "
    "always there: Mavo itself opens the app and tries it, so do not add one. Name each role "
    "for what it does here."
)
TEAM_LINE = re.compile(
    r"^\s*[-*•]?\s*(builder|reviewer|fixer)\s*\|\s*([^|\n]{2,40}?)\s*\|\s*([^\n]{3,300})$",
    re.I | re.M,
)


class Member(BaseModel):
    kind: str
    role: str
    job: str


def read_team(lead: str) -> tuple[list[Member], list[Member], Member]:
    """Who the lead picked. A lead that wrote no team gets the usual one. A lead that rewrote its
    team (a real one wrote TEAM twice) is read from its last one."""
    heads = list(re.finditer(r"(?im)^.*\bTEAM:", lead))
    last = lead[heads[-1].start() :] if heads else lead
    found = [
        Member(kind=k.lower(), role=r.strip(), job=j.strip()) for k, r, j in TEAM_LINE.findall(last)
    ]
    if not found:
        found = [
            Member(kind=k.lower(), role=r.strip(), job=j.strip())
            for k, r, j in TEAM_LINE.findall(lead)
        ]
    builders = list({m.role.lower(): m for m in found if m.kind == "builder"}.values())[:3] or [
        Member(kind="builder", role="Developer", job="writes every file of the design")
    ]
    reviewers = list({m.role.lower(): m for m in found if m.kind == "reviewer"}.values())[:2]
    if not found:
        reviewers = [Member(kind="reviewer", role="Reviewer", job="bugs and missing features")]
    fixer = next((m for m in found if m.kind == "fixer"), None) or Member(
        kind="fixer", role="Debugger", job="fixes what the test and the reviews found"
    )
    return builders, reviewers, fixer


def design_of(lead: str) -> str:
    """The design without the team: the members need what to build, not who else is on it."""
    text = TEAM_LINE.sub("", lead)
    return re.sub(r"(?im)^\s*TEAM:\s*$", "", text).strip()


def builder_system(member: Member, first: bool) -> str:
    head = f"Role: {member.role} on a software team. Your part: {member.job}. "
    if first:
        head += (
            "Implement the design in INPUT completely and correctly: every feature, no "
            "placeholders, no TODOs, nothing left for later. "
        )
        return head + FILE_RULES
    return head + (
        "The project's files so far are in INPUT. Do your part on them without breaking anything "
        "that works, or reply exactly NO CHANGES if your part needs nothing. "
        + EDIT_RULES
        + "After the changes, one line: CHANGES: what you did."
    )


def reviewer_system(member: Member) -> str:
    return (
        f"Role: {member.role} on a software team. Your focus: {member.job}. Check the files in "
        "INPUT against the design and the test report. WRITE OUT each real problem you found (a "
        "bug, a crash, a missing or broken feature, a risk in your focus, anything the test "
        "report says failed) as one numbered line, exactly:\n"
        "N. [major] or [minor] <what goes wrong, in plain words a non-programmer understands, "
        "under 12 words, no code> — fix: <where and the exact fix, code allowed>\n"
        "major: the app breaks, loses data, or a feature does not work. minor: a rare edge case "
        "or small polish. Never style or taste; most serious first. The fixer only sees what "
        "you write here, so a reply that is only a verdict is useless. Last line: `VERDICT: "
        "fix` if you listed a major problem, otherwise `VERDICT: pass` (minor ones alone pass)."
    )


def fixer_system(member: Member) -> str:
    return (
        f"Role: {member.role} on a software team. {member.job}. Fix every problem the test "
        "report and the reviews in INPUT found, without breaking anything else. Only what you "
        "write as EDIT blocks (or whole files) changes the app: describing a fix does nothing. "
        "If there is nothing to fix, reply exactly: NO CHANGES. "
        + EDIT_RULES
        + "After the changes, one line: CHANGES: what you fixed."
    )


TAGGED = re.compile(r"(?im)^[\s*#>-]*(?:\*\*)?\d+[.)]\s*(?:\*\*)?\s*\[(major|minor)\]")


def wants_fix(review: str) -> bool:
    """A fix round only for a real problem: a review that tagged everything [minor] passes,
    whatever its verdict line says (a "Minor: pausing in the last second" held a build up and
    asked the person)."""
    if not re.search(r"VERDICT:\s*fix", review, re.I):
        return False
    tags = [t.lower() for t in TAGGED.findall(review)]
    return not tags or "major" in tags


def plain(point: str, most: int = 90) -> str:
    """A problem as the person reads it: the plain part before the fix, no code, no markup,
    cut at a word, never mid-word."""
    point = re.split(r"\s+[—–-]+\s*fix\s*:|\s+fix\s*:|\.\s+Fix\s*:", point, maxsplit=1, flags=re.I)[
        0
    ]
    point = re.sub(r"\[(major|minor)\]\s*|\*\*|`", "", point, flags=re.I)
    point = re.sub(r"^(bug|minor|major|issue|problem)\s*[:—–-]\s*", "", point, flags=re.I)
    point = re.sub(r"\s+", " ", point).strip(" .:;—–-")
    if len(point) <= most:
        return point
    return point[: point[:most].rfind(" ")].rstrip(" ,;:") + "…"


def first_point(review: str) -> str:
    text = re.sub(r"`?VERDICT:\s*\w+`?", "", review, flags=re.I)
    # The first numbered major problem (else the first numbered one), not whatever musing came
    # before it ("UI/UX issues. Let me think about the invalid state ...").
    numbered = re.search(r"(?m)^[\s*#>-]*(?:\*\*)?\d+[.)]\s*(?:\*\*)?\s*(\[major\].+)$", text, re.I)
    numbered = numbered or re.search(r"(?m)^[\s*#>-]*(?:\*\*)?1[.)]\s*(?:\*\*)?(.+)$", text)
    if numbered:
        return plain(numbered[1]) or "it found a problem"
    lines = [re.sub(r"^[\s\-*#•]*(\d+[.)]\s*)?", "", x).strip() for x in text.splitlines()]
    point = next((x for x in lines if x), "")
    return point[:200] or "it found a problem"


FILES_SHOWN = 150_000  # how much of the project each member is shown

STOP_WORDS = re.compile(
    r"^\s*(stop|no\b|nope|enough|done|leave it|that'?s (fine|enough|ok)|good enough|skip|cancel)",
    re.I,
)


def wants_more(answer: str) -> bool:
    """The person's call, from a button or their own words: "Stop here", "no", "enough" stop;
    "Keep fixing", "yes continue", or an instruction ("fix only the timer bug") go on."""
    return bool(answer.strip()) and not STOP_WORDS.match(answer)


def unusable(saved: str, failed: str | None) -> str:
    """What to tell a fixer whose reply could not (all) be used, or "" when it was fine."""
    if failed and "left code out" in failed:
        return (
            f"Your last reply could not be used: {failed}. Write every changed line in full: no "
            "'...', no 'rest of the code'. Small EDIT blocks are best."
        )
    if failed and "no files or EDIT blocks" in failed:
        return (
            "Your last reply described what to do but changed no file. Send the change itself, as "
            "EDIT blocks (or whole files), or the words NO CHANGES if nothing needs fixing."
        )
    if failed:
        return f"Your last reply could not be used: {failed}. Send small EDIT blocks instead."
    if "Could not apply" in saved or "cut off" in saved:
        tail = saved[
            saved.find(" Could not apply")
            if "Could not apply" in saved
            else saved.find(" The reply") :
        ]
        return (
            f"Part of your last reply could not be used:{tail[:600]} Copy each FIND text exactly "
            "from the files in INPUT, and keep each change small."
        )
    return ""


PLAIN_TEST = (
    (re.compile(r"^(Script|Console) error", re.I), "the page shows a script error"),
    (re.compile(r"SYNTAX ERROR", re.I), "the code has a syntax error"),
    (re.compile(r"did NOT put it on the page"), "adding an item does not work"),
    (re.compile(r"item is gone"), "items are lost after a reload"),
    (re.compile(r"shows nothing"), "the page is blank"),
    (re.compile(r"could NOT load"), "a script it needs does not load"),
    (re.compile(r"did NOT change anything on the page"), "searching does not filter anything"),
    (re.compile(r"does NOT use (\w+)"), r"it does not use \1 yet"),
)


def said_plainly(problems: list[str]) -> list[str]:
    """What the Tester found, as the person reads it: "the page shows a script error", not
    "Script error: undefinedFunction is not defined (2 times)". Repeats become one."""
    out = []
    for problem in problems:
        for pattern, words in PLAIN_TEST:
            found = pattern.search(problem)
            if found:
                problem = found.expand(words) if "\\" in words else words
                break
        out.append(plain(problem, 70))
    return list(dict.fromkeys(out))


def test_problems(report: str) -> list[str]:
    return [
        line[2:] for line in report.splitlines() if line.startswith("- ") and PROBLEM.search(line)
    ]


class BuildTeam:
    """One build, run by the team the lead picked. Each member's turn shows on the task card as
    it happens."""

    def __init__(self, runner, run_id: str, plan, step):
        self.runner, self.run_id, self.plan, self.step = runner, run_id, plan, step
        params = step.params or {}
        self.request = str(params.get("request") or step.instructions or plan.title)[:2000]
        self.params = ProjectParams(
            project=params.get("project") or slug(params.get("name") or self.request),
            framework=params.get("framework"),
        )
        self.desktop = runner.runtime.actions.desktop
        self.owner = f"task:{run_id[:8]}:{step.id}"
        self.thoughts: list[dict] = []  # the current member's thinking, for its step
        self.guidance = ""  # what the person typed when they said to keep fixing
        with runner.factory() as session:
            rules = runner.runtime.actions.rules
            self.write_rule = dict(rules.read(session, "project.create"))
            self.read_rule = dict(rules.read(session, "project.read"))

    # -- the card ----------------------------------------------------------------------------

    def show(self, sid: str, title: str, kind: str) -> None:
        from mavo.database import TaskRun, utcnow

        with self.runner.factory() as session:
            run = session.get(TaskRun, self.run_id)
            kept = [s for s in run.steps if s["id"] not in {self.step.id, sid}]
            run.steps = [
                *kept,
                {
                    "id": sid,
                    "title": title[:120],
                    "kind": kind,
                    "status": "running",
                    "started_at": utcnow().isoformat(),
                },
            ]
            session.commit()

    async def stage(self, sid: str, title: str, kind: str, work) -> str:
        """One member's turn, shown as it runs; its trouble shows on its own line."""
        from mavo.database import utcnow
        from mavo.tasks import Blocked, OverBudget

        self.show(sid, title, kind)
        self.thoughts = []
        try:
            out = await work
        except Blocked as need:
            self.runner.change(self.run_id, sid, status="blocked", question=str(need))
            raise
        except OverBudget:
            self.runner.change(
                self.run_id, sid, status="failed", error="The task's model-call budget ran out."
            )
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.runner.change(self.run_id, sid, status="failed", error=self.runner.explain(exc))
            raise
        self.runner.change(
            self.run_id,
            sid,
            status="done",
            output=out[:6000],
            finished_at=utcnow().isoformat(),
            **({"thinking": self.thoughts} if self.thoughts else {}),
        )
        return out

    def budget(self, calls: int) -> None:
        """Room for the calls still to come (the team is only known once the lead has picked it)."""
        from mavo.database import TaskRun

        with self.runner.factory() as session:
            run = session.get(TaskRun, self.run_id)
            run.budget_calls = min(BUILD_CEILING, max(run.budget_calls, run.used_calls + calls))
            session.commit()

    # -- the work ----------------------------------------------------------------------------

    async def soft(self, sid: str, title: str, kind: str, work) -> tuple[str, str | None]:
        """A turn whose failure does not throw the build away: what went wrong comes back, and
        the person gets the app as it stands with that problem listed."""
        from mavo.tasks import Blocked, OverBudget

        try:
            return await self.stage(sid, title, kind, work), None
        except (Blocked, asyncio.CancelledError):
            raise
        except OverBudget:
            return "", "the task's model-call budget ran out"
        except Exception as exc:
            return "", self.runner.explain(exc).rstrip(".")

    def prompt(self, **parts: str) -> str:
        given = self.runner.inputs_block({k: v for k, v in parts.items() if v}, FILES_SHOWN)
        return f"WHAT THE PERSON WANTS: {self.request}\n\n{given}"

    async def call(self, system: str, prompt: str) -> str:
        """One member's reply, without its thinking; the thinking goes on the member's step."""
        from mavo.thinking import THINK, split_thinking

        answer, self.thoughts = split_thinking(await self.ask_model(system + THINK, prompt))
        return answer

    async def ask_model(self, system: str, prompt: str) -> str:
        from mavo.model_router import LONG_REPLY

        route = self.runner.route()
        self.runner.spend(self.run_id, 1)
        room = LONG_REPLY.set(True)  # whole files need a long reply
        try:
            raw = await asyncio.wait_for(
                self.runner.runtime.router.chat(
                    route.provider,
                    route.model,
                    [{"role": "user", "content": prompt}],
                    system,
                    route.credential_id,
                ),
                timeout=TIMEOUT,
            )
        finally:
            LONG_REPLY.reset(room)
        # Never shortened here: a file cut by Mavo would look like a reply that ran out of room.
        return str(raw).strip()

    async def lead(self) -> str:
        text = await self.call(LEAD, self.prompt())
        asked = re.match(r"^\s*QUESTION:\s*(.+)", text, re.S)
        if not asked:
            return text
        reply = await self.runner.ask_person(
            self.run_id,
            self.plan,
            self.step.model_copy(update={"id": "lead"}),
            asked[1].strip()[:400],
        )
        return await self.call(LEAD, self.prompt() + f"\n\nThe person answered: {reply}")

    async def save(self, text: str) -> str:
        from mavo.site_skills import SkillStop

        try:
            return await project_save(
                self.desktop, self.write_rule, self.owner, self.params, inputs={"work": text}
            )
        except SkillStop as stop:
            raise RuntimeError(str(stop)) from None

    async def test(self) -> str:
        from mavo.site_skills import SkillStop

        try:
            return await app_check(self.desktop, self.read_rule, self.owner, self.params)
        except SkillStop as stop:
            raise RuntimeError(str(stop)) from None

    def files_now(self) -> str:
        """The project as it stands, as the labelled blocks the team writes."""
        projects = self.desktop.projects
        try:
            folder = projects.project_dir(self.params.project, self.read_rule)
        except ValueError:
            return ""
        parts = []
        for path in projects.files(folder):
            text = path.read_text(encoding="utf-8", errors="replace")
            parts.append(f"```file:{path.relative_to(folder).as_posix()}\n{text}\n```")
        return "\n\n".join(parts)

    async def keep_going(self, open_: list[str], done: int, per_round: int) -> bool:
        """Out of rounds with problems left: the person decides, once, on the card."""
        from mavo.tasks import Blocked

        sid = f"ask{done}"
        more = per_round * MORE_ROUNDS
        # Short and plain: what is still wrong, in words, and the choice. No code, never cut
        # mid-word ("... Fix: in `pauseTimer`, if remainingMs is small (e.g., `Math.ceil(remain.").
        left = said_plainly(open_)
        shown = left[:2]
        more_left = f" and {len(left) - 2} more" if len(left) > 2 else ""
        what = "; ".join(shown)
        question = (
            f"Still {'1 problem' if len(left) == 1 else f'{len(left)} problems'}: "
            f"{what[:1].upper() + what[1:]}{more_left}. Keep fixing (about {more} more model "
            "calls) or stop here?"
        )
        self.show(sid, "Your call: keep fixing or stop", "skill")
        try:
            answer = await self.runner.ask_person(
                self.run_id,
                self.plan,
                self.step.model_copy(update={"id": sid}),
                question,
                choices=["Keep fixing", "Stop here"],
            )
        except Blocked:  # nobody answered: keep what was built
            answer = "Stop here"
        keep = wants_more(answer)
        # Typed words that say more than the button ("yes, but only the timer bug") go to the
        # fixer as the person's guidance.
        said = answer.strip()
        self.guidance = said if keep and said.lower() not in {"keep fixing", "yes", "ok"} else ""
        self.runner.change(
            self.run_id, sid, status="done", output="Keep fixing." if keep else "Stopped here."
        )
        if keep:
            self.budget(more)
        return keep

    async def run(self) -> str:
        rounds = rounds_for(self.request)
        lead = await self.stage("lead", "Lead plans it and picks the team", "helper", self.lead())
        design = design_of(lead)
        builders, reviewers, fixer = read_team(lead)
        team = list(dict.fromkeys([*(m.role for m in [*builders, *reviewers, fixer]), "Tester"]))
        self.runner.change(self.run_id, "lead", title="Lead picked the team", team=team)
        self.budget(len(builders) + len(reviewers) * (rounds + 1) + rounds)

        await self.first_build(builders[0], design)
        trouble: str | None = None
        for n, member in enumerate(builders[1:], 2):
            work, trouble = await self.soft(
                f"build{n}",
                f"{member.role} works on it",
                "helper",
                self.call(
                    builder_system(member, False),
                    self.prompt(design=design, files=self.files_now()),
                ),
            )
            if trouble:
                break
            # A later builder's reply that cannot be used leaves the first one's work as it is.
            await self.soft(f"save{n}", "Mavo saves the changes", "skill", self.save(work))
        report = await self.stage("test1", "Tester tries it in a browser", "skill", self.test())

        done, stuck, asking = 0, False, list(reviewers)
        rewritten = False  # one whole rewrite before the team gives up
        reviews: dict[str, str] = {}
        review_points: list[str] = []
        note = ""  # what Mavo tells the fixer about its last reply (an edit that did not fit...)
        while not trouble:
            # Each reviewer that still had something to fix looks again; one that passed is done.
            for i, member in enumerate(asking):
                said, trouble = await self.soft(
                    f"review{done + 1}{'abc'[i]}",
                    f"{member.role} checks it",
                    "helper",
                    self.call(
                        reviewer_system(member),
                        self.prompt(design=design, files=self.files_now(), test_report=report),
                    ),
                )
                if trouble:
                    break
                reviews[member.role] = said
            if trouble:
                break
            asking = [m for m in reviewers if wants_fix(reviews.get(m.role, ""))]
            review_points = list(dict.fromkeys(first_point(reviews[m.role]) for m in asking))
            open_ = test_problems(report) + review_points
            if not open_:
                break
            if done >= rounds:
                if not await self.keep_going(open_, done, len(asking) + 1):
                    break
                rounds += MORE_ROUNDS
            done += 1
            before = self.files_now()
            for attempt in ("", "b"):  # a reply that could not be used gets one more try
                fix, trouble = await self.soft(
                    f"fix{done}{attempt}",
                    f"{fixer.role} fixes what was found" + (" (again)" if attempt else ""),
                    "helper",
                    self.call(
                        fixer_system(fixer),
                        self.prompt(
                            design=design,
                            files=before,
                            test_report=report,
                            mavo_note=note,
                            the_person_says=self.guidance,
                            **{f"review by {m.role}": reviews[m.role] for m in asking},
                        ),
                    ),
                )
                if trouble:
                    break
                saved, failed = await self.soft(
                    f"resave{done}{attempt}", "Mavo saves the fixes", "skill", self.save(fix)
                )
                note = unusable(saved, failed)
                if self.files_now() != before or not note:
                    break
            if trouble:
                break
            if self.files_now() == before:  # the fix changed nothing
                if not rewritten:
                    # Before giving up on an app that does not work (a blank page, a script
                    # error): the first builder writes it again, whole, from the design.
                    rewritten = True
                    problems = "; ".join(open_)[:600]
                    work, trouble = await self.soft(
                        f"rewrite{done}",
                        f"{builders[0].role} writes it again, from the start",
                        "helper",
                        self.call(
                            builder_system(builders[0], True),
                            self.prompt(
                                design=design,
                                mavo_note=(
                                    f"The app does not work ({problems}) and fixing it in place "
                                    "has not helped. Write the WHOLE app again from the design: "
                                    "every file complete, no '...', no placeholders."
                                ),
                            ),
                        ),
                    )
                    if not trouble:
                        await self.soft(
                            f"resave{done}r", "Mavo saves the new version", "skill", self.save(work)
                        )
                if self.files_now() == before or trouble:
                    stuck, done = True, done - 1
                    break
            retested, trouble = await self.soft(
                f"test{done + 1}", "Tester tries it again", "skill", self.test()
            )
            report = retested or report  # a test that could not run leaves the last report
        return self.result(report, review_points, done, stuck, trouble)

    async def first_build(self, member: Member, design: str) -> None:
        """The first builder writes every file. A reply that ran out of room is asked for once
        more, shorter; a second one that does not fit ends the build."""
        note = ""
        for attempt in ("", "b"):
            work = await self.stage(
                f"build1{attempt}",
                f"{member.role} writes it" + (" (shorter)" if attempt else ""),
                "helper",
                self.call(builder_system(member, True), self.prompt(design=design, mavo_note=note)),
            )
            try:
                await self.stage(
                    f"save1{attempt}", "Mavo saves the files", "skill", self.save(work)
                )
                return
            except RuntimeError as exc:
                if "left code out" in str(exc) and not attempt:
                    note = (
                        f"Your last reply left code out ({str(exc).split('(', 1)[-1].split(')', 1)[0]}), "
                        "so nothing was saved. Write EVERY file in full: no '...', no 'rest of "
                        "the code', no placeholders. Keep it compact instead: concise CSS, no "
                        "comments, short sample data."
                    )
                    continue
                if "left code out" in str(exc):
                    raise RuntimeError(
                        "The model kept leaving code out ('...') instead of writing it, so nothing "
                        "was saved. Try again, or choose a stronger model for builds."
                    ) from None
                if attempt or "cut off" not in str(exc):
                    raise
                note = (
                    "Your last reply was cut off before the end of the file, so nothing was "
                    "saved. Write the whole app again, complete but compact: concise CSS, no "
                    "comments, no long sample data."
                )

    @staticmethod
    def result(
        report: str, review_points: list[str], done: int, stuck: bool, trouble: str | None = None
    ) -> str:
        lines = report.splitlines()
        if (review_points or trouble) and "TEST: passed." in lines:
            lines[lines.index("TEST: passed.")] = "TEST: problems found."
        lines += [f"- Still open: {point}" for point in review_points]
        if done:
            lines.append(f"- Fixed in {done} round{'s' if done != 1 else ''}.")
        if stuck and (review_points or test_problems(report)):
            lines.append("- Still open: the last fix changed nothing, so the team stopped.")
        if trouble:
            lines.append(
                f"- Still open: the team stopped early, {trouble[0].lower() + trouble[1:]}."
            )
        return "\n".join(lines)


# ---- opening what was built -------------------------------------------------------------------


class OpenBuilt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    what: str = Field(default="app", pattern=r"^(app|folder)$")


def build_open_api(runtime):
    """Open a built app in the default browser, or its folder, from the task card. Only a project
    inside Mavo's projects folder, and only its index.html or the folder itself."""
    import os

    from fastapi import APIRouter, HTTPException

    router = APIRouter()

    @router.post("/projects/{name}/open", status_code=204)
    def open_built(name: str, payload: OpenBuilt):
        projects = runtime.actions.desktop.projects
        with runtime.factory() as session:
            rule = dict(runtime.actions.rules.read(session, "project.read"))
        try:
            folder = projects.project_dir(name, rule)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from None
        target = folder
        if payload.what == "app":
            pages = sorted(folder.glob("index.html")) or sorted(folder.glob("*.html"))
            if not pages:
                raise HTTPException(404, "This project has no page to open")
            target = pages[0]
        if not hasattr(os, "startfile"):
            raise HTTPException(501, "Opening is available on Windows")
        os.startfile(str(target))  # noqa: S606 - a file Mavo itself wrote, in its projects folder

    return router
