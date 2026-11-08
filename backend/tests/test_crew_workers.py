import asyncio
import json
import re
from datetime import UTC, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from mavo.crew_behaviors import BEHAVIORS
from mavo.crew_engine import recover_interrupted_runs
from mavo.crew_workers import CrewWorkerRuntime, parse_checkpoint
from mavo.database import (
    ActionRequest,
    CrewInstance,
    CrewRun,
    ModelRoute,
    ReminderEvent,
    WorkspaceSetting,
    build_session_factory,
    utcnow,
)
from mavo.migrations import run_migrations
from mavo.resources.crew_weather import WeatherLookup
from mavo.resources.device import device_alerts
from mavo.schemas import WeatherLocation


class Router:
    def __init__(self, state="continue"):
        self.calls = []
        self.state = state
        self.entered = asyncio.Event()
        self.release = None

    async def chat(self, provider, model, messages, system, credential_id=None):
        self.calls.append((messages, system))
        self.entered.set()
        if self.release:
            await self.release.wait()
        return json.dumps(
            {
                "state": self.state,
                "summary": "A concrete deliverable",
                "next_step": "Develop the next section",
            }
        )


@pytest.mark.asyncio
async def test_busy_browser_waits_without_spending_model_budget(factory):
    from types import SimpleNamespace

    add_crew(factory, permissions=["model:invoke", "action:browser.open"])
    router = Router()
    runtime = CrewWorkerRuntime(factory, router)
    runtime.actions = SimpleNamespace(desktop=SimpleNamespace(browser_owner="another-crew"))
    await runtime.work("crew")
    with factory() as session:
        crew = session.get(CrewInstance, "crew")
        assert crew.runtime_state == "waiting_browser" and crew.daily_calls_used == 0
        assert crew.next_run_at.replace(tzinfo=UTC) > utcnow()
        assert not session.scalars(select(CrewRun)).all()
    assert not router.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("closed", [False, True])
async def test_browser_is_released_between_reviews_or_after_manual_window_closes(factory, closed):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    add_crew(factory, next_run_at=utcnow() + timedelta(minutes=30))
    desktop = SimpleNamespace(
        browser_owner="crew",
        page=SimpleNamespace(is_closed=lambda: closed),
        sign_in_held=lambda crew: closed,
        close_owner=AsyncMock(),
    )
    runtime = CrewWorkerRuntime(factory, Router())
    runtime.actions = SimpleNamespace(desktop=desktop)
    await runtime.tick()
    desktop.close_owner.assert_awaited_once_with("crew")


class DestinationWeather:
    def __init__(self, ambiguous=False):
        self.queries, self.forecasts = [], []
        self.ambiguous = ambiguous

    async def search(self, query, country_code=None):
        self.queries.append((query, country_code))
        city = WeatherLocation(
            name="Chennai", region="Tamil Nadu", country="India", latitude=13.083, longitude=80.271
        )
        return [city, city] if self.ambiguous else [city]

    async def read_location(self, location):
        self.forecasts.append(location)

        class Forecast:
            def model_dump(self, **_):
                return {
                    "location": "Chennai, Tamil Nadu, India",
                    "fetched_at": utcnow().isoformat(),
                    "forecast": [
                        {"date": utcnow().date().isoformat(), "precipitation_probability_max": 90}
                    ],
                }

        return Forecast()


class WeatherRouter(Router):
    async def chat(self, provider, model, messages, system, credential_id=None):
        self.calls.append((messages, system))
        if len(self.calls) == 1:
            return json.dumps(
                {
                    "state": "continue",
                    "summary": "Need destination forecast",
                    "next_step": "Check rain",
                    "notification": "UNVERIFIED",
                    "weather_requests": [
                        {"query": "Chennai, Tamil Nadu, India", "country_code": "IN"}
                    ],
                }
            )
        assert "Chennai" in messages[-1]["content"]
        return json.dumps(
            {
                "state": "continue",
                "summary": "Rain risk at destination; verify airline status.",
                "next_step": "Review later",
                "notification": "Rain risk in Chennai.",
            }
        )


