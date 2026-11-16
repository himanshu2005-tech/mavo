"""Event-bound mail triage. Email is data, never tool or configuration authority."""

import json
from copy import deepcopy
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from mavo.database import CrewInstance, GoogleAccount, TaskRun
from mavo.google_auth import GoogleConnectionError


class MailDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    message_id: str = Field(min_length=1, max_length=160)
    important: StrictBool
    # Either may come back empty from a terse model (an unimportant mail often gets no summary):
    # each fills in from the other, so one short reply never fails the whole review.
    reason: str = Field(default="", max_length=500)
    summary: str = Field(
        default="",
        max_length=1000,
        description="One or two short plain sentences to the person: what happened, and what "
        "they should do with any time, place and deadline the mail gives. When nothing is "
        "needed from them, say what happened and end with 'Nothing to do.' Never invent a "
        "to-do, a time or a deadline. Do not repeat the sender or subject.",
    )
    follow_up: Literal["none", "needs_details", "planned", "already_tracked"] | None = None
    question: str | None = Field(default=None, max_length=1000)
    tracked_item_ids: list[str] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def fill_in(self):
        if not self.summary:
            self.summary = self.reason or (
                "Needs your attention." if self.important else "Nothing for you to do."
            )
        if not self.reason:
            self.reason = self.summary[:500]
        return self


class MailReviewError(GoogleConnectionError):
    pass


def assigned_event(factory, worker):
    """Use the parent's committed batch, not model-written event text or an arbitrary plan.

    Member adapters are manual workers, so they must not claim/advance the parent's cursor.
    The existing TaskRun still owns acknowledgement and restart recovery.
    """
    from mavo.crew_composition import execution_agent, validate_worker

    with factory() as session:
        current = session.get(CrewInstance, worker.id)
        agent = execution_agent(session, worker.id)
        if not current or not agent or agent.crew_id == worker.id:
            return None
        validate_worker(session, current)
        run = session.get(TaskRun, current.configuration_plan.get("task_run_id"))
        parent = session.get(CrewInstance, agent.crew_id)
        context = (run.plan.get("trigger_context") or {}) if run else {}
        if not context.get("resource") or not run.crew_id:
            return None
        resource = context["resource"]
        if f"google:{resource}:read" not in current.permissions:
            return None  # This role receives only its explicitly shared dependency artifacts.
        pending = parent.observation.get("pending_google") or {}
        if (
            run.crew_id != parent.id
            or any(
                context.get(k) != pending.get(k)
                for k in ("resource", "subject", "cursor", "generation", "events")
            )
            or context.get("generation") != parent.runtime_generation
        ):
            raise MailReviewError("The Google event assignment changed. Resume the current task.")
        check_account(session, context)
        return deepcopy(pending)


def check_account(session, context):
    account = session.get(GoogleAccount, "google")
    if (
        not account
        or account.subject != context.get("subject")
        or (context.get("epoch") is not None and str(account.connected_at) != context["epoch"])
    ):
        raise MailReviewError("The Google account changed during this event review.")


def new_mail_batch(observation, items, *, recurring, limit=10):
    """Filter before bounding; never acknowledge records omitted from the model's input."""
    seen = set(observation.get("seen_items", [])) if recurring else set()
    kept = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
            raise MailReviewError("Google returned an invalid inbox item")
        if item["id"] not in seen:
            kept.append(item)
            seen.add(item["id"])
        if len(kept) == limit:
            break
    return kept


NOT_SCHEDULED = "Not scheduled (Mavo could not plan this one): "


