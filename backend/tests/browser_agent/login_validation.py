# ruff: noqa: E501
"""Is Mavo's idea of "signed in" right, on real sites and in the real browser? Slow, uses the
network and a visible Edge for a few seconds, so it is a script, not a test.

    python -m tests.browser_agent.login_validation [--profile <automation dir>]

1. Detection: a real site with no sign-in saved must read as signed out (or captcha), never signed in.
2. Sessions: a sign-in made in the person's sign-in window must still be there for Mavo's background
   browser, however the window is closed, and after the background browser restarts.
3. Placement: the sign-in window must open where the person can reach it.
4. With --profile (a COPY of the app's automation dir): the real portal reads as signed in."""

import asyncio
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import psutil

from mavo.desktop_actions import DesktopActions, fit_on_screen, work_area

# (site, what a correct answer is)
SITES = [
    ("https://github.com/", {"out"}),
    ("https://news.ycombinator.com/", {"out"}),
    ("https://en.wikipedia.org/wiki/Main_Page", {"out"}),
    ("https://stackoverflow.com/", {"out"}),
    ("https://www.airouter.in/", {"out"}),
    ("https://www.linkedin.com/", {"out"}),
    ("https://accounts.google.com/", {"out"}),
    ("https://placements.haveloc.com/jobs", {"out", "captcha"}),
    ("https://example.com/", {"unknown"}),
    ("https://httpbin.org/", {"unknown"}),
]
SET_SESSION = "https://httpbin.org/cookies/set?session_cookie=alive"
RULE = {"enabled": True, "mode": "restricted", "domains": ["httpbin.org"], "revision": "v"}
rows = []


def record(area, name, ok, detail):
    rows.append((area, name, ok, detail))
    print(("PASS" if ok else "FAIL"), f"{area}: {name} | {detail}", flush=True)


async def detection():
    """Each site is opened by Mavo's own browser code on an empty profile, like a first visit."""
    for url, expected in SITES:
        host = url.split("/")[2]
        root = Path(tempfile.mkdtemp(prefix="vdet"))
        desktop = DesktopActions(root)
        marker = str(Path(desktop.profile_dir())).lower()
        rule = {"enabled": True, "mode": "restricted", "domains": [host], "revision": "v"}
        try:
            opened = await asyncio.wait_for(
                desktop.browser("browser.open", {"url": url}, rule, "v"), timeout=150
            )
            wall = (opened.get("sign_in_required") or {}).get("kind")
            answer = (
                "captcha"
                if wall == "captcha"
                else "out"
                if (wall or opened.get("session") == "out")
                else opened.get("session", "unknown")
            )
            record(
                "detection",
                host,
                answer in expected,
                f"read as {answer!r}, wanted {sorted(expected)}; page: {opened.get('title', '')[:40]!r}",
            )
        except Exception as exc:
            record("detection", host, False, f"{type(exc).__name__}: {str(exc)[:90]}")
        finally:
            try:
                await asyncio.wait_for(desktop.close_owner("v"), timeout=30)
            except Exception:
                pass
            kill_profile(marker)
            await asyncio.sleep(1)
            shutil.rmtree(root, ignore_errors=True)


def kill_profile(marker):
    for p in psutil.process_iter(["pid", "cmdline"]):
        if marker in " ".join(p.info.get("cmdline") or []).lower():
            try:
                p.kill()
            except Exception:
                pass


async def cookies_seen(desktop, owner):
    await desktop.browser("browser.open", {"url": "https://httpbin.org/cookies"}, RULE, owner)
    read = await desktop.browser("browser.read", {}, RULE, owner)
    try:
        return sorted(json.loads(read["text"]).get("cookies", {}))
    except Exception:
        return ["unreadable"]


