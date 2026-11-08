"""Saved automations: a script run the person has approved, kept to run again with no model.

How this stays in the person's hands:
- Only the person creates one, as an extra choice when they approve a script run: they are
  looking at the exact scripts. An agent cannot create one; it can only say a run is worth saving.
- It is tied to the hash of those scripts (and the tool, window flag and arguments). If any of
  that changes it does not run: it stops and says so, and the person approves the new scripts to
  save it again.
- It runs only while Actions are not paused and "Run a script" is on in their default Actions,
  through the same action pipeline (so it appears in Activity), with the same limits on time,
  output and environment as any script run.
- It runs no model: it costs nothing but the script's own work.
A task can use one as a step, and it can run on a schedule or on demand.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException
from sqlalchemy import select, update

from mavo.automation_spec import AutomationSave
from mavo.database import ActionRequest, WorkspaceSetting, utcnow
from mavo.projects import slug_of

logger = logging.getLogger("mavo.automations")

KEY = "automation:"
CHECK_EVERY = 20  # seconds between looks at what is due
RUN_FIELDS = ("project", "tool", "script", "file", "show", "args")


class Automations:
    def __init__(self, runtime):
        self.runtime = runtime
        self.factory = runtime.factory
        self.running: set[str] = set()
        self.last_check = 0.0

    # ---- keeping them --------------------------------------------------------------------------

    def save(self, session, action: ActionRequest, spec: AutomationSave, sha256: str) -> dict:
        """Called when a script run is approved, with the hash of the scripts the person read."""
        slug = slug_of(spec.name)
        value = {
            "name": spec.name,
            **{k: action.arguments[k] for k in RUN_FIELDS if k in action.arguments},
            "sha256": sha256,
            "every_minutes": spec.every_minutes,
            "notify": spec.notify,
            "approved_at": utcnow().isoformat(),
            "last_run_at": None,
            "last_status": None,
            "last_output": "",
        }
        row = session.get(WorkspaceSetting, KEY + slug)
        if row:
            row.value = value
        else:
            session.add(WorkspaceSetting(key=KEY + slug, value=value))
        return self.view(value)

    @staticmethod
    def view(value: dict) -> dict:
        return {
            "name": value["name"],
            "slug": slug_of(value["name"]),
            "project": value.get("project"),
            "tool": value.get("tool"),
            "script": value.get("script"),
            "file": value.get("file"),
            "show": bool(value.get("show")),
            "args": value.get("args", []),
            "every_minutes": value.get("every_minutes"),
            "notify": value.get("notify", "failure"),
            "approved_at": value.get("approved_at"),
            "last_run_at": value.get("last_run_at"),
            "last_status": value.get("last_status"),
            "last_output": value.get("last_output", ""),
        }

    def listing(self) -> list[dict]:
        with self.factory() as session:
            rows = session.scalars(
                select(WorkspaceSetting).where(WorkspaceSetting.key.startswith(KEY))
            ).all()
            return sorted((self.view(r.value) for r in rows), key=lambda v: v["name"].lower())

    def remove(self, name: str) -> bool:
        with self.factory() as session:
            row = session.get(WorkspaceSetting, KEY + slug_of(name))
            if not row:
                return False
            session.delete(row)
            session.commit()
            return True

    def record(self, slug: str, **changes) -> None:
        with self.factory() as session:
            row = session.get(WorkspaceSetting, KEY + slug)
            if row:
                row.value = {**row.value, **changes}
                session.commit()

    # ---- running one ---------------------------------------------------------------------------

    async def run(self, name: str, source: str = "manual") -> dict:
        """Run it now. Returns {status: ok|failed|changed|blocked, ...}; never raises for those."""
        from mavo.google_actions import ActionDraft, ActionPolicyError

        slug = slug_of(name)
        with self.factory() as session:
            row = session.get(WorkspaceSetting, KEY + slug)
            if not row:
                raise KeyError(name)
            spec = dict(row.value)
            rules = self.runtime.actions.rules
            rule = self.runtime.actions.rule_for(session, "project.run")
            paused = rules.paused(session)
        title = spec["name"]
        if slug in self.running:
            return {"status": "blocked", "problem": "It is already running."}
        self.running.add(slug)
        started = utcnow()
        self.record(slug, last_run_at=started.isoformat())
        try:
            args = {k: spec[k] for k in RUN_FIELDS if k in spec and spec[k] is not None}
            problem, status = None, "blocked"
            if paused:
                problem = "Actions are paused. Resume them in Actions."
            elif not rule["enabled"]:
                problem = "Turn on Run a script in Actions (Defaults) so automations can run."
            else:
                try:
                    current = self.runtime.actions.desktop.projects.binding(args, rule)
                except ValueError as exc:
                    problem = str(exc)
                else:
                    if current != spec["sha256"]:
                        status = "changed"
                        problem = (
                            "The scripts changed since you approved this automation, so it did "
                            "not run. Run the script again and approve it to save it again."
                        )
            if problem:
                self.finish(slug, title, status, problem, "always", started)
                return {"status": status, "problem": problem}
            try:
                action = self.runtime.actions.propose(
                    ActionDraft(operation="project.run", arguments=args),
                    None,
                    None,
                    f"automation:{slug}:{started.isoformat()}",
                )
            except ActionPolicyError as exc:
                self.finish(slug, title, "blocked", str(exc), "always", started)
                return {"status": "blocked", "problem": str(exc)}
            with self.factory() as session:
                session.execute(
                    update(ActionRequest)
                    .where(ActionRequest.id == action["id"])
                    .values(approved_by="user")  # the approval was given when it was saved
                )
                session.commit()
            try:
                outcome = await self.runtime.actions.execute(action["id"])
            except ActionPolicyError as exc:
                self.finish(slug, title, "blocked", str(exc), "always", started)
                return {"status": "blocked", "problem": str(exc)}
            result = outcome.get("result") or {}
            ok = (
                outcome["status"] == "completed"
                and not result.get("timed_out")
                and result.get("exit_code", 0) == 0
            )
            output = str(result.get("output") or outcome.get("error") or "").strip()
            status = "ok" if ok else "failed"
            self.finish(slug, title, status, output, spec.get("notify", "failure"), started)
            return {
                "status": status,
                "output": output,
                "exit_code": result.get("exit_code"),
                "changed_files": result.get("changed_files", []),
            }
        finally:
            self.running.discard(slug)

    def finish(self, slug, title, status, text, notify, started) -> None:
        self.record(slug, last_status=status, last_output=text[-1500:])
        # Something the person must do is always told; otherwise as they chose.
        if status in {"changed", "blocked"}:
            key = hashlib.sha1(f"{status}:{text}".encode()).hexdigest()[:10]
            self.runtime.task_runner.alert(f"automation:{slug}", title, text, key)
        elif notify == "always" or (notify == "failure" and status != "ok"):
            message = (text or "Done.")[-600:] if status == "ok" else f"Failed. {text[-500:]}"
            self.runtime.task_runner.alert(
                f"automation:{slug}", title, message, f"run:{started.isoformat()}"
            )
        logger.info("Automation %s: %s", slug, status)

    # ---- on a schedule -------------------------------------------------------------------------

    def due(self, now: datetime | None = None) -> list[str]:
        now = now or utcnow()
        names = []
        for item in self.listing():
            every = item["every_minutes"]
            if not every or item["last_status"] in {"changed"}:
                continue  # not scheduled, or waiting for the person to approve it again
            last = item["last_run_at"]
            stamp = datetime.fromisoformat(last) if last else None
            if stamp is not None and stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=UTC)
            if stamp is None or now - stamp >= timedelta(minutes=every):
                names.append(item["name"])
        return names

    async def tick(self) -> None:
        if time.monotonic() - self.last_check < CHECK_EVERY:
            return
        self.last_check = time.monotonic()
        for name in self.due():
            try:
                await self.run(name, "schedule")
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("Scheduled automation %s failed", name, exc_info=True)


def build_automations_api(automations: Automations):
    router = APIRouter()

    @router.get("/automations")
    def listing():
        return automations.listing()

    @router.post("/automations/{name}/run")
    async def run_now(name: str):
        try:
            return await automations.run(name, "manual")
        except KeyError:
            raise HTTPException(404, "There is no such automation") from None

    @router.delete("/automations/{name}", status_code=204)
    def remove(name: str):
        if not automations.remove(name):
            raise HTTPException(404, "There is no such automation")

    return router
