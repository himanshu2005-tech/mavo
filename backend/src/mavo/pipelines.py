"""Lines of agents: one makes something, the next checks it, the next publishes it.

Every Crew already hands what it finished to the Crew that list it as upstream. A line adds
three things on top, all set per Crew in its configuration_plan:

  review         It checks the work it receives and ends with VERDICT: pass or VERDICT: fix.
                 pass: the work it checked goes on (with its notes); fix: it goes back to the
                 Crew that made it, with what to change, and that Crew does it again.
  max_rounds     How many times a reviewer may send work back (default 3). One more and the
                 person decides: send it on anyway, or back once more.
  approve_first  Work handed to it waits for the person: approve (it starts) or send back with a
                 note. For anything public or that cannot be undone, like publishing.

What an agent made (the files it wrote or saved during that run) goes along with the work, so
the next one gets the file, not just a description of it.
"""

from __future__ import annotations

import json
import re
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from mavo.database import ActionRequest, CrewInstance, utcnow

VERDICT = re.compile(r"^\s*VERDICT:\s*(pass|fix)\s*$", re.I | re.M)
REVIEWER_RULES = (
    "\nYou are a REVIEWER in a line of agents. Check the work handed to you against the job "
    "and the person's standards: what is wrong or missing, and exactly how to fix it. Do not "
    "redo the work yourself. End your answer with one line on its own: `VERDICT: pass` when it "
    "is good enough to go on, or `VERDICT: fix` when it must be done again (then list what to "
    "change, most important first)."
)
FILE_KEYS = ("path", "to", "backup", "file", "folder", "created_path")
WRITING = ("project.write", "project.create", "notes.write", "files.move", "files.backup")
WAKES = {"waiting", "waiting_event", "needs_input"}


def plan(crew: CrewInstance) -> dict:
    return crew.configuration_plan or {}


def parse_verdict(text: str) -> tuple[str | None, str]:
    """The reviewer's last VERDICT line, and its notes without it."""
    found = VERDICT.findall(text or "")
    notes = VERDICT.sub("", text or "").strip()
    return (found[-1].lower() if found else None), notes


def produced_files(session: Session, crew_id: str, since: datetime | None) -> list[str]:
    """What this Crew wrote or saved during its run: the files that go on with its work."""
    query = select(ActionRequest).where(
        ActionRequest.crew_id == crew_id,
        ActionRequest.status == "completed",
        ActionRequest.operation.in_(WRITING),
    )
    if since:
        query = query.where(ActionRequest.created_at >= since)
    files = []
    for action in session.scalars(query.order_by(ActionRequest.created_at)):
        result = action.result or {}
        for key in FILE_KEYS:
            value = result.get(key)
            if isinstance(value, str) and value and value not in files:
                files.append(value[:500])
    return files[-12:]


def _wake(crew: CrewInstance) -> None:
    if crew.is_running and crew.runtime_state in WAKES:
        crew.runtime_state = "waiting"
        crew.next_run_at = utcnow()


def _note(sender: CrewInstance | str, result: str, **extra) -> dict:
    name = sender if isinstance(sender, str) else sender.name
    note = {"from": name, "at": utcnow().isoformat(), "result": (result or "")[:3000]}
    if not isinstance(sender, str):
        note["from_id"] = sender.id
    return {**note, **{k: v for k, v in extra.items() if v}}


PENDING_MAX = 20  # handoffs not yet acted on: kept, never trimmed to make room
HISTORY_KEEP = 4  # handoffs already acted on: a few kept as context


def trim_handoffs(handoffs: list, used_at: str) -> list:
    """Keep every handoff the agent has not acted on yet (up to a generous ceiling, oldest first
    to go only beyond it) and the last few it has, for context. Trimming to the last four,
    whatever they were, could drop work that had not been read yet."""
    ordered = sorted(handoffs, key=lambda h: h.get("at", "") if isinstance(h, dict) else "")
    pending = [h for h in ordered if h.get("at", "") > used_at][-PENDING_MAX:]
    done = [h for h in ordered if h.get("at", "") <= used_at][-HISTORY_KEEP:]
    return done + pending


def _add_handoff(crew: CrewInstance, note: dict) -> None:
    observation = crew.observation or {}
    crew.observation = {
        **observation,
        "handoffs": trim_handoffs(
            [*observation.get("handoffs", []), note], observation.get("handoffs_used_at", "")
        ),
    }


