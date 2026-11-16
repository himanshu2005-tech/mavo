"""The parts of a workflow that are code, not a model: what each agent step must hand on (its
contract), the gates between steps, and finding a free time.

A workflow is written once by Mavo Core, every step and every branch of its job. At run time a
model reads and writes; code checks that what it handed on fits what the next step needs, lets
through only the items a gate allows, and does the calendar arithmetic. Every failure seen in
testing a mail-and-calendar crew was arithmetic left to a model: a busy 2:30 tried three times,
11:30 offered inside an 11:00 to 12:00 call, "Wednesday 11:00 works" not booked.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

EMAIL = re.compile(r"^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$")
# What a contract field may be. A list of words is "one of these".
TYPES = {
    "text": "text",
    "id": "an id exactly as it was read",
    "email": "one email address, no name",
    "number": "a number",
    "bool": "true or false",
    "time": "one exact time, ISO 8601 with its UTC offset (2026-11-17T15:00:00+05:30)",
    "times": 'a list of {"start": time, "end": time}, each an exact ISO 8601 time with its UTC '
    "offset; [] when none",
    "list": "a list of short texts",
}


# -- contracts ----------------------------------------------------------------------------------


def tidy_contract(value) -> dict | None:
    """{"list": bool, "fields": {name: type or [options]}, "required": [names]}, or None."""
    if not isinstance(value, dict) or not isinstance(value.get("fields"), dict):
        return None
    fields = {}
    for name, kind in list(value["fields"].items())[:20]:
        key = re.sub(r"[^a-zA-Z0-9_]", "_", str(name))[:40]
        if isinstance(kind, list) and kind:
            fields[key] = [str(k)[:40] for k in kind[:12]]
        elif isinstance(kind, str) and kind.strip().lower() in TYPES:
            fields[key] = kind.strip().lower()
        else:
            fields[key] = "text"
    # Only what the writer said is required: an email with no event to delete had a blank
    # "etag", was set aside as unreadable, and failed its test. A blank field is allowed;
    # an action that truly needs it refuses that one item.
    required = [r for r in value.get("required") or [] if r in fields]
    return {"list": bool(value.get("list", True)), "fields": fields, "required": required}


def contract_text(contract: dict) -> str:
    """What the step is told to reply with, from its contract."""
    lines = [
        f'- "{name}": '
        + (f"one of {', '.join(kind)}" if isinstance(kind, list) else TYPES[kind])
        + ("" if name in contract["required"] else " (may be null)")
        for name, kind in contract["fields"].items()
    ]
    shape = (
        "ONLY a JSON array of objects, one per item, [] when there is none"
        if contract["list"]
        else "ONLY one JSON object"
    )
    return f"Reply with {shape}, with exactly these fields:\n" + "\n".join(lines)


def parse_time(value) -> datetime | None:
    raw = value.get("dateTime") or value.get("date") if isinstance(value, dict) else value
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        found = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return found


def contract_errors(text: str, contract: dict, seen: str | None = None) -> tuple[list[str], object]:
    """What does not fit the contract (each said exactly), and the parsed value. `seen`: what
    the step was given; an id or an address it hands on must be one it was given."""
    from mavo.recipes import parse_json

    try:
        value = parse_json(text)
    except ValueError:
        return ["the reply is not JSON"], None
    if contract["list"] and isinstance(value, dict):
        value = [value]
    if contract["list"] and not isinstance(value, list):
        return ["the reply must be a JSON array"], value
    if not contract["list"] and not isinstance(value, dict):
        return ["the reply must be one JSON object"], value
    rows = value if contract["list"] else [value]
    errors: list[str] = []
    for n, row in enumerate(rows):
        where = f"item {n + 1}" if contract["list"] else "the reply"
        if not isinstance(row, dict):
            errors.append(f"{where} is not an object")
            continue
        for name, kind in contract["fields"].items():
            have = row.get(name)
            if have in (None, "", []):
                if name in contract["required"] and kind != "times" and have != []:
                    errors.append(f"{where}: {name} is missing")
                continue
            problem = field_problem(have, kind, seen)
            if problem:
                errors.append(f"{where}: {name} {problem}")
    return errors[:12], value


def field_problem(value, kind, seen: str | None = None) -> str | None:
    if isinstance(kind, list):
        return None if str(value) in kind else f"must be one of {', '.join(kind)}"
    if kind == "email":
        address = str(value).strip()
        if not EMAIL.match(address):
            return "must be one email address, with no name"
        if seen is not None and address.casefold() not in seen.casefold():
            return f"{address} is not an address in what this step was given"
        return None
    if kind == "id":
        if seen is not None and str(value) not in seen:
            return f"{value} is not an id in what this step was given (copy it exactly)"
        return None
    if kind == "number":
        return None if isinstance(value, int | float) else "must be a number"
    if kind == "bool":
        return None if isinstance(value, bool) else "must be true or false"
    if kind == "time":
        found = parse_time(value)
        if found is None:
            return "must be an ISO 8601 time"
        return None if found.tzinfo else "must carry its UTC offset (+05:30, Z)"
    if kind == "times":
        if not isinstance(value, list):
            return 'must be a list of {"start", "end"}'
        for item in value:
            start = parse_time(item.get("start")) if isinstance(item, dict) else None
            end = parse_time(item.get("end")) if isinstance(item, dict) else None
            if not start or not end:
                return 'must list each time as {"start", "end"}, both ISO 8601 times'
            if not start.tzinfo or not end.tzinfo:
                return "must give every time with its UTC offset"
            if end <= start:
                return f"has an end before its start ({item.get('start')})"
        return None
    if kind == "list":
        return None if isinstance(value, list) else "must be a list"
    return None


# -- gates --------------------------------------------------------------------------------------


def tidy_gate(value) -> dict | None:
    """A field condition: {"step", "field", and one of "in" [..], "equals", "min", "max",
    "is": "empty" | "not_empty"}. None when there is no field (an older "when")."""
    if not isinstance(value, dict) or not isinstance(value.get("field"), str):
        return None
    gate = {"step": re.sub(r"[^a-z0-9_]", "_", str(value.get("step", "")).lower()).strip("_")[:24]}
    gate["field"] = value["field"].strip()[:60]
    if isinstance(value.get("in"), list):
        gate["in"] = [str(v)[:60] for v in value["in"][:12]]
    for key in ("min", "max"):
        if isinstance(value.get(key), int | float):
            gate[key] = value[key]
    if "equals" in value:
        gate["equals"] = value["equals"]
    if value.get("is") in {"empty", "not_empty"}:
        gate["is"] = value["is"]
    if isinstance(value.get("label"), str) and value["label"].strip():
        gate["label"] = value["label"].strip()[:80]  # the case, as the person reads it
    return gate


def field_of(item, path: str):
    for part in path.split("."):
        if isinstance(item, dict):
            item = item.get(part)
        else:
            return None
    return item


def passes(item, gate: dict) -> bool:
    value = field_of(item, gate["field"])
    empty = value in (None, "", [], {})
    if "is" in gate and empty != (gate["is"] == "empty"):
        return False
    if "in" in gate and str(value) not in gate["in"]:
        return False
    if "equals" in gate and value != gate["equals"]:
        return False
    if "min" in gate and not (isinstance(value, int | float) and value >= gate["min"]):
        return False
    if "max" in gate and not (isinstance(value, int | float) and value <= gate["max"]):
        return False
    return True


def rows_of(text: str):
    from mavo.recipes import parse_json

    try:
        value = parse_json(text)
    except ValueError:
        return None
    if isinstance(value, dict) and isinstance(value.get("items"), list):
        return value["items"]
    return value


def gate_met(gate: dict, outputs: dict) -> bool:
    """A step's "when" on a field: met when its object passes, or any item of its list does."""
    text = outputs.get(gate["step"])
    if text is None:
        return False
    value = rows_of(text)
    rows = value if isinstance(value, list) else [value]
    return any(isinstance(row, dict) and passes(row, gate) for row in rows)


