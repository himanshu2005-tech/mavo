"""User-owned action policy. Models cannot create or change these settings."""

import time
from pathlib import Path
from types import SimpleNamespace
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from mavo.connectors.catalog import ACTIONS, action_grant, provider_for
from mavo.database import ActionRequest, CrewInstance, WorkspaceSetting, utcnow
from mavo.projects import NEEDS_APPROVAL

CATALOG = {
    **{op: (entry[2], entry[3], entry[4]) for op, entry in ACTIONS.items()},
    "gmail.draft": ("Mail", "Create email draft", False),
    "gmail.send": ("Mail", "Send & reply", True),
    "gmail.archive": ("Mail", "Archive", False),
    "gmail.mark_read": ("Mail", "Mark read", False),
    "gmail.trash": ("Mail", "Move to trash", True),
    "calendar.create": ("Calendar", "Create event", False),
    "calendar.update": ("Calendar", "Edit event", False),
    "calendar.delete": ("Calendar", "Delete event", True),
    "tasks.create": ("Tasks", "Create task", False),
    "tasks.update": ("Tasks", "Edit task", False),
    "tasks.complete": ("Tasks", "Complete task", False),
    "tasks.delete": ("Tasks", "Delete task", True),
    "terminal.run": ("Terminal", "Run a command", False),
    "browser.open": ("Browser", "Open page", False),
    "browser.search": ("Browser", "Search the web", False),
    "browser.navigate": ("Browser", "Find a page", False),
    "browser.read": ("Browser", "Read page", False),
    "browser.click": ("Browser", "Click element", True),
    "browser.double_click": ("Browser", "Double-click element", True),
    "browser.hover": ("Browser", "Hover over element", False),
    "browser.drag": ("Browser", "Drag and drop", True),
    "browser.fill": ("Browser", "Fill field", True),
    "browser.select": ("Browser", "Choose an option", True),
    "browser.press": ("Browser", "Press a key", True),
    "browser.scroll": ("Browser", "Scroll", False),
    "browser.wait": ("Browser", "Wait for the page", False),
    "browser.close": ("Browser", "Close browser", False),
    "windows.list": ("Windows", "Find windows", False),
    "windows.focus": ("Windows", "Focus window", False),
    "windows.minimize": ("Windows", "Minimize window", False),
    "windows.type": ("Windows", "Type text", True),
    "windows.key": ("Windows", "Press key", True),
    "windows.sleep": ("Windows", "Put the computer to sleep", True),
    "windows.shutdown": ("Windows", "Shut down the computer (60-second warning)", True),
    "windows.lock": ("Windows", "Lock the computer", False),
    "windows.volume": ("Windows", "Set the volume", False),
    "windows.media": ("Windows", "Play, pause or skip music", False),
    "windows.speak": ("Windows", "Say something aloud", False),
    "notes.find": ("Notes", "Search notes", False),
    "notes.read": ("Notes", "Read a note", False),
    "notes.write": ("Notes", "Write or add to a note (never overwrites)", False),
    "files.list": ("Files", "List a folder", False),
    "files.move": ("Files", "Move or rename files (never overwrites)", True),
    "files.backup": ("Files", "Back up a folder as a zip", False),
    "helper.ask": ("Agents", "Ask a one-time helper agent", False),
    "project.create": ("Projects", "Create a project folder", False),
    "project.write": ("Projects", "Write a file in a project", False),
    "project.read": ("Projects", "Read a project's files", False),
    "project.run": ("Projects", "Run a script (asks first)", True),
}
LOCAL_SERVICES = {"terminal", "browser", "windows", "project", "helper", "notes", "files"}
TRUST_PREFIX = "project:"
RUN_TOOLS = {"python.exe", "blender.exe"}


def project_trusted(session, name):
    """Has the person marked this project trusted, so its runs no longer ask each time?"""
    from mavo.projects import slug_of

    try:
        row = session.get(WorkspaceSetting, TRUST_PREFIX + slug_of(str(name)))
    except ValueError:
        return False
    return bool(row and row.value.get("trusted"))


