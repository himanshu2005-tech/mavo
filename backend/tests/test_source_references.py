"""Resource-reference and partial-report safety regressions. No live accounts or credits."""

import json
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from mavo.crew_workers import WorkCheckpoint, parse_checkpoint
from mavo.database import CrewInstance, ReminderEvent, TaskRun, utcnow
from mavo.outcomes import verify_sources
from mavo.source_snapshots import (
    FeedEvidenceError,
    SourceSelection,
    bounded_snapshots,
    resolve_source_items,
    validate_feed_checkpoint,
)
from tests.test_planner import planning  # noqa: F401


def snapshots(count=3):
    return bounded_snapshots(
        {
            "feeds:bbc-tech": {
                "items": [
                    {
                        "id": str(i),
                        "title": f"Original news headline number {i} from the connected publisher",
                        "url": f"https://example.org/story/{i}",
                    }
                    for i in range(count)
                ]
            }
        }
    )


def test_reference_ids_are_deterministic_source_scoped_and_do_not_mutate_cache():
    original = {
        "feeds:bbc-tech": {
            "items": [
                {
                    "id": "1",
                    "title": "News",
                    "url": "https://example.org/a",
                    "source_ref": "malicious",
                }
            ]
        }
    }
    before = deepcopy(original)
    first, second = bounded_snapshots(original), bounded_snapshots(original)
    ref = first["feeds:bbc-tech"]["items"][0]["source_ref"]
    assert ref.startswith("src_") and ref != "malicious"
    assert ref == second["feeds:bbc-tech"]["items"][0]["source_ref"]
    other = bounded_snapshots({"feeds:verge": original["feeds:bbc-tech"]})
    assert other["feeds:verge"]["items"][0]["source_ref"] != ref
    assert original == before


def test_selections_need_no_model_urls_quotes_or_even_a_prose_summary():
    data = snapshots()
    ref = data["feeds:bbc-tech"]["items"][0]["source_ref"]
    checkpoint = parse_checkpoint(json.dumps({"source_items": [ref]}))
    result = validate_feed_checkpoint(data, checkpoint)
    assert result["accepted_items"] == 1
    assert "Original news headline number 0" in checkpoint.summary
    assert "https://example.org/story/0" in checkpoint.summary
    assert len(checkpoint.evidence) == 1 and checkpoint.evidence[0].source_ref == ref
    assert checkpoint.notification is None


def test_invalid_selection_does_not_discard_valid_stories_or_retain_model_fabrication():
    data = snapshots(7)
    refs = [i["source_ref"] for i in data["feeds:bbc-tech"]["items"]]
    checkpoint = WorkCheckpoint(
        state="wait_for_next_review",
        summary="FABRICATED_STORY https://evil.example",
        notification="FABRICATED_STORY",
        source_items=[{"source_ref": r} for r in refs[:6]] + [{"source_ref": "unknown"}],
    )
    result = validate_feed_checkpoint(data, checkpoint)
    assert result["partial"] and result["accepted_items"] == 6
    assert len(checkpoint.evidence) == 6
    assert "Partial digest" in checkpoint.summary
    assert "FABRICATED" not in checkpoint.summary and "evil.example" not in checkpoint.summary
    assert "number 6" not in checkpoint.summary


def test_legacy_seven_citations_with_one_bad_quote_publish_only_the_six_valid_items():
    data = snapshots(7)
    evidence = [
        {"kind": "source", "quote": i["title"], "url": i["url"]}
        for i in data["feeds:bbc-tech"]["items"]
    ]
    evidence[3]["quote"] = "This invented claim is not present in any fetched story"
    checkpoint = WorkCheckpoint(
        state="wait_for_next_review", summary="This invented claim", evidence=evidence
    )
    result = validate_feed_checkpoint(data, checkpoint)
    assert result["unmatched_entries"] == [3] and result["accepted_items"] == 6
    assert "invented claim" not in checkpoint.summary and "number 3" not in checkpoint.summary
    assert "Partial digest" in checkpoint.summary