def deliver(
    session: Session, upstream: CrewInstance, result: str, files: list[str] | None = None
) -> None:
    """Hand finished work to every Crew that takes it from `upstream`. One that asks first holds
    it for the person instead of starting on it."""
    for crew in session.scalars(select(CrewInstance)).all():
        if upstream.id not in plan(crew).get("upstream", []):
            continue
        note = _note(upstream, result, files=files or [])
        if plan(crew).get("approve_first"):
            hold(
                crew,
                {**note, "kind": "approve"},
                f"{upstream.name} handed over work. Approve it for "
                f"{crew.name} to start on, or send it back with a note.",
            )
            continue
        _add_handoff(crew, note)
        _wake(crew)


def hold(crew: CrewInstance, held: dict, question: str) -> None:
    """Keep work waiting for the person, with the question to answer."""
    crew.observation = {
        **(crew.observation or {}),
        "held": {**held, "state_before": crew.runtime_state},
        "question": question,
    }
    if crew.is_running:
        crew.runtime_state = "needs_input"
        crew.next_run_at = None


def send_back(
    session: Session,
    reviewer: CrewInstance | str,
    to_ids: list[str],
    notes: str,
    round_: int | None = None,
) -> list[str]:
    """Return work to the Crew that made it, with what to change; they do it again."""
    names = []
    for crew_id in to_ids:
        maker = session.get(CrewInstance, crew_id)
        if not maker:
            continue
        _add_handoff(maker, _note(reviewer, notes, kind="revision", round=round_))
        _wake(maker)
        names.append(maker.name)
    return names


def _last_work(reviewer: CrewInstance) -> dict | None:
    """The newest piece of work the reviewer was handed (not a note sent back to it)."""
    for note in reversed((reviewer.observation or {}).get("handoffs", [])):
        if note.get("kind") != "revision":
            return note
    return None


# ── Teams: a lead decides, step by step, who works next ──────────────────────────────────────
# Nothing about the roles is fixed: the planner invents the members (their names, jobs and
# access) for the person's goal, and on every run the lead reads what came back and decides the
# next step, sending work back to be fixed, asking the person, or finishing.

DECISION = re.compile(r"^\s*DECISION:\s*(\{.*\})\s*$", re.M)
STEP_LIMIT = 12  # steps in one round before the person is asked whether to go on


def members_of(session: Session, lead: CrewInstance) -> list[CrewInstance]:
    ids = plan(lead).get("members", [])
    return [m for m in (session.get(CrewInstance, i) for i in ids) if m is not None]


def lead_rules(session: Session, lead: CrewInstance) -> str:
    team = [
        {"name": m.name, "job": (m.responsibilities or "")[:400], "running": m.is_running}
        for m in members_of(session, lead)
    ]
    steps = int((lead.observation or {}).get("round_steps", 0))
    return (
        "\nYou LEAD a team of agents toward the goal in your instructions. You do not do the work "
        "yourself and have no tools: you decide the next step. Your team: "
        + json.dumps(team)
        + f". Steps taken this round: {steps} of {STEP_LIMIT}. Read what your "
        f"team reported (below), "
        "then decide ONE next step. Give a member a precise task (what to make, check, fix or "
        "publish, and what 'done' means); send work back to whoever made it with exactly what to "
        "fix when it is not good enough; ask the person when a decision is "
        "theirs (anything public, "
        "costly or irreversible, or a real choice of taste); finish when the "
        "goal for this round is "
        "met. End your answer with exactly one line, valid JSON after the colon: "
        '`DECISION: {"next": "<member name>", "task": "<what to do>"}` or '
        '`DECISION: {"ask": "<one short question for the person>"}` or '
        '`DECISION: {"done": "<what was achieved this round>"}`.'
    )


