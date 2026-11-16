"""Deterministic real-runtime mail handoff tests. No real mail, inference or writes."""

import json
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from mavo.crew_composition import dispatch, set_composition, worker_for
from mavo.crew_workers import parse_checkpoint
from mavo.database import CrewInstance, GoogleAccount, ReminderEvent, ResourceEvent, TaskRun, utcnow
from mavo.mail_reviews import MailReviewError, new_mail_batch, validate_review
from tests.test_actions import action_env  # noqa: F401
from tests.test_crew_v2 import composition, seed, spec
from tests.test_tasks import wait

MAIL = {
    "id": "pickup-request",
    "subject": "Pick up relatives from the bus stand",
    "sender": "family@example.com",
    "date": "Tue, 10 Nov 2026 19:55:45 +0530",
    "labels": ["INBOX", "IMPORTANT", "CATEGORY_PERSONAL"],
}


def answer(*, important=True, ids=("pickup-request",)):
    return json.dumps(
        {
            "state": "wait_for_next_review",
            "summary": "Nothing new.",  # Typed important decisions take precedence over old prose.
            "mail_decisions": [
                {
                    "message_id": mid,
                    "important": important,
                    "reason": "Direct personal request" if important else "Routine newsletter",
                    "summary": "Collect relatives tomorrow at 1 PM from the bus stand.",
                }
                for mid in ids
            ],
            # Deliberately no optional notification; app owns delivery from validated decisions.
        }
    )


def prepare(env, responses=None, *, notify=True):
    client, auth, _, _ = env
    runtime = client.app.state.crew_runtime
    runtime.google_pool.read = AsyncMock(
        return_value={
            "items": [
                {"id": "old-promotion", "sender": "shop@example.com", "subject": "Old promotion"}
            ]
        }
    )
    runtime.google_pool.message_body = AsyncMock(
        return_value={
            "id": MAIL["id"],
            "body": "Please collect relatives tomorrow 1PM from the bus stand.",
        }
    )
    runtime.router.chat = AsyncMock(side_effect=responses or [answer()])
    caps = ["google:gmail:read", "google:gmail:body", *(["notifications:show"] if notify else [])]
    with runtime.factory() as session:
        crew = seed(session)
        crew.template_id = "communication"
        crew.responsibilities = "Read new personal mail and alert me to direct requests."
        crew.resources = ["google:gmail"]
        crew.permissions = ["model:invoke", *caps]
        crew.trigger_type = "gmail_message"
        set_composition(
            session, crew, composition(spec("triage", capabilities=caps, max_model_calls=4))
        )
        session.add(
            ResourceEvent(
                account_subject="user-1",
                resource="gmail",
                deduplication_key="pickup-event",
                payload=MAIL,
            )
        )
        crew_id = crew.id
        session.commit()
    return client, auth, runtime, crew_id


def run_event(env, responses=None, *, notify=True):
    client, auth, runtime, crew_id = prepare(env, responses, notify=notify)
    client.portal.call(dispatch, runtime, crew_id)
    with runtime.factory() as session:
        run_id = session.scalar(select(TaskRun.id).where(TaskRun.crew_id == crew_id))
    result = wait(client, auth, run_id)
    return runtime, crew_id, result


def test_exact_event_not_general_inbox_is_read_classified_and_alerted_once(action_env):
    runtime, crew_id, result = run_event(action_env)
    assert result["status"] == "done", result
    assert "Collect relatives tomorrow at 1 PM" in result["result"]
    assert runtime.google_pool.message_body.await_args.args == (MAIL["id"],)
    prompt = runtime.router.chat.await_args.args[2][0]["content"]
    snapshots = json.loads(
        prompt.split("Authorized UNTRUSTED Google snapshots:\n")[1].split("\n")[0]
    )
    assert [m["id"] for m in snapshots["gmail"]] == [MAIL["id"]]
    with runtime.factory() as session:
        owner = session.get(CrewInstance, crew_id)
        worker = owner.agents[0].worker
        assert owner.event_cursor > 0 and "pending_google" not in owner.observation
        from mavo.crew_workers import as_utc

        assert (as_utc(owner.next_run_at) - utcnow()).total_seconds() <= 61
        assert worker.observation["mail_review"]["decisions"][0]["important"] is True
        assert MAIL["id"] in worker.observation["seen_items"]
        events = session.scalars(
            select(ReminderEvent).where(ReminderEvent.crew_id == crew_id)
        ).all()
        assert len(events) == 1 and "Collect relatives" in events[0].message
        owner.next_run_at = None
        session.commit()
    # Simulate re-entry/recovery of the same durable cursor: no new work or popup.
    action_env[0].portal.call(dispatch, runtime, crew_id)
    assert runtime.router.chat.await_count == 1


@pytest.mark.parametrize("notify,important", [(True, False), (False, True)])
def test_unimportant_mail_and_missing_desktop_grant_do_not_alert(action_env, notify, important):
    runtime, crew_id, result = run_event(action_env, [answer(important=important)], notify=notify)
    assert result["status"] == "done", result
    if not important:
        assert "Model assessments:" in result["result"]
        assert MAIL["subject"] in result["result"]
    with runtime.factory() as session:
        assert session.get(CrewInstance, crew_id).event_cursor > 0
        assert not session.scalar(select(ReminderEvent.id).where(ReminderEvent.crew_id == crew_id))


