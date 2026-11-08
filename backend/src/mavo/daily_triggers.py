"""Validated, bounded local reminder triggers; never call a model or execute tools."""

import hashlib
from datetime import datetime, time, timedelta

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DailyTrigger(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,40}$")
    time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    message: str = Field(default="", max_length=1000)
    weekdays: list[int] = Field(default_factory=lambda: list(range(7)), min_length=1, max_length=7)
    enabled: bool = True

    @field_validator("weekdays")
    @classmethod
    def valid_days(cls, value):
        if len(set(value)) != len(value) or any(day not in range(7) for day in value):
            raise ValueError("Weekdays must be unique numbers from 0 (Monday) to 6 (Sunday)")
        return sorted(value)

    @model_validator(mode="after")
    def stable_identity(self):
        if self.id is None:
            raw = self.time + ":" + ",".join(map(str, self.weekdays))
            self.id = "daily-" + hashlib.sha256(raw.encode()).hexdigest()[:20]
        return self


def unique_daily_triggers(values):
    if values is not None and len({rule.id for rule in values}) != len(values):
        raise ValueError("Each daily trigger needs a different identity/time and weekdays")
    return values


def rules_for(crew):
    if crew.daily_triggers:
        return [DailyTrigger.model_validate(rule) for rule in crew.daily_triggers]
    if crew.schedule_time:
        return [
            DailyTrigger(
                id="legacy",
                time=crew.schedule_time,
                message=crew.reminder_text or crew.responsibilities[:1000],
            )
        ]
    return []


def next_occurrence(crew, now):
    local_now = now.astimezone()
    values = []
    for rule in rules_for(crew):
        if not rule.enabled:
            continue
        for offset in range(8):
            date = local_now.date() + timedelta(days=offset)
            if date.weekday() not in rule.weekdays:
                continue
            hour, minute = map(int, rule.time.split(":"))
            at = datetime.combine(date, time(hour, minute), tzinfo=local_now.tzinfo)
            if at > local_now:
                values.append(at)
                break
    return min(values) if values else None


def local_reminder(value):
    """Daily work is a deterministic text notification, not an AI workflow."""
    return value.trigger_type == "daily" and value.template_id == "schedule"