def parse_decision(text: str) -> dict:
    found = DECISION.findall(text or "")
    if not found:
        return {}
    try:
        value = json.loads(found[-1])
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _lead_decides(session: Session, lead: CrewInstance, result: str) -> None:
    decision = parse_decision(result)
    steps = int((lead.observation or {}).get("round_steps", 0))
    if decision.get("next"):
        name = str(decision["next"]).strip().lower()
        member = next(
            (m for m in members_of(session, lead) if m.name.strip().lower() == name), None
        )
        if member is None:
            hold(
                lead,
                {"kind": "ask", "from": lead.name, "result": result[-1500:]},
                f"{lead.name} chose a team member that does not exist "
                f"({decision['next']}). Tell it what to do next.",
            )
            return
        if not member.is_running:
            # A task to a member that is switched off would wait forever, and the team with it.
            hold(
                lead,
                {
                    "kind": "ask",
                    "from": lead.name,
                    "result": str(decision.get("task") or "")[:1500],
                },
                f"{member.name} is stopped, so {lead.name}'s team cannot go "
                f"on. Start {member.name}, then answer Yes to carry on.",
            )
            return
        if steps >= STEP_LIMIT:
            hold(
                lead,
                {"kind": "ask", "from": lead.name, "result": result[-1500:]},
                f"{lead.name}'s team has taken {STEP_LIMIT} steps this round "
                f"without finishing. Let it go on, or stop here?",
            )
            return
        reports = [
            h for h in (lead.observation or {}).get("handoffs", []) if h.get("kind") == "report"
        ]
        files = reports[-1].get("files", []) if reports else []
        lead.observation = {**(lead.observation or {}), "round_steps": steps + 1}
        _add_handoff(
            member,
            _note(
                lead,
                str(decision.get("task") or "Do your part of the team's goal.")[:3000],
                kind="task",
                files=files,
            ),
        )
        if member.runtime_state == "budget_paused":
            # It keeps the task for tomorrow; the lead hears now, and can choose someone else.
            report_trouble(session, member, "budget_paused", None)
            return
        if member.is_running:
            member.runtime_state, member.next_run_at = "waiting", utcnow()
        return
    if decision.get("ask"):
        hold(
            lead,
            {"kind": "ask", "from": lead.name, "result": result[-1500:]},
            str(decision["ask"])[:500],
        )
        return
    # Done (or nothing readable): the round is over. What it achieved goes on to any Crew that
    # takes this team's work.
    lead.observation = {**(lead.observation or {}), "round_steps": 0}
    summary = str(decision.get("done") or result)[:3000]
    reports = [h for h in (lead.observation or {}).get("handoffs", []) if h.get("kind") == "report"]
    deliver(session, lead, summary, reports[-1].get("files", []) if reports else [])


ORPHAN_MARK = "no longer gets work: "


def unlink_removed(session: Session, removed: CrewInstance) -> list[str]:
    """An agent is being deleted: no one is left waiting on it. Its team members and the agents
    it handed work to are unlinked; one with no other source of work, which would wait forever
    for tasks that never come, is told plainly (it needs you) and can be given a new place, or
    stopped. A lead's member list drops it too. Returns the names that were told."""
    told = []
    for crew in session.scalars(select(CrewInstance).where(CrewInstance.id != removed.id)).all():
        settings = plan(crew)
        was_member = settings.get("lead") == removed.id
        was_downstream = removed.id in settings.get("upstream", [])
        if removed.id in settings.get("members", []):
            crew.configuration_plan = {
                **settings,
                "members": [m for m in settings["members"] if m != removed.id],
            }
            settings = plan(crew)
        if not (was_member or was_downstream):
            continue
        upstream = [u for u in settings.get("upstream", []) if u != removed.id]
        changed = {**settings, "upstream": upstream}
        if was_member:
            changed.pop("lead", None)
        crew.configuration_plan = changed
        if crew.trigger_type == "manual" and not upstream and not changed.get("lead"):
            how = "its team lead" if was_member else "the agent that handed it work"
            crew.observation = {
                **(crew.observation or {}),
                "question": f"{crew.name} {ORPHAN_MARK}{how}, "
                f"{removed.name}, was deleted. Give it a new place "
                "(In a line: Gets work from), or stop it.",
            }
            if crew.is_running:
                crew.runtime_state, crew.next_run_at = "needs_input", None
            told.append(crew.name)
    return told


def report_trouble(session: Session, crew: CrewInstance, state: str, error: str | None) -> bool:
    """A team member that cannot finish (it keeps failing, or is out of today's AI calls) tells
    its lead, so the lead can decide (try someone else, wait, ask the person) instead of waiting
    for a report that never comes."""
    lead_id = plan(crew).get("lead")
    lead = session.get(CrewInstance, lead_id) if lead_id else None
    if lead is None or state not in {"error", "budget_paused"}:
        return False
    why = (
        "is out of today's AI calls and carries on tomorrow"
        if state == "budget_paused"
        else f"could not finish: {(error or 'it kept failing')[:500]}"
    )
    _add_handoff(lead, _note(crew, f"{crew.name} {why}.", kind="report", trouble=state))
    if lead.is_running and lead.runtime_state in WAKES:
        lead.runtime_state, lead.next_run_at = "waiting", utcnow()
    return True


