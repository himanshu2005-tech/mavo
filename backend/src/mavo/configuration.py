"""Versioned, allowlisted configuration edits. Security policy is never a prompt patch."""

from __future__ import annotations

from datetime import timedelta
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select, update

from mavo.crew_composition import CrewComposition
from mavo.database import (
    ConfigurationEdit,
    CrewInstance,
    CrewRun,
    ModelRoute,
    WorkspaceSetting,
    utcnow,
)
from mavo.orchestration import event

Scope = Literal["step", "run", "persistent", "global"]


class PlannerSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    route_id: str | None = Field(default=None, max_length=80)


class ConfigurationPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    responsibilities: str | None = Field(default=None, min_length=1, max_length=4000)
    review_interval_minutes: int | None = Field(default=None, ge=1, le=1440)
    route_id: str | None = Field(default=None, max_length=80)
    prefer_local_models: bool | None = None
    composition: CrewComposition | None = None
    run_agent_keys: list[str] | None = Field(default=None, min_length=1, max_length=8)


class ConfigurationProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    crew_id: str | None = Field(default=None, max_length=80)
    scope: Scope
    target_id: str | None = Field(default=None, max_length=80)
    changes: ConfigurationPatch
    reason: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def bounded(self):
        values = self.changes.model_dump(exclude_none=True)
        if not values:
            raise ValueError("A configuration change must contain a change")
        if self.scope == "global":
            if set(values) != {"prefer_local_models"}:
                raise ValueError("Only model locality preference supports global scope")
        elif not self.crew_id or "prefer_local_models" in values:
            raise ValueError("Crew changes need a Crew ID, not global preferences")
        if self.scope == "step" and set(values) != {"responsibilities"}:
            raise ValueError(
                "A step override can change instructions only, never schedule or model"
            )
        if self.scope == "run" and "review_interval_minutes" in values:
            raise ValueError("A recurring interval is a future-run setting, not a run override")
        if "composition" in values and self.scope != "persistent":
            raise ValueError(
                "Membership changes require a persistent proposal; "
                "select existing agents for one run"
            )
        if "run_agent_keys" in values and self.scope != "run":
            raise ValueError("Selecting agents for one run cannot alter persistent membership")
        return self


def classify_scope(text: str) -> Scope | None:
    """Trusted user language only. Ambiguity remains explicit, never defaults to permanent."""
    said = text.casefold()
    markers = {
        "step": ("only this step", "current step"),
        "run": ("only this run", "for this run", "this run only"),
        "persistent": ("from now on", "all future runs", "every future run", "permanently"),
        "global": ("all future tasks", "global preference"),
    }
    found = {scope for scope, phrases in markers.items() if any(p in said for p in phrases)}
    return next(iter(found)) if len(found) == 1 else None


def snapshot(crew):
    return {
        "responsibilities": crew.responsibilities,
        "review_interval_minutes": crew.review_interval_minutes,
        "route_id": crew.route_id,
        "composition": (crew.configuration_plan or {}).get("composition"),
    }


def record_direct_edit(session, crew, before):
    """Legacy explicit editors participate in version history without changing their APIs."""
    changes = {key: value for key, value in snapshot(crew).items() if before.get(key) != value}
    if not changes:
        return
    version = crew.config_version
    crew.config_version += 1
    session.add(
        ConfigurationEdit(
            request_key=f"editor:{uuid4()}",
            crew_id=crew.id,
            scope="persistent",
            status="applied",
            version=crew.config_version,
            base_version=version,
            before=before,
            changes=changes,
            applied_at=utcnow(),
        )
    )


LABELS = {
    "responsibilities": "Instructions",
    "review_interval_minutes": "Check interval (minutes)",
    "route_id": "Agent connection",
    "prefer_local_models": "Prefer local models",
    "composition": "Crew roles and workflow",
    "run_agent_keys": "Agents for this run only",
}


