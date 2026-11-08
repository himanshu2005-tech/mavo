"""Automatic, idempotent Crew creation; user-owned access and independent agent execution."""

import hashlib
import json
import logging
import re
import time
from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError

from mavo.action_rules import CATALOG, LOCAL_SERVICES, Rule, RuleChange
from mavo.connectors.catalog import ConnectorAccess, action_grant
from mavo.conversations import conversation_for, core_chat_view
from mavo.coordinator_client import CoordinatorUnavailable
from mavo.coordinator_contract import GatewayTurn, PlannerContext
from mavo.core import core_conversation
from mavo.crew_workers import first_run_at, stop_crew
from mavo.database import (
    Conversation,
    CoordinatorSession,
    CoordinatorTurn,
    CrewInstance,
    Message,
    ModelRoute,
    ResourceEvent,
    WorkspaceSetting,
    utcnow,
)
from mavo.google_permissions import GoogleCapability
from mavo.resource_pool import brief, ceiling_error
from mavo.schemas import CoordinatorRequest, CrewDraft, CrewView
from mavo.shared_memory import add_fact, memory_lines

logger = logging.getLogger(__name__)


ASKS_FOR = re.compile(
    r"\?|\b(?:tell me|let me know)\b(?! if)|\b(?:share|send me|give me) (?:your|the)\b", re.I
)


WANTS_TO_BE_TOLD = re.compile(
    r"\b(remind|tell|send|notify|notification|alert|update|ping|let me know|keep me posted|"
    r"summar|digest|consolidat|brief me|report to me|warn)",
    re.I,
)


def held_for_a_detail(reply: str) -> str | None:
    """A task started in the same breath as a question ("I've started a search for fares to
    Chennai. Tell me your departure city.") runs without the answer: the agent guesses, searches
    broadly, and runs out of steps. The question is asked instead; the task starts from the
    answer. Returns the question, or None when the reply asks nothing."""
    asking = [s for s in re.split(r"(?<=[.!?])\s+", reply.strip()) if ASKS_FOR.search(s)]
    return " ".join(asking) if asking else None


def tidy_reply(decision):
    """The chat message is a sentence, not a report. Details live in the setup card, and a
    question is asked on its own, one at a time."""
    text = decision.reply.strip()
    if decision.questions:
        return decision.questions[0].strip()
    if decision.crew_proposal or decision.crew_proposals:
        first = re.split(r"\n\s*\n|\n\s*(?:[-*\u2022]|\d+[.)])\s", text)[0].strip()
        short = " ".join(re.split(r"(?<=[.!?])\s+", first)[:2]).strip()
        return short[:240] if short else "Here is the setup. Review it below."
    return text


def base_name(name):
    """'Placements Watch 2' and 'placements watch' are the same setup."""
    return re.sub(r"\s*\d+$", "", name.strip()).strip().lower()


class PlanError(ValueError):
    """A proposed plan is invalid in a way worth telling the user about."""


class CrewAccess(ConnectorAccess):
    model_config = ConfigDict(extra="forbid")
    capabilities: list[GoogleCapability] = Field(default_factory=list, max_length=10)
    actions: dict[str, Rule] = Field(default_factory=dict, max_length=10)
    notifications: bool = False
    device_metrics: bool = False
    temp_cleanup: bool = False
    weather: bool = False
    route_id: str | None = Field(default=None, max_length=80)


