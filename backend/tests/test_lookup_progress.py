from types import SimpleNamespace

from mavo.lookup_progress import current_receipts, lookup_key, scope_for


def test_repeated_lookups_share_a_key_without_rewriting_the_actual_request():
    assert lookup_key(
        {"source": "geocode", "query": " Chennai  Airport ", "to": None}
    ) == lookup_key({"source": "geocode", "query": "chennai airport"})


def test_receipts_do_not_make_a_new_scheduled_review_use_old_data():
    crew = SimpleNamespace(
        configuration_plan={},
        runtime_generation=2,
        observation={
            "review_started_at": "old",
            "lookup_progress": {"scope": "2:old", "receipts": [1]},
        },
    )
    assert current_receipts(crew)[1] == [1]
    assert current_receipts(crew, {**crew.observation, "review_started_at": "new"})[1] == []
    crew.configuration_plan = {"task_run_id": "task"}
    assert scope_for(crew) == "task"
