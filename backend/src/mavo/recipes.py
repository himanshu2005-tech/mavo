"""Recipes: an agent's job, planned once as tagged steps and replayed every run.

A recurring agent used to think through its whole job on every run: which tools to use, in what
order. That cost model calls each time, could do it differently each time, and ran into call
limits. A recipe is planned ONCE from the tags Mavo can actually use, checked, and then replayed:
reading, conditions and actions run as plain code, and a model is called only for the steps
tagged as thinking (summarise, classify, extract, write, decide), or for an agent step when the
work is open-ended (a site it has never seen).

Tags come from the person's connections and permissions, never a fixed full list: Google tags
appear once Google is connected (reading, and adding or deleting only with the write access the
person gave), GitHub tags with GitHub, terminal, file and browser-clicking tags only when turned
on in Actions. Every action still goes through the same permission and approval rules as before.

When a recipe's run fails twice in a row, it is planned again (once), then replayed again.
"""

from __future__ import annotations

import json
import logging
import re

from mavo.action_rules import CATALOG, LOCAL_SERVICES
from mavo.connectors.catalog import provider_for
from mavo.task_plan import TaskPlan, TaskStep

logger = logging.getLogger(__name__)

THINK = {
    "summarise": "Summarise what it is given, short and plain",
    "classify": "Sort items into the labels you name (for example urgent / not urgent)",
    "extract": "Pull fields out of text as JSON (dates, names, amounts, links)",
    "write": "Write a message, plan or report from what it is given",
    "decide": "Choose what to do from what it is given and say why, briefly",
}
GOOGLE_READS = {
    "gmail": "new mail in the inbox (sender, subject, snippet)",
    "calendar": "events in the next 30 days",
    "tasks": "open tasks",
    "drive": "recently changed files (names only)",
    "contacts": "saved contacts",
    "youtube": "new videos from subscriptions",
}
REPLAN_AFTER = 2  # failed runs in a row before a recipe is planned again
POISON_AFTER = 3  # failed runs on the same new items before they are set aside


def available_tags(runtime, crew=None) -> dict:
    """What a recipe may use right now: {"read": {...}, "act": {...}, "think": {...}}.
    Only what is connected and allowed; a crew's own grant narrows actions further."""
    reads, acts = {}, {}
    granted = set(getattr(crew, "permissions", None) or [])
    pool = getattr(runtime, "google_pool", None)
    google_caps: set[str] = set()
    if pool is not None:
        try:
            status = pool.auth.status()
            if status.get("signed_in"):
                google_caps = set(status.get("capabilities") or [])
        except Exception:  # noqa: BLE001 - not connected is the same as no Google tags
            logger.warning("Could not read the Google connection", exc_info=True)
    for service, meaning in GOOGLE_READS.items():
        if f"{service}:read" in google_caps and (
            crew is None or f"google:{service}:read" in granted
        ):
            reads[service] = meaning
    connectors = getattr(runtime, "connectors", None)
    feeds = getattr(connectors, "feeds", None)
    if feeds is not None:
        for key in feeds.selected():
            if crew is None or f"feeds:{key}:read" in granted:
                reads[f"feeds:{key}"] = f"the {key} news feed"
    reads["files"] = "the person's saved files (Knowledge), as text"
    reads["memory"] = "what the person told Mavo about themselves"
    rules = getattr(getattr(runtime, "actions", None), "rules", None)
    from mavo.google_actions import required_capability

    with runtime.factory() as session:
        for operation, (group, label, _consequential) in CATALOG.items():
            provider = provider_for(operation)
            service = operation.split(".")[0]
            if service in LOCAL_SERVICES:
                if service == "helper":
                    continue
                rule = rules.read(session, operation, getattr(crew, "id", None)) if rules else None
                if not rule or not rule.get("enabled"):
                    continue  # terminal, files, clicking: only when turned on in Actions
            elif provider == "google":
                need = required_capability(operation)
                if need not in google_caps:
                    continue  # adding or deleting only with the write access given
                if crew is not None and f"google:{need}" not in granted:
                    continue
            else:
                try:
                    if not connectors or not connectors.subject(provider):
                        continue
                except Exception:  # noqa: BLE001
                    continue
            acts[operation] = f"{group}: {label}"
    return {"read": reads, "act": acts, "think": dict(THINK)}