@pytest.mark.asyncio
async def test_destination_weather_uses_two_budgeted_calls_without_device_location(factory):
    add_crew(
        factory,
        template="travel",
        permissions=["model:invoke", "weather:read", "notifications:show"],
    )
    with factory() as session:
        session.add(
            WorkspaceSetting(
                key="default-resource-access", value={"weather": True, "location": False}
            )
        )
        session.commit()
    adapter, router = DestinationWeather(), WeatherRouter()
    await CrewWorkerRuntime(factory, router, weather=adapter).work("crew")
    assert len(router.calls) == 2 and len(adapter.forecasts) == 1
    with factory() as session:
        run = session.scalar(select(CrewRun))
        assert run.status == "completed" and run.model_calls == 2
        assert session.get(CrewInstance, "crew").daily_calls_used == 2
        assert session.get(WorkspaceSetting, "device-location") is None
        assert session.get(WorkspaceSetting, "weather-location") is None
        notifications = session.scalars(select(ReminderEvent)).all()
        assert [n.message for n in notifications] == ["Rain risk in Chennai."]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "global_access,crew_access,limit,session_limit",
    [
        (False, True, 12, 5),
        (True, False, 12, 5),
        (True, True, 1, 5),
    ],
)
async def test_weather_denial_and_budgets_stop_before_resource_io(
    factory, global_access, crew_access, limit, session_limit
):
    add_crew(
        factory,
        permissions=["model:invoke"] + (["weather:read"] if crew_access else []),
        daily_call_limit=limit,
        max_model_calls=session_limit,
    )
    with factory() as session:
        session.add(
            WorkspaceSetting(
                key="default-resource-access", value={"weather": global_access, "location": False}
            )
        )
        session.commit()
    adapter, router = DestinationWeather(), WeatherRouter()
    await CrewWorkerRuntime(factory, router, weather=adapter).work("crew")
    assert len(router.calls) == 1 and not adapter.queries and not adapter.forecasts
    with factory() as session:
        assert session.scalar(select(CrewRun)).model_calls == 1
        assert not session.scalars(select(ReminderEvent)).all()
        crew = session.get(CrewInstance, "crew")
        if limit == 1:
            # Out of today's calls: it waits for tomorrow by itself, with the fix spelled out,
            # instead of sitting on a question the person cannot usefully answer.
            assert crew.runtime_state == "budget_paused" and crew.next_run_at is not None
            assert crew.limit == {"kind": "day", "current": 1, "suggested": 5}
            assert "model-call budget" not in (crew.last_runtime_error or "")
        else:
            assert crew.runtime_state == "needs_input"


@pytest.mark.asyncio
async def test_one_call_a_run_still_gets_the_one_it_needs_to_finish(factory):
    """Set to 1 call a run, an agent could never finish anything with a second step (it decided,
    then had no call left to act on the answer). Every run has at least 2, and no more."""
    add_crew(
        factory,
        permissions=["model:invoke", "weather:read"],
        daily_call_limit=12,
        max_model_calls=1,
    )
    with factory() as session:
        session.add(
            WorkspaceSetting(
                key="default-resource-access", value={"weather": True, "location": False}
            )
        )
        session.commit()
    adapter, router = DestinationWeather(), WeatherRouter()
    await CrewWorkerRuntime(factory, router, weather=adapter).work("crew")
    assert len(router.calls) == 2 and len(adapter.forecasts) == 1  # looked it up and finished
    with factory() as session:
        assert session.scalar(select(CrewRun)).model_calls == 2
        crew = session.get(CrewInstance, "crew")
        assert crew.runtime_state == "waiting" and crew.question is None


@pytest.mark.asyncio
async def test_ambiguous_destination_never_selects_first_forecast(factory):
    from mavo.resources.crew_weather import lookup_weather

    add_crew(factory, permissions=["model:invoke", "weather:read"])
    with factory() as session:
        session.add(WorkspaceSetting(key="default-resource-access", value={"weather": True}))
        session.commit()
        crew = session.get(CrewInstance, "crew")
    adapter = DestinationWeather(ambiguous=True)
    response = await lookup_weather(factory, crew, adapter, WeatherLookup(query="Chennai"))
    assert response["status"] == "ambiguous" and not adapter.forecasts
    with pytest.raises(ValidationError):
        WeatherLookup(
            location=WeatherLocation(name="device", source="device", latitude=17, longitude=78)
        )


@pytest.mark.asyncio
async def test_weather_revocation_during_io_prevents_analysis_and_notification(factory):
    add_crew(factory, permissions=["model:invoke", "weather:read", "notifications:show"])
    with factory() as session:
        session.add(WorkspaceSetting(key="default-resource-access", value={"weather": True}))
        session.commit()

    class RevokingWeather(DestinationWeather):
        async def read_location(self, location):
            with factory() as session:
                session.get(WorkspaceSetting, "default-resource-access").value = {"weather": False}
                session.commit()
            return await super().read_location(location)

    router = WeatherRouter()
    await CrewWorkerRuntime(factory, router, weather=RevokingWeather()).work("crew")
    assert len(router.calls) == 1
    with factory() as session:
        assert not session.scalars(select(ReminderEvent)).all()
        assert session.scalar(select(CrewRun)).result is None


