from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from email.utils import parseaddr
from typing import Literal
from urllib.parse import quote

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)
from sqlalchemy import func, select, update

from mavo.action_rules import LOCAL_SERVICES, TRUST_PREFIX, project_trusted
from mavo.automation_spec import AutomationSave
from mavo.connectors.actions import MODELS as CONNECTOR_MODELS
from mavo.connectors.catalog import action_grant, provider_for
from mavo.connectors.hub import ConnectorRejected
from mavo.database import ActionRequest, CrewInstance, GoogleAccount, WorkspaceSetting, utcnow
from mavo.desktop_actions import DESKTOP_MODELS
from mavo.google_auth import GoogleConnectionError
from mavo.helpers import HelperAsk
from mavo.local_files import FILE_MODELS
from mavo.pc_controls import PC_MODELS
from mavo.projects import MARKER, NEEDS_APPROVAL, PROJECT_MODELS, slug_of

Operation = Literal[
    "gmail.send",
    "gmail.draft",
    "github.create_issue",
    "github.comment",
    "gmail.archive",
    "gmail.mark_read",
    "gmail.trash",
    "calendar.create",
    "calendar.update",
    "calendar.delete",
    "tasks.create",
    "tasks.update",
    "tasks.complete",
    "tasks.delete",
    "terminal.run",
    "browser.open",
    "browser.search",
    "browser.navigate",
    "browser.read",
    "browser.click",
    "browser.double_click",
    "browser.hover",
    "browser.drag",
    "browser.fill",
    "browser.select",
    "browser.press",
    "browser.scroll",
    "browser.wait",
    "browser.close",
    "windows.list",
    "windows.focus",
    "windows.minimize",
    "windows.type",
    "windows.key",
    "windows.sleep",
    "windows.shutdown",
    "windows.lock",
    "windows.volume",
    "windows.media",
    "windows.speak",
    "notes.find",
    "notes.read",
    "notes.write",
    "files.list",
    "files.move",
    "files.backup",
    "project.create",
    "project.write",
    "project.read",
    "project.run",
    "helper.ask",
]


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SendMail(Arguments):
    to: str = Field(min_length=3, max_length=254)
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=16000)
    thread_id: str | None = Field(default=None, max_length=255)
    in_reply_to: str | None = Field(default=None, max_length=512)

    @field_validator("to", "subject", "in_reply_to")
    @classmethod
    def no_header_injection(cls, value):
        if value is None:
            return value
        if any(c in value for c in ("\r", "\n", "\x00")):
            raise ValueError("Email headers must be a single line")
        return value

    @field_validator("to")
    @classmethod
    def one_mailbox(cls, value):
        _, address = parseaddr(value)
        if address != value or "@" not in address or any(c in value for c in " ,;<>"):
            raise ValueError("Enter one email address")
        return value


class MailId(Arguments):
    message_id: str = Field(min_length=1, max_length=255)


class EventCreate(Arguments):
    summary: str = Field(min_length=1, max_length=500)
    start: datetime
    end: datetime
    description: str = Field(default="", max_length=8000)

    @model_validator(mode="after")
    def bounded_time(self):
        if not self.start.tzinfo or not self.end.tzinfo or self.end <= self.start:
            raise ValueError("Use timezone-aware start/end times with end after start")
        return self


class ExistingItem(Arguments):
    item_id: str = Field(min_length=1, max_length=1024)
    etag: str = Field(min_length=1, max_length=255)

    @field_validator("etag")
    @classmethod
    def safe_etag(cls, value):
        if any(c in value for c in "\r\n"):
            raise ValueError("Invalid version")
        return value


class EventUpdate(EventCreate, ExistingItem):
    pass


class TaskCreate(Arguments):
    title: str = Field(min_length=1, max_length=500)
    notes: str = Field(default="", max_length=8000)
    due: date | None = None


class TaskUpdate(TaskCreate, ExistingItem):
    pass


MODELS = {
    **DESKTOP_MODELS,
    **PC_MODELS,
    **FILE_MODELS,
    **PROJECT_MODELS,
    "helper.ask": HelperAsk,
    **CONNECTOR_MODELS,
    "gmail.send": SendMail,
    "gmail.draft": SendMail,
    **{f"gmail.{v}": MailId for v in ("archive", "mark_read", "trash")},
    "calendar.create": EventCreate,
    "calendar.update": EventUpdate,
    "calendar.delete": ExistingItem,
    "tasks.create": TaskCreate,
    "tasks.update": TaskUpdate,
    "tasks.complete": ExistingItem,
    "tasks.delete": ExistingItem,
}


