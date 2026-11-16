"""Evidence is checked against authorized, assignment-scoped reads, not browser-only activity."""

from mavo.database import CrewInstance, utcnow
from mavo.outcomes import verify_sources


def test_feed_receipts_support_exact_evidence_and_revocation(client):
    runtime = client.app.state.crew_runtime
    quote = "Researchers published a new open source language model today."
    evidence = [{"kind": "source", "url": "https://example.com/news", "quote": quote}]
    with runtime.factory() as session:
        worker = CrewInstance(
            id="feed-worker",
            name="Feed reader",
            template_id="research",
            responsibilities="Read news",
            provider="ollama",
            model="test",
            permissions=["feeds:hacker-news:read"],
            configuration_plan={"task_run_id": "assignment"},
            observation={
                "source_receipts": {
                    "scope": "assignment",
                    "snapshots": {
                        "feeds:hacker-news": {
                            "items": [
                                {"id": "reported", "url": evidence[0]["url"], "title": quote},
                                {
                                    "id": "not-reported",
                                    "url": "https://example.com/other",
                                    "title": "Another article not included in the digest",
                                },
                            ]
                        }
                    },
                }
            },
        )
        session.add(worker)
        session.flush()
        assert verify_sources(session, worker.id, utcnow(), evidence)["step"] == "evidence_checked"
        assert verify_sources(session, worker.id, utcnow(), evidence)["source_item_ids"] == [
            "reported"
        ]
        mismatched = [{**evidence[0], "url": "https://example.com/other"}]
        assert verify_sources(session, worker.id, utcnow(), mismatched)["step"] == "unverified"
        prefix = [{**evidence[0], "url": "https://example.com/new"}]
        assert verify_sources(session, worker.id, utcnow(), prefix)["step"] == "unverified"
        worker.permissions = []
        assert verify_sources(session, worker.id, utcnow(), evidence)["step"] == "unverified"


def test_lookup_evidence_cannot_cross_assignment_scopes(client):
    runtime = client.app.state.crew_runtime
    quote = "A real article title that the lookup actually returned."
    evidence = [{"kind": "source", "url": "https://example.com/news", "quote": quote}]
    with runtime.factory() as session:
        worker = CrewInstance(
            id="lookup-worker",
            name="Lookup reader",
            template_id="research",
            responsibilities="Read",
            provider="ollama",
            model="test",
            configuration_plan={"task_run_id": "current"},
            observation={
                "lookup_progress": {
                    "scope": "previous",
                    "receipts": [
                        {
                            "response": {
                                "status": "ok",
                                "result": {"url": evidence[0]["url"], "title": quote},
                            }
                        }
                    ],
                }
            },
        )
        session.add(worker)
        session.flush()
        assert verify_sources(session, worker.id, utcnow(), evidence)["step"] == "unverified"
        worker.observation = {
            "lookup_progress": {**worker.observation["lookup_progress"], "scope": "current"}
        }
        assert verify_sources(session, worker.id, utcnow(), evidence)["step"] == "evidence_checked"


def test_successfully_read_empty_feeds_are_quiet_but_not_proof_of_zero_job_listings(client):
    runtime = client.app.state.crew_runtime
    with runtime.factory() as session:
        worker = CrewInstance(
            id="empty-feed",
            name="News",
            template_id="research",
            responsibilities="Read",
            provider="ollama",
            model="test",
            permissions=["feeds:hacker-news:read"],
            configuration_plan={"task_run_id": "current"},
            observation={
                "source_receipts": {
                    "scope": "current",
                    "snapshots": {"feeds:hacker-news": {"provider": "feeds", "items": []}},
                }
            },
        )
        session.add(worker)
        session.flush()
        assert verify_sources(session, worker.id, utcnow(), [])["quiet"] is True
        assert (
            verify_sources(session, worker.id, utcnow(), [], require_listings=True)["step"]
            == "unverified"
        )


def test_failed_downstream_work_does_not_hide_collected_news(client):
    from mavo.crew_composition import finish_cycle
    from mavo.database import CrewAgent, TaskRun

    runtime = client.app.state.crew_runtime
    with runtime.factory() as session:
        source = CrewInstance(
            id="news-parent",
            name="Digest",
            template_id="research",
            responsibilities="Read",
            provider="ollama",
            model="test",
            checkpoint="Previous successful digest",
        )
        worker = CrewInstance(
            id="news-adapter",
            name="Reader",
            hidden=True,
            template_id="research",
            responsibilities="Read",
            provider="ollama",
            model="test",
            observation={"pending_seen_items": ["article"], "seen_items": []},
        )
        session.add_all([source, worker])
        session.flush()
        session.add(
            CrewAgent(
                crew_id=source.id,
                worker_id=worker.id,
                key="read",
                name="Read",
                role="Reader",
                instructions="Read",
                execution={"task_id": "news-cycle"},
            )
        )
        run = TaskRun(
            id="news-cycle",
            crew_id=source.id,
            crew_generation=source.runtime_generation,
            title="News",
            status="failed",
            error="Writer failed",
            plan={},
        )
        session.add(run)
        session.flush()
        finish_cycle(session, run)
        assert worker.observation["seen_items"] == []
        assert source.checkpoint == "Previous successful digest"
        run.status, run.result = "done", "Digest completed"
        finish_cycle(session, run)
        assert worker.observation["seen_items"] == ["article"]
        assert worker.observation["pending_seen_items"] == []


def test_crew_list_recovers_old_report_for_display_without_mutating_saved_state(client, auth):
    from mavo.database import TaskRun

    runtime = client.app.state.crew_runtime
    with runtime.factory() as session:
        crew = CrewInstance(
            id="news-report",
            name="News",
            template_id="research",
            responsibilities="Read",
            provider="ollama",
            model="test",
            runtime_state="error",
            checkpoint=None,
            last_runtime_error="Source evidence missing",
        )
        session.add(crew)
        session.flush()
        session.add(
            TaskRun(
                id="verified-news",
                crew_id=crew.id,
                title="Previous news",
                status="done",
                plan={},
                result="Verified earlier headlines",
            )
        )
        session.commit()
    listed = client.get("/api/v1/crew", headers=auth)
    assert listed.status_code == 200, listed.text
    view = next(c for c in listed.json() if c["id"] == "news-report")
    assert view["checkpoint"] == "Verified earlier headlines"
    assert view["last_runtime_error"] == "Source evidence missing"
    with runtime.factory() as session:
        assert session.get(CrewInstance, "news-report").checkpoint is None
