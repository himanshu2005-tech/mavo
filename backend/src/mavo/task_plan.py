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
CALL_CEILING = 40  # the model chooses a budget for the job; nothing may exceed this
STEP_ID = re.compile(r"^[a-z][a-z0-9_]{0,23}$")


class TaskStep(BaseModel):
    # A model writes these plans, and a small slip (a messy address, an agent with no actions
    # listed, a step reading one that is not there) must not throw away a plan whose intent is
    # clear. What can be put right is put right, quietly; only what has no sensible reading is
    # refused.
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    id: str
    kind: Literal["helper", "agent", "crew", "automation", "skill"]
    title: str = Field(min_length=1, max_length=80)
    instructions: str = Field(min_length=1, max_length=3000)
    role: str | None = Field(default=None, max_length=40)  # helper: critic, verifier, writer...
    uses: list[str] = Field(default_factory=list, max_length=6)  # steps whose output this reads
    actions: list[str] = Field(default_factory=list, max_length=10)  # agent: tools it needs
    sites: list[str] = Field(default_factory=list, max_length=5)  # agent: sites it will open
    crew_id: str | None = Field(default=None, max_length=80)
    automation: str | None = Field(default=None, max_length=60)
    skill: str | None = Field(default=None, max_length=60)  # a built-in skill (site_skills.py)
    params: dict = Field(default_factory=dict)  # the skill's parameters
    calls: int = Field(default=6, ge=1, le=20)  # agent: its own cap on model calls
    open_data: bool = False  # agent: looks things up in open data (resources/open_data.py)
    # agent: the person's own Google data it reads, read-only (calendar, tasks, gmail, drive,
    # contacts). "Plan my day" gave this to a helper, which cannot see anything, and the plan
    # was empty slots.
    google: list[str] = Field(default_factory=list, max_length=5)

    @model_validator(mode="before")
    @classmethod
    def tidy(cls, data):
        from mavo.action_rules import CATALOG

        if not isinstance(data, dict):
            return data
        d = dict(data)
        step_id = re.sub(r"[^a-z0-9_]", "_", str(d.get("id") or "").lower()).strip("_")[:24]
        if not step_id or not step_id[0].isalpha():
            step_id = ("s" + step_id)[:24]
        d["id"] = step_id
        d["title"] = (str(d.get("title") or "").strip() or step_id)[:80]
        d["instructions"] = (str(d.get("instructions") or "").strip() or d["title"])[:3000]
        ops = [a for a in (d.get("actions") or []) if isinstance(a, str) and a in CATALOG]
        d["actions"] = list(dict.fromkeys(ops))[:10]
        d["sites"] = clean_sites(d.get("sites"))
        d["uses"] = [u for u in (d.get("uses") or []) if isinstance(u, str)][:6]
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
    steps: list[TaskStep] = Field(min_length=1, max_length=MAX_STEPS)
    final: str | None = Field(default=None, max_length=24)  # the step whose answer is the result
    model_calls: int = Field(default=12, ge=2, le=CALL_CEILING)  # chosen by the model, capped

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
        d["title"] = (str(d.get("title") or "").strip() or "Task")[:80]
        d["deliver"] = (str(d.get("deliver") or "").strip() or "The result")[:300]
        if isinstance(d.get("steps"), list):
            d["steps"] = d["steps"][:MAX_STEPS]
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
    pending = {step.id: set(step.uses) for step in steps}
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
