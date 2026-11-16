"""Writing a workflow, and testing it before it runs.

Mavo Core writes an agent's whole path once (every step, every case its job meets, a contract on
each think step) with test scenarios and what each must lead to. Before it may run, every
scenario is run through it on its own inputs, with nothing proposed or made, and what it would
do is compared, by code, with what it should. A recipe that was planned and never tested replaced
a tested mail crew: no calendar step, the sender's address lost, nothing sent."""

from __future__ import annotations

import asyncio
import json
import logging
import time as clock
from datetime import datetime
from email.utils import format_datetime

from pydantic import BaseModel, ConfigDict, Field

from mavo.workflow import parse_time

logger = logging.getLogger(__name__)

WORKFLOW_SYSTEM = "You write Mavo workflows. Reply with one JSON object only, no prose."
MAX_WORKFLOW_STEPS = 14
WORKFLOW_PROMPT = """You write the WORKFLOW for an agent's whole job: every step from what it reads
to what it does, and a path for every case its job will meet. Code runs it: reads, gates, finding
a time and actions take no model; a model is called only for a think step, and code checks its
reply against the step's contract before the next step gets it.

TAGS it may use:
{catalog}

STEPS (each has a unique short "id"):
- read: {{"id", "kind": "read", "source": a READ tag, "new_only": true for a stream where only
  new items matter (mail); a calendar is read whole}}
- think: {{"id", "kind": "helper", "role": a THINK tag, "title", "instructions": exactly what to
  decide or write and how to handle each case, "uses": [ids it reads], "returns": CONTRACT}}
  CONTRACT: {{"list": true, "fields": {{name: type}}, "required": [names]}}. Types: "text",
  "id" (copied exactly from what it read: code checks), "email" (one address it read: code
  checks), "number", "bool", "time" (ISO 8601 with UTC offset), "times" (a list of
  {{"start", "end"}}), "list", or a list of allowed words ["request", "cancel", ...]. Every
  think step whose output another step reads MUST have a contract; the next step gets only that.
- find time: {{"id", "kind": "automation", "automation": "calendar.slots", "title",
  "instructions", "uses": [the think step listing the requests, the calendar read], "params":
  {{"requests": that think step's id, "timezone": "{zone}", "start": "HH:MM", "end": "HH:MM",
  "days": ["mon", ...], "duration_minutes": n, "buffer_minutes": n, "horizon_days": n,
  "alternatives": n}}}}. Code, for each request item (which carries "offered": times), adds
  "chosen" (the first offered time that is free and inside the hours, or null), "alternatives"
  (free times, when none was) and "why". EVERY choice of a time goes through it: a model never
  checks a calendar or picks a free time.
- act: {{"id", "kind": "act", "operation": an ACT tag, "args_from": the step whose items it acts
  on (one action per item), "args": {{field: "{{{{item.path}}}}"}} to copy an item's field
  exactly (a booking's start is "{{{{item.chosen.start}}}}")}}. Without "args", the items
  themselves are the arguments, so that step's contract lists exactly the action's fields.
GATES, on any step: "when": {{"step", "field", and "in": [...] | "equals" | "min" | "max" |
"is": "empty" | "not_empty"}} runs the step only if an item passes; "where" (same shape) gives
the step only the items of that step that pass. Route each case with them: the items to book,
the ones to answer with other times, the ones to ignore. Give every gate a "label": the case
in a few plain words, as the person would say it ("the sender cancels", "none of their times is
free"): the person reads the path as these cases. A think step that is unsure says so in a
field ("confidence": a number) and a gate sends those items to the safe path.
RULES:
- EVERY step has "run_text": what the person sees while it runs, in plain present-tense
  words about THEIR things ("Reading your new emails", "Picking today's quote", "Checking
  your calendar for Friday"), at most 8 words. Never a step name ("Final Output"), never
  JSON, ids or tool names.
- Cover EVERY case the job meets, each with its own path. Decide every default the job leaves
  open from the person's words (hours, days, length, buffer, how many options, time zone
  {zone}) and write it into the params; never ask.
- What another step or an action needs is handed on exactly: addresses, ids, thread ids, the
  chosen time. A reply goes to the sender's address, keeps the subject with "Re: " in front and
  carries the thread_id; it is signed with the person's name or not at all.
- Never add an action the job does not ask for.
- "final" is a think step (role write, no contract) that tells the person briefly what
  happened: it reads the act steps and says only what they did.
- At most {max_steps} steps.
- "tests": 8 to 12 scenarios that together cover every path and edge case, each {{"name",
  "inputs": {{source: [items]}}, "expect": {{operation: [items]}}}}. Under each operation list
  exactly what must be done, with only the fields that decide it (calendar.create {{"start"}},
  gmail.send {{"to"}}); an operation not listed must not be done at all. What a test expects
  follows the params you set: with a 15-minute buffer, nothing is booked right after an event.
  Items look like:
{shapes}
  Every time asked for is after NOW; an email's own date is a little before NOW. Names and
  addresses at example.com only.

{now}THE AGENT'S JOB: {job}
WHEN IT RUNS: {trigger}

Reply with ONE JSON object: {{"title", "deliver", "final", "steps": [...], "tests": [...]}}"""


