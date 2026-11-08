"""Sleep and shut down that say what really happened, and a time-of-day Crew that waits for its
time. Windows itself is replaced here: nothing sleeps or shuts down during the tests.

"Sleep Before Lunch" reported "Sleep action executed successfully" and the laptop (Modern Standby
only) did not sleep; "Daily 10:55 Shutdown" ran when added at 10:43, then ran out of budget."""

from types import SimpleNamespace

import pytest

import mavo.desktop_actions as desk
from mavo.schemas import CrewDraft


class FakeDll:
    def __init__(self, suspend_ok):
        self.posted = []
        self.SetSuspendState = SimpleNamespace(argtypes=None, restype=None)
        self.SetSuspendState = _Callable(lambda *a: 1 if suspend_ok else 0)
        self.PostMessageW = _Callable(lambda *a: self.posted.append(a) or 1)


class _Callable:
    def __init__(self, fn):
        self.fn, self.argtypes, self.restype = fn, None, None

    def __call__(self, *args):
        return self.fn(*args)


def fake_windows(monkeypatch, suspend_ok, error=50):
    dll = FakeDll(suspend_ok)
    monkeypatch.setattr(desk.ctypes, "WinDLL", lambda name, **kw: dll)
    monkeypatch.setattr(desk.ctypes, "get_last_error", lambda: 0 if suspend_ok else error)
    monkeypatch.setattr(desk.ctypes, "set_last_error", lambda value: None)
    monkeypatch.setattr(desk, "enable_shutdown_privilege", lambda: True)
    return dll


def test_sleep_says_it_slept_only_when_windows_did(monkeypatch):
    fake_windows(monkeypatch, suspend_ok=True)
    assert desk.sleep_computer() == {"slept": True, "woke": True}


def test_refused_sleep_is_said_plainly_and_the_screen_goes_off(monkeypatch):
    dll = fake_windows(monkeypatch, suspend_ok=False, error=50)
    with pytest.raises(
        ValueError,
        match=r"did not put the computer to sleep \(error 50\)\. The screen was turned off instead",
    ):
        desk.sleep_computer()
    assert dll.posted and dll.posted[0][1:] == (0x0112, 0xF170, 2)  # the screen off


def test_shutdown_warns_a_minute_ahead_and_can_be_cancelled(monkeypatch):
    ran = []
    monkeypatch.setattr("subprocess.run", lambda args, **kw: ran.append(args))
    out = desk.DesktopActions.windows("windows.shutdown", {}, {"mode": "unrestricted", "apps": []})
    assert ran and ran[0][:4] == ["shutdown", "/s", "/t", "60"]
    assert out == {"shutting_down_in_seconds": 60, "cancel_with": "shutdown /a"}


def test_a_crew_that_acts_has_room_for_its_follow_up():
    draft = CrewDraft.model_validate(
        {
            "template_id": "operator",
            "name": "Daily 10:55 Shutdown",
            "responsibilities": "Shut down the computer at 10:55.",
            "trigger_type": "interval",
            "review_interval_minutes": 1440,
            "schedule_time": "10:55",
            "max_model_calls": 3,
            "daily_call_limit": 2,
            "requested_actions": [{"operation": "windows.shutdown"}],
        }
    )
    assert draft.daily_call_limit >= draft.max_model_calls + 1


def test_a_time_of_day_crew_first_works_at_its_time_not_when_added(client, auth):
    from datetime import UTC, datetime

    from mavo.database import CrewInstance
    from tests.test_tasks import setup

    runtime = setup(client, auth)
    routes = client.get("/api/v1/routes", headers=auth).json()
    made = client.post(
        "/api/v1/crew",
        headers=auth,
        json={
            "template_id": "operator",
            "name": "Daily Shutdown",
            "route_id": routes[0]["id"],
            "trigger_type": "interval",
            "review_interval_minutes": 1440,
            "schedule_time": "10:55",
            "responsibilities": "Shut down at 10:55.",
        },
    ).json()
    client.post(f"/api/v1/crew/{made['id']}/start", headers=auth).raise_for_status()
    with runtime.factory() as session:
        crew = session.get(CrewInstance, made["id"])
        at = (
            crew.next_run_at.replace(tzinfo=UTC)
            if crew.next_run_at.tzinfo is None
            else crew.next_run_at
        )
        local = at.astimezone()
        assert (local.hour, local.minute) == (10, 55)
        assert at > datetime.now(UTC)  # not "now": at its time
