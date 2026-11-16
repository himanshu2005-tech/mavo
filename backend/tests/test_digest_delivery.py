"""Synthetic completed digests; no external model calls or real workspace writes."""

from datetime import timedelta
from types import SimpleNamespace

import pytest

from mavo.database import CrewInstance, ReminderEvent, TaskRun, utcnow
from mavo.reminders import create_due_reminders
from mavo.tasks import TaskRunner
from tests.test_crew_v2 import composition, seed, set_composition, spec, worker_for
from tests.test_multi_agent import make_session

QUOTE = "A source-confirmed headline long enough for evidence"


def test_complete_digest_is_not_cut_off_at_the_old_desktop_limit(digest):
    runner, factory, crew_id = digest
    message = "\n".join(f"Story {i}: " + "details " * 45 for i in range(8))
    assert len(message) > 1800
    with factory() as session:
        runner.queue_alert(session, "news-run", "News", message, "done", None)
        session.commit()
        assert session.query(ReminderEvent).one().message == message


@pytest.fixture
def digest(tmp_path):
    factory = make_session(tmp_path)
    runner = TaskRunner(SimpleNamespace(factory=factory))
    with factory() as session:
        crew = seed(session)
        crew.permissions = [*crew.permissions, "notifications:show", "feeds:bbc-tech:read"]
        # These tests cover how a report is delivered (once, after a restart, after midnight,
        # cancelled on revocation), so it is delivered at once rather than in Mavo's summary.
        crew.configuration_plan = {**(crew.configuration_plan or {}), "delivery": "now"}
        set_composition(session, crew, composition(spec("digest", verification="source_evidence")))
        agent = crew.agents[0]
        worker = worker_for(session, crew, agent)
        # Specialists can return results without independent desktop permission.
        # The parent Crew owns the user's approved delivery preference.
        worker.permissions = [p for p in crew.permissions if p != "notifications:show"]
        worker.observation = {
            "requested_notification": None,
            "source_receipts": {
                "scope": "news-run",
                "snapshots": {
                    "feeds:bbc-tech": {
                        "items": [{"url": "https://example.org/story", "title": QUOTE}]
                    }
                },
            },
        }
        agent.execution = {
            "task_id": "news-run",
            "verification": {
                "step": "evidence_checked",
                "quiet": False,
                "evidence": [
                    {
                        "kind": "source",
                        "url": "https://example.org/story",
                        "quote": QUOTE,
                        "evidence_id": "story-1",
                    }
                ],
            },
        }
        session.add(
            TaskRun(
                id="news-run",
                crew_id=crew.id,
                crew_generation=crew.runtime_generation,
                title="News",
                status="done",
                plan={},
                steps=[],
                attempts=1,
                result=QUOTE,
            )
        )
        session.commit()
        crew_id = crew.id
    yield runner, factory, crew_id
    factory.kw["bind"].dispose()


def test_verified_new_digest_queues_even_without_model_notification_and_survives_restart(digest):
    runner, factory, crew_id = digest
    runner.alert("news-run", "News", QUOTE, "end")
    runner.alert("news-run", "News", QUOTE, "end")
    with factory() as session:
        assert session.query(ReminderEvent).count() == 1
        assert create_due_reminders(session)[0].message == QUOTE
        agent = session.get(CrewInstance, crew_id).agents[0]
        assert agent.worker.observation["notified_source_ids"] == ["story-1"]
        # A separate completed run quoting the same evidence is not a new digest.
        run = session.get(TaskRun, "news-run")
        run.attempts = 2
        session.commit()
    TaskRunner(SimpleNamespace(factory=factory)).alert("news-run", "News", QUOTE, "end")
    with factory() as session:
        assert session.query(ReminderEvent).count() == 1


@pytest.mark.parametrize(
    "case",
    [
        "quiet",
        "unverified",
        "old_assignment",
        "notification_revoked",
        "feed_revoked",
        "worker_revoked",
        "private_test",
        "stopped_generation",
    ],
)
def test_no_fallback_for_quiet_unverified_revoked_or_stale_work(digest, case):
    runner, factory, crew_id = digest
    with factory() as session:
        crew = session.get(CrewInstance, crew_id)
        agent = crew.agents[0]
        if case == "quiet":
            agent.execution = {
                **agent.execution,
                "verification": {"step": "evidence_checked", "quiet": True},
            }
        elif case == "unverified":
            agent.execution = {**agent.execution, "verification": {"step": "unverified"}}
        elif case == "old_assignment":
            agent.execution = {**agent.execution, "task_id": "some-other-run"}
        elif case == "notification_revoked":
            crew.permissions = [p for p in crew.permissions if p != "notifications:show"]
        elif case == "feed_revoked":
            crew.permissions = [p for p in crew.permissions if not p.startswith("feeds:")]
        elif case == "worker_revoked":
            agent.worker.permissions = []
        elif case == "private_test":
            agent.worker.configuration_plan = {"first_run_source": crew.id}
        elif case == "stopped_generation":
            crew.runtime_generation += 1
        session.commit()
    runner.alert("news-run", "News", QUOTE, "end")
    with factory() as session:
        assert session.query(ReminderEvent).count() == 0


