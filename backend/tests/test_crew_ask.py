# ruff: noqa: F811 - `planning` is a fixture, imported and then named as a parameter
"""Asking a Crew: the question goes to Mavo's assistant with the Crew's report beside it, so it can
answer, or do something about it (a task), and a task's result becomes the answer."""

import json
from datetime import timedelta

from mavo.crew_templates import CREW_TEMPLATES_BY_ID
from mavo.database import Conversation, CrewInstance, utcnow
from tests.test_planner import planning, turn  # noqa: F401
from tests.test_scenarios import checkpoint, decide, run_work
from tests.test_tasks import plan, scripted, step, wait

REPORT = (
    "- What are the changes in the Agniveer scheme, and why? — The Hindu\n"
    "- Army raises recruitment age limit — Indian Express"
)


def news_crew(client, auth, runtime, name="Armed Forces News Watch"):
    client.post(
        "/api/v1/routes", headers=auth, json={"name": "A", "provider": "ollama", "model": "m"}
    ).raise_for_status()
    route = client.get("/api/v1/routes", headers=auth).json()[0]
    template = CREW_TEMPLATES_BY_ID["research"]
    with runtime.factory() as session:
        crew = CrewInstance(
            template_id=template.id,
            name=name,
            responsibilities="Watch Indian armed forces news and tell me what is new.",
            autonomy_mode="proactive",
            status="active",
            provider="ollama",
            model="m",
            route_id=route["id"],
            max_model_calls=4,
            review_interval_minutes=600,
            daily_call_limit=5,
            reminder_text="",
            trigger_type="interval",
            event_cursor=0,
            resources=[],
            configuration_plan={},
            permissions=list(template.default_permissions),
            triggers=[],
            is_running=True,
            runtime_state="waiting",
            next_run_at=utcnow() + timedelta(hours=8),
            checkpoint=REPORT + "\n\nAction browser.read: completed",
        )
        session.add(crew)
        session.commit()
        return crew.id


def last_message(coordinator):
    return coordinator.calls[-1].messages[-1].content


def test_a_question_goes_to_the_assistant_with_the_crews_report_and_its_answer_comes_back(
    planning,
):
    client, auth, runtime, coordinator = planning
    crew = news_crew(client, auth, runtime)
    decide(planning, reply="Agnipath recruits soldiers for four years; 25% are kept on.")
    item = client.post(
        f"/api/v1/crew/{crew}/asks", headers=auth, json={"question": "whats agniveer scheme"}
    ).json()
    assert item["answer"] == "Agnipath recruits soldiers for four years; 25% are kept on."
    assert not item["pending"] and item["task_id"] is None
    sent = last_message(coordinator)
    assert 'About my Crew "Armed Forces News Watch"' in sent and "Agniveer scheme" in sent
    assert "whats agniveer scheme\n\n(Asked from my Crew's card" in sent
    assert "do not answer from memory" in sent and "Action browser.read" not in sent
    # The report goes once; the next question in the same conversation is just the question.
    decide(planning, reply="It was announced in June 2022.")
    client.post(f"/api/v1/crew/{crew}/asks", headers=auth, json={"question": "since when?"})
    assert last_message(coordinator).startswith("since when?\n\n(Asked from my Crew's card")
    assert "Agniveer scheme" not in last_message(coordinator)
    asked = client.get(f"/api/v1/crew/{crew}/asks", headers=auth).json()
    assert [i["question"] for i in asked] == ["whats agniveer scheme", "since when?"]
    # It is an ordinary chat too, named for the Crew.
    with runtime.factory() as session:
        titles = [c.title for c in session.query(Conversation)]
        assert "Ask · Armed Forces News Watch" in titles
    assert client.delete(f"/api/v1/crew/{crew}/asks", headers=auth).status_code == 204
    assert client.get(f"/api/v1/crew/{crew}/asks", headers=auth).json() == []


def test_when_the_assistant_starts_a_task_its_result_becomes_the_answer(planning):
    client, auth, runtime, _ = planning
    crew = news_crew(client, auth, runtime)
    scripted(runtime, helper={"writer": "The age limit rose from 21 to 23."})
    decide(
        planning,
        reply="Looking it up; the answer will appear here.",
        task=plan(step("a", role="writer"), deliver="The answer"),
    )
    item = client.post(
        f"/api/v1/crew/{crew}/asks", headers=auth, json={"question": "what changed exactly?"}
    ).json()
    assert item["pending"] and item["task_id"]
    assert item["answer"] == "Looking it up; the answer will appear here."
    assert wait(client, auth, item["task_id"])["status"] == "done"
    [done] = client.get(f"/api/v1/crew/{crew}/asks", headers=auth).json()
    assert not done["pending"] and done["answer"] == "The age limit rose from 21 to 23."


