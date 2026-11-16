"""News protocol regressions with synthetic feeds and model output; no live credits."""

import asyncio
import json
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from mavo.database import CrewInstance
from mavo.google_auth import GoogleConnectionError
from mavo.model_router import WORKER_SCHEMA
from mavo.schemas import CoordinatorDecision
from mavo.source_snapshots import bounded_snapshots
from tests.test_planner import planning, turn  # noqa: F401

HEADLINE = "Researchers release a new open source assistant toolkit today"
ITEM = {"id": "story", "title": HEADLINE, "url": "https://example.org/story"}


def test_all_fourteen_sources_keep_representation_without_mutating_cache():
    snapshots = {
        f"feeds:{i}": {
            "provider": "feeds",
            "items": [{**ITEM, "id": f"{i}-{n}", "title": HEADLINE + str(n)} for n in range(20)],
        }
        for i in range(14)
    }
    original = deepcopy(snapshots)
    bounded = bounded_snapshots(snapshots, limit=10000)
    assert len(bounded) == 14 and len(json.dumps(bounded)) <= 10000
    assert all(value["items"] for value in bounded.values())
    assert all(value["items_omitted"] > 0 for value in bounded.values())
    assert snapshots == original


def test_one_feed_reader_gets_only_user_selected_feeds_and_passes_real_first_run(planning):
    client, auth, runtime, coordinator = planning
    del runtime.verify_first_run
    route = client.post(
        "/api/v1/routes",
        headers=auth,
        json={
            "name": "Agent",
            "provider": "ollama",
            "model": "test",
        },
    ).json()
    runtime.connectors.feeds.configure(["bbc-tech", "hacker-news"])
    cached = {"provider": "feeds", "items": [dict(ITEM)]}
    runtime.connectors.read = AsyncMock(return_value=cached)
    coordinator.decision = CoordinatorDecision.model_validate(
        {
            "reply": "Here is the draft",
            "crew_proposal": {
                "name": "News Hourly",
                "template_id": "research",
                "responsibilities": "A short digest of new headlines every hour.",
                "trigger_type": "interval",
                "max_model_calls": 20,
                "daily_call_limit": 96,
                "route_id": route["id"],
                "requested_notifications": True,
                "requested_connector_capabilities": [
                    "feeds:bbc-tech:read",
                    "feeds:hacker-news:read",
                ],
                "composition": {
                    "agents": [
                        {
                            "key": "digest",
                            "name": "Digest",
                            "role": "News reader",
                            "instructions": "Summarize supplied headlines.",
                            "capabilities": [],
                            "max_model_calls": 8,
                            "verification": "source_evidence",
                            "completion_criteria": "Provide source-backed headlines",
                        }
                    ],
                    "limits": {"model_calls": 20},
                },
            },
        }
    )
    created = turn(planning, "A news update every hour")["draft"]

    async def model(provider, model, messages, system, credential_id=None):
        assert WORKER_SCHEMA.get()["additionalProperties"] is False
        assert "SourceEvidence" in WORKER_SCHEMA.get()["$defs"]
        assert len(json.dumps(WORKER_SCHEMA.get())) < 4000
        assert WORKER_SCHEMA.get()["properties"]["source_items"]["minItems"] == 1
        assert "lookups" not in WORKER_SCHEMA.get()["properties"]
        assert "initial read-only setup preview" in messages[0]["content"]
        assert '"source_ref": "src_' in messages[0]["content"]
        assert "FEED DIGEST" in system and "OPEN DATA" not in system
        assert "This is an assigned bounded task" in system
        assert "This Crew is manual, so" not in system
        assert "DESKTOP ALERTS ARE ON" not in system  # private first run cannot deliver
        assert HEADLINE in messages[0]["content"]
        if runtime.router.chat.await_count == 1:
            return json.dumps(
                {
                    "state": "wait_for_next_review",
                    "summary": HEADLINE,
                    "evidence": [{"kind": "source", "url": ITEM["url"], "headline": HEADLINE}],
                }
            )
        assert "evidence.0.quote" in messages[-1]["content"]
        return json.dumps(
            {
                "state": "wait_for_next_review",
                "summary": HEADLINE,
                "actions": [],
                "evidence": [
                    {"kind": "source", "url": ITEM["url"] + "-rewritten", "quote": HEADLINE}
                ],
            }
        )

    runtime.router.chat = AsyncMock(side_effect=model)
    response = client.post(
        f"/api/v1/crew/{created['id']}/access",
        headers=auth,
        json={"connector_capabilities": ["feeds:bbc-tech:read"], "notifications": True},
    )
    assert response.status_code == 200, response.text
    added = response.json()
    # Missing requested source still blocks; no unapproved capability is inferred.
    assert added["status"] == "draft"
    assert added["agents"][0]["definition"]["capabilities"] == [
        "feeds:bbc-tech:read",
        "notifications:show",
    ]
    runtime.router.chat.assert_not_called()
    # Selecting both is a new explicit user grant, and now both are assigned to the one reader.
    # Refine the empty proposal: a fresh selection must not broaden a restricted role.
    coordinator.decision.crew_proposal.composition.agents[0].capabilities = []
    turn(planning, "Use both feeds", crew_id=created["id"])
    response = client.post(
        f"/api/v1/crew/{created['id']}/access",
        headers=auth,
        json={
            "connector_capabilities": ["feeds:bbc-tech:read", "feeds:hacker-news:read"],
            "notifications": True,
        },
    )
    added = response.json()
    assert added["status"] == "active", added["configuration_plan"]
    assert added["configuration_plan"]["first_run"]["measured_calls"] == 2
    assert added["configuration_plan"]["first_run"]["feed_checks"]["digest"]["bound_links"] == 1
    assert added["max_model_calls"] == 5
    assert runtime.router.chat.await_count == 2
    assert cached == {"provider": "feeds", "items": [ITEM]}
    assert WORKER_SCHEMA.get() is None

    # Internal workers are manual adapters, but scheduled Crew cycles must still filter
    # already delivered feed items and stay quiet on an unchanged second review.
    from sqlalchemy import select

    from mavo.database import ReminderEvent, TaskRun
    from tests.test_tasks import wait

    async def review(provider, model, messages, system, credential_id=None):
        assert "This is an assigned bounded task" in system
        assert "DESKTOP ALERTS ARE ON FOR THIS CREW" in system
        assert system.rstrip().endswith("review.")  # after the schema, not buried inside it
        if "there are NO new" in messages[0]["content"]:
            assert "minItems" not in WORKER_SCHEMA.get()["properties"]["source_items"]
            return json.dumps(
                {
                    "state": "wait_for_next_review",
                    "summary": "Nothing new.",
                    "notification": None,
                    "evidence": [],
                }
            )
        return json.dumps(
            {
                "state": "wait_for_next_review",
                "summary": HEADLINE,
                # A real successful digest must reach the desktop even when the
                # model omits its optional notification field (the live regression).
                "evidence": [{"kind": "source", "url": ITEM["url"], "quote": HEADLINE}],
            }
        )

    runtime.router.chat = AsyncMock(side_effect=review)
    for _ in range(2):
        client.portal.call(runtime.work, added["id"])
        with runtime.factory() as session:
            run_id = session.scalar(
                select(TaskRun.id)
                .where(TaskRun.crew_id == added["id"])
                .order_by(TaskRun.created_at.desc())
            )
        assert wait(client, auth, run_id)["status"] == "done"
    with runtime.factory() as session:
        assert session.query(ReminderEvent).count() == 1
        source = session.get(CrewInstance, added["id"])
        assert source.agents[0].worker.observation["seen_items"] == [ITEM["id"]]
    assert cached["items"] == [ITEM]


