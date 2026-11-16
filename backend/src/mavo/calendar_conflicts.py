"""Is a time free? Mavo checks this itself before an event goes on the person's calendar, never
leaving it to a model: a booker picked the 11:00 the sender offered first though the calendar
showed it busy, and only a later check stopped the reply.

Only timed events on the person's own calendar block a time. A holiday calendar's all-day
entry (read-only) and the person's own all-day notes do not; an event that ends exactly when
the new one starts does not overlap it."""

from __future__ import annotations

from datetime import datetime


def _when(value) -> datetime | None:
    if not isinstance(value, dict) or not value.get("dateTime"):
        return None  # all-day ("date") or missing
    try:
        moment = datetime.fromisoformat(str(value["dateTime"]).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.astimezone()


def _aware(value) -> datetime | None:
    if isinstance(value, datetime):
        moment = value
    else:
        try:
            moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    return moment if moment.tzinfo else moment.astimezone()


def clash(items, start, end, ignore_id: str | None = None) -> dict | None:
    """The first event in `items` (a calendar read) that overlaps start..end, or None."""
    begins, ends = _aware(start), _aware(end)
    if begins is None or ends is None or ends <= begins:
        return None
    for item in items or []:
        if not isinstance(item, dict) or item.get("read_only") or item.get("status") == "cancelled":
            continue
        if ignore_id and item.get("id") == ignore_id:
            continue  # the event being moved
        other_start, other_end = _when(item.get("start")), _when(item.get("end"))
        if other_start is None or other_end is None:
            continue
        if begins < other_end and ends > other_start:
            return item
    return None


def busy_reason(item: dict) -> str:
    """Why the time was refused, for the agent that chose it."""
    start, end = _when(item.get("start")), _when(item.get("end"))
    span = f" {start:%a %d %b %H:%M}–{end:%H:%M}" if start and end else ""
    return (
        f"That time is busy: '{str(item.get('summary') or 'an event')[:80]}'{span} is on the "
        "calendar. Choose another time that is free (one the sender offered, if they offered "
        "times); never move or double-book the existing event."
    )
