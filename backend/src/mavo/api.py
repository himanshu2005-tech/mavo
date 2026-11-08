from __future__ import annotations

import logging
from datetime import timedelta
from time import perf_counter
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from mavo import __version__
from mavo.core import core_conversation, proposal_view
from mavo.credentials import CredentialError, CredentialStore
from mavo.crew_access import google_grants, validate_trigger
from mavo.crew_engine import crew_system_prompt
from mavo.crew_templates import CREW_TEMPLATES, CREW_TEMPLATES_BY_ID
from mavo.crew_workers import CrewWorkerRuntime, first_run_at, stop_crew
from mavo.database import (
    Conversation,
    CoordinatorSession,
    CrewInstance,
    CrewProposal,
    CrewRun,
    ModelRoute,
    ProviderConfig,
    ReminderEvent,
    ResourceEvent,
    WorkspaceSetting,
    utcnow,
)
from mavo.google_auth import GoogleAuth
from mavo.model_router import PROVIDERS, ModelProviderError, ModelRouter
from mavo.reminders import create_due_reminders, parse_daily_time
from mavo.resource_access import DefaultResourceAccess, read_access, require_access, save_access
from mavo.resources.weather import OpenMeteoWeatherAdapter, WeatherResourceError
from mavo.schemas import (
    AppStatus,
    ConversationRename,
    ConversationView,
    CrewCreateRequest,
    CrewRunRequest,
    CrewRunView,
    CrewTemplateManifest,
    CrewUpdateRequest,
    CrewView,
    HandoverDecision,
    ModelRouteCreateRequest,
    ModelRouteUpdateRequest,
    ModelRouteView,
    ProviderConfigurationRequest,
    ProviderConfigurationView,
    ProviderId,
    ReminderView,
    RouteTestView,
    WeatherLocation,
    WeatherSnapshot,
)

logger = logging.getLogger("mavo.api")

SYSTEM_PROMPT = (
    "You are Mavo, a local-first personal AI assistant. Be concise and useful. "
    "You may explain and plan, but you have no tools or permission to take external actions "
    "in this chat. Never claim an action, connection, or system change occurred unless the "
    "application provides evidence."
)


