"""A thinking model's reasoning is never the answer, and a stray tag never reaches the person."""

import json

from mavo.crew_workers import parse_checkpoint
from mavo.model_router import ModelRouter
from mavo.thinking import without_thinking


def test_reasoning_blocks_and_stray_tags_are_removed_but_the_answer_is_kept():
    assert without_thinking("<think>weigh it</think>\nThe plan.") == "The plan."
    # Mavo's own <thinking>/<steps> blocks are left for split_thinking: the task card shows them.
    assert without_thinking("<thinking>1. Goal: plan</thinking>Answer").startswith("<thinking>")
    assert without_thinking("Tonight (Nov 12):\n</think>]") == "Tonight (Nov 12):"
    assert without_thinking("No tags here.") == "No tags here."


def test_every_provider_reply_is_cleaned_and_a_json_answer_stays_valid():
    """ "Plan today from all my data" on qwen3.7-flash ended its summary with "</think>]"."""
    raw = json.dumps(
        {
            "state": "wait_for_next_review",
            "summary": "Priority 1: Register.\n**Tonight (Nov 12):\n</think>]",
        }
    )
    text = ModelRouter._require_text(raw, "AIRouter")
    checkpoint = parse_checkpoint(text)
    assert "think" not in checkpoint.summary and checkpoint.summary.startswith("Priority 1")
