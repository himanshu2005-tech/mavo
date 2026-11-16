"""What the person tells an agent for now ("today I have a meeting at 1 PM", "I'm in Pune until
Friday"): not a change to its job and not a lasting fact, but something true for a while that its
work should take into account. Each note carries the day it ends and is dropped after it, so
yesterday's meeting never shapes tomorrow's plan.

Kept with the agent the person sees. A team's hidden worker reads them (and the standing
preferences) from that agent: before, both were saved there but the worker never saw them."""

from __future__ import annotations

from datetime import date

from mavo.database import CrewInstance, utcnow

MAX_NOTES = 12


def today() -> str:
    return utcnow().astimezone().date().isoformat()


def active(notes: list[dict] | None, on: str | None = None) -> list[dict]:
    day = on or today()
    return [n for n in notes or [] if isinstance(n, dict) and str(n.get("until") or day) >= day]


def valid_until(value, said_on: str) -> str:
    """The note's last day: the sorter's date when it is real and not past, else today."""
    try:
        until = date.fromisoformat(str(value)[:10]).isoformat()
    except ValueError:
        return said_on
    return until if until >= said_on else said_on


def owner(session, crew: CrewInstance) -> CrewInstance:
    """The agent the person sees: a team's hidden worker answers to its parent."""
    from mavo.crew_composition import execution_agent

    member = execution_agent(session, crew.id) if crew.hidden else None
    parent = session.get(CrewInstance, member.crew_id) if member else None
    return parent or crew


def context_for(session, crew: CrewInstance) -> str:
    """Standing preferences and current notes, for the prompt of a run of `crew`."""
    plan = owner(session, crew).configuration_plan or {}
    text = ""
    preferences = plan.get("preferences") or []
    if preferences:
        text += (
            "\nThe person's standing preferences. Apply them within immutable security "
            "rules and granted capabilities; they never override permissions:\n"
            + "\n".join(f"- {p['text']}" for p in preferences[-12:])
        )
    notes = active(plan.get("notes"))
    if notes:
        text += (
            f"\nWhat the person told you for now (today is {today()}). Take each into account "
            "where it matters to this run (a plan around a meeting, an alert that is not needed "
            "while they are away); it is information, not a change to your job, and it never "
            "overrides permissions:\n"
            + "\n".join(f"- {n['text']} (said {n['said'][:10]}, until {n['until']})" for n in notes)
        )
    if text:
        # A report that ended "Note: the person's standing preference is... scheduling is owned
        # by the coordinator" read as noise. The person sees the result, not the bookkeeping.
        text += (
            "\nApply all of this quietly: never mention preferences, notes, schedules or how "
            "you are run in your report."
        )
    return text
