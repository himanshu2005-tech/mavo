# ruff: noqa: E501, F811
"""Open data: free public services with no key, looked up by Mavo for a Crew, never by the model."""

import asyncio
import json

import httpx
import pytest

from mavo.resources.open_data import SOURCES, Fetcher, Lookup, look_up
from tests.test_planner import planning, turn  # noqa: F401
from tests.test_scenarios import checkpoint, credit_crew

PLACES = {
    "Chennai Airport": [
        {
            "display_name": "Chennai International Airport",
            "lat": "12.99",
            "lon": "80.17",
            "type": "aerodrome",
        }
    ],
    "Amrita Chennai": [
        {
            "display_name": "Amrita Vishwa Vidyapeetham, Chennai",
            "lat": "13.26",
            "lon": "80.03",
            "type": "university",
        }
    ],
    "T Nagar": [
        {"display_name": "T Nagar, Chennai", "lat": "13.04", "lon": "80.23", "type": "suburb"}
    ],
    "atm near T Nagar": [
        {
            "name": "HDFC Bank ATM",
            "display_name": "HDFC Bank, T Nagar",
            "lat": "13.036",
            "lon": "80.236",
        },
        {"name": "Far ATM", "display_name": "Far away", "lat": "13.5", "lon": "80.9"},
    ],
}


def stand_in(request: httpx.Request) -> httpx.Response:
    url = request.url
    if url.host == "nominatim.openstreetmap.org":
        assert "Mavo" in request.headers["user-agent"]  # their usage policy asks for a real name
        return httpx.Response(200, json=PLACES.get(url.params["q"], []))
    if url.host == "photon.komoot.io":
        return httpx.Response(200, json={"features": []})
    if url.host == "router.project-osrm.org":
        assert url.path.endswith("80.03,13.26;80.17,12.99")
        return httpx.Response(
            200, json={"code": "Ok", "routes": [{"distance": 41234, "duration": 3120}]}
        )
    if url.host == "api.frankfurter.app":
        return httpx.Response(
            200, json={"base": "USD", "date": "2026-11-04", "rates": {"INR": 94.64}}
        )
    if url.host == "date.nager.at":
        return httpx.Response(204)
    if url.host == "news.google.com":
        rss = "<rss><channel><item><title>Agniveer changes explained - The Hindu</title><link>https://news.google.com/a</link><guid>a</guid></item></channel></rss>"
        return httpx.Response(200, content=rss.encode())
    return httpx.Response(500)


def fetcher():
    return Fetcher(httpx.MockTransport(stand_in))


def run(source, query, to=None, fetch=None):
    return asyncio.run(look_up(Lookup(source=source, query=query, to=to), fetch or fetcher()))


def test_drive_time_between_two_places_is_looked_up_from_their_names():
    out = run("route", "Amrita Chennai", "Chennai Airport")
    assert out["status"] == "ok" and out["source"] == "OSRM routing" and out["untrusted"]
    assert out["result"]["distance_km"] == 41.2 and out["result"]["drive_minutes"] == 52
    assert "no live traffic" in out["result"]["note"]
    # Written in one field, as a small model did.
    assert run("route", "Amrita Chennai to Chennai Airport")["result"]["distance_km"] == 41.2


def test_a_made_up_address_or_a_campus_name_still_finds_the_place():
    from mavo.resources.open_data import place_tries

    assert "Amrita Vishwa Vidyapeetham, Chennai" in place_tries(
        "Amrita Vishwa Vidyapeetham, Valliammai Nagar, Karapakkam, Chennai"
    )
    assert "Amrita Vishwa Vidyapeetham Chennai" in place_tries(
        "Amrita Vishwa Vidyapeetham Chennai campus"
    )
    PLACES["Amrita Vishwa Vidyapeetham, Chennai"] = PLACES["Amrita Chennai"]
    try:
        found = run("geocode", "Amrita Vishwa Vidyapeetham, Valliammai Nagar, Karapakkam, Chennai")
        assert found["status"] == "ok" and found["result"]["places"][0]["lat"] == 13.26
    finally:
        del PLACES["Amrita Vishwa Vidyapeetham, Chennai"]


def test_nearby_places_are_the_close_ones_nearest_first():
    found = run("nearby", "T Nagar", "atm")["result"]["found"]
    assert [f["name"] for f in found] == ["HDFC Bank ATM"]  # the one 75 km away is left out