def validate_review(checkpoint, items, *, planning=False, fill=False):
    """Every supplied mail gets exactly one decision. Decisions about other IDs, and repeats, are
    dropped rather than failing the run. A mail left out is named in the repair request; when
    `fill` (after that repair) a few still left out are shown to the person as important rather
    than silently dropped, for an alert crew. A reply with no decisions at all still fails."""
    if not items or checkpoint.state != "wait_for_next_review" or checkpoint.question:
        return
    by_id = {item["id"]: item for item in items}
    kept, seen = [], set()
    for decision in checkpoint.mail_decisions:
        if decision.message_id in by_id and decision.message_id not in seen:
            kept.append(decision)
            seen.add(decision.message_id)
    checkpoint.mail_decisions = kept
    missing = [key for key in by_id if key not in seen]
    if missing and fill and kept:
        for key in missing:
            item = by_id[key]
            subject = str(item.get("subject") or "(no subject)")[:200]
            checkpoint.mail_decisions.append(
                MailDecision(
                    message_id=key,
                    important=True,
                    reason="Mavo could not get a clear decision on this mail, so it is shown "
                    "to you to be safe.",
                    summary=(
                        (NOT_SCHEDULED if planning else "") + f"Check this mail yourself: {subject}"
                    )[:1000],
                    follow_up="none" if planning else None,
                )
            )
        missing = []
    if missing:
        named = "; ".join(
            f"{key} ({str(by_id[key].get('sender') or '')[:60]}: "
            f"{str(by_id[key].get('subject') or '')[:80]})"
            for key in missing[:10]
        )
        error = MailReviewError(
            f"The mail review left out {len(missing)} of the {len(by_id)} supplied messages: "
            f"{named}. Return mail_decisions with exactly one message_id, important (boolean), "
            "reason and summary for EVERY supplied Gmail ID, including unimportant ones. Do not "
            "return 'Nothing new' for an unclassified message."
        )
        # A ready answer to fill in: small models complete a template far more reliably than
        # they build one. Includes the decisions already given, so nothing is lost.
        error.template = json.dumps(
            [d.model_dump(exclude_none=True) for d in kept]
            + [
                {"message_id": key, "important": None, "reason": "", "summary": ""}
                for key in missing[:10]
            ]
        )
        raise error
    if planning:
        for decision in checkpoint.mail_decisions:
            if not decision.important:
                continue
            if decision.follow_up == "none" and decision.summary.startswith(NOT_SCHEDULED):
                continue  # already shown to the person as not scheduled (see below)
            unresolved = decision.follow_up not in {"needs_details", "planned", "already_tracked"}
            if fill and (
                unresolved or (decision.follow_up == "needs_details" and not decision.question)
            ):
                # Still unresolved after the repair: shown to the person as not scheduled,
                # never guessed into the calendar, never silently skipped, never a failed run.
                decision.follow_up = "none"
                if not decision.summary.startswith(NOT_SCHEDULED):
                    decision.summary = (NOT_SCHEDULED + decision.summary)[:1000]
                continue
            if unresolved:
                raise MailReviewError(
                    "Resolve each actionable mail with follow_up: needs_details, planned, or "
                    "already_tracked. For missing scheduling details include one question. "
                    "Do not silently skip an actionable request or claim it was scheduled."
                )
            if decision.follow_up == "needs_details":
                if not decision.question:
                    raise MailReviewError(
                        "A needs_details mail decision requires a short question."
                    )
                checkpoint.question = decision.question
                checkpoint.state = "waiting_for_input"
                # A model must not both ask for a missing time and execute an invented
                # calendar/task proposal in the same checkpoint. Resume after the reply.
                checkpoint.actions = []
                break


def important_report(checkpoint, items, contents=None):
    """`contents` is accepted for callers' sake; mail text is never quoted in an alert."""
    headers = {item["id"]: item for item in items}

    # A desktop alert is plain text read in a glance: who, about what, and what it means for
    # the person. No headings, labels or quoted mail (raw mail cut mid-word read as nonsense).
    return "\n\n".join(
        f"{sender_name(headers[d.message_id].get('sender'))}: "
        f"{(headers[d.message_id].get('subject') or '(no subject)').strip()[:120]}\n"
        f"{d.summary.strip()[:400]}"
        for d in checkpoint.mail_decisions
        if d.important
    )


def sender_name(sender: str | None) -> str:
    """'"P&G Workday System" <pg@pg.com>' is P&G Workday System; a bare address is itself."""
    text = (sender or "").strip()
    name = text.split("<", 1)[0].strip().strip('"').strip()
    return (name or text.strip("<>") or "Unknown sender")[:60]