def only_passing(text: str, gate: dict) -> str:
    """A step's input cut to the items its gate lets through, as JSON ("[]" when none)."""
    value = rows_of(text)
    rows = value if isinstance(value, list) else [value] if isinstance(value, dict) else []
    return json.dumps([row for row in rows if isinstance(row, dict) and passes(row, gate)])


def fill_item(value, item):
    """An act step's arguments with "{{item.field}}" (nested: "{{item.chosen.start}}") taken
    from the one item it acts on. A whole value is kept as it is (a list stays a list)."""
    if isinstance(value, dict):
        return {k: fill_item(v, item) for k, v in value.items()}
    if isinstance(value, list):
        return [fill_item(v, item) for v in value]
    if not isinstance(value, str) or "{{" not in value:
        return value
    whole = re.fullmatch(r"\{\{\s*item\.([A-Za-z0-9_.]+)\s*\}\}", value.strip())
    if whole:
        return field_of(item, whole.group(1))

    def one(match):
        found = field_of(item, match.group(1))
        return "" if found is None else str(found)

    return re.sub(r"\{\{\s*item\.([A-Za-z0-9_.]+)\s*\}\}", one, value)


# -- finding a time -----------------------------------------------------------------------------

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def zone_of(name):
    """The person's time zone as the workflow names it ("Asia/Kolkata" or "+05:30"); None
    (the computer's own, as everywhere else in Mavo) when this computer does not know it: a
    Windows Python has no zone database of its own."""
    if not isinstance(name, str) or not name.strip():
        return None
    offset = re.fullmatch(r"(?:UTC|GMT)?\s*([+-])(\d{1,2}):?(\d{2})", name.strip(), re.I)
    if offset:
        sign = 1 if offset.group(1) == "+" else -1
        return timezone(sign * timedelta(hours=int(offset.group(2)), minutes=int(offset.group(3))))
    try:
        return ZoneInfo(name.strip())
    except (ZoneInfoNotFoundError, ValueError):
        return None