async def sessions():
    for how in ("Mavo closes it", "the person closes it with the X"):
        root = Path(tempfile.mkdtemp(prefix="vsess"))
        desktop = DesktopActions(root)
        marker = str(Path(desktop.profile_dir())).lower()
        try:
            desktop.open_user_window(SET_SESSION)
            await asyncio.sleep(7)
            if how == "Mavo closes it":
                await desktop.close_user_window()
            else:
                for pid in desktop.user_window_pids():
                    subprocess.run(["taskkill", "/PID", str(pid)], capture_output=True)
                for _ in range(40):
                    if not desktop.user_window_open():
                        break
                    await asyncio.sleep(0.4)
            await asyncio.sleep(1.5)
            first = await cookies_seen(desktop, "one")
            await desktop.close_owner("one")
            await asyncio.sleep(1.5)
            second = await cookies_seen(desktop, "two")
            await desktop.close_owner("two")
            record(
                "sessions",
                f"a session-only sign-in, window closed: {how}",
                "session_cookie" in first and "session_cookie" in second,
                f"background browser saw {first}, then after a restart {second}",
            )
        finally:
            kill_profile(marker)
            await asyncio.sleep(1.5)
            shutil.rmtree(root, ignore_errors=True)


def windows_of(desktop):
    import ctypes
    from ctypes import wintypes

    owners = set(desktop.user_window_pids())
    for pid in list(owners):
        owners.update(c.pid for c in psutil.Process(pid).children(recursive=True))
    found, user32 = [], ctypes.windll.user32

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
            r = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            if r.right - r.left > 600:
                found.append((r.left, r.top, r.right, r.bottom))
        return True

    user32.EnumWindows(ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(visit), 0)
    return found


async def placement():
    root = Path(tempfile.mkdtemp(prefix="vplace"))
    desktop = DesktopActions(root)
    marker = str(Path(desktop.profile_dir())).lower()
    try:
        desktop.open_user_window("https://example.com")
        await asyncio.sleep(7)
        seen = windows_of(desktop)
        area = work_area()
        record(
            "window",
            "the sign-in window opens on the screen",
            bool(seen) and all(fit_on_screen(w, area) is None for w in seen),
            f"window {seen}, usable screen {area}",
        )
    finally:
        kill_profile(marker)
        await asyncio.sleep(1.5)
        shutil.rmtree(root, ignore_errors=True)


async def portal(profile_root):
    copy = Path(tempfile.mkdtemp(prefix="vportal"))
    shutil.copytree(Path(profile_root) / "browser", copy / "browser")
    desktop = DesktopActions(copy)
    marker = str(Path(desktop.profile_dir())).lower()
    host = "placements.haveloc.com"
    rule = {"enabled": True, "mode": "restricted", "domains": [host], "revision": "v"}
    try:
        closed = desktop.saved_session(host)
        opened = await asyncio.wait_for(
            desktop.browser("browser.open", {"url": f"https://{host}/jobs"}, rule, "v"),
            timeout=150,
        )
        wall = opened.get("sign_in_required")
        # While the browser runs its cookie file is locked: it must answer by asking the browser.
        running = await desktop.saved_login(host)
        record(
            "real portal",
            "your placements portal, signed in",
            not wall and closed is True and running is True,
            f"no sign-in wall={not wall}, saved login (browser closed)={closed}, (browser running)={running}, session hint={opened.get('session')!r}, page: {opened.get('title')!r}",
        )
    finally:
        try:
            await asyncio.wait_for(desktop.close_owner("v"), timeout=30)
        except Exception:
            pass
        kill_profile(marker)
        await asyncio.sleep(1)
        shutil.rmtree(copy, ignore_errors=True)


async def main():
    await detection()
    await sessions()
    await placement()
    if "--profile" in sys.argv:
        await portal(sys.argv[sys.argv.index("--profile") + 1])
    passed = sum(1 for r in rows if r[2])
    print(f"\n{passed}/{len(rows)} checks passed")
    return rows


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(main(), timeout=1500))
    time.sleep(0.2)