BLOCKED_INPUT_APPS = {
    "cmd.exe",
    "powershell.exe",
    "pwsh.exe",
    "windowsterminal.exe",
    "conhost.exe",
    "openconsole.exe",
    "msedge.exe",
    "chrome.exe",
    "firefox.exe",
    "brave.exe",
    "regedit.exe",
    "mmc.exe",
    "wscript.exe",
    "cscript.exe",
    "rundll32.exe",
}


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    mode: Literal["restricted", "unrestricted"] = "restricted"
    folders: list[str] = Field(default_factory=list, max_length=8)
    domains: list[str] = Field(default_factory=list, max_length=20)
    apps: list[str] = Field(default_factory=list, max_length=20)
    recipients: list[str] = Field(default_factory=list, max_length=20)
    repositories: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("repositories")
    @classmethod
    def repository_scope(cls, values):
        import re

        if any(
            not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", v)
            or any(p in {".", ".."} for p in v.split("/"))
            for v in values
        ):
            raise ValueError("Use exact owner/repository names")
        return list(dict.fromkeys(values))

    @field_validator("folders")
    @classmethod
    def folders_exist(cls, values):
        if any(len(value) > 1024 for value in values):
            raise ValueError("Folder path is too long")
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))

    @field_validator("domains", "apps", "recipients")
    @classmethod
    def safe_names(cls, values, info):
        import ipaddress
        import re

        result = []
        for value in values:
            value = value.strip().lower()
            if info.field_name == "domains":
                if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", value):
                    raise ValueError("Use exact site hostnames without paths or wildcards")
                if "." not in value or value.endswith((".localhost", ".local", ".internal")):
                    raise ValueError("Local network sites are not supported")
                try:
                    ipaddress.ip_address(value)
                except ValueError:
                    pass
                else:
                    raise ValueError("Use public site hostnames, not IP addresses")
            elif info.field_name == "apps" and not re.fullmatch(r"[a-z0-9_. -]+\.exe", value):
                raise ValueError("Use application names such as notepad.exe")
            elif info.field_name == "recipients" and not re.fullmatch(
                r"[^\s@,;<>]+@[^\s@,;<>]+\.[^\s@,;<>]+", value
            ):
                raise ValueError("Use exact recipient email addresses")
            result.append(value)
        return list(dict.fromkeys(result))


class RuleChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: str
    crew_id: str | None = None
    rule: Rule

    @model_validator(mode="after")
    def valid_scope(self):
        if self.operation not in CATALOG:
            raise ValueError("Unknown action")
        service = self.operation.split(".")[0]
        if self.rule.enabled and self.rule.mode == "restricted":
            if service == "terminal" and not self.rule.folders:
                raise ValueError("Choose a folder for terminal commands")
            if service == "terminal":
                try:
                    paths = [Path(value).resolve(strict=True) for value in self.rule.folders]
                except OSError:
                    raise ValueError("Choose an existing folder") from None
                if any(
                    not Path(value).is_absolute() or not path.is_dir() or path.parent == path
                    for value, path in zip(self.rule.folders, paths, strict=True)
                ):
                    raise ValueError("Choose existing folders, not entire drives")
                self.rule.folders = [str(path) for path in paths]
            if service == "project":
                if self.rule.folders:
                    try:
                        base = Path(self.rule.folders[0]).resolve(strict=True)
                    except OSError:
                        raise ValueError("Choose an existing projects folder") from None
                    if not base.is_absolute() or not base.is_dir() or base.parent == base:
                        raise ValueError("Choose a projects folder, not an entire drive")
                    self.rule.folders = [str(base)]
                if self.operation == "project.run":
                    if not self.rule.apps:
                        raise ValueError("Choose which tools may run: python.exe or blender.exe")
                    if not set(self.rule.apps) <= RUN_TOOLS:
                        raise ValueError("Only python.exe and blender.exe can run scripts")
            if service == "browser" and not self.rule.domains:
                raise ValueError("Choose allowed browser sites")
            if service == "windows" and not self.rule.apps:
                raise ValueError("Choose allowed Windows apps")
            if self.operation in {
                "windows.type",
                "windows.key",
            } and BLOCKED_INPUT_APPS.intersection(self.rule.apps):
                raise ValueError("Use scoped Terminal and Browser actions for those applications")
            if (
                self.operation in {"gmail.send", "gmail.draft"}
                and self.rule.mode == "restricted"
                and not self.rule.recipients
            ):
                raise ValueError("Choose recipients before enabling automatic sends")
            if service == "github" and not self.rule.repositories:
                raise ValueError("Choose exact repositories")
        return self


class BulkRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["off", "limited", "unrestricted"]
    crew_id: str | None = None
    confirm: bool = False


class ActionRules:
    def __init__(self, factory):
        self.factory = factory

    @staticmethod
    def key(operation, crew_id=None):
        return "action:" + (crew_id or "core") + ":" + operation

    def read(self, session, operation, crew_id=None):
        saved = session.get(WorkspaceSetting, self.key(operation, crew_id))
        if saved:
            return saved.value
        return {
            "enabled": False,
            "mode": "restricted",
            "revision": "default",
            "folders": [],
            "domains": [],
            "apps": [],
            "recipients": [],
            "repositories": [],
        }

    def paused(self, session):
        row = session.get(WorkspaceSetting, "actions-paused")
        return bool(row and row.value.get("paused"))

    def inherit_requested(self, session, crew, operations):
        """Snapshot user-approved defaults; never overwrite an explicit Crew decision."""
        for operation in operations:
            key = self.key(operation, crew.id)
            if session.get(WorkspaceSetting, key):
                continue
            default = self.read(session, operation)
            if not default["enabled"]:
                continue
            rule = Rule.model_validate({k: default[k] for k in Rule.model_fields if k in default})
            session.add(
                WorkspaceSetting(
                    key=key,
                    value={
                        **rule.model_dump(),
                        "revision": str(uuid4()),
                        "origin": "saved-default",
                    },
                )
            )
            if operation.split(".")[0] in LOCAL_SERVICES:
                grant = "action:" + operation
                if grant not in crew.permissions:
                    crew.permissions = [*crew.permissions, grant]

    def check(self, session, action):
        rule = self.read(session, action.operation, action.crew_id)
        if self.paused(session):
            raise ValueError("Actions are paused. Resume them in Actions.")
        if not rule["enabled"]:
            raise ValueError("This action is disabled. Enable it in Actions.")
        from mavo.resource_pool import ceiling_error

        limit = ceiling_error(session, operation=action.operation)
        if limit:
            raise ValueError(limit)
        if (action.policy_revision or "default") != rule["revision"]:
            raise ValueError("Action access changed. Create a new request.")
        args = action.arguments
        if action.operation == "terminal.run" and rule["mode"] == "restricted":
            path = Path(args["folder"]).resolve(strict=True)
            if not any(path.is_relative_to(Path(folder)) for folder in rule["folders"]):
                raise ValueError("The terminal folder is outside the approved scope")
        if (
            action.operation in {"gmail.send", "gmail.draft"}
            and rule["mode"] == "restricted"
            and rule["recipients"]
            and args["to"].lower() not in rule["recipients"]
        ):
            raise ValueError("This recipient is outside the approved scope")
        if (
            action.operation == "project.run"
            and rule["mode"] == "restricted"
            and ({"python": "python.exe", "blender": "blender.exe"}[args["tool"]])
            not in rule["apps"]
        ):
            raise ValueError(f"{args['tool'].title()} is outside this action's allowed tools")
        if action.operation == "browser.search" and rule["mode"] == "restricted":
            from mavo.desktop_actions import SEARCH_HOST, host_allowed, search_url

            if not host_allowed(search_url(args["query"]), rule["domains"]):
                raise ValueError(f"Web search needs {SEARCH_HOST} in the allowed sites")
        if action.operation == "browser.open" and rule["mode"] == "restricted":
            from mavo.desktop_actions import host_allowed

            if not host_allowed(args["url"], rule["domains"]):
                raise ValueError("This site is outside the approved scope")
        if (
            action.operation.startswith("github.")
            and rule["mode"] == "restricted"
            and args["repository"] not in rule.get("repositories", [])
        ):
            raise ValueError("Repository is outside this action's allowed repositories")
        return rule

    def automatic(self, session, action):
        """May this action run now without anyone being asked? Running a script is the one action
        that waits: it needs the person's approval of these exact scripts, or a project they have
        marked trusted. Settings alone never allow it, even "without limits"."""
        rule = self.check(session, action)
        if not rule["enabled"]:
            return False
        if action.operation in NEEDS_APPROVAL:
            return action.approved_by == "user" or project_trusted(
                session, action.arguments.get("project")
            )
        return True


