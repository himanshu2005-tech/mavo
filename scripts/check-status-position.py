"""Move Mavo's own desktop button to screen edges and verify native work-area bounds."""
import asyncio
import ctypes
import json
import os
import sys
from ctypes import wintypes
from pathlib import Path

import httpx
import psutil
from websockets.asyncio.client import connect

ROOT = Path(__file__).resolve().parents[1]


async def main():
    if os.name != "nt":
        raise RuntimeError("This native desktop check requires Windows")
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user.MonitorFromWindow.restype = wintypes.HANDLE
    windows = []

    def collect(hwnd, _):
        text = ctypes.create_unicode_buffer(100)
        user.GetWindowTextW(hwnd, text, 100)
        if text.value == "Mavo Status":
            pid = wintypes.DWORD()
            user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if Path(psutil.Process(pid.value).exe()) == ROOT / "src-tauri/target/debug/mavo.exe":
                windows.append(hwnd)
        return True

    user.EnumWindows(callback_type(collect), 0)
    assert len(windows) == 1, "Expected the status window belonging to this Mavo development instance"
    hwnd = windows[0]

    class MonitorInfo(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("monitor", wintypes.RECT), ("work", wintypes.RECT), ("flags", wintypes.DWORD)]

    user.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]

    def rect():
        value = wintypes.RECT()
        assert user.GetWindowRect(hwnd, ctypes.byref(value))
        return [value.left, value.top, value.right, value.bottom]

    def area():
        value = MonitorInfo()
        value.size = ctypes.sizeof(value)
        assert user.GetMonitorInfoW(user.MonitorFromWindow(hwnd, 2), ctypes.byref(value))
        return [value.work.left, value.work.top, value.work.right, value.work.bottom]

    def within():
        box, work = rect(), area()
        assert box[0] >= work[0] and box[1] >= work[1] and box[2] <= work[2] and box[3] <= work[3], (box, work)
        return {"window": box, "work_area": work}

    if "--dock-only" in sys.argv:
        position = within()
        box, work = position["window"], position["work_area"]
        assert 8 <= work[2] - box[2] <= 48, position
        assert 8 <= work[3] - box[3] <= 48, position
        print("Desktop button: bottom-right docking verified without debugging.")
        return
    targets = httpx.get("http://127.0.0.1:9222/json/list").json()
    target = next(t for t in targets if "overlay=status" in t["url"])
    report, original = [], rect()
    async with connect(target["webSocketDebuggerUrl"]) as socket:
        sequence = 0

        async def run(expression):
            nonlocal sequence
            sequence += 1
            await socket.send(json.dumps({"id": sequence, "method": "Runtime.evaluate", "params": {"expression": expression, "returnByValue": True, "awaitPromise": True}}))
            while True:
                result = json.loads(await asyncio.wait_for(socket.recv(), 10))
                if result.get("id") == sequence:
                    assert "error" not in result and "exceptionDetails" not in result["result"], result
                    return result["result"]["result"].get("value")

        try:
            await run("document.querySelector('[aria-label=\"Collapse status\"]')?.click()")
            await asyncio.sleep(.3)
            original = rect()
            if "--check-dock" in sys.argv:
                work = area()
                assert 8 <= work[2] - original[2] <= 48, (original, work)
                assert 8 <= work[3] - original[3] <= 48, (original, work)
            left, top, right, bottom = area()
            for x, y in ((left - 15, top - 15), (right - 10, top - 15), (left - 15, bottom - 10), (right - 10, bottom - 10)):
                assert user.SetWindowPos(hwnd, None, x, y, 0, 0, 0x15)
                await asyncio.sleep(.4)
                collapsed = within()
                await run("document.querySelector('[aria-label^=\"Mavo status\"]').click()")
                await asyncio.sleep(.4)
                expanded = within()
                assert not await run("document.documentElement.scrollWidth>innerWidth||document.documentElement.scrollHeight>innerHeight")
                await run("document.querySelector('[aria-label=\"Collapse status\"]').click()")
                await asyncio.sleep(.3)
                collapsed_again = within()
                assert collapsed_again["window"] == collapsed["window"], "Collapsing moved the icon away from its original position"
                report.append({"collapsed": collapsed, "expanded": expanded, "collapsed_again": collapsed_again})
        finally:
            await run("document.querySelector('[aria-label=\"Collapse status\"]')?.click()")
            await asyncio.sleep(.2)
            user.SetWindowPos(hwnd, None, original[0], original[1], 0, 0, 0x15)
    (ROOT / ".build/ui-check/status-position.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("Native desktop button: all four edges, expand/collapse, DPI, taskbar bounds, and layout passed.")


if __name__ == "__main__":
    asyncio.run(main())
