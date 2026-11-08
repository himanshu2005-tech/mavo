"""Small, harmless controls of this computer: lock it, set the volume, play or pause what is
playing, and say something aloud. Each uses Windows' own way of doing it (the same keys and voice
a person has), so nothing extra is installed and nothing runs in the background afterwards.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class NoArgs(Args):
    """Lock the computer: the screen goes to the sign-in page; nothing is closed."""


class Volume(Args):
    """Set the speaker volume, 0 (silent) to 100."""

    level: int = Field(ge=0, le=100)


class Media(Args):
    """Control whatever is playing (Spotify, a browser video, any player), like the media keys:
    play_pause, next, previous or stop."""

    key: Literal["play_pause", "next", "previous", "stop"]


class Speak(Args):
    """Say something aloud with Windows' voice. Keep it short and plain."""

    text: str = Field(min_length=1, max_length=1000)


PC_MODELS = {
    "windows.lock": NoArgs,
    "windows.volume": Volume,
    "windows.media": Media,
    "windows.speak": Speak,
}

VK = {
    "play_pause": 0xB3,
    "next": 0xB0,
    "previous": 0xB1,
    "stop": 0xB2,
    "volume_down": 0xAE,
    "volume_up": 0xAF,
}


def press(vk: int, times: int = 1):
    """Presses a key the way a keyboard does (down, then up)."""
    user = ctypes.WinDLL("user32", use_last_error=True)
    for _ in range(times):
        user.keybd_event(vk, 0, 1, 0)  # extended key, down
        user.keybd_event(vk, 0, 3, 0)  # up


def windows_only():
    if os.name != "nt":
        raise ValueError("This needs Windows")


def lock():
    windows_only()
    if not ctypes.WinDLL("user32").LockWorkStation():
        raise ValueError("Windows did not lock the computer")
    return {"locked": True}


def set_volume(level: int):
    """Each volume key moves Windows' volume by 2: all the way down, then up to the level."""
    windows_only()
    press(VK["volume_down"], 50)
    steps = round(level / 2)
    if steps:
        press(VK["volume_up"], steps)
    return {"volume": steps * 2}


def media(key: str):
    windows_only()
    press(VK[key])
    return {"pressed": key}


SPEAK_SCRIPT = (
    "Add-Type -AssemblyName System.Speech; "
    "$voice = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
    "$voice.Speak([Console]::In.ReadToEnd())"
)


def speak(text: str):
    """Windows' built-in voice. The text goes in on standard input, never on the command line,
    so nothing in it is run."""
    windows_only()
    done = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", SPEAK_SCRIPT],
        input=text,
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=180,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if done.returncode:
        raise ValueError(
            "Windows could not speak it: " + (done.stderr.strip()[:200] or "unknown error")
        )
    return {"spoken": True, "characters": len(text)}


def perform(operation, args):
    """Runs one control (blocking: speaking takes as long as the sentence)."""
    if operation == "windows.lock":
        return lock()
    if operation == "windows.volume":
        return set_volume(args["level"])
    if operation == "windows.media":
        return media(args["key"])
    if operation == "windows.speak":
        return speak(args["text"])
    raise ValueError("Unknown control")
