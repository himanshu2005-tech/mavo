# First-run verification and lookup recovery

New chat/builder drafts use **Test & add Crew**. Saving access first validates the existing
schema, provider connectivity and exact grants. When those prerequisites are satisfied, a
bounded real test runs before the draft can become an active Crew. A failed test stays a draft;
typed questions return to setup. Configuration checks alone are not an execution pass.

## Execution and budgets

`first_run.py` creates private read-only adapters and uses the existing `CrewWorkerRuntime`,
composition DAG, TaskRunner, verification and provider router. It is not another agent engine.
The test uses the selected agent connections and can spend their credits. Calls are reserved
against the original draft's daily allowance; unused reservations are refunded. Simultaneous
tests of the same draft are rejected. Test traffic and desktop notifications are not published.

For each specialist, **n is measured model calls**, including repair/follow-up calls, not a model's
estimate or a count of tool operations. Its future limit becomes n + 3. The shared cycle ceiling
must accommodate the specialist limits. Neither individual nor cycle/daily approved ceilings
are silently raised. If the buffer will not fit, setup remains blocked with a budget explanation.
These are call ceilings, not guarantees about token cost or future execution length.

Tests stop after 240 seconds. Cancellation, access changes and deletion invalidate them.
On restart, interrupted tests are recorded as interrupted, unused reserved calls are refunded,
and private adapters are cleaned up without automatically repeating paid work. No schema reset
or data migration is required; `configuration_plan.first_run` holds the result and calibration.
Existing activated/imported Crew are not automatically retested or rewritten.

## Safety and coverage

- Required Google reads must be assigned to an active member and actually observed
  through the authorized resource pool during the test. Crew-level consent alone is
  not member access. Setup shows receipt-backed resource/item/body counts, not inferred
  model claims. Counts can reflect cached snapshots and bounded inputs, not a complete
  inbox review. [Single-reader defaults and restrictions](decisions/0037-single-reader-proposal-access.md).
- Read-only model work executes once and must satisfy its configured source/output checks.
- Reminder schedules are validated without firing an early notification. Actual future desktop
  delivery is **not** claimed as tested.
- Device checks read actual counters without cleaning files or invoking models.
- Every registered effectful action can be **previewed**, never executed during setup.
  The real worker reads authorized data, produces typed proposals and receives explicit
  `previewed` receipts without provider IDs. The selected account, consent, action rules and
  argument schemas are rechecked. Updating an item requires its observed ID/version and read
  permission. Previewed arguments are shown separately from conditional operations that the
  current input did not exercise. An empty inbox can pass its read check without claiming
  that mail changes were executed or fully tested.
  The UI displays exact titles, times and arguments and keeps the Crew a draft until the user
  selects **Review & enable Crew**. Confirmation is bound to configuration, route, account,
  resource access, grants and policy revisions. A change invalidates the preview. Confirmation
  is idempotent and performs no model call or provider write; the enabled Crew's subsequent
  normal run can apply changes under the saved policy. This is not proof that Google accepted
  a write. Private test rows are cleaned up, not shown as real completed or failed actions.
  An already running backend with older action/worker code refuses write previews until it
  is restarted; mixed lazily imported module versions cannot fall through to real writes.
- The canonical tool registry currently contains 51 operations: 12 read/navigation operations
  keep their existing authorized read path; the other 39 are intercepted before execution.
  This includes Gmail send/draft/archive/mark-read/trash, Calendar/Tasks deletes, GitHub writes,
  terminal commands, browser interaction, Windows controls, notes/files/project changes and
  paid helper requests. New registered tools default to non-executing preview, not read access.
  The separate legacy Computer cleanup capability is previewed without deleting temp files
  or emptying the recycle bin. Device counters can still be read.
- A preview checks typed arguments, current actor, grants, source/worker policy equality,
  provider consent and resource ceilings. Existing Google targets must be observed via
  authorized read access; Calendar/Tasks versions must match. File paths are checked without
  creating folders. Missing live browser/window targets and other execution preconditions
  remain explicitly unverified. A dry run is not proof that a command, click, write or goal
  will succeed. Script trust/approval is never conferred by previewing `project.run`.
- Supported actions are no longer categorically rejected at setup. Unconnected accounts,
  revoked access, unsupported operations, invalid arguments or missing observed targets still
  block safely. Preview-only output never contains fabricated provider IDs or command output.
  No third-party mutation, subprocess, UI input or helper inference is performed for a preview.
