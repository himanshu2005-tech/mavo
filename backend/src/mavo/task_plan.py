"""A one-time task: the steps, who does each, and what the person needs back.

The chat plans a task the moment someone asks for a job rather than a lasting responsibility
("create a Microsoft form with name and email and tell me the link", "research X, write it up,
have it checked"). Each step is done by one of:
- a helper: a one-time, think-only agent (a critic, a verifier, a writer...). No tools. It reads
  what it is given and answers. It exists for one call.
- an agent: a one-time agent that may use tools once, under the person's own default Actions
  settings (never more than they have turned on). It lives for the task and is deleted after.
- a crew: one of the person's existing Crew, handed an instruction.
- an automation: something saved earlier, run without any model.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_STEPS = 8
INTERNAL_STEPS = 72  # with the steps Mavo adds and a team's expanded members
CALL_CEILING = 40  # the model chooses a budget for the job; nothing may exceed this
STEP_ID = re.compile(r"^[a-z][a-z0-9_]{0,23}$")


class TaskStep(BaseModel):
    # A model writes these plans, and a small slip (a messy address, an agent with no actions
    # listed, a step reading one that is not there) must not throw away a plan whose intent is
    # clear. What can be put right is put right, quietly; only what has no sensible reading is
    # refused.
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    id: str
    kind: Literal["helper", "agent", "crew", "automation", "skill", "read", "act"]
    title: str = Field(min_length=1, max_length=80)
    # What the person sees while this step runs, in plain present-tense words ("Reading your
    # recent emails", "Picking today's quote"): never a step name like "Final Output".
    run_text: str | None = Field(default=None, max_length=80)
    instructions: str = Field(min_length=1, max_length=3000)
    completion_criteria: str = Field(
        default="Return a verifiable deliverable or state what is blocked.", max_length=1000
    )
    deliverables: list[str] = Field(default_factory=list, max_length=8)
    role: str | None = Field(default=None, max_length=80)  # matches canonical AgentSpec roles
    uses: list[str] = Field(default_factory=list, max_length=72)  # bounded expanded fan-in
    actions: list[str] = Field(default_factory=list, max_length=10)  # agent: tools it needs
    sites: list[str] = Field(default_factory=list, max_length=5)  # agent: sites it will open
    crew_id: str | None = Field(default=None, max_length=80)
    agent_id: str | None = Field(default=None, max_length=36)
    automation: str | None = Field(default=None, max_length=60)
    skill: str | None = Field(default=None, max_length=60)  # a built-in skill (site_skills.py)
    params: dict = Field(default_factory=dict)  # the skill's parameters
    calls: int = Field(default=6, ge=1, le=20)  # agent: its own cap on model calls
    open_data: bool = False  # agent: looks things up in open data (resources/open_data.py)
    # agent: the person's own Google data it reads, read-only (calendar, tasks, gmail, drive,
    # contacts). "Plan my day" gave this to a helper, which cannot see anything, and the plan
    # was empty slots.
    google: list[str] = Field(default_factory=list, max_length=6)
    # Recipe steps (recipes.py), replayed with no model: a read of one source, and one action
    # tag run through the person's permissions with arguments from a think step's JSON.
    source: str | None = Field(default=None, max_length=60)
    new_only: bool = False
    operation: str | None = Field(default=None, max_length=60)
    args: dict = Field(default_factory=dict)
    args_from: str | None = Field(default=None, max_length=24)
    # Runs only when a step's output is empty / not empty / has a word: an empty run costs nothing.
    # Or on a field: {"step", "field", "in" | "equals" | "min" | "max" | "is"} (workflow.py).
    when: dict | None = None
    # A workflow's contracts and gates (workflow.py): what a think step must hand on, checked
    # by code before the next step gets it; and the only items of an input a step takes.
    returns: dict | None = None
    where: dict | None = None

    @model_validator(mode="before")
    @classmethod
    def tidy(cls, data):
        from mavo.action_rules import CATALOG

        if not isinstance(data, dict):
            return data
        d = dict(data)
        from mavo.workflow import tidy_contract, tidy_gate

        if d.get("when") is not None:
            d["when"] = tidy_gate(d["when"]) or cls.tidy_when(d["when"])
        d["returns"] = tidy_contract(d.get("returns"))
        d["where"] = tidy_gate(d.get("where"))
        if d.get("kind") in {"read", "act"}:
            return cls.tidy_recipe_step(d)
        step_id = re.sub(r"[^a-z0-9_]", "_", str(d.get("id") or "").lower()).strip("_")[:24]
        if not step_id or not step_id[0].isalpha():
            step_id = ("s" + step_id)[:24]
        d["id"] = step_id
        d["title"] = (str(d.get("title") or "").strip() or step_id)[:80]
        d["instructions"] = (str(d.get("instructions") or "").strip() or d["title"])[:3000]
        ops = [a for a in (d.get("actions") or []) if isinstance(a, str) and a in CATALOG]
        from mavo.connectors.catalog import provider_for

        # A one-time agent cannot change the person's connected accounts (Google, GitHub):
        # "Save the plan as tasks" was given tasks.create, stopped on "Needs a connected
        # account", and the task never finished. It writes out what to add instead.
        remote = [a for a in ops if provider_for(a) != "local"]
        if remote:
            ops = [a for a in ops if a not in remote]
            d["instructions"] = (
                str(d.get("instructions") or "").strip()
                + "\n\nYou cannot add to or change the person's accounts (Google Tasks, "
                "Calendar, Gmail, GitHub) in this task: list exactly what they should add, "
                "ready to copy."
            ).strip()
        d["actions"] = list(dict.fromkeys(ops))[:10]
        d["sites"] = clean_sites(d.get("sites"))
        d["uses"] = [u for u in (d.get("uses") or []) if isinstance(u, str)][:72]
        try:
            d["calls"] = max(1, min(20, int(d.get("calls", 6))))
        except (TypeError, ValueError):
            d["calls"] = 6
        kind = d.get("kind")
        if kind == "skill":
            kind = cls.tidy_skill(d)
        d["open_data"] = bool(d.get("open_data"))
        from mavo.google_permissions import GOOGLE_SERVICES

        wanted = [
            str(g).lower().removeprefix("google:").split(":")[0] for g in d.get("google") or []
        ]
        wanted = ["gmail" if g in {"mail", "email"} else g for g in wanted]
        d["google"] = [g for g in dict.fromkeys(wanted) if g in GOOGLE_SERVICES]
        if d["google"] and kind in {"helper", "agent", None}:
            kind = "agent"  # only an agent is given the person's data; a helper sees nothing
        if kind == "agent" and not d["actions"] and not d["google"]:
            # An agent that names a site needs to open and read it; one that looks things up in
            # open data needs no actions at all; with nothing to do, it only thinks.
            d["actions"] = ["browser.open", "browser.read"] if d["sites"] else []
            kind = "agent" if d["actions"] or d["open_data"] else "helper"
        elif kind == "helper" and d["actions"]:
            kind = "agent"  # it asked for tools: it is an agent
        elif kind == "crew" and not d.get("crew_id"):
            kind = "helper"
        elif kind == "automation" and not d.get("automation"):
            kind = "helper"
        elif kind not in {"helper", "agent", "crew", "automation", "skill"}:
            kind = "agent" if d["actions"] else "helper"
        if "browser.search" in d["actions"]:
            # Searching is only half of it: a result has to be opened and read. A plan that left
            # browser.open out had an agent search 26 times and never open one result.
            d["actions"] += [
                op for op in ("browser.open", "browser.read") if op not in d["actions"]
            ]
        if kind == "helper":
            d["actions"], d["sites"], d["open_data"], d["google"] = [], [], False, []
        d["kind"] = kind
        return d

    @staticmethod
    def tidy_when(value):
        """{"step": id, "is": "empty" | "not_empty"} or {"step": id, "has": "word"}; else none."""
        if not isinstance(value, dict) or not isinstance(value.get("step"), str):
            return None
        step = re.sub(r"[^a-z0-9_]", "_", value["step"].lower()).strip("_")[:24]
        if value.get("is") in {"empty", "not_empty"}:
            return {"step": step, "is": value["is"]}
        if isinstance(value.get("has"), str) and value["has"].strip():
            return {"step": step, "has": value["has"].strip()[:80]}
        return {"step": step, "is": "not_empty"}

    @classmethod
    def tidy_recipe_step(cls, d: dict) -> dict:
        from mavo.action_rules import CATALOG

        step_id = re.sub(r"[^a-z0-9_]", "_", str(d.get("id") or "").lower()).strip("_")[:24]
        d["id"] = step_id if step_id and step_id[0].isalpha() else ("s" + step_id)[:24]
        d["uses"] = [u for u in (d.get("uses") or []) if isinstance(u, str)][:72]
        if d["kind"] == "read":
            source = str(d.get("source") or "").strip().lower()
            source = {"mail": "gmail", "email": "gmail", "inbox": "gmail"}.get(source, source)
            d["source"] = source
            d["title"] = str(d.get("title") or f"Read {source}")[:80]
            d["instructions"] = str(d.get("instructions") or d["title"])[:3000]
            d["new_only"] = bool(d.get("new_only"))
        else:
            if d.get("operation") not in CATALOG:
                raise ValueError(f"Unknown action {d.get('operation')!r}")
            d["title"] = str(d.get("title") or CATALOG[d["operation"]][1])[:80]
            d["instructions"] = str(d.get("instructions") or d["title"])[:3000]
            d["args"] = d.get("args") if isinstance(d.get("args"), dict) else {}
            frm = d.get("args_from")
            d["args_from"] = frm if isinstance(frm, str) and frm else None
            if d["args_from"] and d["args_from"] not in d["uses"]:
                d["uses"].append(d["args_from"])
        for key in ("actions", "sites", "google"):
            d[key] = []
        return d

    @staticmethod
    def tidy_skill(d: dict) -> str:
        """A skill step runs Mavo's own code for a known job, so what it needs comes from the skill,
        not from the plan. A skill that does not exist, or parameters that do not fit it, still
        leave a plan whose instructions say what to do: an agent does it from those."""
        from mavo.site_skills import SKILLS

        skill = SKILLS.get(str(d.get("skill") or ""))
        try:
            if skill is None:
                raise ValueError("no such skill")
            d["params"] = skill.model.model_validate(d.get("params") or {}).model_dump()
        except ValueError:
            d["skill"], d["params"] = None, {}
            if d["actions"] or d["sites"]:
                return "agent"
            d["actions"] = ["browser.open", "browser.read"] if d["sites"] else []
            return "agent" if d["actions"] else "helper"
        d["actions"], d["sites"] = list(skill.actions), list(skill.sites)
        return "skill"


def clean_sites(values) -> list[str]:
    """Hostnames only: no scheme, path, port or credentials. What is not a hostname is dropped."""
    cleaned = []
    for value in values or []:
        host = str(value).strip().lower().removeprefix("https://").removeprefix("http://")
        host = host.split("/")[0].split("?")[0].split("@")[-1].split(":")[0].strip(".")
        if re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,251}[a-z0-9]", host) and "." in host:
            cleaned.append(host)
    return list(dict.fromkeys(cleaned))[:5]


class TaskPlan(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=80)
    deliver: str = Field(min_length=1, max_length=300)  # what the person needs back, and only that
    steps: list[TaskStep] = Field(min_length=1, max_length=72)
    expanded_crews: bool = False  # internal expansion of the <=8 public assignments
    final: str | None = Field(default=None, max_length=24)  # the step whose answer is the result
    model_calls: int = Field(default=12, ge=2, le=CALL_CEILING)  # chosen by the model, capped
    concurrency: int = Field(default=3, ge=1, le=4)
    timeout_seconds: int = Field(default=900, ge=30, le=3600)
    trigger_context: dict = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def tidy(cls, data):
        if not isinstance(data, dict):
            return data
        d = dict(data)
        build = next(
            (
                s
                for s in d.get("steps") or []
                if isinstance(s, dict) and s.get("skill") == "team.build"
            ),
            None,
        )
        # A build already made into the team's one step (it carries its project) stays as it is.
        given = build.get("params") if isinstance(build, dict) else None
        settled = isinstance(given, dict) and bool(given.get("project"))
        if build is not None and not (settled and len(d.get("steps") or []) == 1):
            # "Build me a ..." is the whole team, not whatever else the plan tried around it.
            from mavo.team_build import team_plan

            params = build.get("params") if isinstance(build.get("params"), dict) else {}
            request = str(
                params.get("request") or build.get("instructions") or d.get("title") or ""
            )
            if len(request.strip()) >= 3:
                d = team_plan(request, params.get("name"))
                if isinstance(data.get("model_calls"), int):
                    d["model_calls"] = data["model_calls"]
        d["title"] = (str(d.get("title") or "").strip() or "Task")[:80]
        d["deliver"] = (str(d.get("deliver") or "").strip() or "The result")[:300]
        if isinstance(d.get("steps"), list):
            # The planner's eight are checked where a plan comes in (validate_planning_graph).
            # Read back here, a plan keeps the steps Mavo added to it (the details each action
            # takes): cut to eight, a crew lost its last step, "Send & reply", on every run.
            d["steps"] = d["steps"][:INTERNAL_STEPS]
        try:
            d["model_calls"] = max(2, min(CALL_CEILING, int(d.get("model_calls", 12))))
        except (TypeError, ValueError):
            d["model_calls"] = 12
        return d

    @model_validator(mode="after")
    def coherent(self):
        seen: dict[str, int] = {}
        for step in self.steps:  # a repeated id gets its own
            seen[step.id] = seen.get(step.id, 0) + 1
            if seen[step.id] > 1:
                step.id = f"{step.id[:20]}_{seen[step.id]}"
        known = {step.id for step in self.steps}
        for step in self.steps:  # a step cannot read itself or one that is not there
            if step.when and step.when["step"] in known and step.when["step"] not in step.uses:
                step.uses = [*step.uses, step.when["step"]]  # its condition runs first
            step.uses = [u for u in dict.fromkeys(step.uses) if u in known and u != step.id]
        if levels(self.steps) is None:  # steps waiting on each other in a loop: do them in order
            for index, step in enumerate(self.steps):
                step.uses = [self.steps[index - 1].id] if index else []
        if self.final not in known:
            self.final = self.steps[-1].id
        return self


def levels(steps) -> list[list[str]] | None:
    """Steps grouped so each group only needs earlier ones (those in a group can run together).
    None when the steps depend on each other in a loop."""
    ids = {step.id for step in steps}
    # "after": runs later without reading it (a confirmation after its booking), so nothing it
    # did, skipped or failed passes on: params, as a workflow's writer sets it.
    pending = {
        step.id: set(step.uses) | {a for a in (step.params or {}).get("after") or [] if a in ids}
        for step in steps
    }
    order, done = [], set()
    while pending:
        ready = sorted(i for i, needs in pending.items() if needs <= done)
        if not ready:
            return None
        order.append(ready)
        done.update(ready)
        for i in ready:
            del pending[i]
    return order


def readable_source(source) -> bool:
    """A source a read step can read (the names read_source knows, and the aliases above)."""
    from mavo.recipes import GOOGLE_READS

    name = str(source or "").strip().lower()
    name = {"mail": "gmail", "email": "gmail", "inbox": "gmail"}.get(name, name)
    return (
        name in GOOGLE_READS
        or name.startswith("feeds:")
        or name
        in {
            "files",
            "memory",
            "weather",
        }
    )


def validate_planning_graph(raw: dict) -> None:
    """New planner/API boundary is strict; old saved plans retain their legacy normalization."""
    from mavo.action_rules import CATALOG

    steps = raw.get("steps", [])
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        raise ValueError("task.steps: a task must have between one and eight bounded steps")
    ids = [s.get("id") for s in steps if isinstance(s, dict)]
    if (
        len(ids) != len(steps)
        or any(not isinstance(i, str) for i in ids)
        or len(set(ids)) != len(ids)
    ):
        raise ValueError("Task step IDs must be present and unique")
    dependencies = {}
    for step in steps:
        needs = step.get("uses") or []
        if any(dep not in ids or dep == step["id"] for dep in needs):
            raise ValueError("Task has an invalid dependency")
        if any(op not in CATALOG for op in step.get("actions", [])):
            raise ValueError("Task asks for an unsupported tool")
        if step.get("kind") == "crew" and not step.get("crew_id"):
            raise ValueError("Crew assignments require an agent ID")
        if step.get("kind") == "read" and not readable_source(step.get("source")):
            # Caught here the planner writes it again; caught when the run reads, four read
            # steps with no source stopped a whole task with "Mavo cannot read ''".
            raise ValueError(
                f"Read step {step['id']!r} needs `source`: gmail, calendar, tasks, drive, "
                "contacts, feeds:<id>, files, memory or weather"
            )
        dependencies[step["id"]] = set(needs)
    done = set()
    while dependencies:
        ready = {sid for sid, needs in dependencies.items() if needs <= done}
        if not ready:
            raise ValueError("Task dependencies must not form a cycle")
        done |= ready
        dependencies = {sid: needs for sid, needs in dependencies.items() if sid not in ready}
    if raw.get("final") and raw["final"] not in ids:
        raise ValueError("Final deliverable step does not exist")
    # The model's call count is not checked: limits.CYCLE_MAX caps the task, whatever it wrote.


def orchestration_view(run_id: str, plan: TaskPlan) -> dict:
    """Consistent interface, without changing persistent Crew or build-member identities."""
    return {
        "id": run_id,
        "owner": "core",
        "model_calls": plan.model_calls,
        "deliverable": plan.deliver,
        "final": plan.final,
        "levels": levels(plan.steps),
        "assignments": [
            {
                "task_id": f"{run_id}:{s.id}",
                "step_id": s.id,
                "agent_id": (
                    f"agent:{s.agent_id}"
                    if s.agent_id
                    else f"crew:{s.crew_id}"
                    if s.kind == "crew"
                    else f"task:{run_id}:{s.id}"
                ),
                "dependencies": [f"{run_id}:{dep}" for dep in s.uses],
                "model_calls": s.calls,
                "completion_criteria": s.completion_criteria,
                "deliverables": s.deliverables,
                "required_capabilities": s.params.get(
                    "required_capabilities", s.actions + [f"google:{g}:read" for g in s.google]
                ),
            }
            for s in plan.steps
        ],
    }


SOURCE_WORDS = {
    "gmail": "Reading your email",
    "calendar": "Checking your calendar",
    "tasks": "Looking at your tasks",
    "drive": "Looking through your Drive",
    "contacts": "Looking at your contacts",
    "memory": "Remembering what you told Mavo",
    "files": "Reading your saved files",
}


def run_text_of(step: dict) -> str:
    """What a step is doing, for the person: its own run_text, else plain words from what it
    is (a run planned before steps had one, or a planner that left it out)."""
    kind = step.get("kind")
    said = str(step.get("run_text") or "").strip()
    # A read says what it really reads, whatever its planner wrote: a memory read called
    # "Checking your current tasks" claimed a task list it never had.
    if said and kind != "read":
        return said[:80]
    if kind == "read":
        source = str(step.get("source") or "")
        return SOURCE_WORDS.get(source.split(":")[0], "Reading " + source.split(":")[0])
    if kind == "act":
        from mavo.action_rules import CATALOG

        return CATALOG.get(step.get("operation") or "", ("", "Doing it"))[1]
    if kind == "skill" and step.get("skill") == "web.read":
        return "Searching the web"
    title = str(step.get("title") or "").strip()
    # A planner that named a step by its id ("s2") said nothing a person can read.
    if not title or title == step.get("id") or re.fullmatch(r"(?i)s(tep)?[\s_-]*\d+", title):
        return {"agent": "Working on it", "automation": "Checking"}.get(kind, "Thinking it through")
    return title[:80]