@pytest.mark.asyncio
async def test_weather_model_cannot_make_an_unbounded_tool_loop(factory):
    add_crew(factory, permissions=["model:invoke", "weather:read"])
    with factory() as session:
        session.add(WorkspaceSetting(key="default-resource-access", value={"weather": True}))
        session.commit()

    class LoopRouter(WeatherRouter):
        async def chat(self, provider, model, messages, system, credential_id=None):
            self.calls.append((messages, system))
            return json.dumps(
                {
                    "state": "continue",
                    "summary": "Again",
                    "next_step": "Again",
                    "weather_requests": [{"query": "Chennai"}],
                }
            )

    router, adapter = LoopRouter(), DestinationWeather()
    await CrewWorkerRuntime(factory, router, weather=adapter).work("crew")
    assert len(router.calls) == 2 and len(adapter.forecasts) == 1
    with factory() as session:
        assert session.get(CrewInstance, "crew").runtime_state == "needs_input"
        assert session.scalar(select(CrewRun)).model_calls == 2


@pytest.mark.parametrize("fenced", [False, True])
def test_checkpoint_keeps_code_fences_inside_deliverables(fenced):
    raw = json.dumps(
        {
            "state": "waiting_for_input",
            "summary": "Example:\n```python\nprint('ok')\n```",
            "next_step": "Review the example",
        }
    )
    result = parse_checkpoint("```json\n" + raw + "\n```" if fenced else raw)
    assert "print('ok')" in result.summary and result.state == "waiting_for_input"


def test_a_step_that_goes_on_without_words_is_accepted_but_an_empty_answer_is_not():
    act = [{"operation": "browser.click", "arguments": {"ref": "e3"}}]
    going = parse_checkpoint(json.dumps({"state": "continue", "summary": "", "actions": act}))
    assert going.summary == "Working." and going.actions[0].operation == "browser.click"
    assert parse_checkpoint(json.dumps({"state": "continue", "actions": act})).summary == "Working."
    # Nothing to go on with, or a finished job with no words: still refused, never "Working.".
    for bad in (
        {"state": "continue", "summary": ""},
        {"state": "wait_for_next_review", "summary": "", "actions": act},
        {"state": "waiting_for_input", "summary": ""},
    ):
        with pytest.raises(ValidationError):
            parse_checkpoint(json.dumps(bad))


def test_checkpoint_accepts_one_json_fence_and_preserves_the_actual_deliverable():
    text = (
        "Define a question, search sources, then screen abstracts.\n\n```json\n"
        + json.dumps(
            {
                "state": "waiting_for_input",
                "summary": "Outlined research steps.",
                "next_step": "Choose your topic.",
            }
        )
        + "\n```"
    )
    checkpoint = parse_checkpoint(text)
    assert checkpoint.state == "waiting_for_input"
    assert checkpoint.summary.startswith("Define a question")
    assert checkpoint.next_step == "Choose your topic."
    with pytest.raises(ValidationError):
        parse_checkpoint(text + '\n```json\n{"state":"continue"}\n```')
    with pytest.raises(ValidationError):
        parse_checkpoint('Some text\n```json\n{"state":"continue","shell":"run"}\n```')


def test_a_finished_answer_without_a_next_step_is_still_an_answer():
    """The agent wrote the right result and "next_step": null; that is what finishing looks like."""
    for fields in ({"next_step": None}, {}):
        raw = json.dumps({"state": "wait_for_next_review", "summary": "3.15.2", **fields})
        checkpoint = parse_checkpoint(raw)
        assert checkpoint.summary == "3.15.2" and checkpoint.next_step == ""
    # Nothing else became lenient: a summary is still required and unknown fields still refused.
    with pytest.raises(ValidationError):
        parse_checkpoint('{"state":"continue","next_step":null}')
    with pytest.raises(ValidationError):
        parse_checkpoint('{"state":"continue","summary":"x","shell":"run"}')
    with pytest.raises(ValidationError):
        parse_checkpoint('{"state":"continue","summary":"x","next_step":5}')


@pytest.fixture
def factory(tmp_path):
    url = f"sqlite:///{(tmp_path / 'workers.db').as_posix()}"
    run_migrations(url)
    factory = build_session_factory(url)
    with factory() as session:
        session.add(ModelRoute(id="route", name="Test", provider="ollama", model="test"))
        session.commit()
    yield factory
    factory.kw["bind"].dispose()


def add_crew(factory, template="research", **changes):
    values = dict(
        id="crew",
        name="My worker",
        template_id=template,
        responsibilities="Complete this concrete task.",
        permissions=["model:invoke"],
        provider="ollama",
        model="test",
        route_id="route",
        is_running=True,
        runtime_state="waiting",
        runtime_generation=1,
    )
    values["trigger_type"] = "daily" if template == "schedule" else "interval"
    values.update(changes)
    with factory() as session:
        session.add(CrewInstance(**values))
        session.commit()
    return values["id"]