def test_unavailable_public_feed_does_not_discard_successful_sources(client):
    runtime = client.app.state.crew_runtime
    with runtime.factory() as session:
        crew = CrewInstance(
            id="feeds-worker",
            name="Reader",
            template_id="research",
            responsibilities="Read feeds",
            provider="ollama",
            model="test",
            is_running=True,
            permissions=["feeds:bbc-tech:read", "feeds:hacker-news:read"],
        )
        session.add(crew)
        session.commit()
    cached = {"provider": "feeds", "items": [dict(ITEM)]}

    async def read(provider, source):
        if source == "bbc-tech":
            raise GoogleConnectionError("Source temporarily unavailable")
        return cached

    runtime.connectors.read = AsyncMock(side_effect=read)
    results = asyncio.run(runtime.connector_snapshots(crew))
    assert results["feeds:bbc-tech"]["status"] == "unavailable"
    results["feeds:hacker-news"]["items"].clear()
    assert cached["items"] == [ITEM]


def test_all_feeds_unavailable_is_a_source_failure_not_fake_empty_news(client):
    runtime = client.app.state.crew_runtime
    with runtime.factory() as session:
        crew = CrewInstance(
            id="feeds-offline",
            name="Reader",
            template_id="research",
            responsibilities="Read feeds",
            provider="ollama",
            model="test",
            is_running=True,
            permissions=["feeds:bbc-tech:read"],
        )
        session.add(crew)
        session.commit()
    runtime.connectors.read = AsyncMock(side_effect=GoogleConnectionError("Unavailable"))
    with pytest.raises(GoogleConnectionError, match="None of the selected feeds"):
        asyncio.run(runtime.connector_snapshots(crew))


