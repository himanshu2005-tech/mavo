"""Mail planning must ask for missing details and verify tools, not merely claim success."""

import json
import time

import pytest
from sqlalchemy import select

from mavo.crew_composition import dispatch, set_composition
from mavo.crew_workers import parse_checkpoint
from mavo.database import ActionRequest, ConversationEvent, CrewInstance, ReminderEvent, TaskRun
from mavo.mail_reviews import MailReviewError, validate_review, verify_mail_plan
from tests.test_action_delivery import delivery  # noqa: F401
from tests.test_actions import action_env  # noqa: F401
from tests.test_crew_v2 import composition, spec
from tests.test_mail_event_delivery import MAIL, answer, prepare
from tests.test_tasks import wait


def decision(follow_up=None, **extra):
    value = json.loads(answer())
    value["mail_decisions"][0].update(follow_up=follow_up, **extra)
    return value


def test_planner_cannot_silently_acknowledge_actionable_mail_without_a_resolution():
    with pytest.raises(MailReviewError, match="Resolve each actionable"):
        validate_review(parse_checkpoint(json.dumps(decision())), [MAIL], planning=True)


def test_missing_details_become_a_question_not_an_invented_event():
    checkpoint = parse_checkpoint(
        json.dumps(
            decision(
                "needs_details",
                question="Which day and time should I schedule the pickup, and how long?",
            )
        )
    )
    validate_review(checkpoint, [MAIL], planning=True)
    assert checkpoint.state == "waiting_for_input" and "Which day" in checkpoint.question
    assert not checkpoint.actions


def test_missing_details_cannot_execute_a_simultaneous_task_proposal():
    value = decision("needs_details", question="When should I reserve the pickup?")
    value["actions"] = [{"operation": "tasks.create", "arguments": {"title": "Unconfirmed pickup"}}]
    checkpoint = parse_checkpoint(json.dumps(value))
    assert checkpoint.actions
    validate_review(checkpoint, [MAIL], planning=True)
    assert not checkpoint.actions and checkpoint.question


@pytest.mark.parametrize("follow_up", ["planned", "already_tracked"])
def test_planner_cannot_claim_changes_or_duplicates_without_current_receipts(delivery, follow_up):
    _, factory, crew_id = delivery
    with factory() as session:
        owner = session.get(CrewInstance, crew_id)
        agent = owner.agents[0]
        agent.definition = {**agent.definition, "actions": ["calendar.create"]}
        agent.worker.observation = {
            "mail_review": {
                "scope": "plan-run",
                "subject": "current",
                "decisions": [{"important": True, "follow_up": follow_up, "tracked_item_ids": []}],
            }
        }
        session.get(ActionRequest, "confirmed").status = "failed"
        session.flush()
        with pytest.raises(MailReviewError):
            verify_mail_plan(session, agent, session.get(TaskRun, "plan-run"))


def test_confirmed_changes_and_observed_existing_items_are_accepted(delivery):
    _, factory, crew_id = delivery
    with factory() as session:
        agent = session.get(CrewInstance, crew_id).agents[0]
        agent.definition = {**agent.definition, "actions": ["calendar.create"]}
        review = {
            "scope": "plan-run",
            "subject": "current",
            "decisions": [{"important": True, "follow_up": "planned"}],
        }
        agent.worker.observation = {"mail_review": review}
        verify_mail_plan(session, agent, session.get(TaskRun, "plan-run"))
        review["decisions"] = [
            {
                "important": True,
                "follow_up": "already_tracked",
                "tracked_item_ids": ["calendar:existing"],
            }
        ]
        agent.worker.observation = {
            "mail_review": review,
            "google_input_ids": {"scope": "plan-run", "calendar": ["existing"]},
        }
        verify_mail_plan(session, agent, session.get(TaskRun, "plan-run"))


def test_plan_can_reuse_an_existing_task_while_creating_only_the_missing_event(delivery):
    _, factory, crew_id = delivery
    with factory() as session:
        agent = session.get(CrewInstance, crew_id).agents[0]
        agent.definition = {**agent.definition, "actions": ["calendar.create", "tasks.create"]}
        agent.worker.observation = {
            "mail_review": {
                "scope": "plan-run",
                "subject": "current",
                "decisions": [
                    {
                        "important": True,
                        "follow_up": "planned",
                        "tracked_item_ids": ["tasks:existing"],
                    }
                ],
            },
            "google_input_ids": {"scope": "plan-run", "tasks": ["existing"]},
        }
        verify_mail_plan(session, agent, session.get(TaskRun, "plan-run"))
        agent.worker.observation = {
            **agent.worker.observation,
            "google_input_ids": {"scope": "other-run", "tasks": ["existing"]},
        }
        with pytest.raises(MailReviewError, match="tasks"):
            verify_mail_plan(session, agent, session.get(TaskRun, "plan-run"))