def fields_of(operation: str) -> str:
    """An action's arguments as the planner must give them: required first, then optional."""
    from mavo.google_actions import MODELS

    model = MODELS.get(operation)
    if model is None:
        return ""
    schema = model.model_json_schema()
    required = schema.get("required", [])
    props = schema.get("properties", {})

    def describe(name):
        spec = props.get(name, {})
        options = spec.get("enum") or next(
            (a.get("enum") for a in spec.get("anyOf", []) if a.get("enum")), None
        )
        return f"{name} (one of {', '.join(options)})" if options else name

    optional = [n for n in props if n not in required]
    text = ", ".join(describe(n) for n in required)
    if optional:
        text += "; optional: " + ", ".join(describe(n) for n in optional[:8])
    return text


def catalog_text(tags: dict) -> str:
    # The tag in backticks, then what it is: written "gmail = new mail ...", a planner used the
    # whole line as the tag ("read tag 'gmail = new mail in the inbox' is not available").
    lines = ["READ (no model): " + "; ".join(f"`{k}`: {v}" for k, v in tags["read"].items())]
    lines.append(
        "ACT (no model, through the person's permissions), with its arguments: "
        + ("; ".join(f"`{k}`: {v} [{fields_of(k)}]" for k, v in tags["act"].items()) or "none")
    )
    lines.append("THINK (one model call): " + "; ".join(f"`{k}`: {v}" for k, v in THINK.items()))
    return "\n".join(lines)


# Some providers refuse an empty system message (AIRouter: "system message must have content").
PLANNER_SYSTEM = "You plan Mavo recipes. Reply with one JSON object only, no prose."

RECIPE_PROMPT = """You turn an agent's job into a RECIPE: a few tagged steps Mavo replays on
every run. Use ONLY these tags:
{catalog}

Return ONE JSON object: {{"title": short name, "deliver": what the person gets each run,
"final": id of the step whose output is the run's result, "steps": [...]}}. Each step:
- {{"id": "s1", "kind": "read", "source": a READ tag, "new_only": true when only items not seen
  by the last run matter (new mail, new feed items)}}
- {{"id": "s2", "kind": "helper", "role": a THINK tag, "title": ..., "instructions": what to do,
  "uses": [ids it reads]}}. For an act step's arguments, its instructions say: "Reply with ONLY a
  JSON array of objects with the fields <fields>, or [] when there is nothing to do."
- {{"id": "s3", "kind": "act", "operation": an ACT tag, "args_from": id of the think step whose
  JSON gives the arguments (one action per object), or "args": fixed arguments; "uses": [...]}}
- {{"id": "s4", "kind": "agent", "title": ..., "instructions": ..., "actions": [ACT tags it may
  use], "uses": [...]}} ONLY for open-ended work no fixed steps can do (an unfamiliar website).
- Any step may have "when": {{"step": id, "is": "not_empty"}} (or "empty"), or
  {{"step": id, "has": "word"}}: it runs only then. Put it on the steps after a read that may
  find nothing new, so an empty run costs nothing.
RULES:
- EVERY step has "run_text": what the person sees while it runs, in plain present-tense
  words about THEIR things ("Reading your new emails", "Picking today's quote", "Checking
  your calendar for Friday"), at most 8 words. Never a step name ("Final Output"), never
  JSON, ids or tool names.
- As FEW steps as the job allows. Writing a message, a quote or a reminder needs NO read and NO
  chain: ONE write step, final. Never pass JSON between helpers just to hand on text.
- Never use one source in place of another: "memory" is what the person told Mavo about
  themselves, not their tasks, mail or calendar. When the job needs a source that is not in
  the READ tags (their tasks, with no tasks tag), read nothing for it and write without it; a
  step's run_text says what it really reads.
- Read ONLY the sources the job needs ("files" and "memory" only when the job is about the
  person's saved files or profile). Keep it short: usually 2 to 5 steps.
- "new_only" is for streams where only new items matter (mail, news feeds). A calendar or a
  task list is read whole: clashes and plans need all of it.
- Never add an action the job does not ask for (no saving notes, no mail, no tasks unless
  asked).
- The run's RESULT is the final step's output; it is what the person gets (as a desktop alert
  when they turned alerts on). To tell, warn, report or plan, end with a write or summarise
  step and make it "final"; it writes for the person, never JSON. Never use an agent step
  or an action just to deliver a message.
  Actions are side effects; the final write step may mention what was done.
- An act step with "args_from": the think step it reads must reply with ONLY a JSON array of
  objects with exactly that action's arguments, or [] when there is nothing to do.
- A step whose output a later step uses passes on EVERY detail that step needs: every option
  the source gave (all the times an email offers, as a list, not one "requested_time"), the
  sender's address, the thread id. A step that chooses among offered options picks from them
  (the earliest that is free) before proposing its own.
- A reply keeps the email's subject with "Re: " in front and goes into its thread (thread_id).
- This PC runs Windows: a terminal "shell" line is PowerShell, and terminal.run needs a
  "folder" (use "C:\\" when no folder matters). Prefer a ready-made "command" when one fits.
- An agent step that browses lists browser.search or browser.open, and browser.read.
- Never use a tag not listed. Never invent arguments the person did not give.

THE AGENT'S JOB: {job}
WHEN IT RUNS: {trigger}"""