class CrewReply(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class CrewPlanner:
    def __init__(self, runtime, coordinator, google, credentials):
        self.runtime, self.factory, self.coordinator, self.google, self.credentials = (
            runtime,
            runtime.factory,
            coordinator,
            google,
            credentials,
        )
        self.next_reconcile = 0

    def agent_routes(self, session):
        return [
            route
            for route in session.scalars(select(ModelRoute).order_by(ModelRoute.created_at)).all()
            if route.provider == "ollama" or self.credentials.get(route.credential_id or "")
        ][:30]

    @staticmethod
    def requirements(draft):
        return set(draft.requested_google_capabilities) | {
            f"{resource}:read" for resource in draft.requested_google_resources
        }

    def reconcile(self, session, crew):
        plan = dict(crew.configuration_plan)
        if not plan.get("draft"):
            return
        draft = CrewDraft.model_validate(plan["draft"])
        missing = []
        route = session.get(ModelRoute, crew.route_id) if crew.route_id else None
        if not route:
            routes = self.agent_routes(session)
            if len(routes) == 1:
                route = routes[0]
                crew.route_id = route.id
        if not route or (
            route.provider != "ollama" and not self.credentials.get(route.credential_id or "")
        ):
            missing.append("Choose an agent connection")
        else:
            crew.provider, crew.model = route.provider, route.model
        blocked = {
            ceiling_error(session, capability=item)
            for item in [
                *draft.requested_google_capabilities,
                *[f"{r}:read" for r in draft.requested_google_resources],
                *draft.requested_connector_capabilities,
                *(["device:read"] if draft.requested_device_metrics else []),
            ]
        } | {ceiling_error(session, operation=a.operation) for a in draft.requested_actions}
        missing.extend(sorted(item for item in blocked if item))
        granted = {p[7:] for p in crew.permissions if p.startswith("google:")}
        for capability in self.requirements(draft) - granted:
            missing.append("Allow " + capability)
        connected = set(self.google.status().get("capabilities", []))
        if self.requirements(draft) - connected:
            missing.append("Connect the required Google services")
        for capability in draft.requested_connector_capabilities:
            if capability not in crew.permissions:
                missing.append("Allow " + capability)
            if (
                capability
                not in self.runtime.connectors.status(capability.split(":")[0])["capabilities"]
            ):
                missing.append("Connect " + capability.split(":")[0].title() + " for " + capability)
        if draft.requested_device_metrics and "device:read" not in crew.permissions:
            missing.append("Allow device metrics")
        if draft.requested_notifications and "notifications:show" not in crew.permissions:
            missing.append("Allow desktop updates")
        if draft.requested_weather:
            from mavo.resource_access import read_access

            if "weather:read" not in crew.permissions:
                missing.append("Allow forecasts for any destination")
            if not read_access(session).weather:
                missing.append("Enable Weather in Resources")
            if draft.max_model_calls < 2 or draft.daily_call_limit < 2:
                missing.append("Allow at least two model calls for weather-assisted work")
        if draft.trigger_type == "daily" and not draft.schedule_time:
            missing.append("Set a valid daily reminder time")
        if crew.status == "draft" and not plan.get("setup_confirmed"):
            missing.append("Review setup and select Verify & add")
        for action in draft.requested_actions:
            rule = self.runtime.actions.rules.read(session, action.operation, crew.id)
            grant = action_grant(action.operation)
            if not rule["enabled"] or grant not in crew.permissions:
                missing.append("Allow " + action.operation)
            elif rule["mode"] == "restricted":
                for scope in ("domains", "folders", "apps", "recipients", "repositories"):
                    if not set(getattr(action, scope)) <= set(rule.get(scope, [])):
                        missing.append("Review " + action.operation + " " + scope + " limits")
        if plan.get("questions"):
            plan["phase"] = "needs_input"
            missing = ["Answer the setup questions", *missing]
        else:
            plan["phase"] = "needs_access" if missing else "ready"
        plan["missing"] = missing
        if not missing:
            plan.setdefault("verified_at", utcnow().isoformat())
            plan["verification_kind"] = "configuration_and_authorization"
            if crew.status == "draft":
                crew.status = "active"
        crew.configuration_plan = plan
        if missing:
            crew.is_running = False
            crew.runtime_state = "needs_input" if plan.get("questions") else "needs_setup"
            crew.next_run_at = None
            crew.last_runtime_error = " · ".join(missing)
        elif not plan.get("user_paused") and not self.runtime.actions.rules.paused(session):
            crew.trigger_type = draft.trigger_type
            crew.schedule_time = (
                draft.schedule_time if draft.trigger_type in {"daily", "interval"} else None
            )
            crew.daily_triggers = (
                [rule.model_dump() for rule in draft.daily_triggers]
                if draft.trigger_type == "daily"
                else []
            )
            crew.mail_sender, crew.mail_subject = draft.mail_sender, draft.mail_subject
            if not crew.is_running:
                crew.runtime_generation += 1
                crew.is_running = True
                crew.status = "active"
                crew.runtime_state = "waiting"
                crew.next_run_at = first_run_at(crew, utcnow())
                crew.event_cursor = session.scalar(select(func.max(ResourceEvent.id))) or 0
                crew.observation = {**crew.observation, "failures": 0, "automation_steps": 0}
            crew.last_runtime_error = None

    def discard_stale_drafts(self, session, crew):
        """Verifying a setup must not leave its earlier drafts behind in the Crew list."""
        if crew.status == "draft":
            return
        base = base_name(crew.name)
        for other in session.scalars(
            select(CrewInstance).where(CrewInstance.status == "draft", CrewInstance.id != crew.id)
        ).all():
            if base_name(other.name) != base or other.created_at >= crew.created_at:
                continue
            for policy in session.scalars(
                select(WorkspaceSetting).where(
                    WorkspaceSetting.key.startswith("action:" + other.id + ":")
                )
            ).all():
                session.delete(policy)
            session.delete(other)

    def reconcile_pending(self):
        if time.monotonic() < self.next_reconcile:
            return
        self.next_reconcile = time.monotonic() + 5
        with self.factory() as session:
            crews = session.scalars(
                select(CrewInstance)
                .where(
                    CrewInstance.configuration_plan["phase"]
                    .as_string()
                    .in_(["needs_access", "needs_input"])
                )
                .limit(100)
            ).all()
            for crew in crews:
                before = json.dumps(crew.configuration_plan, sort_keys=True)
                self.reconcile(session, crew)
                if before == json.dumps(crew.configuration_plan, sort_keys=True):
                    session.expire(crew)
            session.commit()

    def apply_plan(self, session, decision, current_crew):
        draft = decision.crew_proposal
        if not draft:
            if current_crew and decision.questions:
                stop_crew(session, current_crew)
                self.runtime.cancel(current_crew.id)
                current_crew.runtime_state = "needs_input"
                current_crew.observation = {
                    **current_crew.observation,
                    "question": decision.questions[0],
                }
                if current_crew.configuration_plan.get("draft"):
                    current_crew.configuration_plan = {
                        **current_crew.configuration_plan,
                        "questions": decision.questions,
                        "phase": "needs_input",
                    }
            return current_crew if decision.questions else None
        crew = current_crew if decision.target_crew_id else None
        if decision.target_crew_id and (not crew or decision.target_crew_id != crew.id):
            raise ValueError("Crew target changed")
        if not crew:
            name = draft.name
            number = 2
            while session.scalar(
                select(CrewInstance.id).where(func.lower(CrewInstance.name) == name.lower())
            ):
                name = f"{draft.name[:70]} {number}"
                number += 1
            crew = CrewInstance(
                name=name,
                template_id=draft.template_id,
                provider="unassigned",
                model="",
                responsibilities=draft.responsibilities,
                permissions=["model:invoke"],
                trigger_type="manual",
                status="draft",
            )
            session.add(crew)
            session.flush()
        else:
            stop_crew(session, crew)
            self.runtime.cancel(crew.id)
            if crew.template_id != draft.template_id:
                crew.permissions = ["model:invoke"]
                crew.observation = {}
                crew.resources = []
            crew.checkpoint = None
        routes = {route.id: route for route in self.agent_routes(session)}
        if draft.route_id and draft.route_id not in routes:
            raise ValueError("Agent connection is unavailable")
        crew.route_id = draft.route_id or (crew.route_id if crew.route_id in routes else None)
        crew.template_id = draft.template_id
        crew.responsibilities = draft.responsibilities
        crew.observation = {**crew.observation, "question": None}
        crew.trigger_type = draft.trigger_type
        # An interval Crew with a time works once a day at that time (schemas.CrewDraft).
        crew.schedule_time = (
            draft.schedule_time if draft.trigger_type in {"daily", "interval"} else None
        )
        crew.daily_triggers = (
            [rule.model_dump() for rule in draft.daily_triggers]
            if draft.trigger_type == "daily"
            else []
        )
        crew.mail_sender, crew.mail_subject = draft.mail_sender, draft.mail_subject
        crew.review_interval_minutes = draft.review_interval_minutes
        crew.daily_call_limit = draft.daily_call_limit
        crew.max_model_calls = draft.max_model_calls
        crew.reminder_text = draft.responsibilities[:1000]
        crew.autonomy_mode = (
            "reactive"
            if draft.trigger_type
            in {"manual", "gmail_message", "calendar_changed", "tasks_changed"}
            else "proactive"
        )
        old = crew.configuration_plan or {}
        requested = self.requirements(draft)
        allowed_actions = {"action:" + a.operation for a in draft.requested_actions}
        crew.permissions = [
            p
            for p in crew.permissions
            if (not p.startswith("google:") or p[7:] in requested)
            and (not p.startswith("action:") or p in allowed_actions)
            and (p != "notifications:show" or draft.requested_notifications)
            and (p != "device:read" or draft.requested_device_metrics)
            and (
                p != "device:clean_temp"
                or bool(draft.device_monitor and draft.device_monitor.clean_temp_days)
            )
            and (p != "weather:read" or draft.requested_weather)
            and (
                not p.startswith(("github:", "feeds:"))
                or p in draft.requested_connector_capabilities
            )
        ]
        crew.resources = sorted(
            {"google:" + p.split(":")[1] for p in crew.permissions if p.startswith("google:")}
        )
        crew.configuration_plan = {
            "draft": draft.model_dump(mode="json"),  # dates (run_until) as text, JSON-safe
            "questions": decision.questions,
            "phase": "needs_access",
            "denied_resources": old.get("denied_resources", []),
            "user_paused": False,
            "setup_confirmed": crew.status != "draft",
            "upstream": old.get("upstream", []),
            "group": old.get("group"),
            # Its stage in a line of agents (pipelines.py).
            "review": draft.review,
            "approve_first": draft.approve_first,
            "max_rounds": draft.max_rounds,
            "leads_team": draft.leads_team,
        }
        if draft.depends_on and not getattr(decision, "in_batch", False):
            self.link_upstream(session, crew, draft.depends_on, {})
        if crew.status == "draft" and not decision.questions:
            self.runtime.actions.rules.inherit_requested(
                session, crew, [a.operation for a in draft.requested_actions]
            )
            session.flush()
        self.reconcile(session, crew)
        return crew

    @staticmethod
    def would_cycle(session, crew_id, upstream_ids):
        """True if any proposed upstream already (transitively) depends on crew_id."""
        seen, stack = set(), list(upstream_ids)
        while stack:
            current = stack.pop()
            if current == crew_id:
                return True
            if current in seen:
                continue
            seen.add(current)
            other = session.get(CrewInstance, current)
            if other:
                stack.extend(other.configuration_plan.get("upstream", []))
        return False

    def connect_crews(self, session, links, context):
        """Connect existing Crew so one's finished results reach another (deliver_handoffs).
        Only the person's own Crew from this turn's context, never a loop."""
        known = {item.id for item in context.available_crews}
        for link in links:
            source = session.get(CrewInstance, link.from_crew_id)
            target = session.get(CrewInstance, link.to_crew_id)
            if (
                not source
                or not target
                or source.id == target.id
                or not {source.id, target.id} <= known
            ):
                raise ValueError("Those Crew are unavailable to connect")
            if self.would_cycle(session, target.id, [source.id]):
                raise PlanError(
                    f"{source.name} already waits on {target.name}: connecting them the other way "
                    "would make them wait on each other forever."
                )
            upstream = list(target.configuration_plan.get("upstream", []))
            if source.id not in upstream:
                target.configuration_plan = {
                    **target.configuration_plan,
                    "upstream": [*upstream, source.id],
                }

    def link_upstream(self, session, crew, names, batch):
        ids = []
        for name in names:
            key = name.strip().lower()
            target = batch.get(key)
            if not target:
                target = session.get(CrewInstance, name.strip()) or session.scalar(
                    select(CrewInstance).where(func.lower(CrewInstance.name) == key)
                )
            if not target or target.id == crew.id:
                raise PlanError("A Crew dependency does not exist: " + name[:80])
            if target.id not in ids:
                ids.append(target.id)
        if self.would_cycle(session, crew.id, ids):
            raise PlanError("Crew dependencies cannot form a loop")
        crew.configuration_plan = {**crew.configuration_plan, "upstream": ids}

    def apply_batch(self, session, decision, earlier=()):
        """Create several independent setup drafts, then link their dependency graph. A Crew this
        chat already drafted under the same name is updated, not drafted again (a podcast plan
        asked over three messages left two of each Crew)."""
        proposals = decision.crew_proposals
        if len({p.name.strip().lower() for p in proposals}) != len(proposals):
            raise PlanError("Each Crew in a batch needs a different name")
        group = hashlib.sha256(
            json.dumps([p.name for p in proposals]).encode() + str(time.time()).encode()
        ).hexdigest()[:16]
        created, by_name = [], {}
        drafted = {
            c.name.strip().lower(): c
            for c in (session.get(CrewInstance, i) for i in earlier)
            if c is not None and c.status == "draft"
        }
        for draft in proposals:
            again = drafted.get(draft.name.strip().lower())
            crew = self.apply_plan(
                session,
                SimpleNamespace(
                    crew_proposal=draft,
                    target_crew_id=again.id if again else None,
                    questions=[],
                    in_batch=True,
                ),
                again,
            )
            crew.configuration_plan = {**crew.configuration_plan, "group": group}
            created.append((draft, crew))
            by_name[draft.name.strip().lower()] = crew
        for draft, crew in created:
            if draft.depends_on:
                self.link_upstream(session, crew, draft.depends_on, by_name)
        # Teams: each member to its lead (a lead in this batch), the lead knowing its members.
        for draft, crew in created:
            if not draft.lead:
                continue
            lead = by_name.get(draft.lead.strip().lower())
            if lead is None or lead.id == crew.id or not lead.configuration_plan.get("leads_team"):
                raise PlanError(
                    "A team member's lead must be a team lead in the same plan: " + draft.lead[:80]
                )
            crew.configuration_plan = {**crew.configuration_plan, "lead": lead.id}
            crew.trigger_type = "manual"  # it works when its lead gives it a task
            members = list(lead.configuration_plan.get("members", []))
            lead.configuration_plan = {
                **lead.configuration_plan,
                "members": [*members, crew.id][:12],
            }
        return [crew for _, crew in created]

    async def turn(self, payload):
        # The person's message is from when they sent it; the reply follows it. Both were stamped
        # when saved together at the end, so they tied and a chat sometimes showed the reply above
        # the message (and a task started during the turn above both).
        asked_at = utcnow()
        fingerprint = hashlib.sha256(payload.model_dump_json().encode()).hexdigest()
        with self.factory() as session:
            if payload.conversation_id:
                bound = session.get(CoordinatorSession, payload.session_id)
                if not bound or bound.conversation_id != payload.conversation_id:
                    raise HTTPException(404, "Chat not found")
            cached = session.get(CoordinatorTurn, payload.request_id)
            if cached:
                if cached.fingerprint != fingerprint:
                    raise HTTPException(409, "This request ID belongs to another message.")
                if cached.result:
                    return self.response(session, cached.result, payload.session_id)
            thread = session.get(CoordinatorSession, payload.session_id)
            if not thread:
                thread = CoordinatorSession(id=payload.session_id, messages=[])
                session.add(thread)
                session.flush()
            now = utcnow()
            claimed = session.execute(
                update(CoordinatorSession)
                .where(
                    CoordinatorSession.id == payload.session_id,
                    or_(
                        CoordinatorSession.lease_until.is_(None),
                        CoordinatorSession.lease_until < now,
                    ),
                )
                .values(active_request=payload.request_id, lease_until=now + timedelta(seconds=220))
                .execution_options(synchronize_session=False)
            )
            if claimed.rowcount != 1:
                raise HTTPException(409, "Mavo is still finishing your previous message.")
            if not cached:
                session.add(
                    CoordinatorTurn(
                        id=payload.request_id,
                        session_id=payload.session_id,
                        fingerprint=fingerprint,
                    )
                )
            current_id = payload.crew_id or thread.crew_id
            crew = session.get(CrewInstance, current_id) if current_id else None
            if payload.crew_id and not crew:
                raise HTTPException(404, "Crew not found")
            plan = crew.configuration_plan if crew else {}
            messages = list(thread.messages)
            if not messages:
                conversation = conversation_for(session, thread)
                if not conversation and payload.session_id == "main":
                    conversation = core_conversation(session)
                if conversation:
                    messages = [
                        {"role": m.role, "content": m.content[:8000]}
                        for m in conversation.messages[-16:]
                    ]
            messages.append({"role": "user", "content": payload.message})
            messages = messages[-24:]
            while sum(len(m["content"]) for m in messages) > 40000:
                messages.pop(0)
            context = PlannerContext(
                available_crews=[
                    {
                        "id": item.id,
                        "name": item.name
                        if item.template_id not in {"health", "wellbeing"}
                        else "Private specialist",
                        "template_id": item.template_id,
                        "responsibilities": item.responsibilities[:500]
                        if item.template_id not in {"health", "wellbeing"}
                        else "Private specialist; select it to edit its setup",
                    }
                    for item in session.scalars(
                        select(CrewInstance)
                        .where(CrewInstance.status != "draft", CrewInstance.hidden.is_(False))
                        .order_by(CrewInstance.created_at.desc())
                        .limit(40)
                    )
                ],
                agent_connections=[
                    {"id": r.id, "name": r.name, "provider": r.provider, "model": r.model}
                    for r in self.agent_routes(session)
                ],
                connected_capabilities=self.google.status().get("capabilities", []),
                connected_connector_capabilities=[
                    cap
                    for provider in ("github", "feeds")
                    for cap in self.runtime.connectors.status(provider)["capabilities"]
                ],
                current_crew_id=crew.id if crew else None,
                current_plan=plan.get("draft"),
                denied_resources=plan.get("denied_resources", []),
                local_time=datetime.now().astimezone().isoformat(),
                resource_pool=brief(session, self.runtime),
                automations=[a["name"] for a in self.runtime.actions.automations.listing()][:30],
                enabled_defaults=[
                    op for op in CATALOG if self.runtime.actions.rules.read(session, op)["enabled"]
                ],
                memory=memory_lines(session),
            )
            turn = session.get(CoordinatorTurn, payload.request_id)
            if turn.request_payload:
                gateway_turn = GatewayTurn.model_validate(turn.request_payload["turn"])
                generation = turn.request_payload["generation"]
                current_id = gateway_turn.context.current_crew_id
            else:
                gateway_turn = GatewayTurn(
                    request_id=payload.request_id, messages=messages, context=context
                )
                generation = crew.runtime_generation if crew else None
                turn.request_payload = {
                    "turn": gateway_turn.model_dump(mode="json"),
                    "generation": generation,
                }
            session.commit()
        try:
            direct = self.mavo_agent(payload.message)
            if direct is not None:
                # "@web ..." goes straight to one of Mavo's own agents: no planning call.
                decision, model = direct, "mavo"
            else:
                decision, model = await self.coordinator.complete(gateway_turn)
            decision.questions = decision.questions[:1]
            if decision.task and (question := held_for_a_detail(decision.reply)):
                decision.task, decision.reply = None, question
            if WANTS_TO_BE_TOLD.search(payload.message):
                # "Remind me of the news every 30 minutes": the model drafted the Crew with desktop
                # alerts off, twice, so its reports would only have sat in the app.
                for draft in [decision.crew_proposal, *decision.crew_proposals]:
                    if draft is not None and draft.template_id != "schedule":
                        draft.requested_notifications = True
            if payload.crew_id and decision.crew_proposal:
                decision.target_crew_id = payload.crew_id
            with self.factory() as session:
                thread = session.get(CoordinatorSession, payload.session_id)
                if not thread or thread.active_request != payload.request_id:
                    raise HTTPException(409, "This planning turn expired. Send a new message.")
                current = session.get(CrewInstance, current_id) if current_id else None
                if current_id and (not current or current.runtime_generation != generation):
                    raise HTTPException(
                        409, "This Crew changed while Mavo was planning. Send a new message."
                    )
                if decision.task:
                    offered = {item.id for item in gateway_turn.context.available_crews}
                    if any(
                        s.kind == "crew" and s.crew_id not in offered for s in decision.task.steps
                    ):
                        raise PlanError("A task step names a Crew that does not exist")
                proposal = decision.crew_proposal
                if (
                    proposal
                    and not decision.target_crew_id
                    and not decision.crew_proposals
                    and current
                    and current.status == "draft"
                    and (
                        current.template_id == proposal.template_id
                        or base_name(current.name) == base_name(proposal.name)
                    )
                ):
                    decision.target_crew_id = current.id
                batch = []
                if decision.crew_proposals:
                    batch = self.apply_batch(session, decision, thread.crew_ids or [])
                    crew = batch[0]
                else:
                    crew = self.apply_plan(session, decision, current)
                if decision.delegate_to:
                    crew = session.get(CrewInstance, decision.delegate_to)
                    if not crew or decision.delegate_to not in {
                        item.id for item in gateway_turn.context.available_crews
                    }:
                        raise ValueError("Delegation target is unavailable")
                    self.queue_reply(session, crew, decision.instruction)
                if decision.connect:
                    self.connect_crews(session, decision.connect, gateway_turn.context)
                for fact in decision.remember:
                    add_fact(session, fact, source="Chat")
                reply = tidy_reply(decision)
                if crew:
                    thread.crew_id = crew.id
                    thread.crew_ids = [c.id for c in batch] if batch else [crew.id]
                if len(batch) > 1:
                    reply += "\n\n" + "\n".join(
                        "\u2022 " + item.name + " (draft)" for item in batch[1:]
                    )
                thread.messages = [*messages, {"role": "assistant", "content": reply}][-24:]
                thread.active_request = None
                thread.lease_until = None
                conversation = conversation_for(session, thread)
                if payload.session_id == "main" or conversation:
                    if not conversation:
                        conversation = Conversation(title="Mavo")
                        session.add(conversation)
                        session.flush()
                        setting = session.get(WorkspaceSetting, "core-conversation")
                        if setting:
                            setting.value = {"id": conversation.id}
                        else:
                            session.add(
                                WorkspaceSetting(
                                    key="core-conversation", value={"id": conversation.id}
                                )
                            )
                        thread.conversation_id = conversation.id
                    if conversation.title == "New chat" and not conversation.messages:
                        conversation.title = " ".join(payload.message.split())[:80]
                    session.add_all(
                        [
                            Message(
                                conversation_id=conversation.id,
                                role="user",
                                content=payload.message,
                                provider="airouter",
                                model=model,
                                created_at=asked_at,
                            ),
                            Message(
                                conversation_id=conversation.id,
                                role="assistant",
                                content=reply,
                                provider="airouter",
                                model=model,
                                created_at=asked_at + timedelta(milliseconds=1),
                            ),
                        ]
                    )
                    conversation.updated_at = utcnow()
                result = {
                    "reply": reply,
                    "questions": decision.questions,
                    "crew_id": crew.id if crew else None,
                    "model": model,
                    "request_id": payload.request_id,
                }
                if decision.task:
                    # A job to do now. It is started once this turn is saved (below): its own
                    # database writes cannot overlap this transaction.
                    task_run_id = str(uuid4())
                    result["task_run_id"] = task_run_id
                    task_chat = conversation.id if conversation else None
                session.get(CoordinatorTurn, payload.request_id).result = result
                session.commit()
                # Message rows were inserted by foreign-key ID; refresh the
                # already-loaded relationship before returning this reply.
                session.expire_all()
                answer = self.response(session, result, payload.session_id)
            if decision.task:
                # Mavo itself reports the result on the desktop when the task ends.
                self.runtime.task_runner.start(
                    decision.task, payload.message, task_run_id, task_chat
                )
            return answer
        except Exception:
            with self.factory() as session:
                session.execute(
                    update(CoordinatorSession)
                    .where(
                        CoordinatorSession.id == payload.session_id,
                        CoordinatorSession.active_request == payload.request_id,
                    )
                    .values(active_request=None, lease_until=None)
                )
                session.commit()
            raise

    def mavo_agent(self, message: str):
        from mavo.mavo_agents import decision_for
        from mavo.resource_pool import level

        if not message.lstrip().startswith("@"):
            return None
        with self.factory() as session:
            on = level(session, "open_data") != "off"
        return decision_for(message, on)

    def queue_reply(self, session, crew, message):
        stop_crew(session, crew)
        self.runtime.cancel(crew.id)
        replies = [*crew.observation.get("user_messages", []), message][-8:]
        observation = {
            **crew.observation,
            "user_messages": replies,
            "automation_steps": 0,
            "failures": 0,
            "question": None,
        }
        if observation.get("pending_google"):
            observation["pending_google"] = {
                **observation["pending_google"],
                "generation": crew.runtime_generation,
            }
        crew.observation = observation
        if crew.configuration_plan.get("draft"):
            crew.configuration_plan = {**crew.configuration_plan, "user_paused": False}
            self.reconcile(session, crew)
            if crew.configuration_plan["phase"] != "ready":
                return
        if not crew.route_id:
            raise HTTPException(409, "Choose an agent connection first")
        if self.runtime.actions.rules.paused(session):
            return
        crew.is_running = True
        crew.status = "active"
        crew.runtime_state = "waiting"
        crew.next_run_at = utcnow()  # answering it: it carries on now, whatever its usual time
        crew.last_runtime_error = None

    @staticmethod
    def thread_crews(session, thread):
        ids = list(thread.crew_ids or []) if thread else []
        return [c for c in (session.get(CrewInstance, i) for i in ids) if c]

    def response(self, session, result, session_id):
        thread = session.get(CoordinatorSession, session_id)
        crew = session.get(CrewInstance, result["crew_id"]) if result["crew_id"] else None
        conversation = conversation_for(session, thread)
        return {
            **result,
            "crew_id": crew.id if crew and crew.status != "draft" else None,
            "crews": [
                CrewView.model_validate(item).model_dump(mode="json")
                for item in self.thread_crews(session, thread)
            ],
            "draft_id": crew.id if crew and crew.status == "draft" else None,
            "session_id": session_id,
            "crew": CrewView.model_validate(crew).model_dump(mode="json")
            if crew and crew.status != "draft"
            else None,
            "draft": CrewView.model_validate(crew).model_dump(mode="json")
            if crew and crew.status == "draft"
            else None,
            "messages": thread.messages if thread else [],
            "conversation": core_chat_view(session, thread) if conversation else None,
        }


def build_planner_api(planner, get_session):
    router = APIRouter()

    @router.get("/core/status")
    async def status():
        return await planner.coordinator.status()

    @router.get("/core/conversations")
    def conversations(session=Depends(get_session)):
        threads = session.scalars(
            select(CoordinatorSession)
            .join(Conversation, Conversation.id == CoordinatorSession.conversation_id)
            .order_by(Conversation.updated_at.desc(), Conversation.id)
        ).all()
        return [core_chat_view(session, thread) for thread in threads]

    @router.post("/core/conversations", status_code=201)
    def create_conversation(session=Depends(get_session)):
        conversation = Conversation(title="New chat")
        session.add(conversation)
        session.flush()
        thread = CoordinatorSession(id=str(uuid4()), conversation_id=conversation.id, messages=[])
        session.add(thread)
        session.commit()
        return core_chat_view(session, thread)

    @router.post("/core/turn")
    async def turn(payload: CoordinatorRequest):
        try:
            return await planner.turn(payload)
        except CoordinatorUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc
        except HTTPException:
            raise
        except PlanError as exc:
            raise HTTPException(409, str(exc)) from None
        except (ValueError, IntegrityError):
            # The same sentence covers a genuine conflict and a real fault; the log keeps which.
            logger.warning("Planning turn rejected", exc_info=True)
            raise HTTPException(409, "The Crew setup changed. Try a fresh message.") from None
        except Exception:
            logger.exception("Planning turn failed")
            raise HTTPException(
                502, "Mavo could not finish that message. Nothing was changed; try again."
            ) from None

    @router.post("/crew/{crew_id}/access", response_model=CrewView)
    def access(crew_id: str, payload: CrewAccess, session=Depends(get_session)):
        crew = session.get(CrewInstance, crew_id)
        if not crew:
            raise HTTPException(404, "Crew not found")
        if not crew.configuration_plan.get("draft"):
            raise HTTPException(409, "Use this Crew's existing configuration editor.")
        if (
            crew.configuration_plan.get("questions")
            or crew.configuration_plan.get("phase") == "needs_input"
        ):
            raise HTTPException(409, "Answer all setup questions before configuring access.")
        draft = CrewDraft.model_validate(crew.configuration_plan["draft"])
        required = planner.requirements(draft)
        if set(payload.connector_capabilities) - set(draft.requested_connector_capabilities):
            raise HTTPException(400, "Choose only this Crew's requested connector access")
        if not set(payload.capabilities) <= required:
            raise HTTPException(400, "Choose only this Crew's requested resources")
        expected = {a.operation for a in draft.requested_actions}
        if not set(payload.actions) <= expected:
            raise HTTPException(400, "Choose only this Crew's requested actions")
        stop_crew(session, crew)
        planner.runtime.cancel(crew.id)
        from mavo.reminders import set_notification_access

        set_notification_access(
            session, crew, payload.notifications and draft.requested_notifications
        )
        crew.permissions = [p for p in crew.permissions if p == "notifications:show"] + [
            "model:invoke",
            *["google:" + cap for cap in payload.capabilities],
            *payload.connector_capabilities,
        ]
        if payload.weather and draft.requested_weather:
            crew.permissions = [*crew.permissions, "weather:read"]
        if (
            payload.device_metrics
            and draft.requested_device_metrics
            and draft.template_id == "computer"
        ):
            crew.permissions = [*crew.permissions, "device:read"]
        if (
            payload.temp_cleanup
            and draft.template_id == "computer"
            and draft.device_monitor
            and draft.device_monitor.clean_temp_days
        ):
            crew.permissions = [*crew.permissions, "device:clean_temp"]
        for operation in expected:
            saved_rule = planner.runtime.actions.rules.read(session, operation, crew.id)
            rule = payload.actions.get(operation) or Rule.model_validate(
                {k: saved_rule[k] for k in Rule.model_fields if k in saved_rule}
            )
            RuleChange(operation=operation, crew_id=crew.id, rule=rule)
            key = planner.runtime.actions.rules.key(operation, crew.id)
            data = {**rule.model_dump(), "revision": str(uuid4())}
            saved = session.get(WorkspaceSetting, key)
            if saved:
                saved.value = data
            else:
                session.add(WorkspaceSetting(key=key, value=data))
            if rule.enabled and operation.split(".")[0] in LOCAL_SERVICES:
                crew.permissions = [*crew.permissions, "action:" + operation]
        if payload.route_id:
            if payload.route_id not in {r.id for r in planner.agent_routes(session)}:
                raise HTTPException(400, "Choose an available agent connection")
            crew.route_id = payload.route_id
        crew.resources = sorted({"google:" + cap.split(":")[0] for cap in payload.capabilities})
        crew.configuration_plan = {
            **crew.configuration_plan,
            "denied_resources": sorted(required - set(payload.capabilities)),
            "user_paused": False,
            "setup_confirmed": True,
        }
        session.flush()
        planner.reconcile(session, crew)
        planner.discard_stale_drafts(session, crew)
        session.commit()
        session.refresh(crew)
        return crew

    @router.post("/crew/{crew_id}/reply", response_model=CrewView)
    def reply(crew_id: str, payload: CrewReply, session=Depends(get_session)):
        crew = session.get(CrewInstance, crew_id)
        if not crew:
            raise HTTPException(404, "Crew not found")
        if crew.configuration_plan.get("phase") in {"needs_input", "needs_access"}:
            raise HTTPException(409, "Finish this Crew's setup conversation first.")
        if not crew.route_id:
            raise HTTPException(409, "Choose an agent connection first")
        planner.queue_reply(session, crew, payload.message)
        session.commit()
        session.refresh(crew)
        return crew

    return router
