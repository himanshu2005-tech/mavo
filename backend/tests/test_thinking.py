"""Every model step of a task thinks in numbered steps first; the person sees them, the answer
stays exactly as it was."""

from mavo.thinking import THINK, split_thinking
from tests.test_tasks import plan, setup, start, step, wait


def test_the_thinking_is_taken_out_of_the_answer_as_structured_steps():
    reply = (
        "<thinking>\n1. Goal: a todo list that keeps items.\n2. **Approach**: one index.html,\n"
        "   localStorage for the items.\n3. Check: empty input is refused.\n</thinking>\n\n"
        "```file:index.html\n<html></html>\n```"
    )
    answer, steps = split_thinking(reply)
    assert answer == "```file:index.html\n<html></html>\n```"
    assert steps == [
        {"label": "Goal", "text": "a todo list that keeps items."},
        {"label": "Approach", "text": "one index.html,"},
        {"label": "Check", "text": "empty input is refused."},
    ]
    # A running monologue is not a structured step: only labelled lines count, each kept short.
    rambling = (
        "<steps>\n1. Goal: review the tracker.\nIs there an issue with total spent? No. Need "
        "perhaps inspect sortable.\n2. Another bug: " + "x " * 200 + "\n</steps>\nVERDICT: pass"
    )
    answer, steps = split_thinking(rambling)
    assert answer == "VERDICT: pass" and [s["label"] for s in steps] == ["Goal", "Another bug"]
    assert len(steps[1]["text"]) <= 201 and steps[1]["text"].endswith("…")
    assert split_thinking("Just the answer.") == ("Just the answer.", [])
    # Opened and never closed: what follows is still the answer, never lost.
    assert split_thinking("<thinking>\nVERDICT: pass") == ("VERDICT: pass", [])
    assert "never put passwords" in THINK
    # A real reply repeated the instruction first, empty "<steps></steps>" and all.
    echoed = (
        "START with your working steps, written out, inside <steps></steps>: 3 to 6 lines.\n\n"
        "<steps>\n1. Goal: review the calculator.\n2. Check: buttons and maths.\n</steps>\n"
        "1. [minor] Letters show $0.00 — fix: add a message.\nVERDICT: pass"
    )
    answer, steps = split_thinking(echoed)
    assert answer == "1. [minor] Letters show $0.00 — fix: add a message.\nVERDICT: pass"
    assert [s["label"] for s in steps] == ["Goal", "Check"]


def test_a_helper_thinks_first_and_its_steps_show_on_the_task(client, auth):
    runtime = setup(client, auth)
    systems = []

    async def chat(provider, model, messages, system, credential_id=None):
        systems.append(system)
        return (
            "<thinking>\n1. Goal: a short summary.\n2. Check: under 50 "
            "words.\n</thinking>\nThe summary."
        )

    runtime.router.chat = chat
    done = wait(client, auth, start(client, auth, plan(step("write", role="writer"))))
    assert done["status"] == "done" and done["result"] == "The summary."
    assert "<steps>" in systems[0]
    assert done["steps"][0]["thinking"] == [
        {"label": "Goal", "text": "a short summary."},
        {"label": "Check", "text": "under 50 words."},
    ]