def parse_json(text: str):
    """The JSON a model gave, from plain JSON, a fenced block, or JSON inside prose."""
    raw = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", raw, re.S | re.I)
    if fenced:
        raw = fenced.group(1).strip()
    # Whichever comes first: a recipe object's own "steps" list is not the answer.
    for start in sorted(i for i in (raw.find("["), raw.find("{")) if i >= 0):
        try:
            value, _ = json.JSONDecoder().raw_decode(raw[start:])
            return value
        except ValueError:
            continue
    raise ValueError("No JSON in the reply")


def check(plan: TaskPlan, tags: dict) -> TaskPlan:
    """A recipe uses only the tags it was given, reads before it acts, and every act step's
    arguments come from somewhere. Raises ValueError with what is wrong."""
    ids = [s.id for s in plan.steps]
    if len(set(ids)) != len(ids):
        raise ValueError("Step ids must be unique")
    for step in plan.steps:
        if step.kind == "read" and step.source not in tags["read"]:
            # A tag written with its description ("gmail = new mail in the inbox") is that tag.
            head = re.split(r"[\s=:(`]", (step.source or "").strip("` "), maxsplit=1)[0].lower()
            if head in tags["read"]:
                step.source = head
            else:
                raise ValueError(
                    f"{step.id}: read tag {step.source!r} is not available; use one of: "
                    + ", ".join(tags["read"])
                )
        if step.kind == "act":
            if step.operation not in tags["act"]:
                raise ValueError(f"{step.id}: act tag {step.operation!r} is not available")
            if not step.args and not step.args_from:
                raise ValueError(f"{step.id}: an act step needs args or args_from")
            if step.args and not step.args_from and "{{" not in json.dumps(step.args):
                from mavo.google_actions import ActionDraft

                try:
                    ActionDraft.model_validate(
                        {"operation": step.operation, "arguments": step.args}
                    )
                except ValueError as exc:
                    detail = "; ".join(
                        f"{'.'.join(str(x) for x in e.get('loc', []))}: {e.get('msg')}"
                        for e in getattr(exc, "errors", lambda: [])()
                    ) or str(exc)
                    raise ValueError(
                        f"{step.id}: the arguments for {step.operation} are not valid "
                        f"({detail[:300]}); it takes {fields_of(step.operation)}"
                    ) from None
            if step.args_from and step.args_from not in ids:
                raise ValueError(f"{step.id}: args_from {step.args_from!r} is not a step")
            source = next((s for s in plan.steps if s.id == step.args_from), None)
            if source is not None and source.kind != "helper":
                raise ValueError(
                    f"{step.id}: args_from must be a THINK step that replies with the JSON "
                    f"arguments, not a {source.kind} step"
                )
        if step.kind == "agent" and any(a not in tags["act"] for a in step.actions):
            raise ValueError(f"{step.id}: the agent step uses an action that is not available")
        if step.kind in {"crew", "skill", "automation"}:
            raise ValueError(f"{step.id}: a recipe uses read, think, act and agent steps only")
        if step.when and step.when.get("step") not in ids:
            raise ValueError(f"{step.id}: its condition names a step that does not exist")
    if plan.final and plan.final not in ids:
        raise ValueError("final must be one of the steps")
    return plan