class WorkflowRequest(BaseModel):
    """What the app sends Mavo Core to have a workflow written; Core builds the prompt itself,
    so its key writes workflows and nothing else."""

    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    job: str = Field(min_length=1, max_length=3000)
    trigger: str = Field(default="", max_length=300)
    catalog: str = Field(min_length=1, max_length=6000)
    shapes: str = Field(default="", max_length=2000)
    now: str = Field(default="", max_length=400)
    zone: str = Field(default="+00:00", max_length=40)
    problem: str | None = Field(default=None, max_length=4000)  # what was wrong last time
    previous: str | None = Field(default=None, max_length=24000)  # the reply that was wrong


def workflow_messages(request: WorkflowRequest) -> list[dict]:
    messages = [
        {"role": "system", "content": WORKFLOW_SYSTEM},
        {
            "role": "user",
            "content": workflow_prompt(
                request.job,
                request.trigger,
                request.catalog,
                request.shapes,
                request.now,
                request.zone,
            ),
        },
    ]
    if request.problem and request.previous:
        messages += [
            {"role": "assistant", "content": request.previous},
            {
                "role": "user",
                "content": f"{request.problem}\nReply with ONLY the corrected JSON object.",
            },
        ]
    return messages


def workflow_prompt(job: str, trigger: str, catalog: str, shapes: str, now: str, zone: str) -> str:
    return WORKFLOW_PROMPT.format(
        catalog=catalog[:6000],
        zone=zone[:40],
        max_steps=MAX_WORKFLOW_STEPS,
        shapes=shapes[:2000],
        now=now[:400],
        job=job[:3000],
        trigger=trigger[:300],
    )


def check_workflow(data, tags: dict, required: list[str] | tuple = ()):
    """A workflow as Mavo will run it, and its test scenarios. Raises ValueError saying what
    is wrong, for the writer to put right."""
    from mavo.task_plan import TaskPlan

    if not isinstance(data, dict):
        raise ValueError("The workflow must be one JSON object")
    data = dict(data)
    tests = data.pop("tests", None)
    ids = [str(s.get("id")) for s in data.get("steps") or [] if isinstance(s, dict)]
    if data.get("final") not in ids and ids:
        # "final" written as a sentence, not a step id: the step that tells the person is the
        # last think step with no contract (or the last step).
        telling = [
            str(s.get("id"))
            for s in data["steps"]
            if isinstance(s, dict) and s.get("kind") == "helper" and not s.get("returns")
        ]
        data["final"] = (telling or ids)[-1]
    if len(data.get("steps") or []) > MAX_WORKFLOW_STEPS:
        raise ValueError(f"A workflow has at most {MAX_WORKFLOW_STEPS} steps")
    plan = TaskPlan.model_validate(data)
    by_id = {s.id: s for s in plan.steps}
    if len(by_id) != len(plan.steps):
        raise ValueError("Step ids must be unique")
    read_by_others = {u for s in plan.steps for u in s.uses} | {
        s.args_from for s in plan.steps if s.args_from
    }
    for step in plan.steps:
        if step.kind not in {"read", "helper", "automation", "act"}:
            raise ValueError(f"{step.id}: a workflow uses read, think, find-time and act steps")
        if step.kind == "read" and step.source not in tags["read"]:
            raise ValueError(f"{step.id}: read tag {step.source!r} is not available")
        if step.kind == "automation":
            if step.automation != "calendar.slots":
                raise ValueError(f"{step.id}: the only code step is calendar.slots")
            if step.params.get("requests") not in by_id:
                raise ValueError(f"{step.id}: params.requests must name the think step it reads")
            if not any(
                by_id.get(u) and by_id[u].kind == "read" and by_id[u].source == "calendar"
                for u in step.uses
            ):
                # With no calendar, every time looks free.
                raise ValueError(f"{step.id}: finding a time needs the calendar read in its uses")
        if step.kind == "act":
            if step.operation not in tags["act"]:
                raise ValueError(f"{step.id}: act tag {step.operation!r} is not available")
            source = by_id.get(step.args_from or "")
            if source is None:
                raise ValueError(f"{step.id}: args_from must name the step whose items it acts on")
        if (
            step.kind == "helper"
            and step.id in read_by_others
            and step.id != plan.final
            and not step.returns
        ):
            raise ValueError(f"{step.id}: another step reads it, so it needs a contract (returns)")
        for gate in (step.when, step.where):
            if gate and gate.get("step") not in by_id:
                raise ValueError(f"{step.id}: a gate names a step that does not exist")
    if plan.final not in by_id:
        raise ValueError("final must name the step that tells the person what happened")
    drop_missing_extras(plan)
    problems = wiring_problems(plan, tests)
    if problems:
        # All at once, so one correction fixes them all.
        raise ValueError("; ".join(problems[:8]))
    doing = {s.operation for s in plan.steps if s.kind == "act"}
    for operation in required:
        if operation not in doing:
            # A path that only drafts what the job asks it to send or book does nothing.
            raise ValueError(f"the job asks for {operation}: add the act step that does it")
    # Actions happen in the order written: a confirmation waits for its booking, so one whose
    # booking did not happen is never sent (tasks.act links them through their ids).
    # A message about a change goes out after the change: a confirmation was written before
    # its booking, so a booking that failed would still have been confirmed.
    acts = [s for s in plan.steps if s.kind == "act"]
    ordered = [s for s in acts if s.operation not in MESSAGES] + [
        s for s in acts if s.operation in MESSAGES
    ]
    earlier: list[str] = []
    for step in ordered:
        step.params = {**step.params, "after": [e for e in earlier if e not in step.uses]}
        earlier.append(step.id)
    fresh = {s.id for s in plan.steps if s.kind == "read" and s.new_only}
    for step in plan.steps:
        source = next((u for u in step.uses if u in fresh), None)
        if source and not step.when and step.kind != "read":
            # Nothing new is nothing to do, and costs nothing: a step that read an empty inbox
            # handed on an address it had never seen.
            step.when = {"step": source, "is": "not_empty"}
    if not isinstance(tests, list) or not 3 <= len(tests) <= 12:
        raise ValueError("tests must list 8 to 12 scenarios")
    for n, test in enumerate(tests, 1):
        if not isinstance(test, dict) or not isinstance(test.get("inputs"), dict):
            raise ValueError(f"test {n}: needs its inputs")
        if not isinstance(test.get("expect") or {}, dict):
            raise ValueError(f"test {n}: expect maps an operation to what it must do")
    return plan, [as_read(test, n) for n, test in enumerate(tests, 1)]


