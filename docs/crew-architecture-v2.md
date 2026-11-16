# Crew architecture V2

Mavo Core coordinates a persistent Crew containing one or more logical agents. Agents are
identities, not Python processes, Ollama servers, or separate application instances.

## What changed

- `CrewInstance` remains the public Crew identity, permission boundary, trigger owner and
  schedule. Existing IDs, routes, checkpoints, observations and running executions are retained.
- `CrewAgent` owns membership, role, instructions, optional route, operation restrictions,
  dependencies, completion criteria and per-cycle call budget. Retiring a member preserves its
  ID and history. Reintroducing the same role key restores that identity.
- The existing `TaskRun` is the cycle/assignment authority. Its dependency levels execute
  independent steps concurrently. Existing `CrewRun` leases execute bounded worker sessions.
  There is no second job queue or competing orchestration engine.
- A Core task addressed to a whole composed Crew expands into its member assignments plus a
  deterministic fan-in step in that same TaskRun. The original step ID remains its external
  dependency/result boundary. Public plans still have at most eight top-level assignments;
  internal expansion is bounded to 72 steps (eight eight-member Crew plus eight fan-ins), without
  increasing the approved model-call budget. No manager model call is added for aggregation.
- Single-worker legacy Crew retain their original worker. Explicit compositions use internal
  hidden worker adapters for private observations and existing tool execution. They are never
  listed as extra persistent Crew and never run their own schedules.
- Reminders and device monitors retain their deterministic, one-worker, zero-manager-call path.
  One logical model agent likewise requires no manager inference.

## Creating a Crew

Describe the responsibility in normal chat, for example:

> Help me find full-stack and AI internships, check which ones suit me, and prepare application drafts.

Core is instructed to reuse suitable Crew or propose the smallest useful composition. Discovery,
eligibility matching and preparation may justify three roles; a storage check does not.
Material missing profile details should be asked for, rather than inventing eligibility. Inbox
access is not assumed. Disposable software builds continue to use the existing temporary
`team.build` runtime instead of automatically becoming permanent Crew.

The proposal contains typed `composition.agents` with role keys, instructions, capability and
operation restrictions, dependencies, routes, call budgets and success criteria. Unsupported
tools, unknown dependency keys, cycles, duplicate roles and over-budget plans are rejected.
The existing planner repair/provider boundary handles malformed proposals. An optional planning
route is selected in Settings; no new expensive provider is silently selected.

Review the role preview and the existing resource/action controls before adding the Crew.
Requests are not grants. Creating roles never connects an account or enables a tool.

## Runtime and limits

`composition.limits` contains configurable upper bounds, not a target team size:

- `max_agents`: 1–8, within the existing TaskPlan structural ceiling.
- `concurrency`: 1–4 for independent steps in this Crew cycle.
- `model_calls` and each member's `max_model_calls` are kept as stored history only; a value
  out of range is clamped, never a reason to refuse a design. AI calls are capped by the fixed
  ceilings in `backend/src/mavo/limits.py`: `RUN_MAX` per run or agent turn, `CYCLE_MAX` per
  task or crew cycle, and `DAY_MAX` per crew per day (higher only when a frequent schedule asks
  for more). A run that reaches `RUN_MAX` finishes with what it found.
- `timeout_seconds`: 30–3600 per assignment.
- `review_rounds`: 0–3. Reviewers declare `reviews` as a dependency role key.
- `delegation_depth`: 0–3. Zero forbids temporary helpers. The current helper is text-only,
  has no tools and cannot delegate again, so its actual depth cannot exceed one.
- Optional `spending_limit_usd`: a cloud cycle is refused when this is set because verified
  provider pricing/usage accounting is not yet available. Local Ollama can run under it.
  Call limits are not falsely presented as exact token or dollar accounting.

Calls are charged before dispatch, transactionally against member, cycle and Crew-day budgets.
All model routes also share `MAVO_MAX_CONCURRENT_MODEL_CALLS` (default 4, range 1–16).
`MAVO_MAX_MODEL_OUTPUT_TOKENS` caps output (default 16000 for long build replies; ordinary
replies remain at most 4096). This applies to Ollama's `num_predict` as well as cloud request
limits. Work waiting for a model slot rechecks task state and permissions before sending data.
Concurrency does not multiply the approved call budget. An optional `lead` identifies an ordinary
member of the validated graph; it is not an extra free model process. Persistent V2 execution is
currently a bounded graph, not unrestricted self-rewriting dynamic team topology.

