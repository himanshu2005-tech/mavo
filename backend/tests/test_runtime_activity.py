import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from mavo.credentials import MemoryCredentialStore
from mavo.model_router import ModelRouter
from mavo.ollama import OllamaClient
from mavo.runtime_activity import ActivityFeed

ROUTE = SimpleNamespace(
    id="route-a", provider="airouter", model="test-model", credential_id="secret-ref"
)


def test_only_actual_requests_signal_provider_and_reads_do_not_execute():
    feed = ActivityFeed()
    with feed.scope("crew-a"):
        feed.start_run("run-a", ROUTE)
        assert not feed.snapshot()["active_calls"]
        with feed.model_call("airouter", "test-model", "secret-ref"):
            snapshot = feed.snapshot(0)
            assert len(snapshot["active_calls"]) == 1
            assert snapshot["active_calls"][0]["route_id"] == "route-a"
        feed.finish_run("run-a", "completed")
    snapshot = feed.snapshot(0)
    assert [e["kind"] for e in snapshot["events"]] == [
        "run_started",
        "model_started",
        "model_finished",
        "run_finished",
    ]
    assert snapshot["events"][-1]["status"] == "completed"
    assert not snapshot["active_runs"] and not snapshot["active_calls"]
    assert feed.snapshot()["events"] == []  # baseline never replays history
    assert feed.snapshot(snapshot["cursor"])["events"] == []
    assert "secret-ref" not in json.dumps(snapshot)


def test_failed_cancelled_short_and_unscoped_calls():
    feed = ActivityFeed()
    with feed.model_call("airouter", "test-model", "secret-ref"):
        pass
    assert feed.snapshot(0)["events"] == []  # unrelated chat is not attributed to a Crew
    with feed.scope("crew-a"):
        feed.start_run("run-a", ROUTE)
        with pytest.raises(ValueError), feed.model_call("airouter", "test-model", "secret-ref"):
            raise ValueError("private request content")
        with (
            pytest.raises(asyncio.CancelledError),
            feed.model_call("airouter", "test-model", "secret-ref"),
        ):
            raise asyncio.CancelledError()
        feed.finish_run("run-a", "cancelled")
    events = feed.snapshot(0)["events"]
    assert [e["status"] for e in events if e["kind"] == "model_finished"] == ["failed", "cancelled"]
    assert "private request content" not in json.dumps(events)


def test_isolation_bounded_replay_and_signout_fencing():
    feed = ActivityFeed()
    other = ActivityFeed()
    with feed.scope("old-crew"):
        feed.start_run("old-run", ROUTE)
        with feed.model_call("airouter", "test-model", "secret-ref"):
            assert not other.snapshot(0)["events"]
            old_stream = feed.snapshot()["stream_id"]
            feed.clear()
        assert not feed.snapshot(0)["events"]
        assert feed.snapshot()["stream_id"] != old_stream
    for i in range(300):
        with feed.scope("crew"):
            feed.start_run(f"run-{i}", ROUTE)
            feed.finish_run(f"run-{i}", "completed")
    assert len(feed.snapshot(0)["events"]) == 512
    assert feed.snapshot(0)["reset"]


def test_route_mismatch_is_never_attributed_to_a_different_saved_connection():
    feed = ActivityFeed()
    with feed.scope("crew"):
        feed.start_run("run", ROUTE)
        with feed.model_call("airouter", "other-model", "other-secret"):
            assert feed.snapshot()["active_calls"][0]["route_id"] is None
        feed.finish_run("run", "completed")


