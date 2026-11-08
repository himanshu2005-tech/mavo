"""Validated read-only device monitoring settings, independent of model prompts."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DeviceMonitor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["system", "storage"] = "system"
    drive: str = "system"
    notifications: Literal["alerts", "each_check", "changes"] = "alerts"
    min_free_gb: float = Field(default=5, ge=0, le=10000)
    # Alert when the battery, unplugged, loses at least this many percent an hour ("remind me
    # whenever it drains fast"): measured by Mavo from its own readings, not left to a model.
    battery_drain_per_hour: float | None = Field(default=None, ge=1, le=100)
    # Free space each check, done by Mavo itself and only with the Crew's device:clean_temp grant:
    # delete the person's own temp files older than this many days ("clear my temp folder every
    # 3 hours"), and empty the Recycle Bin when they asked to clear drive space.
    clean_temp_days: int | None = Field(default=None, ge=1, le=90)
    empty_recycle_bin: bool = False

    @field_validator("drive")
    @classmethod
    def local_drive_only(cls, value):
        value = value.strip().upper().rstrip("\\/")
        if value == "SYSTEM":
            return "system"
        if not re.fullmatch(r"[A-Z]:", value):
            raise ValueError("Choose system or a local drive letter such as C:")
        return value


def legacy_monitor(instructions: str) -> DeviceMonitor:
    """Narrow compatibility for pre-structured drive checks; never grants access."""
    drive = re.search(r"\b([a-z])(?::|\s+drive\b)", instructions, re.I)
    storage = bool(
        drive and re.search(r"storage|disk|free space|space remaining", instructions, re.I)
    )
    each = bool(
        re.search(r"(?:report|notif\w*|send)[^.]*\b(?:every|each)\s+check", instructions, re.I)
    )
    return DeviceMonitor(
        kind="storage" if storage else "system",
        drive=drive[1].upper() + ":" if storage else "system",
        notifications="each_check" if each else "alerts",
    )


DRAIN = re.compile(r"\bdrain\w*|\b(drop\w*|los[est]\w*|fall\w*|fell)\b[^.]{0,40}%", re.I)
PERCENT = re.compile(r"(\d{1,2}(?:\.\d+)?)\s*%")


def drain_threshold(instructions: str) -> float | None:
    """ "Alert when it drops 10% or more in an hour" -> 10; "drains fast" alone -> 10."""
    if not DRAIN.search(instructions or ""):
        return None
    near = re.search(r"(\d{1,2}(?:\.\d+)?)\s*%[^.]{0,60}(hour|hr|60 ?min)", instructions, re.I)
    found = near or PERCENT.search(instructions)
    value = float(found[1]) if found else 10.0
    return value if 1 <= value <= 100 else 10.0


CLEAN_TEMP = re.compile(r"\b(clear|clean|delete|empty|wipe|purge)\w*\b[^.]{0,60}\btemp", re.I)
RECYCLE = re.compile(r"recycle\s*bin|clear (my )?drive space|free (up )?(drive|disk) space", re.I)


def clean_threshold(instructions: str) -> int | None:
    """ "Clear the temp folder of files older than 1 day" -> 1; "clean my temp folder" -> 1."""
    if not CLEAN_TEMP.search(instructions or ""):
        return None
    days = re.search(r"older than (\d{1,2})\s*(day|d\b)", instructions, re.I)
    return int(days[1]) if days and 1 <= int(days[1]) <= 90 else 1


def wants_recycle(instructions: str) -> bool:
    return bool(RECYCLE.search(instructions or ""))


def monitor_for(crew) -> DeviceMonitor:
    plan = crew.configuration_plan or {}
    saved = plan.get("device_monitor") or plan.get("draft", {}).get("device_monitor")
    monitor = (
        DeviceMonitor.model_validate(saved) if saved else legacy_monitor(crew.responsibilities)
    )
    if monitor.kind == "system" and monitor.battery_drain_per_hour is None:
        # A Crew set up before drain watching existed ("Battery Watch": "if the charge dropped
        # 10% or more in the last hour, notify") is read from what it was asked to do.
        drain = drain_threshold(crew.responsibilities)
        if drain:
            monitor = monitor.model_copy(update={"battery_drain_per_hour": drain})
    if monitor.clean_temp_days is None:
        # "Temp & Drive Cleaner" was set up to clear the temp folder and only ever read the drive.
        days = clean_threshold(crew.responsibilities)
        if days:
            monitor = monitor.model_copy(
                update={
                    "clean_temp_days": days,
                    "empty_recycle_bin": wants_recycle(crew.responsibilities),
                }
            )
    return monitor