def as_read(test: dict, n: int) -> dict:
    """A test email or event shaped as Mavo reads the real one: Gmail always gives an email its
    id and thread id, a calendar its event ids. Test emails without them were set aside, and the
    test could not pass."""
    inputs = {}
    for source, items in (test.get("inputs") or {}).items():
        shaped = []
        for i, item in enumerate(items if isinstance(items, list) else [], 1):
            item = dict(item) if isinstance(item, dict) else {"text": str(item)}
            if source == "gmail":
                item.setdefault("id", f"test-mail-{n}-{i}")
                item.setdefault("thread_id", item["id"])
                item.setdefault("labels", ["INBOX", "UNREAD"])
                item.setdefault("date", format_datetime(datetime.now().astimezone()))
            elif source == "calendar":
                item.setdefault("id", f"test-event-{n}-{i}")
                item.setdefault("status", "confirmed")
                item.setdefault("etag", f'"test-etag-{n}-{i}"')  # as every real event has
            else:
                item.setdefault("id", f"test-{source}-{n}-{i}")
            item["sample"] = True
            shaped.append(item)
        inputs[source] = shaped
    return {**test, "inputs": inputs}


FOUND_TIME = {"chosen": "slot", "alternatives": "slots", "why": "text"}  # what find time adds


def fields_out(plan, step_id: str, seen=None) -> dict | None:
    """The fields a step hands on, as code knows them; None when not known (a read)."""
    by_id = {s.id: s for s in plan.steps}
    step = by_id.get(step_id)
    seen = seen or set()
    if step is None or step_id in seen:
        return None
    seen.add(step_id)
    if step.kind == "helper" and step.returns:
        return dict(step.returns.get("fields") or {})
    if step.kind == "automation" and step.automation == "calendar.slots":
        source = fields_out(plan, step.params.get("requests") or "", seen) or {}
        return {**source, "offered": "times", "duration_minutes": "number", **FOUND_TIME}
    return None


def drop_missing_extras(plan) -> None:
    """An optional detail of an action (a booking's description) that points at a field its
    source never hands on is left out, in code; a whole plan was refused for one such detail.
    A required one (a time, an address) is never guessed: wiring_problems refuses it."""
    import re as regex

    from mavo.google_actions import MODELS

    for step in plan.steps:
        if step.kind != "act" or not step.args:
            continue
        known = fields_out(plan, step.args_from or "")
        model = MODELS.get(step.operation)
        if known is None or model is None:
            continue
        needed = set(model.model_json_schema().get("required", []))
        for name, value in list(step.args.items()):
            roots = {
                p.split(".")[0] for p in regex.findall(r"\{\{\s*item\.([\w.]+)\s*\}\}", str(value))
            }
            if name not in needed and roots and not roots <= set(known):
                del step.args[name]


def wiring_problems(plan, tests) -> list[str]:
    """Where one step hands another what it does not have: an action reading a field its source
    never gives, a booking whose time a model chose, a gate on a field that is not there. A path
    booked "{{item.booking.start}}" from a list of texts, and no test could pass."""
    import re as regex

    from mavo.google_actions import MODELS

    problems: list[str] = []
    for step in plan.steps:
        for name, gate in (("where", step.where), ("when", step.when)):
            if gate and gate.get("field"):
                known = fields_out(plan, gate["step"])
                root = gate["field"].split(".")[0]
                if known is not None and root not in known:
                    problems.append(
                        f"{step.id}: its {name} checks {gate['field']}, which {gate['step']} "
                        f"does not hand on (it gives {', '.join(known) or 'nothing'})"
                    )
        if step.kind != "act":
            continue
        known = fields_out(plan, step.args_from or "")
        # A booking may take its time from an agent that decides when (only once the sender
        # accepted): code checks that time is free and inside the person's hours as it books
        # (tasks.act), so the plan is never refused, nor rewired, for it. Rewiring it onto the
        # found time dropped "only after they accept" and booked every request at once.
        paths = regex.findall(r"\{\{\s*item\.([\w.]+)\s*\}\}", json.dumps(step.args))
        for path in paths if known is not None else []:
            root, _, rest = path.partition(".")
            kind = known.get(root)
            if kind is None:
                problems.append(
                    f"{step.id}: {{{{item.{path}}}}} is not something {step.args_from} hands on "
                    f"(it gives {', '.join(known)})"
                )
            elif rest and not (kind == "slot" and rest in {"start", "end"}):
                problems.append(
                    f"{step.id}: {{{{item.{path}}}}} reads inside {root}, which is {kind}, not "
                    "an object. Hand on each value as its own field"
                )
        model = MODELS.get(step.operation)
        if not paths and known is not None and model is not None:
            needed = model.model_json_schema().get("required", [])
            missing = [f for f in needed if f not in known]
            if missing:
                problems.append(
                    f"{step.id}: its items are its arguments, but {step.args_from} does not hand "
                    f"on {', '.join(missing)}"
                )
    doing = {s.operation for s in plan.steps if s.kind == "act"}
    for n, test in enumerate(tests or [], 1):
        for operation in (test.get("expect") or {}) if isinstance(test, dict) else {}:
            if operation not in doing:
                problems.append(f"test {n} expects {operation}, which no step does")
    return problems