Read-only reviewer fixes reuse the existing worker, task checkpoints and pipeline verdict
semantics (`VERDICT: pass` / `VERDICT: fix`). Revision messages are persisted. Consequential
actions are not automatically replayed to satisfy a reviewer; those revisions require the user
to inspect the prior outcomes. Retry ceilings and cumulative budgets still apply.

## Communication, chat and the map

Agents use canonical `agent:<UUID>` message actors through the existing durable outbox.
Fan-out creates one delivery per recipient; claims, leases and acknowledgements survive restart.
Inbox claims are task-correlated: one assignment cannot consume another task's messages.
Existing Crew-to-Crew pipeline handoffs bridge into authorized root members and verified completed
cycle results route back through the existing pipeline machinery. Manual event-driven Crew remain
idle until an actual handoff, user request or explicit check, rather than waking an LLM on a timer.
Assignments and revision outputs have idempotent, iteration-specific keys. Messages contain
bounded task artifacts, not another agent's entire private context.

General chat supports `@Crew` and `@Crew/Agent`, keyboard selection and multiple mentions.
The actual routing fields are `mentioned_crew_ids` and `mentioned_agent_ids`; display names are
not the authority. A member of a stopped Crew can answer an explicit one-time assignment without
starting its recurring schedule. Collaborations use task dependencies, not blind broadcasting.

The main map keeps each Crew collapsed. Expand its member count to inspect its internal graph,
then select a member for its role, connection, task, evidence and restrictions. Selecting an edge
opens actual message delivery details. A handoff lights only for a recorded message; static
dependencies do not pretend to be live traffic. Reduced motion is respected. Closed role previews
do not poll message history. Crew cycles are not duplicated as unrelated temporary teams.

Important questions and typed missing-capability requests appear in general chat with the parent
Crew and member identity. Blocked cycle updates expose existing task details/recovery controls.
Successful quiet checks do not automatically produce desktop popups from every specialist.

## Access and private state

Effective access is the intersection of account consent, parent Crew grants, member restrictions,
the current assignment and existing action policies/approvals. Exact allowed operations are
restricted separately even when several provider actions share one OAuth scope. Internal workers
read the parent action policy, never a broader policy copied once at creation.

An inline action grant for an already-blocked cycle preserves that cycle's generation and completed
checkpoints. Changing action scope during active execution stops the affected V2 Crew; it does not
silently start another cycle or replay earlier writes under the new scope.

Parent generations, assignment state and grants are checked again at claim, model and action
boundaries. Stop cancels the Crew cycle and fences member workers. A task assignment cannot widen
access by handing off to another agent. Revoked source-data permissions also prevent message
consumption. Sensitive specialist output stays within its explicitly correlated Crew cycle;
cross-Crew sharing still requires a separate authorized purpose.

Credentials remain under existing secure storage; none enter agent specifications or messages.
Agent observations/checkpoints are private worker state. Only explicit task outputs cross the
dependency graph. Tool results and browser content remain untrusted data.

The current inline grant duration is **until revoked for the Crew**, not a fabricated expiring
task-only grant. Enabling terminal capability does not bypass existing command/script approval
and sandbox policies. Unsupported capabilities remain unsupported.

## Configuration changes

Use the existing versioned configuration proposals:

- `changes.composition`: persistent role/workflow changes, reviewed before application.
- `changes.run_agent_keys`: choose existing roles and their dependencies for one queued cycle.
- `changes.responsibilities`: instructions for a step, current/next run or persistent Crew,
  according to the explicitly selected scope.
- `changes.review_interval_minutes`: changes the actual recurring schedule and next execution.
- `changes.route_id`: a reviewed route selection; no mid-call provider switch.

Persistent edits defer while a Crew cycle is in flight. Schedule edits at the safe boundary do
not start an extra immediate cycle. Current-run instructions take effect at subsequent model
boundaries. Ambiguous concurrent current-step overrides require an explicit worker run target.
Temporary edits expire in 24 hours and do not replace persistent configuration. History and
rollback use the existing configuration edit log. A rollback from the first V2 composition to an
unstructured legacy configuration requires a new reviewed proposal rather than guessing old roles.

## Verification means evidence, not confidence

The browser setup trial now says **Page accessible**, not **Worked**. It explicitly marks the
Crew objective unverified.

An agent's `verification` can be:

- `deliverable`: a task result was produced; factual/objective success is not automatically proved.
- `source_evidence`: exact quotations and URLs must match actual browser reads, authorized feed
  snapshots or successful public lookup receipts from that assignment. Another model's prose
  is not evidence. See [first-run verification](first-run-verification.md) for activation checks.
- `listings`: requires listing evidence, or a supported explicit zero-results observation from
  a searched page. A generic homepage quote does not count as a listing.

