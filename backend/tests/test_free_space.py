""" "Create an agent which clears my temp folder and clears my drive space every 3 hrs." The Crew
was told it would clear %TEMP%, C:\\Windows\\Temp and the Recycle Bin, and it only ever read the
drive ("Read-only check; no files changed"). Mavo now frees the space itself, behind one grant.

These tests never touch the real temp folder or Recycle Bin: the cleanup runs on a throwaway
folder, and in the worker both are replaced."""

import asyncio
import os
import subprocess
import time
from datetime import timedelta

import pytest

from mavo.resources.device import clean_temp, describe_cleanup
from mavo.resources.device_config import clean_threshold, monitor_for, wants_recycle
from mavo.schemas import CrewDraft

ASKED = (
    "Every 3 hours: (1) read disk counters for drive C: and report free "
    "space; (2) clear the Windows "
    "temp folder (%TEMP% and C:\\Windows\\Temp) of files older than 1 day using the terminal, and "
    "empty the Recycle Bin; (3) report how much space was freed."
)


def old(path, days=3):
    stamp = time.time() - days * 86400
    os.utime(path, (stamp, stamp))


def test_only_old_temp_files_go_and_nothing_behind_a_link(tmp_path):
    temp = tmp_path / "Temp"
    (temp / "sub").mkdir(parents=True)
    (temp / "stale.tmp").write_bytes(b"x" * 2048)
    old(temp / "stale.tmp")
    (temp / "sub" / "stale2.log").write_bytes(b"y" * 1024)
    old(temp / "sub" / "stale2.log")
    (temp / "fresh.tmp").write_bytes(b"z")  # changed just now: kept
    outside = tmp_path / "Documents"
    outside.mkdir()
    (outside / "thesis.docx").write_bytes(b"precious")
    old(outside / "thesis.docx", 30)
    linked = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(temp / "jump"), str(outside)], capture_output=True
    )
    done = clean_temp(1, root=temp)
    assert (
        done["deleted"] == 2 and not (temp / "stale.tmp").exists() and (temp / "fresh.tmp").exists()
    )
    assert temp.exists()  # the folder itself stays
    assert (outside / "thesis.docx").exists()  # never through the junction
    if linked.returncode == 0:
        assert (temp / "jump").exists()
    assert describe_cleanup(done, 0.5).startswith(
        "Cleared 0.00 GB of temp files (2 older than a day)."
    )
    assert "Emptied the Recycle Bin (0.50 GB)." in describe_cleanup(done, 0.5)
    with pytest.raises(OSError):
        clean_temp(1, root=tmp_path / "missing")


def test_what_was_asked_is_read_and_the_drafted_crew_can_do_it():
    assert clean_threshold(ASKED) == 1 and wants_recycle(ASKED)
    assert clean_threshold("clear my temp folder every 3 hours") == 1
    assert not wants_recycle("clear my temp folder every 3 hours")
    assert wants_recycle("clear my temp folder and clear my drive space")
    assert clean_threshold("report free space on C:") is None
    draft = CrewDraft.model_validate(
        {
            "template_id": "computer",
            "name": "Temp & Drive Cleaner",
            "responsibilities": ASKED,
            "trigger_type": "interval",
            "review_interval_minutes": 180,
            "requested_notifications": True,
            "device_monitor": {"kind": "storage", "drive": "C:"},
        }
    )
    assert draft.device_monitor.clean_temp_days == 1 and draft.device_monitor.empty_recycle_bin


def test_the_existing_cleaner_is_read_from_its_instructions():
    from types import SimpleNamespace

    crew = SimpleNamespace(
        configuration_plan={"draft": {"device_monitor": {"kind": "storage", "drive": "C:"}}},
        responsibilities=ASKED,
    )
    monitor = monitor_for(crew)
    assert monitor.clean_temp_days == 1 and monitor.empty_recycle_bin and monitor.kind == "storage"


def test_the_cleaner_frees_space_only_once_allowed(client, auth, monkeypatch):
    import mavo.crew_workers as workers
    from mavo.database import CrewInstance, ReminderEvent, utcnow
    from tests.test_tasks import setup

    runtime = setup(client, auth)
    calls = []
    monkeypatch.setattr(
        workers,
        "clean_temp",
        lambda days: (
            calls.append(("temp", days))
            or {"freed_gb": 1.25, "deleted": 840, "skipped": 12, "days": days}
        ),
    )
    monkeypatch.setattr(workers, "empty_recycle_bin", lambda: calls.append(("bin",)) or 0.5)
    monkeypatch.setattr(
        workers,
        "read_storage_metrics",
        lambda drive: {
            "drive": "C:\\",
            "storage_percent": 85.0,
            "storage_free_gb": 35.36,
            "storage_total_gb": 230.9,
        },
    )
    with runtime.factory() as session:
        route = runtime.planner.agent_routes(session)[0]
        crew = CrewInstance(
            template_id="computer",
            name="Temp & Drive Cleaner",
            responsibilities=ASKED,
            autonomy_mode="reactive",
            status="active",
            provider=route.provider,
            model=route.model,
            route_id=route.id,
            max_model_calls=4,
            review_interval_minutes=180,
            daily_call_limit=24,
            reminder_text="",
            trigger_type="interval",
            resources=[],
            triggers=[],
            configuration_plan={
                "device_monitor": {"kind": "storage", "drive": "C:", "notifications": "each_check"}
            },
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
            c = session.get(CrewInstance, crew_id)
            shown = [
                e.message
                for e in session.query(ReminderEvent).filter(ReminderEvent.crew_id == crew_id)
            ]
            return c.last_runtime_error, shown

    # Not allowed yet: nothing is deleted, and it says what to allow (the Crew page has the switch).
    error, _ = check()
    assert calls == [] and error == "Allow cleaning temp files to free space on this computer."
    # Allowed: temp files, then the Recycle Bin, and it says what it freed.
    client.patch(
        f"/api/v1/crew/{crew_id}", headers=auth, json={"allow_temp_cleanup": True}
    ).raise_for_status()
    error, shown = check()
    assert calls == [("temp", 1), ("bin",)] and error is None
    assert any(
        m.startswith(
            "Cleared 1.25 GB of temp files (840 older than a day), leaving "
            "12 in use. Emptied the Recycle Bin (0.50 GB)."
        )
        and "Read-only check" not in m
        for m in shown
    ), shown
