"""Running one-time tasks (see task_plan.py).

A task is a small graph of steps. Each runs as soon as the steps it reads are done (steps that
do not need each other run together), and the answer of the step marked final is the result:
reported on the person's desktop by Mavo itself, never left to a model to remember.

How each kind of step runs, and what keeps it safe:
- helper: one think-only model call. No tools, so it can do no harm; it costs one model call.
- agent: a short-lived hidden Crew, driven through the same worker as any Crew (so sign-in handling,
  budgets, stale-page handling and script approvals all apply). Its access is a copy of the
  person's own default Actions settings, and only for what they have turned on: asking for more
  than that stops the step and says exactly what to turn on. It is deleted when the step ends.
- crew: one of the person's existing Crew, handed the instruction.
Every run is held to the model-call budget its plan chose, up to a hard ceiling.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import re
from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from mavo.connectors.catalog import provider_for
from mavo.crew_templates import CREW_TEMPLATES_BY_ID
from mavo.database import (
    CrewInstance,
    CrewRun,
    ReminderEvent,
    ResourceEvent,
    TaskRun,
    WorkspaceSetting,
    utcnow,
)
from mavo.helpers import system_for
from mavo.task_plan import TaskPlan, TaskStep, levels

logger = logging.getLogger("mavo.tasks")

OWNER_NAME = "Mavo tasks"
APPROVAL_WAIT = 900  # how long a run waits for the person to approve a script
BUSY_WAIT = 600  # how long a step waits for a browser another job is using
STEP_TIMEOUT = 125
SKILL_LIMIT = 150  # seconds a built-in skill may take
GOOGLE_NAMES = {
    "calendar": "Calendar",
    "tasks": "Tasks",
    "gmail": "Gmail (subjects and senders)",
    "drive": "Drive (file names)",
    "contacts": "Contacts",
    "youtube": "YouTube (new videos from your subscriptions)",
}
SKILL_BUSY_WAIT = 120  # seconds it waits for a browser another job is using
CHECKERS = {"critic", "verifier"}
EVIDENCE = re.compile(r"\n\s*\**evidence\**\s*:", re.I)
SIGN_IN = re.compile(r"sign[ -]?in|log[ -]?in|login", re.I)
# What must never be typed to a model: the person types it in Mavo's own browser instead.
SECRET = re.compile(
    r"password|passcode|passphrase|\bpin\b|\botp\b|one[- ]time (?:code|pass)|verification code"
    r"|security code|authenticator|2fa|two[- ]factor|api[ -]?key|secret|\btoken\b|card number|cvv",
    re.I,
)
# Questions Mavo itself raises: a text box cannot settle them, so they stop the task as before.
FOR_YOU_TO_DO = re.compile(
    r"human check|Open Resources|another agent connection|access settings", re.I
)
BUILDS = {"browser.click", "browser.fill", "browser.select", "browser.press", "browser.drag"}
BUILDING = (
    "TO BUILD OR CHANGE SOMETHING on a page, chain several actions in ONE step (up to 16 run in "
    "order: click a control, fill it, press a key, click the next). Mavo reads the page again for "
    "you after every click, fill, press or navigation, so never spend a step on browser.read "
    "right after one, and never repeat an action that already worked. A control named like "
    '"Form title collapsed" is click-to-edit: click it, then fill it. A button such as "Text" or '
    '"Choice" adds that kind of question. If what you need is not in the last read, open the '
    "menu that holds it or scroll, instead of reading the same page again. "
)


class Blocked(Exception):  # noqa: N818 - reads as a state: the step needs the person
    """A step cannot go on without the person (a setting to turn on, a sign-in, an approval)."""


class OverBudget(Exception):  # noqa: N818
    pass


def answer_of(text: str) -> str:
    """The answer without the evidence behind it. An agent puts its answer first and then, after a
    line "EVIDENCE:", the exact lines it relied on: a checker reads all of it, the person only the
    answer."""
    found = EVIDENCE.search(text or "")
    return (text[: found.start()] if found else text or "").strip()


def plain(text: str) -> str:
    """The text as a person reads it on the desktop: no markdown bold or code ticks."""
    unbolded = re.sub(r"\*\*(.+?)\*\*", r"\1", text or "")
    return re.sub(r"`([^`\n]+)`", r"\1", unbolded).strip()


def clean_answer(text: str) -> str:
    """What an agent concluded, without the bookkeeping the worker appends to a checkpoint."""
    kept = [
        line
        for line in (text or "").splitlines()
        if not re.match(r"^(Action [\w.]+:|Execution details are in Activity)", line.strip())
    ]
    answer = "\n".join(kept)
    answer = re.split(r"\n\s*Next:", answer, maxsplit=1)[0]
    return re.sub(r"\n{3,}", "\n\n", answer).strip()


class TaskRunner:
    def __init__(self, runtime):
        self.runtime = runtime
        self.factory = runtime.factory
        self.jobs: dict[str, asyncio.Task] = {}
        self.browser_turn = asyncio.Lock()
        # (run, step) -> the answer a step is waiting for from the person
        self.asking: dict[tuple[str, str], asyncio.Future] = {}

    # ---- starting, stopping, looking ---------------------------------------------------------

    def start(
        self,
        plan: TaskPlan,
        request: str = "",
        run_id: str | None = None,
        conversation_id: str | None = None,
    ) -> str:
        with self.factory() as session:
            run = TaskRun(
                **({"id": run_id} if run_id else {}),
                title=plan.title,
                request=request[:4000],
                plan=plan.model_dump(mode="json"),
                steps=[
                    {"id": s.id, "title": s.title, "kind": s.kind, "status": "waiting"}
                    for s in plan.steps
                ],
                budget_calls=plan.model_calls,
                conversation_id=conversation_id,
            )
            session.add(run)
            session.commit()
            run_id = run.id
        job = asyncio.ensure_future(self.execute(run_id))
        self.jobs[run_id] = job
        job.add_done_callback(lambda _: self.jobs.pop(run_id, None))
        return run_id

    def stop(self, run_id: str) -> bool:
        with self.factory() as session:
            run = session.get(TaskRun, run_id)
            if not run or run.status != "running":
                return False
            run.status, run.error, run.finished_at = "stopped", "Stopped.", utcnow()
            run.steps = [
                {**s, "status": "stopped"} if s["status"] in {"waiting", "running"} else s
                for s in run.steps
            ]
            session.commit()
        job = self.jobs.get(run_id)
        if job:
            job.cancel()
        return True

    def recover(self) -> None:
        """Mavo closed in the middle of a task: say so, and clear the short-lived agents."""
        with self.factory() as session:
            for run in session.scalars(select(TaskRun).where(TaskRun.status == "running")):
                run.status, run.finished_at = "failed", utcnow()
                run.error = "Mavo closed while this task was running. Ask for it again."
                run.steps = [
                    {**s, "status": "failed"} if s["status"] in {"waiting", "running"} else s
                    for s in run.steps
                ]
            for crew in session.scalars(
                select(CrewInstance).where(
                    CrewInstance.hidden.is_(True), CrewInstance.name != OWNER_NAME
                )
            ):
                self.remove_agent(session, crew)
            session.commit()

    def sign_in_url(self, run: TaskRun) -> str | None:
        """Where the person signs in when a step stopped at a sign-in: the first site that step
        was allowed to open. The site sends them on to its own sign-in page."""
        steps = {s.get("id"): s for s in (run.plan or {}).get("steps", [])}
        for step in run.steps or []:
            if step.get("status") == "blocked" and SIGN_IN.search(step.get("question") or ""):
                sites = (steps.get(step["id"]) or {}).get("sites") or []
                if sites:
                    return "https://" + sites[0]
        return None

    def view(self, run: TaskRun) -> dict:
        return {
            "id": run.id,
            "title": run.title,
            "status": run.status,
            "steps": run.steps,
            "result": run.result,
            "error": run.error,
            "used_calls": run.used_calls,
            "budget_calls": run.budget_calls,
            "conversation_id": run.conversation_id,
            "sign_in_url": self.sign_in_url(run) if run.status == "needs_you" else None,
            "created_at": run.created_at,
            "finished_at": run.finished_at,
        }

    async def retry(self, run_id: str) -> str | None:
        """Run the same plan again (no new planning, so no model call to plan it): after the person
        has signed in, or turned an action on. A sign-in window left open is closed first, which is
        what saves its session for the browser the task uses."""
        with self.factory() as session:
            run = session.get(TaskRun, run_id)
            if not run or run.status == "running":
                return None
            plan = TaskPlan.model_validate(run.plan)
            request, conversation_id = run.request, run.conversation_id
        desktop = self.runtime.actions.desktop
        if desktop.user_window_open():
            await desktop.close_user_window()
        return self.start(plan, request, conversation_id=conversation_id)

    # ---- the run ---------------------------------------------------------------------------

    def change(self, run_id: str, step_id: str | None = None, **fields) -> None:
        with self.factory() as session:
            run = session.get(TaskRun, run_id)
            if not run:
                return
            if step_id is None:
                for key, value in fields.items():
                    setattr(run, key, value)
            else:
                run.steps = [{**s, **fields} if s["id"] == step_id else s for s in run.steps]
            session.commit()

    def status_of(self, run_id: str) -> str | None:
        with self.factory() as session:
            run = session.get(TaskRun, run_id)
            return run.status if run else None

    async def execute(self, run_id: str) -> None:
        with self.factory() as session:
            run = session.get(TaskRun, run_id)
            plan = TaskPlan.model_validate(run.plan)
        by_id = {s.id: s for s in plan.steps}
        outputs: dict[str, str] = {}
        stopped_at: dict[str, str] = {}  # step id -> why it did not finish
        blocked: list[str] = []
        gate = asyncio.Semaphore(3)

        async def one(step: TaskStep) -> None:
            if any(use in stopped_at for use in step.uses):
                stopped_at[step.id] = "A step it reads did not finish."
                self.change(run_id, step.id, status="skipped", error=stopped_at[step.id])
                return
            async with gate:
                if self.status_of(run_id) != "running":
                    return
                self.change(run_id, step.id, status="running", started_at=utcnow().isoformat())
                try:
                    inputs = {use: outputs[use] for use in step.uses}
                    outputs[step.id] = await self.perform(run_id, plan, step, inputs)
                    self.change(
                        run_id,
                        step.id,
                        status="done",
                        output=outputs[step.id][:6000],
                        finished_at=utcnow().isoformat(),
                    )
                except Blocked as need:
                    stopped_at[step.id] = str(need)
                    blocked.append(f"{step.title}: {need}")
                    self.change(run_id, step.id, status="blocked", question=str(need))
                except OverBudget:
                    stopped_at[step.id] = "The task's model-call budget ran out."
                    self.change(run_id, step.id, status="failed", error=stopped_at[step.id])
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning("Task step %s failed", step.id, exc_info=True)
                    stopped_at[step.id] = self.explain(exc)
                    self.change(run_id, step.id, status="failed", error=stopped_at[step.id])

        try:
            for group in levels(plan.steps) or []:
                if self.status_of(run_id) != "running":
                    return
                await asyncio.gather(*(one(by_id[i]) for i in group))
        except asyncio.CancelledError:
            return
        self.finish(run_id, plan, outputs, blocked, stopped_at)

    @staticmethod
    def explain(exc: Exception) -> str:
        if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
            return "The agent connection took too long to answer."
        text = str(exc).strip()
        return (text or type(exc).__name__)[:300]

    def finish(self, run_id, plan, outputs, blocked, stopped_at) -> None:
        with self.factory() as session:
            run = session.get(TaskRun, run_id)
            if not run or run.status != "running":
                return
            answer = self.deliverable(plan, outputs)
            if answer is not None:
                run.status, run.result = "done", answer
                message = answer
            elif blocked:
                run.status, run.error = "needs_you", "; ".join(blocked)
                message = "Needs you. " + "; ".join(blocked)
            else:
                run.status = "failed"
                run.error = "; ".join(stopped_at.values()) or "The task did not finish."
                message = "Could not finish. " + run.error
            run.finished_at = utcnow()
            title, status = run.title, run.status
            session.commit()
        self.alert(run_id, title, message, "end")
        logger.info("Task %s finished: %s", run_id, status)

    @staticmethod
    def deliverable(plan, outputs) -> str | None:
        """What the person is given. A critic's or verifier's remark is never the answer: the answer
        is the work it checked, and its verdict is added only when it found a problem."""
        by_id = {s.id: s for s in plan.steps}

        def checks(step) -> bool:
            return step.kind == "helper" and (step.role or "").lower() in CHECKERS

        target = plan.final
        while checks(by_id[target]) and by_id[target].uses:
            target = by_id[target].uses[0]
        if target not in outputs:
            return None
        notes = []
        for step in plan.steps:
            if checks(step) and step.id in outputs and target in step.uses:
                verdict = outputs[step.id]
                if re.search(r"VERDICT:\s*(revise|issues)", verdict, re.I):
                    lines = [x.strip() for x in verdict.splitlines() if x.strip()]
                    first = next((x for x in lines if not x.upper().startswith("VERDICT")), "")
                    notes.append(first[:240] or "It had concerns.")
        text = plain(answer_of(outputs[target]))
        if not notes:
            return text
        return text + "\n\nNot confirmed by the checker: " + " ".join(notes)

    # ---- one step --------------------------------------------------------------------------

    async def perform(self, run_id, plan, step, inputs) -> str:
        if step.kind == "helper":
            return await self.helper(run_id, plan, step, inputs)
        if step.kind == "agent":
            return await self.agent(run_id, plan, step, inputs)
        if step.kind == "crew":
            return await self.crew(run_id, plan, step, inputs)
        if step.kind == "skill" and step.skill == "team.build":
            from mavo.team_build import BuildTeam

            problem = self.access_problems(step)
            if problem:
                raise Blocked(problem)
            return await BuildTeam(self, run_id, plan, step).run()
        if step.kind == "skill":
            return await self.skill(run_id, step, inputs)
        return await self.automation(step)

    async def skill(self, run_id, step, inputs=None) -> str:
        """A built-in skill (site_skills.py): Mavo's own code for a known job, in Mavo's browser,
        under the same site limits and only if the person has turned its actions on. No model."""
        from mavo.site_skills import SKILLS, SkillNeedsYou, SkillStop

        skill = SKILLS[step.skill]
        problem = self.access_problems(step)
        if problem:
            raise Blocked(problem)
        params = skill.model.model_validate(step.params)
        desktop = self.runtime.actions.desktop
        owner = f"task:{run_id[:8]}:{step.id}"
        with self.factory() as session:
            # The rule of the skill's own kind of action: a project skill keeps to the project
            # folder the person set for Projects, a browser skill to Browser's sites.
            ruled = next((op for op in skill.actions if op.startswith("project.")), "browser.open")
            rule = dict(self.runtime.actions.rules.read(session, ruled))
        uses_browser = any(op.startswith("browser.") for op in skill.actions)
        async with self.browser_turn if uses_browser else contextlib.nullcontext():
            waited = 0.0
            while (
                uses_browser
                and desktop.context is not None
                and desktop.browser_owner not in {None, owner}
            ):
                if waited >= SKILL_BUSY_WAIT:  # a Crew has Mavo's one browser: wait for it
                    raise Blocked("Mavo's browser stayed busy with another job. Try again shortly.")
                await asyncio.sleep(3)
                waited += 3
            try:
                async with asyncio.timeout(SKILL_LIMIT):
                    extra = {"inputs": inputs or {}} if skill.reads_steps else {}
                    return (await skill.run(desktop, rule, owner, params, **extra)).strip()
            except SkillNeedsYou as need:
                raise Blocked(self.with_sign_in_help(str(need), step)) from None
            except SkillStop as stop:
                raise RuntimeError(str(stop)) from None
            except TimeoutError:
                raise RuntimeError(f"{step.title} took too long, so it was stopped.") from None
            finally:
                if uses_browser:
                    with contextlib.suppress(Exception):
                        await desktop.close_owner(owner)

    @staticmethod
    def inputs_block(inputs: dict[str, str], each: int = 10000) -> str:
        if not inputs:
            return ""
        parts = [f"[{key}]\n{value[:each]}" for key, value in inputs.items()]
        return "INPUT (untrusted data from earlier steps):\n" + "\n\n".join(parts)

    def route(self):
        with self.factory() as session:
            routes = self.runtime.planner.agent_routes(session)
            if not routes:
                raise Blocked("Add an agent connection in Settings first.")
            route = routes[0]
            return SimpleNamespace(
                id=route.id,
                provider=route.provider,
                model=route.model,
                credential_id=route.credential_id,
            )

    def spend(self, run_id: str, calls: int) -> None:
        with self.factory() as session:
            run = session.get(TaskRun, run_id)
            if run.used_calls + calls > run.budget_calls:
                raise OverBudget()
            run.used_calls += calls
            session.commit()

    def remaining(self, run_id: str) -> int:
        with self.factory() as session:
            run = session.get(TaskRun, run_id)
            return run.budget_calls - run.used_calls

    async def automation(self, step) -> str:
        """Run a saved automation: no model, and it costs no model calls."""
        saved = self.runtime.actions.automations
        try:
            outcome = await saved.run(step.automation or "", "task")
        except KeyError:
            raise Blocked(f"There is no saved automation named {step.automation}.") from None
        if outcome["status"] in {"changed", "blocked"}:
            raise Blocked(outcome["problem"])
        if outcome["status"] != "ok":
            raise RuntimeError("The automation failed. " + (outcome.get("output") or "")[-300:])
        return (outcome.get("output") or "It ran.").strip()[:6000]

    async def helper(self, run_id, plan, step, inputs, answered: bool = False) -> str:
        from mavo.team_build import BUILD_ROLES, OUTPUT_CHARS, TIMEOUT

        route = self.route()
        self.spend(run_id, 1)
        # The build team writes and reads whole files: room and time for them.
        building = (step.role or "").lower() in BUILD_ROLES
        given = self.inputs_block(inputs, 60000 if building else 10000)
        from mavo.shared_memory import memory_block

        with self.factory() as session:
            known = memory_block(session)
        prompt = (
            f"TASK: {plan.deliver}\nYOUR ROLE: {step.role or 'helper'}\n"
            f"YOUR JOB: {step.instructions}\n\n{given}{known}"
        )
        from mavo.model_router import LONG_REPLY
        from mavo.thinking import THINK, split_thinking

        system = system_for(step.role) + THINK

        room = LONG_REPLY.set(building)  # a whole file needs a long reply
        try:
            raw = await asyncio.wait_for(
                self.runtime.router.chat(
                    route.provider,
                    route.model,
                    [{"role": "user", "content": prompt}],
                    system,
                    route.credential_id,
                ),
                timeout=TIMEOUT if building else STEP_TIMEOUT,
            )
        finally:
            LONG_REPLY.reset(room)
        answer, thoughts = split_thinking(str(raw))
        if thoughts:  # the steps it thought in, shown on its line of the task card
            self.change(run_id, step.id, thinking=thoughts)
        asked = re.match(r"^\s*QUESTION:\s*(.+)", answer, re.S)
        if asked and (step.role or "").lower() == "architect" and not answered:
            # The team asks the person, on the task card, and carries on from the answer.
            reply = await self.ask_person(run_id, plan, step, asked[1].strip()[:400])
            step = step.model_copy(
                update={"instructions": f"{step.instructions}\n\nThe person answered: {reply}"}
            )
            return await self.helper(run_id, plan, step, inputs, answered=True)
        return answer[: OUTPUT_CHARS if building else 6000]

    # -- a one-time agent: a hidden, short-lived Crew under the person's default Actions --------

    def access_problems(self, step: TaskStep) -> str | None:
        from mavo.action_rules import CATALOG
        from mavo.desktop_actions import host_allowed, with_companions

        rules = self.runtime.actions.rules
        turn_on, sites, accounts = [], [], []
        with self.factory() as session:
            if rules.paused(session):
                return "Actions are paused. Resume them in Actions, then press Try again."
            for op in step.actions:
                label = CATALOG[op][1]
                if provider_for(op) != "local":
                    accounts.append(label)
                    continue
                rule = rules.read(session, op)
                if not rule["enabled"]:
                    turn_on.append(label)
                elif rule["mode"] == "restricted" and op.startswith("browser."):
                    allowed = with_companions(rule["domains"])
                    sites += [s for s in step.sites if not host_allowed(f"https://{s}/", allowed)]
        parts = []
        missing = [g for g in step.google if g not in self.google_readable()]
        if missing:
            names = ", ".join(GOOGLE_NAMES.get(g, g) for g in missing)
            parts.append(f"Connect Google in Resources and allow reading {names}")
        if turn_on:
            parts.append("Turn on in Actions (Defaults): " + ", ".join(dict.fromkeys(turn_on)))
        if sites:
            parts.append("Allow these sites for Browser: " + ", ".join(dict.fromkeys(sites)))
        if accounts:
            parts.append(
                "Needs a connected account, so use an existing Crew: " + ", ".join(accounts)
            )
        if not parts:
            return None
        hint = (
            " Then press Try again. Tip: Set all, then without limits, lets one-time tasks run "
            "without asking."
        )
        return ". ".join(parts) + "." + (hint if turn_on or sites else "")

    def google_readable(self) -> set[str]:
        """The Google services the person's connection lets Mavo read ("calendar", "gmail"...)."""
        pool = getattr(self.runtime, "google_pool", None)
        if pool is None:
            return set()
        try:
            status = pool.auth.status()
        except Exception:
            logger.warning("Could not read the Google connection", exc_info=True)
            return set()
        if not status.get("signed_in"):
            return set()
        return {c.split(":")[0] for c in status.get("capabilities", []) if c.endswith(":read")}

    def agent_brief(self, plan, step, inputs, calls: int = 4) -> str:
        return (
            "ONE-TIME TASK. Do it now, once; this is not a recurring job.\n"
            f"Goal of the whole task: {plan.deliver}\nYour step: {step.instructions}\n"
            + (f"Sites you will use: {', '.join(step.sites)}\n" if step.sites else "")
            + (self.inputs_block(inputs) + "\n" if inputs else "")
            + (
                "The person's own "
                + ", ".join(GOOGLE_NAMES.get(g, g) for g in step.google)
                + " are given to you below in your observation (read-only, already loaded: you "
                "need no action to read them). Use what is there; say plainly when something is "
                "empty.\n"
                if step.google
                else ""
            )
            + f"You have {calls} steps in all. "
            + (
                "Your tools are the OPEN DATA lookups: ask for what you need in your FIRST "
                "step, then answer from the results. "
                if step.open_data and not step.actions
                else "Open a page and read it in ONE step (browser.open and browser.read "
                "together); do not spend steps on side details. "
            )
            + "Give the final answer by your last step, whatever you managed to find. "
            + (BUILDING if any(op in BUILDS for op in step.actions) else "")
            + "If a later step will check your work, add a line `EVIDENCE:` after your answer and "
            "under it the exact lines you relied on: the person never sees the evidence. "
            "When you have finished, put ONLY what the person needs in summary, as plain short "
            "text with no commentary or steps: " + plan.deliver + " Ask the person only when a "
            "detail that DECIDES the answer is missing and you cannot give a useful answer "
            "without it (where from, a date, which account): ask in question at once, before "
            "searching; they answer on the task card and you carry on. When you can answer most "
            "of it, answer, and say in one line what was missing (a plan without the weather is "
            "still a plan): never stop for a nice-to-have. "
            "If you cannot finish (a site needs a sign-in, for example), say exactly why in "
            "question."
        )[:3900]

    def make_agent(self, run_id, plan, step, inputs, route, calls) -> str:
        template = CREW_TEMPLATES_BY_ID["operator"]
        with self.factory() as session:
            name = f"One-time {run_id[:6]} {step.id}: {step.title}"[:80]
            crew = CrewInstance(
                template_id=template.id,
                name=name,
                responsibilities=self.agent_brief(plan, step, inputs, calls),
                autonomy_mode="reactive",
                status="active",
                provider=route.provider,
                model=route.model,
                route_id=route.id,
                max_model_calls=calls,
                review_interval_minutes=30,
                daily_call_limit=calls + 2,
                reminder_text="",
                trigger_type="manual",
                event_cursor=session.scalar(
                    select(ResourceEvent.id).order_by(ResourceEvent.id.desc())
                )
                or 0,
                resources=[],
                configuration_plan={},
                # Read-only, and only what the person's Google connection already allows: the
                # agent is shown that data, as a Crew with the same access is.
                permissions=[
                    *template.default_permissions,
                    *(f"google:{g}:read" for g in step.google if g in self.google_readable()),
                ],
                triggers=[],
                hidden=True,
                is_running=True,
                runtime_state="waiting",
                next_run_at=utcnow(),
            )
            session.add(crew)
            session.flush()
            self.runtime.actions.rules.inherit_requested(session, crew, step.actions)
            session.commit()
            return crew.id

    def remove_agent(self, session, crew) -> None:
        self.runtime.cancel(crew.id)
        for policy in session.scalars(
            select(WorkspaceSetting).where(WorkspaceSetting.key.startswith(f"action:{crew.id}:"))
        ).all():
            session.delete(policy)
        session.delete(crew)

    async def agent(self, run_id, plan, step, inputs) -> str:
        route = self.route()
        problem = self.access_problems(step)
        if problem:
            raise Blocked(problem)
        calls = min(step.calls, self.remaining(run_id))
        if calls < 1:
            raise OverBudget()
        if step.google:
            # Their day: today's weather where they are comes from Mavo (their saved location,
            # if they allowed Weather), not from the model asking where they live.
            inputs = {**inputs, "weather_where_you_are": await self.local_weather()}
        # One browser: steps that need it take turns instead of fighting over it.
        needs_browser = any(op.startswith("browser.") for op in step.actions)
        async with self.browser_turn if needs_browser else contextlib.nullcontext():
            crew_id = self.make_agent(run_id, plan, step, inputs, route, calls)
            try:
                return await self.drive(run_id, plan, step, crew_id, calls)
            finally:
                self.settle(run_id, crew_id)

    async def local_weather(self) -> str:
        """Today's forecast at the person's saved location, without its coordinates; or why there
        is none, so the agent skips it instead of stopping to ask (a "plan my day" stopped for 15
        minutes asking for the person's city)."""
        from mavo.resource_access import read_access
        from mavo.schemas import WeatherLocation

        skip = "No weather: {}. Leave weather out and say so in one short line; do not ask."
        with self.factory() as session:
            allowed = read_access(session).weather
            saved = session.get(WorkspaceSetting, "weather-location")
            place = dict(saved.value) if saved else None
        adapter = getattr(self.runtime, "weather", None)
        if not allowed or adapter is None:
            return skip.format("Weather is off in Mavo's Resources")
        if not place:
            return skip.format("no location is set in Mavo's Resources")
        try:
            snapshot = (
                await adapter.read_location(WeatherLocation.model_validate(place))
            ).model_dump(mode="json")
        except Exception as exc:
            logger.warning("Weather for the task failed: %s", exc)
            return skip.format("the forecast could not be fetched")

        def without_position(value):
            if isinstance(value, dict):
                return {
                    k: without_position(v)
                    for k, v in value.items()
                    if k not in {"latitude", "longitude", "accuracy_m", "id"}
                }
            if isinstance(value, list):
                return [without_position(v) for v in value]
            return value

        return json.dumps(without_position(snapshot), ensure_ascii=False)[:2500]

    def settle(self, run_id: str, crew_id: str) -> None:
        """Charge what the agent used to the task, then delete it."""
        with self.factory() as session:
            used = sum(
                r.model_calls
                for r in session.scalars(select(CrewRun).where(CrewRun.crew_id == crew_id))
            )
            run = session.get(TaskRun, run_id)
            if run:
                run.used_calls = min(run.budget_calls + 10, run.used_calls + used)
            crew = session.get(CrewInstance, crew_id)
            if crew:
                self.remove_agent(session, crew)
            session.commit()

    async def crew(self, run_id, plan, step, inputs) -> str:
        with self.factory() as session:
            crew = session.get(CrewInstance, step.crew_id)
            if not crew or crew.hidden or crew.status == "draft":
                raise RuntimeError("That Crew no longer exists.")
            calls = min(crew.max_model_calls, max(1, self.remaining(run_id)))
            instruction = (
                f"One-time request from a task. Goal: {plan.deliver}\n{step.instructions}\n"
                + self.inputs_block(inputs)
            )[:3900]
            self.runtime.planner.queue_reply(session, crew, instruction)
            session.commit()
        try:
            return await self.drive(run_id, plan, step, step.crew_id, calls)
        finally:
            with self.factory() as session:
                used = sum(
                    r.model_calls
                    for r in session.scalars(
                        select(CrewRun).where(
                            CrewRun.crew_id == step.crew_id,
                            CrewRun.created_at >= self.began(run_id),
                        )
                    )
                )
                run = session.get(TaskRun, run_id)
                if run:
                    run.used_calls = min(run.budget_calls + 10, run.used_calls + used)
                session.commit()

    def began(self, run_id):
        with self.factory() as session:
            return session.get(TaskRun, run_id).created_at

    def crew_state(self, crew_id: str):
        with self.factory() as session:
            crew = session.get(CrewInstance, crew_id)
            if not crew:
                return None
            soon = crew.next_run_at is not None and crew.next_run_at.replace(
                tzinfo=crew.next_run_at.tzinfo or utcnow().tzinfo
            ) <= utcnow() + timedelta(seconds=5)
            return SimpleNamespace(
                state=crew.runtime_state,
                question=(crew.observation or {}).get("question"),
                cut_short=bool((crew.observation or {}).get("cut_short")),
                soon=soon,
                error=crew.last_runtime_error,
                running=crew.is_running,
            )

    def last_answer(self, crew_id: str, since) -> str:
        with self.factory() as session:
            run = session.scalar(
                select(CrewRun)
                .where(
                    CrewRun.crew_id == crew_id,
                    CrewRun.status == "completed",
                    CrewRun.created_at >= since,
                )
                .order_by(CrewRun.created_at.desc())
            )
            return clean_answer(run.result or "") if run else ""

    async def drive(self, run_id, plan, step, crew_id, limit) -> str:
        """Let the worker run the Crew step by step until it has answered or needs the person.

        Mavo has one browser. When another Crew holds it the worker parks this one ("waiting for
        the browser") and tries again shortly: that is waiting, never an answer, so it is waited
        out."""
        since = utcnow() - timedelta(seconds=2)
        worked, waited = 0, 0.0
        challenged = False
        while True:
            if self.status_of(run_id) != "running":
                raise asyncio.CancelledError()
            seen = self.crew_state(crew_id)
            if seen is None or not seen.running:
                raise RuntimeError("The agent was stopped.")
            if seen.state in {"working", "waiting_browser"}:
                if waited >= BUSY_WAIT:
                    raise Blocked("Mavo's browser stayed busy with another job. Try again shortly.")
                if seen.state == "waiting_browser":
                    self.make_due(crew_id)
                await asyncio.sleep(1.5)
                waited += 1.5
                continue
            if seen.state == "waiting" and seen.soon:
                if worked >= limit + 3:
                    raise RuntimeError("The agent ran out of steps before it finished.")
                self.make_due(crew_id)
                await self.runtime.work(crew_id)
                worked += 1
                continue
            if seen.state == "budget_paused":
                raise Blocked("This agent's daily model-call budget is used up.")
            if seen.state == "error":
                raise RuntimeError(seen.error or "The agent stopped with an error.")
            if seen.state == "needs_input" and (seen.question or "").startswith(
                "Waiting for your OK"
            ):
                await self.wait_for_approval(run_id, plan, step, crew_id, seen.question)
                continue
            if seen.state == "needs_input" and seen.question:
                question = str(seen.question)
                if not challenged and (url := await self.false_sign_in(question)):
                    challenged = True  # once: after that the agent's word stands
                    self.nudge(crew_id, url)
                    continue
                if (
                    SIGN_IN.search(question)
                    or SECRET.search(question)
                    or FOR_YOU_TO_DO.search(question)
                ):
                    raise Blocked(self.with_sign_in_help(question[:400], step))
                # Anything else the agent asks, the person answers on the task card, and the
                # same agent carries on from there.
                self.hand_over(crew_id, await self.ask_person(run_id, plan, step, question))
                continue
            if seen.cut_short:
                # Its last words are progress ("reading the page again"), never the answer.
                left_at = self.last_answer(crew_id, since).splitlines()
                raise RuntimeError(
                    "The agent ran out of steps before it finished"
                    + (f" (it was at: {left_at[0][:140]})." if left_at else ".")
                )
            answer = self.last_answer(crew_id, since)
            if answer:
                return answer
            raise RuntimeError("The agent finished without an answer.")

    async def false_sign_in(self, question: str) -> str | None:
        """An agent that says it is stopped at a sign-in is sometimes going by the first screen it
        saw (Microsoft's "Pick an account") and not the page it is on now. Mavo looks at the page
        itself, with no model: when it is an ordinary page, this returns its address."""
        from mavo.desktop_actions import AUTH_HOP

        if not SIGN_IN.search(question):
            return None
        desktop = self.runtime.actions.desktop
        page = desktop.page
        try:
            if page is None or page.is_closed():
                return None
            address = urlsplit(page.url)
            if (
                AUTH_HOP.search(address.path)
                or re.match(r"(login|signin|accounts?|auth|sso)\.", address.hostname or "")
                or await desktop.sign_in_wall()
            ):
                return None
            return page.url
        except Exception:
            return None

    def nudge(self, crew_id: str, url: str) -> None:
        with self.factory() as session:
            crew = session.get(CrewInstance, crew_id)
            if crew:
                crew.daily_call_limit += 2  # the turn it spent on the wrong claim is given back
                self.runtime.planner.queue_reply(
                    session,
                    crew,
                    f"Mavo checked the browser: it is now on {url[:200]}, which is not a sign-in "
                    "page, so you are signed in. Read that page and carry on with the task.",
                )
                session.commit()

    async def ask_person(self, run_id, plan, step, question: str, choices=None) -> str:
        """An agent asked something only the person can answer. The task waits here, with the
        question on its card (and buttons, when the answer is one of a few choices); the agent
        and its page stay as they are, and what the person types is passed on as typed."""
        waiting = asyncio.get_running_loop().create_future()
        key = (run_id, step.id)
        self.asking[key] = waiting
        self.change(
            run_id, step.id, ask=question[:400], asked_at=utcnow().isoformat(), choices=choices
        )
        self.alert(
            run_id,
            plan.title,
            f"Needs your answer. {question[:300]}",
            f"ask:{step.id}:{question[:40]}",
            step_id=step.id,
        )
        try:
            return await asyncio.wait_for(waiting, APPROVAL_WAIT)
        except TimeoutError:
            raise Blocked(
                f"{question[:400]} Nobody answered, so it stopped. Press Try again."
            ) from None
        finally:
            self.asking.pop(key, None)
            self.change(run_id, step.id, ask=None, choices=None)
            self.clear_alert(run_id, step.id)

    def answer(self, run_id: str, text: str, step_id: str | None = None) -> bool:
        """The person's answer to a step that asked. False when no step of this task is asking."""
        for (run, step), waiting in list(self.asking.items()):
            if run == run_id and step_id in (None, step) and not waiting.done():
                waiting.set_result(text)
                return True
        return False

    def hand_over(self, crew_id: str, answer: str) -> None:
        """Give the agent the answer, as it would get a reply in a chat, and let it carry on."""
        with self.factory() as session:
            crew = session.get(CrewInstance, crew_id)
            if not crew:
                return
            if crew.hidden:
                crew.daily_call_limit += 2  # the turn that asked is given back
            self.runtime.planner.queue_reply(session, crew, answer)
            session.commit()

    @staticmethod
    def with_sign_in_help(question: str, step: TaskStep | None = None) -> str:
        """An agent that met a sign-in says so in its own words; Mavo adds where to sign in and
        what to press afterwards. A password or code is typed in Mavo's browser, not to a model."""
        where = f" at https://{step.sites[0]}" if step is not None and step.sites else ""
        if SIGN_IN.search(question):
            return question.rstrip() + (
                f" Sign in{where} with the Sign in button on the task card, then press Try again."
            )
        if SECRET.search(question):
            return question.rstrip() + (
                " A password or code is never sent to the agent's model: type it in Mavo's "
                f"browser{where} (the Sign in button on the task card), then press Try again."
            )
        return question

    def make_due(self, crew_id: str) -> None:
        with self.factory() as session:
            crew = session.get(CrewInstance, crew_id)
            if crew:
                crew.runtime_state = "waiting"
                crew.next_run_at = utcnow() - timedelta(seconds=5)
                session.commit()

    async def wait_for_approval(self, run_id, plan, step, crew_id, question) -> None:
        self.alert(
            run_id, plan.title, f"Needs your OK. {question}", f"ok:{step.id}:{question[:30]}"
        )
        deadline = asyncio.get_running_loop().time() + APPROVAL_WAIT
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(2)
            if self.status_of(run_id) != "running":
                raise asyncio.CancelledError()
            seen = self.crew_state(crew_id)
            if seen is None or seen.question != question or seen.state != "needs_input":
                return
        raise Blocked(question)

    # ---- the desktop alert -------------------------------------------------------------------

    def owner(self, session):
        owner = session.scalar(
            select(CrewInstance).where(
                CrewInstance.hidden.is_(True), CrewInstance.name == OWNER_NAME
            )
        )
        if owner:
            if "notifications:show" not in owner.permissions:
                owner.permissions = [*owner.permissions, "notifications:show"]
            owner.is_running, owner.status = True, "active"
            return owner
        route = next(iter(self.runtime.planner.agent_routes(session)), None)
        template = CREW_TEMPLATES_BY_ID["operator"]
        owner = CrewInstance(
            template_id=template.id,
            name=OWNER_NAME,
            responsibilities="Carries the desktop alerts of one-time tasks. It never runs.",
            autonomy_mode="reactive",
            status="active",
            provider=route.provider if route else "ollama",
            model=route.model if route else "none",
            route_id=route.id if route else None,
            max_model_calls=1,
            review_interval_minutes=30,
            daily_call_limit=1,
            reminder_text="",
            trigger_type="manual",
            event_cursor=0,
            resources=[],
            configuration_plan={},
            permissions=[*template.default_permissions, "notifications:show"],
            triggers=[],
            hidden=True,
            is_running=True,
            runtime_state="needs_input",
        )
        session.add(owner)
        session.flush()
        return owner

    def alert(
        self, run_id: str, title: str, message: str, kind: str, step_id: str | None = None
    ) -> None:
        """Put a message on the person's desktop. Deterministic: this is Mavo, not a model. A
        question carries its step, so it is answered from the desktop too."""
        try:
            with self.factory() as session:
                owner = self.owner(session)
                key = hashlib.sha1(f"task:{run_id}:{kind}".encode()).hexdigest()
                if session.scalar(
                    select(ReminderEvent.id).where(ReminderEvent.deduplication_key == key)
                ):
                    return
                now = utcnow()
                session.add(
                    ReminderEvent(
                        crew_id=owner.id,
                        crew_name=title[:80],
                        kind="crew_update",
                        message=message[:1800],
                        scheduled_for=now,
                        local_date=now.astimezone().date().isoformat(),
                        deduplication_key=key,
                        status="pending",
                        task_run_id=run_id,
                        task_step_id=step_id,
                    )
                )
                session.commit()
        except Exception:
            logger.warning("Could not show the task alert", exc_info=True)

    def clear_alert(self, run_id: str, step_id: str) -> None:
        """A question that has been answered (here or on the task card) leaves the desktop."""
        try:
            with self.factory() as session:
                for event in session.scalars(
                    select(ReminderEvent).where(
                        ReminderEvent.task_run_id == run_id,
                        ReminderEvent.task_step_id == step_id,
                        ReminderEvent.status != "dismissed",
                    )
                ):
                    event.status, event.dismissed_at = "dismissed", utcnow()
                session.commit()
        except Exception:
            logger.warning("Could not clear the task alert", exc_info=True)


class StartTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan: TaskPlan
    request: str = ""


class AnswerTask(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    answer: str = Field(min_length=1, max_length=2000)
    step_id: str | None = None


def build_tasks_api(runner: TaskRunner):
    router = APIRouter()

    @router.get("/tasks")
    def listing(limit: int = 20, conversation_id: str | None = None):
        with runner.factory() as session:
            query = select(TaskRun).order_by(TaskRun.created_at.desc())
            if conversation_id:
                query = query.where(TaskRun.conversation_id == conversation_id)
            rows = session.scalars(query.limit(max(1, min(limit, 50)))).all()
            return [runner.view(r) for r in rows]

    @router.get("/tasks/{run_id}")
    def detail(run_id: str):
        with runner.factory() as session:
            run = session.get(TaskRun, run_id)
            if not run:
                raise HTTPException(404, "Task not found")
            return runner.view(run)

    @router.post("/tasks", status_code=201)
    async def begin(payload: StartTask):
        run_id = runner.start(payload.plan, payload.request)
        return detail(run_id)

    @router.post("/tasks/{run_id}/retry", status_code=201)
    async def again(run_id: str):
        new_id = await runner.retry(run_id)
        if not new_id:
            raise HTTPException(409, "That task is still running, or was not found")
        return detail(new_id)

    @router.post("/tasks/{run_id}/answer", status_code=202)
    async def answer(run_id: str, payload: AnswerTask):
        if not runner.answer(run_id, payload.answer, payload.step_id):
            raise HTTPException(409, "That task is not waiting for an answer")
        return detail(run_id)

    @router.post("/tasks/{run_id}/stop")
    async def stop(run_id: str):
        if not runner.stop(run_id):
            raise HTTPException(409, "That task is not running")
        return detail(run_id)

    return router
