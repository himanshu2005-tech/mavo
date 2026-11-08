"""Lifecycle/fault matrix using disposable Crew, never a user's workspace or accounts."""

import asyncio
import json
import threading
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import select

from mavo.database import CrewInstance, CrewRun, ReminderEvent


def make_monitor(client, auth, *, mode="each_check", notifications=True, device=True):
    route = client.post(
        "/api/v1/routes",
        headers=auth,
        json={
            "name": "Isolated route",
            "provider": "ollama",
            "model": "never-invoked",
        },
    ).json()
    response = client.post(
        "/api/v1/crew",
        headers=auth,
        json={
            "name": "Isolated drive monitor",
            "template_id": "computer",
            "responsibilities": "Read counters only; never modify files",
            "route_id": route["id"],
            "allow_device_metrics": device,
            "allow_notifications": notifications,
            "review_interval_minutes": 5,
            "device_monitor": {
                "kind": "storage",
                "drive": "C:",
                "notifications": mode,
                "min_free_gb": 5,
            },
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def counters(free=4):
    return {
        "drive": "C:\\",
        "storage_free_gb": free,
        "storage_total_gb": 100,
        "storage_percent": 100 - free,
    }


@pytest.mark.parametrize("mode", ["alerts", "changes", "each_check"])
def test_enabling_desktop_delivery_reports_current_status_not_a_silent_old_baseline(
    client, auth, monkeypatch, mode
):
    monkeypatch.setattr("mavo.crew_workers.read_storage_metrics", lambda drive: counters())
    crew = make_monitor(client, auth, mode=mode, notifications=False)
    assert client.post(f"/api/v1/crew/{crew['id']}/start", headers=auth).status_code == 200
    runtime = client.app.state.crew_runtime
    client.portal.call(runtime.work, crew["id"])
    assert not client.get("/api/v1/reminders/due", headers=auth).json()
    assert (
        client.patch(
            f"/api/v1/crew/{crew['id']}", headers=auth, json={"allow_notifications": True}
        ).status_code
        == 200
    )
    client.portal.call(runtime.work, crew["id"])
    due = client.get("/api/v1/reminders/due", headers=auth).json()
    assert len(due) == 1 and "4.00 GB free" in due[0]["message"]


@pytest.mark.parametrize("interruption", ["stop", "delete", "revoke", "pause"])
def test_interruption_during_device_read_fences_late_results(
    client, auth, monkeypatch, interruption
):
    entered, release = threading.Event(), threading.Event()

    def read(drive):
        entered.set()
        if not release.wait(10):
            raise TimeoutError("test did not release the counter read")
        return counters()

    monkeypatch.setattr("mavo.crew_workers.read_storage_metrics", read)
    crew = make_monitor(client, auth)
    crew_id = crew["id"]
    client.post(f"/api/v1/crew/{crew_id}/start", headers=auth)
    runtime = client.app.state.crew_runtime

    async def begin():
        task = asyncio.create_task(runtime.work(crew_id))
        await asyncio.wait_for(asyncio.to_thread(entered.wait), 5)
        return task

    task = client.portal.call(begin)
    try:
        if interruption == "delete":
            response = client.delete(f"/api/v1/crew/{crew_id}", headers=auth)
        elif interruption == "revoke":
            response = client.patch(
                f"/api/v1/crew/{crew_id}", headers=auth, json={"allow_device_metrics": False}
            )
        elif interruption == "pause":
            response = client.post("/api/v1/actions/pause", headers=auth)
        else:
            response = client.post(f"/api/v1/crew/{crew_id}/stop", headers=auth)
        assert response.status_code in {200, 204}
    finally:
        release.set()

        async def finish():
            await asyncio.wait_for(task, 5)

        client.portal.call(finish)
    with runtime.factory() as session:
        assert not session.scalars(select(ReminderEvent)).all()
        saved = session.get(CrewInstance, crew_id)
        assert saved is None or saved.checkpoint is None


@pytest.mark.parametrize("mode", ["alerts", "changes", "each_check"])
@pytest.mark.parametrize("notifications", [False, True])
def test_repeated_counter_matrix_is_bounded_and_uses_no_model(
    client, auth, monkeypatch, mode, notifications
):
    read = Mock(return_value=counters())
    monkeypatch.setattr("mavo.crew_workers.read_storage_metrics", read)
    crew = make_monitor(client, auth, mode=mode, notifications=notifications)
    client.post(f"/api/v1/crew/{crew['id']}/start", headers=auth)
    runtime = client.app.state.crew_runtime
    for _ in range(4):
        client.portal.call(runtime.work, crew["id"])
    due = client.get("/api/v1/reminders/due", headers=auth).json()
    assert len(due) == int(notifications)
    with runtime.factory() as session:
        assert len(session.scalars(select(CrewRun)).all()) == 4
        assert all(run.model_calls == 0 for run in session.scalars(select(CrewRun)))
        saved = session.get(CrewInstance, crew["id"])
        assert saved.daily_calls_used == 0 and saved.is_running
    assert read.call_count == 4


def test_revocation_removes_pending_delivery_and_regrant_does_not_resurrect_it(
    client, auth, monkeypatch
):
    monkeypatch.setattr("mavo.crew_workers.read_storage_metrics", lambda drive: counters())
    crew = make_monitor(client, auth)
    client.post(f"/api/v1/crew/{crew['id']}/start", headers=auth)
    runtime = client.app.state.crew_runtime
    client.portal.call(runtime.work, crew["id"])
    old = client.get("/api/v1/reminders/due", headers=auth).json()[0]["id"]
    client.patch(f"/api/v1/crew/{crew['id']}", headers=auth, json={"allow_notifications": False})
    assert not client.get("/api/v1/reminders/due", headers=auth).json()
    client.patch(f"/api/v1/crew/{crew['id']}", headers=auth, json={"allow_notifications": True})
    assert not client.get("/api/v1/reminders/due", headers=auth).json()
    client.portal.call(runtime.work, crew["id"])
    due = client.get("/api/v1/reminders/due", headers=auth).json()
    assert len(due) == 1 and due[0]["id"] != old


@pytest.mark.parametrize("mode", ["alerts", "changes", "each_check"])
def test_revoke_and_regrant_before_delivery_poll_cannot_resurrect_old_notice(
    client, auth, monkeypatch, mode
):
    monkeypatch.setattr("mavo.crew_workers.read_storage_metrics", lambda drive: counters())
    crew = make_monitor(client, auth, mode=mode)
    client.post(f"/api/v1/crew/{crew['id']}/start", headers=auth)
    runtime = client.app.state.crew_runtime
    client.portal.call(runtime.work, crew["id"])
    old = client.get("/api/v1/reminders/due", headers=auth).json()[0]["id"]
    for grant in (False, True):
        assert (
            client.patch(
                f"/api/v1/crew/{crew['id']}", headers=auth, json={"allow_notifications": grant}
            ).status_code
            == 200
        )
    assert client.get("/api/v1/reminders/due", headers=auth).json() == []
    client.portal.call(runtime.work, crew["id"])
    due = client.get("/api/v1/reminders/due", headers=auth).json()
    assert len(due) == 1 and due[0]["id"] != old


def test_new_runtime_retains_alert_baseline_without_repeating_notification(
    client, auth, monkeypatch
):
    from mavo.crew_workers import CrewWorkerRuntime

    monkeypatch.setattr("mavo.crew_workers.read_storage_metrics", lambda drive: counters())
    crew = make_monitor(client, auth, mode="alerts")
    client.post(f"/api/v1/crew/{crew['id']}/start", headers=auth)
    runtime = client.app.state.crew_runtime
    client.portal.call(runtime.work, crew["id"])
    restarted = CrewWorkerRuntime(runtime.factory, runtime.router)
    client.portal.call(restarted.work, crew["id"])
    with runtime.factory() as session:
        assert len(session.scalars(select(ReminderEvent)).all()) == 1
        assert session.get(CrewInstance, crew["id"]).daily_calls_used == 0


@pytest.mark.parametrize("fault", ["network", "timeout", "invalid_json"])
def test_model_faults_are_bounded_and_never_notified_as_success(client, auth, fault):
    from mavo.model_router import ModelProviderError

    route = client.post(
        "/api/v1/routes",
        headers=auth,
        json={"name": "Fault route", "provider": "ollama", "model": "fixture"},
    ).json()
    crew = client.post(
        "/api/v1/crew",
        headers=auth,
        json={
            "name": "Fault test",
            "template_id": "research",
            "route_id": route["id"],
            "responsibilities": "Research only the supplied facts",
            "daily_call_limit": 5,
            "allow_notifications": True,
        },
    ).json()
    client.post(f"/api/v1/crew/{crew['id']}/start", headers=auth)
    runtime = client.app.state.crew_runtime
    if fault == "invalid_json":
        runtime.router.chat = AsyncMock(
            return_value='{"summary":"UNVERIFIED","notification":"FAKE SUCCESS"}'
        )
        attempts = 1
        spent = 2  # the reply plus the single repair attempt that says what was wrong
    else:
        runtime.router.chat = AsyncMock(
            side_effect=ModelProviderError("Fixture provider unavailable")
            if fault == "network"
            else TimeoutError()
        )
        attempts = spent = 3
    for _ in range(attempts):
        client.portal.call(runtime.work, crew["id"])
    with runtime.factory() as session:
        saved = session.get(CrewInstance, crew["id"])
        assert saved.runtime_state == "error" and saved.next_run_at is None
        assert saved.daily_calls_used == spent
        assert not session.scalars(select(ReminderEvent)).all()
    client.portal.call(runtime.tick)
    assert runtime.router.chat.await_count == spent


def test_scheduler_limits_concurrency_and_gives_third_crew_a_turn(client, auth):
    route = client.post(
        "/api/v1/routes",
        headers=auth,
        json={"name": "Scheduler", "provider": "ollama", "model": "fixture"},
    ).json()
    for i in range(3):
        crew = client.post(
            "/api/v1/crew",
            headers=auth,
            json={
                "name": f"Bounded test {i}",
                "template_id": "project",
                "route_id": route["id"],
                "responsibilities": "One bounded test step",
            },
        ).json()
        client.post(f"/api/v1/crew/{crew['id']}/start", headers=auth)
    runtime = client.app.state.crew_runtime

    async def scenario():
        release = asyncio.Event()
        entered = asyncio.Event()
        active = maximum = calls = 0

        async def chat(*args, **kwargs):
            nonlocal active, maximum, calls
            active += 1
            calls += 1
            maximum = max(maximum, active)
            if active == 2:
                entered.set()
            await release.wait()
            active -= 1
            return json.dumps(
                {
                    "state": "wait_for_next_review",
                    "summary": "Fixture result",
                    "next_step": "Next review",
                }
            )

        runtime.router.chat = chat
        await runtime.tick()
        try:
            await asyncio.wait_for(entered.wait(), 5)
            assert len(runtime.tasks) == 2
        finally:
            release.set()
            await asyncio.gather(*(task for _, task in runtime.tasks.values()))
        await runtime.tick()
        await asyncio.gather(*(task for _, task in runtime.tasks.values()))
        assert maximum == 2 and calls == 3

    client.portal.call(scenario)