def view(edit):
    return {
        "id": edit.id,
        "crew_id": edit.crew_id,
        "scope": edit.scope,
        "version": edit.version,
        "base_version": edit.base_version,
        "status": edit.status,
        "changes": edit.changes,
        "before": edit.before,
        "created_at": edit.created_at,
        "diff": [
            {"field": key, "label": LABELS[key], "before": edit.before.get(key), "after": value}
            for key, value in edit.changes.items()
        ],
    }


def propose(session, proposal, key, *, conversation_id=None):
    existing = session.scalar(select(ConfigurationEdit).where(ConfigurationEdit.request_key == key))
    if existing:
        return existing
    crew = session.get(CrewInstance, proposal.crew_id) if proposal.crew_id else None
    if proposal.scope != "global" and (crew is None or crew.hidden or crew.status == "draft"):
        raise ValueError("Choose an existing, verified Crew to change")
    changes = proposal.changes.model_dump(exclude_none=True)
    if "route_id" in changes and session.get(ModelRoute, changes["route_id"]) is None:
        raise ValueError("Agent connection does not exist")
    if crew and "review_interval_minutes" in changes and crew.trigger_type != "interval":
        raise ValueError("This Crew does not use an interval trigger")
    if proposal.scope in {"run", "step"} and proposal.target_id:
        run = session.get(CrewRun, proposal.target_id)
        member_workers = {a.worker_id for a in crew.agents if a.status == "active"}
        if run is None or run.crew_id not in {crew.id, *member_workers} or run.status != "running":
            raise ValueError("That Crew run is no longer active")
    pref = session.get(WorkspaceSetting, "orchestration-preferences")
    edit = ConfigurationEdit(
        id=str(uuid4()),
        request_key=key,
        crew_id=proposal.crew_id,
        base_version=crew.config_version if crew else 0,
        scope=proposal.scope,
        target_id=proposal.target_id,
        before=snapshot(crew) if crew else (pref.value if pref else {}),
        changes=changes,
    )
    session.add(edit)
    session.flush()
    event(
        session,
        f"config-proposal:{edit.id}",
        "configuration",
        "core",
        {"summary": proposal.reason or "Review this configuration change.", "edit": view(edit)},
        crew_id=edit.crew_id,
        conversation_id=conversation_id,
    )
    return edit