ACTION_WORDS = {"gmail.send": "Emails sent", "calendar.create": "Bookings", "gmail.draft": "Drafts"}


def when_line(value) -> str:
    found = parse_time(value)
    return found.astimezone().strftime("%a %d %b %H:%M") if found and found.tzinfo else str(value)


def say(operation: str, args: dict) -> str:
    """One action as the person reads it: "Send an email to asha@example.com"."""
    args = args if isinstance(args, dict) else {}
    if operation == "gmail.send":
        return f"Send an email to {args.get('to') or 'someone'}"
    if operation == "gmail.draft":
        return f"Draft an email to {args.get('to') or 'someone'}"
    if operation in {"calendar.create", "calendar.update"}:
        return "Book " + (when_line(args.get("start")) if args.get("start") else "an event")
    return operation


def email_line(test: dict) -> str:
    mails = [m for m in (test.get("inputs") or {}).get("gmail") or [] if isinstance(m, dict)]
    if not mails:
        return "No new email"
    first = mails[0]
    who = str(first.get("sender") or "").split("<")[0].strip() or str(first.get("sender") or "")
    more = f" (and {len(mails) - 1} more)" if len(mails) > 1 else ""
    return f"From {who}: {first.get('subject') or '(no subject)'}{more}"[:160]


def expectation_problems(expect: dict, would: list[dict]) -> list[str]:
    """How what a scenario did differs from what it had to do, in plain words; [] when the same."""

    def same(field, want, have) -> bool:
        if field in {"start", "end"}:
            a, b = parse_time(want), parse_time(have)
            return bool(a and b and a.tzinfo and b.tzinfo and a == b)
        return str(want).casefold().strip() in str(have or "").casefold()

    problems = []
    done: dict[str, list[dict]] = {}
    for action in would:
        done.setdefault(action["operation"], []).append(action.get("arguments") or {})
    for operation in sorted(set(expect or {}) | set(done)):
        wanted, made = list((expect or {}).get(operation) or []), list(done.get(operation) or [])
        label = ACTION_WORDS.get(operation, operation)
        if len(wanted) != len(made):
            problems.append(f"{label}: did {len(made)}, expected {len(wanted)}")
            continue
        for want in wanted:
            match = next(
                (m for m in made if all(same(k, v, m.get(k)) for k, v in dict(want).items())),
                None,
            )
            if match is None:
                problems.append(
                    f"{label}: expected {say(operation, want)}, did "
                    + ", ".join(say(operation, m) for m in made)
                )
            else:
                made.remove(match)
    return problems


async def run_scenario(runtime, plan, test: dict, timeout: float = 300) -> dict:
    """One scenario through the workflow on its own inputs: nothing is proposed or made."""
    from uuid import uuid4

    from mavo.database import TaskRun
    from mavo.task_plan import TaskPlan
    from mavo.tasks import SCENARIO

    trial = TaskPlan.model_validate(
        {
            **plan.model_dump(mode="json"),
            "trigger_context": {"dry_run": {"inputs": test.get("inputs") or {}}},
        }
    )
    run_id = str(uuid4())
    runner = runtime.task_runner
    with runtime.factory() as session:
        runner.prepare(session, trial, (SCENARIO + str(test.get("name") or ""))[:300], run_id)
        session.commit()
    runner.schedule(run_id)
    ends = clock.monotonic() + timeout
    while clock.monotonic() < ends:
        with runtime.factory() as session:
            run = session.get(TaskRun, run_id)
            if run and run.status not in {"running", "queued"}:
                would = [w for s in run.steps for w in s.get("would") or []]
                problems = expectation_problems(test.get("expect") or {}, would)
                if run.status != "done":
                    problems.insert(0, f"it did not finish: {run.error or run.status}")
                aside = [a for s in run.steps for a in s.get("set_aside") or []]
                if aside and not problems:
                    # A test email it could not read is a failed test, never a quiet pass.
                    problems.append("it could not read: " + aside[0]["problems"][0])
                titles = {s.id: s.title for s in plan.steps}
                stopped = [
                    f"{titles.get(s['id'], s['id'])}: {s.get('error')}"
                    for s in run.steps
                    if s.get("status") == "failed" and s.get("error")
                ] + [
                    f"{titles.get(s['id'], s['id'])}: set aside an email ({a['problems'][0]})"
                    for s in run.steps
                    for a in s.get("set_aside") or []
                ]
                session.delete(run)  # scratch work: the result is kept on the setup card
                session.commit()
                expected = [
                    say(operation, want)
                    for operation, wanted in (test.get("expect") or {}).items()
                    for want in wanted or []
                ]
                return {
                    "name": str(test.get("name") or "")[:120],
                    "passed": not problems,
                    "problems": problems[:6],
                    "would": would[:10],
                    # What it met and what it did, said plainly for the setup card.
                    "email": email_line(test),
                    "expected": expected or ["Nothing"],
                    "did": [say(w["operation"], w.get("arguments") or {}) for w in would]
                    or ["Nothing"],
                    "stopped": stopped[:4],
                }
        await asyncio.sleep(0.5)
    return {
        "name": str(test.get("name") or "")[:120],
        "passed": False,
        "problems": ["it took too long"],
        "would": [],
    }