def sorter(runtime, reply: dict):
    """The Crew's own model, as it sorts what the person typed."""
    seen = []

    async def chat(provider, model, messages, system, credential_id=None):
        seen.append((system, messages[-1]["content"]))
        return "Sure:\n" + json.dumps(reply)

    runtime.router.chat = chat
    return seen


def test_telling_a_crew_how_to_work_is_kept_confirmed_and_used_from_the_next_review(planning):
    """'show only jobs' was sent as a passing reply: followed a review or two, then lost, and
    nothing said it had been heard."""
    client, auth, runtime, coordinator = planning
    crew = news_crew(client, auth, runtime, name="Inbox Summary")
    calls = len(coordinator.calls)
    sorter(
        runtime,
        {
            "kind": "preference",
            "rule": "Show only job-related emails.",
            "ack": "Got it, from now on I'll only show job emails.",
            "replaces": [],
        },
    )
    item = client.post(
        f"/api/v1/crew/{crew}/asks", headers=auth, json={"question": "show only jobs"}
    ).json()
    assert item["kind"] == "preference" and not item["pending"]
    assert item["answer"] == "Got it, from now on I'll only show job emails. Checking again now."
    assert len(coordinator.calls) == calls  # nothing went to the chat assistant
    with runtime.factory() as session:
        stored = session.get(CrewInstance, crew)
        assert [p["text"] for p in stored.configuration_plan["preferences"]] == [
            "Show only job-related emails."
        ]
        assert stored.next_run_at <= utcnow().replace(tzinfo=stored.next_run_at.tzinfo)
    # Its next review is told, as a standing rule, not as an old chat message.
    chat = run_work(runtime, crew, checkpoint("Nothing new."))
    prompt = chat.await_args_list[0].args[2][0]["content"]
    assert "standing preferences" in prompt and "- Show only job-related emails." in prompt
    # A later preference that contradicts it replaces it; one can be removed.
    sorter(
        runtime,
        {
            "kind": "preference",
            "rule": "Show jobs and bank emails.",
            "ack": "Done.",
            "replaces": [1],
        },
    )
    client.post(f"/api/v1/crew/{crew}/asks", headers=auth, json={"question": "banks too"})
    sorter(
        runtime, {"kind": "preference", "rule": "Keep it short.", "ack": "Sure.", "replaces": []}
    )
    client.post(f"/api/v1/crew/{crew}/asks", headers=auth, json={"question": "shorter"})
    with runtime.factory() as session:
        texts = [
            p["text"] for p in session.get(CrewInstance, crew).configuration_plan["preferences"]
        ]
        assert texts == ["Show jobs and bank emails.", "Keep it short."]
    left = client.delete(f"/api/v1/crew/{crew}/preferences/0", headers=auth).json()
    assert [p["text"] for p in left["configuration_plan"]["preferences"]] == ["Keep it short."]
    assert client.delete(f"/api/v1/crew/{crew}/preferences/5", headers=auth).status_code == 404


def test_a_question_is_still_a_question_and_a_muddled_sort_counts_as_one(planning):
    client, auth, runtime, coordinator = planning
    crew = news_crew(client, auth, runtime)
    for reply in ({"kind": "question"}, {"kind": "preference"}, {"nonsense": 1}):
        sorter(runtime, reply)
        decide(planning, reply="Here is the answer.")
        item = client.post(
            f"/api/v1/crew/{crew}/asks", headers=auth, json={"question": "what is new?"}
        ).json()
        assert item["kind"] == "question" and item["answer"] == "Here is the answer."


def test_asking_a_crew_that_is_not_there_is_refused(planning):
    client, auth, _, _ = planning
    assert (
        client.post("/api/v1/crew/nope/asks", headers=auth, json={"question": "x"}).status_code
        == 404
    )
    assert (
        client.post("/api/v1/crew/nope/asks", headers=auth, json={"question": "  "}).status_code
        == 422
    )


def test_a_desktop_update_is_written_as_the_news_not_as_a_report_of_work():
    from mavo.crew_workers import ALERT_RULE, NOTIFICATION_STYLE

    assert "lead with the news itself" in NOTIFICATION_STYLE
    assert "reviewed" in NOTIFICATION_STYLE  # named as what not to write
    assert ALERT_RULE.endswith("review.")  # the alert rule stays the last thing said