def remember_a_page_read(factory, minutes_ago, view):
    with factory() as session:
        session.add(
            ActionRequest(
                crew_id="crew",
                account_subject="local",
                operation="browser.read",
                arguments={},
                fingerprint="read-" + str(minutes_ago),
                status="completed",
                result={"view": view},
                created_at=utcnow() - timedelta(minutes=minutes_ago),
            )
        )
        session.commit()


@pytest.mark.asyncio
async def test_a_new_review_treats_the_last_pages_as_history_and_must_look_again(factory):
    """A Crew that checks a site every 30 minutes once answered "checked, nothing changed" from the
    last review's page without opening the site. Old reads now arrive labelled as old, trimmed, and
    the agent is told to read the site again; within one review the page it just read is in full."""
    view = "Balance $0.74 " + "x" * 1500 + " END-OF-PAGE"
    add_crew(factory, permissions=["model:invoke", "action:browser.open"])
    remember_a_page_read(factory, 30, view)
    router = Router(state="wait_for_next_review")
    await CrewWorkerRuntime(factory, router).work("crew")
    prompt = router.calls[0][0][0]["content"]
    system = router.calls[0][1]
    assert "HISTORY from your previous review" in prompt and "NEW scheduled review" in prompt
    assert "Prior action results" not in prompt
    assert re.search(r'"minutes_ago": (29|30|31)', prompt)
    assert "END-OF-PAGE" not in prompt  # trimmed: history is for comparing, not for answering
    assert "re-checks its site on every review" in system

    # Carrying on within one review: the page just read is shown in full, as before.
    with factory() as session:
        crew = session.get(CrewInstance, "crew")
        crew.observation = {"automation_steps": 1}
        crew.next_run_at = None
        crew.runtime_state = "waiting"
        session.commit()
    router = Router(state="wait_for_next_review")
    await CrewWorkerRuntime(factory, router).work("crew")
    prompt = router.calls[0][0][0]["content"]
    assert "Results of THIS review so far" in prompt and "partway through" in prompt
    assert "HISTORY" not in prompt and "END-OF-PAGE" in prompt

    # Once this review has begun, last review's page is gone from view: the agent has to read.
    began = (utcnow() - timedelta(seconds=30)).isoformat()
    with factory() as session:
        crew = session.get(CrewInstance, "crew")
        crew.observation = {"automation_steps": 1, "review_started_at": began}
        crew.next_run_at = None
        crew.runtime_state = "waiting"
        session.commit()
    router = Router(state="wait_for_next_review")
    await CrewWorkerRuntime(factory, router).work("crew")
    prompt = router.calls[0][0][0]["content"]
    assert "END-OF-PAGE" not in prompt and "Prior action results" not in prompt
    remember_a_page_read(factory, 0, "Balance now $0.75 FRESH-READ")
    with factory() as session:
        crew = session.get(CrewInstance, "crew")
        crew.observation = {"automation_steps": 1, "review_started_at": began}
        crew.next_run_at = None
        crew.runtime_state = "waiting"
        session.commit()
    router = Router(state="wait_for_next_review")
    await CrewWorkerRuntime(factory, router).work("crew")
    prompt = router.calls[0][0][0]["content"]
    assert "FRESH-READ" in prompt and "END-OF-PAGE" not in prompt


@pytest.mark.asyncio
async def test_a_page_read_before_a_navigation_is_marked_as_out_of_date(factory):
    """The agent sent open, read, navigate and no read after. Shown only the homepage read, it
    concluded "the navigation landed on the homepage" and asked the person to sign in."""
    add_crew(factory, permissions=["model:invoke", "action:browser.open"])
    began = (utcnow() - timedelta(minutes=5)).isoformat()
    remember_a_page_read(factory, 3, "HOMEPAGE-VIEW " + "x" * 1000 + " HOME-END")
    with factory() as session:
        session.add(
            ActionRequest(
                crew_id="crew",
                account_subject="local",
                operation="browser.navigate",
                arguments={"goal": "credits"},
                fingerprint="nav-1",
                status="completed",
                result={"navigation": {"reached": True}},
                created_at=utcnow() - timedelta(minutes=2),
            )
        )
        crew = session.get(CrewInstance, "crew")
        crew.observation = {"automation_steps": 1, "review_started_at": began}
        session.commit()
    router = Router(state="wait_for_next_review")
    await CrewWorkerRuntime(factory, router).work("crew")
    prompt = router.calls[0][0][0]["content"]
    assert "The page changed after this read" in prompt and "HOME-END" not in prompt

    # Read again after the navigation: that one is current, and is shown in full.
    remember_a_page_read(factory, 0, "CREDITS-VIEW " + "y" * 1000 + " CREDITS-END")
    with factory() as session:
        crew = session.get(CrewInstance, "crew")
        crew.observation = {"automation_steps": 2, "review_started_at": began}
        crew.next_run_at = None
        crew.runtime_state = "waiting"
        session.commit()
    router = Router(state="wait_for_next_review")
    await CrewWorkerRuntime(factory, router).work("crew")
    prompt = router.calls[0][0][0]["content"]
    assert "CREDITS-END" in prompt and "HOME-END" not in prompt
    assert prompt.count("The page changed after this read") == 1  # only the old homepage read