AT_ONCE = 4  # test emails run together: one after another, twelve took many minutes


async def test_workflow(runtime, plan, tests, progress=None) -> list[dict]:
    """Every scenario, a few at a time, in their own order; `progress(done, total)` after each."""
    gate = asyncio.Semaphore(AT_ONCE)
    done = 0

    async def one(n, test):
        nonlocal done
        async with gate:
            result = await run_scenario(runtime, plan, as_read(test, n))
        done += 1
        if progress:
            progress(done, len(tests))
        return result

    return list(await asyncio.gather(*(one(n, t) for n, t in enumerate(tests, 1))))


def zone_label(now: datetime | None = None) -> str:
    """The person's UTC offset ("+05:30"): what their computer says, as everywhere in Mavo."""
    offset = (now or datetime.now().astimezone()).strftime("%z")
    return f"{offset[:3]}:{offset[3:]}" if offset else "+00:00"


def writing_context(runtime, crew) -> tuple[dict, dict, list[str]]:
    """What Core is told about the job (the request's fixed part), the tags it may use, and
    the actions the job asks for."""
    from mavo.first_run import SAMPLE_SHAPES
    from mavo.recipes import available_tags, catalog_text, trigger_words
    from mavo.tasks import now_line

    tags = draft_tags(runtime, crew, available_tags(runtime, crew))
    required = sorted(
        op for op in requested_ops(crew) if op in tags["act"] and not op.startswith("helper.")
    )
    base = {
        "job": crew.responsibilities[:3000],
        "trigger": trigger_words(crew)[:300],
        "catalog": catalog_text(tags)[:6000],
        "shapes": "\n".join(f"  - {k}: {v}" for k, v in SAMPLE_SHAPES.items() if k in tags["read"])[
            :2000
        ],
        "now": now_line()[:400],
        "zone": zone_label(),
    }
    return base, tags, required


async def ask_core(chat, base, tags, required, problem=None, previous=None, attempts=3, crew_id=""):
    """Core's answer, checked by code; told exactly what is wrong until it checks out or the
    attempts are used. A passing failure (the provider busy for a moment) uses an attempt."""
    from uuid import uuid4

    from mavo.coordinator_client import CoordinatorUnavailable
    from mavo.recipes import parse_json
    from mavo.thinking import split_thinking

    for attempt in range(attempts):
        request = {**base, "request_id": str(uuid4()), "problem": problem, "previous": previous}
        try:
            raw = str(await chat(request))
        except CoordinatorUnavailable as exc:
            if (exc.problem or {}).get("code") not in PASSING or attempt == attempts - 1:
                raise
            await asyncio.sleep(RETRY_WAITS[min(attempt, len(RETRY_WAITS) - 1)])
            continue
        previous = raw[:24000]
        try:
            return check_workflow(parse_json(split_thinking(raw)[0]), tags, required)
        except ValueError as exc:  # pydantic's ValidationError is a ValueError
            problem = f"That workflow does not check out: {str(exc)[:900]}"
            logger.warning(
                "Workflow for %s refused: %s | reply began: %s",
                crew_id,
                problem,
                raw[:300].replace("\n", " "),
            )
    logger.warning("Workflow for %s not written: %s", crew_id, problem)
    # The person reads this on the card: plain words, no field names (those are in the log).
    raise ValueError(
        "Mavo Core could not write a working plan for this crew. Try again, or describe what it "
        "should do in simpler words."
    )


async def write_workflow(runtime, crew, chat) -> tuple:
    """The workflow and its scenarios, written by `chat(request)` (Mavo Core; the request is a
    WorkflowRequest) and checked by code; told exactly what is wrong up to twice."""
    base, tags, required = writing_context(runtime, crew)
    return await ask_core(chat, base, tags, required, crew_id=getattr(crew, "id", ""))


def failure_report(results: list[dict]) -> str:
    """The failed tests as Core reads them: the email, what it should do, what it did, where
    it stopped."""
    lines = []
    for r in results:
        if r.get("passed"):
            continue
        lines.append(
            f"- {r.get('name')}: email {r.get('email')}; "
            f"expected {', '.join(r.get('expected') or [])}; "
            f"did {', '.join(r.get('did') or [])}"
            + (f"; stopped at {'; '.join(r['stopped'][:2])}" if r.get("stopped") else "")
            + (f"; {'; '.join(r['problems'][:2])}" if not r.get("stopped") else "")
        )
    return "\n".join(lines)[:3000]


async def revise_workflow(runtime, crew, chat, plan, tests, results, attempts=2) -> tuple:
    """Core sees what its own tests did when the path ran, decides for each failure whether the
    path or the test's expected result was wrong, and fixes that one. Code checks it again."""
    base, tags, required = writing_context(runtime, crew)
    whole = json.dumps({**plan.model_dump(mode="json"), "tests": tests}, ensure_ascii=False)
    problem = (
        "When this path ran, these test emails did not lead to what they expected:\n"
        + failure_report(results)
        + "\nFor each, decide whether the path or that test's expected result is wrong, and fix "
        "that one. Keep what passed working. Reply with the whole corrected JSON object, "
        "steps and tests."
    )
    return await ask_core(
        chat,
        base,
        tags,
        required,
        problem=problem,
        previous=whole[:24000],
        attempts=attempts,
        crew_id=getattr(crew, "id", ""),
    )


