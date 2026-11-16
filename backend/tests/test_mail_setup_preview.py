"""Gmail first-run proposals stay private and non-writing until reviewed activation."""

import json
from unittest.mock import AsyncMock

import pytest

from mavo.database import ActionRequest, GoogleAccount
from mavo.schemas import CoordinatorDecision, CrewDraft
from tests.test_actions import action_env  # noqa: F401


@pytest.mark.parametrize(
    "operation", ["gmail.mark_read", "gmail.archive", "gmail.trash", "gmail.send", "gmail.draft"]
)
def test_mail_setup_previews_then_requires_review_before_activation(
    action_env, sample_mail, operation
):
    client, auth, engine, state = action_env
    runtime = client.app.state.crew_runtime
    cap = "gmail:send" if operation == "gmail.send" else "gmail:modify"
    route = client.post(
        "/api/v1/routes",
        headers=auth,
        json={"name": "Fixture", "provider": "ollama", "model": "fixture"},
    ).json()
    spec = CrewDraft.model_validate(
        {
            "name": "Mail helper",
            "template_id": "communication",
            "responsibilities": "Read my inbox and perform the configured mail action.",
            "trigger_type": "interval",
            "review_interval_minutes": 60,
            "route_id": route["id"],
            "max_model_calls": 8,
            "daily_call_limit": 30,
            "requested_google_capabilities": ["gmail:read", cap],
            "requested_actions": [{"operation": operation, "reason": "User-selected mail action"}],
            "composition": {
                "agents": [
                    {
                        "key": "reader",
                        "name": "Mail Reader",
                        "role": "Inbox helper",
                        "instructions": "Read current mail, then propose the configured action. "
                        "In setup do not claim a real write.",
                        "capabilities": ["google:gmail:read", "google:" + cap],
                        "actions": [operation],
                        "max_model_calls": 3,
                        "completion_criteria": "Propose the action on an observed target.",
                    }
                ],
                "limits": {"model_calls": 8},
            },
        }
    )
    with runtime.factory() as session:
        source = runtime.planner.apply_plan(
            session, CoordinatorDecision(reply="Review", crew_proposal=spec), None
        )
        source_id = source.id
        account = session.get(GoogleAccount, "google")
        account.cursors = {
            "gmail": {
                "snapshot": [{"id": "observed-mail", "subject": "Example", "labels": ["UNREAD"]}]
            }
        }
        session.commit()
    runtime.google_pool.read = AsyncMock(
        return_value={
            "items": [{"id": "observed-mail", "subject": "Example", "labels": ["UNREAD"]}]
        }
    )
    # Tested on a sample only, never the person's inbox.
    sample_mail([{"id": "observed-mail", "subject": "Example", "labels": ["UNREAD"]}])
    args = (
        {"to": "recipient@example.com", "subject": "Example", "body": "Proposed reply"}
        if operation in {"gmail.send", "gmail.draft"}
        else {"message_id": "observed-mail"}
    )
    runtime.router.chat = AsyncMock(
        side_effect=[
            json.dumps(
                {
                    "state": "continue",
                    "summary": "Proposing the configured operation.",
                    "actions": [{"operation": operation, "arguments": args}],
                }
            ),
            json.dumps(
                {
                    "state": "wait_for_next_review",
                    "summary": "Preview only: proposed mail action; nothing changed.",
                }
            ),
        ]
    )
    engine.perform = AsyncMock(
        side_effect=AssertionError("A setup test must not touch the mailbox")
    )
    result = client.post(
        f"/api/v1/crew/{source_id}/access",
        headers=auth,
        json={
            "capabilities": ["gmail:read", cap],
            "actions": {operation: {"enabled": True, "mode": "unrestricted"}},
        },
    )
    assert result.status_code == 200, result.text
    crew = result.json()
    check = crew["configuration_plan"]["first_run"]
    assert check["status"] == "passed", check
    assert crew["status"] == "draft" and not crew["is_running"]
    assert check["kind"] == "action_preview" and check["confirmed"] is False
    assert check["proposed_actions"][0]["operation"] == operation
    assert check["proposed_actions"][0]["arguments"] == args
    assert runtime.router.chat.await_count == 2
    runtime.google_pool.read.assert_not_awaited()
    assert not state["writes"]
    engine.perform.assert_not_called()
    with runtime.factory() as session:
        assert session.query(ActionRequest).count() == 0
        assert session.get(GoogleAccount, "google").cursors["gmail"]["snapshot"][0]["labels"] == [
            "UNREAD"
        ]
    activated = client.post(
        f"/api/v1/crew/{source_id}/activate-preview",
        headers=auth,
        json={
            "verification_id": check["adapter_id"],
            "generation": check["generation"],
        },
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["status"] == "active"
    assert not state["writes"]
    engine.perform.assert_not_called()