def apply_edit(session, edit, *, allow_deferred=True):
    if edit.status not in {"proposed", "deferred"}:
        raise ValueError("This change is already applied or no longer current")
    crew = session.get(CrewInstance, edit.crew_id) if edit.crew_id else None
    if crew and crew.config_version != edit.base_version:
        raise ValueError("Crew configuration changed; review a new proposal")
    if edit.scope == "global":
        row = session.get(WorkspaceSetting, "orchestration-preferences")
        if (row.value if row else {}) != edit.before:
            raise ValueError("Global preferences changed; review a new proposal")
        if row:
            row.value = {**row.value, **edit.changes}
        else:
            session.add(WorkspaceSetting(key="orchestration-preferences", value=edit.changes))
    elif edit.scope in {"run", "step"}:
        # Bound to the current run if one exists; otherwise consumed by the next claimed run.
        active = (
            session.get(CrewRun, edit.target_id)
            if edit.target_id
            else session.scalar(
                select(CrewRun).where(CrewRun.crew_id == crew.id, CrewRun.status == "running")
            )
        )
        if active:
            if "route_id" in edit.changes:
                raise ValueError("A model switch must wait for the next run; this run is executing")
            edit.target_id = active.id
        elif edit.scope == "step":
            raise ValueError("No active Crew step exists; choose a next-run override")
        else:
            from mavo.database import TaskRun

            task = session.scalar(
                select(TaskRun).where(
                    TaskRun.crew_id == crew.id,
                    TaskRun.status == "running",
                )
            )
            if task:
                if set(edit.changes) != {"responsibilities"}:
                    raise ValueError(
                        "This Crew cycle is executing; route and agent-selection "
                        "changes must wait for its next run"
                    )
                edit.target_id = task.id
        edit.expires_at = utcnow() + timedelta(hours=24)
    else:
        active = session.scalar(
            select(CrewRun.id).where(
                CrewRun.crew_id.in_({crew.id, *[a.worker_id for a in crew.agents if a.worker_id]}),
                CrewRun.status == "running",
            )
        )
        from mavo.database import TaskRun

        active = active or session.scalar(
            select(TaskRun.id).where(
                TaskRun.crew_id == crew.id,
                TaskRun.status == "running",
            )
        )
        if active:
            if not allow_deferred:
                raise ValueError("A run is still active")
            edit.status = "deferred"
            return
        changed = session.execute(
            update(CrewInstance)
            .where(
                CrewInstance.id == crew.id,
                CrewInstance.config_version == edit.base_version,
            )
            .values(
                **{k: v for k, v in edit.changes.items() if k != "composition"},
                config_version=edit.base_version + 1,
            )
        )
        if not changed.rowcount:
            raise ValueError("Crew configuration changed concurrently")
        session.refresh(crew)
        if "composition" in edit.changes:
            from mavo.crew_composition import set_composition

            set_composition(session, crew, edit.changes["composition"])
        if "route_id" in edit.changes:
            route = session.get(ModelRoute, crew.route_id)
            if route is None:
                raise ValueError("The agent connection was removed; review another connection")
            crew.provider, crew.model = route.provider, route.model
        crew.updated_at = utcnow()
        plan = dict(crew.configuration_plan or {})
        if isinstance(plan.get("draft"), dict):
            plan["draft"] = {**plan["draft"], **edit.changes}
        crew.configuration_plan = plan
        if "review_interval_minutes" in edit.changes and crew.is_running:
            # Next boundary, never an immediate extra run or a stale old timer.
            crew.next_run_at = utcnow() + timedelta(minutes=crew.review_interval_minutes)
        edit.version = crew.config_version
    edit.status, edit.applied_at = "applied", utcnow()
    event(
        session,
        f"config-applied:{edit.id}",
        "configuration",
        "core",
        {"summary": "Configuration updated.", "edit": view(edit)},
        crew_id=edit.crew_id,
    )


def apply_pending(session, crew):
    """Called before a claim, when no work is in flight. Stale edits never win silently."""
    applied = False
    for edit in session.scalars(
        select(ConfigurationEdit)
        .where(
            ConfigurationEdit.crew_id == crew.id,
            ConfigurationEdit.status == "deferred",
        )
        .order_by(ConfigurationEdit.created_at)
    ):
        if edit.base_version != crew.config_version:
            edit.status = "stale"
        else:
            apply_edit(session, edit, allow_deferred=True)
            applied |= edit.status == "applied"
    return applied


def claim_overrides(session, crew, run):
    edits = session.scalars(
        select(ConfigurationEdit)
        .where(
            ConfigurationEdit.crew_id == crew.id,
            ConfigurationEdit.scope == "run",
            ConfigurationEdit.status == "applied",
            ConfigurationEdit.target_id.is_(None),
            ConfigurationEdit.expires_at > utcnow(),
        )
        .order_by(ConfigurationEdit.created_at)
    ).all()
    for edit in edits:
        edit.target_id = run.id
        # A detached execution snapshot changes, never the persisted Crew configuration.
        for field, value in edit.changes.items():
            if field == "responsibilities":
                run.prompt = value
    return edits