def limited(chat, calls: int):
    """Mavo Core asked at most `calls` times in one setup test, whatever happens."""
    used = {"n": 0}

    async def ask(request):
        if used["n"] >= calls:
            raise ValueError(f"Mavo Core was asked {calls} times for this test, the most allowed")
        used["n"] += 1
        return await chat(request)

    ask.used = used
    return ask


WORKFLOW_VERSION = 6  # a path written by an earlier version of this is written again
FIX_ROUNDS = 3  # times Core fixes the path from its own failed tests, then the person is told
# The person's choice of how thoroughly to test: test emails run, and fix rounds at most.
LEVELS = {"quick": (4, 1), "standard": (8, 2), "thorough": (12, 3)}


def spread(tests: list, count: int) -> list:
    """`count` of the tests, spread over the whole list (Core lists them case by case), so a
    quick test still meets different cases, not only the first few."""
    if count >= len(tests):
        return list(tests)
    step = len(tests) / count
    return [tests[int(i * step)] for i in range(count)]


CORE_CALLS = 7  # Core calls in one setup test at most: writing and fixing together
MESSAGES = {"gmail.send", "gmail.draft", "notifications.show"}  # they tell someone what was done


def requested_ops(crew) -> set[str]:
    plan = getattr(crew, "configuration_plan", None) or {}
    draft = plan.get("draft") or {}
    ops = {a.get("operation") for a in draft.get("requested_actions") or [] if isinstance(a, dict)}
    for agent in composition_of(plan).get("agents") or []:
        ops |= set(agent.get("actions") or [])
    return {op for op in ops if op}


def draft_tags(runtime, crew, tags: dict) -> dict:
    """What the path may use: what is connected and granted, and what this draft asks for (a
    draft has not been granted its mail yet; without it, a path read saved files instead).
    Permissions are still checked when it runs."""
    from mavo.action_rules import CATALOG, LOOKS_ONLY
    from mavo.recipes import GOOGLE_READS

    plan = getattr(crew, "configuration_plan", None) or {}
    draft = plan.get("draft") or {}
    services = set(draft.get("requested_google_resources") or [])
    services |= {str(c).split(":")[0] for c in draft.get("requested_google_capabilities") or []}
    for agent in composition_of(plan).get("agents") or []:
        services |= {
            str(c).split(":")[1] for c in agent.get("capabilities") or [] if str(c).count(":") >= 2
        }
    reads = {**tags["read"]}
    reads.update({s: GOOGLE_READS[s] for s in services if s in GOOGLE_READS})
    acts = {**tags["act"]}
    for op in requested_ops(crew):
        if op in CATALOG and op not in LOOKS_ONLY and not op.startswith("helper."):
            group, label, _ = CATALOG[op]
            acts.setdefault(op, f"{group}: {label}")
    return {**tags, "read": reads, "act": acts}


def composition_of(plan: dict) -> dict:
    return plan.get("composition") or (plan.get("draft") or {}).get("composition") or {}


def wants_workflow(runtime, crew) -> bool:
    """An agent that sends, books or writes, on recipes: its whole path is written and tested
    before it is turned on. Others keep their own tests."""
    from mavo.recipes import changes_anything

    plan = crew.configuration_plan or {}
    return bool(
        getattr(runtime, "recipes", False)
        and composition_of(plan)
        and changes_anything({**plan, "composition": composition_of(plan)})
        and crew.trigger_type != "daily"
        and crew.template_id != "computer"
    )


def basis(crew) -> str:
    """What a written path was written from: the job, when it runs and what it may do. When
    any of these changes, the path is written again. Not the agent list: turning a draft on
    stores it again, and a path written a minute before was thrown away and written anew."""
    import hashlib

    plan = crew.configuration_plan or {}
    draft = plan.get("draft") or {}
    return hashlib.sha256(
        json.dumps(
            [
                WORKFLOW_VERSION,
                crew.responsibilities,
                draft.get("trigger_type") or crew.trigger_type,
                sorted(a.get("operation", "") for a in draft.get("requested_actions") or []),
            ],
            sort_keys=True,
            default=str,
        ).encode()
    ).hexdigest()[:24]


def stale_path(plan: dict, crew) -> dict:
    """The draft without a path written for an earlier version of it."""
    if plan.get("workflow_draft") and plan.get("workflow_basis") != basis(crew):
        return {
            k: v
            for k, v in plan.items()
            if k not in {"workflow_draft", "workflow_tests", "workflow_basis", "workflow_problem"}
        }
    return plan


def snapshot_of(crew):
    snapshot = type("Crew", (), {})()
    for field in (
        "id",
        "name",
        "responsibilities",
        "permissions",
        "trigger_type",
        "schedule_time",
        "review_interval_minutes",
        "configuration_plan",
    ):
        setattr(snapshot, field, getattr(crew, field))
    return snapshot


def update(runtime, crew_id, **fields) -> None:
    from mavo.database import CrewInstance

    with runtime.factory() as session:
        crew = session.get(CrewInstance, crew_id)
        if crew:
            plan = {**crew.configuration_plan, **fields}
            crew.configuration_plan = {k: v for k, v in plan.items() if v is not None}
            session.commit()