def test_all_unknown_references_fail_honestly_and_empty_reads_are_not_fabricated():
    checkpoint = WorkCheckpoint(
        state="wait_for_next_review",
        summary="Nothing new",
        source_items=[{"source_ref": "unknown"}],
    )
    with pytest.raises(FeedEvidenceError):
        validate_feed_checkpoint(snapshots(), checkpoint)
    assert validate_feed_checkpoint({"feeds:bbc-tech": {"items": []}}, checkpoint) == {}


def test_short_unicode_headlines_duplicate_ids_and_duplicate_urls_are_supported():
    data = bounded_snapshots(
        {
            "feeds:bbc-tech": {
                "items": [
                    {"id": "same", "title": "雨", "url": "https://example.org/a"},
                    {
                        "id": "same",
                        "title": "Another valid observed headline",
                        "url": "https://example.org/b",
                    },
                    {"id": "different", "title": "雨", "url": "https://example.org/a"},
                ]
            }
        }
    )
    refs = [SourceSelection(source_ref=i["source_ref"]) for i in data["feeds:bbc-tech"]["items"]]
    checkpoint = WorkCheckpoint(state="wait_for_next_review", summary="News", source_items=refs)
    result = validate_feed_checkpoint(data, checkpoint)
    assert result["accepted_items"] == 2
    assert "雨" in checkpoint.summary


def test_headline_only_feed_records_use_publisher_provenance_without_inventing_article_urls(client):
    data = bounded_snapshots(
        {
            "feeds:bbc-tech": {
                "items": [
                    {"id": "a", "title": "Story A"},
                    {"id": "b", "title": "Story B"},
                ]
            }
        }
    )
    checkpoint = parse_checkpoint(
        json.dumps(
            {"source_items": [item["source_ref"] for item in data["feeds:bbc-tech"]["items"]]}
        )
    )
    result = validate_feed_checkpoint(data, checkpoint)
    assert result["accepted_items"] == 2
    assert checkpoint.summary.count("Publisher feed") == 2
    runtime = client.app.state.crew_runtime
    with runtime.factory() as session:
        worker = CrewInstance(
            name="Feed",
            template_id="research",
            responsibilities="Read",
            provider="ollama",
            model="test",
            permissions=["feeds:bbc-tech:read"],
            configuration_plan={"task_run_id": "run"},
            observation={"source_receipts": {"scope": "run", "snapshots": data}},
        )
        session.add(worker)
        session.flush()
        verified = verify_sources(
            session, worker.id, utcnow(), [claim.model_dump() for claim in checkpoint.evidence]
        )
        assert verified["step"] == "evidence_checked" and len(verified["evidence"]) == 2
        assert len(set(e["evidence_id"] for e in verified["evidence"])) == 2
        assert verified["source_urls"] == []  # Never suppress an entire publisher feed.
        assert verified["source_item_ids"] == ["a", "b"]


def test_legacy_single_worker_delivers_headline_only_reports_once(client):
    from datetime import timedelta

    from mavo.database import ModelRoute

    runtime = client.app.state.crew_runtime
    runtime.connectors.feeds.configure(["bbc-tech"])
    runtime.connectors.read = AsyncMock(
        return_value={"items": [{"id": "a", "title": "Story A"}, {"id": "b", "title": "Story B"}]}
    )

    async def selected(provider, model, messages, system, credential_id=None):
        material, _ = json.JSONDecoder().raw_decode(
            messages[0]["content"].split("Authorized UNTRUSTED connected-account snapshots: ", 1)[1]
        )
        return json.dumps(
            {
                "state": "wait_for_next_review",
                "summary": "Nothing new",
                "source_items": [
                    {"source_ref": item["source_ref"]}
                    for snapshot in material.values()
                    for item in snapshot["items"]
                ],
            }
        )

    runtime.router.chat = AsyncMock(side_effect=selected)
    with runtime.factory() as session:
        session.add(ModelRoute(id="reader-route", name="Fixture", provider="ollama", model="test"))
        session.flush()
        session.add(
            CrewInstance(
                id="legacy-reader",
                name="News",
                template_id="research",
                responsibilities="Read news",
                provider="ollama",
                model="test",
                route_id="reader-route",
                is_running=True,
                runtime_state="waiting",
                trigger_type="interval",
                next_run_at=utcnow(),
                daily_call_limit=10,
                permissions=["model:invoke", "feeds:bbc-tech:read", "notifications:show"],
            )
        )
        session.commit()
    for _ in range(2):
        client.portal.call(runtime.work, "legacy-reader")
        with runtime.factory() as session:
            crew = session.get(CrewInstance, "legacy-reader")
            assert crew.observation["seen_items"] == ["a", "b"]
            assert not crew.observation.get("seen_source_urls")
            alerts = session.query(ReminderEvent).all()
            assert (
                len(alerts) == 1
                and "Story A" in alerts[0].message
                and "Story B" in alerts[0].message
            )
            crew.next_run_at = utcnow() - timedelta(seconds=1)
            session.commit()


