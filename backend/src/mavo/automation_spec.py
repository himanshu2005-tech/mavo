"""What a person chooses when they save an approved script run as an automation."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AutomationSave(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=40)
    every_minutes: int | None = Field(default=None, ge=5, le=10080)  # none: only when asked
    notify: Literal["never", "failure", "always"] = "failure"