def build_api(  # type: ignore[no-untyped-def]
    get_session,
    model_router: ModelRouter,
    credentials: CredentialStore,
    weather: OpenMeteoWeatherAdapter,
    crew_runtime: CrewWorkerRuntime,
    google: GoogleAuth,
) -> APIRouter:
    router = APIRouter()

    @router.get("/runtime/activity")
    def runtime_activity(response: Response, after: int | None = Query(None, ge=0)):
        response.headers["Cache-Control"] = "no-store"
        return crew_runtime.activity.snapshot(after)

    @router.get("/resources/access", response_model=DefaultResourceAccess)
    def resource_access(session: Session = Depends(get_session)):
        return read_access(session)

    @router.put("/resources/access", response_model=DefaultResourceAccess)
    def set_resource_access(
        payload: DefaultResourceAccess, session: Session = Depends(get_session)
    ):
        return save_access(session, payload, weather)

    @router.get("/resources/location", response_model=WeatherLocation | None)
    def device_location(session: Session = Depends(get_session)):
        require_access(session, "location")
        saved = session.get(WorkspaceSetting, "device-location")
        return saved.value if saved else None

    @router.put("/resources/location", response_model=WeatherLocation)
    def save_device_location(payload: WeatherLocation, session: Session = Depends(get_session)):
        require_access(session, "location")
        value = payload.model_copy(
            update={
                "name": "Current location",
                "source": "device",
                "latitude": round(payload.latitude, 3),
                "longitude": round(payload.longitude, 3),
            }
        )
        saved = session.get(WorkspaceSetting, "device-location")
        if saved:
            saved.value = value.model_dump()
        else:
            session.add(WorkspaceSetting(key="device-location", value=value.model_dump()))
        session.commit()
        return value

    @router.get("/status", response_model=AppStatus)
    async def get_status(session: Session = Depends(get_session)) -> AppStatus:
        defaults = {
            config.provider: config.default_model
            for config in session.scalars(select(ProviderConfig)).all()
        }
        return AppStatus(version=__version__, providers=await model_router.statuses(defaults))

    @router.get("/resources/weather", response_model=WeatherSnapshot)
    async def get_weather(
        location: str = Query(min_length=2, max_length=120),
        session: Session = Depends(get_session),
    ) -> WeatherSnapshot:
        require_access(session, "weather")
        try:
            result = await weather.read(location.strip())
            require_access(session, "weather")
            return result
        except WeatherResourceError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.put("/providers/{provider}", response_model=ProviderConfigurationView)
    def configure_provider(
        provider: ProviderId,
        payload: ProviderConfigurationRequest,
        session: Session = Depends(get_session),
    ) -> ProviderConfigurationView:
        if provider == "ollama":
            raise HTTPException(status_code=400, detail="Ollama does not use an API key")
        if provider not in PROVIDERS:
            raise HTTPException(status_code=404, detail="Unknown model provider")
        try:
            credentials.set(provider, payload.api_key.get_secret_value())
        except CredentialError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        config = session.get(ProviderConfig, provider)
        if config is None:
            config = ProviderConfig(provider=provider, default_model=payload.default_model)
            session.add(config)
        else:
            config.default_model = payload.default_model
            config.updated_at = utcnow()
        session.commit()
        return ProviderConfigurationView(
            provider=provider, default_model=config.default_model, has_credential=True
        )

    @router.get("/resources/weather/locations", response_model=list[WeatherLocation])
    async def search_weather_locations(
        query: str = Query(min_length=2, max_length=160),
        country_code: str | None = Query(default=None, pattern=r"^[A-Za-z]{2}$"),
        session: Session = Depends(get_session),
    ):
        require_access(session, "weather")
        try:
            result = await weather.search(query, country_code)
            require_access(session, "weather")
            return result
        except WeatherResourceError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.get("/resources/weather/location", response_model=WeatherLocation | None)
    def saved_weather_location(session: Session = Depends(get_session)):
        require_access(session, "weather")
        setting = session.get(WorkspaceSetting, "weather-location")
        if setting and setting.value.get("source") == "device":
            require_access(session, "location")
        return setting.value if setting else None

    @router.post("/resources/weather/forecast", response_model=WeatherSnapshot)
    async def weather_forecast(payload: WeatherLocation, session: Session = Depends(get_session)):
        require_access(session, "weather")
        if payload.source == "device":
            require_access(session, "location")
        try:
            snapshot = await weather.read_location(payload)
        except WeatherResourceError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        require_access(session, "weather")
        if payload.source == "device":
            require_access(session, "location")
        setting = session.get(WorkspaceSetting, "weather-location")
        value = payload.model_copy(
            update={
                "latitude": round(payload.latitude, 3),
                "longitude": round(payload.longitude, 3),
            }
        ).model_dump()
        if setting:
            setting.value = value
        else:
            session.add(WorkspaceSetting(key="weather-location", value=value))
        session.commit()
        return snapshot

    @router.delete("/providers/{provider}", status_code=204)
    def disconnect_provider(provider: ProviderId, session: Session = Depends(get_session)) -> None:
        if provider == "ollama":
            raise HTTPException(status_code=400, detail="Ollama is a local runtime")
        try:
            credentials.delete(provider)
        except CredentialError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        config = session.get(ProviderConfig, provider)
        if config is not None:
            session.delete(config)
            session.commit()

    def route_view(route: ModelRoute) -> ModelRouteView:
        has_credential = route.provider == "ollama" or bool(
            route.credential_id and credentials.get(route.credential_id)
        )
        return ModelRouteView(
            id=route.id,
            name=route.name,
            provider=route.provider,  # type: ignore[arg-type]
            model=route.model,
            has_credential=has_credential,
            created_at=route.created_at,
            updated_at=route.updated_at,
        )

    def get_route_or_404(route_id: str, session: Session) -> ModelRoute:
        route = session.get(ModelRoute, route_id)
        if route is None:
            raise HTTPException(status_code=404, detail="Model route not found")
        return route

    def require_unique_route_name(
        session: Session, name: str, excluding_id: str | None = None
    ) -> None:
        statement = select(ModelRoute.id).where(func.lower(ModelRoute.name) == name.lower())
        if excluding_id is not None:
            statement = statement.where(ModelRoute.id != excluding_id)
        if session.scalar(statement) is not None:
            raise HTTPException(status_code=409, detail="A route with this name already exists")

    @router.get("/routes", response_model=list[ModelRouteView])
    def list_routes(session: Session = Depends(get_session)) -> list[ModelRouteView]:
        routes = session.scalars(select(ModelRoute).order_by(ModelRoute.created_at)).all()
        return [route_view(route) for route in routes]

    @router.post("/routes", response_model=ModelRouteView, status_code=201)
    def create_route(
        payload: ModelRouteCreateRequest, session: Session = Depends(get_session)
    ) -> ModelRouteView:
        require_unique_route_name(session, payload.name)
        if payload.provider != "ollama" and payload.api_key is None:
            raise HTTPException(status_code=400, detail="An API key is required for cloud routes")
        route_id = str(uuid4())
        credential_id = None if payload.provider == "ollama" else f"route-{route_id}"
        if credential_id and payload.api_key:
            try:
                credentials.set(credential_id, payload.api_key.get_secret_value())
            except CredentialError as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc
        route = ModelRoute(
            id=route_id,
            name=payload.name,
            provider=payload.provider,
            model=payload.model,
            credential_id=credential_id,
        )
        session.add(route)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            if credential_id:
                credentials.delete(credential_id)
            raise HTTPException(status_code=409, detail="Route name must be unique") from exc
        session.refresh(route)
        return route_view(route)

    @router.patch("/routes/{route_id}", response_model=ModelRouteView)
    def update_route(
        route_id: str,
        payload: ModelRouteUpdateRequest,
        session: Session = Depends(get_session),
    ) -> ModelRouteView:
        route = get_route_or_404(route_id, session)
        changes = payload.model_dump(exclude_unset=True, exclude={"api_key"})
        if "name" in changes:
            require_unique_route_name(session, changes["name"], route.id)
        new_provider = changes.get("provider", route.provider)
        api_key = payload.api_key.get_secret_value() if payload.api_key else None
        if (
            new_provider != "ollama"
            and not api_key
            and (route.provider == "ollama" or new_provider != route.provider)
        ):
            raise HTTPException(status_code=400, detail="Enter an API key when changing provider")
        old_credential_id = route.credential_id
        if new_provider == "ollama":
            route.credential_id = None
        elif api_key:
            route.credential_id = route.credential_id or f"route-{route.id}"
            try:
                credentials.set(route.credential_id, api_key)
            except CredentialError as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc
        for field, value in changes.items():
            setattr(route, field, value)
        route.updated_at = utcnow()
        session.execute(
            update(CrewInstance)
            .where(CrewInstance.route_id == route.id)
            .values(
                provider=route.provider,
                model=route.model,
            )
        )
        session.commit()
        if old_credential_id and route.credential_id is None:
            credentials.delete(old_credential_id)
        session.refresh(route)
        return route_view(route)

    @router.post("/routes/{route_id}/test", response_model=RouteTestView)
    async def test_route(route_id: str, session: Session = Depends(get_session)) -> RouteTestView:
        route = get_route_or_404(route_id, session)
        start = perf_counter()
        try:
            response = await model_router.chat(
                route.provider,
                route.model,  # type: ignore[arg-type]
                [{"role": "user", "content": "Reply with: Mavo connection OK"}],
                "This is a connection test. Reply in one short sentence.",
                route.credential_id,
            )
        except (ModelProviderError, CredentialError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return RouteTestView(
            route_id=route.id,
            model=route.model,
            response=response,
            latency_ms=round((perf_counter() - start) * 1000),
        )

    @router.delete("/routes/{route_id}", status_code=204)
    def delete_route(route_id: str, session: Session = Depends(get_session)) -> Response:
        route = get_route_or_404(route_id, session)
        if session.scalar(select(CrewInstance.id).where(CrewInstance.route_id == route.id)):
            raise HTTPException(
                status_code=409, detail="This route is assigned to a Crew; change the Crew first"
            )
        credential_id = route.credential_id
        if credential_id == route.provider:
            legacy_config = session.get(ProviderConfig, route.provider)
            if legacy_config is not None:
                session.delete(legacy_config)
        session.delete(route)
        session.commit()
        if credential_id:
            credentials.delete(credential_id)
        return Response(status_code=204)

    @router.get("/conversations", response_model=list[ConversationView])
    def list_conversations(session: Session = Depends(get_session)) -> list[Conversation]:
        statement = (
            select(Conversation)
            .options(selectinload(Conversation.messages))
            .order_by(Conversation.updated_at.desc())
        )
        return list(session.scalars(statement).all())

    @router.get("/conversations/{conversation_id}", response_model=ConversationView)
    def get_conversation(
        conversation_id: str, session: Session = Depends(get_session)
    ) -> Conversation:
        conversation = session.scalar(
            select(Conversation)
            .where(Conversation.id == conversation_id)
            .options(selectinload(Conversation.messages))
        )
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        return conversation

    @router.delete("/conversations/{conversation_id}", status_code=204)
    def delete_conversation(
        conversation_id: str,
        session: Session = Depends(get_session),
    ) -> Response:
        conversation = session.get(Conversation, conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        from mavo.conversations import claim_idle_chat

        claim_idle_chat(session, conversation_id)
        setting = session.get(WorkspaceSetting, "core-conversation")
        if setting and setting.value.get("id") == conversation_id:
            session.delete(setting)
        session.delete(conversation)
        session.commit()
        return Response(status_code=204)

    @router.patch("/conversations/{conversation_id}")
    def rename_conversation(
        conversation_id: str, payload: ConversationRename, session: Session = Depends(get_session)
    ):
        from mavo.conversations import core_chat_view

        conversation = session.get(Conversation, conversation_id)
        if not conversation:
            raise HTTPException(404, "Chat not found")
        conversation.title = payload.title
        session.commit()
        thread = session.scalar(
            select(CoordinatorSession).where(CoordinatorSession.conversation_id == conversation_id)
        )
        return (
            core_chat_view(session, thread)
            if thread
            else ConversationView.model_validate(conversation)
        )

    @router.get("/crew/templates", response_model=list[CrewTemplateManifest])
    def list_crew_templates() -> list[CrewTemplateManifest]:
        return CREW_TEMPLATES

    @router.get("/crew", response_model=list[CrewView])
    def list_crew(session: Session = Depends(get_session)) -> list[CrewInstance]:
        statement = (
            select(CrewInstance)
            .where(CrewInstance.status != "draft", CrewInstance.hidden.is_(False))
            .order_by(CrewInstance.created_at.desc())
        )
        return list(session.scalars(statement).all())

    @router.get("/crew-drafts", response_model=list[CrewView])
    def list_crew_drafts(session: Session = Depends(get_session)):
        return list(
            session.scalars(
                select(CrewInstance)
                .where(CrewInstance.status == "draft")
                .order_by(CrewInstance.updated_at.desc())
            ).all()
        )

    def require_unique_crew_name(
        session: Session, name: str, excluding_id: str | None = None
    ) -> None:
        statement = select(CrewInstance.id).where(func.lower(CrewInstance.name) == name.lower())
        if excluding_id is not None:
            statement = statement.where(CrewInstance.id != excluding_id)
        if session.scalar(statement) is not None:
            raise HTTPException(status_code=409, detail="A Crew with this name already exists")

    def prepare_crew(payload: CrewCreateRequest, session: Session) -> CrewInstance:
        template = CREW_TEMPLATES_BY_ID.get(payload.template_id)
        if template is None:
            raise HTTPException(status_code=400, detail="Unknown Crew template")
        require_unique_crew_name(session, payload.name)
        model_route = get_route_or_404(payload.route_id, session)
        if payload.allow_device_metrics and payload.template_id != "computer":
            raise HTTPException(status_code=400, detail="Device metrics require a Computer Crew")
        if payload.device_monitor and payload.template_id != "computer":
            raise HTTPException(400, "Device monitor settings require a Computer Crew")
        trigger = payload.trigger_type or (
            "daily" if payload.template_id == "schedule" else "interval"
        )
        resources, permissions = google_grants(
            payload.google_resources, payload.google_capabilities
        )
        validate_trigger(trigger, [p.split(":")[1] for p in permissions if p.endswith(":read")])
        crew = CrewInstance(
            template_id=template.id,
            name=payload.name,
            responsibilities=payload.responsibilities,
            autonomy_mode=payload.autonomy_mode,
            status="active",
            provider=model_route.provider,
            model=model_route.model,
            route_id=model_route.id,
            max_model_calls=payload.max_model_calls,
            review_interval_minutes=payload.review_interval_minutes,
            daily_call_limit=payload.daily_call_limit,
            schedule_time=(
                payload.daily_triggers[0].time
                if payload.daily_triggers
                else payload.schedule_time or parse_daily_time(payload.responsibilities)
            ),
            daily_triggers=[rule.model_dump() for rule in payload.daily_triggers],
            reminder_text=payload.reminder_text or payload.responsibilities,
            trigger_type=trigger,
            event_cursor=session.scalar(select(func.max(ResourceEvent.id))) or 0,
            mail_sender=payload.mail_sender,
            mail_subject=payload.mail_subject,
            resources=resources,
            configuration_plan={"device_monitor": payload.device_monitor.model_dump()}
            if payload.device_monitor
            else {},
            permissions=list(template.default_permissions)
            + (["device:read"] if payload.allow_device_metrics else [])
            + (["weather:read"] if payload.allow_weather else [])
            + (["notifications:show"] if payload.allow_notifications else [])
            + permissions,
            # Account connections never implicitly grant these capabilities to a Crew.
            triggers=[],
        )
        session.add(crew)
        crew.permissions = [*crew.permissions, *payload.connector_capabilities]
        return crew

    @router.post("/crew", response_model=CrewView, status_code=201)
    def create_crew(
        payload: CrewCreateRequest, session: Session = Depends(get_session)
    ) -> CrewInstance:
        crew = prepare_crew(payload, session)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise HTTPException(status_code=409, detail="Crew name must be unique") from exc
        session.refresh(crew)
        return crew

    def safe_limits(crew: CrewInstance, changes: dict) -> None:
        """AI-call limits an agent can work within: at least two calls a run (one to decide,
        one to finish) and a day that holds at least one whole run. A limit raised clears the
        note about the old one, and the agent tries again."""
        from mavo.crew_workers import MIN_CALLS_PER_RUN

        if "max_model_calls" in changes:
            changes["max_model_calls"] = max(changes["max_model_calls"], MIN_CALLS_PER_RUN)
        per_run = changes.get("max_model_calls", crew.max_model_calls)
        if "daily_call_limit" in changes or "max_model_calls" in changes:
            changes["daily_call_limit"] = max(
                changes.get("daily_call_limit", crew.daily_call_limit), per_run
            )
            if (crew.observation or {}).get("limit"):
                kept = {k: v for k, v in crew.observation.items() if k != "limit"}
                if (crew.observation.get("question") or "").startswith(
                    ("It needed one more step", "It used all")
                ) or "model-call budget" in (crew.observation.get("question") or ""):
                    kept.pop("question", None)
                crew.observation = kept
            elif "model-call budget" in ((crew.observation or {}).get("question") or ""):
                crew.observation = {k: v for k, v in crew.observation.items() if k != "question"}

    def set_line(session: Session, crew: CrewInstance, changes: dict) -> None:
        """Its place in a line of agents: where its work comes from, whether it reviews that
        work (and how many times it may send it back), and whether it asks the person first."""
        from mavo.crew_planner import CrewPlanner

        line = {
            key: changes.pop(key)
            for key in ("upstream", "review", "approve_first", "max_rounds")
            if key in changes
        }
        if not line:
            return
        if "upstream" in line:
            ids = list(dict.fromkeys(line["upstream"] or []))
            for other_id in ids:
                other = session.get(CrewInstance, other_id)
                if not other or other.id == crew.id or other.hidden or other.status == "draft":
                    raise HTTPException(400, "Choose your own agents to take work from")
            if CrewPlanner.would_cycle(session, crew.id, ids):
                raise HTTPException(409, "That would make these agents wait on each other forever")
            line["upstream"] = ids
        crew.configuration_plan = {**crew.configuration_plan, **line}
        from mavo.pipelines import ORPHAN_MARK

        # Given a new place after the agent it worked for was deleted: that note is answered.
        if line.get("upstream") and ORPHAN_MARK in ((crew.observation or {}).get("question") or ""):
            crew.observation = {k: v for k, v in crew.observation.items() if k != "question"}
            if crew.is_running and crew.runtime_state == "needs_input":
                crew.runtime_state = "waiting_event" if crew.trigger_type == "manual" else "waiting"

    def get_crew_or_404(crew_id: str, session: Session) -> CrewInstance:
        crew = session.get(CrewInstance, crew_id)
        if crew is None:
            raise HTTPException(status_code=404, detail="Crew not found")
        return crew

    def clear_fallback_retry(crew: CrewInstance) -> None:
        observation = {k: v for k, v in crew.observation.items() if k != "fallback_route"}
        entry = observation.get("fallback")
        if isinstance(entry, dict) and entry.get("status") == "queued":
            observation["fallback"] = {**entry, "status": "cancelled"}
        crew.observation = observation

    @router.patch("/crew/{crew_id}", response_model=CrewView)
    def update_crew(
        crew_id: str,
        payload: CrewUpdateRequest,
        session: Session = Depends(get_session),
    ) -> CrewInstance:
        crew = get_crew_or_404(crew_id, session)
        changes = payload.model_dump(exclude_unset=True)
        if "fallback_route_id" in changes:
            fallback_id = changes.pop("fallback_route_id")
            if fallback_id:
                fallback = get_route_or_404(fallback_id, session)
                primary = get_route_or_404(changes.get("route_id", crew.route_id), session)
                if primary.provider != "ollama" or fallback.provider == "ollama":
                    raise HTTPException(400, "Choose a cloud fallback for an Ollama agent")
            crew.configuration_plan = {
                **crew.configuration_plan,
                "fallback_route_id": fallback_id,
            }
            # Revoke queued fallbacks too. Generation fencing below stops in-flight work.
            clear_fallback_retry(crew)
        set_line(session, crew, changes)
        safe_limits(crew, changes)
        connector_capabilities = changes.pop("connector_capabilities", None)
        if connector_capabilities is not None:
            crew.permissions = [
                p for p in crew.permissions if not p.startswith(("github:", "feeds:"))
            ] + connector_capabilities
        if crew.status == "draft":
            raise HTTPException(409, "Finish this draft in Chat and select Verify & add.")
        google_resources = changes.pop("google_resources", None)
        google_capabilities = changes.pop("google_capabilities", None)
        if google_resources is not None or google_capabilities is not None:
            resources, permissions = google_grants(
                google_resources or [], google_capabilities or []
            )
            crew.resources = [r for r in crew.resources if not r.startswith("google:")] + resources
            crew.permissions = [
                p for p in crew.permissions if not p.startswith("google:")
            ] + permissions
        validate_trigger(
            changes.get("trigger_type", crew.trigger_type),
            [
                p.split(":")[1]
                for p in crew.permissions
                if p.startswith("google:") and p.endswith(":read")
            ],
        )
        device_allowed = changes.pop("allow_device_metrics", None)
        cleanup_allowed = changes.pop("allow_temp_cleanup", None)
        if cleanup_allowed is not None:
            if cleanup_allowed and crew.template_id != "computer":
                raise HTTPException(400, "Freeing space is done by a Computer Crew")
            crew.permissions = [p for p in crew.permissions if p != "device:clean_temp"] + (
                ["device:clean_temp"] if cleanup_allowed else []
            )
        notifications_allowed = changes.pop("allow_notifications", None)
        if notifications_allowed is not None:
            from mavo.reminders import set_notification_access

            set_notification_access(session, crew, notifications_allowed)
        if "device_monitor" in changes:
            monitor = changes.pop("device_monitor")
            if monitor and crew.template_id != "computer":
                raise HTTPException(400, "Device monitor settings require a Computer Crew")
            crew.configuration_plan = {**crew.configuration_plan, "device_monitor": monitor}
            crew.observation = {}
        weather_allowed = changes.pop("allow_weather", None)
        if weather_allowed is not None:
            crew.permissions = [p for p in crew.permissions if p != "weather:read"] + (
                ["weather:read"] if weather_allowed else []
            )
        if device_allowed is not None:
            if device_allowed and crew.template_id != "computer":
                raise HTTPException(
                    status_code=400, detail="Device metrics require a Computer Crew"
                )
            crew.permissions = [p for p in crew.permissions if p != "device:read"] + (
                ["device:read"] if device_allowed else []
            )
        if "name" in changes:
            require_unique_crew_name(session, changes["name"], crew.id)
        if changes.get("route_id"):
            model_route = get_route_or_404(changes["route_id"], session)
            crew.provider = model_route.provider
            crew.model = model_route.model
            clear_fallback_retry(crew)
            if model_route.provider != "ollama":
                crew.configuration_plan = {**crew.configuration_plan, "fallback_route_id": None}
        if (
            "responsibilities" in changes
            and "schedule_time" not in changes
            and not crew.schedule_time
        ):
            changes["schedule_time"] = parse_daily_time(changes["responsibilities"])
        if changes.get("daily_triggers"):
            changes["schedule_time"] = changes["daily_triggers"][0]["time"]
            changes.setdefault("trigger_type", "daily")
        for field, value in changes.items():
            setattr(crew, field, value)
        if (
            google_resources is not None
            or google_capabilities is not None
            or any(field in changes for field in ("trigger_type", "mail_sender", "mail_subject"))
        ):
            crew.observation = {
                key: value for key, value in crew.observation.items() if key != "pending_google"
            }
            crew.checkpoint = None
            crew.event_cursor = session.scalar(select(func.max(ResourceEvent.id))) or 0
        if changes.get("status") == "paused":
            stop_crew(session, crew)
            crew_runtime.cancel(crew.id)
        elif crew.is_running:
            # Unsticking it (it needed you, or was out of today's calls) finishes that work now;
            # otherwise a timed agent keeps its time instead of working the moment it is saved.
            stuck = crew.runtime_state in {"needs_input", "budget_paused", "error"}
            crew.runtime_generation += 1
            crew.runtime_state = "waiting"
            crew.next_run_at = utcnow() if stuck else first_run_at(crew, utcnow())
            crew.last_runtime_error = None
            if "responsibilities" in changes:
                crew.checkpoint = None
            crew_runtime.cancel(crew.id)
        crew.updated_at = utcnow()
        if not crew.is_running and changes.get("status") != "paused":
            crew.runtime_generation += 1
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise HTTPException(status_code=409, detail="Crew name must be unique") from exc
        session.refresh(crew)
        return crew

    @router.post("/crew/{crew_id}/handover", response_model=CrewView)
    def answer_handover(
        crew_id: str, payload: HandoverDecision, session: Session = Depends(get_session)
    ) -> CrewInstance:
        """The person's answer to work held for them: approve it (it goes on) or send it back
        to the agent that made it, with a note."""
        from mavo.pipelines import decide

        crew = get_crew_or_404(crew_id, session)
        try:
            decide(session, crew, payload.approve, payload.note)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        crew.updated_at = utcnow()
        session.commit()
        session.refresh(crew)
        return crew

    @router.delete("/crew/{crew_id}", status_code=204)
    def delete_crew(crew_id: str, session: Session = Depends(get_session)) -> Response:
        crew = get_crew_or_404(crew_id, session)
        stop_crew(session, crew)
        crew_runtime.cancel(crew_id)
        from mavo.pipelines import unlink_removed

        unlink_removed(session, crew)  # no member or downstream agent is left waiting on it
        for policy in session.scalars(
            select(WorkspaceSetting).where(
                WorkspaceSetting.key.startswith("action:" + crew_id + ":")
            )
        ).all():
            session.delete(policy)
        session.delete(crew)
        session.commit()
        return Response(status_code=204)

    @router.post("/crew/{crew_id}/start", response_model=CrewView)
    async def start_worker(crew_id: str, session: Session = Depends(get_session)) -> CrewInstance:
        crew = get_crew_or_404(crew_id, session)
        if crew.configuration_plan.get("draft"):
            crew_runtime.planner.reconcile(session, crew)
            if crew.configuration_plan.get("phase") != "ready":
                session.commit()
                raise HTTPException(409, "Finish this Crew's setup questions and access first.")
        if crew_runtime.actions.rules.paused(session):
            raise HTTPException(409, "Automations are paused. Resume them in Actions first.")
        if crew.is_running:
            session.commit()
            return crew
        if "model:invoke" not in crew.permissions:
            raise HTTPException(status_code=403, detail="Model access has been revoked")
        from mavo.google_auth import GoogleConnectionError
        from mavo.resources.google import EVENT_RESOURCES

        resource = EVENT_RESOURCES.get(crew.trigger_type)
        if resource:
            validate_trigger(
                crew.trigger_type,
                [
                    p.split(":")[1]
                    for p in crew.permissions
                    if p.startswith("google:") and p.endswith(":read")
                ],
            )
            try:
                await crew_runtime.google_pool.read(resource)
            except GoogleConnectionError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        route = get_route_or_404(crew.route_id or "", session)
        if route.provider != "ollama" and not credentials.get(route.credential_id or ""):
            raise HTTPException(
                status_code=409, detail="Add a key to this Crew's model route first"
            )
        if crew.configuration_plan.get("draft"):
            crew.configuration_plan = {**crew.configuration_plan, "user_paused": False}
        crew.is_running = True
        crew.status = "active"
        crew.runtime_generation += 1
        crew.runtime_state = "waiting"
        crew.last_runtime_error = None
        # A Crew that works at a time of day ("shut down at 10:55") first works at that time,
        # not when it is added (it ran at 10:43, found nothing to do, and spent its budget).
        crew.next_run_at = first_run_at(crew, utcnow())
        crew.observation = {
            **crew.observation,
            "failures": 0,
            "automation_steps": 0,
            "question": None,
        }
        crew.event_cursor = session.scalar(select(func.max(ResourceEvent.id))) or 0
        crew.observation = {
            key: value for key, value in crew.observation.items() if not key.startswith("pending_")
        }
        crew.updated_at = utcnow()
        session.commit()
        session.refresh(crew)
        return crew

    @router.post("/crew/{crew_id}/stop", response_model=CrewView)
    def stop_worker(crew_id: str, session: Session = Depends(get_session)) -> CrewInstance:
        crew = get_crew_or_404(crew_id, session)
        if crew.is_running:
            stop_crew(session, crew)
            session.execute(
                update(ReminderEvent)
                .where(
                    ReminderEvent.crew_id == crew_id,
                    ReminderEvent.status == "pending",
                )
                .values(status="cancelled")
            )
            crew.updated_at = utcnow()
            session.commit()
            crew_runtime.cancel(crew_id)
        session.refresh(crew)
        return crew

    @router.post("/crew/{crew_id}/check", response_model=CrewView)
    def check_worker(crew_id: str, session: Session = Depends(get_session)) -> CrewInstance:
        crew = get_crew_or_404(crew_id, session)
        if not crew.is_running:
            raise HTTPException(status_code=409, detail="Run this Crew first")
        if crew.runtime_state == "working":
            raise HTTPException(status_code=409, detail="This Crew is already working")
        crew.runtime_state = "waiting"
        crew.last_runtime_error = None
        crew.next_run_at = utcnow()
        crew.observation = {**crew.observation, "failures": 0, "automation_steps": 0}
        session.commit()
        session.refresh(crew)
        return crew

    @router.get("/crew-runs", response_model=list[CrewRunView])
    def list_runs(
        crew_id: str | None = None,
        session: Session = Depends(get_session),
    ) -> list[CrewRun]:
        query = select(CrewRun).order_by(CrewRun.created_at.desc()).limit(100)
        if crew_id:
            query = query.where(CrewRun.crew_id == crew_id)
        return list(session.scalars(query).all())

    @router.post("/crew/{crew_id}/run", response_model=CrewRunView)
    async def run_crew(
        crew_id: str,
        payload: CrewRunRequest,
        session: Session = Depends(get_session),
    ) -> CrewRun:
        crew = get_crew_or_404(crew_id, session)
        if crew.status != "active":
            raise HTTPException(status_code=409, detail="Enable this Crew before running a session")
        if "model:invoke" not in crew.permissions or crew.max_model_calls < 1:
            raise HTTPException(status_code=403, detail="This Crew cannot invoke a model")
        if not crew.route_id:
            raise HTTPException(status_code=409, detail="Assign a model route first")
        route = get_route_or_404(crew.route_id, session)
        run = CrewRun(
            crew_id=crew.id,
            crew_name=crew.name,
            route_name=route.name,
            provider=route.provider,
            model=route.model,
            prompt=payload.message,
            lease_until=utcnow() + timedelta(seconds=180),
        )
        session.add(run)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise HTTPException(
                status_code=409, detail="This Crew already has a running session"
            ) from exc
        with crew_runtime.activity.scope(crew.id):
            crew_runtime.activity.start_run(run.id, route)
            result_text = None
            run_error = None
            run_status = "completed"
            try:
                result_text = await model_router.chat(
                    route.provider,
                    route.model,  # type: ignore[arg-type]
                    [{"role": "user", "content": payload.message}],
                    crew_system_prompt(crew),
                    route.credential_id,
                )
            except (ModelProviderError, CredentialError) as exc:
                run_status = "failed"
                run_error = str(exc)
            except Exception:
                logger.exception("Crew session %s failed", run.id)
                run_status = "failed"
                run_error = "The session failed unexpectedly. Check the local backend log."
            session.execute(
                update(CrewRun)
                .where(
                    CrewRun.id == run.id,
                    CrewRun.status == "running",
                )
                .values(
                    status=run_status, result=result_text, error=run_error, finished_at=utcnow()
                )
            )
            session.commit()
            session.refresh(run)
            crew_runtime.activity.finish_run(run.id, run.status)
            return run

    @router.get("/reminders/due", response_model=list[ReminderView])
    def due_reminders(session: Session = Depends(get_session)) -> list[ReminderEvent]:
        return create_due_reminders(session)

    @router.get("/reminders", response_model=list[ReminderView])
    def reminder_history(session: Session = Depends(get_session)) -> list[ReminderEvent]:
        return list(
            session.scalars(
                select(ReminderEvent).order_by(ReminderEvent.created_at.desc()).limit(100)
            ).all()
        )

    @router.post("/reminders/{reminder_id}/dismiss", status_code=204)
    def dismiss_reminder(reminder_id: str, session: Session = Depends(get_session)) -> Response:
        reminder = session.get(ReminderEvent, reminder_id)
        if reminder is None:
            raise HTTPException(status_code=404, detail="Reminder not found")
        reminder.status = "dismissed"
        reminder.dismissed_at = utcnow()
        session.commit()
        return Response(status_code=204)

    @router.post("/chat")
    async def retired_chat():
        raise HTTPException(
            410, "Normal chat uses the publisher coordinator. Agent connections are for Crew only."
        )

    @router.get("/core/conversation")
    def get_core_conversation(
        conversation_id: str | None = None, session: Session = Depends(get_session)
    ):
        from mavo.conversations import chat_drafts, conversation_for

        thread = (
            session.scalar(
                select(CoordinatorSession).where(
                    CoordinatorSession.conversation_id == conversation_id
                )
            )
            if conversation_id
            else session.get(CoordinatorSession, "main")
        )
        if conversation_id and not thread:
            raise HTTPException(404, "Chat not found")
        conversation = conversation_for(session, thread)
        if not conversation_id and not conversation:
            conversation = core_conversation(session)
        proposals = (
            session.scalars(
                select(CrewProposal)
                .where(CrewProposal.conversation_id == conversation.id)
                .order_by(CrewProposal.created_at)
            ).all()
            if conversation
            else []
        )
        return {
            "conversation": {
                **ConversationView.model_validate(conversation).model_dump(),
                "session_id": thread.id if thread else "main",
            }
            if conversation
            else None,
            "planning_crew_id": thread.crew_id if thread else None,
            "planning_crew_ids": list(thread.crew_ids or []) if thread else [],
            "crew_proposals": [proposal_view(p) for p in proposals],
            "setup_drafts": chat_drafts(session, thread),
        }

    @router.post("/core/chat")
    async def retired_core_chat():
        raise HTTPException(410, "Use /core/turn with the dedicated coordinator.")

    return router
