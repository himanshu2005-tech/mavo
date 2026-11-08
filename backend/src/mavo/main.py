from __future__ import annotations

import argparse
import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import uvicorn
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, update

from mavo import __version__
from mavo.action_rules import ActionRules, build_policy_api
from mavo.api import build_api
from mavo.config import Settings
from mavo.coordinator_client import CoordinatorClient
from mavo.credentials import CredentialStore, DpapiCredentialStore
from mavo.crew_engine import recover_interrupted_runs
from mavo.crew_planner import CrewPlanner, build_planner_api
from mavo.crew_workers import CrewWorkerRuntime, stop_crew
from mavo.database import (
    ActionRequest,
    CrewInstance,
    build_session_factory,
    ensure_data_dir,
    session_dependency,
)
from mavo.desktop_actions import DesktopActions
from mavo.firebase_auth import FirebaseIdentity, build_firebase_api
from mavo.google_actions import GoogleActions, build_actions_api
from mavo.google_api import build_google_api
from mavo.google_auth import GoogleAuth
from mavo.migrations import run_migrations
from mavo.model_router import ModelRouter
from mavo.model_routes import import_legacy_provider_routes
from mavo.ollama import OllamaClient
from mavo.publisher import publisher_github_config, publisher_google_config
from mavo.reminders import backfill_daily_schedules
from mavo.resources.google import GoogleResourcePool
from mavo.resources.weather import OpenMeteoWeatherAdapter
from mavo.security import token_guard
from mavo.site_logins import SiteLogins

logger = logging.getLogger("mavo")


