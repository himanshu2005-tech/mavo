"""Keyboard shortcuts that run an agent at once ("when I press Win+F9, check my credits").

A shortcut is the person's, set when the agent is made ("when I press...") or later in its settings.
Mavo registers it with Windows while it runs, so it works from any app, with Mavo's window closed.
A press runs the agent now, as "Check now" does, besides any schedule it has. A shortcut that
Windows or another app already uses cannot be taken: the agent says so in its settings."""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
from collections.abc import Callable

from sqlalchemy import select

from mavo.database import CrewInstance, utcnow

logger = logging.getLogger("mavo.hotkeys")

MODIFIERS = {
    "ctrl": "Ctrl",
    "control": "Ctrl",
    "alt": "Alt",
    "option": "Alt",
    "shift": "Shift",
    "win": "Win",
    "windows": "Win",
    "super": "Win",
    "meta": "Win",
    "cmd": "Win",
    "command": "Win",
}
ORDER = ["Ctrl", "Alt", "Shift", "Win"]
MOD_BITS = {"Alt": 0x0001, "Ctrl": 0x0002, "Shift": 0x0004, "Win": 0x0008}
MOD_NOREPEAT = 0x4000
NAMED = {
    "space": ("Space", 0x20),
    "insert": ("Insert", 0x2D),
    "delete": ("Delete", 0x2E),
    "del": ("Delete", 0x2E),
    "home": ("Home", 0x24),
    "end": ("End", 0x23),
    "pageup": ("PageUp", 0x21),
    "pagedown": ("PageDown", 0x22),
    "pause": ("Pause", 0x13),
    "printscreen": ("PrintScreen", 0x2C),
    "prtsc": ("PrintScreen", 0x2C),
}


def parse(text: str | None) -> tuple[str, int, int] | None:
    """ "win + f9" → ("Win+F9", modifier bits, key code), or None when it is not a shortcut Mavo
    can register: a key with at least one modifier, or F13–F24 alone."""
    if not text or not str(text).strip():
        return None
    parts = [p.strip().casefold() for p in str(text).replace(" ", "").split("+") if p.strip()]
    if not parts:
        return None
    mods = []
    for part in parts[:-1]:
        name = MODIFIERS.get(part)
        if not name or name in mods:
            return None
        mods.append(name)
    key = parts[-1]
    if key in MODIFIERS:
        return None
    if key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        label, code = key.upper(), 0x70 + int(key[1:]) - 1
    elif len(key) == 1 and (key.isalpha() or key.isdigit()):
        label, code = key.upper(), ord(key.upper())
    elif key in NAMED:
        label, code = NAMED[key]
    else:
        return None
    if not mods and not (label.startswith("F") and label[1:].isdigit() and int(label[1:]) >= 13):
        return None  # a plain key would fire while typing
    mods = sorted(mods, key=ORDER.index)
    bits = 0
    for m in mods:
        bits |= MOD_BITS[m]
    return "+".join([*mods, label]), bits, code


def normalize(text: str | None) -> str | None:
    found = parse(text)
    return found[0] if found else None


def hotkey_of(crew: CrewInstance) -> str | None:
    """Its shortcut: the one set in its settings, else the one it was made with."""
    plan = crew.configuration_plan or {}
    if "hotkey" in plan:
        return normalize(plan.get("hotkey"))
    return normalize((plan.get("draft") or {}).get("hotkey"))


def wanted(session) -> dict[str, tuple[str, int, int]]:
    """Each running agent's shortcut; two agents never share one (the first made keeps it)."""
    out, taken = {}, set()
    for crew in session.scalars(
        select(CrewInstance)
        .where(
            CrewInstance.status == "active",
            CrewInstance.is_running.is_(True),
            CrewInstance.hidden.is_(False),
        )
        .order_by(CrewInstance.created_at)
    ):
        found = parse(hotkey_of(crew))
        if found and found[0] not in taken:
            out[crew.id] = found
            taken.add(found[0])
    return out