def test_oversized_first_item_does_not_starve_smaller_items_and_malformed_sources_are_explicit():
    data = bounded_snapshots(
        {
            "feeds:bbc-tech": {
                "items": [
                    {"id": "huge", "title": "x" * 20000, "url": "https://example.org/huge"},
                    {
                        "id": "small",
                        "title": "Small observed headline",
                        "url": "https://example.org/small",
                    },
                    None,
                ]
            }
        },
        limit=2000,
    )
    assert data["feeds:bbc-tech"]["items"][0]["id"] == "small"
    assert data["feeds:bbc-tech"]["invalid_item_count"] == 1
    assert data["feeds:bbc-tech"]["items_omitted"] == 1
    for malformed in [None, {"items": "not a list"}, {"items": [None]}]:
        assert bounded_snapshots({"feeds:bbc-tech": malformed})["feeds:bbc-tech"]["error"]


@pytest.mark.parametrize(
    "key",
    [
        "github:issues",
        "github:pulls",
        "github:workflows",
        "google:calendar",
        "google:tasks",
        "google:gmail",
    ],
)
def test_generic_resource_evidence_is_per_item_and_references_cannot_cross_scopes(client, key):
    runtime = client.app.state.crew_runtime
    data = bounded_snapshots(
        {
            key: {
                "items": [
                    {
                        "id": "a",
                        "title": "An actual observed source record with sufficient detail",
                        "html_url": "https://example.org/a",
                    },
                    {
                        "id": "b",
                        "title": "Another source record not related to the first one",
                        "html_url": "https://example.org/b",
                    },
                ]
            }
        }
    )
    item = data[key]["items"][0]
    claims, rejected = resolve_source_items(data, [SourceSelection(source_ref=item["source_ref"])])
    assert not rejected
    with runtime.factory() as session:
        worker = CrewInstance(
            name="Source reader",
            template_id="research",
            responsibilities="Read",
            provider="ollama",
            model="test",
            permissions=[key + ":read"],
            configuration_plan={"task_run_id": "assignment"},
            observation={"source_receipts": {"scope": "assignment", "snapshots": data}},
        )
        session.add(worker)
        session.flush()
        result = verify_sources(session, worker.id, utcnow(), [claims[0].model_dump()])
        assert result["step"] == "evidence_checked"
        mixed = {**claims[0].model_dump(), "url": "https://example.org/b"}
        assert verify_sources(session, worker.id, utcnow(), [mixed])["step"] == "unverified"
        worker.configuration_plan = {"task_run_id": "other-assignment"}
        assert (
            verify_sources(session, worker.id, utcnow(), [claims[0].model_dump()])["step"]
            == "unverified"
        )
        worker.configuration_plan = {"task_run_id": "assignment"}
        worker.permissions = []
        assert (
            verify_sources(session, worker.id, utcnow(), [claims[0].model_dump()])["step"]
            == "unverified"
        )