def test_invalid_checkpoint_explains_the_field_without_persisting_model_content(client, auth):
    from mavo.first_run import verify_first_run
    from tests.test_first_run import draft

    runtime = client.app.state.crew_runtime
    crew_id = draft(client, auth, runtime)
    marker = "DO_NOT_PERSIST_INVALID_CONTENT"
    runtime.router.chat = AsyncMock(
        return_value=json.dumps(
            {
                "state": "wait_for_next_review",
                "summary": "News",
                "notification": marker + "x" * 1100,
            }
        )
    )
    result = asyncio.run(verify_first_run(runtime, crew_id))
    assert result["status"] == "failed", result
    assert "notification: string_too_long" in result["message"]
    assert marker not in json.dumps(result)
    assert runtime.router.chat.await_count == 2


@pytest.mark.parametrize("repair_succeeds", [True, False])
def test_new_feeds_cannot_be_silenced_by_an_old_quiet_checkpoint(planning, repair_succeeds):
    """Reproduce the saved News Reader failure through actual worker + parent execution."""
    from sqlalchemy import select

    from mavo.crew_composition import dispatch, set_composition
    from mavo.database import ReminderEvent, TaskRun
    from tests.test_crew_v2 import composition, seed, spec
    from tests.test_tasks import setup, wait

    client, auth, runtime, _ = planning
    setup(client, auth)
    runtime.connectors.feeds.configure(["bbc-tech"])
    extra = {"id": "unreported", "title": HEADLINE + " tomorrow", "url": ITEM["url"] + "-next"}
    runtime.connectors.read = AsyncMock(return_value={"provider": "feeds", "items": [ITEM, extra]})
    with runtime.factory() as session:
        crew = seed(session, checkpoint="Previous successful digest", max_model_calls=5)
        crew.permissions = ["model:invoke", "feeds:bbc-tech:read", "notifications:show"]
        set_composition(
            session,
            crew,
            composition(
                spec(
                    "reader",
                    verification="source_evidence",
                    max_model_calls=5,
                    capabilities=["feeds:bbc-tech:read"],
                )
            ),
        )
        crew_id = crew.id
        session.commit()

    async def model(provider, model, messages, system, credential_id=None):
        assert "Mavo owns the delivered-item history" in system
        assert HEADLINE in messages[0]["content"]
        if runtime.router.chat.await_count == 1 or not repair_succeeds:
            return json.dumps(
                {
                    "state": "wait_for_next_review",
                    "summary": "Nothing new. I need the previous digest to distinguish new items.",
                    "evidence": [],
                }
            )
        assert "already deduplicated" in messages[-1]["content"]
        return json.dumps(
            {
                "state": "wait_for_next_review",
                "summary": HEADLINE,
                "evidence": [{"kind": "source", "quote": HEADLINE, "url": ITEM["url"]}],
            }
        )

    runtime.router.chat = AsyncMock(side_effect=model)
    assert client.portal.call(dispatch, runtime, crew_id)
    with runtime.factory() as session:
        run_id = session.scalar(select(TaskRun.id).where(TaskRun.crew_id == crew_id))
    run = wait(client, auth, run_id)
    assert run["status"] == "done", run
    assert runtime.router.chat.await_count == 2  # one shared bounded repair, never a loop
    with runtime.factory() as session:
        parent = session.get(CrewInstance, crew_id)
        observed = parent.agents[0].worker.observation
        if repair_succeeds:
            assert observed["seen_items"] == [ITEM["id"]]  # NOT extra, which wasn't reported
            assert session.query(ReminderEvent).count() == 1
        else:
            assert observed["seen_items"] == [ITEM["id"], extra["id"]]
            assert observed["feed_checkpoint"]["fallback"] == "feed_headlines"
            assert parent.last_runtime_error is None
            alerts = session.query(ReminderEvent).all()
            assert len(alerts) == 1
            assert "AI selection was unavailable" in alerts[0].message
            assert HEADLINE in alerts[0].message and extra["title"] in alerts[0].message
            assert "I need the previous digest" not in alerts[0].message
    traffic = client.get(f"/api/v1/orchestration/messages?crew_id={crew_id}", headers=auth)
    assert traffic.status_code == 200, traffic.text
    assert all(m["task_status"] == run["status"] for m in traffic.json())
