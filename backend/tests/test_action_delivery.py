"""Outbox eligibility from simulated completed receipts; no Google writes or model calls."""

from datetime import timedelta
from types import SimpleNamespace

import pytest

from mavo.database import ActionRequest, CrewInstance, GoogleAccount, ReminderEvent, TaskRun, utcnow
from mavo.tasks import TaskRunner
from tests.test_crew_v2 import composition, seed, set_composition, spec, worker_for
from tests.test_multi_agent import make_session


@pytest.fixture
def delivery(tmp_path):
    factory = make_session(tmp_path)
    runner = TaskRunner(SimpleNamespace(factory=factory))
    with factory() as session:
        crew = seed(session)
        crew.permissions = ["model:invoke", "notifications:show", "google:calendar:write"]
        set_composition(
            session, crew, composition(spec("plan", capabilities=["google:calendar:write"]))
        )
        agent = crew.agents[0]
        worker = worker_for(session, crew, agent)
        agent.execution = {"task_id": "plan-run"}
        worker.observation = {"requested_notification": None}
        session.add(
            GoogleAccount(
                id="google", subject="current", email="owner@example.com", name="Owner", scopes=[]
            )
        )
        run = TaskRun(
            id="plan-run",
            crew_id=crew.id,
            crew_generation=crew.runtime_generation,
            title="Daily Planner",
            status="done",
            plan={},
            steps=[],
            attempts=1,
            created_at=utcnow() - timedelta(seconds=10),
        )
        session.add(run)
        session.flush()
        session.add(
            ActionRequest(
                id="confirmed",
                crew_id=worker.id,
                source="crew",
                generation=worker.runtime_generation,
                account_subject="current",
                operation="calendar.create",
                arguments={},
                fingerprint="one",
                status="completed",
                result={"id": "provider-confirmed-event"},
            )
        )
        session.commit()
        crew_id = crew.id
    yield runner, factory, crew_id
    factory.kw["bind"].dispose()


def test_parent_notifies_verified_write_without_giving_specialist_desktop_access(delivery):
    runner, factory, crew_id = delivery
    with factory() as session:
        owner = session.get(CrewInstance, crew_id)
        assert "notifications:show" not in owner.agents[0].worker.permissions
        runner.queue_alert(session, "plan-run", "Planner", "Pickup scheduled", "end", None)
        session.commit()
        runner.queue_alert(session, "plan-run", "Planner", "Pickup scheduled", "end", None)
        session.commit()
        assert session.query(ReminderEvent).count() == 1
        assert "calendar.create (1)" in session.query(ReminderEvent).one().message


@pytest.mark.parametrize(
    "case",
    [
        "previewed",
        "pending",
        "executing",
        "failed",
        "old",
        "wrong-account",
        "old-generation",
        "other-assignment",
        "retired",
        "write-revoked",
        "desktop-revoked",
        "run-failed",
        "unrelated-tool",
    ],
)
def test_previews_failures_stale_receipts_and_revoked_access_do_not_publish(delivery, case):
    runner, factory, crew_id = delivery
    with factory() as session:
        owner = session.get(CrewInstance, crew_id)
        agent = owner.agents[0]
        action = session.get(ActionRequest, "confirmed")
        if case in {"previewed", "pending", "executing", "failed"}:
            action.status = case
        elif case == "old":
            action.created_at = utcnow() - timedelta(days=1)
        elif case == "wrong-account":
            action.account_subject = "other-user"
        elif case == "old-generation":
            action.generation -= 1
        elif case == "other-assignment":
            agent.execution = {"task_id": "another-run"}
        elif case == "retired":
            agent.status = "retired"
        elif case == "write-revoked":
            owner.permissions = ["model:invoke", "notifications:show"]
        elif case == "desktop-revoked":
            owner.permissions = ["model:invoke", "google:calendar:write"]
        elif case == "run-failed":
            session.get(TaskRun, "plan-run").status = "failed"
            # Failure alerts remain legitimate; specifically test the completed-changes gate.
            from mavo.action_delivery import completed_changes

            assert not completed_changes(session, session.get(TaskRun, "plan-run"), owner)
            return
        elif case == "unrelated-tool":
            action.operation = "terminal.run"
        session.flush()
        runner.queue_alert(session, "plan-run", "Planner", "Proposed plan", "end", None)
        session.commit()
        assert session.query(ReminderEvent).count() == 0
