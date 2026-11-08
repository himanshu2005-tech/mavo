"""Crew talking to one another: "@Inbox Summary, when you find a job email, tell @Placements
Job Watch". The planner returns connect; Mavo links them (the second hears each of the first's
finished results) and refuses a loop."""

from mavo.database import CrewInstance
from tests.test_crew_ask import news_crew
from tests.test_planner import planning, turn  # noqa: F401
from tests.test_scenarios import decide


def second_crew(runtime, like: str, name: str) -> str:
    """Another of the person's Crew, made like the first (the helper creates the route too)."""
    with runtime.factory() as session:
        first = session.get(CrewInstance, like)
        copy = CrewInstance(
            **{
                c.name: getattr(first, c.name)
                for c in CrewInstance.__table__.columns
                if c.name not in {"id", "name"}
            },
            name=name,
        )
        session.add(copy)
        session.commit()
        return copy.id


def test_two_existing_crew_are_connected_from_chat_and_a_loop_is_refused(planning):
    client, auth, runtime, _ = planning
    inbox = news_crew(client, auth, runtime, name="Inbox Summary")
    jobs = second_crew(runtime, inbox, "Placements Job Watch")
    decide(
        planning,
        reply="Done: Placements Job Watch now hears from Inbox Summary.",
        connect=[{"from_crew_id": inbox, "to_crew_id": jobs}],
    )
    said = turn(planning, "@Inbox Summary when you find a job email, tell @Placements Job Watch")
    assert said["reply"].startswith("Done")
    with runtime.factory() as session:
        assert session.get(CrewInstance, jobs).configuration_plan["upstream"] == [inbox]
        assert "upstream" not in session.get(CrewInstance, inbox).configuration_plan

    # The other way round would make them wait on each other: refused, nothing changed.
    decide(planning, reply="Linked.", connect=[{"from_crew_id": jobs, "to_crew_id": inbox}])
    refused = client.post(
        "/api/v1/core/turn",
        headers=auth,
        json={
            "message": "@Placements Job Watch tell @Inbox Summary",
            "request_id": "11111111-1111-1111-1111-111111111111",
        },
    )
    assert refused.status_code == 409 and "wait on each other" in refused.json()["detail"]
    with runtime.factory() as session:
        assert "upstream" not in session.get(CrewInstance, inbox).configuration_plan


def test_a_planner_cannot_connect_a_crew_that_is_not_the_persons(planning):
    client, auth, runtime, _ = planning
    inbox = news_crew(client, auth, runtime, name="Inbox Summary")
    decide(planning, reply="Linked.", connect=[{"from_crew_id": inbox, "to_crew_id": "made-up-id"}])
    response = client.post(
        "/api/v1/core/turn",
        headers=auth,
        json={"message": "connect them", "request_id": "22222222-2222-2222-2222-222222222222"},
    )
    assert response.status_code == 409
