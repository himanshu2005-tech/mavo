"""Schema and gateway regressions: no live credits, calendar writes or user Crew."""

import json
import sqlite3

import pytest
from pydantic import ValidationError

from mavo.coordinator_contract import parse_decision
from mavo.publisher_gateway import validation_issues
from tests.test_publisher_gateway import payload, serving


def draft(**changes):
    return {
        "template_id": "schedule",
        "name": "Daily Planner",
        "responsibilities": "Read my calendar and tasks and suggest a day plan; gym 7–8 AM.",
        "trigger_type": "daily",
        "schedule_time": "08:00",
        "requested_google_capabilities": ["calendar:read", "tasks:read"],
        **changes,
    }


def test_tool_using_schedule_crew_uses_the_existing_daily_ai_review_path():
    result = parse_decision(json.dumps({"reply": "Review this draft", "crew_proposal": draft()}))
    crew = result.crew_proposal
    assert crew.trigger_type == "interval" and crew.review_interval_minutes == 1440
    assert crew.schedule_time == "08:00"
    assert crew.requested_google_capabilities == ["calendar:read", "tasks:read"]
    assert crew.requested_actions == []  # suggesting a plan never grants calendar writes


def test_plain_reminders_stay_deterministic_and_multiple_ai_times_are_not_discarded():
    plain = parse_decision(
        json.dumps({"reply": "Review", "crew_proposal": draft(requested_google_capabilities=[])})
    )
    assert plain.crew_proposal.trigger_type == "daily"
    with pytest.raises(ValidationError, match="Multiple daily AI review times"):
        parse_decision(
            json.dumps(
                {
                    "reply": "Review",
                    "crew_proposal": draft(
                        daily_triggers=[
                            {"time": "08:00", "message": "Plan"},
                            {"time": "18:00", "message": "Review"},
                        ]
                    ),
                }
            )
        )


def test_gateway_accepts_calendar_planning_without_an_unnecessary_paid_repair(tmp_path):
    client, calls = serving(
        tmp_path, [json.dumps({"reply": "Review this draft", "crew_proposal": draft()})]
    )
    with client:
        response = client.post(
            "/v1/core/turn", headers={"Authorization": "Bearer account-a"}, json=payload()
        )
    assert response.status_code == 200
    assert response.json()["decision"]["crew_proposal"]["trigger_type"] == "interval"
    assert len(calls) == 1  # mocked provider request, not a billed call


def test_failed_validation_records_only_allowlisted_diagnostics(tmp_path):
    raw = json.dumps(
        {
            "reply": "PRIVATE_CONTENT_NEVER_STORE",
            "crew_proposal": draft(
                daily_triggers=[
                    {"time": "08:00", "message": "Plan"},
                    {"time": "18:00", "message": "Review"},
                ]
            ),
        }
    )
    client, calls = serving(tmp_path, [raw])
    with client:
        response = client.post(
            "/v1/core/turn", headers={"Authorization": "Bearer account-a"}, json=payload()
        )
    assert response.status_code == 502 and len(calls) == 3  # two corrections, then refused
    with sqlite3.connect(tmp_path / "gateway.db") as db:
        saved = db.execute("SELECT response FROM turns").fetchone()[0]
    assert "PRIVATE_CONTENT_NEVER_STORE" not in saved
    assert (
        json.loads(saved)["validation_issues"][0]["reason"]
        == "Multiple daily AI review times are not supported; use one review time"
    )
    assert validation_issues(ValueError("PRIVATE_EXCEPTION")) == []