def run_now(factory, crew_id: str) -> bool:
    """A press: the agent runs now, as Check now does. False when it cannot (stopped, busy)."""
    with factory() as session:
        crew = session.get(CrewInstance, crew_id)
        if crew is None or not crew.is_running or crew.runtime_state == "working":
            return False
        crew.runtime_state = "waiting"
        crew.last_runtime_error = None
        crew.next_run_at = utcnow()
        crew.observation = {
            **(crew.observation or {}),
            "failures": 0,
            "run_failures": 0,
            "automation_steps": 0,
            "hotkey_pressed": utcnow().isoformat(),
        }
        session.commit()
        return True


def record(factory, problems: dict[str, str | None]) -> None:
    """Whether each shortcut could be registered, kept with its agent for its settings."""
    with factory() as session:
        for crew_id, problem in problems.items():
            crew = session.get(CrewInstance, crew_id)
            if crew is None:
                continue
            observation = dict(crew.observation or {})
            if problem:
                observation["hotkey_problem"] = problem
            else:
                observation.pop("hotkey_problem", None)
            crew.observation = observation
        session.commit()


class WindowsHotkeys(threading.Thread):
    """Registers shortcuts on its own thread (Windows sends a press to the thread that registered
    it) and runs a message loop; `sync` hands it the shortcuts wanted now."""

    WM_HOTKEY, WM_SYNC, WM_QUIT_LOOP = 0x0312, 0x8001, 0x8002

    def __init__(self, on_press: Callable[[str], None], on_result: Callable[[dict], None]):
        super().__init__(daemon=True, name="mavo-hotkeys")
        self.on_press, self.on_result = on_press, on_result
        self.desired: dict[str, tuple[str, int, int]] = {}
        self.ids: dict[int, str] = {}
        self.thread_id = 0
        self.ready = threading.Event()
        self.lock = threading.Lock()

    def run(self) -> None:  # pragma: no cover - Windows only, needs a desktop session
        import ctypes
        from ctypes import wintypes

        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        self.thread_id = kernel32.GetCurrentThreadId()
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)  # make this thread's message queue
        self.ready.set()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == self.WM_HOTKEY:
                crew_id = self.ids.get(int(msg.wParam))
                if crew_id:
                    self.on_press(crew_id)
            elif msg.message == self.WM_SYNC:
                for hotkey_id in list(self.ids):
                    user32.UnregisterHotKey(None, hotkey_id)
                self.ids.clear()
                with self.lock:
                    desired = dict(self.desired)
                problems = {}
                for n, (crew_id, (label, bits, code)) in enumerate(desired.items(), start=1):
                    if user32.RegisterHotKey(None, n, bits | MOD_NOREPEAT, code):
                        self.ids[n] = crew_id
                        problems[crew_id] = None
                    else:
                        problems[crew_id] = (
                            f"{label} is already used by Windows or another app. Pick another."
                        )
                self.on_result(problems)
            elif msg.message == self.WM_QUIT_LOOP:
                break
        for hotkey_id in list(self.ids):
            user32.UnregisterHotKey(None, hotkey_id)

    def sync(self, desired: dict[str, tuple[str, int, int]]) -> None:  # pragma: no cover
        import ctypes

        with self.lock:
            self.desired = dict(desired)
        if self.ready.wait(5):
            ctypes.windll.user32.PostThreadMessageW(self.thread_id, self.WM_SYNC, 0, 0)

    def stop(self) -> None:  # pragma: no cover
        import ctypes

        if self.thread_id:
            ctypes.windll.user32.PostThreadMessageW(self.thread_id, self.WM_QUIT_LOOP, 0, 0)


async def keep_hotkeys(factory, every: float = 3.0) -> None:  # pragma: no cover - runs with the app
    """Keeps Windows' shortcuts in step with the agents: added, changed, removed, stopped."""
    if sys.platform != "win32":
        return
    loop = asyncio.get_running_loop()

    def pressed(crew_id: str) -> None:
        loop.call_soon_threadsafe(
            lambda: run_now(factory, crew_id) and logger.info("Shortcut ran %s", crew_id)
        )

    def result(problems: dict) -> None:
        loop.call_soon_threadsafe(record, factory, problems)

    service, last = None, None
    try:
        while True:
            with factory() as session:
                now = wanted(session)
            if now != last:
                if service is None and now:
                    service = WindowsHotkeys(pressed, result)
                    service.start()
                if service is not None:
                    service.sync(now)
                last = now
            await asyncio.sleep(every)
    finally:
        if service is not None:
            service.stop()
