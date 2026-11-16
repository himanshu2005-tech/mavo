"""One voice: the person hears from Mavo, not from each agent.

Every agent message passes through here. What needs the person (a question, an OK, a failure),
what is time-bound (a mail alert, a calendar change, a plan at 8 AM) and what they are waiting for
(a one-time task) still reaches them at once. Routine reports from agents that run on a timer wait
for Mavo's next summary: one message every so often (30 minutes by default) for all of them,
written by the model, or a plain list when no model can be reached, so nothing is ever lost.

Each held message keeps its agent, so a reply still goes to the right one. An agent can be set to
"now" or "summary" (configuration_plan["delivery"]) to override the default.

Plain rules, no model, decide what is held: the decision must be instant and cannot go down."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from mavo.database import CrewInstance, ModelRoute, ReminderEvent, WorkspaceSetting, utcnow

logger = logging.getLogger("mavo.attention")

SETTING = "attention"
DEFAULT_MINUTES = 30
FULL = 15  # this many held messages are summarised at once, without waiting for the window
EVENT_TRIGGERS = {"gmail_message", "calendar_changed", "tasks_changed"}


def settings(session) -> dict:
    row = session.get(WorkspaceSetting, SETTING)
    value = dict(row.value) if row else {}
    minutes = value.get("digest_minutes", DEFAULT_MINUTES)
    return {
        "digest_minutes": max(5, min(int(minutes or DEFAULT_MINUTES), 24 * 60)),
        "enabled": value.get("enabled", True),
    }


def save_settings(session, **changes) -> dict:
    row = session.get(WorkspaceSetting, SETTING)
    value = {**(dict(row.value) if row else {}), **changes}
    if row:
        row.value = value
    else:
        session.add(WorkspaceSetting(key=SETTING, value=value))
    return settings(session)


def when(session, crew: CrewInstance | None, *, needs_person: bool = False) -> str:
    """ "now" or "summary" for one message from `crew`."""
    if needs_person or crew is None or crew.hidden:
        return "now"  # a question, an OK, a failure, or a one-time task the person is waiting on
    choice = (crew.configuration_plan or {}).get("delivery")
    if choice in {"now", "summary"}:
        return choice
    if not settings(session)["enabled"]:
        return "now"
    plan = crew.configuration_plan or {}
    if crew.template_id == "computer" or plan.get("device_monitor"):
        return "now"  # "battery at 10%" cannot wait half an hour
    if crew.trigger_type in EVENT_TRIGGERS or crew.trigger_type in {"daily", "manual"}:
        return "now"  # it happened, or it is due now
    if crew.trigger_type == "interval" and crew.schedule_time:
        return "now"  # once a day at its time: the 8 AM plan is wanted at 8 AM
    return "summary"


def admit(session, event: ReminderEvent, crew: CrewInstance | None, *, needs_person=False) -> str:
    """Sets the event to pop up now ("pending") or wait for the summary ("digest")."""
    route = when(session, crew, needs_person=needs_person)
    event.status = "pending" if route == "now" else "digest"
    return route


SUMMARY = (
    "You are Mavo, the person's assistant. Their agents reported the updates below since your "
    "last message. Write ONE short message to the person: most important first, grouped by "
    "agent, each agent's name in bold once. Keep every time, date, name, amount and link exactly "
    "as written; never add facts. Leave out updates that say nothing new. At most 14 lines. "
    "Reply with the message only."
)


def plain(items: list[dict]) -> str:
    lines = []
    for item in items:
        text = " ".join(item["text"].split())
        lines.append(f"**{item['agent']}**: {text[:300]}{'…' if len(text) > 300 else ''}")
    return "\n".join(lines)


async def summarise(chat, route, items: list[dict]) -> str:
    """The model's summary, or the plain list when it fails, is empty, or drops an agent."""
    if chat is None or route is None:
        return plain(items)
    try:
        text = await asyncio.wait_for(
            chat(
                route.provider,
                route.model,
                [{"role": "user", "content": json.dumps(items)[:24000]}],
                SUMMARY,
                route.credential_id,
            ),
            timeout=90,
        )
        text = re.sub(r"<think>.*?</think>", "", str(text or ""), flags=re.S).strip()
    except Exception:
        logger.warning("Mavo's summary could not be written; sending the plain list", exc_info=True)
        return plain(items)
    if not text or len(text) > 6000:
        return plain(items)
    return text


