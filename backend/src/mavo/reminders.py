from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, time

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mavo.daily_triggers import rules_for
from mavo.database import CrewInstance, ReminderEvent


def set_notification_access(session: Session, crew: CrewInstance, allowed: bool) -> None:
    """Reset delivery baselines on a grant transition, not physical alert hysteresis."""
    previously_allowed = "notifications:show" in crew.permissions
    crew.permissions = [p for p in crew.permissions if p != "notifications:show"] + (
        ["notifications:show"] if allowed else []
    )
    if previously_allowed != allowed:
        crew.observation = {
            key: value
            for key, value in crew.observation.items()
            if key not in {"delivered_status", "delivered_alerts", "delivered_device_error"}
        }
    if not allowed:
        session.execute(
            update(ReminderEvent)
            .where(
                ReminderEvent.crew_id == crew.id,
                ReminderEvent.kind == "crew_update",
                ReminderEvent.status == "pending",
            )
            .values(status="cancelled")
        )


DAILY_PATTERN = re.compile(
    r"\b(?:at\s*)?(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<period>am|pm)\b",
    re.IGNORECASE,
)


def parse_daily_time(text: str) -> str | None:
    lowered = text.lower()
    if "every day" not in lowered and "everyday" not in lowered and "daily" not in lowered:
        return None
    match = DAILY_PATTERN.search(text)
    if not match:
        return None
    hour = int(match.group("hour"))
    minute = int(match.group("minute") or 0)
    if hour < 1 or hour > 12 or minute > 59:
        return None
    if match.group("period").lower() == "pm" and hour != 12:
        hour += 12
    if match.group("period").lower() == "am" and hour == 12:
        hour = 0
    return f"{hour:02d}:{minute:02d}"


def backfill_daily_schedules(session: Session) -> None:
    crews = session.scalars(
        select(CrewInstance).where(
            CrewInstance.template_id == "schedule",
            CrewInstance.schedule_time.is_(None),
        )
    ).all()
    changed = False
    for crew in crews:
        parsed = parse_daily_time(crew.responsibilities)
        if parsed:
            crew.schedule_time = parsed
            crew.reminder_text = crew.responsibilities
            changed = True
    if changed:
        session.commit()


def create_due_reminders(session: Session, now: datetime | None = None) -> list[ReminderEvent]:
    local_now = now or datetime.now().astimezone()
    if local_now.tzinfo is None:
        local_now = local_now.astimezone()
    crews = session.scalars(
        select(CrewInstance).where(
            CrewInstance.trigger_type == "daily",
            CrewInstance.status == "active",
            CrewInstance.is_running.is_(True),
        )
    ).all()
    today = local_now.date().isoformat()
    for crew in crews:
        for rule in rules_for(crew):
            if not rule.enabled or local_now.weekday() not in rule.weekdays:
                continue
            hour, minute = (int(part) for part in rule.time.split(":"))
            scheduled_local = datetime.combine(
                local_now.date(), time(hour=hour, minute=minute), tzinfo=local_now.tzinfo
            )
            if scheduled_local > local_now:
                continue
            # The pre-multi-trigger key format is kept for a single legacy schedule so an
            # upgrade never re-delivers a reminder that was already shown today.
            short = hashlib.sha1(rule.id.encode()).hexdigest()[:8]
            key = f"{crew.id}:{today}" if rule.id == "legacy" else f"{crew.id}:{short}:{today}"
            event = ReminderEvent(
                crew_id=crew.id,
                crew_name=crew.name,
                message=rule.message or crew.reminder_text or crew.responsibilities,
                scheduled_for=scheduled_local.astimezone(UTC),
                local_date=today,
                deduplication_key=key,
                status="pending",
            )
            session.add(event)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                continue
            # The reminder has fired (once a day per time): Crew that hear from this one are
            # handed it and woken ("whenever my Lunch Reminder goes off, put my laptop to sleep").
            from mavo.crew_workers import deliver_handoffs

            deliver_handoffs(
                session, crew, f"{crew.name} went off at {rule.time}: {event.message}"[:1500]
            )
            session.commit()
    pending = list(
        session.scalars(
            select(ReminderEvent)
            .join(CrewInstance, CrewInstance.id == ReminderEvent.crew_id)
            .where(
                ReminderEvent.status == "pending",
                CrewInstance.status == "active",
                CrewInstance.is_running.is_(True),
                or_(CrewInstance.trigger_type == "daily", ReminderEvent.kind == "crew_update"),
                ReminderEvent.local_date == local_now.date().isoformat(),
                ReminderEvent.scheduled_for <= local_now.astimezone(UTC),
            )
            .order_by(ReminderEvent.scheduled_for)
        ).all()
    )
    result = []
    for event in pending:
        crew = session.get(CrewInstance, event.crew_id)
        if event.kind == "crew_update" and "notifications:show" not in crew.permissions:
            event.status = "cancelled"
        else:
            result.append(event)
    session.commit()
    return result
