"""An agent that emails other people has a reviewer, and what it would send waits for that
review: made on a pass, dropped on a fix. A mail-booking crew of two agents sent replies nobody
had checked, and a reviewer that looked only afterwards could not stop one."""

import json

import pytest

from mavo.action_rules import held_for_review
from mavo.coordinator_contract import parse_decision
from mavo.database import ActionRequest, CrewInstance, utcnow
from tests.test_tasks import setup


def decision(agents):
    return json.dumps(
        {
            "reply": "Here is your mail crew.",
            "crew_proposal": {
                "template_id": "communication",
                "name": "Mail Slot Booker",
                "responsibilities": "Book meetings from mail and confirm them.",
                "trigger_type": "gmail_message",
                "composition": {"agents": agents, "limits": {"model_calls": 12}},
            },
        }
    )


def agent(key, actions=(), reviews=None, depends=()):
    return {
        "key": key,
        "name": key.title(),
        "role": key,
        "instructions": f"Do the {key} part.",
        "capabilities": ["google:gmail:read"],
        "actions": list(actions),
        "depends_on": list(depends),
        "reviews": reviews,
        "max_model_calls": 3,
        "completion_criteria": "Done.",
    }


def test_a_crew_that_emails_people_without_a_reviewer_goes_back_to_be_designed_again():
    two = [agent("reader"), agent("closer", ["calendar.create", "gmail.send"], depends=["reader"])]
    with pytest.raises(ValueError, match="'closer' emails other people"):
        parse_decision(decision(two))
    gated = [*two, agent("auditor", reviews="closer", depends=["closer"])]
    assert parse_decision(decision(gated)).crew_proposal.composition.agents[2].reviews == "closer"
    # Only calendar changes, no email to anyone: no reviewer is required.
    parse_decision(decision([agent("booker", ["calendar.create"])]))


def worker(runtime, held=True):
    with runtime.factory() as session:
        session.add(
            CrewInstance(
                id="closer-worker",
                name="_agent_closer",
                template_id="communication",
                responsibilities="Close.",
                permissions=["model:invoke"],
                provider="ollama",
                model="m",
                hidden=True,
                configuration_plan={"held_for_review": "run-1"} if held else {},
            )
        )
        session.commit()


def proposal(runtime, n, operation="gmail.send", **extra):
    with runtime.factory() as session:
        row = ActionRequest(
            crew_id="closer-worker",
            account_subject="local",
            operation=operation,
            arguments={"to": "hr@example.com", "subject": "Re: Interview", "body": "2 PM works."},
            fingerprint=f"held-{n}",
            **extra,
        )
        session.add(row)
        session.commit()
        return row.id


def test_what_a_reviewed_agent_proposes_waits_for_its_review(client, auth):
    runtime = setup(client, auth)
    worker(runtime)
    action_id = proposal(runtime, 1)
    with runtime.factory() as session:
        row = session.get(ActionRequest, action_id)
        assert held_for_review(session, row)  # waits: its reviewer has not passed it
        row.operation = "browser.read"
        assert not held_for_review(session, row)  # looking is never held


@pytest.mark.asyncio
async def test_a_pass_makes_the_held_changes_and_a_fix_drops_them(client, auth, monkeypatch):
    runtime = setup(client, auth)
    worker(runtime)
    since = utcnow()
    kept, dropped = proposal(runtime, 1), None
    made = []

    async def maybe_execute(action_id):
        with runtime.factory() as session:
            made.append((action_id, session.get(ActionRequest, action_id).approved_by))
        return {"id": action_id, "status": "completed", "target_summary": "Re: Interview"}

    monkeypatch.setattr(runtime.actions, "maybe_execute", maybe_execute)
    runner = runtime.task_runner
    lines = await runner.release("closer-worker", since)
    assert made == [(kept, "review")] and lines == ["Done: Send & reply (Re: Interview)"]

    with runtime.factory() as session:  # made: no longer waiting
        session.get(ActionRequest, kept).status = "completed"
        session.commit()
    dropped = proposal(runtime, 2)
    runner.discard("closer-worker", since, "Its reviewer asked for changes; it proposes again.")
    with runtime.factory() as session:
        row = session.get(ActionRequest, dropped)
        assert row.status == "rejected" and "reviewer" in row.error
    assert await runner.release("closer-worker", since) == []  # nothing left to make