def test_outbox_and_deduplication_state_rollback_together(digest):
    runner, factory, crew_id = digest
    with factory() as session:
        runner.alert("news-run", "News", QUOTE, "end", session=session)
        session.flush()
        session.rollback()  # e.g. completion transaction failed/crashed
    with factory() as session:
        assert session.query(ReminderEvent).count() == 0
        assert (
            not session.get(CrewInstance, crew_id)
            .agents[0]
            .worker.observation.get("notified_source_ids")
        )
    runner.alert("news-run", "News", QUOTE, "end")
    with factory() as session:
        assert session.query(ReminderEvent).count() == 1


def test_unread_digest_survives_midnight_and_revocation_still_cancels_it(digest):
    runner, factory, crew_id = digest
    runner.alert("news-run", "News", QUOTE, "end")
    with factory() as session:
        assert len(create_due_reminders(session, utcnow().astimezone() + timedelta(days=1))) == 1
        crew = session.get(CrewInstance, crew_id)
        crew.permissions = []
        session.commit()
        assert create_due_reminders(session) == []
        assert session.query(ReminderEvent).one().status == "cancelled"


def test_restart_repairs_a_pre_fix_missing_digest_without_re_running_it(digest):
    runner, factory, crew_id = digest
    original = utcnow() - timedelta(minutes=30)
    with factory() as session:
        crew = session.get(CrewInstance, crew_id)
        crew.observation = {"last_task_id": "news-run"}
        session.get(TaskRun, "news-run").finished_at = original
        session.commit()
    runner.recover()
    runner.recover()
    with factory() as session:
        event = session.query(ReminderEvent).one()
        assert event.message == QUOTE
        from mavo.crew_workers import as_utc

        assert as_utc(event.scheduled_for) == original
        assert event.status == "pending"


@pytest.fixture
def helpers_only(tmp_path):
    """An agent whose plan is only reads and helpers ("a quote every 15 minutes"): no member
    agent takes part in its runs."""
    factory = make_session(tmp_path)
    runner = TaskRunner(SimpleNamespace(factory=factory))
    with factory() as session:
        crew = seed(session)
        crew.permissions = [*crew.permissions, "notifications:show"]
        crew.configuration_plan = {"draft": {"requested_notifications": True}, "delivery": "now"}
        session.commit()
        crew_id = crew.id

    def finish(run_id: str, result: str):
        with factory() as session:
            crew = session.get(CrewInstance, crew_id)
            session.add(
                TaskRun(
                    id=run_id,
                    crew_id=crew_id,
                    crew_generation=crew.runtime_generation,
                    title="Momentum Spark",
                    status="done",
                    plan={},
                    steps=[],
                    attempts=1,
                    result=result,
                )
            )
            session.commit()
        runner.alert(run_id, "Momentum Spark", result, "end")

    yield factory, finish
    factory.kw["bind"].dispose()


def test_a_run_of_helpers_sends_its_result_as_the_alert_the_person_asked_for(helpers_only):
    """A quote agent ran every 15 minutes and no alert ever came: its plan had no member agent
    to flag anything, so each run counted as a quiet check."""
    factory, finish = helpers_only
    finish("r1", "Message: Small steps still move you forward.")
    finish("r2", "Nothing new.")  # its writer says there is nothing
    finish("r3", "Message: Small steps still move you forward.")  # the same, again
    finish("r4", "Message: Rest, then do the next small thing.")
    with factory() as session:
        sent = [e.message for e in session.query(ReminderEvent).order_by(ReminderEvent.created_at)]
    assert sent == [
        "Message: Small steps still move you forward.",
        "Message: Rest, then do the next small thing.",
    ]


def test_an_agent_that_did_not_ask_for_alerts_stays_quiet(helpers_only):
    factory, finish = helpers_only
    with factory() as session:
        crew = session.query(CrewInstance).one()
        crew.configuration_plan = {"draft": {"requested_notifications": False}}
        session.commit()
    finish("r1", "Message: hello")
    with factory() as session:
        assert session.query(ReminderEvent).count() == 0