SETTINGS_PROMPT = (
    "From this job, as the person agreed it, give ONLY a JSON object with the settings it "
    "states in words. Leave out every setting it does not state: never guess one (code has "
    "sensible defaults, and the person sees and can change each one). The settings: start and "
    'end (working hours, "HH:MM", 24-hour), days (["mon", ...]), duration_minutes, '
    "buffer_minutes, horizon_days, alternatives (how many options to offer), max_rounds (how "
    "many back-and-forth emails before stopping).\nTHE JOB: "
)

# What the person may change on the card, in their own words: the rest stays as set.
EDITABLE = ("start", "end", "days", "duration_minutes")


def with_settings(steps_plan: dict | None, settings: dict) -> dict | None:
    """A path with its guarded agent's settings replaced."""
    if not steps_plan:
        return steps_plan
    steps = [
        {**s, "params": {**(s.get("params") or {}), **settings}}
        if s.get("automation") == "agent.desk"
        else s
        for s in steps_plan.get("steps") or []
    ]
    return {**steps_plan, "steps": steps}


def desk_sources(crew) -> list[str]:
    """What the crew reads, as its draft asked for it: its mail, its calendar, its tasks."""
    from mavo.recipes import GOOGLE_READS

    tags = draft_tags(None, crew, {"read": {}, "act": {}, "think": {}})
    order = ["gmail", "calendar", "tasks", "contacts", "drive"]
    return [s for s in order if s in tags["read"] and s in GOOGLE_READS]


async def write_desk(runtime, crew):
    """The crew's run: its reads and the guarded agent, with the settings Core's summary of
    the job states, in a fixed shape (defaults for what it leaves out). No program to test."""
    from mavo.desk import DeskSettings, desk_plan
    from mavo.recipes import parse_json
    from mavo.task_plan import TaskPlan
    from mavo.thinking import split_thinking

    stated: dict = {}
    try:
        route = runtime.task_runner.models()[0]
        raw = await runtime.router.chat(
            route.provider,
            route.model,
            [{"role": "user", "content": SETTINGS_PROMPT + crew.responsibilities[:3000]}],
            "You read settings out of a job description. JSON only.",
            route.credential_id,
        )
        found = parse_json(split_thinking(str(raw))[0])
        stated = found if isinstance(found, dict) else {}
    except Exception:  # noqa: BLE001 - the defaults stand
        logger.info("Settings for %s not read; defaults used", getattr(crew, "id", ""))
    fields = DeskSettings.model_fields
    settings = DeskSettings.model_validate({"timezone": zone_label()})
    # The zone is always the computer's (a model once wrote UTC for "local"); what the person
    # set on the card wins over what was read from the job.
    chosen = (getattr(crew, "configuration_plan", None) or {}).get("desk_settings") or {}
    for name, value in {**stated, **chosen}.items():
        if name in fields and name != "timezone":
            try:  # each on its own: one odd value never loses the others
                settings = DeskSettings.model_validate({**settings.model_dump(), name: value})
            except ValueError:
                continue
    return TaskPlan.model_validate(desk_plan(desk_sources(crew), settings, crew.responsibilities))


def is_desk(plan: dict | None) -> bool:
    return any(s.get("automation") == "agent.desk" for s in (plan or {}).get("steps") or [])


async def draft_workflow(runtime, crew_id) -> None:
    """The whole path of a draft, written as soon as it is designed, so the person sees every
    case it handles before testing it. The test then runs this same path."""
    from mavo.database import CrewInstance

    with runtime.factory() as session:
        crew = session.get(CrewInstance, crew_id)
        if crew is None or crew.status != "draft" or not wants_workflow(runtime, crew):
            update(runtime, crew_id, workflow_writing=None)
            return
        snapshot, written_for = snapshot_of(crew), basis(crew)
    try:
        plan, tests = await write_desk(runtime, snapshot), []
    except Exception as exc:  # noqa: BLE001 - said on the card
        update(runtime, crew_id, workflow_writing=None, workflow_problem=str(exc)[:300])
        return
    update(
        runtime,
        crew_id,
        workflow_writing=None,
        workflow_problem=None,
        workflow_draft=plan.model_dump(mode="json"),
        workflow_tests=tests,
        workflow_basis=written_for,
    )


RETRY_WAITS = (4, 12)  # seconds before asking Core again after a passing failure
PASSING = {"core_gateway_failed", "core_timeout", "core_bad_response", "core_empty_response"}


def core_chat(runtime):
    """Mavo Core writes it; with no Core set up, the person's planning model does."""
    from uuid import uuid4

    from mavo.coordinator_client import CoordinatorUnavailable

    async def chat(request: dict) -> str:
        core = getattr(runtime, "coordinator", None)
        if core is not None and hasattr(core, "workflow"):
            try:
                return await core.workflow({**request, "request_id": str(uuid4())})
            except CoordinatorUnavailable as exc:
                if (exc.problem or {}).get("code") != "core_not_configured":
                    raise
        route = runtime.task_runner.models()[0]
        messages = workflow_messages(WorkflowRequest.model_validate(request))
        return await runtime.router.chat(
            route.provider, route.model, messages[1:], messages[0]["content"], route.credential_id
        )

    return chat


