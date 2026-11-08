"""Start with Windows: one startup entry, chosen to suit an installed app or a development copy."""

import sys
from pathlib import Path

import pytest

from mavo import autostart


class FakeRegistry:
    """Just enough of winreg to prove what would be written to the Run key."""

    HKEY_CURRENT_USER = "HKCU"
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self):
        self.values = {}

    def OpenKey(self, root, path, reserved=0, access=0):  # noqa: N802 - mirrors winreg
        assert root == "HKCU" and path == autostart.RUN_KEY
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def QueryValueEx(self, key, name):  # noqa: N802
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name], self.REG_SZ

    def SetValueEx(self, key, name, reserved, kind, value):  # noqa: N802
        self.values[name] = value

    def DeleteValue(self, key, name):  # noqa: N802
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]


@pytest.fixture
def registry(monkeypatch):
    fake = FakeRegistry()
    monkeypatch.setattr(autostart, "_registry", lambda: fake)
    return fake


def test_an_installed_app_starts_itself():
    exe = Path(r"C:\Program Files\Mavo\mavo.exe")
    assert autostart.startup_command(exe) == ("app", r'"C:\Program Files\Mavo\mavo.exe"')


def test_a_development_copy_starts_through_the_project_script(tmp_path):
    (tmp_path / "scripts").mkdir()
    script = tmp_path / "scripts" / "start-mavo.ps1"
    script.write_text("# start")
    exe = Path(r"D:\work\Mavo\src-tauri\target\debug\mavo.exe")
    mode, command = autostart.startup_command(exe, tmp_path)
    assert mode == "development"
    assert command.startswith("powershell.exe") and "-WindowStyle Hidden" in command
    assert f'-File "{script}"' in command
    # A debug build with no start script, or no known app at all, cannot start itself.
    assert autostart.startup_command(exe, tmp_path / "elsewhere") is None
    assert autostart.startup_command(None) is None or autostart.app_executable() is not None


def test_the_startup_entry_is_added_and_removed(registry):
    assert autostart.is_enabled() is False
    autostart.set_enabled(True, r'"C:\Mavo\mavo.exe"')
    assert registry.values == {"Mavo": r'"C:\Mavo\mavo.exe"'} and autostart.is_enabled() is True
    autostart.set_enabled(False)
    assert registry.values == {} and autostart.is_enabled() is False
    autostart.set_enabled(False)  # turning off what is already off is fine


@pytest.mark.skipif(sys.platform != "win32", reason="the startup entry is a Windows setting")
def test_the_setting_is_read_and_changed_through_the_app(client, auth, registry, monkeypatch):
    monkeypatch.setattr(
        autostart, "startup_command", lambda *a, **k: ("app", r'"C:\Mavo\mavo.exe"')
    )
    first = client.get("/api/v1/system/autostart", headers=auth).json()
    assert first == {"supported": True, "enabled": False, "mode": "app"}
    on = client.put("/api/v1/system/autostart", headers=auth, json={"enabled": True}).json()
    assert on["enabled"] is True and registry.values["Mavo"] == r'"C:\Mavo\mavo.exe"'
    off = client.put("/api/v1/system/autostart", headers=auth, json={"enabled": False}).json()
    assert off["enabled"] is False and registry.values == {}
    assert (
        client.put("/api/v1/system/autostart", headers=auth, json={"enabled": "x"}).status_code
        == 422
    )


def test_a_copy_that_cannot_start_itself_says_so(client, auth, registry, monkeypatch):
    monkeypatch.setattr(autostart, "startup_command", lambda *a, **k: None)
    assert client.get("/api/v1/system/autostart", headers=auth).json()["supported"] is False
    refused = client.put("/api/v1/system/autostart", headers=auth, json={"enabled": True})
    assert refused.status_code == 409 and registry.values == {}