def window_of(params: dict, zone) -> dict:
    """The hours a meeting may take, as the workflow's writer set them from the person's words."""

    def clock(value, default):
        try:
            hours, _, minutes = str(value or default).partition(":")
            return time(int(hours), int(minutes or 0))
        except ValueError:
            return time.fromisoformat(default)

    days = [str(d).strip().lower()[:3] for d in params.get("days") or DAYS[:5]]
    return {
        "zone": zone,
        "days": [d for d in days if d in DAYS] or DAYS[:5],
        "start": clock(params.get("start"), "09:00"),
        "end": clock(params.get("end"), "18:00"),
        "minutes": int(params.get("duration_minutes") or 30),
        "buffer": int(params.get("buffer_minutes") or 0),
        "horizon": int(params.get("horizon_days") or 7),
        "alternatives": int(params.get("alternatives") or 3),
    }


def inside(start: datetime, end: datetime, window: dict) -> str | None:
    """Why this time is outside the person's window, or None."""
    local_start, local_end = start.astimezone(window["zone"]), end.astimezone(window["zone"])
    if DAYS[local_start.weekday()] not in window["days"]:
        return f"{local_start:%A} is not a working day"
    if local_start.date() != local_end.date():
        return "it runs past midnight"
    if local_start.time() < window["start"] or local_end.time() > window["end"]:
        return (
            f"{local_start:%H:%M} to {local_end:%H:%M} is outside "
            f"{window['start']:%H:%M} to {window['end']:%H:%M}"
        )
    return None


