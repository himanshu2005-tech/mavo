# Recipes: plan once, replay every run

A recurring agent used to think through its whole job on every run: which tools, in what order.
That cost model calls each run, could differ from run to run, and kept hitting call limits.
A **recipe** is the job planned **once** as a few tagged steps, checked, then **replayed**:
reads, conditions and actions run as plain code; a model is called only for think steps.

Code: `backend/src/mavo/recipes.py` (catalogue, planner, checks, replay hooks),
`task_plan.py` (`read` and `act` steps, `when`), `tasks.py` (running them),
`crew_composition.dispatch` (a scheduled run uses the recipe). Tests: `tests/test_recipes.py`.

## Tags come from connections, not a fixed list

`available_tags(runtime, crew)` builds the catalogue each time it plans:

| Kind | What appears | When |
|---|---|---|
| READ | `gmail`, `calendar`, `tasks`, `drive`, `contacts`, `youtube` | Google connected with that read access (and the agent was granted it) |
| READ | `feeds:<id>` | that public feed turned on in Resources |
| READ | `files`, `memory` | always (the person's own Knowledge) |
| ACT | Google add / edit / delete (`calendar.create`, `tasks.delete`, `gmail.send`…) | only with the write access the person gave Google, and the agent's grant |
| ACT | GitHub | GitHub connected |
| ACT | terminal, files, notes, projects, windows, browser clicking and typing | only when turned on in Actions |
| THINK | `summarise`, `classify`, `extract`, `write`, `decide` | always (one model call each) |

The planner sees each ACT tag with its real arguments (from the action's own model), so
`terminal.run` is planned as `{folder, shell}` in PowerShell, not `df -h`.

## Steps

```json
{"id": "mail",  "kind": "read",   "source": "gmail", "new_only": true}
{"id": "find",  "kind": "helper", "role": "extract", "uses": ["mail"],
 "instructions": "Reply with ONLY a JSON array of events, or []"}
{"id": "add",   "kind": "act",    "operation": "calendar.create", "args_from": "find"}
{"id": "check", "kind": "agent",  "actions": ["browser.search", "browser.open", "browser.read"]}
```

- **read**: one source, no model. `new_only` passes only items the last *successful* run had
  not seen (their ids are kept when a run finishes).
- **think** (`helper` with a role): one model call. A step that feeds an action replies with JSON.
- **act**: one action tag, no model. Arguments are fixed (`args`, may use `{{step}}` /
  `{{step.field}}`) or come from a think step's JSON (`args_from`, one action per object, max 10).
  Every action goes through the person's permissions exactly as before: allowed, asked first
  ("Waiting for your OK"), or refused.
- **agent**: the escape hatch for open-ended work (an unfamiliar website), bounded by calls.
- **when** on any step: `{"step": id, "is": "empty" | "not_empty"}` or `{"step": id, "has": word}`.
  A step whose condition fails, or that reads a skipped step, is skipped on purpose. If the final
  step is skipped the run is done with "Nothing new." (quiet, no model call, no alert).

## Planning (once) and the checks

`ensure_recipe` plans when a recurring agent has no recipe, with the person's planning model
("Chat uses") when they chose one, else the agent's model. Up to two repairs. Checks
(`check`) refuse: a tag not in the catalogue, an act step with no arguments, fixed arguments
the real action rejects, `args_from` that is not a think step, a condition on a missing step.

Put right in code (`tighten`), whatever the model wrote:
- steps after a `new_only` read wait for something new (an empty run costs nothing);
- an action never reads a prose step: an `extract` step is inserted to give it JSON, and the
  prose stays the person's result;
- an action of a kind the job never mentions is dropped (no unasked notes, mail or tasks);
- a browsing agent can open and read pages, and clicks or types only when the job asks it to
  apply, fill in, book or submit.

## Replay, result, and planning again

Each scheduled run of an eligible agent replays its recipe as a task run. The result is the
final step's text, then a line for each action (`Done: Create event (Team sync)`).
After **2 failed runs in a row** the recipe is dropped and planned again at the next run.
A planning failure leaves the agent on its old way of working and is retried in six hours.

Not recipes: daily reminders and device monitors (already plain code), stages in a line of
agents, hidden workers and setup tests. The agent panel shows the recipe as one line of tags
("Read Gmail → Extract → Create event") with **Plan again**.

Setting: `recipes` (`MAVO_RECIPES`, on by default). Tests that cover the agent-per-run path
turn it off.