def test_unclassified_event_gets_one_repair_instead_of_silent_acknowledgement(action_env):
    invalid = json.dumps({"state": "wait_for_next_review", "summary": "Nothing new."})
    runtime, crew_id, result = run_event(action_env, [invalid, answer()])
    assert result["status"] == "done", result
    assert runtime.router.chat.await_count == 2
    assert "mail_decisions" in runtime.router.chat.await_args.args[2][-1]["content"]


def test_failed_repair_preserves_pending_event_for_recovery(action_env):
    invalid = json.dumps({"state": "wait_for_next_review", "summary": "Nothing new."})
    runtime, crew_id, result = run_event(action_env, [invalid, invalid])
    assert result["status"] != "done", result
    assert runtime.router.chat.await_count == 2
    with runtime.factory() as session:
        owner = session.get(CrewInstance, crew_id)
        assert owner.event_cursor == 0 and owner.observation["pending_google"]["events"] == [MAIL]
        assert MAIL["id"] not in owner.agents[0].worker.observation.get("seen_items", [])


@pytest.mark.parametrize("change", ["account", "revoke"])
def test_account_change_or_revocation_during_inference_cannot_publish(action_env, change):
    client, auth, runtime, crew_id = prepare(action_env)

    async def changed(*args, **kwargs):
        with runtime.factory() as session:
            if change == "account":
                session.get(GoogleAccount, "google").subject = "somebody-else"
            else:
                session.get(CrewInstance, crew_id).permissions = ["model:invoke"]
            session.commit()
        return answer()

    runtime.router.chat = changed
    client.portal.call(dispatch, runtime, crew_id)
    with runtime.factory() as session:
        run_id = session.scalar(select(TaskRun.id).where(TaskRun.crew_id == crew_id))
    result = wait(client, auth, run_id)
    assert result["status"] != "done", result
    with runtime.factory() as session:
        assert session.get(CrewInstance, crew_id).event_cursor == 0
        alerts = session.scalars(select(ReminderEvent).where(ReminderEvent.crew_id == crew_id))
        assert all("Collect relatives" not in event.message for event in alerts)


@pytest.mark.parametrize("ids", [(), ("unknown",)])
def test_missing_or_only_unknown_message_decisions_are_rejected(ids):
    with pytest.raises(MailReviewError):
        validate_review(parse_checkpoint(answer(ids=ids)), [MAIL])


def test_a_repeated_decision_is_dropped_not_a_failed_review():
    checkpoint = parse_checkpoint(answer(ids=("pickup-request", "pickup-request")))
    validate_review(checkpoint, [MAIL])
    assert [d.message_id for d in checkpoint.mail_decisions] == ["pickup-request"]


def test_dedupe_precedes_batching_and_does_not_acknowledge_omitted_items():
    items = [{"id": str(i)} for i in range(20)]
    observed = {"seen_items": [str(i) for i in range(10)]}
    assert [i["id"] for i in new_mail_batch(observed, items, recurring=True)] == [
        str(i) for i in range(10, 20)
    ]
    assert observed["seen_items"] == [str(i) for i in range(10)]


def test_scheduled_inbox_review_reaches_unseen_mail_beyond_old_first_ten(action_env):
    client, auth, runtime, crew_id = prepare(action_env)
    old = [
        {"id": f"old-{i}", "sender": "shop@example.com", "subject": "Old promotion"}
        for i in range(10)
    ]
    runtime.google_pool.read = AsyncMock(return_value={"items": [*old, MAIL]})
    with runtime.factory() as session:
        parent = session.get(CrewInstance, crew_id)
        parent.trigger_type = "interval"
        worker = worker_for(session, parent, parent.agents[0])
        worker.observation = {"seen_items": [m["id"] for m in old]}
        session.commit()
    client.portal.call(dispatch, runtime, crew_id)
    with runtime.factory() as session:
        run_id = session.scalar(select(TaskRun.id).where(TaskRun.crew_id == crew_id))
    assert wait(client, auth, run_id)["status"] == "done"
    assert runtime.google_pool.message_body.await_args.args == (MAIL["id"],)
    with runtime.factory() as session:
        parent = session.get(CrewInstance, crew_id)
        assert MAIL["id"] in parent.agents[0].worker.observation["seen_items"]
        assert (
            parent.event_cursor == 0
        )  # An interval review never consumes another trigger's cursor.
        from mavo.crew_workers import as_utc

        assert (as_utc(parent.next_run_at) - utcnow()).total_seconds() > 29 * 60
        assert session.query(ReminderEvent).count() == 1


def test_mail_batch_deduplicates_provider_rows_and_report_stays_bounded():
    from mavo.mail_reviews import important_report

    items = [{**MAIL, "id": str(i), "subject": "s" * 1000, "sender": "f" * 500} for i in range(10)]
    assert len(new_mail_batch({}, [*items, *items], recurring=False)) == 10
    checkpoint = parse_checkpoint(answer(ids=tuple(str(i) for i in range(10))))
    for decision in checkpoint.mail_decisions:
        decision.summary = "x" * 1000
    report = important_report(checkpoint, items)
    assert len(report) < 12000 and len(report.split("\n\n")) == 10  # one short block per mail