def due(session, now=None) -> list[ReminderEvent]:
    """The held messages, when it is time for a summary (the window passed, or many waiting)."""
    now = now or utcnow()
    held = session.scalars(
        select(ReminderEvent)
        .where(ReminderEvent.status == "digest")
        .order_by(ReminderEvent.created_at)
    ).all()
    if not held:
        return []
    first = held[0].created_at
    first = first if first.tzinfo else first.replace(tzinfo=now.tzinfo)
    window = timedelta(minutes=settings(session)["digest_minutes"])
    return list(held) if len(held) >= FULL or now - first >= window else []


async def flush(factory, owner_of, chat, now=None) -> str | None:
    """Turns the held messages into one message from Mavo. Returns its text, or None."""
    with factory() as session:
        held = due(session, now)
        for event in list(held):
            # An agent stopped, deleted, or no longer allowed to alert: its held report goes too.
            crew = session.get(CrewInstance, event.crew_id) if event.crew_id else None
            if (
                not crew
                or crew.status != "active"
                or not crew.is_running
                or "notifications:show" not in crew.permissions
            ):
                event.status = "cancelled"
                held.remove(event)
        session.commit()
        if not held:
            return None
        items = [
            {
                "agent": e.crew_name,
                "at": e.created_at.isoformat(timespec="minutes"),
                "text": e.message[:2000],
            }
            for e in held[:40]
        ]
        ids = [e.id for e in held[:40]]
        route = None
        owner = owner_of(session)
        if owner and owner.route_id:
            route = session.get(ModelRoute, owner.route_id)
        route = route or session.scalar(select(ModelRoute).order_by(ModelRoute.created_at))
        session.commit()
    text = await summarise(chat, route, items)
    agents = list(dict.fromkeys(i["agent"] for i in items))
    heading = (
        f"{len(items)} update{'s' if len(items) != 1 else ''} from {', '.join(agents[:4])}"
        + (f" and {len(agents) - 4} more" if len(agents) > 4 else "")
    )
    with factory() as session:
        owner = owner_of(session)
        stamp = utcnow()
        session.add(
            ReminderEvent(
                crew_id=owner.id,
                crew_name="Mavo",
                kind="crew_update",
                message=(heading + "\n\n" + text)[:12000],
                scheduled_for=stamp,
                local_date=stamp.astimezone().date().isoformat(),
                deduplication_key="summary:" + ids[0],
                status="pending",
            )
        )
        for event in session.scalars(select(ReminderEvent).where(ReminderEvent.id.in_(ids))):
            if event.status == "digest":
                event.status = "summarised"
        session.commit()
    return text


class AttentionSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    digest_minutes: int | None = Field(default=None, ge=5, le=24 * 60)
    enabled: bool | None = None


class Delivery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    delivery: Literal["auto", "now", "summary"]


def build_attention_api(factory):
    from fastapi import APIRouter, HTTPException

    router = APIRouter()

    @router.get("/attention")
    def read():
        with factory() as session:
            return settings(session)

    @router.put("/attention")
    def write(payload: AttentionSettings):
        """How often Mavo sends its summary, or off (every report arrives on its own)."""
        with factory() as session:
            value = save_settings(session, **payload.model_dump(exclude_none=True))
            if payload.enabled is False:  # nothing waits for a summary that will not come
                for event in session.scalars(
                    select(ReminderEvent).where(ReminderEvent.status == "digest")
                ):
                    event.status = "pending"
            session.commit()
            return value

    @router.put("/crew/{crew_id}/delivery")
    def delivery(crew_id: str, payload: Delivery):
        """One agent's reports: always now, always in Mavo's summary, or Mavo decides (auto)."""
        with factory() as session:
            crew = session.get(CrewInstance, crew_id)
            if not crew or crew.hidden:
                raise HTTPException(404, "Crew not found")
            plan = {k: v for k, v in (crew.configuration_plan or {}).items() if k != "delivery"}
            if payload.delivery != "auto":
                plan["delivery"] = payload.delivery
            crew.configuration_plan = plan
            session.commit()
            return {"crew_id": crew_id, "delivery": payload.delivery}

    return router