@pytest.mark.asyncio
async def test_a_finished_review_remembers_where_the_answer_was_and_the_next_one_goes_there(
    factory,
):
    add_crew(factory, permissions=["model:invoke", "action:browser.open"])
    began = (utcnow() - timedelta(minutes=1)).isoformat()
    with factory() as session:
        for n, (operation, arguments, result) in enumerate(
            [
                ("browser.navigate", {"goal": "credits"}, {"navigation": {"reached": True}}),
                (
                    "browser.read",
                    {},
                    {"url": "https://www.example.com/settings/credits", "view": "v"},
                ),
            ]
        ):
            session.add(
                ActionRequest(
                    crew_id="crew",
                    account_subject="local",
                    operation=operation,
                    arguments=arguments,
                    fingerprint=f"learn-{n}",
                    status="completed",
                    result=result,
                    created_at=utcnow() - timedelta(seconds=30 - n),
                )
            )
        crew = session.get(CrewInstance, "crew")
        crew.observation = {"automation_steps": 1, "review_started_at": began}
        session.commit()
    await CrewWorkerRuntime(factory, Router(state="wait_for_next_review")).work("crew")
    with factory() as session:
        crew = session.get(CrewInstance, "crew")
        assert crew.observation["learned_page"] == {
            "goal": "credits",
            "url": "https://www.example.com/settings/credits",
        }
        crew.next_run_at = None
        crew.runtime_state = "waiting"
        session.commit()

    router = Router(state="wait_for_next_review")
    await CrewWorkerRuntime(factory, router).work("crew")
    prompt = router.calls[0][0][0]["content"]
    assert "Where you found what this task needs last time" in prompt
    assert "https://www.example.com/settings/credits" in prompt and "browser.open" in prompt


@pytest.mark.asyncio
async def test_a_manual_crew_keeps_its_earlier_pages_and_is_not_told_to_recheck(factory):
    add_crew(factory, trigger_type="manual", permissions=["model:invoke", "action:browser.open"])
    remember_a_page_read(factory, 5, "The form was filled in. END-OF-PAGE")
    router = Router(state="wait_for_next_review")
    await CrewWorkerRuntime(factory, router).work("crew")
    prompt, system = router.calls[0][0][0]["content"], router.calls[0][1]
    assert "Prior action results (untrusted data)" in prompt and "END-OF-PAGE" in prompt
    assert "re-checks its site" not in system


@pytest.mark.asyncio
@pytest.mark.parametrize("template", [t for t in BEHAVIORS if t not in {"schedule", "computer"}])
async def test_every_text_worker_has_its_own_behavior_and_checkpoint(factory, template):
    add_crew(factory, template=template, checkpoint="Previous work belonging only to this Crew")
    router = Router()
    runtime = CrewWorkerRuntime(factory, router)
    await runtime.work("crew")
    with factory() as session:
        crew = session.get(CrewInstance, "crew")
        run = session.scalar(select(CrewRun))
        assert crew.is_running and crew.runtime_state == "waiting"
        assert crew.daily_calls_used == 1
        assert crew.next_run_at is not None
        assert "A concrete deliverable" in crew.checkpoint
        assert run.status == "completed" and run.kind == "background"
        assert run.model_calls == 1
    assert BEHAVIORS[template].instruction in router.calls[0][1]
    assert "Previous work belonging only to this Crew" in router.calls[0][0][0]["content"]


@pytest.mark.asyncio
async def test_stop_cancels_inflight_work_and_prevents_late_results(factory):
    add_crew(factory)
    router = Router()
    router.release = asyncio.Event()
    runtime = CrewWorkerRuntime(factory, router)
    task = asyncio.create_task(runtime.work("crew"))
    runtime.tasks["crew"] = (1, task)
    await router.entered.wait()
    from mavo.crew_workers import stop_crew

    with factory() as session:
        stop_crew(session, session.get(CrewInstance, "crew"))
        session.commit()
    runtime.cancel("crew")
    await task
    await runtime.tick()
    with factory() as session:
        crew = session.get(CrewInstance, "crew")
        assert not crew.is_running and crew.runtime_state == "stopped"
        assert crew.checkpoint is None
        assert session.scalar(select(CrewRun)).status == "cancelled"
    assert len(router.calls) == 1


