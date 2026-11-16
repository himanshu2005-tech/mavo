"""Headline-only degradation uses actual receipts, never paid inference or account changes."""

import json
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from mavo.crew_workers import WorkCheckpoint
from mavo.database import CrewInstance, ReminderEvent, TaskRun
from mavo.source_snapshots import (
    FeedEvidenceError,
    bounded_snapshots,
    fallback_feed_headlines,
    validate_feed_checkpoint,
)
from tests.test_planner import planning  # noqa: F401


def inputs():
    return bounded_snapshots(
        {
            f"feeds:{source}": {
                "items": [
                    {
                        "id": f"{source}-{i}",
                        "title": f"Observed headline {source} number {i}",
                        "url": f"https://example.org/{source}/{i}",
                    }
                    for i in range(10)
                ]
            }
            for source in ("bbc-tech", "bbc-world", "verge")
        },
        limit=30000,
    )


def invalid(**extra):
    return WorkCheckpoint(
        state="wait_for_next_review",
        summary="FABRICATED ANALYSIS",
        notification="FABRICATED ALERT",
        **extra,
    )


def test_failed_model_selection_becomes_labelled_observed_headlines_without_mutating_inputs():
    data = inputs()
    original = deepcopy(data)
    with pytest.raises(FeedEvidenceError):
        validate_feed_checkpoint(data, invalid())
    result, checks = fallback_feed_headlines(data, invalid())
    assert checks["fallback"] == "feed_headlines" and checks["accepted_items"] == 8
    assert checks["partial"] and checks["deferred_items"] == 22
    assert "not an AI-ranked or analyzed digest" in result.summary
    assert "FABRICATED" not in result.summary and result.notification is None
    assert result.actions == [] and result.lookups == [] and result.question is None
    assert result.state == "wait_for_next_review" and not result.goal_met
    assert {e.url.split("/")[-2] for e in result.evidence} == {"bbc-tech", "bbc-world", "verge"}
    assert len(set(e.url for e in result.evidence)) == 8
    assert data == original


@pytest.mark.parametrize("status", ["stale", "disconnected", "failed", "unavailable"])
def test_fallback_never_turns_unavailable_sources_into_success_or_quiet_results(status):
    data = inputs()
    for value in data.values():
        value["status"] = status
    with pytest.raises(FeedEvidenceError, match="No current usable"):
        fallback_feed_headlines(data, invalid())
    with pytest.raises(FeedEvidenceError):
        fallback_feed_headlines({"feeds:bbc-tech": {"items": []}}, invalid())


def test_successful_feed_can_degrade_with_an_explicit_unavailable_source_note():
    data = inputs()
    data["feeds:verge"]["error"] = "Feed unavailable"
    result, _ = fallback_feed_headlines(data, invalid())
    assert "1 feed source(s) were unavailable" in result.summary
    assert all("/verge/" not in e.url for e in result.evidence)


def test_fallback_does_not_make_up_missing_links_or_copy_untrusted_source_references():
    data = bounded_snapshots(
        {
            "feeds:bbc-tech": {
                "items": [
                    {"id": "a", "title": "Short", "source_ref": "evil"},
                    {"id": "b", "title": "Other"},
                ]
            }
        }
    )
    result, checks = fallback_feed_headlines(data, invalid())
    assert checks["accepted_items"] == 2
    assert "Publisher feed" in result.summary
    assert all(e.source_ref.startswith("src_") for e in result.evidence)
    assert all(e.url == data["feeds:bbc-tech"]["items"][0]["feed_url"] for e in result.evidence)


def test_fallback_cannot_satisfy_other_resource_tools_or_review_decisions():
    with pytest.raises(FeedEvidenceError):
        fallback_feed_headlines({**inputs(), "github:issues": {"items": []}}, invalid())
    for checkpoint in [
        invalid(review_verdict="pass"),
        invalid(
            actions=[{"operation": "browser.open", "arguments": {"url": "https://example.org"}}]
        ),
        WorkCheckpoint(state="continue", summary="Read more"),
    ]:
        with pytest.raises(FeedEvidenceError):
            fallback_feed_headlines(inputs(), checkpoint)