- The complete preview is shown in a scrollable list. Previews exceeding 128 actions or
  256 KB of arguments fail safely instead of hiding unreviewed actions. Activation binding
  includes all granted action policies, account subjects/scopes/repositories, resource access,
  paused status and device cleanup configuration, not only actions listed in the original draft.
- The older website sample button remains an optional access diagnostic, not an activation pass.
- Legacy direct developer API creation remains compatible; the new gate is enforced for setup
  drafts through Chat and the Crew builder, including older drafts when access is submitted.

One successful run is not proof of correctness for every future input, website, login expiry,
provider outage or changing page layout. Release verification still needs authorized real-account
testing and task-specific success criteria. Setup previewing does not perform supervised real
writes. The normal runtime and its existing action-level requirements remain authoritative.

## Worker and news checkpoint contracts

Worker requests include the actual checkpoint JSON schema, including typed `SourceEvidence`
fields. Evidence shape errors are handled by the existing budgeted repair, rather than surfacing
only after the worker has finished. Local Ollama requests use its schema `format`; AIRouter and
OpenRouter-compatible worker calls request JSON-object responses. File-fence project-writing
steps retain their existing protocol. Unsupported provider formatting fails visibly; it does
not silently switch to another model. Sources: [Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs)
and [AIRouter parameter forwarding](https://www.airouter.in/docs/api-reference/chat-completions).

For one source-reading draft whose role has no read/action restrictions, the user's explicit
public-feed selection is assigned to that sole reader, together with separately enabled desktop
updates. This does not infer private-account grants, write access, or permissions for other agents.
An explicitly restricted role is not broadened. The planner is also instructed to put feed
requirements on the reader, not just the parent Crew.

Public feeds are collected with bounded concurrency and independent errors. The model receives
a fair, bounded sample from each selected source instead of silently losing later sources to
input truncation. The shared cache is copied before per-Crew filtering. News instructions limit
digest/notification length, do not invent article bodies from headlines, and avoid redundant
public lookups when supplied feeds are sufficient. Unavailable or omitted sources are not proof
of an empty news feed. Failed checkpoints retain only field/type diagnostics, never raw output.

On 2026-11-09, a read-only probe of the user's 14 selected public feeds returned valid feeds from
all 14. An approved AIRouter `Main Tasks` test with synthetic headlines initially failed twice;
after the nested evidence schema was included, its separately approved one-call retest returned
a valid checkpoint with five source-matched citations. These checks did not activate, modify or
delete the saved Crew. They establish protocol compatibility for that sample, not universal
model reliability or a completed live Crew workflow.

## Lookup recovery

Canonical lookup requests are deduplicated within their assignment. Receipts are committed before
model interpretation, retained across work-step retries, and supplied back as **untrusted data**.
They are never promoted to permanent user memory. Scheduled reviews get separate scopes so old
news/weather is not mistaken for a new check. Retention is bounded to 24 receipts / roughly 64 KB.

The runtime permits at most eight lookup batches within the approved model-call budget. Exact
repeats receive saved results and a final synthesis instruction, not another external fetch.
Unproductive continuation cannot be reported as success. Failures distinguish source errors,
lookup loops/round limits and actual budget exhaustion; available partial results are retained.
Internal verification failures are failed work, not a question the person must somehow answer.

Source verification accepts matching quotations from actual browser reads, authorized connected
feed snapshots, and successful public lookup receipts from the same assignment. Revoked access
or another assignment's receipts cannot establish evidence. Source access alone still does not
prove eligibility, factual completeness or the full Crew objective.

## Tests

`test_first_run.py` exercises real worker/task code with fake models and isolated databases:
single and multiple workers, measured budgets, unsafe-action rejection, questions, revocation,
restart accounting and activation gating. `test_lookup_progress.py`, `test_source_receipts.py`
and `test_open_data.py` cover deduplication, review isolation, partial results and source evidence.
The planner-only fixture explicitly simulates first-run verification; it is not a live test.
`src/lib/firstRun.test.ts` covers the setup UI's verification labels.

`test_calendar_setup_preview.py` covers missing action access without model spending,
zero-write previews, explicit activation, stale previews and revocation during a model call.
`CrewChangePreview.test.tsx` checks the visible proposal and non-execution wording. With Vite
running, `backend\.venv\Scripts\python.exe scripts/check-crew-setup-ui.py` clicks the actual
setup component against intercepted HTTP fixtures, exercising Off actions → visible blockers →
test preview → explicit enable. It never calls the real backend, Google or a model provider.