def test_the_person_chooses_each_agents_model(client, auth):
    from mavo.crew_composition import CrewComposition
    from mavo.schemas import CoordinatorDecision

    runtime = setup(client, auth)
    other = client.post(
        "/api/v1/routes",
        headers=auth,
        json={"name": "Strong", "provider": "ollama", "model": "big"},
    ).json()
    proposal_ = CoordinatorDecision.model_validate(
        json.loads(
            decision(
                [
                    agent("reader"),
                    agent("closer", ["gmail.send"], depends=["reader"]),
                    agent("auditor", reviews="closer", depends=["closer"]),
                ]
            )
        )
    ).crew_proposal
    with runtime.factory() as session:
        crew = runtime.planner.apply_plan(
            session, CoordinatorDecision(reply="ok", crew_proposal=proposal_), None
        )
        crew_id = crew.id
        session.commit()
    changed = client.post(
        f"/api/v1/crew/{crew_id}/agents/closer/model",
        headers=auth,
        json={"route_id": other["id"]},
    )
    assert changed.status_code == 200, changed.text
    plan = changed.json()["configuration_plan"]
    chosen = {a.key: a.route_id for a in CrewComposition.model_validate(plan["composition"]).agents}
    assert chosen["closer"] == other["id"]
    assert plan["draft"]["composition"]["agents"][1]["route_id"] == other["id"]
    unknown = client.post(
        f"/api/v1/crew/{crew_id}/agents/closer/model", headers=auth, json={"route_id": "nope"}
    )
    assert unknown.status_code == 400


def test_a_sample_email_has_always_arrived_already():
    """A sample email dated tomorrow was taken as not received yet, and the test tested nothing."""
    from email.utils import parsedate_to_datetime

    from mavo.first_run import safe_sample

    sample = safe_sample(
        {
            "gmail": [
                {
                    "sender": "HR <hr@acme.com>",
                    "subject": "Interview",
                    "date": "Sun, 15 Nov 2099 09:00:00 +0530",
                    "text": "Mon 10:00 or 14:00?",
                }
            ]
        },
        ["gmail"],
    )
    sent = parsedate_to_datetime(sample["gmail"][0]["date"])
    assert sent <= utcnow() and sample["gmail"][0]["sender"].endswith("@example.com>")


def test_a_reviewers_verdict_is_kept_when_its_summary_is_empty():
    """Refused for an empty summary, a reviewer's reply went back for repair, the verdict was
    lost, and the setup test failed on it."""
    from mavo.crew_workers import WorkCheckpoint

    checkpoint = WorkCheckpoint.model_validate(
        {"state": "wait_for_next_review", "summary": "", "review_verdict": "fix"}
    )
    assert checkpoint.review_verdict == "fix" and checkpoint.summary == "Review: fix."


def test_findings_are_handed_on_though_the_last_turn_said_nothing_new(client, auth):
    """A reader listed the interview and its offered times, closed with "Nothing new.", and the
    booker never saw the times."""
    from datetime import timedelta

    from mavo.database import CrewRun

    runtime = setup(client, auth)
    worker(runtime, held=False)
    start = utcnow()
    with runtime.factory() as session:
        for n, text in enumerate(
            ["Nothing new.\n\nRavi asks for Sun 15 Nov, 10:00 or 14:00 IST.", "Nothing new."]
        ):
            session.add(
                CrewRun(
                    crew_id="closer-worker",
                    crew_name="Reader",
                    route_name="A",
                    provider="ollama",
                    model="m",
                    prompt="Read.",
                    status="completed",
                    result=text,
                    created_at=start + timedelta(seconds=n + 1),
                )
            )
        session.commit()
    answer = runtime.task_runner.last_answer("closer-worker", start)
    assert answer.startswith("Ravi asks for Sun 15 Nov, 10:00 or 14:00 IST.")
