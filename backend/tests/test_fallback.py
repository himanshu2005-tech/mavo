"""Only an explicitly selected cloud fallback may retry a failed local-model run."""

import asyncio
import json

from sqlalchemy import select

from mavo.crew_workers import CrewWorkerRuntime
from mavo.database import CrewInstance, CrewRun, ModelRoute
from tests.test_crew_workers import Router, add_crew, factory  # noqa: F401

GOOD = json.dumps(
    {
        "state": "wait_for_next_review",
        "summary": "3 important mails.",
        "next_step": "",
        "notification": "",
    }
)


class SmallLocalModel(Router):
    """Ollama's small model answers in the wrong shape; the cloud model answers properly."""

    async def chat(self, provider, model, messages, system, credential_id=None):
        self.calls.append(provider)
        return "Here are your mails, summarised nicely!" if provider == "ollama" else GOOD


def with_routes(factory):
    with factory() as session:
        session.add_all(
            [
                ModelRoute(id="local", name="Ollama", provider="ollama", model="qwen2.5:3b"),
                ModelRoute(
                    id="cloud",
                    name="AIRouter default",
                    provider="airouter",
                    model="deepseek-v4-flash",
                ),
            ]
        )
        session.commit()


def test_a_run_the_local_model_cannot_do_is_done_again_on_the_cloud_route(factory):
    with_routes(factory)
    add_crew(
        factory,
        name="Inbox Summary",
        route_id="local",
        provider="ollama",
        model="qwen2.5:3b",
        configuration_plan={"fallback_route_id": "cloud"},
    )
    router = SmallLocalModel()
    runtime = CrewWorkerRuntime(factory, router)
    asyncio.run(runtime.work("crew"))
    with factory() as session:
        crew = session.get(CrewInstance, "crew")
        assert (
            crew.runtime_state == "waiting" and crew.observation.get("failures", 0) == 0
        )  # not a failure
        assert crew.fallback["status"] == "queued"
        assert (
            crew.fallback["from"] == "Ollama (qwen2.5:3b)"
            and crew.fallback["to"] == "AIRouter default (deepseek-v4-flash)"
        )
    asyncio.run(runtime.work("crew"))
    with factory() as session:
        crew = session.get(CrewInstance, "crew")
        runs = session.scalars(select(CrewRun).order_by(CrewRun.created_at)).all()
        assert [r.provider for r in runs] == [
            "ollama",
            "airouter",
        ]  # the history says which model did what
        assert runs[-1].status == "completed" and crew.runtime_state == "waiting"
        assert crew.fallback["status"] == "completed"
        assert (
            crew.route_id == "local"
        )  # its own route is unchanged: next time it tries local again
        assert "fallback_route" not in crew.observation
    assert router.calls[-1] == "airouter"


def test_a_cloud_route_that_fails_is_not_sent_anywhere_else(factory):
    with_routes(factory)
    add_crew(factory, route_id="cloud", provider="airouter", model="deepseek-v4-flash")

    class Broken(Router):
        async def chat(self, provider, model, messages, system, credential_id=None):
            self.calls.append(provider)
            return "not json"

    asyncio.run(CrewWorkerRuntime(factory, Broken()).work("crew"))
    with factory() as session:
        crew = session.get(CrewInstance, "crew")
        assert crew.fallback is None and "fallback_route" not in crew.observation


def test_local_data_never_goes_to_cloud_without_explicit_selection(factory):
    with_routes(factory)
    add_crew(factory, route_id="local", provider="ollama", model="qwen2.5:3b")
    router = SmallLocalModel()
    asyncio.run(CrewWorkerRuntime(factory, router).work("crew"))
    with factory() as session:
        crew = session.get(CrewInstance, "crew")
        assert crew.fallback is None
        assert "fallback_route" not in crew.observation
    assert router.calls and set(router.calls) == {"ollama"}


def test_revoking_fallback_discards_an_already_queued_retry(factory):
    with_routes(factory)
    add_crew(
        factory,
        route_id="local",
        provider="ollama",
        model="qwen2.5:3b",
        configuration_plan={"fallback_route_id": "cloud"},
    )
    router = SmallLocalModel()
    runtime = CrewWorkerRuntime(factory, router)
    asyncio.run(runtime.work("crew"))
    with factory() as session:
        crew = session.get(CrewInstance, "crew")
        assert crew.observation["fallback_route"] == "cloud"
        crew.configuration_plan = {}
        session.commit()
    asyncio.run(runtime.work("crew"))
    assert router.calls and set(router.calls) == {"ollama"}


def test_fallback_cannot_bypass_the_daily_model_call_budget(factory):
    with_routes(factory)
    add_crew(
        factory,
        route_id="local",
        provider="ollama",
        model="qwen2.5:3b",
        daily_call_limit=1,
        configuration_plan={"fallback_route_id": "cloud"},
    )
    router = SmallLocalModel()
    runtime = CrewWorkerRuntime(factory, router)
    asyncio.run(runtime.work("crew"))
    asyncio.run(runtime.work("crew"))
    assert router.calls == ["ollama"]
    with factory() as session:
        assert session.get(CrewInstance, "crew").runtime_state == "budget_paused"


def test_tools_already_started_prevent_automatic_fallback(factory):
    from mavo.model_router import ModelProviderError

    with_routes(factory)
    add_crew(
        factory,
        route_id="local",
        provider="ollama",
        model="qwen2.5:3b",
        permissions=["model:invoke", "action:notes.write"],
        configuration_plan={"fallback_route_id": "cloud"},
    )

    class ToolModel(Router):
        async def chat(self, provider, *args, **kwargs):
            self.calls.append(provider)
            return json.dumps(
                {
                    "state": "continue",
                    "summary": "Writing a note",
                    "actions": [
                        {
                            "operation": "notes.write",
                            "arguments": {
                                "path": "fixture.md",
                                "text": "Isolated test",
                            },
                        }
                    ],
                }
            )

    class Tools:
        rules = True
        calls = 0

        def available(self, *args):
            return {"notes.write": {}}

        def propose(self, *args):
            return {"id": "fixture-action"}

        async def maybe_execute(self, _):
            self.calls += 1
            raise ModelProviderError("Failure after tool dispatch")

    router, tools = ToolModel(), Tools()
    runtime = CrewWorkerRuntime(factory, router)
    runtime.actions = tools
    asyncio.run(runtime.work("crew"))
    assert tools.calls == 1
    with factory() as session:
        assert "fallback_route" not in session.get(CrewInstance, "crew").observation
    assert router.calls == ["ollama"]
