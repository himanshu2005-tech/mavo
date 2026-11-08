from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from mavo.crew_engine import crew_system_prompt, recover_interrupted_runs
from mavo.database import CrewInstance, CrewRun, ModelRoute, build_session_factory
from mavo.migrations import run_migrations


def test_only_one_run_is_claimed_and_restart_records_interruption(tmp_path):
    url = f"sqlite:///{(tmp_path / 'runs.db').as_posix()}"
    run_migrations(url)
    factory = build_session_factory(url)
    with factory() as session:
        route = ModelRoute(id="r", name="Route", provider="ollama", model="m")
        crew = CrewInstance(
            id="c",
            name="Crew",
            template_id="coding",
            responsibilities="Code",
            provider="ollama",
            model="m",
            route_id="r",
        )
        session.add(route)
        session.commit()
        session.add(crew)
        session.commit()
        session.add(
            CrewRun(
                id="one",
                crew_id="c",
                crew_name="Crew",
                route_name="Route",
                provider="ollama",
                model="m",
                prompt="Task",
            )
        )
        session.commit()
    with factory() as other:
        other.add(
            CrewRun(
                id="two",
                crew_id="c",
                crew_name="Crew",
                route_name="Route",
                provider="ollama",
                model="m",
                prompt="Duplicate",
            )
        )
        with pytest.raises(IntegrityError):
            other.commit()
        other.rollback()
    with factory() as restarted:
        recover_interrupted_runs(restarted)
        previous = restarted.get(CrewRun, "one")
        assert previous.status == "interrupted"
        assert previous.finished_at is not None
        assert previous.result is None
        restarted.add(
            CrewRun(
                id="retry",
                crew_id="c",
                crew_name="Crew",
                route_name="Route",
                provider="ollama",
                model="m",
                prompt="Retry",
            )
        )
        restarted.commit()
    factory.kw["bind"].dispose()


def test_crew_prompt_has_explicit_scope_without_other_crew_data():
    crew = CrewInstance(name="My studies", template_id="learning", responsibilities="Algebra")
    prompt = crew_system_prompt(crew)
    assert "Algebra" in prompt
    assert "one model call" in prompt
    assert "no direct account" in prompt
    assert "explicitly authorized resource snapshots" in prompt
    assert "untrusted data" in prompt


def test_reminders_reject_old_pending_events_and_use_given_local_time(tmp_path):
    from datetime import timedelta, timezone

    from mavo.database import ReminderEvent
    from mavo.reminders import create_due_reminders

    url = f"sqlite:///{(tmp_path / 'reminders.db').as_posix()}"
    run_migrations(url)
    factory = build_session_factory(url)
    with factory() as session:
        session.add(
            CrewInstance(
                id="c",
                name="Watch",
                template_id="schedule",
                status="active",
                responsibilities="Wear watch",
                provider="ollama",
                model="m",
                schedule_time="22:00",
                is_running=True,
                trigger_type="daily",
            )
        )
        session.commit()
        session.add(
            ReminderEvent(
                crew_id="c",
                crew_name="Watch",
                message="Yesterday",
                scheduled_for=datetime(2026, 10, 31, 16, 30, tzinfo=UTC),
                local_date="2026-10-31",
                deduplication_key="c:2026-10-31",
            )
        )
        session.commit()
        zone = timezone(timedelta(hours=5, minutes=30))
        assert create_due_reminders(session, datetime(2026, 11, 1, 21, 59, tzinfo=zone)) == []
        due = create_due_reminders(session, datetime(2026, 11, 1, 22, 0, tzinfo=zone))
        assert len(due) == 1 and due[0].message == "Wear watch"
        assert len(create_due_reminders(session, datetime(2026, 11, 1, 22, 1, tzinfo=zone))) == 1
    factory.kw["bind"].dispose()