def find_slots(requests: list, calendar: list, params: dict, now: datetime | None = None) -> list:
    """For each request: the first offered time that is free and inside the window ("chosen"),
    or, when none is, the next free times ("alternatives"), with why. Two requests never get
    the same time. The person's calendar is never moved."""
    from mavo.calendar_conflicts import clash

    zone = zone_of(params.get("timezone"))
    now = now or datetime.now().astimezone()
    zone = zone or now.tzinfo
    window = window_of(params, zone)
    taken: list[dict] = []
    pad = timedelta(minutes=window["buffer"])
    out = []
    for request in requests:
        if not isinstance(request, dict):
            continue
        minutes = int(request.get("duration_minutes") or window["minutes"])

        def busy(start: datetime, end: datetime):
            return clash(
                [*calendar, *taken], (start - pad).isoformat(), (end + pad).isoformat(), None
            )

        def event(start: datetime, end: datetime) -> dict:
            return {"start": start.isoformat(), "end": end.isoformat()}

        chosen, notes = None, []
        for offer in request.get("offered") or []:
            start = parse_time(offer.get("start") if isinstance(offer, dict) else offer)
            if start is None:
                continue
            start = start if start.tzinfo else start.replace(tzinfo=zone)
            end = parse_time(offer.get("end")) if isinstance(offer, dict) else None
            end = (end if end and end.tzinfo else None) or start + timedelta(minutes=minutes)
            if end - start < timedelta(minutes=minutes):
                end = start + timedelta(minutes=minutes)
            label = f"{start.astimezone(zone):%a %d %b %H:%M}"
            if start <= now:
                notes.append(f"{label} has passed")
                continue
            outside = inside(start, end, window)
            if outside:
                notes.append(f"{label}: {outside}")
                continue
            hit = busy(start, end)
            if hit:
                notes.append(f"{label} is busy ({hit.get('summary') or 'an event'})")
                continue
            chosen = event(start, end)
            break
        alternatives = []
        if chosen is None:
            # From the day they asked about ("from"), when later than today: someone asking for
            # a date next month is offered times then, not this week.
            day = now.astimezone(zone).date()
            asked = parse_time(request.get("from"))
            if asked is not None:
                day = max(day, (asked if asked.tzinfo else asked.replace(tzinfo=zone)).date())
            cursor = datetime.combine(day, window["start"], tzinfo=zone)
            last = cursor + timedelta(days=window["horizon"])
            step = timedelta(minutes=30)
            while cursor < last and len(alternatives) < window["alternatives"]:
                end = cursor + timedelta(minutes=minutes)
                if cursor > now and not inside(cursor, end, window) and not busy(cursor, end):
                    alternatives.append(event(cursor, end))
                    # spread them: the next on another part of the day
                    cursor += timedelta(hours=2)
                    continue
                cursor += step
        if chosen:
            taken.append(  # shaped as a calendar event, as clash() reads them
                {
                    "start": {"dateTime": chosen["start"]},
                    "end": {"dateTime": chosen["end"]},
                    "summary": "booked just now",
                    "status": "confirmed",
                }
            )
        if chosen and not notes:
            why = "the first offered time is free"
        elif chosen:
            why = "; ".join(notes) + "; took the next offered time that is free"
        elif notes:
            why = "; ".join(notes)
        else:
            why = "no time was offered"
        out.append({**request, "chosen": chosen, "alternatives": alternatives, "why": why})
    return out


ID_KEY = re.compile(r"(^|_)id$|^in_reply_to$")


def item_ids(*values: dict) -> set[str]:
    """What identifies the item an action is for (its email's id and thread id, an event's id),
    so that actions on the same item are linked across steps."""
    found: set[str] = set()
    for value in values:
        for key, item in (value or {}).items():
            if ID_KEY.search(str(key)) and isinstance(item, str | int) and str(item).strip():
                found.add(f"{key.split('_')[0] if key != 'id' else 'id'}:{item}")
    return found


def split_items(value, contract: dict, seen: str | None = None) -> tuple[list, list[dict]]:
    """The items that fit the contract, and the ones that do not with what is wrong: one email
    that cannot be read clearly is set aside for the person, and the others go on."""
    rows = value if isinstance(value, list) else [value]
    kept, aside = [], []
    for row in rows:
        errors, _ = contract_errors(json.dumps([row]), {**contract, "list": True}, seen)
        if errors:
            aside.append({"item": row, "problems": [e.removeprefix("item 1: ") for e in errors]})
        else:
            kept.append(row)
    return kept, aside


def fit_types(operation: str, arguments: dict) -> dict:
    """An action's arguments in the shapes its fields take: a date field given a date and time
    keeps the date ("due" on a task takes a day; a step handed on the deadline with its hour and
    the task was refused). Only such sure, lossless fits; anything else stays as given."""
    from mavo.google_actions import MODELS

    model = MODELS.get(operation)
    if model is None or not isinstance(arguments, dict):
        return arguments
    props = model.model_json_schema().get("properties", {})
    fitted = dict(arguments)
    for name, spec in props.items():
        formats = {spec.get("format"), *(a.get("format") for a in spec.get("anyOf", []))}
        value = fitted.get(name)
        if "date" in formats and "date-time" not in formats and isinstance(value, str):
            found = parse_time(value)
            if found is not None and len(value.strip()) > 10:
                fitted[name] = found.date().isoformat()
    return fitted