def quiet_report(checkpoint, items):
    headers = {item["id"]: item for item in items}
    return "No mail selected as important.\n\nModel assessments:\n" + "\n".join(
        f"- {(headers[d.message_id].get('subject') or '(No subject)')[:160]}: {d.reason}"
        for d in checkpoint.mail_decisions
    )


def new_mail_alert_ids(agent, run_id, owner_permissions, subject):
    """The parent owns delivery; reading mail does not grant a worker independent alerts."""
    worker = agent.worker
    if (
        not worker
        or agent.status != "active"
        or agent.execution.get("task_id") != run_id
        or "google:gmail:read" not in owner_permissions
        or "google:gmail:read" not in worker.permissions
        or worker.configuration_plan.get("first_run_source")
    ):
        return []
    required = set(agent.definition.get("capabilities", [])) & {
        "google:gmail:read",
        "google:gmail:body",
    }
    if not required <= set(owner_permissions):
        return []
    review = worker.observation.get("mail_review", {})
    if review.get("scope") != run_id or review.get("subject") != subject:
        return []
    # Effectful planners deliver confirmed changes or a correlated question, not a
    # mail-summary popup that could imply their scheduling objective was completed.
    if agent.definition.get("actions"):
        return []
    known = set(worker.observation.get("notified_mail_ids", []))
    return [
        d["message_id"]
        for d in review.get("decisions", [])
        if d.get("important") is True and d.get("message_id") not in known
    ]


def verify_mail_plan(session, agent, run):
    """Reading mail or saying 'planned' is not evidence of a scheduling outcome."""
    from sqlalchemy import select

    from mavo.database import ActionRequest

    if not agent or not agent.worker:
        raise MailReviewError("The assigned worker was removed before its outcome was verified.")
    worker = agent.worker
    review = worker.observation.get("mail_review", {})
    if review.get("scope") != run.id:
        return
    services = {
        op.split(".")[0]
        for op in agent.definition.get("actions", [])
        if op.startswith(("calendar.", "tasks."))
    }
    if not services:
        return
    decisions = [d for d in review.get("decisions", []) if d.get("important") is True]
    account = session.get(GoogleAccount, "google")
    if not account or review.get("subject") != account.subject:
        raise MailReviewError("The mail planner's Google account changed.")
    from mavo.first_run import is_verification

    parent = session.get(CrewInstance, agent.crew_id)
    statuses = ["completed", "previewed"] if is_verification(session, parent) else ["completed"]
    receipts = list(
        session.scalars(
            select(ActionRequest).where(
                ActionRequest.crew_id == worker.id,
                ActionRequest.generation == worker.runtime_generation,
                ActionRequest.account_subject == account.subject,
                ActionRequest.created_at >= run.created_at,
                ActionRequest.status.in_(statuses),
            )
        )
    )
    planned = [d for d in decisions if d.get("follow_up") == "planned"]
    inputs = worker.observation.get("google_input_ids", {})
    observed = (
        {resource + ":" + key for resource in services for key in inputs.get(resource, [])}
        if inputs.get("scope") == run.id
        else set()
    )
    operations = set(agent.definition.get("actions", []))
    for service in services:
        count = sum(
            r.operation in operations and r.operation.startswith(service + ".") for r in receipts
        )
        needs_change = sum(
            not any(
                key in observed and key.startswith(service + ":")
                for key in d.get("tracked_item_ids", [])
            )
            for d in planned
        )
        if count < needs_change:
            raise MailReviewError(
                f"The mail planner claimed a plan without confirmed {service} changes. "
                "The email is retained; no completed schedule was verified."
            )
    for decision in decisions:
        if decision.get("follow_up") == "already_tracked":
            ids = set(decision.get("tracked_item_ids", []))
            if (
                inputs.get("scope") != run.id
                or not ids
                or not ids <= observed
                or {key.split(":", 1)[0] for key in ids} != services
            ):
                raise MailReviewError(
                    "The mail planner did not identify existing Calendar/Tasks items for this "
                    "request. The email is retained instead of silently discarded."
                )