@pytest.mark.asyncio
async def test_concurrent_crews_and_cancellation_do_not_mix_provider_requests():
    feed = ActivityFeed()
    ready = [asyncio.Event(), asyncio.Event()]
    release = asyncio.Event()

    async def work(index):
        with feed.scope(f"crew-{index}"):
            feed.start_run(f"run-{index}", ROUTE)
            with feed.model_call("airouter", "test-model", "secret-ref"):
                ready[index].set()
                await release.wait()
            feed.finish_run(f"run-{index}", "completed")

    tasks = [asyncio.create_task(work(i)) for i in range(2)]
    await asyncio.gather(*(event.wait() for event in ready))
    assert {event["crew_id"] for event in feed.snapshot()["active_calls"]} == {"crew-0", "crew-1"}
    tasks[0].cancel()
    with pytest.raises(asyncio.CancelledError):
        await tasks[0]
    assert [event["crew_id"] for event in feed.snapshot()["active_calls"]] == ["crew-1"]
    release.set()
    await tasks[1]
    events = feed.snapshot(0)["events"]
    assert any(e["crew_id"] == "crew-0" and e.get("status") == "interrupted" for e in events)
    assert any(e["crew_id"] == "crew-1" and e.get("status") == "completed" for e in events)
    assert not feed.snapshot()["active_calls"] and not feed.snapshot()["active_runs"]


@pytest.mark.asyncio
async def test_model_router_observes_real_inflight_request_and_cleans_up():
    entered, release = asyncio.Event(), asyncio.Event()

    async def handler(request):
        entered.set()
        await release.wait()
        return httpx.Response(200, json={"choices": [{"message": {"content": "answer"}}]})

    credentials = MemoryCredentialStore()
    credentials.set("secret-ref", "never-expose-this-api-key")
    router = ModelRouter(
        OllamaClient("http://127.0.0.1:11434"), credentials, httpx.MockTransport(handler)
    )

    async def work():
        with router.activity.scope("crew"):
            router.activity.start_run("run", ROUTE)
            await router.chat(
                "airouter",
                "test-model",
                [{"role": "user", "content": "private prompt"}],
                "private system",
                "secret-ref",
            )
            router.activity.finish_run("run", "completed")

    task = asyncio.create_task(work())
    await entered.wait()
    assert router.activity.snapshot()["active_calls"][0]["crew_id"] == "crew"
    release.set()
    await task
    snapshot = router.activity.snapshot(0)
    assert not snapshot["active_calls"]
    assert "private" not in json.dumps(snapshot) and "never-expose" not in json.dumps(snapshot)


def test_runtime_endpoint_is_authenticated_and_metadata_only(client, auth):
    assert client.get("/api/v1/runtime/activity").status_code == 401
    response = client.get("/api/v1/runtime/activity", headers=auth)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["events"] == []
    assert client.get("/api/v1/runtime/activity?after=-1", headers=auth).status_code == 422


def test_manual_run_shorter_than_a_refresh_is_replayed_once_without_content(client, auth):
    route = client.post(
        "/api/v1/routes",
        headers=auth,
        json={
            "name": "Local fixture",
            "provider": "ollama",
            "model": "qwen-test:latest",
        },
    ).json()
    crew = client.post(
        "/api/v1/crew",
        headers=auth,
        json={
            "name": "Activity fixture",
            "template_id": "research",
            "responsibilities": "A private responsibility",
            "route_id": route["id"],
            "max_model_calls": 1,
        },
    ).json()
    cursor = client.get("/api/v1/runtime/activity", headers=auth).json()["cursor"]
    run = client.post(
        f"/api/v1/crew/{crew['id']}/run", headers=auth, json={"message": "A private test prompt"}
    ).json()
    assert run["status"] == "completed"
    snapshot = client.get(f"/api/v1/runtime/activity?after={cursor}", headers=auth).json()
    assert [e["kind"] for e in snapshot["events"]] == [
        "run_started",
        "model_started",
        "model_finished",
        "run_finished",
    ]
    assert {e["run_id"] for e in snapshot["events"]} == {run["id"]}
    assert snapshot["active_calls"] == [] and snapshot["active_runs"] == []
    assert "private" not in json.dumps(snapshot) and "A real model response" not in json.dumps(
        snapshot
    )
    assert (
        client.get(f"/api/v1/runtime/activity?after={snapshot['cursor']}", headers=auth).json()[
            "events"
        ]
        == []
    )