Listing evidence has stable observed-ID/content fingerprints. Seen IDs are retained in the
discovery worker; notification IDs are committed with the durable Crew alert, not merely when a
source was read. Repeated/zero-result checks stay quiet, and a failed downstream step does not
mark a job as already notified. This is bounded to the latest 1000 identifiers; provider-specific
stable job IDs remain preferable to content fingerprints for changing portals.

Evidence distinguishes operation success, step evidence and objective verification. Eligibility
and overall suitability still require reviewing actual profile/source criteria; source access
does not prove them. Missing evidence blocks the dependent work instead of claiming discovery
succeeded. Existing development build/test verification and reminder-delivery state remain
authoritative for their own deterministic workflows.

## Draft setup versus live configuration

Setup answers refine the current conversation's saved draft, preserving its Crew and member
IDs. Drafts are not executable configuration targets and are excluded from the available-Crew
catalog. If a planner returns a persistent configuration patch for that same draft, the backend
validates its supported setup fields against the complete saved proposal and routes it through
normal draft review. It does not activate the Crew or grant requested capabilities. Temporary
or unsupported changes require clarification; an unrelated draft remains an invalid target.
Completed answers reveal connection/access review, not implicit authorization. Regression tests
are in `backend/tests/test_draft_refinement.py` and use fixture providers, not live accounts.

## Migration and restart

Migration `0020` adds `crew_agents`, task-to-Crew correlation and persisted agent mention IDs.
It deterministically backfills one agent for each existing public Crew, pointing at its existing
worker. It creates no jobs and does not change active leases, IDs, schedules or checkpoints.
SQLite columns are added without rebuilding execution tables. Downgrade is refused once V2
membership/executions would be lost. Back up the data directory before distributing upgrades.

Restart cleanup excludes registered persistent member adapters. Completed task outputs and
pending requests remain durable. Interrupted tool work is held for review/verified checkpoints,
not blindly replayed. Local work cannot run while the computer is off or Mavo is suspended.

## Tests and remaining release work

Implementation map for this change:

- Domain/migration: `database.py`, `schemas.py`, `crew_composition.py`,
  `backend/migrations/versions/0020_crew_agents.py`, and migration compatibility detection.
- Planning/runtime: `coordinator_contract.py`, `crew_planner.py`, `crew_engine.py`,
  `task_plan.py`, `tasks.py`, `crew_workers.py`, `configuration.py`, `orchestration.py`,
  `action_rules.py`, `google_actions.py`, `outcomes.py` and the browser trial verdict.
- Shared model limits: `model_router.py`, `ollama.py`, `config.py`, `main.py`, `.env.example`.
- UI: `CrewMembers.tsx`, `crew-members.css`, `AgentGraph.tsx`, `CrewGraph.tsx`,
  `GraphPanels.tsx`, `CrewSetupCard.tsx`, `CrewMentions.tsx`, `AgentTraffic.tsx`,
  `ConversationActivity.tsx`, `ChatPage.tsx`, `Home.tsx`, API/chat/mention/task projections
  and shared TypeScript types.
- Regressions: `test_crew_v2.py`, provider/Ollama/migration assertions,
  `CrewMembers.test.tsx` and `scripts/check-orchestration.py`.
- Documentation: README, architecture/development/security notes, roadmap and ADR 0033.

Pre-existing uncommitted onboarding, connector and orchestration work was preserved; this list
does not imply every file in the working tree was introduced by V2.

`backend/tests/test_crew_v2.py` uses fixture providers, not paid/live integrations. It exercises
composition validation, legacy migration, call ceilings, minimal three-role execution, parallel
fan-in, review/fix, denied access/resumption, private-context retention, scoped edits, direct member
addressing, cancellation and deletion. Existing task, pipeline, development-team and permission
tests continue to exercise those reused runtimes.

`src/components/CrewMembers.test.tsx` covers ID-backed dependencies/mentions, collapsed previews,
readable diffs and avoiding duplicate task teams. `scripts/check-orchestration.py` verifies real
browser keyboard interactions, member expansion/inspection, pending replies and permission cards
using isolated HTTP fixtures. Run it with Vite on port 1432.

Before claiming a fully production-verified internship workflow, still validate real authorized
job sources, login/session expiry, stable listing IDs/deduplication, eligibility inputs and the
selected model's plan quality. No generic browser extractor guarantees those semantics for every
portal. Exact per-provider token/currency accounting, expiring task-only grants, arbitrary dynamic
lead re-planning and encrypted-at-rest noncredential working context are not implemented here.