ACTING_BROWSER = {
    "browser.click",
    "browser.double_click",
    "browser.drag",
    "browser.fill",
    "browser.upload",
    "browser.select",
    "browser.press",
}
ACTING_WORDS = re.compile(
    r"\b(apply|applies|application|fill|submit|book|register|sign up|order|buy|post|click|"
    r"upload|enrol|enroll|rsvp|reply on|vote)\b",
    re.I,
)


# What the job must mention for an action of that kind to stay in its recipe ("summarise my
# mail" was given a notes.write nobody asked for).
ASKED = {
    "notes": r"note|notes|journal|log|write (?:it )?down|save",
    "gmail": r"mail|email|e-mail|inbox|reply|send|draft|archive|trash|unsubscribe",
    "calendar": r"calendar|event|meeting|schedule|appointment|block",
    "tasks": r"task|to-?do|reminder",
    "terminal": r"terminal|command|disk|drive|cpu|memory|process|run|script|powershell|check",
    "files": r"file|folder|download|move|backup|back up|organi[sz]e|clean",
    "project": r"project|code|script|build|app",
    "windows": r"computer|pc|volume|music|media|speak|say|lock|shut ?down|sleep|window",
    "github": r"github|issue|pull request|repo",
    "browser": r".",
}


def asked_for(operation: str, job: str) -> bool:
    pattern = ASKED.get(operation.split(".")[0])
    return pattern is None or bool(re.search(pattern, job or "", re.I))


# An action is only ever for what was found, never for a placeholder.
ONLY_FOUND = (
    "Only for what an earlier step actually found: when it found nothing to act on, reply []; "
    "never placeholders or made-up details."
)


def acts_for_named_actions(plan: TaskPlan) -> TaskPlan:
    """A helper told to make a change ("create them in Google Tasks via the tasks.create
    action") can only write about it: a chief-of-staff run wrote seven tasks "for you to copy"
    and created none. Each create or draft action a helper's instructions name by its tag gets
    its own act step reading that helper (json_for_actions then gives it exact JSON), and the
    action still goes through the person's permissions. Only additions in the person's own
    Mail, Calendar and Tasks, never a risky one."""
    addable = [
        op
        for op, (group, _, risky) in CATALOG.items()
        if group in {"Mail", "Calendar", "Tasks"}
        and not risky
        and op.endswith((".create", ".draft"))
    ]
    steps = list(plan.steps)
    taken = {s.id for s in steps}
    fed = {(s.args_from, s.operation) for s in steps if s.kind == "act"}
    for helper in [s for s in steps if s.kind == "helper"]:
        for op in addable:
            if (helper.id, op) in fed or not re.search(
                rf"(?<![\w.]){re.escape(op)}(?![\w.])", helper.instructions or ""
            ):
                continue
            new_id = f"{helper.id}_{op.replace('.', '_')}"[:40]
            if new_id in taken or len(steps) >= 12:
                continue
            taken.add(new_id)
            act = TaskStep.model_validate(
                {
                    "id": new_id,
                    "kind": "act",
                    "title": CATALOG[op][1],
                    "operation": op,
                    "args_from": helper.id,
                    "uses": [helper.id],
                }
            )
            steps.insert(steps.index(helper) + 1, act)
            for later in steps:
                # What read the helper's words now also reads what the action did.
                if helper.id in later.uses and later.id != new_id and later.kind != "act":
                    later.uses = [*later.uses, new_id]
    plan.steps = steps
    return plan