def test_missing_mail_timing_reaches_durable_chat_question_and_desktop_queue(action_env):
    client, auth, runtime, crew_id = prepare(
        action_env,
        [
            json.dumps(
                decision(
                    "needs_details",
                    question="What date and time is the pickup, and how long should I reserve?",
                )
            )
        ],
        notify=False,
    )
    caps = ["google:gmail:read", "google:gmail:body", "google:calendar:write", "google:tasks:write"]
    with runtime.factory() as session:
        owner = session.get(CrewInstance, crew_id)
        owner.permissions = ["model:invoke", "notifications:show", *caps]
        owner.trigger_type = "interval"
        set_composition(
            session,
            owner,
            composition(
                spec(
                    "planner",
                    capabilities=caps,
                    actions=["calendar.create", "tasks.create"],
                    max_model_calls=4,
                )
            ),
        )
        session.commit()
    for operation in ["calendar.create", "tasks.create"]:
        client.put(
            "/api/v1/action-policies",
            headers=auth,
            json={
                "crew_id": crew_id,
                "operation": operation,
                "rule": {"enabled": True, "mode": "unrestricted"},
            },
        ).raise_for_status()
    # Editing permissions safely pauses work; explicitly start only this isolated fixture
    # after all user-owned policies have been configured.
    with runtime.factory() as session:
        owner = session.get(CrewInstance, crew_id)
        owner.status, owner.is_running = "active", True
        owner.runtime_state, owner.next_run_at = "waiting", None
        session.commit()
    # Same approved mail, deliberately no date/time: never create a placeholder event.
    runtime.google_pool.read.return_value = {"items": [MAIL]}
    runtime.google_pool.message_body.return_value = {
        "id": MAIL["id"],
        "body": "Please collect relatives from the bus stand.",
    }
    client.portal.call(dispatch, runtime, crew_id)
    deadline = time.monotonic() + 10
    found = False
    try:
        while time.monotonic() < deadline:
            with runtime.factory() as session:
                event = session.scalar(
                    select(ConversationEvent).where(
                        ConversationEvent.crew_id == crew_id,
                        ConversationEvent.kind == "question",
                        ConversationEvent.status == "pending",
                    )
                )
                alert = session.scalar(
                    select(ReminderEvent.id).where(ReminderEvent.crew_id == crew_id)
                )
                if event and alert:
                    found = True
                    assert "date and time" in event.payload["summary"]
                    owner = session.get(CrewInstance, crew_id)
                    assert owner.runtime_state == "needs_input"
                    member = next(a for a in owner.agents if a.status == "active")
                    assert MAIL["id"] not in member.worker.observation.get("seen_items", [])
                    assert session.query(ActionRequest).count() == 0
                    assert session.scalar(
                        select(ReminderEvent.id).where(ReminderEvent.crew_id == crew_id)
                    )
                    run_id = event.task_id
                    break
            time.sleep(0.1)
        with runtime.factory() as session:
            runs = [(r.status, r.error, r.steps) for r in session.scalars(select(TaskRun))]
            workers = [
                (r.runtime_state, r.last_runtime_error)
                for r in session.scalars(select(CrewInstance).where(CrewInstance.hidden.is_(True)))
            ]
        assert found, ("No correlated question reached chat", runs, workers)
    finally:
        with runtime.factory() as session:
            run_id = session.scalar(select(TaskRun.id).where(TaskRun.crew_id == crew_id))
        if run_id:
            runtime.task_runner.stop(run_id)


def test_parent_can_deliver_read_only_mail_without_expanding_member_permissions(action_env):
    client, auth, runtime, crew_id = prepare(action_env, notify=False)
    with runtime.factory() as session:
        owner = session.get(CrewInstance, crew_id)
        owner.permissions = [*owner.permissions, "notifications:show"]
        session.commit()
    client.portal.call(dispatch, runtime, crew_id)
    with runtime.factory() as session:
        run_id = session.scalar(select(TaskRun.id).where(TaskRun.crew_id == crew_id))
    assert wait(client, auth, run_id)["status"] == "done"
    with runtime.factory() as session:
        worker = session.get(CrewInstance, crew_id).agents[0].worker
        assert "notifications:show" not in worker.permissions
        assert worker.observation["notified_mail_ids"] == [MAIL["id"]]
        assert session.query(ReminderEvent).count() == 1
