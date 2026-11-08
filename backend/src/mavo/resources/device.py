from pathlib import Path

import psutil


def read_device_metrics() -> dict:
    """Call only after the worker verifies the Crew's explicit device:read grant."""
    battery = psutil.sensors_battery()
    disk = psutil.disk_usage(Path.home().anchor)
    return {
        "cpu_percent": psutil.cpu_percent(interval=0.2),
        "memory_percent": psutil.virtual_memory().percent,
        "storage_percent": disk.percent,
        "storage_free_gb": disk.free / 1024**3,
        "storage_total_gb": disk.total / 1024**3,
        "battery_percent": battery.percent if battery else None,
        "plugged_in": battery.power_plugged if battery else None,
    }


def read_storage_metrics(drive: str) -> dict:
    """Only disk counters; never enumerates or opens the drive's files."""
    from mavo.resources.device_config import DeviceMonitor

    drive = DeviceMonitor(drive=drive).drive
    path = Path.home().anchor if drive == "system" else drive + "\\"
    disk = psutil.disk_usage(path)
    return {
        "drive": path,
        "storage_percent": disk.percent,
        "storage_free_gb": disk.free / 1024**3,
        "storage_total_gb": disk.total / 1024**3,
    }


def describe_storage(metrics: dict, changed: bool = False) -> str:
    return (
        f"{metrics['drive']} {metrics['storage_free_gb']:.2f} GB free "
        f"({100 - metrics['storage_percent']:.1f}% free) "
        f"of {metrics['storage_total_gb']:.1f} GB."
        + ("" if changed else " Read-only check; no files changed.")
    )


def clean_temp(days: int, root: Path | None = None, now: float | None = None) -> dict:
    """Delete the person's own temp files (their %TEMP%) last changed more than `days` ago, and
    the folders that leaves empty. Never the folder itself, never through a link or junction
    (what is behind one is not temp), and a file in use is simply skipped."""
    import os
    import time

    root = (root or Path(os.environ.get("TEMP") or os.environ.get("TMP") or "")).resolve()
    if not root.name or not root.is_dir() or root == Path(root.anchor):
        raise OSError("The temp folder could not be found")
    cutoff = (now or time.time()) - days * 86400
    freed = deleted = skipped = 0

    def linked(path: Path) -> bool:
        return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())

    folders: list[Path] = []
    for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
        here = Path(current)
        # Never into a link or junction: whatever is behind one is not this temp folder.
        dirs[:] = [d for d in dirs if not linked(here / d)]
        folders += [here / d for d in dirs]
        for name in files:
            path = here / name
            try:
                if linked(path):
                    continue
                info = path.stat()
                if info.st_mtime > cutoff:
                    continue
                path.unlink()
                freed += info.st_size
                deleted += 1
            except OSError:
                skipped += 1  # in use, or not ours to delete
    for path in reversed(folders):  # deepest first, so a folder emptied of folders goes too
        try:
            if path.stat().st_mtime <= cutoff and not any(path.iterdir()):
                path.rmdir()
        except OSError:
            pass
    return {"freed_gb": freed / 1024**3, "deleted": deleted, "skipped": skipped, "days": days}


def empty_recycle_bin() -> float:
    """Empty the Recycle Bin on every drive (Windows' own call, no prompt or sound); the GB
    freed. Permanent: only with the person's grant for it."""
    import ctypes
    from ctypes import wintypes

    class Info(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("i64Size", ctypes.c_longlong),
            ("i64NumItems", ctypes.c_longlong),
        ]

    shell = ctypes.windll.shell32
    info = Info()
    info.cbSize = ctypes.sizeof(Info)
    shell.SHQueryRecycleBinW(None, ctypes.byref(info))
    if info.i64NumItems == 0:
        return 0.0
    # SHERB_NOCONFIRMATION | SHERB_NOPROGRESSUI | SHERB_NOSOUND
    result = shell.SHEmptyRecycleBinW(None, None, 0x1 | 0x2 | 0x4)
    if result not in (0, 1):  # 0: done; 1: S_FALSE, nothing to empty
        raise OSError(f"Windows could not empty the Recycle Bin ({result:#x})")
    return info.i64Size / 1024**3


def describe_cleanup(done: dict, bin_gb: float | None) -> str:
    age = "a day" if done["days"] == 1 else f"{done['days']} days"
    text = f"Cleared {done['freed_gb']:.2f} GB of temp files ({done['deleted']} older than {age})"
    if done["skipped"]:
        text += f", leaving {done['skipped']} in use"
    text += "."
    if bin_gb is not None:
        text += f" Emptied the Recycle Bin ({bin_gb:.2f} GB)."
    return text


def describe_metrics(metrics: dict) -> str:
    battery = metrics["battery_percent"]
    power_state = "charging" if metrics["plugged_in"] else "on battery"
    return (
        f"CPU: {metrics['cpu_percent']:.0f}%\nMemory: {metrics['memory_percent']:.0f}%\n"
        f"System drive used: {metrics['storage_percent']:.0f}%\n"
        + (
            "Battery: not available"
            if battery is None
            else f"Battery: {battery:.0f}% ({power_state})"
        )
    )


def battery_drain(log: list, now: float) -> dict | None:
    """How fast the battery is draining, from Mavo's own readings ([time, percent, plugged],
    oldest first, the last one now): only over a stretch unplugged the whole time, at least 15
    minutes and at most 2 hours long. None when it cannot be told."""
    if not log or log[-1][1] is None or log[-1][2]:
        return None
    current = log[-1][1]
    start = None
    for when, percent, plugged in reversed(log[:-1]):
        if plugged or percent is None or now - when > 7200:
            break
        start = (when, percent)
    if start is None or now - start[0] < 900:
        return None
    hours = (now - start[0]) / 3600
    dropped = start[1] - current
    if dropped <= 0:
        return None
    per_hour = dropped / hours
    return {
        "per_hour": per_hour,
        "from": start[1],
        "to": current,
        "minutes": round(hours * 60),
        "hours_left": current / per_hour,
    }


def describe_drain(drain: dict) -> str:
    left = drain["hours_left"]
    left_text = f"about {left:.0f} h left" if left >= 1.5 else f"about {left * 60:.0f} min left"
    return (
        f"Battery draining fast: {drain['per_hour']:.0f}% an hour "
        f"({drain['from']:.0f}% → {drain['to']:.0f}% in {drain['minutes']} min, unplugged), "
        f"{left_text}."
    )


def device_alerts(metrics: dict, previous: list[str]) -> list[str]:
    alerts = []
    for field, name in (
        ("cpu_percent", "CPU"),
        ("memory_percent", "Memory"),
        ("storage_percent", "Storage"),
    ):
        if metrics[field] >= (85 if name in previous else 90):
            alerts.append(name)
    battery = metrics["battery_percent"]
    if battery is not None and not metrics["plugged_in"]:
        if battery <= (25 if "Battery" in previous else 20):
            alerts.append("Battery")
    return alerts
