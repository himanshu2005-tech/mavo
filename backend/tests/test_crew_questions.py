import json
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from mavo.crew_workers import CrewWorkerRuntime
from mavo.database import CrewInstance, ReminderEvent
from mavo.reminders import create_due_reminders
from tests.test_crew_workers import add_crew
from tests.test_crew_workers import factory as worker_factory

factory = worker_factory


@pytest.mark.asyncio
@pytest.mark.parametrize("allow", [True, False])
async def test_important_mail_updates_require_notification_access_and_deduplicate(factory, allow):
    permissions = ["model:invoke", "google:gmail:read"] + (["notifications:show"] if allow else [])
    add_crew(
        factory,
        template="communication",
        trigger_type="gmail_message",
        resources=["google:gmail"],
        permissions=permissions,
    )
    router = type("Router", (), {})()
    router.chat = AsyncMock(
        return_value=json.dumps(
            {
                "state": "waiting_for_input",
                "summary": "Found an urgent college deadline.",
                "next_step": "Watch for new messages",
                "notification": "College submission due tomorrow.",
            }
        )
    )
    runtime = CrewWorkerRuntime(factory, router)
    runtime.prepare_event = AsyncMock(
        return_value={
            "resource": "gmail",
            "subject": "user-1",
            "generation": 1,
            "cursor": 7,
            "events": [{"id": "mail-7", "subject": "Urgent deadline", "labels": ["INBOX"]}],
        }
    )
    await runtime.work("crew")
    with factory() as session:
        notices = list(session.scalars(select(ReminderEvent)))
        assert len(notices) == (1 if allow else 0)
        if allow:
            assert notices[0].kind == "crew_update"
            assert create_due_reminders(session)[0].id == notices[0].id
    await runtime.work("crew")
    with factory() as session:
        assert len(list(session.scalars(select(ReminderEvent)))) == (1 if allow else 0)
        if allow:
            crew = session.get(CrewInstance, "crew")
            crew.permissions = [p for p in crew.permissions if p != "notifications:show"]
            session.commit()
            assert create_due_reminders(session) == []
            assert session.scalar(select(ReminderEvent)).status == "cancelled"


@pytest.mark.asyncio
async def test_promotional_message_without_notification_stays_quiet(factory):
    add_crew(
        factory,
        template="communication",
        trigger_type="gmail_message",
        resources=["google:gmail"],
        permissions=["model:invoke", "google:gmail:read", "notifications:show"],
    )
    router = type("Router", (), {})()
    router.chat = AsyncMock(
        return_value=json.dumps(
            {
                "state": "waiting_for_input",
                "summary": "Promotion filtered out.",
                "next_step": "Wait for matching mail",
                "notification": None,
            }
        )
    )
    runtime = CrewWorkerRuntime(factory, router)
    runtime.prepare_event = AsyncMock(
        return_value={
            "resource": "gmail",
            "subject": "user-1",
            "generation": 1,
            "cursor": 9,
            "events": [
                {"id": "mail-9", "subject": "Sale", "labels": ["INBOX", "CATEGORY_PROMOTIONS"]}
            ],
        }
    )
    await runtime.work("crew")
    with factory() as session:
        assert list(session.scalars(select(ReminderEvent))) == []
        assert session.get(CrewInstance, "crew").runtime_state == "waiting_event"


@pytest.mark.asyncio
async def test_agent_question_pauses_until_user_reply(factory):
    add_crew(
        factory,
        template="communication",
        trigger_type="gmail_message",
        resources=["google:gmail"],
        permissions=["model:invoke", "google:gmail:read"],
    )
    router = type("Router", (), {})()
    router.chat = AsyncMock(
        return_value=json.dumps(
            {
                "state": "waiting_for_input",
                "summary": "I need one detail.",
                "next_step": "Ask user",
                "question": "Which sender should count as important?",
            }
        )
    )
    runtime = CrewWorkerRuntime(factory, router)
    runtime.prepare_event = AsyncMock(
        return_value={
            "resource": "gmail",
            "subject": "user-1",
            "generation": 1,
            "cursor": 11,
            "events": [{"id": "mail-11"}],
        }
    )
    await runtime.work("crew")
    with factory() as session:
        crew = session.get(CrewInstance, "crew")
        assert crew.question == "Which sender should count as important?"
        assert crew.runtime_state == "needs_input" and crew.next_run_at is None
    await runtime.tick()
    assert router.chat.await_count == 1