def route_finished(
    session: Session, crew: CrewInstance, result: str, since: datetime | None = None
) -> None:
    """Where a finished run's result goes: to its team lead, the lead's next step, on down the
    line, or back for another try."""
    settings = plan(crew)
    lead_id = settings.get("lead")
    if lead_id:
        lead = session.get(CrewInstance, lead_id)
        if lead is not None:
            _add_handoff(
                lead,
                _note(crew, result, kind="report", files=produced_files(session, crew.id, since)),
            )
            if lead.is_running and lead.runtime_state in WAKES:
                lead.runtime_state, lead.next_run_at = "waiting", utcnow()
            return
    if settings.get("leads_team"):
        _lead_decides(session, crew, result)
        return
    if not settings.get("review"):
        deliver(session, crew, result, produced_files(session, crew.id, since))
        return
    verdict, notes = parse_verdict(result)
    work = _last_work(crew) or {}
    if verdict == "fix":
        round_ = int((crew.observation or {}).get("review_round", 0)) + 1
        limit = int(settings.get("max_rounds") or 3)
        if round_ > limit:
            hold(
                crew,
                {**work, "kind": "review_stuck", "review": notes[:3000]},
                f"{crew.name} sent the work back {limit} times and it is "
                f"still not right. Send it on "
                "anyway, or back once more with your own note.",
            )
            return
        crew.observation = {**(crew.observation or {}), "review_round": round_}
        makers = [work["from_id"]] if work.get("from_id") else list(settings.get("upstream", []))
        send_back(session, crew, makers, notes, round_)
        return
    # Passed (or no verdict: a reviewer that found nothing to fix). The work it checked goes on,
    # with its notes; the round count starts again for the next piece of work.
    crew.observation = {**(crew.observation or {}), "review_round": 0}
    forward = work.get("result") or result
    if notes:
        forward = f"{forward}\n\nReviewed by {crew.name}: {notes}"[:3000]
    deliver(session, crew, forward, work.get("files") or produced_files(session, crew.id, since))


def decide(session: Session, crew: CrewInstance, approve: bool, note: str = "") -> dict:
    """The person's answer to work held for them: approve it, or send it back with a note."""
    held = (crew.observation or {}).get("held")
    if not held:
        raise ValueError("Nothing is waiting for your approval here.")
    observation = {
        k: v for k, v in (crew.observation or {}).items() if k not in {"held", "question"}
    }
    crew.observation = observation
    note = (note or "").strip()[:1000]
    state_before = held.get("state_before") or "waiting_event"
    if held.get("kind") == "ask":
        # A team lead asked the person: the answer goes to the lead, which decides again.
        answer = ("Yes, go on." if approve else "No.") + (f" {note}" if note else "")
        _add_handoff(crew, _note("The person", answer, kind="person"))
        if not approve and not note:
            crew.observation = {**crew.observation, "round_steps": 0}
        if crew.is_running:
            crew.runtime_state, crew.next_run_at = "waiting", utcnow()
        return {"decision": "answered"}
    if held.get("kind") == "review_stuck":
        crew.observation = {**crew.observation, "review_round": 0}
        if approve:
            forward = held.get("result", "") + (f"\n\nYour note: {note}" if note else "")
            deliver(session, crew, forward, held.get("files") or [])
            outcome = "sent on"
        else:
            send_back(
                session,
                f"You (via {crew.name})",
                [held["from_id"]] if held.get("from_id") else plan(crew).get("upstream", []),
                (held.get("review", "") + (f"\n\nFrom the person: {note}" if note else "")).strip(),
            )
            outcome = "sent back"
        if crew.is_running:
            crew.runtime_state, crew.next_run_at = (
                "waiting_event" if crew.trigger_type == "manual" else "waiting",
                None if crew.trigger_type == "manual" else utcnow(),
            )
        return {"decision": outcome}
    if approve:
        work = {k: v for k, v in held.items() if k not in {"kind", "state_before"}}
        if note:
            work["result"] = (work.get("result", "") + f"\n\nThe person's note: {note}")[:3000]
        _add_handoff(crew, work)
        if crew.is_running:
            crew.runtime_state, crew.next_run_at = "waiting", utcnow()
        return {"decision": "approved"}
    send_back(
        session,
        f"You (via {crew.name})",
        [held["from_id"]] if held.get("from_id") else plan(crew).get("upstream", []),
        note or "The person sent this back without a note: do it again, better.",
    )
    if crew.is_running:
        crew.runtime_state = (
            state_before if state_before not in {"needs_input", "working"} else "waiting_event"
        )
        crew.next_run_at = None
    return {"decision": "sent back"}