def json_for_actions(plan: TaskPlan) -> TaskPlan:
    """An action is given exactly its fields as JSON: a think step that feeds it is told the
    action's real field names, and a prose step (a written plan) gets a small extract step
    between, so the prose stays the person's result. A task created "due_date" where the action
    takes "due", and a reminder was refused."""
    steps = list(plan.steps)
    by_id = {s.id: s for s in steps}
    for act in [s for s in steps if s.kind == "act" and s.args_from in by_id]:
        source = by_id[act.args_from]
        if source.kind != "helper" or source.returns or "{{item." in json.dumps(act.args):
            continue  # its contract, or the action's own item fields, already give the shape
        if (source.role or "").lower() == "extract":
            if plan.final == source.id:
                plan.final = act.id  # its JSON is for the action; the person reads what was done
            if "exactly the fields" not in source.instructions:
                source.instructions = (
                    source.instructions
                    + "\n\nReply with ONLY a JSON array of objects with exactly the fields "
                    f"{fields_of(act.operation)} (for {act.operation}), or [] when there is "
                    "nothing to do. " + ONLY_FOUND
                )[:3000]
            continue
        from mavo.task_plan import TaskStep

        extract = TaskStep.model_validate(
            {
                "id": f"{act.id}_args"[:24],
                "kind": "helper",
                "role": "extract",
                "title": f"Details for {act.title}"[:80],
                "instructions": f"From what you are given, list each {act.operation} to do as "
                f"ONLY a JSON array of objects with the fields {fields_of(act.operation)}, or "
                "[] when there is nothing to do. " + ONLY_FOUND + " No other text.",
                "uses": [source.id],
            }
        )
        steps.insert(steps.index(act), extract)
        by_id[extract.id] = extract
        act.args_from = extract.id
        act.uses = [extract.id if u == source.id else u for u in act.uses]
        if extract.id not in act.uses:
            act.uses.append(extract.id)
        if plan.final == act.id:
            plan.final = source.id  # the prose is what the person reads
    # The step that writes the person's report reads what each action actually did: a report
    # said "a Focus Time block was created" when the action had nothing to do and made none.
    by_id = {s.id: s for s in steps}
    final = by_id.get(plan.final)
    if final is not None and final.kind == "helper":

        def upstream(step_id, seen=None):
            seen = set() if seen is None else seen
            for used in by_id[step_id].uses if step_id in by_id else []:
                if used not in seen:
                    seen.add(used)
                    upstream(used, seen)
            return seen

        for act in [s for s in steps if s.kind == "act"]:
            if act.id not in final.uses and plan.final not in upstream(act.id):
                final.uses = [*final.uses, act.id]
    plan.steps = steps
    return plan


def tighten(plan: TaskPlan, job: str = "") -> TaskPlan:
    """What a planner forgets, put right in code:
    - a step after a new-only read waits for something new (an empty run costs no model call);
    - an action never reads a prose step: a small extract step between them gives it JSON,
      and the prose stays the person's result ("plan tomorrow and add prep tasks");
    - a browsing agent can open and read pages, and clicks or types only when the job asks it
      to apply, fill in, book or submit something."""
    unasked = {s.id for s in plan.steps if s.kind == "act" and not asked_for(s.operation, job)}
    if unasked and len(unasked) < len(plan.steps):
        kept = [s for s in plan.steps if s.id not in unasked]
        for step in kept:
            step.uses = [u for u in step.uses if u not in unasked]
            if step.when and step.when.get("step") in unasked:
                step.when = None
        if plan.final in unasked:
            plan.final = kept[-1].id
        plan.steps = kept
    json_for_actions(plan)
    fresh = {s.id for s in plan.steps if s.kind == "read" and s.new_only}
    for step in plan.steps:
        if step.kind in {"helper", "act", "agent"} and not step.when:
            source = next((u for u in step.uses if u in fresh), None)
            if source:
                step.when = {"step": source, "is": "not_empty"}
        if step.kind == "agent" and any(a.startswith("browser.") for a in step.actions):
            for needed in ("browser.open", "browser.read"):
                if needed not in step.actions:
                    step.actions.append(needed)
            if not ACTING_WORDS.search(job):
                step.actions = [a for a in step.actions if a not in ACTING_BROWSER]
        if step.kind == "agent":
            step.actions = [a for a in step.actions if asked_for(a, job)]
    return plan


