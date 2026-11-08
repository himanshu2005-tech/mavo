# ruff: noqa: E501
"""Scoped desktop tools. No shell interpreters or personal browser profiles. Scripts run only as
project runs (see projects.py): inside a Mavo project folder, after the person's approval."""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import ipaddress
import os
import re
import shutil
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import psutil
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mavo.browser_proxy import PublicBrowserProxy, same_site
from mavo.projects import Projects
from mavo.session_keeper import free_port, keep_session_cookies


class ToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TerminalArgs(ToolArgs):
    command: Literal[
        "git.status",
        "git.diff",
        "git.log",
        "git.branches",
        "python.version",
        "node.version",
        "system.info",
    ]
    folder: str = Field(min_length=1, max_length=1024)


class BrowserOpen(ToolArgs):
    url: str = Field(min_length=1, max_length=2048)

    @field_validator("url")
    @classmethod
    def public_https(cls, value):
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.port not in {None, 443}
        ):
            raise ValueError("Use a public HTTPS URL without credentials")
        return value


# Labels Mavo will never click while finding a page, whatever the Crew asks.
DANGEROUS = re.compile(
    r"\b(?:delete|remove|sign ?out|log ?out|logout|pay|purchase|buy|checkout|subscribe|unsubscribe"
    r"|cancel|confirm|top ?up|deactivate|revoke|reset|submit|send|apply|withdraw|transfer|order"
    r"|add (?:credits?|funds?|money|balance)|recharge|upgrade|renew)\b",
    re.I,
)
# A dialog that warns about something irreversible is declined; ordinary "Are you sure?" is accepted.
DESTRUCTIVE_DIALOG = re.compile(
    r"\b(?:delete|remove|erase|permanent(?:ly)?|cannot be undone|can't be undone|deactivate"
    r"|close (?:your )?account|cancel (?:your )?(?:subscription|order)|pay|purchase|buy|checkout"
    r"|transfer|withdraw|unsubscribe)\b",
    re.I,
)
NAV_SYNONYMS = {
    "credits": {"credit", "balance", "billing", "usage", "wallet"},
    "credit": {"credits", "balance", "billing", "usage", "wallet"},
    "balance": {"credits", "billing", "wallet", "usage"},
    "billing": {"payments", "invoices", "plan", "credits"},
    "profile": {"account", "settings", "user"},
    "account": {"profile", "settings"},
    "settings": {"preferences", "account", "configuration"},
    "docs": {"documentation", "guide", "api", "help"},
    "pricing": {"plans", "price", "billing"},
    "jobs": {"careers", "openings", "listings", "placements", "opportunities"},
    "messages": {"inbox", "chat", "mail"},
    "orders": {"purchases", "history"},
    "usage": {"activity", "logs", "analytics"},
    "keys": {"api", "tokens", "credentials"},
}
OPENER = re.compile(r"\b(?:profile|account|menu|user|avatar|settings|more|options|me)\b", re.I)
# A link that starts another program (mailto:, tel:, an app's own scheme), never a page of the
# site. The browser hands these to the operating system, which opens the person's mail app.
APP_LINK = re.compile(r"^\s*(?!https?:|javascript:)[a-z][a-z0-9+.-]*:", re.I)
EMAIL_LABEL = re.compile(r"^\S+@\S+\.\S+$")

SEARCH_HOST = "html.duckduckgo.com"


class BrowserSearch(ToolArgs):
    """A web search through one fixed engine. The model supplies words, never a URL."""

    query: str = Field(min_length=1, max_length=300)

    @field_validator("query")
    @classmethod
    def plain_words(cls, value):
        if any(ord(ch) < 32 for ch in value):
            raise ValueError("Search with plain words")
        return " ".join(value.split())


def search_url(query):
    from urllib.parse import quote_plus

    return f"https://{SEARCH_HOST}/html/?q={quote_plus(query)}"


class BrowserNavigate(ToolArgs):
    """Where to go, in words ("credits", "billing", "my profile"), not how to get there."""

    goal: str = Field(min_length=2, max_length=80)

    @field_validator("goal")
    @classmethod
    def plain_goal(cls, value):
        value = " ".join(value.split())
        if any(ord(ch) < 32 for ch in value) or DANGEROUS.search(value):
            raise ValueError("Name a page to find, such as credits or settings")
        return value


class BrowserRead(ToolArgs):
    scroll: bool = False  # scroll down first so lazily loaded rows and cards appear


class BrowserElement(ToolArgs):
    """Point at something on the page: a `ref` from the latest browser.read (preferred, exact),
    or a CSS `selector` when a Crew was set up with one."""

    ref: str | None = Field(default=None, pattern=r"^(?:f[0-9]{1,2}_)?e[0-9]{1,4}$")
    selector: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def one_target(self):
        if bool(self.ref) == bool(self.selector):
            raise ValueError("Give either ref or selector")
        return self


class BrowserFill(BrowserElement):
    text: str = Field(max_length=4000)


class BrowserSelect(BrowserElement):
    option: str = Field(min_length=1, max_length=200)


class BrowserDrag(BrowserElement):
    """Drag the element (`ref` or `selector`) either onto another element (`to_ref` or
    `to_selector`) or by a distance in pixels (`dx`, `dy`), such as a slider handle."""

    to_ref: str | None = Field(default=None, pattern=r"^(?:f[0-9]{1,2}_)?e[0-9]{1,4}$")
    to_selector: str | None = Field(default=None, min_length=1, max_length=500)
    dx: int | None = Field(default=None, ge=-3000, le=3000)
    dy: int | None = Field(default=None, ge=-3000, le=3000)

    @model_validator(mode="after")
    def one_destination(self):
        onto = bool(self.to_ref) + bool(self.to_selector)
        by = self.dx is not None or self.dy is not None
        if onto + by != 1:
            raise ValueError("Give one destination: to_ref, to_selector, or dx/dy")
        return self


class BrowserPress(ToolArgs):
    key: Literal["Enter", "Tab", "Escape", "ArrowDown", "ArrowUp", "PageDown", "PageUp", "Space"]


class BrowserScroll(ToolArgs):
    direction: Literal["down", "up", "top", "bottom"] = "down"


class BrowserWait(ToolArgs):
    seconds: float = Field(default=2, ge=0.5, le=15)
    text: str | None = Field(default=None, max_length=100)  # stop early once this appears


class WindowTarget(ToolArgs):
    window_id: int = Field(gt=0)
    process_id: int = Field(gt=0)
    process_started: float = Field(gt=0)


class WindowText(WindowTarget):
    text: str = Field(min_length=1, max_length=2000)


class WindowKey(WindowTarget):
    key: Literal["enter", "tab", "escape", "ctrl+a"]


DESKTOP_MODELS = {
    "terminal.run": TerminalArgs,
    "browser.open": BrowserOpen,
    "browser.search": BrowserSearch,
    "browser.navigate": BrowserNavigate,
    "browser.read": BrowserRead,
    "browser.click": BrowserElement,
    "browser.double_click": BrowserElement,
    "browser.hover": BrowserElement,
    "browser.drag": BrowserDrag,
    "browser.fill": BrowserFill,
    "browser.select": BrowserSelect,
    "browser.press": BrowserPress,
    "browser.scroll": BrowserScroll,
    "browser.wait": BrowserWait,
    "browser.close": ToolArgs,
    "windows.list": ToolArgs,
    "windows.focus": WindowTarget,
    "windows.minimize": WindowTarget,
    "windows.type": WindowText,
    "windows.key": WindowKey,
    "windows.sleep": ToolArgs,
    "windows.shutdown": ToolArgs,
}