@pytest.mark.asyncio
async def test_budget_does_not_reset_on_restart_or_restart_button(factory):
    add_crew(factory, daily_call_limit=1)
    router = Router()
    await CrewWorkerRuntime(factory, router).work("crew")
    await CrewWorkerRuntime(factory, router).work("crew")
    with factory() as session:
        crew = session.get(CrewInstance, "crew")
        assert crew.is_running and crew.runtime_state == "budget_paused"
        assert crew.daily_calls_used == 1
        assert crew.next_run_at > utcnow().replace(tzinfo=None)
    assert len(router.calls) == 1


@pytest.mark.asyncio
async def test_completed_work_waits_instead_of_looping(factory):
    # A scheduled Crew that says "done, waiting" asks for nothing: it waits for its next review
    # (not for the person), and is not run again in a loop in between.
    add_crew(factory)
    router = Router("waiting_for_input")
    runtime = CrewWorkerRuntime(factory, router)
    await runtime.work("crew")
    for _ in range(3):
        await runtime.tick()
    with factory() as session:
        crew = session.get(CrewInstance, "crew")
        assert crew.is_running and crew.runtime_state == "waiting"
        assert crew.next_run_at is not None
    assert len(router.calls) == 1


@pytest.mark.asyncio
async def test_a_manual_crew_that_finished_waits_for_the_person(factory):
    add_crew(factory, trigger_type="manual")
    router = Router("waiting_for_input")
    runtime = CrewWorkerRuntime(factory, router)
    await runtime.work("crew")
    for _ in range(3):
        await runtime.tick()
    with factory() as session:
        crew = session.get(CrewInstance, "crew")
        assert crew.is_running and crew.runtime_state == "needs_input"
        assert crew.next_run_at is None
    assert len(router.calls) == 1


@pytest.mark.asyncio
async def test_schedule_is_deterministic_and_run_is_required(factory):
    add_crew(factory, template="schedule", schedule_time="00:00", is_running=False)
    router = Router()
    runtime = CrewWorkerRuntime(factory, router)
    from mavo.reminders import create_due_reminders

    with factory() as session:
        assert create_due_reminders(session) == []
        session.get(CrewInstance, "crew").is_running = True
        session.commit()
    await runtime.tick()
    with factory() as session:
        assert len(create_due_reminders(session)) == 1
        assert session.get(CrewInstance, "crew").next_run_at is not None
    assert router.calls == []


@pytest.mark.asyncio
async def test_device_metrics_require_permission_and_never_call_a_model(factory, monkeypatch):
    add_crew(factory, template="computer")
    metrics = {
        "cpu_percent": 92,
        "memory_percent": 45,
        "storage_percent": 60,
        "battery_percent": None,
        "plugged_in": None,
    }
    reads = []

    def read():
        reads.append(1)
        return metrics

    monkeypatch.setattr("mavo.crew_workers.read_device_metrics", read)
    router = Router()
    runtime = CrewWorkerRuntime(factory, router)
    await runtime.work("crew")
    assert reads == []
    with factory() as session:
        crew = session.get(CrewInstance, "crew")
        assert crew.runtime_state == "needs_input"
        crew.permissions = ["model:invoke", "device:read"]
        session.commit()
    await runtime.work("crew")
    with factory() as session:
        assert "CPU: 92%" in session.get(CrewInstance, "crew").checkpoint
        assert session.scalar(select(CrewRun)).model_calls == 0
    assert reads == [1] and router.calls == []
    events = runtime.activity.snapshot(0)["events"]
    assert [event["kind"] for event in events] == ["run_started", "run_finished"]
    assert events[-1]["status"] == "completed"
    assert not runtime.activity.snapshot()["active_calls"]
    assert device_alerts({**metrics, "cpu_percent": 86}, ["CPU"]) == ["CPU"]
    assert device_alerts({**metrics, "cpu_percent": 84}, ["CPU"]) == []