def trigger_words(crew) -> str:
    kind = crew.trigger_type
    if kind == "interval":
        if crew.schedule_time:
            return f"every day at {crew.schedule_time}"
        return f"every {crew.review_interval_minutes} minutes"
    return {
        "gmail_message": "when new mail arrives (the run is given the new mail)",
        "calendar_changed": "when the calendar changes",
        "tasks_changed": "when tasks change",
        "daily": "at set times each day",
    }.get(kind, "when the person asks")


async def compile_recipe(runtime, crew, chat) -> TaskPlan:
    """Plans the crew's job once, from the tags available now. `chat(system, prompt)` is the
    model call. One repair if the first answer does not check out."""
    tags = available_tags(runtime, crew)
    prompt = RECIPE_PROMPT.format(
        catalog=catalog_text(tags),
        job=crew.responsibilities[:3000],
        trigger=trigger_words(crew),
    )
    messages = [{"role": "user", "content": prompt}]
    problem = None
    for attempt in range(3):
        raw = await chat(messages)
        try:
            data = parse_json(raw)
            if not isinstance(data, dict):
                raise ValueError("The recipe must be one JSON object")
            data.setdefault("title", crew.name[:80])
            data.setdefault("deliver", crew.responsibilities[:300])
            checked = check(TaskPlan.model_validate(data), tags)
            from mavo.task_plan import MAX_STEPS

            if len(data.get("steps") or []) > MAX_STEPS:  # the model's own plan: eight at most
                raise ValueError(f"A recipe has at most {MAX_STEPS} steps; combine some")
            return tighten(checked, crew.responsibilities)
        except ValueError as exc:  # pydantic's ValidationError is a ValueError
            problem = str(exc)[:600]
            if attempt == 2:
                break
            messages = [
                *messages,
                {"role": "assistant", "content": str(raw)[:6000]},
                {
                    "role": "user",
                    "content": "That recipe does not check out: "
                    + problem
                    + ". Reply with ONLY the corrected JSON object.",
                },
            ]
    raise ValueError(f"Could not plan a recipe: {problem}")


def recipe_line(plan: dict | None) -> str:
    """The recipe as the person reads it: "Read Gmail → Summarise → Create event"."""
    if not plan:
        return ""
    words = []
    for step in plan.get("steps", []):
        kind = step.get("kind")
        if kind == "read":
            words.append("Read " + str(step.get("source", "")).replace("feeds:", "").title())
        elif kind == "act":
            label = CATALOG.get(step.get("operation", ""), ("", step.get("operation", "")))[1]
            words.append(label)
        elif kind == "agent":
            words.append("Agent: " + str(step.get("title", ""))[:40])
        else:
            words.append(str(step.get("role") or "think").title())
    return " → ".join(words)


def changes_anything(plan: dict) -> bool:
    """Whether any of its agents can change something (the flag set when it was planned, or
    its agents' actions for one planned before the flag)."""
    from mavo.action_rules import LOOKS_ONLY

    if "changes_things" in plan:
        return bool(plan["changes_things"])
    agents = (plan.get("composition") or {}).get("agents") or []
    actions = {op for agent in agents for op in agent.get("actions") or []}
    return any(op not in LOOKS_ONLY and not op.startswith("helper.") for op in actions)


