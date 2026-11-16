"""Inbox setup regressions: proposed access is not a grant; model completion is not a read."""

import json
from unittest.mock import AsyncMock

import pytest

from mavo.coordinator_contract import PlannerContext, coordinator_prompt
from mavo.schemas import CoordinatorDecision, CrewDraft
from tests.test_actions import action_env  # noqa: F401


def proposal(*, body=True, roles=None):
    return CrewDraft.model_validate(
        {
            "name": "Meridian Brief",
            "template_id": "communication",
            "responsibilities": "Read new inbox messages and summarize important updates.",
            "trigger_type": "interval",
            "review_interval_minutes": 30,
            "requested_google_capabilities": ["gmail:read", *(["gmail:body"] if body else [])],
            "requested_google_resources": ["gmail"],
            "requested_notifications": True,
            "max_model_calls": 8,
            "daily_call_limit": 40,
            "composition": {
                "agents": roles
                or [
                    {
                        "key": "inbox_reader",
                        "name": "Signal Curator",
                        "role": "Inbox prioritization",
                        "instructions": "Read granted mail, ignore spam and summarize "
                        "useful updates.",
                        "capabilities": [],
                        "actions": [],
                        "max_model_calls": 4,
                        "completion_criteria": "Summarize current authorized inbox messages.",
                    }
                ],
                "limits": {"model_calls": 12},
            },
        }
    )


def setup_reader(env, *, body=True, roles=None, legacy=False):
    client, auth, _, _ = env
    runtime = client.app.state.crew_runtime
    route = client.post(
        "/api/v1/routes",
        headers=auth,
        json={"name": "Fixture", "provider": "ollama", "model": "fixture"},
    ).json()
    draft = proposal(body=body, roles=roles)
    draft.route_id = route["id"]
    with runtime.factory() as session:
        crew = runtime.planner.apply_plan(
            session, CoordinatorDecision(reply="Review", crew_proposal=draft), None
        )
        source_id = crew.id
        # Before human consent, proposed member requirements confer no account access.
        assert crew.permissions == ["model:invoke"] and crew.status == "draft"
        if legacy:
            member = crew.agents[0]
            member.definition = {**member.definition, "capabilities": []}
            composition = dict(crew.configuration_plan["composition"])
            composition["agents"] = [{**composition["agents"][0], "capabilities": []}]
            crew.configuration_plan = {**crew.configuration_plan, "composition": composition}
        session.commit()
    runtime.google_pool.read = AsyncMock(
        return_value={
            "items": [
                {
                    "id": "observed-mail",
                    "subject": "An important update",
                    "sender": "sender@example.com",
                }
            ]
        }
    )
    runtime.google_pool.message_body = AsyncMock(
        return_value={"id": "observed-mail", "text": "PRIVATE_FIXTURE_CONTENT"}
    )
    runtime.router.chat = AsyncMock(
        return_value=json.dumps(
            {
                "state": "wait_for_next_review",
                "summary": "Read-only inbox review.",
                "mail_decisions": [
                    {
                        "message_id": "observed-mail",
                        "important": False,
                        "reason": "Fixture triage",
                        "summary": "Read the update.",
                    }
                ],
            }
        )
    )
    return runtime, source_id


