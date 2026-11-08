"""Read-only overviews: what will happen next (timeline) and how Crew hand work to each other
(graph). Both are derived from saved Crew state; nothing here starts, changes or grants anything."""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from mavo.daily_triggers import rules_for
from mavo.database import CrewInstance, CrewRun, ReminderEvent

MAX_EVENTS = 300
STANDING = {
    "gmail_message": "Runs when a matching email arrives",
    "calendar_changed": "Runs when your calendar changes",
    "tasks_changed": "Runs when your tasks change",
    "manual": "Runs when you ask it to",
}


def _local(value):
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone()


def upcoming_events(crews, now, days):
    """Deterministic projection of scheduled work. Event triggers have no time, so they are
    listed separately as 'standing' rather than invented into the timeline."""
    local_now = _local(now)
    horizon = local_now + timedelta(days=days)
    events, standing = [], []
    for crew in crews:
        if crew.status != "active" or not crew.is_running:
            continue
        if crew.trigger_type == "daily":
            for rule in rules_for(crew):
                if not rule.enabled:
                    continue
                hour, minute = map(int, rule.time.split(":"))
                for offset in range(days + 1):
                    date = local_now.date() + timedelta(days=offset)
                    if date.weekday() not in rule.weekdays:
                        continue
                    at = datetime.combine(date, datetime.min.time(), local_now.tzinfo).replace(
                        hour=hour, minute=minute
                    )
                    if local_now < at <= horizon:
                        events.append(
                            {
                                "at": at.isoformat(),
                                "kind": "reminder",
                                "crew_id": crew.id,
                                "crew_name": crew.name,
                                "title": (rule.message or crew.reminder_text or crew.name)[:140],
                                "uses_model": False,
                            }
                        )
        elif crew.trigger_type == "interval" and crew.next_run_at:
            at = _local(crew.next_run_at)
            step = timedelta(minutes=max(crew.review_interval_minutes, 5))
            if at < local_now:
                at = local_now
            for _ in range(3):
                if at > horizon:
                    break
                events.append(
                    {
                        "at": at.isoformat(),
                        "kind": "review",
                        "crew_id": crew.id,
                        "crew_name": crew.name,
                        "title": "Scheduled review",
                        "uses_model": crew.template_id != "computer",
                    }
                )
                at += step
        elif crew.trigger_type in STANDING:
            standing.append(
                {"crew_id": crew.id, "crew_name": crew.name, "label": STANDING[crew.trigger_type]}
            )
    events.sort(key=lambda item: item["at"])
    return events[:MAX_EVENTS], standing


def crew_graph(crews):
    by_id = {crew.id: crew for crew in crews}
    nodes, edges = [], []
    for crew in crews:
        plan = crew.configuration_plan or {}
        nodes.append(
            {
                "id": crew.id,
                "name": crew.name,
                "template_id": crew.template_id,
                "status": crew.status,
                "state": crew.runtime_state,
                "running": crew.is_running,
                "trigger_type": crew.trigger_type,
                "group": plan.get("group"),
            }
        )
        for source in plan.get("upstream", []):
            if source in by_id:
                edges.append({"from": source, "to": crew.id})
    return {"nodes": nodes, "edges": edges}


def build_timeline_api(get_session):
    router = APIRouter()

    @router.get("/timeline")
    def timeline(days: int = Query(default=7, ge=1, le=30), session=Depends(get_session)):
        now = datetime.now(UTC)
        crews = list(
            session.scalars(select(CrewInstance).where(CrewInstance.hidden.is_(False))).all()
        )
        upcoming, standing = upcoming_events(crews, now, days)
        since = now - timedelta(days=2)
        recent = [
            {
                "at": run.created_at.replace(tzinfo=UTC).isoformat()
                if run.created_at.tzinfo is None
                else run.created_at.isoformat(),
                "kind": "run",
                "crew_name": run.crew_name,
                "title": (run.result or run.error or run.status)[:140],
                "status": run.status,
            }
            for run in session.scalars(
                select(CrewRun)
                .where(CrewRun.created_at >= since.replace(tzinfo=None))
                .order_by(CrewRun.created_at.desc())
                .limit(20)
            )
        ] + [
            {
                "at": reminder.scheduled_for.replace(tzinfo=UTC).isoformat()
                if reminder.scheduled_for.tzinfo is None
                else reminder.scheduled_for.isoformat(),
                "kind": "reminder",
                "crew_name": reminder.crew_name,
                "title": reminder.message[:140],
                "status": reminder.status,
            }
            for reminder in session.scalars(
                select(ReminderEvent)
                .where(ReminderEvent.scheduled_for >= since.replace(tzinfo=None))
                .order_by(ReminderEvent.scheduled_for.desc())
                .limit(20)
            )
        ]
        recent.sort(key=lambda item: item["at"], reverse=True)
        return {
            "generated_at": now.isoformat(),
            "days": days,
            "upcoming": upcoming,
            "standing": standing,
            "recent": recent[:30],
        }

    @router.get("/crew-graph")
    def graph(session=Depends(get_session)):
        return crew_graph(
            list(session.scalars(select(CrewInstance).where(CrewInstance.hidden.is_(False))).all())
        )

    return router