def eligible(crew) -> bool:
    """A recurring agent that works on its own: not a reminder or device monitor (already plain
    code), not a stage in a line of agents, not a hidden worker or setup test."""
    plan = crew.configuration_plan or {}
    return bool(
        crew
        # One that sends, books or writes runs the agents its test passed, each deciding on what
        # arrives. A recipe planned after the test replaced a tested mail crew and dropped its
        # calendar step, its reviewer and the sender's address: nothing was sent.
        and (
            not changes_anything(plan)
            or plan.get("recipe_tested")
            # written by Core and checked by code, turned on to ask first: no test needed
            or plan.get("recipe_checked")
        )
        and not crew.hidden
        and crew.status == "active"
        and crew.is_running
        and plan.get("composition")
        and crew.trigger_type != "daily"
        and crew.template_id != "computer"
        and not plan.get("upstream")
        and not plan.get("lead")
        and not plan.get("recipe_off")
    )


async def ensure_recipe(runtime, crew_id) -> bool:
    """Plans the agent's recipe once, if it has none (or it was dropped to be planned again).
    A failure leaves the agent on its old way of working and is tried again in six hours."""
    from datetime import datetime, timedelta

    from mavo.database import CrewInstance, ModelRoute, WorkspaceSetting, utcnow

    with runtime.factory() as session:
        crew = session.get(CrewInstance, crew_id)
        if not crew or not eligible(crew) or crew.configuration_plan.get("recipe"):
            return False
        failed = (crew.observation or {}).get("recipe_failed_at")
        if failed and utcnow() < datetime.fromisoformat(failed) + timedelta(hours=6):
            return False
        # Planned once, so with the person's planning model ("Chat uses") when they chose one.
        chosen = session.get(WorkspaceSetting, "planning-route")
        planner_id = (chosen.value or {}).get("route_id") if chosen else None
        route = session.get(ModelRoute, planner_id) if planner_id else None
        if route is None and crew.route_id:
            route = session.get(ModelRoute, crew.route_id)
        if route is None:
            route = next(iter(runtime.planner.agent_routes(session)), None)
        if route is None:
            return False
        provider, model, credential = route.provider, route.model, route.credential_id
        snapshot = type("Crew", (), {})()
        for field in (
            "id",
            "name",
            "responsibilities",
            "permissions",
            "trigger_type",
            "schedule_time",
            "review_interval_minutes",
        ):
            setattr(snapshot, field, getattr(crew, field))

    async def chat(messages):
        return await runtime.router.chat(provider, model, messages, PLANNER_SYSTEM, credential)

    try:
        plan = await compile_recipe(runtime, snapshot, chat)
    except Exception as exc:  # noqa: BLE001 - the agent keeps working the old way
        logger.warning("Recipe for %s not planned: %s", crew_id, exc)
        with runtime.factory() as session:
            crew = session.get(CrewInstance, crew_id)
            if crew:
                crew.observation = {
                    **(crew.observation or {}),
                    "recipe_failed_at": utcnow().isoformat(),
                    "recipe_problem": str(exc)[:300],
                }
                session.commit()
        return False
    with runtime.factory() as session:
        crew = session.get(CrewInstance, crew_id)
        if not crew or not eligible(crew) or crew.configuration_plan.get("recipe"):
            return False
        crew.configuration_plan = {
            **crew.configuration_plan,
            "recipe": plan.model_dump(mode="json"),
            "recipe_at": utcnow().isoformat(),
        }
        observation = dict(crew.observation or {})
        for key in ("recipe_failed_at", "recipe_problem", "recipe_failures"):
            observation.pop(key, None)
        crew.observation = observation
        session.commit()
    return True