@pytest.mark.parametrize("body", [False, True])
@pytest.mark.parametrize("legacy", [False, True])
def test_single_unconfigured_reader_gets_only_user_selected_reads_and_is_tested_on_a_sample(
    action_env, sample_mail, body, legacy
):
    client, auth, _, state = action_env
    runtime, source_id = setup_reader(action_env, body=body, legacy=legacy)
    sample_mail(
        [
            {
                "id": "observed-mail",
                "subject": "An important update",
                "sender": "sender@example.com",
                "text": "Sample text",
            }
        ]
    )
    result = client.post(
        f"/api/v1/crew/{source_id}/access",
        headers=auth,
        json={
            "capabilities": ["gmail:read", *(["gmail:body"] if body else [])],
            "notifications": True,
        },
    )
    assert result.status_code == 200, result.text
    crew = result.json()
    check = crew["configuration_plan"]["first_run"]
    assert check["status"] == "passed", check
    assert crew["status"] == "active"
    member = next(a for a in crew["agents"] if a["status"] == "active")
    caps = set(member["definition"]["capabilities"])
    assert "google:gmail:read" in caps and "notifications:show" in caps
    assert ("google:gmail:body" in caps) == body
    assert not any(c.endswith((":modify", ":write", ":send")) for c in caps)
    # Never the person's own mail: not the inbox, not one email's text.
    runtime.google_pool.read.assert_not_awaited()
    runtime.google_pool.message_body.assert_not_awaited()
    assert check["tested_with"] == "sample"
    receipt = check["resource_checks"]["inbox_reader"][0]
    assert receipt["resource"] == "gmail" and receipt["item_count"] == 1
    assert "PRIVATE_FIXTURE_CONTENT" not in json.dumps(check["resource_checks"])
    assert not state["writes"]


@pytest.mark.parametrize("count", [1, 2])
def test_explicitly_restricted_or_multi_agent_roles_are_not_silently_broadened(action_env, count):
    client, auth, _, state = action_env
    roles = [
        {
            "key": f"role{i}",
            "name": f"Role {i}",
            "role": "Delivery only",
            "instructions": "Use supplied results only, never read inbox directly.",
            "capabilities": ["notifications:show"],
            "max_model_calls": 4,
            "completion_criteria": "Deliver a supplied report.",
        }
        for i in range(count)
    ]
    runtime, source_id = setup_reader(action_env, body=False, roles=roles)
    result = client.post(
        f"/api/v1/crew/{source_id}/access",
        headers=auth,
        json={"capabilities": ["gmail:read"], "notifications": True},
    )
    check = result.json()["configuration_plan"]["first_run"]
    assert check["status"] == "failed" and "no active member can read" in check["message"]
    assert result.json()["status"] == "draft"
    runtime.google_pool.read.assert_not_called()
    runtime.router.chat.assert_not_called()
    assert not state["writes"]


@pytest.mark.parametrize(
    "bad_snapshot",
    [{"items": [], "status": "stale"}, {"items": [], "error": "unavailable"}, {"items": None}],
)
def test_a_mail_crews_test_never_reads_the_inbox_whatever_state_it_is_in(
    action_env, sample_mail, bad_snapshot
):
    """The test is for how the agents behave, on a sample: the person's inbox is not read, so
    an unavailable one is neither certified as empty nor shown to anyone."""
    client, auth, _, _ = action_env
    runtime, source_id = setup_reader(action_env, body=False)
    sample_mail(
        [
            {
                "id": "observed-mail",
                "subject": "An important update",
                "sender": "sender@example.com",
                "text": "Sample text",
            }
        ]
    )
    runtime.google_pool.read = AsyncMock(return_value=bad_snapshot)
    result = client.post(
        f"/api/v1/crew/{source_id}/access",
        headers=auth,
        json={"capabilities": ["gmail:read"], "notifications": True},
    )
    check = result.json()["configuration_plan"]["first_run"]
    assert check["tested_with"] == "sample", check
    runtime.google_pool.read.assert_not_awaited()


def test_a_mail_crew_without_gmail_access_fails_its_test_before_any_model_call(action_env):
    client, auth, _, _ = action_env
    runtime, source_id = setup_reader(action_env, body=False)
    runtime.google_pool.auth.status = lambda: {"signed_in": False, "capabilities": []}
    result = client.post(
        f"/api/v1/crew/{source_id}/access",
        headers=auth,
        json={"capabilities": ["gmail:read"], "notifications": True},
    )
    crew = result.json()
    # Not tested at all: what is missing is said on the card, before any model call.
    assert (
        crew["status"] == "draft"
        and crew["configuration_plan"]["first_run"].get("status") != "passed"
    )
    assert any("Google" in item for item in crew["configuration_plan"]["missing"])
    runtime.router.chat.assert_not_called()
    runtime.google_pool.read.assert_not_called()