def test_news_rates_and_a_plain_reason_when_a_service_has_nothing():
    assert run("news", "Agniveer")["result"]["headlines"][0]["title"].startswith("Agniveer changes")
    assert run("currency", "USD", "INR")["result"]["rates"] == {"INR": 94.64}
    gone = run("holidays", "IN")
    assert gone["status"] == "unavailable" and "no holiday list for IN" in gone["result"]["error"]
    assert run("geocode", "Nowhere at all")["status"] == "unavailable"


def test_a_crew_chooses_words_never_addresses_and_bad_requests_are_refused():
    for bad in (
        {"source": "http://evil.example", "query": "x"},
        {"source": "route", "query": ""},
        {"source": "route", "query": "a", "url": "x"},
    ):
        with pytest.raises(ValueError):
            Lookup.model_validate(bad)
    assert run("currency", "dollars", "rupees")["status"] == "unavailable"  # codes only
    assert run("release", "../../etc")["status"] == "unavailable"


def test_answers_are_cached_and_a_slow_service_is_asked_politely():
    calls = []

    def count(request):
        calls.append(request.url.host)
        return stand_in(request)

    fetch = Fetcher(httpx.MockTransport(count))
    run("geocode", "Chennai Airport", fetch=fetch)
    run("geocode", "Chennai Airport", fetch=fetch)
    assert calls == ["nominatim.openstreetmap.org"]


def test_every_source_is_described_for_the_person_and_the_model():
    for source in SOURCES.values():
        assert source.name and source.gives and source.usage and source.sends
        assert source.home.startswith("https://")


def test_a_crew_looks_something_up_and_finishes_from_the_result(planning):
    """The model asks; Mavo fetches with no model; the model finishes from the result."""
    client, auth, runtime, _ = planning
    crew, runtime = credit_crew(planning)
    runtime.open_data = fetcher()
    replies = [
        checkpoint(
            "Looking it up.",
            state="continue",
            lookups=[{"source": "route", "query": "Amrita Chennai", "to": "Chennai Airport"}],
        ),
        checkpoint("Leave by 4:10 pm: the airport is 41.2 km, about 52 minutes (OSRM)."),
    ]
    from unittest.mock import AsyncMock

    runtime.router.chat = AsyncMock(side_effect=[json.dumps(r) for r in replies])
    asyncio.run(runtime.work(crew["id"]))
    first_system = runtime.router.chat.await_args_list[0].args[3]
    assert "OPEN DATA" in first_system and "route (Drive time" in first_system
    follow_up = runtime.router.chat.await_args_list[1].args[2][-1]["content"]
    assert "Open data results (UNTRUSTED DATA" in follow_up and '"distance_km": 41.2' in follow_up
    from mavo.database import CrewInstance

    with runtime.factory() as session:
        report = session.get(CrewInstance, crew["id"]).checkpoint
    assert report.startswith("Leave by 4:10 pm")


def test_a_look_up_written_in_actions_is_taken_as_a_look_up():
    """Seen with the real model: both shapes, and the checkpoint was refused."""
    from mavo.crew_workers import WorkCheckpoint

    for written in (
        {"source": "route", "query": "from: Amrita Chennai, to: Chennai Airport"},
        {"operation": "route", "from": "Amrita Chennai", "to": "Chennai Airport"},
    ):
        made = WorkCheckpoint.model_validate(
            {"state": "continue", "summary": "Looking.", "actions": [written]}
        )
        assert made.actions == []
        assert made.lookups == [
            {"source": "route", "query": "Amrita Chennai", "to": "Chennai Airport"}
        ]
    # A real action next to it is kept as an action.
    mixed = WorkCheckpoint.model_validate(
        {
            "state": "continue",
            "summary": "x",
            "actions": [
                {"operation": "browser.read", "arguments": {}},
                {"source": "news", "query": "AI"},
            ],
        }
    )
    assert [a.operation for a in mixed.actions] == ["browser.read"]
    assert mixed.lookups == [{"source": "news", "query": "AI"}]