def needs_reason(crew) -> str:
    """Why an agent is waiting for the person, as one short line."""
    if crew.runtime_state == "needs_setup":
        return "Finish setting it up"
    text = (
        (crew.question if crew.runtime_state == "needs_input" else None)
        or crew.last_runtime_error
        or ""
    )
    line = next((part.strip() for part in text.splitlines() if part.strip()), "")
    if not line:
        return "Has a question" if crew.runtime_state == "needs_input" else "Stopped by a problem"
    return line if len(line) <= 90 else line[:87].rsplit(" ", 1)[0] + "…"


def build_policy_api(engine, runtime):
    router = APIRouter()
    from mavo.desktop_actions import BrowserOpen

    @router.post("/crew/{crew_id}/browser-session")
    async def open_sign_in(crew_id: str, payload: BrowserOpen):
        from mavo.crew_workers import stop_crew
        from mavo.google_actions import ActionDraft, ActionPolicyError

        with engine.factory() as session:
            crew = session.get(CrewInstance, crew_id)
            if not crew:
                raise HTTPException(404, "Crew not found")
            if crew.configuration_plan.get("questions"):
                raise HTTPException(409, "Finish the setup questions first.")
            rule = engine.rules.read(session, "browser.open", crew.id)
            if not rule["enabled"] or "action:browser.open" not in crew.permissions:
                raise HTTPException(403, "Save browser.open access for this Crew first.")
            if engine.rules.paused(session):
                raise HTTPException(409, "Resume Actions before opening Mavo's browser.")
            if engine.desktop.browser_owner not in {None, crew_id}:
                raise HTTPException(409, "Another Crew is using Mavo's browser. Try again shortly.")
            try:
                engine.rules.check(
                    session,
                    SimpleNamespace(
                        operation="browser.open",
                        crew_id=crew_id,
                        arguments=payload.model_dump(),
                        policy_revision=rule["revision"],
                    ),
                )
            except ValueError as exc:
                raise HTTPException(403, str(exc)) from exc
            # Opening a sign-in window explicitly pauses automated work, not its permissions.
            stop_crew(session, crew)
            runtime.cancel(crew.id)
            session.commit()
            generation = crew.runtime_generation
            is_draft = crew.status == "draft"
        engine.desktop.hold_for_sign_in(crew_id, generation)
        try:
            action = engine.propose(
                ActionDraft(operation="browser.open", arguments=payload.model_dump()),
                crew_id,
                generation,
            )
            outcome = await engine.maybe_execute(action["id"])
            if outcome["status"] != "completed":
                raise HTTPException(
                    409, "The browser did not open. Check Activity for its outcome."
                )
            return {
                "opened": True,
                "paused": True,
                "url": payload.url,
                "message": "Sign in in Mavo's separate browser, then "
                + ("Verify & add in setup. " if is_draft else "Resume Crew. ")
                + "The sign-in window stays open for up to 15 minutes.",
            }
        except ActionPolicyError as exc:
            engine.desktop.interactive_owner = None
            raise HTTPException(409, str(exc)) from exc
        except Exception:
            engine.desktop.interactive_owner = None
            raise

    @router.get("/browser")
    def browser_state():
        desktop = engine.desktop
        is_open = desktop.user_window_open()
        return {
            "engine": desktop.engine_options()["label"],
            "open": is_open,
            "busy": desktop.context is not None,
            "seconds_left": max(0, int(desktop.interactive_until - time.monotonic()))
            if is_open
            else 0,
        }

    @router.get("/browser/saved")
    async def browser_saved(host: str):
        """Does Mavo's browser hold a saved login for this site? A look at its cookie store (or,
        while a Crew has the browser open, a question to it): no page opens, so it answers at once.
        "saved" is null when it cannot be told right now."""
        import re

        host = host.strip().lower()
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", host):
            raise HTTPException(422, "That is not a site address.")
        return {"host": host, "saved": await engine.desktop.saved_login(host)}

    @router.post("/browser/sign-in")
    async def browser_sign_in(payload: BrowserOpen):
        """Open Mavo's browser as a normal window for the person to sign in, including with
        Google. Crew reuse the session afterwards, only on the sites each one allows."""
        with engine.factory() as session:
            if engine.rules.paused(session):
                raise HTTPException(409, "Resume Actions before opening Mavo's browser.")
        try:
            engine.desktop.open_user_window(payload.url)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"opened": True, "engine": engine.desktop.engine_options()["label"]}

    @router.post("/browser/close")
    async def browser_close():
        await engine.desktop.close_user_window()
        return {"open": engine.desktop.user_window_open()}

    @router.get("/crew/{crew_id}/browser-session")
    def sign_in_status(crew_id: str):
        with engine.factory() as session:
            crew = session.get(CrewInstance, crew_id)
            if not crew:
                raise HTTPException(404, "Crew not found")
            desktop = engine.desktop
            return {
                "open": desktop.browser_owner == crew_id
                and desktop.page is not None
                and not desktop.page.is_closed(),
                "signing_in": desktop.sign_in_held(crew),
            }

    @router.get("/action-policies/{operation}/crew")
    def assignments(operation: str):
        if operation not in CATALOG:
            raise HTTPException(404, "Unknown action")
        from sqlalchemy import select

        with engine.factory() as session:
            result = []
            for crew in session.scalars(
                select(CrewInstance)
                .where(CrewInstance.hidden.is_(False))
                .order_by(CrewInstance.name)
            ).all():
                if crew.status == "draft" and crew.configuration_plan.get("questions"):
                    continue
                grant = action_grant(operation)
                result.append(
                    {
                        "id": crew.id,
                        "name": crew.name,
                        "status": crew.status,
                        "responsibilities": crew.responsibilities,
                        "authorized": grant in crew.permissions,
                        "rule": engine.rules.read(session, operation, crew.id),
                    }
                )
            return result

    def available(operation):
        if provider_for(operation) == "local":
            return True
        if getattr(engine, "connectors", None):
            return engine.connectors.capability_available(operation)
        return action_grant(operation)[7:] in engine.auth.status().get("capabilities", [])

    @router.get("/action-policies")
    def listing(crew_id: str | None = None, include_unavailable: bool = False):
        with engine.factory() as session:
            crew = session.get(CrewInstance, crew_id) if crew_id else None
            if crew_id and not crew:
                raise HTTPException(404, "Crew not found")

            def authorized(operation):
                grant = action_grant(operation)
                return not crew or grant in crew.permissions

            return {
                "paused": engine.rules.paused(session),
                "actions": [
                    {
                        "operation": op,
                        "group": group,
                        "label": label,
                        "requires_review": risk,
                        "authorized": authorized(op),
                        "available": available(op),
                        "provider": provider_for(op),
                        "rule": engine.rules.read(session, op, crew_id),
                        "arguments_schema": engine.models[op].model_json_schema(),
                    }
                    for op, (group, label, risk) in CATALOG.items()
                    if include_unavailable or available(op)
                ],
            }

    def write_rule(session, change, crew):
        key = engine.rules.key(change.operation, change.crew_id)
        data = {**change.rule.model_dump(), "revision": str(uuid4())}
        row = session.get(WorkspaceSetting, key)
        if row:
            row.value = data
        else:
            session.add(WorkspaceSetting(key=key, value=data))
        if crew and change.operation.split(".")[0] in LOCAL_SERVICES:
            grant = "action:" + change.operation
            crew.permissions = [p for p in crew.permissions if p != grant] + (
                [grant] if data["enabled"] else []
            )
        return data

    def restart_crew(crew):
        """New access means new work: what the Crew was doing under the old settings is stopped."""
        crew.runtime_generation += 1
        runtime.cancel(crew.id)
        crew.runtime_state = "waiting" if crew.is_running else "stopped"
        crew.next_run_at = utcnow() if crew.is_running else None

    def owner_of(session, crew_id):
        crew = session.get(CrewInstance, crew_id) if crew_id else None
        if crew_id and not crew:
            raise HTTPException(404, "Crew not found")
        if crew and crew.status == "draft" and crew.configuration_plan.get("questions"):
            raise HTTPException(409, "Answer the setup questions before configuring access.")
        return crew

    @router.put("/action-policies")
    def save(change: RuleChange):
        with engine.factory() as session:
            crew = owner_of(session, change.crew_id)
            data = write_rule(session, change, crew)
            if crew:
                restart_crew(crew)
            session.commit()
            return data

    @router.put("/action-policies/bulk")
    def set_all(change: BulkRule):
        """Set every available action to one level at once: off, with limits, or without limits.
        An action that cannot be allowed with limits yet (no site, folder or app list chosen) is
        left as it was and reported, never half-set."""
        if change.mode == "unrestricted" and not change.confirm:
            raise HTTPException(409, "Confirm to allow every action without limits.")
        with engine.factory() as session:
            crew = owner_of(session, change.crew_id)
            changed, skipped = [], []
            for operation, (_, label, _risk) in CATALOG.items():
                if not available(operation):
                    continue
                existing = engine.rules.read(session, operation, change.crew_id)
                fields = {k: existing[k] for k in Rule.model_fields if k in existing}
                if change.mode == "off":
                    if not existing["enabled"]:
                        continue
                    fields["enabled"] = False
                elif change.mode == "limited":
                    if existing["enabled"] and existing["mode"] == "restricted":
                        continue
                    fields.update(enabled=True, mode="restricted")
                    if operation == "project.run" and not fields["apps"]:
                        fields["apps"] = sorted(RUN_TOOLS)
                else:
                    if existing["enabled"] and existing["mode"] == "unrestricted":
                        continue
                    fields = {
                        **{
                            k: []
                            for k in ("folders", "domains", "apps", "recipients", "repositories")
                        },
                        "enabled": True,
                        "mode": "unrestricted",
                    }
                try:
                    rule_change = RuleChange(
                        operation=operation, crew_id=change.crew_id, rule=Rule(**fields)
                    )
                except ValidationError as exc:
                    reason = exc.errors()[0]["msg"].removeprefix("Value error, ")
                    skipped.append({"operation": operation, "label": label, "reason": reason})
                    continue
                write_rule(session, rule_change, crew)
                changed.append({"operation": operation, "label": label})
            if crew and changed:
                restart_crew(crew)
            session.commit()
            return {"changed": changed, "skipped": skipped}

    @router.post("/actions/pause")
    async def pause():
        with engine.factory() as session:
            row = session.get(WorkspaceSetting, "actions-paused")
            if row:
                row.value = {"paused": True}
            else:
                session.add(WorkspaceSetting(key="actions-paused", value={"paused": True}))
            from sqlalchemy import select, update

            from mavo.crew_workers import stop_crew

            for crew in session.scalars(
                select(CrewInstance).where(CrewInstance.is_running.is_(True))
            ):
                stop_crew(session, crew)
                runtime.cancel(crew.id)
            session.execute(
                update(ActionRequest)
                .where(ActionRequest.status == "pending")
                .values(status="rejected", error="Paused by the user.")
            )
            session.commit()
        await engine.desktop.close()
        return {"paused": True}

    @router.post("/actions/resume")
    def resume():
        with engine.factory() as session:
            row = session.get(WorkspaceSetting, "actions-paused")
            if row:
                row.value = {"paused": False}
                session.commit()
        return {"paused": False}

    @router.get("/desktop-status")
    def status():
        from sqlalchemy import func, select

        with engine.factory() as session:
            # Only the person's own agents. Mavo's hidden ones (the one that carries one-time
            # tasks' alerts sits in needs_input on purpose so it never runs, and short-lived
            # helpers) are not something they can see or answer, so they are never counted.
            yours = CrewInstance.hidden.is_(False)
            waiting = session.scalars(
                select(CrewInstance)
                .where(
                    yours, CrewInstance.runtime_state.in_(["needs_setup", "needs_input", "error"])
                )
                .order_by(CrewInstance.name)
            ).all()
            return {
                "paused": engine.rules.paused(session),
                "running": session.scalar(
                    select(func.count())
                    .select_from(CrewInstance)
                    .where(yours, CrewInstance.is_running.is_(True))
                ),
                "pending": len(waiting),
                # Working right now (not just switched on): the desktop icon shows it moving.
                "working": session.scalars(
                    select(CrewInstance.name)
                    .where(
                        yours,
                        CrewInstance.is_running.is_(True),
                        CrewInstance.runtime_state == "working",
                    )
                    .order_by(CrewInstance.name)
                ).all()[:5],
                # Which agents, and in a few words why, so "needs you" can say what to do.
                "needs": [
                    {"id": crew.id, "name": crew.name, "reason": needs_reason(crew)}
                    for crew in waiting[:5]
                ],
            }

    return router