def after_run(crew, run) -> bool:
    """A recipe run's end: remember what its reads saw (only when it succeeded), and after
    REPLAN_AFTER failures in a row drop the recipe so the next run plans it again."""
    if not (run.plan or {}).get("trigger_context", {}).get("recipe"):
        return False
    observation = dict(crew.observation or {})
    if run.status == "done":
        seen = dict(observation.get("recipe_seen") or {})
        for step in run.steps or []:
            if step.get("seen_ids") is not None:
                seen[step["id"]] = step["seen_ids"][:500]
        observation["recipe_seen"] = seen
        observation["recipe_failures"] = 0
        observation["poison_tries"] = 0
    elif run.status == "failed":
        failures = int(observation.get("recipe_failures", 0)) + 1
        observation["recipe_failures"] = failures
        outside = any(step.get("outside") for step in run.steps or [])
        kept = (crew.configuration_plan or {}).get("recipe_tested") or (
            crew.configuration_plan or {}
        ).get("recipe_checked")
        if kept and not outside:
            # The same new items broke a tested workflow three times, and not because the
            # provider or Google was down: they are set aside and the person is told, so one
            # email never stops every run after it. Outside faults are simply tried again.
            tries = int(observation.get("poison_tries", 0)) + 1
            observation["poison_tries"] = tries
            if tries >= POISON_AFTER:
                seen = dict(observation.get("recipe_seen") or {})
                count = 0
                for step in run.steps or []:
                    if step.get("seen_ids"):
                        seen[step["id"]] = [*seen.get(step["id"], []), *step["seen_ids"]][-500:]
                        count += len(step["seen_ids"])
                observation["recipe_seen"] = seen
                observation["poison_tries"] = 0
                observation["set_aside"] = (
                    f"Mavo set aside {count} new item{'s' if count != 1 else ''} it could not "
                    f"handle after {POISON_AFTER} tries: {run.error or 'a step failed'}"
                )[:400]
                run.error = observation["set_aside"]  # what the person reads for this run
                crew.observation = observation
                return True
        # A workflow that passed its scenarios is kept: planning it again untested is how a
        # mail crew lost its calendar step. Its failures show on the card instead.
        if failures >= REPLAN_AFTER and not kept:
            plan = dict(crew.configuration_plan or {})
            plan.pop("recipe", None)
            plan["recipe_replanned"] = int(plan.get("recipe_replanned", 0)) + 1
            crew.configuration_plan = plan
            observation["recipe_failures"] = 0
            observation.pop("recipe_seen", None)
    crew.observation = observation
    return False


def build_recipe_api(runtime):
    from fastapi import APIRouter, HTTPException

    from mavo.database import CrewInstance

    router = APIRouter()

    @router.get("/crew/{crew_id}/recipe")
    def show(crew_id: str):
        with runtime.factory() as session:
            crew = session.get(CrewInstance, crew_id)
            if not crew or crew.hidden:
                raise HTTPException(404, "Crew not found")
            plan = (crew.configuration_plan or {}).get("recipe")
            return {
                "enabled": bool(getattr(runtime, "recipes", False)) and eligible(crew),
                "off": bool((crew.configuration_plan or {}).get("recipe_off")),
                "recipe": plan,
                "line": recipe_line(plan),
                "planned_at": (crew.configuration_plan or {}).get("recipe_at"),
                "problem": (crew.observation or {}).get("recipe_problem"),
            }

    @router.post("/crew/{crew_id}/recipe/replan")
    def replan(crew_id: str):
        """Plan it again at the next run: after a change, or when it no longer fits."""
        with runtime.factory() as session:
            crew = session.get(CrewInstance, crew_id)
            if not crew or crew.hidden:
                raise HTTPException(404, "Crew not found")
            plan = dict(crew.configuration_plan or {})
            plan.pop("recipe", None)
            plan.pop("recipe_off", None)
            plan.pop("recipe_tested", None)  # a new one runs only once it passes its tests
            plan.pop("recipe_checked", None)
            crew.configuration_plan = plan
            observation = dict(crew.observation or {})
            for key in ("recipe_failed_at", "recipe_problem", "recipe_failures", "recipe_seen"):
                observation.pop(key, None)
            crew.observation = observation
            session.commit()
        return show(crew_id)

    return router