def enable_shutdown_privilege() -> bool:
    """Switch on this process's SeShutdownPrivilege (held but off by default): Windows refuses to
    sleep or shut down without it."""
    from ctypes import wintypes

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)

    class Luid(ctypes.Structure):
        _fields_ = [("low", wintypes.DWORD), ("high", wintypes.LONG)]

    class LuidAndAttributes(ctypes.Structure):
        _fields_ = [("luid", Luid), ("attributes", wintypes.DWORD)]

    class TokenPrivileges(ctypes.Structure):
        _fields_ = [("count", wintypes.DWORD), ("privileges", LuidAndAttributes * 1)]

    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    advapi.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi.LookupPrivilegeValueW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p]
    advapi.AdjustTokenPrivileges.argtypes = [
        wintypes.HANDLE,
        wintypes.BOOL,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(
        kernel.GetCurrentProcess(), 0x0020 | 0x0008, ctypes.byref(token)
    ):
        return False
    try:
        luid = Luid()
        if not advapi.LookupPrivilegeValueW(None, "SeShutdownPrivilege", ctypes.byref(luid)):
            return False
        wanted = TokenPrivileges(1, (LuidAndAttributes * 1)(LuidAndAttributes(luid, 0x2)))
        ctypes.set_last_error(0)
        done = advapi.AdjustTokenPrivileges(token, False, ctypes.byref(wanted), 0, None, None)
        return bool(done) and ctypes.get_last_error() == 0  # 1300: not all assigned
    finally:
        kernel.CloseHandle(token)


def sleep_computer() -> dict:
    """Put the computer to sleep (Modern Standby on laptops that have only that), and say what
    really happened. The call returns when the computer wakes again."""
    from ctypes import wintypes

    privileged = enable_shutdown_privilege()
    power = ctypes.WinDLL("powrprof", use_last_error=True)
    power.SetSuspendState.argtypes = [ctypes.c_ubyte, ctypes.c_ubyte, ctypes.c_ubyte]
    power.SetSuspendState.restype = ctypes.c_ubyte
    ctypes.set_last_error(0)
    if power.SetSuspendState(0, 0, 0):
        return {"slept": True, "woke": True}
    error = ctypes.get_last_error()
    # Refused: the screen off at least (on a Modern Standby laptop, often the way into standby).
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user.PostMessageW(
        wintypes.HWND(0xFFFF), 0x0112, 0xF170, 2
    )  # WM_SYSCOMMAND, SC_MONITORPOWER, off
    raise ValueError(
        f"Windows did not put the computer to sleep (error {error}"
        + ("" if privileged else "; the shutdown privilege could not be switched on")
        + "). The screen was turned off instead."
    )


def without_www(host):
    return host[4:] if host and host.startswith("www.") else host


def host_allowed(url, domains):
    """May a Crew go to this page? The host must be one it was given, apart from the leading
    "www.": example.com and www.example.com are one site, and many sites redirect from one to the
    other, so a Crew allowed one must not be refused the page it was just sent to."""
    parsed = urlsplit(url)
    return (
        parsed.scheme == "https"
        and (
            parsed.hostname in domains
            or without_www(parsed.hostname) in {without_www(d) for d in domains}
        )
        and parsed.port in {None, 443}
        and not parsed.username
        and not parsed.password
    )


# Some sites cannot be used without the hosts they hand the browser to. Microsoft Forms, for one,
# answers forms.office.com with a redirect to login.microsoftonline.com and lands on
# forms.cloud.microsoft: a Crew allowed only forms.office.com was stopped at the first redirect,
# signed in or not. Allowing such a site allows its own sign-in and landing hosts too.
MICROSOFT_SUFFIXES = (
    "office.com",
    "microsoft.com",
    "cloud.microsoft",
    "microsoftonline.com",
    "live.com",
)
# The sign-in pages and where their scripts and styles come from. Without the second line the page
# loads empty (blank "Sign in to your account") whenever a job is limited to certain sites.
MICROSOFT_SIGN_IN = (
    "login.microsoftonline.com",
    "login.live.com",
    "login.microsoft.com",
    "aadcdn.msauth.net",
    "aadcdn.msftauth.net",
)
MICROSOFT_FORMS = ("forms.office.com", "forms.cloud.microsoft", "forms.microsoft.com")


def with_companions(domains):
    result = list(domains)

    def under(host, suffixes):
        return any(host == s or host.endswith("." + s) for s in suffixes)

    if any(under(d, MICROSOFT_SUFFIXES) for d in domains):
        result += [h for h in MICROSOFT_SIGN_IN if h not in result]
    if any(d in MICROSOFT_FORMS for d in domains):
        result += [h for h in MICROSOFT_FORMS if h not in result]
    return result


async def public_host(host):
    addresses = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    if not addresses or any(
        not ipaddress.ip_address(address[4][0]).is_global for address in addresses
    ):
        raise ValueError("Browser actions cannot access local or private networks")
    return next(
        (address[4][0] for address in addresses if address[0] == socket.AF_INET), addresses[0][4][0]
    )


BROWSERS = {
    # label -> (Windows ProgId prefix, install locations under Program Files / LocalAppData)
    "Edge": ("MSEdgeHTM", [r"Microsoft\Edge\Application\msedge.exe"]),
    "Chrome": ("ChromeHTML", [r"Google\Chrome\Application\chrome.exe"]),
    "Brave": ("BraveHTML", [r"BraveSoftware\Brave-Browser\Application\brave.exe"]),
}


def work_area():
    """The main screen's usable area (left, top, right, bottom), without the taskbar."""
    if os.name != "nt":
        return (0, 0, 1920, 1080)
    from ctypes import wintypes

    rect = wintypes.RECT()
    ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(rect), 0)  # SPI_GETWORKAREA
    return (rect.left, rect.top, rect.right, rect.bottom)


def default_window(area):
    """Where a new sign-in window goes: comfortably inside the screen, never off its edge."""
    left, top, right, bottom = area
    width, height = min(1280, right - left - 120), min(820, bottom - top - 70)
    return (left + 60, top + 30, width, height)


def fit_on_screen(rect, area):
    """None when a window is comfortably on the screen; otherwise a rectangle (x, y, width, height)
    that is. A window left mostly beyond an edge (the off-screen position Mavo's background
    browser uses is remembered by the profile) must not be where the person cannot reach it."""
    left, top, right, bottom = rect
    a_left, a_top, a_right, a_bottom = area
    width, height = right - left, bottom - top
    seen_w = max(0, min(right, a_right) - max(left, a_left))
    seen_h = max(0, min(bottom, a_bottom) - max(top, a_top))
    if width > 0 and height > 0 and seen_w >= 0.6 * width and seen_h >= 0.6 * height:
        return None
    return default_window(area)


def installed_browsers():
    found = {}
    for label, (_, paths) in BROWSERS.items():
        for base in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            root = os.environ.get(base)
            for relative in paths:
                if root and (Path(root) / relative).exists():
                    found[label] = str(Path(root) / relative)
                    break
            if label in found:
                break
    return found


def default_browser():
    """The engine behind the user's default browser, used only as an engine: Mavo always runs it
    with its own private profile, never the user's. If that browser cannot be found (or is
    Firefox, which cannot be driven this way) the first installed Chromium browser is used."""
    progid = ""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice",
        ) as key:
            progid = winreg.QueryValueEx(key, "ProgId")[0]
    except (ImportError, OSError):
        pass
    installed = installed_browsers()
    preferred = next(
        (label for label, (prefix, _) in BROWSERS.items() if progid.startswith(prefix)), None
    )
    for label in [preferred, *BROWSERS]:
        if label in installed:
            return {"label": label, "executable_path": installed[label]}
    # Last resort: let Playwright resolve Edge itself.
    return {"label": "Edge", "channel": "msedge"}


# Text that usually means "still loading" or "genuinely nothing": wait longer before trusting it.
EMPTY_HINT = re.compile(
    r"\b0\s+(?:\w+\s+)?(?:listed|found|results?|items?|jobs?)\b"
    r"|loading|please wait|no (?:results|jobs)",
    re.I,
)
CAPTCHA = re.compile(
    r"verify (?:you(?:'|\u2019)re|you are) (?:a )?human|captcha|are you a robot"
    r"|security check",
    re.I,
)
TRACKING_COOKIE = re.compile(
    r"_ga|_gid|_gat|_gcl|_fb|_hj|__utm|_cl[ik]|_uet|_dd_|ajs_|amp_|mp_|NID$|IDE$|__cf|cf_|"
    r"optanon|cookieconsent|cookie_?(?:consent|notice|policy)|_GRECAPTCHA|visitor|tracking",
    re.I,
)
# Seconds an action may take before the page is declared stuck (the rest get 80).
BROWSER_LIMITS = {"browser.open": 130, "browser.search": 130, "browser.navigate": 170}
SESSION_ENDED = re.compile(
    r"\b(?:session|login|log-?in|sign-?in)\b[^.\n]{0,40}\b(?:timed out|time out|expired|no longer valid)\b"
    r"|\b(?:timed out|expired)\b[^.\n]{0,40}\b(?:sign|log) ?in again"
    r"|please (?:sign|log) ?in again|you (?:have been|were|are) (?:signed|logged) out",
    re.I,
)


# The middle of a single sign-on (the site sends the browser to the identity provider, which sends
# it straight back): such an address is not where the person is going to stay.
AUTH_HOP = re.compile(r"/oauth2?/|/authorize\b|/signin-oidc|/saml2?\b|/sso/|/connect/", re.I)
SIGN_IN_FIELD = "input[type=password], input[type=email], input[name=loginfmt]"

# A page that loaded but reports its own failure: worth one reload before anyone trusts it.
SITE_ERROR = re.compile(
    r"failed to load|something went wrong|error (?:while )?loading|could not (?:load|be loaded)"
    r"|unable to load|try again later|temporarily unavailable",
    re.I,
)