@pytest.mark.parametrize("legacy", [False, True])
def test_partial_digest_commits_one_alert_and_does_not_consume_unreported_items(planning, legacy):
    from mavo.crew_composition import dispatch, set_composition
    from tests.test_crew_v2 import composition, seed, spec
    from tests.test_tasks import setup, wait

    client, auth, runtime, _ = planning
    setup(client, auth)
    runtime.connectors.feeds.configure(["bbc-tech"])
    cached = {
        "provider": "feeds",
        "items": [
            {
                "id": str(i),
                "title": f"Observed report headline {i} with genuine source information",
                "url": f"https://example.org/{i}",
            }
            for i in range(7)
        ],
    }
    runtime.connectors.read = AsyncMock(return_value=cached)

    async def model(provider, model, messages, system, credential_id=None):
        marker = "Authorized UNTRUSTED connected-account snapshots: "
        material, _ = json.JSONDecoder().raw_decode(messages[0]["content"].split(marker, 1)[1])
        items = material["feeds:bbc-tech"]["items"]
        result = {"state": "wait_for_next_review", "summary": "UNSUPPORTED_OUTPUT"}
        if legacy:
            result["evidence"] = [
                {"kind": "source", "quote": item["title"], "url": item["url"]} for item in items[:6]
            ]
            result["evidence"].append(
                {
                    "kind": "source",
                    "quote": "This unsupported quotation was invented by the model",
                    "url": "https://example.org/invented",
                }
            )
        else:
            result["source_items"] = [{"source_ref": item["source_ref"]} for item in items[:6]] + [
                {"source_ref": "unknown"}
            ]
        return json.dumps(result)

    runtime.router.chat = AsyncMock(side_effect=model)
    with runtime.factory() as session:
        crew = seed(session, max_model_calls=5)
        crew.permissions = ["model:invoke", "notifications:show", "feeds:bbc-tech:read"]
        set_composition(
            session,
            crew,
            composition(
                spec(
                    "reader",
                    verification="source_evidence",
                    capabilities=["feeds:bbc-tech:read"],
                    max_model_calls=5,
                )
            ),
        )
        crew_id = crew.id
        session.commit()
    assert client.portal.call(dispatch, runtime, crew_id)
    with runtime.factory() as session:
        run_id = session.scalar(select(TaskRun.id).where(TaskRun.crew_id == crew_id))
    run = wait(client, auth, run_id)
    assert run["status"] == "done", run
    assert runtime.router.chat.await_count == 1
    with runtime.factory() as session:
        source = session.get(CrewInstance, crew_id)
        worker = source.agents[0].worker
        assert worker.observation["seen_items"] == [str(i) for i in range(6)]
        assert source.agents[0].execution["verification"]["partial"]
        alert = session.scalar(select(ReminderEvent).where(ReminderEvent.crew_id == crew_id))
        assert (
            alert
            and "Partial digest" in alert.message
            and "UNSUPPORTED_OUTPUT" not in alert.message
        )
        assert "headline 5" in alert.message and "headline 6" not in alert.message
        # The unselected seventh story remains available; same URL under a new ID doesn't repeat.
        fresh = {}
        remaining = runtime.only_new(
            worker.observation,
            [{**cached["items"][0], "id": "duplicate-id"}, cached["items"][6]],
            fresh,
        )
        assert [item["id"] for item in remaining] == ["6"]


def test_very_long_links_are_not_truncated_and_deferred_stories_are_not_marked_reported():
    raw = {
        "feeds:bbc-tech": {
            "items": [
                {
                    "id": str(i),
                    "title": "Original title " + "n" * 400,
                    "url": "https://example.org/" + str(i) + "/" + "x" * 1750,
                }
                for i in range(12)
            ]
        }
    }
    data = bounded_snapshots(raw, limit=50000)
    checkpoint = WorkCheckpoint(
        state="wait_for_next_review",
        summary="News",
        source_items=[
            {"source_ref": item["source_ref"]} for item in data["feeds:bbc-tech"]["items"]
        ],
    )
    result = validate_feed_checkpoint(data, checkpoint)
    assert len(checkpoint.summary) <= 12000
    assert 0 < result["accepted_items"] < 8 and result["deferred_items"] > 0
    assert len(checkpoint.evidence) == result["accepted_items"]
    assert all(e.url in checkpoint.summary for e in checkpoint.evidence)


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "file:///secret",
        "https://127.0.0.1/a",
        "https://user:pass@example.org/a",
        "https://example.org/" + "x" * 2200,
    ],
)
def test_invalid_feed_links_cannot_become_referenced_sources(url):
    from mavo.resources.feeds import safe_link

    assert not safe_link(url)
    data = bounded_snapshots(
        {"feeds:bbc-tech": {"items": [{"id": "a", "title": "Observed title", "url": url}]}}
    )
    assert data["feeds:bbc-tech"]["items"] == [] and data["feeds:bbc-tech"]["error"]