def test_model_alone_cannot_pass_a_missing_google_read(action_env):
    client, auth, _, _ = action_env
    runtime, source_id = setup_reader(action_env, body=False)
    runtime.google_pool = None
    result = client.post(
        f"/api/v1/crew/{source_id}/access",
        headers=auth,
        json={"capabilities": ["gmail:read"], "notifications": True},
    )
    check = result.json()["configuration_plan"]["first_run"]
    assert check["status"] == "failed" and "allow reading Gmail" in check["message"]
    assert result.json()["status"] == "draft"


def test_naming_guide_adds_distinct_identity_without_rewriting_user_names_or_permissions():
    prompt = coordinator_prompt(PlannerContext(local_time="2026-11-10T19:30:00+05:30"))
    for text in [
        "Meridian Brief",
        "Signal Curator",
        "Honor any name",
        "Preserve existing names",
        "never change canonical keys/IDs",
    ]:
        assert text in prompt
    value = proposal(body=True)
    assert value.name == "Meridian Brief" and value.composition.agents[0].name == "Signal Curator"
    value.name = "My simple name"
    assert CrewDraft.model_validate(value.model_dump()).name == "My simple name"


def test_proposal_defaults_never_add_writes_or_copy_private_access_to_multiple_roles():
    raw = proposal().model_dump()
    raw["requested_google_capabilities"] += ["gmail:modify", "gmail:send"]
    member = {**raw["composition"]["agents"][0], "capabilities": []}
    raw["composition"]["agents"] = [member]
    single = CrewDraft.model_validate(raw)
    assert set(single.composition.agents[0].capabilities) == {
        "google:gmail:read",
        "google:gmail:body",
        "notifications:show",
    }
    raw["composition"]["agents"] = [member, {**member, "key": "reviewer", "name": "Prism Verifier"}]
    multiple = CrewDraft.model_validate(raw)
    assert all(not member.capabilities for member in multiple.composition.agents)


def test_display_name_refinement_preserves_member_ids_and_access(tmp_path):
    from mavo.crew_composition import set_composition
    from tests.test_crew_v2 import composition, seed, spec
    from tests.test_multi_agent import make_session

    factory = make_session(tmp_path)
    with factory() as session:
        crew = seed(session)
        set_composition(session, crew, composition({**spec("reader"), "name": "Inbox Reader"}))
        member_id, worker_id = crew.agents[0].id, crew.agents[0].worker_id
        set_composition(session, crew, composition({**spec("reader"), "name": "Signal Curator"}))
        assert crew.agents[0].id == member_id and crew.agents[0].worker_id == worker_id
        assert crew.agents[0].key == "reader" and crew.agents[0].name == "Signal Curator"
        assert (
            crew.permissions == ["model:invoke"] and not crew.agents[0].definition["capabilities"]
        )
    factory.kw["bind"].dispose()


def test_a_question_about_a_sample_email_is_not_a_setup_question(action_env, sample_mail):
    """An inbox summary's draft asked the person "For the Q4 Strategy Sync with Alex, would you
    prefer the earlier slot?": a question about Mavo's own made-up test email."""
    client, auth, _, _ = action_env
    runtime, source_id = setup_reader(action_env)
    sample_mail(
        [
            {
                "id": "s1",
                "subject": "Q4 Strategy Sync",
                "sender": "alex@example.com",
                "text": "Earlier or later slot?",
            }
        ]
    )
    runtime.router.chat = AsyncMock(
        return_value=json.dumps(
            {
                "state": "waiting_for_input",
                "summary": "Need a choice",
                "question": "Would you prefer the earlier slot or the later one?",
                "actions": [],
            }
        )
    )
    result = client.post(
        f"/api/v1/crew/{source_id}/access",
        headers=auth,
        json={"capabilities": ["gmail:read", "gmail:body"], "notifications": True},
    )
    assert result.status_code == 200, result.text
    crew = result.json()
    plan = crew["configuration_plan"]
    assert not plan.get("questions")  # nothing asked of the person
    assert plan["first_run"]["status"] == "passed", plan["first_run"]
    assert plan["first_run"]["asked_on_sample"] is True
    assert "earlier slot" in plan["first_run"]["preview"]
