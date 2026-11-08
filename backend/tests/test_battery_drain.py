"""Battery Watch: "remind me whenever it drains fast and is not plugged in". It was drafted
without device metrics (added, then blocked: "Allow device metrics in Edit"), and a Computer
Crew only ever alerted at 20% battery: the fast drain it promised was never measured."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from mavo.resources.device import battery_drain, describe_drain
from mavo.resources.device_config import drain_threshold, monitor_for
from mavo.schemas import CrewDraft

NOW = 1_800_000_000.0


def test_the_drain_is_measured_over_an_unplugged_stretch_only():
    # 64% -> 55% in 30 minutes, unplugged throughout: 18% an hour, about 3 hours left.
    drain = battery_drain([[NOW - 1800, 64, False], [NOW, 55, False]], NOW)
    assert round(drain["per_hour"]) == 18 and drain["minutes"] == 30
    assert describe_drain(drain) == (
        "Battery draining fast: 18% an hour (64% → 55% in 30 min, unplugged), about 3 h left."
    )
    # Plugged in now, plugged in between, too soon, too old, or charging: nothing to say.
    assert battery_drain([[NOW - 1800, 64, False], [NOW, 55, True]], NOW) is None
    assert (
        battery_drain([[NOW - 3600, 80, False], [NOW - 1800, 70, True], [NOW, 69, False]], NOW)
        is None
    )
    assert battery_drain([[NOW - 300, 64, False], [NOW, 60, False]], NOW) is None
    assert battery_drain([[NOW - 9000, 90, False], [NOW, 55, False]], NOW) is None
    assert battery_drain([[NOW - 1800, 50, False], [NOW, 52, False]], NOW) is None
    # No battery at all (a desktop PC).
    assert battery_drain([[NOW - 1800, None, None], [NOW, None, None]], NOW) is None


def test_the_persons_threshold_is_read_from_what_they_asked():
    assert drain_threshold("If the charge dropped 10% or more in the last hour, notify") == 10
    assert drain_threshold("alert me when it loses 25% an hour unplugged") == 25
    assert drain_threshold("tell me whenever the battery drains fast") == 10
    assert drain_threshold("report CPU and memory every check") is None


def test_a_computer_crew_always_reads_the_device_and_never_runs_terminal_commands():
    draft = CrewDraft.model_validate(
        {
            "template_id": "computer",
            "name": "Battery Watch",
            "responsibilities": "Run powercfg in the terminal. If on battery "
            "and the charge dropped 10% or more in the last hour, notify.",
            "trigger_type": "interval",
            "review_interval_minutes": 30,
            "requested_notifications": True,
            "requested_actions": [{"operation": "terminal.run"}],
        }
    )
    assert draft.requested_device_metrics is True
    assert draft.requested_actions == []
    assert draft.device_monitor.battery_drain_per_hour == 10


def test_an_existing_battery_watch_is_read_from_its_instructions():
    crew = SimpleNamespace(
        configuration_plan={"draft": {"device_monitor": {"kind": "system"}}},
        responsibilities="If the machine is on battery and the charge dropped 10% or more in "
        "the last hour, notify the user.",
    )
    assert monitor_for(crew).battery_drain_per_hour == 10


def test_the_battery_watch_alerts_once_when_it_drains_fast(client, auth, monkeypatch):
    """Two real worker checks 30 minutes apart, the device readings faked: the second check
    measures 18% an hour and puts it on the desktop."""
    import mavo.crew_workers as workers
    from mavo.database import CrewInstance, ReminderEvent, utcnow
    from tests.test_tasks import setup

    runtime = setup(client, auth)
    readings = iter(
        [
            {
                "cpu_percent": 10.0,
                "memory_percent": 40.0,
                "storage_percent": 50.0,
                "storage_free_gb": 100.0,
                "storage_total_gb": 200.0,
                "battery_percent": 64.0,
                "plugged_in": False,
            },
            {
                "cpu_percent": 10.0,
                "memory_percent": 40.0,
                "storage_percent": 50.0,
                "storage_free_gb": 100.0,
                "storage_total_gb": 200.0,
                "battery_percent": 55.0,
                "plugged_in": False,
            },
        ]
    )
    monkeypatch.setattr(workers, "read_device_metrics", lambda: next(readings))
    clock = [datetime(2026, 11, 6, 10, 0, tzinfo=UTC)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0] if tz else clock[0].replace(tzinfo=None)

    monkeypatch.setattr(workers, "datetime", Clock)
    with runtime.factory() as session:
        route = runtime.planner.agent_routes(session)[0]
        crew = CrewInstance(
            template_id="computer",
            name="Battery Watch",
            responsibilities="Battery drain watch",
            autonomy_mode="reactive",
            status="active",
            provider=route.provider,
            model=route.model,
            route_id=route.id,
            max_model_calls=4,
            review_interval_minutes=30,
            daily_call_limit=10,
            reminder_text="",
            trigger_type="interval",
            resources=[],
            triggers=[],
            configuration_plan={"device_monitor": {"kind": "system", "battery_drain_per_hour": 10}},
            permissions=["model:invoke", "device:read", "notifications:show"],
            is_running=True,
            runtime_state="waiting",
            next_run_at=utcnow(),
        )
        session.add(crew)
        session.commit()
        crew_id = crew.id

    def check():
        with runtime.factory() as session:
            c = session.get(CrewInstance, crew_id)
            c.runtime_state, c.next_run_at = "waiting", utcnow() - timedelta(seconds=5)
            session.commit()
        asyncio.run(runtime.work(crew_id))
        with runtime.factory() as session:
            return [
                e.message
                for e in session.query(ReminderEvent).filter(ReminderEvent.crew_id == crew_id)
            ]

    assert not any("draining" in m for m in check())  # one reading: nothing to measure yet
    clock[0] += timedelta(minutes=30)
    shown = check()
    assert any(
        "Battery draining fast: 18% an hour (64% → 55% in 30 min, unplugged)" in m for m in shown
    ), shown