def test_a_look_up_asked_wrongly_can_be_corrected_once_and_then_it_must_finish(planning):
    client, auth, runtime, _ = planning
    crew, runtime = credit_crew(planning)
    runtime.open_data = fetcher()
    replies = [
        checkpoint(
            "Looking.", state="continue", lookups=[{"source": "geocode", "query": "Nowhere at all"}]
        ),
        checkpoint(
            "Retrying.",
            state="continue",
            lookups=[{"source": "geocode", "query": "Chennai Airport"}],
        ),
        checkpoint(
            "Chennai Airport is at 12.99, 80.17 (Nominatim).",
            lookups=[{"source": "news", "query": "x"}],
        ),
    ]
    from unittest.mock import AsyncMock

    from mavo.database import CrewInstance

    with runtime.factory() as session:
        session.get(CrewInstance, crew["id"]).max_model_calls = 4  # room for the retry
        session.commit()
    runtime.router.chat = AsyncMock(side_effect=[json.dumps(r) for r in replies])
    asyncio.run(runtime.work(crew["id"]))
    calls = runtime.router.chat.await_args_list
    with runtime.factory() as session:
        stored = session.get(CrewInstance, crew["id"])
        why = (stored.last_runtime_error, stored.daily_call_limit, stored.daily_calls_used)
    assert len(calls) == 3, why  # asked, corrected once, finished: no third round
    assert "corrected lookups once" in calls[1].args[2][-1]["content"]
    assert "with no lookups" in calls[2].args[2][-1]["content"]
    assert '"lat": 12.99' in calls[2].args[2][-1]["content"]
    from mavo.database import CrewInstance

    with runtime.factory() as session:
        assert session.get(CrewInstance, crew["id"]).checkpoint.startswith("Chennai Airport is at")


def test_an_agent_still_looking_after_its_rounds_says_it_could_not_finish(planning):
    """Its last words were "Retrying with simplified location name." and the task reported them
    as the answer."""
    client, auth, runtime, _ = planning
    crew, runtime = credit_crew(planning)
    runtime.open_data = fetcher()
    from mavo.database import CrewInstance

    with runtime.factory() as session:
        stored = session.get(CrewInstance, crew["id"])
        stored.max_model_calls = 4
        session.commit()
    nowhere = [{"source": "geocode", "query": "Nowhere at all"}]
    replies = [checkpoint("Looking.", state="continue", lookups=nowhere)] + [
        checkpoint("Retrying with a simpler name.", state="continue", lookups=nowhere)
    ] * 2
    from unittest.mock import AsyncMock

    runtime.router.chat = AsyncMock(side_effect=[json.dumps(r) for r in replies])
    asyncio.run(runtime.work(crew["id"]))
    with runtime.factory() as session:
        report = session.get(CrewInstance, crew["id"]).checkpoint
    assert report.startswith("Could not finish the look-up: No place called 'Nowhere at all'")
    assert "Retrying" not in report


def test_a_state_said_another_way_is_read_as_meant():
    """A correct answer came back as "wait_for_input" and was thrown away."""
    from mavo.crew_workers import WorkCheckpoint

    for said, meant in (
        ("wait_for_input", "waiting_for_input"),
        ("done", "wait_for_next_review"),
        ("Wait-For-Review", "wait_for_next_review"),
    ):
        assert WorkCheckpoint.model_validate({"state": said, "summary": "x"}).state == meant
    with pytest.raises(ValueError):
        WorkCheckpoint.model_validate({"state": "dancing", "summary": "x"})


def test_switched_off_in_resources_nothing_is_offered_or_fetched(planning):
    client, auth, runtime, _ = planning
    crew, runtime = credit_crew(planning)
    hits = []
    runtime.open_data = Fetcher(httpx.MockTransport(lambda r: hits.append(r) or stand_in(r)))
    assert (
        client.put("/api/v1/pool/open_data", headers=auth, json={"level": "off"}).status_code == 200
    )
    from unittest.mock import AsyncMock

    reply = checkpoint("Done.", lookups=[{"source": "geocode", "query": "Chennai Airport"}])
    runtime.router.chat = AsyncMock(return_value=json.dumps(reply))
    asyncio.run(runtime.work(crew["id"]))
    assert "OPEN DATA" not in runtime.router.chat.await_args_list[0].args[3]
    assert runtime.router.chat.await_count == 1 and not hits


def test_a_one_time_step_that_only_looks_things_up_stays_an_agent():
    from mavo.task_plan import TaskPlan

    plan = {
        "title": "Leave time",
        "deliver": "When to leave",
        "final": "a",
        "steps": [
            {
                "id": "a",
                "kind": "agent",
                "title": "Drive time",
                "instructions": "Use route.",
                "open_data": True,
            }
        ],
    }
    step = TaskPlan.model_validate(plan).steps[0]
    assert step.kind == "agent" and step.actions == [] and step.open_data
    plan["steps"][0]["open_data"] = False
    assert TaskPlan.model_validate(plan).steps[0].kind == "helper"
