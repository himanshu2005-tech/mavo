"""The setup test runs in the background: "Test it" answers at once with "Testing…", and the
card's refresh shows how it ended. Nobody waits on a request for minutes."""

import asyncio
import threading
import time

from mavo.database import CrewInstance
from tests.test_skip_test import planning, proposal, turn  # noqa: F401


def first_run(runtime, crew_id):
    with runtime.factory() as session:
        return dict(session.get(CrewInstance, crew_id).configuration_plan.get("first_run") or {})


def waited(runtime, crew_id, status):
    for _ in range(300):
        if first_run(runtime, crew_id).get("status") == status:
            return first_run(runtime, crew_id)
        time.sleep(0.02)
    raise AssertionError(f"still {first_run(runtime, crew_id)}")


def test_test_it_answers_at_once_and_the_result_arrives_later(planning):
    client, auth, runtime, _ = planning
    runtime.first_run_in_background = True
    go = threading.Event()

    async def slow_test(rt, crew_id):
        while not go.is_set():
            await asyncio.sleep(0.01)
        with rt.factory() as session:
            crew = session.get(CrewInstance, crew_id)
            record = {**crew.configuration_plan["first_run"], "status": "passed", "message": ""}
            record.pop("phase", None)
            crew.configuration_plan = {**crew.configuration_plan, "first_run": record}
            session.commit()

    runtime.verify_first_run = slow_test
    created = proposal(planning)
    answered = client.post(f"/api/v1/crew/{created['id']}/access", headers=auth, json={})
    assert answered.status_code == 200, answered.text
    assert answered.json()["configuration_plan"]["first_run"]["status"] == "running"
    # Pressing it again while it runs does not start a second test.
    again = client.post(f"/api/v1/crew/{created['id']}/access", headers=auth, json={})
    assert again.status_code == 409
    go.set()
    assert waited(runtime, created["id"], "passed")["status"] == "passed"


def test_a_test_that_breaks_in_the_background_says_so_instead_of_testing_forever(planning):
    client, auth, runtime, _ = planning
    runtime.first_run_in_background = True

    async def broken(rt, crew_id):
        raise RuntimeError("provider went away")

    runtime.verify_first_run = broken
    created = proposal(planning)
    client.post(f"/api/v1/crew/{created['id']}/access", headers=auth, json={})
    record = waited(runtime, created["id"], "failed")
    assert record["message"] == "The test stopped. Test it again."
