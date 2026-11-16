"""The example collection is concrete, bounded and never an implicit permission grant."""

import json
from pathlib import Path

from mavo.crew_templates import CREW_TEMPLATES_BY_ID
from mavo.schemas import CrewCreateRequest, CrewUpdateRequest


def catalog():
    path = Path(__file__).resolve().parents[2] / "docs/examples/practical-crew-catalog.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_catalog_has_100_distinct_scoped_examples_across_known_templates():
    entries = catalog()["agents"]
    assert len(entries) == 100
    assert len({entry["name"].casefold() for entry in entries}) == 100
    assert len({entry["goal"].casefold() for entry in entries}) == 100
    assert {entry["template"] for entry in entries} == set(CREW_TEMPLATES_BY_ID)
    for entry in entries:
        assert set(entry) <= {
            "name",
            "template",
            "goal",
            "needs",
            "steps",
            "output",
            "daily",
            "interval",
        }
        for field in ("goal", "needs", "steps", "output"):
            assert len(entry[field]) >= (10 if field == "output" else 30)
        assert not (entry.get("daily") and entry.get("interval"))


def test_example_timings_and_create_update_payloads_validate_without_granting_access():
    entries = catalog()["agents"]
    assert sum(bool(entry.get("daily")) for entry in entries) == 3
    assert any(len(entry.get("daily", [])) > 1 for entry in entries)
    for entry in entries:
        daily = [{"time": time, "message": message} for time, message in entry.get("daily", [])]
        if daily:
            assert entry["template"] == "schedule"
        payload = CrewCreateRequest(
            template_id=entry["template"],
            name=f"Example · {entry['name']}",
            responsibilities="\n".join(
                entry[field] for field in ("goal", "needs", "steps", "output")
            ),
            route_id="catalog-validation-only",
            trigger_type="daily" if daily else "interval" if entry.get("interval") else "manual",
            daily_triggers=daily,
            review_interval_minutes=entry.get("interval", 30),
            max_model_calls=4,
            daily_call_limit=8,
        )
        assert not payload.allow_device_metrics
        assert not payload.allow_notifications
        assert not payload.allow_weather
        assert not payload.google_resources
        assert not payload.google_capabilities
        assert not payload.connector_capabilities
        update = payload.model_dump(exclude={"template_id"})
        CrewUpdateRequest.model_validate(update)


def test_example_policy_does_not_present_requirements_as_authorization():
    policy = catalog()
    assert "never an access grant" in policy["permission_policy"]
    assert "Stopped initially" in policy["activation"]
    assert "ask one concise setup question and stop" in policy["execution_policy"]
    assert "Never fabricate" in policy["execution_policy"]


def test_connection_suggestions_reference_real_examples_and_are_display_only():
    path = Path(__file__).resolve().parents[2] / "docs/examples/connection-requirements.json"
    suggestions = json.loads(path.read_text(encoding="utf-8"))
    names = {entry["name"] for entry in catalog()["agents"]}
    assert "Display only" in suggestions["description"]
    for entries in [*suggestions["resources"].values(), *suggestions["actions"].values()]:
        assert set(entries) <= names
