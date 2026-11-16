# ruff: noqa: F811 - `planning` is a fixture, imported and then named as a parameter
"""Every step says what it is doing in the person's words, and the last step writes the answer
in Mavo's LaTeX: a quote agent showed "Final Output", "Find Prompts" and JSON between helpers."""

from mavo.recipes import RECIPE_PROMPT
from mavo.task_plan import TaskStep, run_text_of
from mavo.workflow_write import WORKFLOW_PROMPT
from tests.test_planner import planning  # noqa: F401


def test_a_step_keeps_the_words_its_planner_gave():
    step = TaskStep(
        id="s3",
        kind="helper",
        title="Select Message",
        run_text="Picking today's quote",
        instructions="Pick one.",
    )
    assert run_text_of(step.model_dump()) == "Picking today's quote"


def test_without_them_it_says_what_the_step_is_in_plain_words():
    assert run_text_of({"kind": "read", "source": "gmail"}) == "Reading your email"
    assert run_text_of({"kind": "read", "source": "memory"}) == "Remembering what you told Mavo"
    assert run_text_of({"kind": "act", "operation": "gmail.send"}) == "Send & reply"
    assert run_text_of({"kind": "skill", "skill": "web.read"}) == "Searching the web"
    assert run_text_of({"kind": "helper", "title": "Summarise"}) == "Summarise"


def test_every_planner_asks_for_run_text_and_small_plans():
    for prompt in (RECIPE_PROMPT, WORKFLOW_PROMPT):
        assert '"run_text"' in prompt and "Never a step name" in prompt
    assert "ONE write step, final" in RECIPE_PROMPT


def test_the_last_step_writes_what_the_person_reads_in_latex(planning):
    """The final helper is told its answer is read as it is, in Mavo's LaTeX; a helper whose
    output another step reads is not."""
    import asyncio

    from mavo.task_plan import TaskPlan

    client, auth, runtime, _ = planning
    client.post(
        "/api/v1/routes", headers=auth, json={"name": "A", "provider": "ollama", "model": "m"}
    ).raise_for_status()
    seen = []

    async def chat(provider, model, messages, system=None, credential_id=None, **kw):
        seen.append(system or "")
        return "Keep going."

    runtime.router.chat = chat
    runner = runtime.task_runner
    # Only the prompt matters here: no run to charge or record on.
    runner.spend = lambda *a, **k: None
    runner.change = lambda *a, **k: None
    plan = TaskPlan.model_validate(
        {
            "title": "Quote",
            "deliver": "A quote",
            "final": "write",
            "steps": [
                {"id": "pick", "kind": "helper", "title": "Pick", "instructions": "Pick a theme."},
                {
                    "id": "write",
                    "kind": "helper",
                    "title": "Write",
                    "instructions": "Write it.",
                    "uses": ["pick"],
                },
            ],
        }
    )
    for step in plan.steps:
        asyncio.run(runner.helper("r", plan, step, {}))
    assert "YOUR ANSWER IS WHAT THE PERSON READS" not in seen[0]
    assert "YOUR ANSWER IS WHAT THE PERSON READS" in seen[1]
    assert "styled report" in seen[1]  # Mavo's LaTeX style


def test_a_step_named_by_its_id_reads_as_plain_words():
    assert run_text_of({"id": "s2", "kind": "helper", "title": "s2"}) == "Thinking it through"
    assert run_text_of({"id": "x", "kind": "agent", "title": "Step 3"}) == "Working on it"


def test_a_read_says_what_it_really_reads():
    step = {"kind": "read", "source": "memory", "run_text": "Checking your current tasks"}
    assert run_text_of(step) == "Remembering what you told Mavo"


def test_a_last_step_that_answers_with_data_gives_its_message():
    from mavo.tasks import prose_of

    assert prose_of('{"text": "Keep going.", "notify": true}') == "Keep going."
    assert prose_of('```json\n{"message": "Hi"}\n```') == "Hi"
    assert prose_of("Just a sentence.") == "Just a sentence."
    assert prose_of('{"count": 3}') == '{"count": 3}'  # nothing to say: left as it was