def create_app(
    settings: Settings | None = None,
    ollama: OllamaClient | None = None,
    credentials: CredentialStore | None = None,
    cloud_transport: httpx.AsyncBaseTransport | None = None,
    coordinator=None,
) -> FastAPI:
    app_settings = settings or Settings()
    ensure_data_dir(app_settings.data_dir)
    session_factory = build_session_factory(app_settings.database_url)
    ollama_client = ollama or OllamaClient(
        app_settings.ollama_url, context=app_settings.ollama_context
    )
    credential_store = credentials or DpapiCredentialStore(app_settings.data_dir)
    model_router = ModelRouter(ollama_client, credential_store, cloud_transport)
    weather = OpenMeteoWeatherAdapter(cloud_transport)
    google = GoogleAuth(
        session_factory,
        credential_store,
        cloud_transport,
        publisher_config=publisher_google_config(
            app_settings.google_client_id,
            app_settings.google_client_secret,
            app_settings.core_gateway_url,
        ),
        allow_developer_setup=app_settings.allow_google_developer_setup,
    )
    google_pool = GoogleResourcePool(google)
    crew_runtime = CrewWorkerRuntime(session_factory, model_router, google_pool, weather)
    crew_runtime.actions = GoogleActions(google_pool)
    crew_runtime.actions.rules = ActionRules(session_factory)
    desktop = DesktopActions(app_settings.automation_dir or app_settings.data_dir / "automation")
    site_logins = SiteLogins(session_factory, credential_store)
    desktop.login_lookup = site_logins.lookup
    crew_runtime.actions.desktop = desktop
    from mavo.connectors.hub import ConnectorHub

    connectors = ConnectorHub(
        session_factory,
        credential_store,
        google,
        transport=cloud_transport,
    )
    connectors.on_disconnect = crew_runtime.cancel
    from mavo.connectors.github_auth import GitHubConnection

    connectors.github_auth = GitHubConnection(
        connectors,
        publisher_github_config(app_settings.github_client_id, app_settings.github_app_slug),
    )
    crew_runtime.connectors = connectors
    crew_runtime.actions.connectors = connectors
    firebase_config = (
        publisher_google_config(
            app_settings.firebase_google_client_id,
            app_settings.firebase_google_client_secret,
            app_settings.core_gateway_url,
        )
        if app_settings.firebase_google_client_id
        else google.configuration()
    )
    firebase = FirebaseIdentity(session_factory, credential_store, firebase_config, cloud_transport)
    crew_runtime.coordinator = coordinator or CoordinatorClient(
        app_settings, firebase, cloud_transport
    )
    crew_runtime.planner = CrewPlanner(
        crew_runtime, crew_runtime.coordinator, google, credential_store
    )
    from mavo.tasks import TaskRunner, build_tasks_api

    crew_runtime.task_runner = TaskRunner(crew_runtime)
    crew_runtime.actions.helper = crew_runtime.ask_helper
    from mavo.automations import Automations, build_automations_api

    crew_runtime.actions.automations = Automations(crew_runtime)

    async def session_ready():
        if crew_runtime.closing:
            return False
        if not app_settings.require_login:
            return True
        await firebase.refresh()
        return bool(firebase.status()["signed_in"])

    async def require_identity():
        if not await session_ready():
            raise HTTPException(401, "Sign in with Google to access your Mavo workspace.")

    def stop_signed_out_work():
        crew_runtime.activity.clear()
        asyncio.create_task(desktop.close())
        asyncio.create_task(google.cancel())
        asyncio.create_task(connectors.github_auth.close())
        with session_factory() as session:
            for crew in session.scalars(
                select(CrewInstance).where(CrewInstance.is_running.is_(True))
            ):
                stop_crew(session, crew)
                crew_runtime.cancel(crew.id)
            session.execute(
                update(ActionRequest)
                .where(ActionRequest.status == "pending")
                .values(status="rejected", error="Mavo was signed out before approval.")
            )
            session.commit()

    firebase.on_sign_out = stop_signed_out_work

    def desktop_allowed():
        with session_factory() as session:
            return (
                not crew_runtime.closing
                and (not app_settings.require_login or bool(firebase.status()["signed_in"]))
            ) and not crew_runtime.actions.rules.paused(session)

    desktop.guard = desktop_allowed
    crew_runtime.session_guard = session_ready
    crew_runtime.actions.session_guard = lambda: (
        not crew_runtime.closing
        and (not app_settings.require_login or bool(firebase.status()["signed_in"]))
    )
    connectors.github_auth.session_guard = crew_runtime.actions.session_guard

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        run_migrations(app_settings.database_url)
        from mavo.connectors.retired import remove_retired_connector

        remove_retired_connector(session_factory, credential_store)
        with session_factory() as session:
            import_legacy_provider_routes(session)
            backfill_daily_schedules(session)
            recover_interrupted_runs(session)
        crew_runtime.task_runner.recover()
        logger.info("Mavo backend ready on loopback")
        worker_task = (
            asyncio.create_task(crew_runtime.run()) if app_settings.workers_enabled else None
        )
        try:
            yield
        finally:
            for job in list(crew_runtime.task_runner.jobs.values()):
                job.cancel()
            if worker_task:
                worker_task.cancel()
                await asyncio.gather(worker_task, return_exceptions=True)
            await google.close()
            await connectors.github_auth.close()
            await desktop.close()
            await firebase.google.close()
            session_factory.kw["bind"].dispose()

    app = FastAPI(
        title="Mavo Local API",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(app_settings.allowed_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post(
        "/api/v1/runtime/close-tools", dependencies=[Depends(token_guard(app_settings.api_token))]
    )
    async def close_tools():
        crew_runtime.closing = True
        for _, task in list(crew_runtime.tasks.values()):
            task.cancel()
        for job in list(crew_runtime.task_runner.jobs.values()):
            job.cancel()  # a one-time task stops with the rest of Mavo's tools
        await desktop.close()
        return {"closed": True}

    def get_session():  # type: ignore[no-untyped-def]
        yield from session_dependency(session_factory)

    app.state.crew_runtime = crew_runtime
    app.state.firebase = firebase
    protected_api = build_api(
        get_session, model_router, credential_store, weather, crew_runtime, google
    )
    app.include_router(
        build_planner_api(crew_runtime.planner, get_session),
        prefix="/api/v1",
        dependencies=[Depends(token_guard(app_settings.api_token)), Depends(require_identity)],
    )
    app.include_router(
        protected_api,
        prefix="/api/v1",
        dependencies=[Depends(token_guard(app_settings.api_token)), Depends(require_identity)],
    )
    app.include_router(
        build_google_api(google, google_pool),
        prefix="/api/v1",
        dependencies=[Depends(token_guard(app_settings.api_token)), Depends(require_identity)],
    )
    app.include_router(
        build_policy_api(crew_runtime.actions, crew_runtime),
        prefix="/api/v1",
        dependencies=[Depends(token_guard(app_settings.api_token)), Depends(require_identity)],
    )
    from mavo.autostart import build_autostart_api
    from mavo.connectors.api import build_connector_api
    from mavo.crew_ask import build_ask_api
    from mavo.mavo_agents import build_agents_api
    from mavo.resource_pool import build_pool_api
    from mavo.shared_memory import build_memory_api
    from mavo.site_logins import build_logins_api
    from mavo.team_build import build_open_api
    from mavo.timeline import build_timeline_api
    from mavo.trial import build_trial_api

    for extra in (
        build_pool_api(crew_runtime, weather, get_session),
        build_timeline_api(get_session),
        build_logins_api(site_logins),
        build_trial_api(crew_runtime, get_session),
        build_autostart_api(),
        build_tasks_api(crew_runtime.task_runner),
        build_ask_api(crew_runtime),
        build_memory_api(crew_runtime),
        build_agents_api(),
        build_open_api(crew_runtime),
        build_automations_api(crew_runtime.actions.automations),
    ):
        app.include_router(
            extra,
            prefix="/api/v1",
            dependencies=[Depends(token_guard(app_settings.api_token)), Depends(require_identity)],
        )

    app.include_router(
        build_connector_api(connectors),
        prefix="/api/v1",
        dependencies=[Depends(token_guard(app_settings.api_token)), Depends(require_identity)],
    )
    app.include_router(
        build_actions_api(crew_runtime.actions, get_session),
        prefix="/api/v1",
        dependencies=[Depends(token_guard(app_settings.api_token)), Depends(require_identity)],
    )
    app.include_router(
        build_firebase_api(firebase),
        prefix="/api/v1",
        dependencies=[Depends(token_guard(app_settings.api_token))],
    )
    return app


def cli() -> None:
    parser = argparse.ArgumentParser(description="Run the Mavo local backend")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()
    settings = Settings(
        **{
            key: value
            for key, value in {"host": args.host, "port": args.port}.items()
            if value is not None
        }
    )
    logging.basicConfig(level=settings.log_level)
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    cli()