@pytest.mark.parametrize("mode", ["missing", "unknown", "revoked"])
def test_parent_worker_fallback_delivery_recurrence_and_revocation(planning, mode):
    from mavo.crew_composition import dispatch, set_composition
    from tests.test_crew_v2 import composition, seed, spec
    from tests.test_tasks import setup, wait

    client, auth, runtime, _ = planning
    setup(client, auth)
    runtime.connectors.feeds.configure(["bbc-tech"])
    cached = {
        "items": [
            {
                "id": str(i),
                "title": f"Observed public headline number {i}",
                "url": f"https://example.org/{i}",
            }
            for i in range(3)
        ]
    }
    runtime.connectors.read = AsyncMock(return_value=cached)
    with runtime.factory() as session:
        parent = seed(session, max_model_calls=5)
        parent.permissions = ["model:invoke", "notifications:show", "feeds:bbc-tech:read"]
        set_composition(
            session,
            parent,
            composition(
                spec(
                    "reader",
                    verification="source_evidence",
                    max_model_calls=5,
                    capabilities=["feeds:bbc-tech:read"],
                )
            ),
        )
        crew_id = parent.id
        session.commit()

    async def model(*args, **kwargs):
        if mode == "revoked" and runtime.router.chat.await_count == 2:
            with runtime.factory() as session:
                parent = session.get(CrewInstance, crew_id)
                parent.permissions = ["model:invoke", "notifications:show"]
                session.commit()
        result = {"state": "wait_for_next_review", "summary": "FABRICATED MODEL PROSE"}
        if mode == "unknown":
            result["source_items"] = [{"source_ref": "unknown"}]
        return json.dumps(result)

    runtime.router.chat = AsyncMock(side_effect=model)

    def run():
        assert client.portal.call(dispatch, runtime, crew_id)
        with runtime.factory() as session:
            task_id = session.scalar(
                select(TaskRun.id)
                .where(TaskRun.crew_id == crew_id)
                .order_by(TaskRun.created_at.desc())
            )
        return wait(client, auth, task_id)

    result = run()
    assert runtime.router.chat.await_count == 2
    with runtime.factory() as session:
        parent = session.get(CrewInstance, crew_id)
        alerts = session.scalars(
            select(ReminderEvent).where(ReminderEvent.crew_id == crew_id)
        ).all()
        if mode == "revoked":
            assert result["status"] != "done", result
            assert not any("Observed public headline" in a.message for a in alerts)
            assert not parent.agents[0].worker.observation.get("seen_items")
            return
        assert result["status"] == "done", result
        assert len(alerts) == 1 and "AI selection was unavailable" in alerts[0].message
        assert "FABRICATED" not in alerts[0].message
        assert parent.agents[0].worker.observation["seen_items"] == ["0", "1", "2"]
        assert parent.last_runtime_error is None
    # Committed delivery survives another cycle; no duplicate popup and no repeated repair.
    assert run()["status"] == "done"
    assert runtime.router.chat.await_count == 3
    with runtime.factory() as session:
        assert session.query(ReminderEvent).filter_by(crew_id=crew_id).count() == 1


@pytest.mark.parametrize("reviewing", [False, True])
def test_legacy_delivery_and_reviewers_do_not_conflate_headlines_with_a_review(client, reviewing):
    from mavo.database import ModelRoute

    runtime = client.app.state.crew_runtime
    runtime.connectors.feeds.configure(["bbc-tech"])
    runtime.connectors.read = AsyncMock(
        return_value={
            "items": [
                {
                    "id": "story",
                    "title": "A genuine publisher-supplied headline",
                    "url": "https://example.org/story",
                }
            ]
        }
    )
    runtime.router.chat = AsyncMock(
        return_value=json.dumps(
            {
                "state": "wait_for_next_review",
                "summary": "No selected records",
            }
        )
    )
    with runtime.factory() as session:
        route = ModelRoute(name="Fixture", provider="ollama", model="fixture")
        session.add(route)
        session.flush()
        worker = CrewInstance(
            name="Legacy feed reader",
            template_id="research",
            responsibilities="Read feed headlines",
            route_id=route.id,
            provider="ollama",
            model="fixture",
            status="active",
            is_running=True,
            trigger_type="interval",
            max_model_calls=4,
            permissions=["model:invoke", "feeds:bbc-tech:read", "notifications:show"],
            configuration_plan={"review": reviewing},
        )
        session.add(worker)
        session.flush()
        worker_id = worker.id
        session.commit()
    client.portal.call(runtime.work, worker_id)
    assert runtime.router.chat.await_count == 2
    with runtime.factory() as session:
        worker = session.get(CrewInstance, worker_id)
        alerts = session.query(ReminderEvent).filter_by(crew_id=worker_id).all()
        if reviewing:
            assert worker.runtime_state == "error"
            # Said plainly to the person; the technical reason is kept as detail.
            assert "paused" in worker.last_runtime_error
            assert "did not select any" in worker.observation["error_detail"]
            assert not any("A genuine publisher" in a.message for a in alerts)
            assert not worker.observation.get("seen_items")
        else:
            assert worker.last_runtime_error is None
            assert len(alerts) == 1 and "A genuine publisher" in alerts[0].message
            assert "AI selection was unavailable" in alerts[0].message
            assert worker.observation["seen_items"] == ["story"]