def required_capability(operation):
    if provider_for(operation) not in {"google", "local"}:
        return action_grant(operation).split(":", 1)[1]
    service, verb = operation.split(".")
    if service in LOCAL_SERVICES:
        return "action:" + operation
    return f"{service}:{'send' if verb == 'send' else 'modify' if service == 'gmail' else 'write'}"


class ActionDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Operation
    arguments: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_arguments(self, info: ValidationInfo):
        # The context carries files a reply gave beside its JSON (see ProjectWrite).
        self.arguments = (
            MODELS[self.operation]
            .model_validate(self.arguments, context=info.context)
            .model_dump(mode="json", exclude_unset=True)
        )
        return self


class ActionPolicyError(RuntimeError):
    pass


class ActionRejected(ActionPolicyError):
    pass


class ActionSlip(ActionPolicyError):
    """The agent asked for something that cannot work as written (a script that is not in the
    project, say). Not the person's to fix: it is told plainly and tries again."""


def view(action):
    return {
        key: getattr(action, key)
        for key in (
            "id",
            "crew_id",
            "crew_name",
            "source",
            "operation",
            "arguments",
            "status",
            "result",
            "error",
            "created_at",
            "finished_at",
            "target_summary",
            "approved_by",
        )
    }


class GoogleActions:
    def __init__(self, pool):
        self.pool, self.auth, self.factory = pool, pool.auth, pool.factory
        self.models = MODELS
        self.rules = self.desktop = None
        self.helper = None  # set by the worker runtime: asks a one-time helper agent
        self.automations = None  # saved script runs (see automations.py)
        self.background = set()

    def available(self, session, crew_id=None):
        crew = session.get(CrewInstance, crew_id) if crew_id else None
        result = {}
        if self.rules.paused(session):
            return result
        for operation, model in MODELS.items():
            local = operation.split(".")[0] in LOCAL_SERVICES
            grant = action_grant(operation)
            if not local:
                if getattr(self, "connectors", None):
                    if not self.connectors.capability_available(operation):
                        continue
                elif grant[7:] not in self.auth.status().get("capabilities", []):
                    continue
            rule = self.rules.read(session, operation, crew_id)
            if rule["enabled"] and (not crew or grant in crew.permissions):
                result[operation] = {"arguments": model.model_json_schema(), "scope": rule}
        return result

    def check_actor(self, session, action):
        if action.operation not in MODELS:
            raise ActionPolicyError("This action is no longer supported.")
        local = action.operation.split(".")[0] in LOCAL_SERVICES
        if not local:
            provider = provider_for(action.operation)
            subject = (
                self.connectors.subject(provider)
                if getattr(self, "connectors", None)
                else (
                    session.get(GoogleAccount, "google").subject
                    if session.get(GoogleAccount, "google")
                    else None
                )
            )
            if not subject or subject != action.account_subject:
                raise ActionPolicyError("The connected account changed. Create a new action.")
            if (
                provider != "google"
                and action.status in {None, "pending", "executing"}
                and not self.connectors.capability_available(action.operation)
            ):
                raise ActionPolicyError("The connected account no longer grants this action.")
            if provider == "github" and action.arguments.get(
                "repository"
            ) not in self.connectors.account("github").get("repositories", []):
                raise ActionPolicyError(
                    "Repository is outside the connection's selected repositories"
                )
        if self.rules:
            try:
                self.rules.check(session, action)
            except (ValueError, OSError) as exc:
                raise ActionPolicyError(str(exc)) from exc
        if action.source == "crew":
            crew = session.get(CrewInstance, action.crew_id) if action.crew_id else None
            cap = action_grant(action.operation)
            if (
                not crew
                or crew.runtime_generation != action.generation
                or cap not in crew.permissions
            ):
                raise ActionPolicyError(
                    "This Crew changed or its permission was revoked. Review it again."
                )

    def propose(self, draft: ActionDraft, crew_id=None, generation=None, event_key=""):
        with self.factory() as session:
            account = session.get(GoogleAccount, "google")
            local = draft.operation.split(".")[0] in LOCAL_SERVICES
            provider = provider_for(draft.operation)
            subject = (
                (
                    self.connectors.subject(provider)
                    if getattr(self, "connectors", None)
                    else account.subject
                    if account
                    else None
                )
                if not local
                else "local"
            )
            if not subject:
                raise ActionPolicyError(f"Connect {provider.title()} first")
            crew = session.get(CrewInstance, crew_id) if crew_id else None
            if crew_id and (not crew or crew.runtime_generation != generation):
                raise ActionPolicyError("Crew configuration changed")
            fingerprint = hashlib.sha256(
                json.dumps(
                    [
                        crew_id or secrets.token_hex(16),
                        generation,
                        event_key,
                        draft.operation,
                        draft.arguments,
                    ],
                    sort_keys=True,
                ).encode()
            ).hexdigest()
            existing = session.scalar(
                select(ActionRequest).where(ActionRequest.fingerprint == fingerprint)
            )
            if existing:
                return view(existing)
            binding = None
            if draft.operation in NEEDS_APPROVAL and self.desktop and self.rules:
                # What a person approves is exactly these scripts: remember their hash now.
                try:
                    binding = self.desktop.projects.binding(
                        draft.arguments, self.rule_for(session, draft.operation, crew_id)
                    )
                except ValueError as exc:
                    raise ActionSlip(str(exc)) from None
            row = ActionRequest(
                crew_id=crew_id,
                source="crew" if crew_id else "user",
                crew_name=crew.name if crew else None,
                generation=generation,
                account_subject=subject,
                operation=draft.operation,
                arguments=draft.arguments,
                fingerprint=fingerprint,
                policy_revision=self.rules.read(session, draft.operation, crew_id)["revision"]
                if self.rules
                else None,
            )
            target_id = draft.arguments.get("item_id") or draft.arguments.get("message_id")
            service = draft.operation.split(".")[0]
            target = next(
                (
                    item
                    for item in (
                        account.cursors.get(service, {}).get("snapshot", []) if account else []
                    )
                    if item.get("id") == target_id
                ),
                {},
            )
            row.target_summary = str(
                draft.arguments.get("summary")
                or draft.arguments.get("title")
                or draft.arguments.get("subject")
                or draft.arguments.get("url")
                or draft.arguments.get("command")
                or (
                    f"{draft.arguments['tool'].title()}: "
                    f"{draft.arguments.get('script') or draft.arguments.get('file')} "
                    f"in {draft.arguments['project']}"
                    if draft.operation == "project.run"
                    else None
                )
                or draft.arguments.get("path")
                or draft.arguments.get("name")
                or target.get("summary")
                or target.get("title")
                or target.get("subject")
                or target_id
                or ""
            )[:500]
            self.check_actor(session, row)
            session.add(row)
            session.commit()
            if binding:
                session.add(WorkspaceSetting(key=f"run:{row.id}", value={"sha256": binding}))
                session.commit()
            return view(row)

    def rule_for(self, session, operation, crew_id=None):
        """The saved access for an action. Where projects are kept is one setting, saved on
        "Create a project folder": the other project actions follow it, so a project made
        in one place is never looked for in another."""
        rule = self.rules.read(session, operation, crew_id)
        if operation.startswith("project."):
            home = self.rules.read(session, "project.create", crew_id)
            rule = {**rule, "folders": home.get("folders", [])}
        return rule

    def start(self, coroutine):
        """Run something after the request that started it has been answered."""
        task = asyncio.ensure_future(coroutine)
        self.background.add(task)
        task.add_done_callback(self.background.discard)

    async def run_approved(self, action_id):
        """Carry out a script run the person has approved, then let its Crew see what happened."""
        try:
            await self.execute(action_id)
        except (ActionPolicyError, GoogleConnectionError) as exc:
            with self.factory() as session:
                session.execute(
                    update(ActionRequest)
                    .where(ActionRequest.id == action_id, ActionRequest.status == "pending")
                    .values(status="blocked", error=str(exc), finished_at=utcnow())
                )
                session.commit()
        finally:
            with self.factory() as session:
                row = session.get(ActionRequest, action_id)
                if row:
                    self.wake_crew(session, row.crew_id)
                    session.commit()

    def wake_crew(self, session, crew_id):
        """The person answered (or the run finished): the Crew carries on with what happened."""
        crew = session.get(CrewInstance, crew_id) if crew_id else None
        if crew and crew.is_running:
            crew.runtime_state = "waiting"
            crew.next_run_at = utcnow()
            crew.observation = {**(crew.observation or {}), "question": None}

    async def request(
        self, service, capability, method, path, payload=None, headers=None, action=None
    ):
        if getattr(self, "session_guard", None) and not self.session_guard():
            raise ActionPolicyError("Sign in to Mavo before applying changes")
        token = await self.auth.access_token(service, capability)
        if getattr(self, "session_guard", None) and not self.session_guard():
            raise ActionPolicyError("Mavo was signed out before the action started")
        if action:
            with self.factory() as session:
                self.check_actor(session, action)
        base = {
            "gmail": "https://gmail.googleapis.com/gmail/v1",
            "calendar": "https://www.googleapis.com/calendar/v3",
            "tasks": "https://tasks.googleapis.com/tasks/v1",
        }[service]
        async with httpx.AsyncClient(transport=self.auth.transport, timeout=20) as client:
            response = await client.request(
                method,
                base + path,
                json=payload,
                headers={"Authorization": "Bearer " + token, **(headers or {})},
            )
        return response

    async def perform(self, action):
        service, verb = action.operation.split(".")
        if action.operation == "helper.ask":
            with self.factory() as session:
                self.check_actor(session, action)
            if not self.helper:
                raise ActionRejected("Helper agents are unavailable")
            return {**(await self.helper(action)), "verification": "local_tool_result"}
        if provider_for(action.operation) not in {"local", "google"}:
            from mavo.connectors.actions import perform

            def guard():
                if getattr(self, "session_guard", None) and not self.session_guard():
                    raise ActionPolicyError("Sign in to Mavo first")
                with self.factory() as session:
                    self.check_actor(session, action)

            return await perform(self.connectors, action, guard)
        if service in LOCAL_SERVICES:
            if not self.desktop:
                raise ActionPolicyError("Desktop actions are unavailable")
            with self.factory() as session:
                self.check_actor(session, action)
                rule = self.rule_for(session, action.operation, action.crew_id)

            def guard():
                with self.factory() as session:
                    self.check_actor(session, action)

            if action.operation in NEEDS_APPROVAL:
                with self.factory() as session:
                    bound = session.get(WorkspaceSetting, f"run:{action.id}")
                try:
                    current = self.desktop.projects.binding(action.arguments, rule)
                except ValueError as exc:
                    raise ActionRejected(str(exc)) from None
                if not bound or bound.value.get("sha256") != current:
                    raise ActionRejected(
                        "The project's scripts changed after this was requested. "
                        "Ask for the run again."
                    )
            try:
                outcome = await self.desktop.perform(action, rule, guard)
            except ValueError as exc:
                # These check every path and limit before they change anything: a refusal
                # means nothing happened.
                if service in {"browser", "project", "notes", "files"}:
                    raise ActionRejected(str(exc)) from None
                raise
            return {**outcome, "verification": "local_tool_result"}
        capability = required_capability(action.operation).split(":")[1]
        args = action.arguments
        headers = {"If-Match": args["etag"]} if args.get("etag") else {}
        if service == "gmail":
            if verb in {"send", "draft"}:
                with self.factory() as session:
                    sender = session.get(GoogleAccount, "google").email
                message = EmailMessage()
                message["From"], message["To"], message["Subject"] = (
                    sender,
                    args["to"],
                    args["subject"],
                )
                message["Message-ID"] = f"<mavo-{action.id}@local.mavo>"
                if args.get("in_reply_to"):
                    message["In-Reply-To"] = args["in_reply_to"]
                    message["References"] = args["in_reply_to"]
                message.set_content(args["body"])
                data = {"raw": base64.urlsafe_b64encode(message.as_bytes()).decode()}
                if args.get("thread_id"):
                    data["threadId"] = args["thread_id"]
                path = "/users/me/messages/send" if verb == "send" else "/users/me/drafts"
                if verb == "draft":
                    data = {"message": data}
            else:
                suffix = "trash" if verb == "trash" else "modify"
                path = "/users/me/messages/" + quote(args["message_id"], safe="") + "/" + suffix
                data = (
                    {}
                    if verb == "trash"
                    else {"removeLabelIds": ["INBOX" if verb == "archive" else "UNREAD"]}
                )
            response = await self.request(service, capability, "POST", path, data, action=action)
        else:
            base = "/calendars/primary/events" if service == "calendar" else "/lists/@default/tasks"
            path = base + ("/" + quote(args["item_id"], safe="") if args.get("item_id") else "")
            method = "DELETE" if verb == "delete" else "POST" if verb == "create" else "PATCH"
            if service == "calendar":
                data = {
                    "summary": args.get("summary"),
                    "start": {"dateTime": args.get("start")},
                    "end": {"dateTime": args.get("end")},
                }
                if verb == "create":
                    data["id"] = action.id.replace("-", "")
                if "description" in args:
                    data["description"] = args["description"]
            else:
                data = {"title": args.get("title")}
                if "notes" in args:
                    data["notes"] = args["notes"]
                if "due" in args:
                    data["due"] = args["due"] + "T00:00:00Z" if args["due"] else None
                if verb == "complete":
                    data = {"status": "completed"}
            response = await self.request(
                service,
                capability,
                method,
                path,
                None if verb == "delete" else data,
                headers,
                action=action,
            )
        if response.status_code == 412:
            raise ActionRejected(
                "The item changed since review. Refresh it and create a new action."
            )
        if response.is_error:
            if response.status_code >= 500 or response.status_code == 408:
                raise RuntimeError("Uncertain provider outcome")
            raise ActionRejected(
                f"Google rejected the action ({response.status_code}). Nothing was retried."
            )
        if verb == "delete":
            verified = await self.request(service, capability, "GET", path, action=action)
            if verified.status_code not in {404, 410}:
                raise RuntimeError("Deletion could not be verified")
            return {"verification": "deletion_verified", "id": args["item_id"]}
        item = response.json()
        if not item.get("id"):
            raise RuntimeError("Google returned no outcome identifier")
        if service == "gmail" and verb == "draft":
            if not item.get("message", {}).get("id"):
                raise RuntimeError("Google returned no draft message")
            return {"id": item["id"], "verification": "draft_created_not_sent"}
        if service == "gmail" and verb != "send":
            labels = item.get("labelIds")
            if not isinstance(labels, list) or (
                verb == "archive"
                and "INBOX" in labels
                or verb == "mark_read"
                and "UNREAD" in labels
                or verb == "trash"
                and "TRASH" not in labels
            ):
                raise RuntimeError("The mailbox state could not be verified")
            return {"id": item["id"], "verification": "provider_state_verified"}
        if service != "gmail":
            verified = await self.request(
                service,
                capability,
                "GET",
                base + "/" + quote(item["id"], safe=""),
                action=action,
            )
            verified.raise_for_status()
            observed = verified.json()
            expected = (
                {"summary": args.get("summary")}
                if service == "calendar"
                else (
                    {"status": "completed"} if verb == "complete" else {"title": args.get("title")}
                )
            )
            if any(observed.get(k) != v for k, v in expected.items()):
                raise RuntimeError("The observed result did not match the approved action")
            if service == "calendar":
                for key in ("start", "end"):
                    actual = datetime.fromisoformat(
                        observed[key]["dateTime"].replace("Z", "+00:00")
                    )
                    if actual != datetime.fromisoformat(args[key].replace("Z", "+00:00")):
                        raise RuntimeError("Event time differs from the approved action")
        return {
            "id": item["id"],
            "verification": "provider_acknowledged" if service == "gmail" else "read_back_verified",
        }

    async def maybe_execute(self, action_id):
        with self.factory() as session:
            row = session.get(ActionRequest, action_id)
            if not row:
                raise ActionPolicyError("Action not found")
            self.check_actor(session, row)
            if row.status != "pending" or not self.rules or not self.rules.automatic(session, row):
                return view(row)
        try:
            return await self.execute(action_id)
        except (ActionPolicyError, GoogleConnectionError) as exc:
            with self.factory() as session:
                session.execute(
                    update(ActionRequest)
                    .where(ActionRequest.id == action_id, ActionRequest.status == "pending")
                    .values(status="blocked", error=str(exc), finished_at=utcnow())
                )
                session.commit()
            raise

    async def execute(self, action_id):
        with self.factory() as session:
            row = session.get(ActionRequest, action_id)
            if not row:
                raise ActionPolicyError("Action not found")
            if row.status == "completed":
                return view(row)
            if row.status != "pending":
                raise ActionPolicyError("This action cannot be retried. Check its outcome first.")
            self.check_actor(session, row)
            if row.created_at.replace(tzinfo=utcnow().tzinfo) < utcnow() - timedelta(days=1):
                raise ActionPolicyError("This approval expired. Create a new action.")
            if not self.rules or not self.rules.automatic(session, row):
                raise ActionPolicyError("This action is not allowed by its access settings")
            local = row.operation.split(".")[0] in LOCAL_SERVICES
            if not local:
                service, capability = required_capability(row.operation).split(":")
        if not local:
            if provider_for(row.operation) == "google":
                await self.auth.access_token(service, capability)
            else:
                await self.connectors.access_token(
                    provider_for(row.operation), action_grant(row.operation)
                )
        with self.factory() as session:
            row = session.get(ActionRequest, action_id)
            self.check_actor(session, row)
            claimed = session.execute(
                update(ActionRequest)
                .where(ActionRequest.id == action_id, ActionRequest.status == "pending")
                .values(
                    status="executing",
                    lease_until=utcnow()
                    + timedelta(seconds=120 if row.operation != "project.run" else 900),
                    # A person's approval of a script stays recorded as theirs.
                    approved_by=func.coalesce(ActionRequest.approved_by, "access"),
                )
            )
            if claimed.rowcount != 1:
                raise ActionPolicyError("This action is already being processed")
            session.commit()
        try:
            result = await self.perform(row)
            status, error = "completed", None
            self.pool.clear()
        except asyncio.CancelledError:
            with self.factory() as session:
                current = session.get(ActionRequest, action_id)
                current.status, current.error, current.finished_at = (
                    "unknown",
                    "Execution was interrupted. Check the target before retrying.",
                    utcnow(),
                )
                session.commit()
            raise
        except ActionRejected as exc:
            result, status, error = None, "failed", str(exc)
        except ConnectorRejected as exc:
            result, status, error = None, "failed", str(exc)
        except (httpx.HTTPError, ValueError, RuntimeError, OSError, GoogleConnectionError):
            result, status, error = (
                None,
                "unknown",
                "Outcome uncertain. Check the target before trying again. No automatic retry.",
            )
        with self.factory() as session:
            row = session.get(ActionRequest, action_id)
            row.status, row.result, row.error, row.finished_at = status, result, error, utcnow()
            session.commit()
            return view(row)