async def setup_test(runtime, crew_id) -> dict:
    """The setup test of an agent that changes things: Mavo Core writes its whole workflow and
    its scenarios, every scenario runs through it with nothing made, and only a workflow that
    does what every scenario needs is kept, to run as written from then on."""
    from mavo.database import CrewInstance, utcnow

    def save(record: dict, **extra) -> None:
        with runtime.factory() as session:
            crew = session.get(CrewInstance, crew_id)
            if crew:
                crew.configuration_plan = {**crew.configuration_plan, "first_run": record, **extra}
                session.commit()

    from mavo.task_plan import TaskPlan

    with runtime.factory() as session:
        crew = session.get(CrewInstance, crew_id)
        if crew is None:
            return {}
        snapshot, written_for = snapshot_of(crew), basis(crew)
        current = crew.configuration_plan or {}
        written = (
            current.get("workflow_draft")
            if current.get("workflow_basis") == basis(crew)
            and (current.get("workflow_tests") or is_desk(current.get("workflow_draft")))
            else None
        )
        tests = current.get("workflow_tests") if written else None
    started = utcnow().isoformat()
    if written is None or is_desk(written):
        # The guarded agent: no program was generated for this crew, so there is none to
        # test. It is turned on to ask before its first changes; every action is checked.
        from mavo.action_rules import ASK_FIRST

        save({"status": "running", "phase": "writing", "started_at": started, "kind": "desk"})
        try:
            desk = written or (await write_desk(runtime, snapshot)).model_dump(mode="json")
        except Exception as exc:  # noqa: BLE001 - said on the card
            record = {"status": "failed", "kind": "desk", "message": str(exc)[:300]}
            save(record)
            return record
        record = {
            "status": "passed",
            "kind": "desk",
            "message": "Turned on. It asks you before its first 3 changes, then works on its own."
            if current.get("ask_first_choice")
            else "Turned on. Every action it takes is checked as it acts.",
        }
        save(
            record,
            workflow_draft=desk,
            workflow_basis=written_for,
            recipe=desk,
            recipe_at=started,
            recipe_checked=True,
            **(
                {"ask_first": {"since": started, "count": ASK_FIRST}}
                if current.get("ask_first_choice")
                else {}
            ),
        )
        return record
    chat = limited(core_chat(runtime), CORE_CALLS)
    save({"status": "running", "phase": "writing", "started_at": started, "kind": "workflow"})
    try:
        # The path the person was shown, when it is still the one for this draft.
        plan, tests = (
            (TaskPlan.model_validate(written), tests)
            if written
            else await write_workflow(runtime, snapshot, chat)
        )
    except Exception as exc:  # noqa: BLE001 - said on the card, never hidden
        record = {"status": "failed", "kind": "workflow", "message": str(exc)[:400]}
        save(record)
        return record
    # The path is shown as soon as it is written, while its tests run, and stays when one
    # fails: the person sees what was tried, not only that it did not pass.
    save(
        {"status": "running", "phase": "testing", "started_at": started, "kind": "workflow"},
        workflow_draft=plan.model_dump(mode="json"),
        workflow_tests=tests,
        workflow_basis=written_for,
    )

    fix_round = 0
    wanted, rounds = LEVELS.get(current.get("test_level") or "standard", LEVELS["standard"])
    rounds = min(rounds, FIX_ROUNDS)
    tests = spread(tests, wanted)

    def progress(done: int, total: int) -> None:
        save(
            {
                "status": "running",
                "phase": "testing",
                "started_at": started,
                "kind": "workflow",
                "done": done,
                "total": total,
                **({"round": fix_round, "rounds": rounds} if fix_round else {}),
            }
        )

    results = await test_workflow(runtime, plan, tests, progress)
    failed = [r for r in results if not r["passed"]]
    # Core sees what its tests did and fixes the path, or its own wrong expectation, by itself:
    # the person is never asked to test case after case.
    while failed and fix_round < rounds:
        fix_round += 1
        save(
            {
                "status": "running",
                "phase": "fixing",
                "started_at": started,
                "kind": "workflow",
                "round": fix_round,
                "rounds": rounds,
                "failed": len(failed),
                "total": len(results),
            }
        )
        try:
            plan, tests = await revise_workflow(runtime, snapshot, chat, plan, tests, results)
        except Exception as exc:  # noqa: BLE001 - the last tested path and its results stand
            logger.warning("Workflow for %s not fixed: %s", crew_id, exc)
            break
        save(
            {"status": "running", "phase": "testing", "started_at": started, "kind": "workflow"},
            workflow_draft=plan.model_dump(mode="json"),
            workflow_tests=tests,
            workflow_basis=written_for,
        )
        results = await test_workflow(runtime, plan, tests, progress)
        failed = [r for r in results if not r["passed"]]
    passed = len(results) - len(failed)
    # Most cases right: the person may turn it on, told which cases it may get wrong. Fewer
    # than half: not recommended. Either way, never turned on without them.
    mostly = bool(results) and passed * 2 >= len(results)
    record = {
        "status": "passed" if not failed else "partial" if mostly else "failed",
        "kind": "workflow",
        "tests": results,
        "fixed_rounds": fix_round,
        "passed_count": passed,
        "total": len(results),
        "message": (
            f"All {len(results)} tests passed."
            if not failed
            else f"Passed {passed} of {len(results)} tests. It may not handle some cases well."
            if mostly
            else f"Passed {passed} of {len(results)} tests. Not recommended to turn on yet."
        ),
    }
    if failed:
        save(record)
    else:
        save(
            record,
            recipe=plan.model_dump(mode="json"),
            recipe_at=utcnow().isoformat(),
            recipe_tested=True,
            workflow_tests=tests,
        )
    return record