@pytest.mark.parametrize("state", ["unavailable", "stale", "disconnected"])
def test_unavailable_and_stale_resource_data_cannot_establish_fresh_results(client, state):
    runtime = client.app.state.crew_runtime
    data = snapshots(1)
    item = data["feeds:bbc-tech"]["items"][0]
    data["feeds:bbc-tech"]["status"] = state
    with runtime.factory() as session:
        worker = CrewInstance(
            name="News",
            template_id="research",
            responsibilities="Read",
            provider="ollama",
            model="test",
            permissions=["feeds:bbc-tech:read"],
            configuration_plan={"task_run_id": "run"},
            observation={"source_receipts": {"scope": "run", "snapshots": data}},
        )
        session.add(worker)
        session.flush()
        result = verify_sources(
            session,
            worker.id,
            utcnow(),
            [{"kind": "source", "quote": item["title"], "url": item["url"]}],
        )
        assert result["step"] == "unverified" and not result["quiet"]
        assert result["unavailable_sources"] == ["feeds:bbc-tech"]


def test_browser_source_read_is_assignment_fresh_and_revocable(client):
    from datetime import timedelta

    from mavo.database import ActionRequest

    runtime = client.app.state.crew_runtime
    quote = "This observed listing contains a real software internship opportunity"
    evidence = [{"kind": "source", "url": "https://example.org/jobs", "quote": quote}]
    with runtime.factory() as session:
        worker = CrewInstance(
            name="Browser reader",
            template_id="research",
            responsibilities="Read",
            provider="ollama",
            model="test",
            permissions=["action:browser.read"],
        )
        session.add(worker)
        session.flush()
        began = utcnow()
        read = ActionRequest(
            crew_id=worker.id,
            account_subject="local",
            operation="browser.read",
            source="crew",
            arguments={},
            fingerprint="read",
            status="completed",
            result={"url": evidence[0]["url"], "text": quote},
            created_at=began - timedelta(seconds=5),
        )
        session.add(read)
        session.flush()
        assert verify_sources(session, worker.id, began, evidence)["step"] == "unverified"
        read.created_at = began + timedelta(seconds=1)
        session.flush()
        assert verify_sources(session, worker.id, began, evidence)["step"] == "evidence_checked"
        # A malformed sibling claim does not crash verification or count as supported.
        partial = verify_sources(session, worker.id, began, [*evidence, {"kind": "invented"}])
        assert partial["partial"] and partial["rejected_evidence_count"] == 1
        worker.permissions = []
        assert verify_sources(session, worker.id, began, evidence)["step"] == "unverified"


@pytest.mark.parametrize(
    "key", ["github:issues", "google:gmail", "google:calendar", "google:tasks"]
)
def test_empty_authorized_reads_are_quiet_but_never_prove_writes_or_job_search_completion(
    client, key
):
    runtime = client.app.state.crew_runtime
    with runtime.factory() as session:
        worker = CrewInstance(
            name="Read only",
            template_id="research",
            responsibilities="Read",
            provider="ollama",
            model="test",
            permissions=[key + ":read"],
            configuration_plan={"task_run_id": "read"},
            observation={"source_receipts": {"scope": "read", "snapshots": {key: {"items": []}}}},
        )
        session.add(worker)
        session.flush()
        assert verify_sources(session, worker.id, utcnow(), [])["quiet"]
        assert not verify_sources(session, worker.id, utcnow(), [], require_listings=True)["quiet"]
        worker.permissions = [*worker.permissions, "google:calendar:write"]
        assert not verify_sources(session, worker.id, utcnow(), [])["quiet"]