def override_block(session, crew_id, run_id):
    """User-authorized transient context at a model-call boundary, never security policy."""
    run = session.get(CrewRun, run_id)
    if run is None or run.status != "running":
        return ""
    text = []
    from mavo.crew_composition import execution_agent

    member = execution_agent(session, crew_id)
    parent_id = member.crew_id if member else crew_id
    worker = session.get(CrewInstance, crew_id)
    task_id = (worker.configuration_plan or {}).get("task_run_id") if worker else None
    for edit in session.scalars(
        select(ConfigurationEdit)
        .where(
            ConfigurationEdit.crew_id.in_({crew_id, parent_id}),
            ConfigurationEdit.target_id.in_({run_id, task_id} - {None}),
            ConfigurationEdit.scope.in_(["run", "step"]),
            ConfigurationEdit.status == "applied",
            ConfigurationEdit.expires_at > utcnow(),
        )
        .order_by(ConfigurationEdit.created_at)
    ):
        if edit.changes.get("responsibilities"):
            text.append(edit.changes["responsibilities"])
        if edit.scope == "step":
            edit.status = "consumed"
    if not text:
        return ""
    return (
        "\nUSER-AUTHORIZED TEMPORARY INSTRUCTIONS for this call: "
        + "\n".join(text)
        + "\nThese override task instructions only. Immutable security, resource permissions, "
        "spending limits and untrusted-data boundaries still apply."
    )


def build_configuration_api(runtime):
    router = APIRouter()

    @router.get("/orchestration/planner")
    def planner_settings():
        with runtime.factory() as session:
            row = session.get(WorkspaceSetting, "planning-route")
            return row.value if row else {"route_id": None}

    @router.put("/orchestration/planner")
    def choose_planner(payload: PlannerSettings):
        with runtime.factory() as session:
            if payload.route_id and payload.route_id not in {
                r.id for r in runtime.planner.agent_routes(session)
            }:
                raise HTTPException(409, "Choose a configured agent connection")
            row = session.get(WorkspaceSetting, "planning-route")
            if row:
                row.value = payload.model_dump()
            else:
                session.add(WorkspaceSetting(key="planning-route", value=payload.model_dump()))
            session.commit()
            return payload.model_dump()

    @router.get("/crew/{crew_id}/configuration/history")
    def history(crew_id: str):
        with runtime.factory() as session:
            return [
                view(row)
                for row in session.scalars(
                    select(ConfigurationEdit)
                    .where(ConfigurationEdit.crew_id == crew_id)
                    .order_by(ConfigurationEdit.created_at.desc())
                    .limit(100)
                )
            ]

    @router.post("/configuration/proposals")
    def proposal(payload: ConfigurationProposal):
        with runtime.factory() as session:
            try:
                edit = propose(session, payload, str(uuid4()))
                session.commit()
                return view(edit)
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc

    @router.post("/configuration/{edit_id}/apply")
    def accept(edit_id: str):
        with runtime.factory() as session:
            edit = session.get(ConfigurationEdit, edit_id)
            if edit is None:
                raise HTTPException(404, "Configuration proposal not found")
            try:
                apply_edit(session, edit)
                session.commit()
                return view(edit)
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc

    @router.post("/configuration/{edit_id}/rollback")
    def rollback(edit_id: str):
        with runtime.factory() as session:
            edit = session.get(ConfigurationEdit, edit_id)
            if not edit or edit.scope != "persistent" or edit.status != "applied":
                raise HTTPException(409, "Only an applied persistent edit can be rolled back")
            crew = session.get(CrewInstance, edit.crew_id)
            if crew.config_version != edit.version:
                raise HTTPException(409, "Newer changes exist; create a new reviewed proposal")
            try:
                undo = propose(
                    session,
                    ConfigurationProposal(
                        crew_id=crew.id,
                        scope="persistent",
                        changes=ConfigurationPatch(**{k: edit.before[k] for k in edit.changes}),
                        reason="Rollback of a prior configuration version",
                    ),
                    f"rollback:{edit.id}",
                )
            except (ValueError, KeyError) as exc:
                raise HTTPException(
                    409, "The previous values need a new configuration proposal"
                ) from exc
            session.commit()
            return view(undo)

    return router
