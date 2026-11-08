"""Start Mavo when the person signs in to Windows, so a restart never silently stops their Crew.

One value under the current user's "Run" key; nothing needs administrator rights and nothing is
installed. The installed app starts itself. A development build cannot (it needs its dev server),
so it starts through the project's own start script, which brings up the same things by hand."""

import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "Mavo"


def app_executable() -> Path | None:
    """The desktop app that launched this backend: the nearest parent process called mavo.exe."""
    try:
        import psutil

        for parent in psutil.Process().parents():
            if parent.name().lower() == "mavo.exe":
                return Path(parent.exe())
    except Exception:
        return None
    return None


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def startup_command(
    executable: Path | None = None, root: Path | None = None
) -> tuple[str, str] | None:
    """(mode, command line) for the startup entry, or None when this copy cannot start itself."""
    exe = executable if executable is not None else app_executable()
    if exe is None:
        return None
    if "\\target\\debug\\" in str(exe).lower().replace("/", "\\"):
        script = (root or project_root()) / "scripts" / "start-mavo.ps1"
        if not script.exists():
            return None
        shell = "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass"
        return "development", f'{shell} -File "{script}"'
    return "app", f'"{exe}"'


def _registry():
    if sys.platform != "win32":
        return None
    import winreg

    return winreg


def is_enabled() -> bool:
    registry = _registry()
    if registry is None:
        return False
    try:
        with registry.OpenKey(registry.HKEY_CURRENT_USER, RUN_KEY) as key:
            return bool(registry.QueryValueEx(key, VALUE_NAME)[0])
    except OSError:
        return False


def set_enabled(enabled: bool, command: str | None = None) -> None:
    registry = _registry()
    if registry is None:
        raise RuntimeError("Starting with Windows is only available on Windows.")
    with registry.OpenKey(registry.HKEY_CURRENT_USER, RUN_KEY, 0, registry.KEY_SET_VALUE) as key:
        if enabled:
            registry.SetValueEx(key, VALUE_NAME, 0, registry.REG_SZ, command or "")
        else:
            try:
                registry.DeleteValue(key, VALUE_NAME)
            except FileNotFoundError:
                pass


class AutostartChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


def status() -> dict:
    found = startup_command()
    return {
        "supported": sys.platform == "win32" and found is not None,
        "enabled": is_enabled(),
        "mode": found[0] if found else None,
    }


def build_autostart_api() -> APIRouter:
    router = APIRouter()

    @router.get("/system/autostart")
    def read_autostart():
        return status()

    @router.put("/system/autostart")
    def change_autostart(payload: AutostartChange):
        found = startup_command()
        if sys.platform != "win32" or found is None:
            raise HTTPException(409, "This copy of Mavo cannot start by itself.")
        try:
            set_enabled(payload.enabled, found[1])
        except OSError as exc:
            raise HTTPException(500, "Windows would not save the startup setting.") from exc
        return status()

    return router