@pytest.mark.asyncio
async def test_drive_reports_use_actual_counters_notify_each_review_and_coalesce(
    factory, monkeypatch
):
    from unittest.mock import Mock

    read = Mock(
        return_value={
            "drive": "C:\\",
            "storage_free_gb": 12.5,
            "storage_total_gb": 100,
            "storage_percent": 87.5,
        }
    )
    monkeypatch.setattr("mavo.crew_workers.read_storage_metrics", read)
    add_crew(
        factory,
        template="computer",
        review_interval_minutes=5,
        responsibilities="Check free space on the C: drive. Send a notification on every check.",
        permissions=["model:invoke", "device:read", "notifications:show"],
    )
    router = Router()
    runtime = CrewWorkerRuntime(factory, router)
    await runtime.work("crew")
    await runtime.work("crew")
    with factory() as session:
        events = session.scalars(select(ReminderEvent).order_by(ReminderEvent.scheduled_for)).all()
        assert [e.status for e in events] == ["superseded", "pending"]
        assert "12.50 GB free (12.5% free)" in events[-1].message
        crew = session.get(CrewInstance, "crew")
        assert crew.configuration_plan["device_monitor"]["drive"] == "C:"
        assert crew.daily_calls_used == 0 and crew.is_running
        assert all(r.model_calls == 0 for r in session.scalars(select(CrewRun)))
    assert not router.calls
    read.assert_called_with("C:")


@pytest.mark.asyncio
async def test_drive_warning_hysteresis_recovery_and_revocation(factory, monkeypatch):
    free = [4.0]
    monkeypatch.setattr(
        "mavo.crew_workers.read_storage_metrics",
        lambda drive: {
            "drive": drive,
            "storage_free_gb": free[0],
            "storage_total_gb": 100,
            "storage_percent": 100 - free[0],
        },
    )
    add_crew(
        factory,
        template="computer",
        permissions=["model:invoke", "device:read", "notifications:show"],
        configuration_plan={
            "device_monitor": {
                "kind": "storage",
                "drive": "C:",
                "notifications": "alerts",
                "min_free_gb": 5,
            }
        },
    )
    runtime = CrewWorkerRuntime(factory, Router())
    for value in (4, 4.5, 5.5, 7):
        free[0] = value
        await runtime.work("crew")
    with factory() as session:
        assert len(session.scalars(select(ReminderEvent)).all()) == 2
        crew = session.get(CrewInstance, "crew")
        crew.permissions = ["model:invoke", "device:read"]
        session.commit()
    free[0] = 3
    await runtime.work("crew")
    with factory() as session:
        assert len(session.scalars(select(ReminderEvent)).all()) == 2


@pytest.mark.asyncio
async def test_unavailable_drive_reports_failure_without_inventing_metrics(factory, monkeypatch):
    def failed(drive):
        raise PermissionError("fixture")

    monkeypatch.setattr("mavo.crew_workers.read_storage_metrics", failed)
    add_crew(
        factory,
        template="computer",
        permissions=["model:invoke", "device:read", "notifications:show"],
        configuration_plan={
            "device_monitor": {"kind": "storage", "drive": "Z:", "notifications": "each_check"}
        },
    )
    runtime = CrewWorkerRuntime(factory, Router())
    await runtime.work("crew")
    with factory() as session:
        crew = session.get(CrewInstance, "crew")
        assert crew.runtime_state == "needs_input" and "PermissionError" in crew.checkpoint
        assert session.scalar(select(ReminderEvent)).message.startswith("Could not read Z:")
        assert session.scalar(select(CrewRun)).status == "failed"


@pytest.mark.asyncio
async def test_expired_claim_recovers_without_losing_checkpoint(factory):
    add_crew(factory, runtime_state="working", checkpoint="Saved before restart")
    with factory() as session:
        session.add(
            CrewRun(
                crew_id="crew",
                crew_name="Worker",
                route_name="Test",
                provider="ollama",
                model="test",
                prompt="Task",
                kind="background",
                runtime_generation=1,
                lease_until=utcnow() - timedelta(seconds=1),
            )
        )
        session.commit()
        recover_interrupted_runs(session)
        assert session.scalar(select(CrewRun)).status == "interrupted"
        assert session.get(CrewInstance, "crew").checkpoint == "Saved before restart"
    router = Router()
    await CrewWorkerRuntime(factory, router).work("crew")
    assert "Saved before restart" in router.calls[0][0][0]["content"]


@pytest.mark.asyncio
async def test_invalid_checkpoint_blocks_instead_of_spending_tokens_forever(factory):
    add_crew(factory)
    router = Router("made-up-state")
    runtime = CrewWorkerRuntime(factory, router)
    await runtime.work("crew")
    await runtime.tick()
    with factory() as session:
        crew = session.get(CrewInstance, "crew")
        assert crew.runtime_state == "error" and crew.is_running
        assert "valid work checkpoint" in crew.last_runtime_error
    # One reply, one repair attempt that says what was wrong, then it stops.
    assert len(router.calls) == 2
    assert "not valid" in router.calls[1][0][-1]["content"]