def build_actions_api(actions, get_session):
    router = APIRouter()

    @router.get("/actions")
    def listing():
        with actions.factory() as session:
            session.execute(
                update(ActionRequest)
                .where(ActionRequest.status == "executing", ActionRequest.lease_until < utcnow())
                .values(
                    status="unknown",
                    error="The runtime stopped during an action. Check Google before trying again.",
                )
            )
            session.commit()
            return [
                view(a)
                for a in session.scalars(
                    select(ActionRequest).order_by(ActionRequest.created_at.desc()).limit(100)
                )
            ]

    @router.post("/actions", status_code=201)
    async def propose(draft: ActionDraft):
        try:
            item = actions.propose(draft)
            return await actions.maybe_execute(item["id"])
        except (ActionPolicyError, GoogleConnectionError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/actions/{action_id}/approve")
    async def approve(action_id: str, payload: RunApproval | None = None):
        """Per-action approval is retired, with one exception: running a script. The person
        approves these exact scripts (by their hash), once, and may mark the project trusted."""
        with actions.factory() as session:
            row = session.get(ActionRequest, action_id)
            if not row or row.operation not in NEEDS_APPROVAL:
                raise HTTPException(
                    410, "Per-action approval is retired. Configure access in Actions."
                )
            if payload is None:
                raise HTTPException(422, "Send the sha256 of the scripts you reviewed.")
            if row.status != "pending" or row.approved_by == "user":
                raise HTTPException(409, "This request is no longer waiting for approval")
            rule = actions.rule_for(session, row.operation, row.crew_id)
            bound = session.get(WorkspaceSetting, f"run:{row.id}")
            try:
                current = actions.desktop.projects.binding(row.arguments, rule)
                actions.check_actor(session, row)
            except (ValueError, ActionPolicyError) as exc:
                raise HTTPException(409, str(exc)) from None
            if not bound or not (bound.value.get("sha256") == current == payload.sha256):
                raise HTTPException(
                    409, "The scripts changed since this was requested. Review them again."
                )
            if payload.trust:
                set_trusted(session, row.arguments["project"], True)
            if payload.automation and actions.automations:
                actions.automations.save(session, row, payload.automation, payload.sha256)
            row.approved_by = "user"
            session.commit()
        actions.start(actions.run_approved(action_id))
        return {"status": "approved"}

    @router.post("/actions/{action_id}/reject")
    def reject(action_id: str):
        with actions.factory() as session:
            row = session.get(ActionRequest, action_id)
            changed = session.execute(
                update(ActionRequest)
                .where(ActionRequest.id == action_id, ActionRequest.status == "pending")
                .values(status="rejected", finished_at=utcnow())
            )
            if changed.rowcount == 1 and row and row.operation in NEEDS_APPROVAL:
                actions.wake_crew(session, row.crew_id)  # it learns the answer was no
            session.commit()
            if changed.rowcount != 1:
                raise HTTPException(409, "Only pending actions can be rejected")
            return {"status": "rejected"}

    @router.get("/projects/approvals")
    def waiting_runs():
        """Script runs waiting for the person, each with the scripts they would be approving."""
        with actions.factory() as session:
            rows = session.scalars(
                select(ActionRequest)
                .where(
                    ActionRequest.operation.in_(NEEDS_APPROVAL), ActionRequest.status == "pending"
                )
                .order_by(ActionRequest.created_at)
            ).all()
            result = []
            for row in rows:
                if row.approved_by == "user":
                    continue
                rule = actions.rule_for(session, row.operation, row.crew_id)
                bound = session.get(WorkspaceSetting, f"run:{row.id}")
                try:
                    shown = actions.desktop.projects.preview(row.arguments, rule)
                    current = actions.desktop.projects.binding(row.arguments, rule)
                except ValueError as exc:
                    shown, current = (
                        {"project": row.arguments.get("project"), "error": str(exc)},
                        None,
                    )
                recorded = bound.value.get("sha256") if bound else None
                result.append(
                    {
                        "id": row.id,
                        "crew_id": row.crew_id,
                        "crew_name": row.crew_name,
                        "created_at": row.created_at,
                        "sha256": recorded,
                        "changed": current is None or current != recorded,
                        "trusted": project_trusted(session, row.arguments.get("project")),
                        **shown,
                    }
                )
            return result

    @router.get("/projects")
    def project_list():
        with actions.factory() as session:
            rule = actions.rules.read(session, "project.create")
            try:
                root = actions.desktop.projects.root(rule)
            except (ValueError, OSError) as exc:
                return {"folder": None, "error": str(exc), "projects": []}
            found = []
            for folder in sorted(root.iterdir()) if root.is_dir() else []:
                if (folder / MARKER).is_file():
                    found.append(
                        {
                            "project": folder.name,
                            "folder": str(folder),
                            "files": len(actions.desktop.projects.files(folder)),
                            "trusted": project_trusted(session, folder.name),
                        }
                    )
            return {"folder": str(root), "projects": found}

    @router.put("/projects/{name}/trust")
    def trust(name: str, payload: ProjectTrust):
        with actions.factory() as session:
            try:
                set_trusted(session, name, payload.trusted)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None
            session.commit()
        return {"project": slug_of(name), "trusted": payload.trusted}

    return router


class RunApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    trust: bool = False
    # Also keep this exact run as an automation: the person chose it while reading the scripts.
    automation: AutomationSave | None = None


class ProjectTrust(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trusted: bool


def set_trusted(session, name, trusted):
    key = TRUST_PREFIX + slug_of(str(name))
    row = session.get(WorkspaceSetting, key)
    if row:
        row.value = {"trusted": trusted}
    else:
        session.add(WorkspaceSetting(key=key, value={"trusted": trusted}))