class DesktopActions:
    close_patience = 10  # seconds each step of closing the browser may take before it is ended
    MAX_TABS = 4  # tabs a site may have open at once; one opening more than that is closed

    def __init__(self, root):
        self.root = Path(root)
        self.new_tabs = []
        self.browser_lock = asyncio.Lock()
        self.context = self.playwright = self.page = None
        self.browser_owner = None
        self.active_domains = []
        self.blocked_hosts = []  # pages the browser tried to open outside the allowed sites
        self.guard = None
        self.children = set()
        self.projects = Projects(children=self.children)
        self.pinned_domains = []
        self.scope_guard = None
        self.proxy = None
        self.active_unrestricted = False
        self.active_tasks = set()
        self.interactive_owner = None
        self.interactive_generation = None
        self.interactive_until = 0
        self.probes = {}
        self.engine = None
        self.user_window_flag = False
        self.dialogs = []
        self.open_redirected = False
        self.session_hint = "unknown"  # "in", "out" or "unknown": what the last page said
        self.user_window_port = None  # the sign-in window's debug port while it is open
        self.login_link = ""  # that page's own "Sign in" link on the same site, if it had one
        self.login_lookup = None  # set by the app: hostname -> (username, password) | None

    FILL_LOGIN = """({u, p}) => {
      const visible = el => { const r = el.getBoundingClientRect(), s = getComputedStyle(el);
        return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
      const pw = [...document.querySelectorAll('input[type=password]')].find(visible);
      if (!pw) return false;
      const scope = pw.form || document;
      const user = [...scope.querySelectorAll('input')]
        .filter(el => visible(el)
          && ['text', 'email', 'tel', ''].includes((el.getAttribute('type') || '').toLowerCase())
          && (el.compareDocumentPosition(pw) & Node.DOCUMENT_POSITION_FOLLOWING)).pop();
      const set = (el, v) => {
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, v);
        el.dispatchEvent(new Event('input', {bubbles: true}));
        el.dispatchEvent(new Event('change', {bubbles: true}));
      };
      if (user) set(user, u);
      set(pw, p);
      const form = pw.form;
      const button = (form || document).querySelector('button[type=submit], input[type=submit]');
      if (button && visible(button)) button.click();
      else if (form) { form.requestSubmit ? form.requestSubmit() : form.submit(); }
      return true;
    }"""

    async def autofill(self):
        """Fill and submit a sign-in form with the user's saved login for this exact site. Plain
        code, not a model action: the password never appears in any result or prompt."""
        if not self.login_lookup or not self.page or self.page.is_closed():
            return False
        parts = urlsplit(self.page.url)
        if parts.scheme != "https":
            return False
        login = self.login_lookup(parts.hostname)
        if not login:
            return False
        try:
            done = await self.page.evaluate(self.FILL_LOGIN, {"u": login[0], "p": login[1]})
            if done:
                await self.page.wait_for_load_state("domcontentloaded", timeout=6000)
            return bool(done)
        except Exception:
            return False

    # True when the element is (inside) a link whose address starts another program, not a page.
    OPENS_APP = """el => { const a = el.closest('a[href]');
      return !!a && !!a.protocol && !['http:', 'https:', 'javascript:'].includes(a.protocol); }"""

    # A box a web app edits itself (contenteditable), not a plain form field.
    RICH_EDITOR = """el => !['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName)
      && (el.isContentEditable || el.getAttribute('role') === 'textbox')"""

    # What sits on top of the element, where a click would land: a dialog or banner by its name.
    COVERED_BY = """el => {
      el.scrollIntoView({block: 'center', inline: 'center'});
      const r = el.getBoundingClientRect();
      if (!r.width || !r.height) return {hidden: true};
      const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      if (!top || top === el || el.contains(top) || top.contains(el)) return null;
      let box = top;
      for (let n = top; n && n !== document.body; n = n.parentElement) {
        const role = n.getAttribute('role');
        if (role === 'dialog' || role === 'alertdialog' || n.getAttribute('aria-modal') === 'true') {
          box = n; break; }
      }
      const name = (box.getAttribute('aria-label') || box.innerText || '').trim()
        .replace(/\\s+/g, ' ').slice(0, 80);
      return {cover: name || box.tagName.toLowerCase()};
    }"""

    # The search engine's results: title, the real address (not the engine's redirect) and the
    # snippet. Ads are left out.
    SEARCH_RESULTS = """() => [...document.querySelectorAll('.result:not(.result--ad)')].map(r => {
      const a = r.querySelector('a.result__a'); if (!a) return null;
      let url = a.getAttribute('href') || '';
      try { const u = new URL(url, location.href); url = u.searchParams.get('uddg') || u.href; }
      catch (e) { return null; }
      const s = r.querySelector('.result__snippet');
      return {title: (a.innerText || '').trim().slice(0, 160), url,
              snippet: s ? (s.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 260) : ''};
    }).filter(x => x && /^https?:/.test(x.url) && !/duckduckgo\\.com/.test(x.url)).slice(0, 8)"""

    async def search_results(self) -> list[dict]:
        """Each search hands back its own results, so a search that is followed by another one
        before a read is not lost (an agent ran five in a row and only ever saw the last)."""
        try:
            return await self.page.evaluate(self.SEARCH_RESULTS)
        except Exception:
            return []

    async def press_on(self, element, double=False):
        """Click as a person would. Something in the way (a banner, a tip, a dialog) is first
        dismissed with Escape, as a person would; if it is still there, the agent is told what it
        is, so its next step closes that instead of clicking the same spot again."""
        click = element.dblclick if double else element.click
        try:
            await click(timeout=8000)
            return
        except Exception as exc:
            if "Timeout" not in type(exc).__name__:
                raise
        await self.page.keyboard.press("Escape")
        await asyncio.sleep(0.5)
        try:
            await click(timeout=4000)
            return
        except Exception as exc:
            if "Timeout" not in type(exc).__name__:
                raise
            found = None
            with contextlib.suppress(Exception):
                found = await element.evaluate(self.COVERED_BY)
            if found and found.get("hidden"):
                raise ValueError(
                    "That element is hidden. Open the menu or section that holds it first, "
                    "then read the page again."
                ) from None
            if found and found.get("cover"):
                raise ValueError(
                    f'Something covers that element: "{found["cover"]}". Close or answer that '
                    "first (its close, OK or Dismiss button), then click again."
                ) from None
            raise

    SCAN = """() => {
      const vis = el => { const r = el.getBoundingClientRect(), s = getComputedStyle(el);
        return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'
          && r.bottom > 0 && r.right > 0; };
      const out = []; let i = 0;
      const sel = 'a[href], button, [role=button], [role=menuitem], [role=tab], [role=link], summary,'
        + ' [aria-haspopup], [aria-expanded], input[type=button]';
      for (const el of document.querySelectorAll(sel)) {
        if (!vis(el) || el.disabled) continue;
        // mailto:, tel: and app links open another program, not a page: never offer them.
        if (el.tagName === 'A' && el.protocol
            && !['http:', 'https:', 'javascript:'].includes(el.protocol)) continue;
        const aria = ((el.getAttribute('aria-label') || '') + ' ' + (el.title || '')).trim();
        const label = (el.innerText || aria || el.getAttribute('alt') || el.value || '')
          .trim().replace(/\\s+/g, ' ').slice(0, 120);
        if (el.tagName === 'A') el.removeAttribute('target');
        el.setAttribute('data-mavo-nav', String(i));
        const r = el.getBoundingClientRect();
        out.push({i, label, aria, href: el.getAttribute('href') || '', tag: el.tagName.toLowerCase(),
          popup: el.hasAttribute('aria-haspopup') || el.getAttribute('aria-expanded') === 'false',
          image: el.querySelector('img,svg') ? 1 : 0, top: Math.round(r.top), left: Math.round(r.left),
          right: Math.round(window.innerWidth - r.right)});
        if (++i >= 300) break;
      }
      return out; }"""

    async def scan(self):
        """Read the page's clickable things, tolerating a navigation that is still in flight."""
        for attempt in range(4):
            try:
                return await self.page.evaluate(self.SCAN)
            except Exception:
                if attempt == 3:
                    raise
                await self.settle(3)
        return []

    @staticmethod
    def nav_score(goal, item):
        text = " ".join([item["label"], item["aria"], item["href"]]).lower()
        if DANGEROUS.search(item["label"] + " " + item["aria"]) or re.search(
            r"logout|signout|sign-out|delete|checkout|purchase", item["href"], re.I
        ):
            return 0
        # An email address or a mailto:/tel: link only shares words with the goal ("billing@...").
        if APP_LINK.match(item["href"]) or EMAIL_LABEL.match(item["label"].strip()):
            return 0
        words = set(re.findall(r"[a-z0-9]+", text))
        goal_words = [w for w in re.findall(r"[a-z0-9]+", goal.lower()) if len(w) > 2]
        synonyms = (
            set().union(*(NAV_SYNONYMS.get(w, set()) for w in goal_words)) if goal_words else set()
        )
        score = 0
        for word in goal_words:
            if word in words:
                score += 10
            elif any(word in token or token in word for token in words if len(token) > 3):
                score += 4
        score += 5 * len(synonyms & words)
        return score + (2 if score and len(item["label"]) <= 30 else 0)

    async def reached(self, goal):
        words = [w for w in re.findall(r"[a-z0-9]+", goal.lower()) if len(w) > 2]
        try:
            heading = await self.page.evaluate(
                "[...document.querySelectorAll('h1,h2,[role=heading]')].slice(0,6)"
                ".map(e => e.innerText).join(' ')"
            )
        except Exception:
            heading = ""
        haystack = (self.page.url + " " + await self.page.title() + " " + heading).lower()
        # A page called "Credits" is where "billing" was meant to lead: a synonym that appears as
        # a whole word counts as arriving, so the search stops instead of wandering on.
        tokens = set(re.findall(r"[a-z0-9]+", haystack))
        synonyms = set().union(*(NAV_SYNONYMS.get(w, set()) for w in words))
        return any(w in haystack for w in words) or bool(synonyms & tokens)

    async def navigate(self, goal, hops=4):
        """Find a page by name the way a person would: look for a link, else open the account or
        menu button and look again, up to a few hops. Pure code, no model call; it never clicks
        anything that deletes, pays, signs out or submits."""
        steps, tried = [], set()
        start = self.page.url
        for _ in range(hops):
            self.check()
            items = await self.scan()
            ranked = sorted(
                (i for i in items if self.nav_score(goal, i) > 0 and i["i"] not in tried),
                key=lambda i: -self.nav_score(goal, i),
            )
            target = ranked[0] if ranked else None
            if not target:
                header = [i for i in items if i["top"] < 140 and i["tag"] in {"a", "button"}][:8]
                for candidate in header:
                    try:
                        await self.page.locator(f'[data-mavo-nav="{candidate["i"]}"]').first.hover(
                            timeout=2000
                        )
                    except Exception:
                        continue
                    await asyncio.sleep(0.35)
                    shown = [
                        i
                        for i in await self.scan()
                        if self.nav_score(goal, i) > 0 and i["i"] not in tried
                    ]
                    if shown:
                        steps.append({"opened": (candidate["label"] or "menu")[:40]})
                        target = max(shown, key=lambda i: self.nav_score(goal, i))
                        break
            if not target:
                openers = sorted(
                    (
                        i
                        for i in items
                        if i["popup"]
                        or (
                            (i["tag"] != "a" or not i["href"] or i["href"].startswith("#"))
                            and OPENER.search(i["label"] + " " + i["aria"])
                        )
                        or (i["image"] and not i["label"] and i["top"] < 160)
                    ),
                    key=lambda i: (
                        not i["popup"],
                        not (i["top"] < 160 and i["right"] < 300),
                        i["top"],
                    ),
                )
                for opener in openers[:5]:
                    if DANGEROUS.search(opener["label"] + " " + opener["aria"]):
                        continue
                    opener_start = self.page.url
                    try:
                        await self.page.locator(f'[data-mavo-nav="{opener["i"]}"]').first.click(
                            timeout=4000
                        )
                    except Exception:
                        continue
                    await asyncio.sleep(0.7)
                    if self.page.url != opener_start:
                        await self.page.go_back(wait_until="domcontentloaded")
                        await self.settle(4)
                        continue
                    after = await self.scan()
                    found = [i for i in after if self.nav_score(goal, i) > 0]
                    if found:
                        steps.append({"opened": (opener["label"] or opener["aria"] or "menu")[:40]})
                        target = max(found, key=lambda i: self.nav_score(goal, i))
                        break
                    await self.page.keyboard.press("Escape")
            if not target:
                break
            tried.add(target["i"])
            before = self.page.url
            try:
                await self.page.locator(f'[data-mavo-nav="{target["i"]}"]').first.click(
                    timeout=6000
                )
            except Exception:
                continue
            await self.settle(8)
            steps.append(
                {
                    "clicked": (target["label"] or target["aria"] or target["href"])[:60],
                    "url": self.page.url,
                }
            )
            if self.page.url == before:
                # A button that goes nowhere ("Get API Key" on a marketing page) is not progress:
                # it once ended five tries on the same homepage, each told "reached".
                steps[-1]["stayed"] = True
                continue
            if await self.reached(goal):
                return {"reached": True, "steps": steps}
        # Arrived only on a different page that matches: a homepage titled "One API for Every AI
        # Model" is not the "API keys" page just because it shares a word.
        if self.page.url != start and await self.reached(goal):
            return {"reached": True, "steps": steps}
        tried_names = ", ".join(
            dict.fromkeys(s.get("clicked") or s.get("opened") or "" for s in steps if s)
        ).strip(", ")
        return {
            "reached": False,
            "steps": steps,
            "note": (
                f"Not found from {self.page.url}"
                + (f" (tried: {tried_names})" if tried_names else "")
                + ". Do not ask for the same name again: try the name of the page that holds it "
                "(credits, billing, usage, settings, dashboard, account), or say it was not found."
            ),
        }

    SNAPSHOT = r"""(prefix) => {
      const clean = t => (t || '').replace(/\s+/g, ' ').trim();
      const vis = el => { const r = el.getBoundingClientRect(), s = getComputedStyle(el);
        return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
      // Open shadow roots are part of the page: look inside them too.
      const deep = (sel, root = document) => {
        const out = [...root.querySelectorAll(sel)];
        for (const host of root.querySelectorAll('*')) if (host.shadowRoot) out.push(...deep(sel, host.shadowRoot));
        return out;
      };
      deep('[data-mavo-ref]').forEach(e => e.removeAttribute('data-mavo-ref'));
      const label = el => {
        const by = el.getAttribute('aria-labelledby');
        const named = by ? clean(by.split(' ').map(i => (el.getRootNode().getElementById(i) || {}).innerText || '').join(' ')) : '';
        const forLabel = el.id ? el.getRootNode().querySelector('label[for="' + CSS.escape(el.id) + '"]') : null;
        const wrapEl = el.closest('label');
        // The label's own words, without the options of a select it wraps.
        const wrap = wrapEl && (() => { const c = wrapEl.cloneNode(true);
          c.querySelectorAll('select,textarea,input,option').forEach(n => n.remove());
          return { innerText: c.textContent }; })();
        const own = el.tagName === 'INPUT' && ['button', 'submit', 'reset'].includes(el.type) ? el.value : '';
        return clean(el.getAttribute('aria-label') || named || (forLabel && forLabel.innerText)
          || (wrap && wrap.innerText) || el.getAttribute('placeholder') || own || el.innerText
          || el.title || el.getAttribute('alt') || el.name || '').slice(0, 100);
      };
      const role = el => {
        const r = el.getAttribute('role'); if (r) return r;
        const t = el.tagName.toLowerCase();
        if (t === 'a') return 'link';
        if (t === 'select') return 'select';
        if (t === 'textarea') return 'textbox';
        if (t === 'input') { const ty = el.type || 'text';
          return ['checkbox', 'radio'].includes(ty) ? ty : ['button', 'submit', 'reset'].includes(ty) ? 'button' : 'textbox'; }
        return t;
      };
      const sel = 'a[href],button,input:not([type=hidden]),select,textarea,[role=button],[role=tab],'
        + '[role=menuitem],[role=link],[role=checkbox],[role=switch],[role=option],summary,[contenteditable=true]';
      const elements = []; let n = 0;
      for (const el of deep(sel)) {
        if (!vis(el)) continue;
        const ref = prefix + 'e' + (++n);
        el.setAttribute('data-mavo-ref', ref);
        const item = { ref, role: role(el), name: label(el) };
        if (el.tagName === 'A') { const h = el.getAttribute('href') || ''; if (h && !h.startsWith('javascript')) item.href = h.slice(0, 140); }
        if (['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName)) {
          if (el.type !== 'password') item.value = (el.value || '').slice(0, 60);
          if (el.type === 'checkbox' || el.type === 'radio') item.checked = el.checked;
          if (el.tagName === 'SELECT') item.options = [...el.options].slice(0, 25).map(o => clean(o.text));
        }
        if (el.disabled || el.getAttribute('aria-disabled') === 'true') item.disabled = true;
        if (el.getAttribute('aria-expanded')) item.expanded = el.getAttribute('aria-expanded') === 'true';
        elements.push(item);
        if (n >= 600) break;
      }
      const headings = deep('h1,h2,h3,[role=heading]').filter(vis).slice(0, 14).map(h => clean(h.innerText)).filter(Boolean);
      const cells = row => [...row.children].slice(0, 12).map(c => clean(c.innerText).slice(0, 80));
      const tables = [];
      for (const t of deep('table,[role=table],[role=grid]')) {
        if (!vis(t) || tables.length >= 6) continue;
        const rows = [...t.querySelectorAll('tr,[role=row]')].filter(r => r.closest('table,[role=table],[role=grid]') === t);
        if (rows.length < 2) continue;
        const all = rows.map(cells).filter(r => r.some(Boolean));
        if (all.length < 2) continue;
        tables.push({ headers: all[0], rows: all.slice(1, 61), total_rows: all.length - 1 });
      }
      const shadow = [];
      const walk = root => { for (const host of root.querySelectorAll('*')) if (host.shadowRoot) {
        shadow.push(clean([...host.shadowRoot.children]
          .filter(c => !['STYLE', 'SCRIPT'].includes(c.tagName)).map(c => c.textContent).join(' ')));
        walk(host.shadowRoot); } };
      walk(document);
      return { elements, headings, tables, shadow: shadow.filter(Boolean).join(' ') };
    }"""

    @staticmethod
    def view_of(snapshot, url, title, text):
        """The page as a model should see it: compact lines, not JSON, with exact refs to act on."""
        lines = [f"PAGE {url} — {title}"]
        if snapshot["headings"]:
            lines.append("HEADINGS: " + " | ".join(snapshot["headings"]))
        for number, table in enumerate(snapshot["tables"], 1):
            lines.append(
                f"TABLE {number} ({table['total_rows']} rows): " + " | ".join(table["headers"])
            )
            lines += [f"  {i}. " + " | ".join(row) for i, row in enumerate(table["rows"], 1)]
        everything = snapshot["elements"]
        shown = everything if len(everything) <= 220 else everything[:150] + everything[-70:]
        lines.append(
            "ELEMENTS (act with ref):"
            + (
                f" showing {len(shown)} of {len(everything)}; the middle is omitted"
                if len(shown) < len(everything)
                else ""
            )
        )
        for item in shown:
            bits = [f"[{item['ref']}]", item["role"], '"' + item["name"] + '"']
            if "value" in item and item["value"]:
                bits.append("= " + repr(item["value"]))
            if item.get("checked") is not None:
                bits.append("checked" if item["checked"] else "unchecked")
            if item.get("options"):
                bits.append("options: " + ", ".join(item["options"][:12]))
            if item.get("disabled"):
                bits.append("(disabled)")
            if item.get("expanded") is not None:
                bits.append("expanded" if item["expanded"] else "collapsed")
            if item.get("href"):
                bits.append("→ " + item["href"])
            if item.get("frame"):
                bits.append('in frame "' + item["frame"] + '"')
            lines.append("  " + " ".join(bits))
        lines.append("TEXT: " + text[:7000])
        return "\n".join(lines)

    async def snapshot(self):
        """The page and its frames as one list of elements. A frame's refs start with f<number>_,
        so an action can reach into a payment or login frame as easily as the main page."""
        self.frames = [self.page.main_frame]
        merged = {"elements": [], "headings": [], "tables": [], "shadow": ""}
        for frame in [f for f in self.page.frames if f != self.page.main_frame][:6]:
            if frame.is_detached() or not frame.url.startswith(("http", "about:srcdoc")):
                continue
            self.frames.append(frame)
        for index, frame in enumerate(self.frames):
            try:
                part = await frame.evaluate(self.SNAPSHOT, "" if index == 0 else f"f{index}_")
            except Exception:
                if index == 0:
                    raise ValueError("The page could not be read right now. Try again.") from None
                continue  # a frame that is mid-navigation is skipped, not fatal
            label = (
                ""
                if index == 0
                else (
                    frame.name
                    or await self.frame_title(frame)
                    or urlsplit(frame.url).hostname
                    or "frame"
                )
            )
            for item in part["elements"]:
                merged["elements"].append({**item, **({"frame": label} if label else {})})
            merged["headings"] += part["headings"]
            merged["tables"] += part["tables"]
            merged["shadow"] = (merged["shadow"] + " " + part.get("shadow", "")).strip()
        return merged

    @staticmethod
    async def frame_title(frame):
        try:
            owner = await frame.frame_element()
            return await owner.get_attribute("title")
        except Exception:
            return None

    def target(self, args):
        ref = args.get("ref")
        if ref:
            frame = self.page.main_frame
            if ref.startswith("f"):
                number = int(ref[1:].split("_")[0])
                frames = getattr(self, "frames", [])
                if number >= len(frames):
                    return self.page.locator("[data-mavo-ref='missing']").first
                frame = frames[number]
            return frame.locator(f'[data-mavo-ref="{ref}"]').first
        selector = re.sub(r""":contains\((['"])(.*?)\1\)""", r':has-text("\2")', args["selector"])
        return self.page.locator(selector).first

    async def drag(self, args):
        """Drag one thing onto another, or by a distance (a slider, a resize handle, a canvas).
        Moves in small steps like a hand would, so pages that listen for movement see it."""
        source = self.target(args)
        if await source.count() == 0:
            raise ValueError("That element is gone. Read the page again for fresh refs.")
        await source.scroll_into_view_if_needed(timeout=5000)
        if args.get("to_ref") or args.get("to_selector"):
            destination = self.target(
                {"ref": args.get("to_ref"), "selector": args.get("to_selector")}
            )
            if await destination.count() == 0:
                raise ValueError("The drop target is gone. Read the page again for fresh refs.")
            await destination.scroll_into_view_if_needed(timeout=5000)
            await source.drag_to(destination, timeout=10000)
            return
        box = await source.bounding_box()
        if not box or box["width"] <= 0 or box["height"] <= 0:
            raise ValueError("That element has no visible area to drag")
        x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        dx, dy = args.get("dx") or 0, args.get("dy") or 0
        mouse = self.page.mouse
        await mouse.move(x, y)
        await mouse.down()
        try:
            steps = 12
            for step in range(1, steps + 1):
                await mouse.move(x + dx * step / steps, y + dy * step / steps)
                await asyncio.sleep(0.02)
        finally:
            await mouse.up()

    async def autoscroll(self):
        """Load lazily rendered rows and cards the way scrolling would, then return to the top."""
        height = 0
        for _ in range(8):
            now = await self.page.evaluate(
                "() => { window.scrollTo(0, document.body.scrollHeight); return document.body.scrollHeight; }"
            )
            await asyncio.sleep(0.8)
            if now == height:
                break
            height = now
        await self.page.evaluate("() => window.scrollTo(0, 0)")

    async def settle(self, limit=12.0, quiet=1.5, empty_hold=4.0):
        """Wait until the page has stopped changing. Modern sites draw an empty skeleton first
        ("0 Jobs Listed") and fill it in a moment later; reading too early reports nothing."""
        page, start = self.page, time.monotonic()
        try:
            await page.wait_for_load_state("load", timeout=min(limit, 10) * 1000)
        except Exception:  # noqa: S110 - a slow "load" event must not fail the read
            pass
        last, changed, sizes = None, time.monotonic(), []
        while time.monotonic() - start < limit:
            self.check()
            try:
                text = await page.evaluate("document.body ? document.body.innerText : ''")
            except Exception:
                text = last  # navigation in flight; try again
            if text != last:
                last, changed = text, time.monotonic()
            sizes = (sizes + [len(text or "")])[-8:]
            hold = max(quiet, empty_hold) if EMPTY_HINT.search(last or "") else quiet
            if last and time.monotonic() - changed >= hold:
                break
            # A rotating headline, ticker or clock keeps changing forever without the page
            # growing. If the size has been flat for a while, the page is as loaded as it gets.
            if (
                len(sizes) == 8
                and time.monotonic() - start >= 3.5
                and not EMPTY_HINT.search(last or "")
                and max(sizes) - min(sizes) <= max(40, 0.04 * max(sizes))
            ):
                break
            await asyncio.sleep(0.4)
        return round(time.monotonic() - start, 1)

    async def sign_in_wall(self):
        """Tell the Crew (and the user) when a page is a login or human-check, instead of letting
        a model mistake it for an empty result."""
        try:
            info = await self.page.evaluate(
                """() => ({
                  password: [...document.querySelectorAll('input[type=password]')].some(el => {
                    const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; }),
                  cta: [...document.querySelectorAll('a,button')].some(el => {
                    const r = el.getBoundingClientRect();
                    return r.width > 0 && r.height > 0
                      && /^\\s*(?:sign|log)\\s?in\\s*$/i.test(el.innerText || ''); }),
                  link: ([...document.querySelectorAll('a[href]')].find(el => {
                    const r = el.getBoundingClientRect();
                    return r.width > 0 && r.height > 0
                      && /^\\s*(?:sign|log)\\s?in\\s*$/i.test(el.innerText || ''); }) || {}).href || '',
                  text: (document.body ? document.body.innerText : '').slice(0, 2000)
                })"""
            )
        except Exception:
            return None
        host = urlsplit(self.page.url).hostname
        here = self.page.url.split("#")[0]

        def wall(kind, login=None):
            # Where to send the person so they land on the sign-in form itself.
            link = info["link"] if urlsplit(info["link"]).hostname == host else ""
            return {"kind": kind, "host": host, "url": here, "login_url": login or link or here}

        signed_in = re.search(r"\b(?:sign|log) ?out\b|\blogout\b", info["text"], re.I)
        # What the page itself says about the session: a sign-out control means signed in; a sign-in
        # form or button means signed out; a page showing neither tells us nothing.
        self.session_hint = (
            "in"
            if signed_in and not info["password"]
            else "out"
            if info["password"] or info["cta"]
            else "unknown"
        )
        self.login_link = info["link"] if urlsplit(info["link"]).hostname == host else ""
        if CAPTCHA.search(info["text"] + " " + self.page.url):
            return wall("captcha")
        if info["password"]:
            return wall("login", here)
        sso = re.search(
            r"continue with (?:google|github|microsoft|apple)|sign in with|log in with|login with",
            info["text"],
            re.I,
        )
        looks_like_login = re.search(
            r"/(?:log-?in|sign-?in|auth|sso|accounts?/login)\b", self.page.url, re.I
        )
        if not signed_in and (sso or looks_like_login):
            return wall("login", here if looks_like_login else None)
        # "Your session has timed out. Please sign in again." is a login wall too.
        if not signed_in and SESSION_ENDED.search(info["text"]):
            return wall("login")
        # Asked for a deep page, bounced to the front page that offers "Sign in": a logged-out
        # redirect, which must never be read as "the balance/jobs/messages are empty".
        if self.open_redirected and info["cta"] and not signed_in:
            return wall("login")
        return None

    async def goto(self, url):
        """Open a page that may be slow or briefly unreachable: two patient attempts."""
        for attempt in range(2):
            try:
                await self.page.goto(
                    url, wait_until="domcontentloaded" if attempt == 0 else "commit", timeout=45000
                )
                return
            except Exception as exc:
                self.check()
                timed_out = "Timeout" in type(exc).__name__
                network = "net::ERR_" in str(exc)
                if (timed_out or network) and not attempt:
                    await asyncio.sleep(2)
                    continue
                if timed_out:
                    raise ValueError(
                        "The site took too long to respond. Check that it opens normally."
                    ) from None
                if self.blocked_hosts:
                    raise ValueError(self.blocked_message()) from None
                if network:
                    raise ValueError(
                        "Mavo could not reach the site. Check your connection and that the "
                        "address is right."
                    ) from None
                raise

    def blocked_message(self):
        """Say which site the page was sent to and refused, so the fix is obvious."""
        names = ", ".join(dict.fromkeys(self.blocked_hosts))
        return (
            f"The site sent the browser to {names}, which this Crew is not allowed to open. "
            "Add it to the Crew's allowed sites (usually a sign-in or landing address)."
        )

    def watch_dialogs(self, page):
        """alert/confirm/prompt: accepted unless they warn about something destructive, and always
        reported to the model so a silently dismissed confirmation cannot hide a failed step."""

        async def on_dialog(dialog):
            risky = bool(DESTRUCTIVE_DIALOG.search(dialog.message))
            self.dialogs.append(
                {"type": dialog.type, "message": dialog.message[:200], "accepted": not risky}
            )
            if risky or dialog.type == "prompt":
                await dialog.dismiss()
            else:
                await dialog.accept()

        page.on("dialog", lambda d: asyncio.create_task(on_dialog(d)))

    def take_dialogs(self):
        found, self.dialogs = self.dialogs, []
        return {"dialogs": found} if found else {}

    def track_tabs(self):
        """Note every tab a page opens (script `window.open`, as Microsoft Forms' "New Form" does),
        to be followed after the action that opened it."""
        self.new_tabs = []
        self.context.on("page", self.tab_opened)

    def tab_opened(self, tab):
        if tab == self.page:
            return
        if len(self.context.pages) > self.MAX_TABS:  # a page opening tab after tab
            asyncio.create_task(tab.close())
        else:
            self.new_tabs.append(tab)

    async def follow_new_tab(self):
        """A site may do its work in a new tab: Microsoft Forms opens the form designer that way, and
        used to look as if "New Form" did nothing. Model actions work on one page, so the newest tab
        on a site this job may use becomes that page, as it would for a person; the tab before it
        stays behind and returns when the new one closes. A tab anywhere else is closed."""
        followed = False
        fresh, self.new_tabs = self.new_tabs, []
        for tab in fresh:
            try:
                if tab.is_closed():
                    continue
                try:
                    await tab.wait_for_load_state("domcontentloaded", timeout=8000)
                except Exception:  # noqa: S110 - judged by where it is, loaded or not
                    pass
                if tab.is_closed():
                    continue
                if not (self.active_unrestricted or host_allowed(tab.url, self.active_domains)):
                    await tab.close()
                    continue
            except Exception:  # noqa: S112 - a tab that vanished while it was being looked at
                continue
            tab.set_default_timeout(15000)
            self.watch_dialogs(tab)
            tab.once("close", self.tab_closed)
            self.page, followed = tab, True
        return followed

    async def wait_out_redirects(self, patience=10):
        """A sign-in that is only passing the person through (an OAuth hop that signs them in again
        from the saved session and sends them on) is not a page. Mavo used to hand the agent that
        screen mid-hop, and the agent reported "needs sign-in" about a login that was working.
        Wait for it to land. A real sign-in form (a password or email box) is never waited on."""
        for _ in range(patience):
            try:
                if not AUTH_HOP.search(urlsplit(self.page.url).path):
                    return
                if await self.page.locator(SIGN_IN_FIELD).first.is_visible():
                    return
            except Exception:
                return
            await asyncio.sleep(1)
        await self.settle(4)

    def tab_closed(self, tab):
        if tab != self.page or not self.context:
            return
        left = [p for p in self.context.pages if not p.is_closed()]
        self.page = left[-1] if left else None

    def profile_dir(self):
        return str(self.root / "browser" / "mavo")

    def _profile_mains(self):
        """Main browser processes using Mavo's profile, with their arguments (children carry
        --type=). Two kinds exist: the automated browser, which Playwright drives through a
        pipe, and the sign-in window, which has a debug port instead."""
        import psutil

        marker = self.profile_dir().lower()
        found = []
        for proc in psutil.process_iter(["pid", "cmdline"]):
            args = [a.lower() for a in (proc.info.get("cmdline") or [])]
            if any(marker in a for a in args) and not any(a.startswith("--type=") for a in args):
                found.append((proc.info["pid"], args))
        return found

    def automated_pids(self):
        """Mavo's automated browser: a Crew's, or one that was closed and is still leaving."""
        return [pid for pid, args in self._profile_mains() if "--remote-debugging-pipe" in args]

    def user_window_pids(self):
        """The sign-in window. A leftover automated browser is not one: mistaking it for one made
        every run say "please sign in" until it was gone."""
        return [pid for pid, args in self._profile_mains() if "--remote-debugging-pipe" not in args]

    async def clear_leftovers(self, patience=3.0):
        """An automated browser that was closed but has not finished leaving still holds the
        profile, and a new one cannot start beside it. Give it a moment, then end it (the profile
        is Mavo's own) and wait until it is really gone."""
        import subprocess

        waited = 0.0
        while self.automated_pids() and waited < patience:
            await asyncio.sleep(0.3)
            waited += 0.3
        for pid in self.automated_pids():
            subprocess.run(  # noqa: S603, S607 - fixed command, a process using Mavo's own profile
                ["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False
            )
        for _ in range(25):
            if not self.automated_pids():
                return
            await asyncio.sleep(0.3)

    def user_window_open(self):
        try:
            return bool(self.user_window_pids())
        except Exception:
            return False

    @staticmethod
    def _site_names(host):
        """The domains whose cookies a site sees: itself and each parent, never a bare suffix."""
        parts = (host or "").lower().split(".")
        return {".".join(parts[i:]) for i in range(len(parts) - 1)}

    async def saved_login(self, host):
        """True or False as saved_session, and also while a Crew has the browser open, when its
        cookie file is locked: then the running browser is asked instead. None means it cannot be
        told right now, which is not the same as "no login saved"."""
        if self.context is not None:
            names = self._site_names(host)
            try:
                cookies = await asyncio.wait_for(self.context.cookies(), 5)
            except Exception:
                return None
            now = time.time()
            return any(
                c.get("domain", "").lstrip(".").lower() in names
                and c.get("expires", -1) > now
                and not TRACKING_COOKIE.match(c.get("name", ""))
                for c in cookies
            )
        return await asyncio.to_thread(self.saved_session, host)

    def saved_session(self, host):
        """Does Mavo's browser hold a saved login for this site? A look at its cookie store (no
        browser started): is there an unexpired cookie that is not a tracker? It says a sign-in
        was kept, not that the site still accepts it; only opening the site tells that. While a
        browser has the store open Windows will not let it be read: that answers None (cannot
        tell), never False, so a locked file is not mistaken for "nothing saved"."""
        import shutil
        import sqlite3
        import tempfile

        source = Path(self.profile_dir()) / "Default" / "Network" / "Cookies"
        names = self._site_names(host)
        if not source.exists() or not names:
            return False
        hosts = sorted(names | {"." + n for n in names})
        chrome_now = int((time.time() + 11644473600) * 1_000_000)  # microseconds since 1601
        try:
            with tempfile.TemporaryDirectory() as folder:
                copy = Path(folder) / "cookies.db"
                shutil.copyfile(source, copy)
                db = sqlite3.connect(copy)
                try:
                    rows = db.execute(
                        "select name from cookies where is_persistent = 1 and expires_utc > ? "
                        f"and host_key in ({','.join('?' * len(hosts))})",
                        [chrome_now, *hosts],
                    ).fetchall()
                finally:
                    db.close()
        except Exception:
            return None  # a browser has the store locked: not the same as "nothing saved"
        return any(not TRACKING_COOKIE.match(name) for (name,) in rows)

    def open_user_window(self, url):
        """Open Mavo's browser profile as an ordinary window. Google and other strict sites refuse
        to sign in inside a remote-controlled browser, so the person signs in here, by hand, and
        Crew later reuse that session."""
        import subprocess

        if self.context or self.automated_pids():
            raise ValueError("A Crew is using the browser. Try again shortly.")
        if self.user_window_open():
            return
        options = self.engine_options()
        exe = options.get("executable_path") or installed_browsers().get(options["label"])
        if not exe:
            raise ValueError("No supported browser was found on this computer.")
        Path(self.profile_dir()).mkdir(parents=True, exist_ok=True)
        x, y, width, height = default_window(work_area())
        port = free_port()
        subprocess.Popen(  # noqa: S603 - fixed executable, our own profile, a validated https URL
            [
                exe,
                f"--user-data-dir={self.profile_dir()}",
                "--no-first-run",
                "--no-default-browser-check",
                # Keep sign-ins that last only "for the session": without this the browser forgets
                # them the moment it closes, and a site that uses them would sign Mavo out again.
                "--restore-last-session",
                # Always on screen. The profile remembers the off-screen spot of the background
                # browser, and would otherwise open this window where nobody can reach it.
                f"--window-position={x},{y}",
                f"--window-size={width},{height}",
                # The browser's own debug channel, on this computer only, for as long as the window
                # is open: it lets Mavo keep session-only sign-ins (see session_keeper.py).
                f"--remote-debugging-port={port}",
                url,
            ],
            close_fds=True,
        )
        self.interactive_until = time.monotonic() + 1800
        self.user_window_flag = True
        self.user_window_port = port
        threading.Thread(target=self._keep_sign_ins, args=(port,), daemon=True).start()
        # Belt and braces: once it has opened, move it onto the screen if the browser put it elsewhere.
        for delay in (2.5, 6.0):
            timer = threading.Timer(delay, self.bring_on_screen)
            timer.daemon = True
            timer.start()

    def bring_on_screen(self):
        """Move any visible window of Mavo's own browser that is out of reach onto the screen.
        Returns how many it moved."""
        if os.name != "nt":
            return 0
        from ctypes import wintypes

        try:
            mains = self.user_window_pids()
            owners = set(mains)
            for pid in mains:
                owners.update(child.pid for child in psutil.Process(pid).children(recursive=True))
        except Exception:
            return 0
        user32 = ctypes.windll.user32
        area = work_area()
        moved = []

        def visit(hwnd, _):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            name = ctypes.create_unicode_buffer(40)
            user32.GetClassNameW(hwnd, name, 40)
            if (
                pid.value in owners
                and name.value == "Chrome_WidgetWin_1"
                and user32.IsWindowVisible(hwnd)
            ):
                rect = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(rect))
                # The background browser's off-screen window is not one a person can reach, and
                # is not what they are waiting for: only ordinary-sized windows are moved.
                if rect.right - rect.left < 200:
                    return True
                fit = fit_on_screen((rect.left, rect.top, rect.right, rect.bottom), area)
                if fit:
                    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                    user32.SetWindowPos(hwnd, 0, fit[0], fit[1], fit[2], fit[3], 0x0040)
                    moved.append(hwnd)
            return True

        callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(visit)
        user32.EnumWindows(callback, 0)
        return len(moved)

    def _keep_sign_ins(self, port):
        """While the sign-in window is open, make its session-only cookies last, once a second, so
        the person can close it any way they like without ending their sign-in."""

        async def watch():
            await asyncio.sleep(1.5)
            while self.user_window_open():
                try:
                    await keep_session_cookies(port)
                except Exception:  # noqa: S110 - not up yet, or already closing
                    pass
                await asyncio.sleep(1.0)

        asyncio.run(watch())

    async def close_user_window(self):
        """Close the sign-in window so everything signed in is saved, then insist."""
        import subprocess

        port = getattr(self, "user_window_port", None)
        if port and self.user_window_open():
            try:
                # Keep the session cookies, then close through the browser's own channel: the one
                # way a browser saves them. A plain window-close message would drop them.
                await keep_session_cookies(port, close=True)
            except Exception:  # noqa: S110 - fall back to closing it from outside
                pass
            for _ in range(20):
                if not self.user_window_open():
                    self.user_window_flag = False
                    return
                await asyncio.sleep(0.4)
        for pid in self.user_window_pids():
            subprocess.run(["taskkill", "/PID", str(pid)], capture_output=True, check=False)  # noqa: S603, S607
        for _ in range(20):
            if not self.user_window_open():
                self.user_window_flag = False
                return
            await asyncio.sleep(0.4)
        for pid in self.user_window_pids():
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False
            )  # noqa: S603, S607
        self.user_window_flag = False

    def engine_options(self):
        if self.engine is None:
            self.engine = default_browser()
        return self.engine

    def hold_for_sign_in(self, owner, generation):
        self.interactive_owner, self.interactive_generation = owner, generation
        self.interactive_until = time.monotonic() + 900

    def sign_in_held(self, crew):
        return bool(
            crew
            and self.interactive_owner == crew.id
            and self.interactive_generation == crew.runtime_generation
            and time.monotonic() < self.interactive_until
        )

    def check(self):
        if self.guard and not self.guard():
            raise ValueError("Desktop actions are paused or Mavo is signed out")

    async def teardown(self, force=False):
        """Close Mavo's automated browser whatever state it is in. Each step is bounded: a frozen
        page cannot stop it from closing, and a browser that will not close is ended outright
        (its profile is Mavo's own) so the next launch is not blocked by a leftover process."""
        context, playwright, proxy = self.context, self.playwright, self.proxy
        self.context = self.playwright = self.page = self.proxy = None
        self.browser_owner = self.scope_guard = self.interactive_owner = None
        stuck = force
        for step in (
            context.close() if context else None,
            playwright.stop() if playwright else None,
            proxy.close() if proxy else None,
        ):
            if step is None:
                continue
            try:
                async with asyncio.timeout(self.close_patience):
                    await step
            except Exception:  # includes the timeout: keep closing the rest
                stuck = True
        if stuck:
            await self.clear_leftovers(patience=0)

    async def close_owner(self, owner):
        async with self.browser_lock:
            if self.browser_owner == owner:
                await self.teardown()

    async def close(self):
        for task in list(self.active_tasks):
            if task != asyncio.current_task():
                task.cancel()
        for process in list(self.children):
            if process.returncode is None:
                process.kill()
        # Mavo is closing: nothing may hold it up. A browser that is already gone or frozen, or a
        # lock something still holds, is waited for briefly and then ended outright.
        locked = False
        try:
            async with asyncio.timeout(self.close_patience):
                await self.browser_lock.acquire()
            locked = True
        except TimeoutError:
            pass
        try:
            await self.teardown()
        finally:
            if locked:
                self.browser_lock.release()
        for process in list(self.children):
            if process.returncode is None:
                process.kill()

    async def terminal(self, args):
        folder = Path(args["folder"]).resolve(strict=True)
        command = args["command"]
        if command == "python.version":
            return {"stdout": "Python " + sys.version.split()[0], "exit_code": 0}
        if command == "system.info":
            import platform

            return {"stdout": platform.platform(), "exit_code": 0}
        if command.startswith("git."):
            metadata = folder / ".git"
            if not metadata.is_dir() or not metadata.resolve().is_relative_to(folder):
                raise ValueError("Select a Git repository root with a local .git directory")
            executable = shutil.which("git")
            commands = {
                "git.status": ["status", "--short", "--branch"],
                "git.diff": ["diff", "--no-ext-diff", "--no-textconv", "--stat"],
                "git.log": ["log", "-10", "--format=%h %s"],
                "git.branches": ["branch", "--list"],
            }
            argv = [
                "--no-pager",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "core.hooksPath=" + str(self.root / "no-hooks"),
                *commands[command],
            ]
        else:
            executable, argv = shutil.which("node"), ["--version"]
        if not executable:
            raise ValueError("The requested command is not installed")
        if Path(executable).resolve().is_relative_to(folder):
            raise ValueError("Commands must use an installed tool outside the work folder")
        environment = {
            key: value
            for key, value in os.environ.items()
            if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"}
        }
        environment.update(
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL=os.devnull,
            GIT_TERMINAL_PROMPT="0",
            GIT_PAGER="cat",
            NO_COLOR="1",
        )
        process = await asyncio.create_subprocess_exec(
            executable,
            *argv,
            cwd=folder,
            env=environment,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            **({"creationflags": 0x08000000} if os.name == "nt" else {}),
        )
        self.children.add(process)
        try:
            async with asyncio.timeout(20):
                output = await process.stdout.read(32001)
                if len(output) > 32000:
                    process.kill()
                await process.wait()
            return {
                "stdout": output[:32000].decode(errors="replace"),
                "exit_code": process.returncode,
                "truncated": len(output) > 32000,
            }
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
            self.children.discard(process)

    async def browser(self, operation, args, rule, owner, scope_guard=None, limit=None):
        """Run one browser action. Failures come back as a short, actionable sentence.

        Every action has a ceiling (`limit` seconds, or the usual one for that action). A page
        that never answers (a script stuck in a loop, a frozen tab) must not hold the browser and
        its lock for ever: it is closed, the action fails with a plain reason, and the next
        action starts from a clean browser."""
        try:
            async with asyncio.timeout(limit or BROWSER_LIMITS.get(operation, 80)) as ceiling:
                return await self._browser(operation, args, rule, owner, scope_guard)
        except TimeoutError:
            if not ceiling.expired():
                raise
            async with self.browser_lock:
                if self.context and self.browser_owner in {owner, None}:
                    await self.teardown(force=True)
            raise ValueError(
                "The page stopped responding, so Mavo closed it. Try again in a moment."
            ) from None
        except ValueError:
            raise
        except Exception as exc:
            if "playwright" not in type(exc).__module__:
                raise
            if "Timeout" in type(exc).__name__:
                raise ValueError(
                    f"{operation} timed out. The element may be covered by a pop-up or banner, "
                    "hidden, or not ready. Close any pop-up, read the page again, then retry."
                ) from None
            reason = str(exc).strip().splitlines()[0][:160]
            if "not a valid selector" in str(exc) or "Unexpected token" in str(exc):
                raise ValueError(
                    "That selector is not valid. Use the ref of the element from the latest "
                    "browser.read instead (for example ref e12)."
                ) from None
            raise ValueError("The page rejected that action: " + reason) from None

    async def _browser(self, operation, args, rule, owner, scope_guard=None):
        used_saved_login = False
        navigation = None
        async with self.browser_lock:
            self.scope_guard = scope_guard
            self.check()
            if self.context and self.browser_owner != owner:
                raise ValueError(
                    "Mavo's browser is open for sign-in. Close it first."
                    if self.browser_owner == "core"
                    else "Another Crew owns the browser. Close its session first."
                )
            if operation == "browser.close":
                if self.context:
                    await self.context.close()
                if self.playwright:
                    await self.playwright.stop()
                self.context = self.playwright = self.page = None
                self.browser_owner = None
                self.interactive_owner = None
                if self.proxy:
                    await self.proxy.close()
                    self.proxy = None
                return {"closed": True}
            searched = operation == "browser.search"
            if searched:
                # A search is an ordinary page load of the fixed engine, with the same scope,
                # proxy and public-address checks as browser.open.
                operation, args = "browser.open", {"url": search_url(args["query"])}
            self.active_domains = with_companions(rule["domains"])
            self.active_unrestricted = rule["mode"] == "unrestricted"
            if self.proxy:
                self.proxy.domains = None if self.active_unrestricted else self.active_domains
            if operation == "browser.open":
                if self.context and (not self.page or self.page.is_closed()):
                    await self.context.close()
                    await self.playwright.stop()
                    if self.proxy:
                        await self.proxy.close()
                    self.context = self.playwright = self.page = self.proxy = None
                    self.browser_owner = None
                if not self.active_unrestricted and not host_allowed(
                    args["url"], self.active_domains
                ):
                    raise ValueError("This site is outside the approved scope")
                await public_host(urlsplit(args["url"]).hostname)
                if not self.context:
                    if self.user_window_open():
                        raise ValueError("Mavo's browser is open for sign-in. Close it first.")
                    await self.clear_leftovers()
                    from playwright.async_api import async_playwright

                    self.playwright = await async_playwright().start()
                    try:

                        def proxy_guard():
                            self.check()
                            if self.scope_guard:
                                self.scope_guard()

                        self.proxy = PublicBrowserProxy(
                            None if self.active_unrestricted else self.active_domains, proxy_guard
                        )
                        proxy_settings = await self.proxy.start()
                        # Default browser's engine, Mavo's own profile; no download.
                        self.context = await self.playwright.chromium.launch_persistent_context(
                            self.profile_dir(),
                            chromium_sandbox=True,
                            **{k: v for k, v in self.engine_options().items() if k != "label"},
                            proxy=proxy_settings,
                            env={
                                key: value
                                for key, value in os.environ.items()
                                if key.upper()
                                in {
                                    "PATH",
                                    "SYSTEMROOT",
                                    "WINDIR",
                                    "TEMP",
                                    "TMP",
                                    "LOCALAPPDATA",
                                    "USERPROFILE",
                                    "PROGRAMFILES",
                                    "PROGRAMFILES(X86)",
                                    "COMMONPROGRAMFILES",
                                }
                            },
                            # Visible on purpose: the person can watch it and step in to sign
                            # in. Only automated test runs hide it (they set this), so tests never
                            # open windows on the person's screen.
                            headless=os.environ.get("MAVO_BROWSER_HIDDEN") == "1",
                            ignore_default_args=["--enable-automation"],
                            accept_downloads=False,
                            service_workers="block",
                            no_viewport=True,
                            args=[
                                "--disable-extensions",
                                # No "--restore-last-session" here: reopening the last run's tab
                                # (another Crew's site, outside this one's scope) can stop the
                                # browser from starting at all. Sign-ins made in the sign-in window
                                # are already made lasting there (see session_keeper.py).
                                "--disable-blink-features=AutomationControlled",
                                "--proxy-bypass-list=<-loopback>",
                                "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                                # Scheduled work must not pop windows over what you are doing.
                                "--window-position=-32000,-32000",
                                "--window-size=1366,900",
                            ],
                        )

                        async def route(request_route):
                            request = request_route.request
                            host = urlsplit(request.url).hostname or ""
                            top_level = (
                                request.resource_type == "document"
                                and request.frame.parent_frame is None
                            )
                            in_scope = (
                                host_allowed(request.url, self.active_domains)
                                if top_level
                                else request.url.startswith("https://")
                                and same_site(host, self.active_domains)
                            )
                            if not in_scope and not (
                                self.active_unrestricted and host_allowed(request.url, [host])
                            ):
                                if top_level and host and host not in self.blocked_hosts:
                                    self.blocked_hosts.append(host)
                                await request_route.abort()
                                return
                            try:
                                self.check()
                                if self.scope_guard:
                                    self.scope_guard()
                                await public_host(urlsplit(request.url).hostname)
                            except (ValueError, OSError, RuntimeError):
                                await request_route.abort()
                                return
                            await request_route.continue_()

                        # Restoring the last session also reopens its tabs: keep one, close the rest.
                        for extra in self.context.pages[1:]:
                            try:
                                await extra.close()
                            except Exception:  # noqa: S110 - a tab that is already gone is fine
                                pass
                        await self.context.route("**/*", route)
                        await self.context.route_web_socket(
                            "**/*", lambda connection: connection.close()
                        )
                        self.page = (
                            self.context.pages[0]
                            if self.context.pages
                            else await self.context.new_page()
                        )
                        self.page.set_default_timeout(15000)
                        self.watch_dialogs(self.page)
                        # Model actions operate one page. A tab a site opens is followed only if it
                        # is on a site this job may use (see follow_new_tab); the scope rules above
                        # cover every tab of this context.
                        self.track_tabs()
                        self.browser_owner = owner
                        self.pinned_domains = list(self.active_domains)
                    except Exception:
                        if self.context:
                            await self.context.close()
                        await self.playwright.stop()
                        self.context = self.playwright = self.page = None
                        if self.proxy:
                            await self.proxy.close()
                            self.proxy = None
                        raise
                self.blocked_hosts = []
                await self.goto(args["url"])
                await self.settle(12)
                asked, landed = urlsplit(args["url"]).path, urlsplit(self.page.url).path
                self.open_redirected = asked.strip("/") != "" and landed.strip("/") == ""
                used_saved_login = await self.autofill()
                if used_saved_login:
                    await self.settle(12)
            else:
                if not self.page or self.page.is_closed():
                    raise ValueError("Open a page in Mavo's browser first")
                if not self.active_unrestricted and not host_allowed(
                    self.page.url, self.active_domains
                ):
                    raise ValueError(
                        self.blocked_message()
                        if self.blocked_hosts
                        else "The current page is outside the approved scope"
                    )
                navigation = None
                if operation in {
                    "browser.navigate",
                    "browser.click",
                    "browser.double_click",
                    "browser.drag",
                }:
                    self.open_redirected = False
                if operation == "browser.navigate":
                    navigation = await self.navigate(args["goal"])
                elif operation == "browser.hover":
                    element = self.target(args)
                    if await element.count() == 0:
                        raise ValueError(
                            "That element is gone. Read the page again for fresh refs."
                        )
                    await element.hover(timeout=8000)
                    await self.settle(3, quiet=0.5, empty_hold=1.5)
                elif operation == "browser.drag":
                    await self.drag(args)
                    await self.settle(6, quiet=0.7, empty_hold=2.5)
                elif operation in {"browser.click", "browser.double_click"}:
                    element = self.target(args)
                    if await element.count() == 0:
                        raise ValueError(
                            "That element is gone. Read the page again for fresh refs."
                        )
                    if await element.evaluate(self.OPENS_APP):
                        raise ValueError(
                            "That link opens another program (such as email), not a page. "
                            "Mavo does not follow it."
                        )
                    await element.evaluate(
                        "el => el.tagName === 'A' && el.removeAttribute('target')"
                    )
                    await self.press_on(element, double=operation == "browser.double_click")
                    await self.settle(6, quiet=0.7, empty_hold=2.5)
                elif operation == "browser.fill":
                    element = self.target(args)
                    if await element.count() == 0:
                        raise ValueError("That field is gone. Read the page again for fresh refs.")
                    if (await element.get_attribute("type") or "").lower() in {"password", "file"}:
                        raise ValueError("Enter passwords and choose files yourself in the browser")
                    if await element.evaluate(self.RICH_EDITOR):
                        # Web apps (Microsoft Forms, docs, chat boxes) edit in their own boxes,
                        # which only hear typed keys: text set in one go shows on the page but
                        # is never saved. Typed like a person, it is.
                        await self.press_on(element)
                        await self.page.keyboard.press("Control+A")
                        text = args["text"]
                        await element.press_sequentially(
                            text, delay=15, timeout=max(8000, len(text) * 60)
                        )
                    else:
                        await element.fill(args["text"], timeout=8000)
                elif operation == "browser.select":
                    element = self.target(args)
                    if await element.count() == 0:
                        raise ValueError("That list is gone. Read the page again for fresh refs.")
                    await element.select_option(label=args["option"], timeout=8000)
                    await self.settle(4, quiet=0.7, empty_hold=2.5)
                elif operation == "browser.press":
                    await self.page.keyboard.press(args["key"])
                    await self.settle(4, quiet=0.7, empty_hold=2.5)
                elif operation == "browser.scroll":
                    script = {
                        "down": "window.scrollBy(0, window.innerHeight * 0.85)",
                        "up": "window.scrollBy(0, -window.innerHeight * 0.85)",
                        "top": "window.scrollTo(0, 0)",
                        "bottom": "window.scrollTo(0, document.body.scrollHeight)",
                    }[args["direction"]]
                    await self.page.evaluate(f"() => {script}")
                    await asyncio.sleep(1)
                elif operation == "browser.wait":
                    if args.get("text"):
                        try:
                            await self.page.wait_for_function(
                                "t => document.body && document.body.innerText.includes(t)",
                                arg=args["text"],
                                timeout=args["seconds"] * 1000,
                            )
                        except Exception:  # noqa: S110 - waiting is best effort
                            pass
                    else:
                        await asyncio.sleep(args["seconds"])
            opened_tab = await self.follow_new_tab()
            await self.wait_out_redirects()
            self.check()
            if operation != "browser.read":
                return {
                    "url": self.page.url,
                    "title": await self.page.title(),
                    "untrusted": True,
                    "content_available_via": "browser.read",
                    **(
                        {"new_tab": "The page opened a new tab, and it is now the page."}
                        if opened_tab
                        else {}
                    ),
                    **({"signed_in_with_saved_login": True} if used_saved_login else {}),
                    **({"navigation": navigation} if navigation else {}),
                    **({"results": await self.search_results()} if searched else {}),
                    **self.take_dialogs(),
                    **({"sign_in_required": wall} if (wall := await self.sign_in_wall()) else {}),
                    "session": self.session_hint,
                    **(
                        {"login_url": self.login_link}
                        if self.session_hint == "out" and self.login_link
                        else {}
                    ),
                }
            waited = await self.settle(10, quiet=1.0)
            for _ in range(2):
                body = await self.page.locator("body").inner_text()
                if len(body) > 600 or not SITE_ERROR.search(body):
                    break
                await asyncio.sleep(2)
                await self.page.reload(wait_until="domcontentloaded", timeout=45000)
                waited += await self.settle(10)
            if args.get("scroll"):
                await self.autoscroll()
                waited += await self.settle(4)
            page_text = await self.page.locator("body").inner_text()
            snapshot = await self.snapshot()
            if snapshot["shadow"]:
                page_text += "\n" + snapshot["shadow"]
            url, title = self.page.url, await self.page.title()
            return {
                "url": url,
                "title": title,
                "view": self.view_of(snapshot, url, title, page_text),
                "text": page_text[:12000],
                "untrusted": True,
                "waited_seconds": waited,
                **(
                    {"site_error": True}
                    if len(page_text) <= 600 and SITE_ERROR.search(page_text)
                    else {}
                ),
                "tables": snapshot["tables"],
                "elements": (
                    snapshot["elements"]
                    if len(snapshot["elements"]) <= 220
                    else snapshot["elements"][:150] + snapshot["elements"][-70:]
                ),
                "elements_total": len(snapshot["elements"]),
                **self.take_dialogs(),
                **({"sign_in_required": wall} if (wall := await self.sign_in_wall()) else {}),
            }

    @staticmethod
    def windows(operation, args, rule):
        if os.name != "nt":
            raise ValueError("Windows actions require Windows")
        from ctypes import wintypes

        from mavo.action_rules import BLOCKED_INPUT_APPS

        if operation == "windows.shutdown":
            # Windows' own shutdown, a minute from now, with its warning on screen: the person
            # can still stop it (shutdown /a). Nothing is forced closed.
            import subprocess

            subprocess.run(
                [
                    "shutdown",
                    "/s",
                    "/t",
                    "60",
                    "/c",
                    "Mavo is shutting down this computer in 60 seconds. "
                    "To cancel, run: shutdown /a",
                ],
                check=True,
                capture_output=True,
                timeout=15,
            )
            return {"shutting_down_in_seconds": 60, "cancel_with": "shutdown /a"}
        if operation == "windows.sleep":
            return sleep_computer()
        user = ctypes.WinDLL("user32", use_last_error=True)
        user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user.IsWindowVisible.argtypes = [wintypes.HWND]
        user.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user.GetForegroundWindow.restype = wintypes.HWND
        user.SetForegroundWindow.argtypes = [wintypes.HWND]
        user.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]

        def identify(hwnd):
            pid = wintypes.DWORD()
            user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            process = psutil.Process(pid.value)
            name = process.name().lower()
            if rule["mode"] != "unrestricted" and name not in rule["apps"]:
                raise ValueError("This application is outside the approved scope")
            text = ctypes.create_unicode_buffer(min(user.GetWindowTextLengthW(hwnd), 500) + 1)
            user.GetWindowTextW(hwnd, text, len(text))
            return {
                "window_id": hwnd,
                "process_id": pid.value,
                "process_started": process.create_time(),
                "app": name,
                "title": text.value,
            }

        if operation == "windows.list":
            windows = []
            callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

            def collect(hwnd, _):
                if user.IsWindowVisible(hwnd):
                    try:
                        item = identify(hwnd)
                        if item["title"]:
                            windows.append(item)
                    except (ValueError, psutil.Error):
                        pass  # Unapproved or already-closed windows are intentionally excluded.
                return True

            user.EnumWindows(callback_type(collect), 0)
            return {"windows": windows[:50]}
        hwnd = args["window_id"]
        target = identify(hwnd)
        if operation in {"windows.type", "windows.key"} and target["app"] in BLOCKED_INPUT_APPS:
            raise ValueError("Use scoped Terminal and Browser actions for this app")
        if (
            target["process_id"] != args["process_id"]
            or abs(target["process_started"] - args["process_started"]) > 0.01
        ):
            raise ValueError("The target window changed. Find windows again.")
        if operation == "windows.minimize":
            user.ShowWindow(hwnd, 6)
            return {"minimized": True, **target}
        user.ShowWindow(hwnd, 9)
        user.SetForegroundWindow(hwnd)
        if user.GetForegroundWindow() != hwnd:
            raise ValueError("Windows did not allow focus. Select the target app and try again.")
        if operation == "windows.focus":
            return {"focused": True, **target}

        class Keyboard(ctypes.Structure):
            _fields_ = [
                ("vk", wintypes.WORD),
                ("scan", wintypes.WORD),
                ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("extra", ctypes.c_size_t),
            ]

        class Mouse(ctypes.Structure):
            _fields_ = [
                ("dx", wintypes.LONG),
                ("dy", wintypes.LONG),
                ("data", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("extra", ctypes.c_size_t),
            ]

        class Union(ctypes.Union):
            _fields_ = [("keyboard", Keyboard), ("mouse", Mouse)]

        class Input(ctypes.Structure):
            _fields_ = [("type", wintypes.DWORD), ("value", Union)]

        user.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int]
        entries = []

        def add(vk=0, scan=0, flags=0):
            entries.append(Input(1, Union(keyboard=Keyboard(vk, scan, flags, 0, 0))))

        if operation == "windows.type":
            encoded = args["text"].encode("utf-16-le")
            for index in range(0, len(encoded), 2):
                code = int.from_bytes(encoded[index : index + 2], "little")
                add(scan=code, flags=4)
                add(scan=code, flags=6)
        else:
            key = {"enter": 13, "tab": 9, "escape": 27, "ctrl+a": 65}[args["key"]]
            if args["key"] == "ctrl+a":
                add(vk=17)
            add(vk=key)
            add(vk=key, flags=2)
            if args["key"] == "ctrl+a":
                add(vk=17, flags=2)
        inputs = (Input * len(entries))(*entries)
        if user.GetForegroundWindow() != hwnd or user.SendInput(
            len(entries), inputs, ctypes.sizeof(Input)
        ) != len(entries):
            raise ValueError("Input could not be delivered safely to the approved window")
        return {"input_sent": True, **target}

    async def perform(self, action, rule, scope_guard=None):
        self.check()
        task = asyncio.current_task()
        self.active_tasks.add(task)
        try:
            if action.operation == "terminal.run":
                return await self.terminal(action.arguments)
            if action.operation.startswith("project."):
                return await self.projects.perform(action, rule, scope_guard)
            if action.operation.startswith("browser."):
                return await self.browser(
                    action.operation, action.arguments, rule, action.crew_id or "core", scope_guard
                )
            if action.operation.startswith(("notes.", "files.")):
                from mavo import local_files

                return await asyncio.to_thread(
                    local_files.perform, action.operation, action.arguments, rule
                )
            if action.operation in {
                "windows.lock",
                "windows.volume",
                "windows.media",
                "windows.speak",
            }:
                from mavo import pc_controls

                return await asyncio.to_thread(
                    pc_controls.perform, action.operation, action.arguments
                )
            if action.operation == "windows.sleep":
                # It returns only when the computer wakes: off the event loop.
                return await asyncio.to_thread(
                    self.windows, action.operation, action.arguments, rule
                )
            return self.windows(action.operation, action.arguments, rule)
        except (ValueError, OSError):
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Desktop tool failed ({type(exc).__name__}). Check the target before retrying."
            ) from None
        finally:
            self.active_tasks.discard(task)