def test_durable_claim_rejects_another_runtime_without_double_spending(factory):
    add_crew(factory)
    first = CrewWorkerRuntime(factory, Router())
    second = CrewWorkerRuntime(factory, Router())
    assert first.claim("crew", utcnow()) is not None
    assert second.claim("crew", utcnow()) is None
    with factory() as session:
        assert session.get(CrewInstance, "crew").daily_calls_used == 1
        assert len(session.scalars(select(CrewRun)).all()) == 1
        recover_interrupted_runs(session)
        assert session.scalar(select(CrewRun)).status == "running"


@pytest.mark.asyncio
async def test_shutdown_preserves_running_intent_and_checkpoint(factory):
    add_crew(factory, checkpoint="Saved progress")
    router = Router()
    router.release = asyncio.Event()
    runtime = CrewWorkerRuntime(factory, router)
    service = asyncio.create_task(runtime.run())
    await asyncio.wait_for(router.entered.wait(), timeout=5)
    service.cancel()
    await asyncio.gather(service, return_exceptions=True)
    with factory() as session:
        crew = session.get(CrewInstance, "crew")
        assert crew.is_running and crew.checkpoint == "Saved progress"
        assert crew.runtime_state == "waiting"
        assert session.scalar(select(CrewRun)).status == "interrupted"


@pytest.mark.asyncio
async def test_deleting_during_a_model_call_never_recreates_the_crew_or_result(factory):
    from mavo.crew_workers import stop_crew

    add_crew(factory)
    router = Router()
    router.release = asyncio.Event()
    runtime = CrewWorkerRuntime(factory, router)
    task = asyncio.create_task(runtime.work("crew"))
    await asyncio.wait_for(router.entered.wait(), timeout=5)
    with factory() as session:
        crew = session.get(CrewInstance, "crew")
        stop_crew(session, crew)
        session.delete(crew)
        session.commit()
    router.release.set()
    await task
    with factory() as session:
        assert session.get(CrewInstance, "crew") is None
        assert session.scalars(select(CrewRun)).all() == []
        assert session.scalars(select(ReminderEvent)).all() == []


@pytest.mark.asyncio
async def test_enabled_browser_denial_is_repaired_once_and_billed_within_budget(factory):
    add_crew(
        factory,
        template="operator",
        permissions=["model:invoke", "action:browser.open", "action:browser.read"],
    )

    class ToolRouter(Router):
        async def chat(self, provider, model, messages, system, credential_id=None):
            self.calls.append((messages, system))
            if len(self.calls) == 1:
                return json.dumps(
                    {
                        "state": "waiting_for_input",
                        "summary": "Cannot browse",
                        "next_step": "Enable browser",
                        "question": "Please enable browser tools.",
                    }
                )
            return json.dumps(
                {
                    "state": "continue",
                    "summary": "Requesting the authorized browser",
                    "next_step": "Analyze returned page",
                    "actions": [
                        {"operation": "browser.open", "arguments": {"url": "https://example.com"}},
                        {"operation": "browser.read", "arguments": {}},
                    ],
                }
            )

    class Tools:
        rules = True

        def __init__(self):
            self.operations = []

        def available(self, *args):
            return {"browser.open": {}, "browser.read": {}}

        def propose(self, draft, *args):
            self.operations.append(draft.operation)
            return {"id": len(self.operations) - 1}

        async def maybe_execute(self, id):
            return {"operation": self.operations[id], "status": "completed"}

    router = ToolRouter()
    runtime = CrewWorkerRuntime(factory, router)
    runtime.actions = Tools()
    await runtime.work("crew")
    assert runtime.actions.operations == ["browser.open", "browser.read"]
    assert len(router.calls) == 2
    with factory() as s:
        assert s.scalar(select(CrewRun)).model_calls == 2
        assert s.get(CrewInstance, "crew").daily_calls_used == 2
        assert s.get(CrewInstance, "crew").runtime_state == "waiting"


@pytest.mark.asyncio
async def test_interval_reviews_continue_and_same_listing_is_not_notified_twice(factory):
    add_crew(factory, permissions=["model:invoke", "notifications:show"])

    class ReviewRouter(Router):
        async def chat(self, *args, **kwargs):
            return json.dumps(
                {
                    "state": "wait_for_next_review",
                    "summary": "Review complete",
                    "next_step": "Wait for next review",
                    "notification": "One new job is open.",
                    "notification_ids": ["job:123"],
                }
            )

    runtime = CrewWorkerRuntime(factory, ReviewRouter())
    await runtime.work("crew")
    await runtime.work("crew")
    with factory() as s:
        crew = s.get(CrewInstance, "crew")
        assert crew.is_running and crew.runtime_state == "waiting" and crew.next_run_at
        assert crew.observation["notified_items"] == ["job:123"]
        assert len(s.scalars(select(ReminderEvent)).all()) == 1
