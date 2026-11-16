# Unified orchestration integration

## Existing lifecycle and actual gaps

Core chat enters `CrewPlanner.turn` through `/core/turn`, with per-conversation leases and
request fingerprints. The typed coordinator decision already chooses persistent Crew drafts,
delegation, links, or a one-time `TaskPlan`. Crew drafts reconcile resources/actions before
publication. Persistent work is claimed by `CrewWorkerRuntime`, checked by the existing action
engine and recorded in `CrewRun`. `pipelines.py` handles leads/members, upstream results,
bounded review/fix rounds and human approval. `TaskRunner` executes dependency levels in
parallel, invokes existing Crew or bounded temporary helpers/agents, and delegates builds to
`BuildTeam`. Those identities and execution paths remain separate.

The audit found JSON-only handoff queues and lost concurrent-update risks, in-memory task
answers, restart-as-failure, replay-from-start retries, name-based mentions, regex-derived
permission switches, and configuration edits without revision history. Task creation also
followed the coordinator transaction, leaving a crash window. General chat already renders
task cards but not persistent Crew outcomes or typed capability requests.

## Compatibility design

Keep TaskRun/CrewRun as execution authorities. Add transactional message envelopes with
per-recipient delivery, durable conversation events/questions, typed capability requests and
configuration revisions. Existing handoff JSON is a compatibility projection, not the only
queue. Persisted step outputs are checkpoints; never replay uncertain side effects silently.
Use canonical IDs in mention payloads, never resolve a selected mention from its display name.
All actual execution continues through the current permission engine and provider router.

No migration erases or rewrites existing conversations, Crew, routes or credentials. Testing
uses isolated SQLite databases and mocked model/provider transports, not real accounts.

Deleting a Crew cancels its pending conversation requests (including internal workers and
owned tasks) in the deletion transaction. Event records remain available in storage for audit.
The chat events API excludes references to missing Crew/tasks, including events left by older
deletions, and refuses replies to orphaned requests. This prevents stale recovery cards without
erasing unrelated conversation updates or requiring a database reset.

## Implemented behavior

`TaskRun` owns one-time DAG work. `CrewRun` owns bounded persistent work. `BuildTeam` retains
its nested member/stage lifecycle. `orchestration_view` projects stable task/agent IDs,
dependencies, assignments, deliverables, completion criteria, capability requirements and
call budgets onto existing plans. Persistent team leads now accept member IDs, with legacy
name compatibility. New planner/API input rejects invalid dependencies, cycles, nonexistent
Crew, unsupported tools and excessive budgets; older saved plans retain legacy normalization.

The publisher coordinator remains the default. Settings → Planning connection explicitly
selects a saved route and its credits, with one bounded JSON repair and no provider fallback.
Update the publisher gateway alongside the desktop for the extended coordinator contract.
Schema validation cannot prove a model's plan is semantically correct.

`agent_envelopes` stores immutable, idempotent messages. `agent_deliveries` stores independent
pending/claimed/consumed/revoked state for each recipient. Fan-out and step/result checkpoints
commit transactionally. Inbox pages are leased; excess pending work remains durable instead of
being trimmed from prompt history. Worker recovery releases interrupted deliveries. Tasks use
the same outbox for assignments and dependency results without turning temporary specialists
into persistent Crew. Actor IDs distinguish `core`, `user`, `crew:<id>` and
`task:<run-id>:<step-id>`; task artifacts cannot implicitly cross task boundaries.

Crew handoffs conservatively require the recipient to possess the sender's private read grants.
Public weather/news results can pass along an authorized workflow without redundant provider access.
Health/wellbeing output cannot be implicitly shared; private facts supplied to these Crew
are excluded from other Crew and general coordinator memory. This is conservative grant-level
provenance, not per-sentence data lineage or an implemented private-sharing consent system.

Task retries retain the same ID, committed outputs and accumulated call spend, with a
three-attempt ceiling. Parallel checkpoint changes use revision compare-and-swap. Calls are
charged atomically before dispatch, including Crew calls made for a task. Build leads cannot
enlarge their own budgets. Task-owned work does not also invoke persistent downstream
pipelines as a second coordinator. Temporary task instructions are removed from existing Crew
at the assignment boundary; previously stopped Crew are not left running.

Task leases last 30 seconds and heartbeat every 10 seconds. Recovery respects a live owner,
resumes safe checkpointed work after expiry, and holds uncertain tool work for review. Failed
agent steps with uncertain mutation outcomes cannot be blindly retried. Record a verified
outcome in the task card, then resume; that step is skipped, not replayed. An executing action
cannot be marked verified. External providers do not offer a universal exactly-once guarantee.

## Chat, permissions and map

The default Chat dock is the general Core conversation; Updates preserves the previous agent
inbox. Existing conversations, task cards, setup cards and the dark map remain in use.
Mention selection carries canonical Crew IDs, with fuzzy search, keyboard navigation, colored
chips, editable ranges and accessible option IDs. Edits inside a token invalidate the selection.
Mention IDs persist alongside message text, so renaming an agent cannot redirect the selection.
Multiple mentions supply an addressed set to the planner, not a blind instruction broadcast.

`conversation_events` persists concise completions/failures, correlated questions, typed
capability requests, approval references and configuration diffs. Answers identify the event,
revision, task/step or Crew and are committed before waking a worker. Stale/already answered
requests cannot grant access again. Routine updates are expandable and pending requests remain
prominent. Health/wellbeing events stay in their existing private Crew views, not this feed.

The regex that converted model-written error prose into permission switches is removed.
`MissingCapability` comes from deterministic worker/policy checks and identifies the exact
capability/operation, scope, risk, supported duration and setup destination. Inline grants check
the resource ceiling; action edits reuse `ActionAccessEditor` and the authoritative policy API.
Resumption rechecks current configuration, connectivity and saved access. No model tool writes
grants. Enabling a capability is not approval of a specific command/script; consequential
action approvals reference the existing ActionRequest review UI.

Handoff dots require actual durable messages. Select an edge midpoint to inspect its delivery
states and messages; agent panels also show recent handoffs. Generic task specialists appear
as temporary task members. Selecting an idle agent no longer animates hypothetical tool use.
Reduced-motion mode hides message motion.

## Configuration scopes

`configuration_edits` records before/after values, base version, scope and application time.
Apply changes is required. Conflicts reject stale proposals; rollback creates a new inverse
proposal instead of rewriting history. Existing explicit editors also version supported fields.

- Step: temporary instructions at the next model-call boundary in an active Crew run, once.
- Run: instructions for the active/next Crew run; queued route choices apply at claim.
  Overrides expire after 24 hours and never rewrite a permanent interval.
- Persistent: responsibilities, actual interval and saved route. In-flight work finishes first;
  deferred edits apply at that boundary, updating canonical setup, real scheduler and UI.
- Global: prefer local models for future task helpers. Explicit Crew routes are not silently
  replaced by this preference.

Security, permissions and budgets remain code-enforced. A model switch in an already executing
run is rejected instead of silently changing where its authorized context is sent.

## Storage, APIs and verification

Migration 0018 adds envelopes, deliveries, conversation events, configuration history and
Crew/TaskRun revision/lease fields. Migration 0019 adds message mention IDs. Forward migrations
are additive. Downgrades remove only their newly introduced structures; tests verify older
conversation/checkpoint rows survive a downgrade/upgrade roundtrip. Back up real SQLite data
with its backup API before upgrading.

Existing loopback authentication and identity checks protect these `/api/v1` endpoints:

- `GET /orchestration/events?conversation_id=…&after=…&before=…`
- `GET /orchestration/messages?crew_id=…`
- `POST /orchestration/events/{id}/answer`
- `GET/PUT /orchestration/planner`
- `POST /configuration/proposals`, `POST /configuration/{id}/apply`
- `GET /crew/{id}/configuration/history`, `POST /configuration/{id}/rollback`
- `POST /tasks/{id}/checkpoint` for user-verified uncertain outcomes.

The feed has bounded windows: 100 recent events plus up to 500 pending requests, with `before`
for older records. Message inspection returns the latest 100 deliveries. Internal exchanges
are not automatically copied into the normal chat transcript.

`backend/tests/test_orchestration.py` covers transactional fan-out, duplicate/concurrent
publication, lease recovery, backlog, denial/revocation, privacy, strict plans, scope/version
conflicts, scheduler consistency, temporary overrides, checkpoint retries, two existing Crew
collaborating through selected IDs, and migration roundtrips. Existing task/pipeline/worker/
build suites exercise parallel joins and review/fix loops. Frontend tests cover mention ranges,
correlated replies, permission cards, diffs and real-event graph states.
`scripts/check-orchestration.py` drives the real chat components with isolated HTTP fixtures;
it never connects accounts, runs real automations or spends model credits.

## Remaining boundaries

This implementation is not a claim that every advanced requirement is complete:

- General calendar/cron edits, arbitrary global preferences and “never send mail without
  asking” are not canonical conversational policy fields. Use Actions for action policy;
  rewritten responsibilities do not implement an approval policy.
- Inline duration currently means **until revoked for this Crew**. Task-only/expiring grants
  and automatic external consent are not implemented. Disconnected providers need sign-in.
- Build member stages keep their existing nested lifecycle, not individual persistent inboxes.
- Uncertain tool recovery needs manual reconciliation, including when a pending answer survives
  the restart. Safe persistence is not permission to replay an unverified side effect.
- Call budgets and existing response-size bounds are enforced; exact run-level token/currency
  accounting is not implemented.
- Feed/map windows are bounded. No unlimited history UI or new 500-agent frame-rate guarantee
  is claimed. The existing graph's large-map tests remain relevant.
- Model-plan quality and real Google/GitHub/browser/cloud outcomes still need credentialed
  scenario testing. No model is guaranteed to produce a correct plan.

## Main files changed for this integration

Backend: `orchestration.py`, `configuration.py`, `database.py`, `schemas.py`, `task_plan.py`,
`tasks.py`, `crew_planner.py`, `coordinator_contract.py`, `coordinator_client.py`, `crew_workers.py`,
`crew_engine.py`, `pipelines.py`, `google_actions.py`, `shared_memory.py`, `api.py`, `main.py`,
`migrations.py` and migrations 0018/0019. Existing `action_rules.py` is reused, not replaced.

Frontend: `ConversationActivity.tsx`, `AgentTraffic.tsx`, `CrewGrants.tsx`, `CrewMentions.tsx`,
`CrewGraph.tsx`, `AgentGraph.tsx`, `GraphPanels.tsx`, `TaskRunCard.tsx`, `ChatPage.tsx`, `Home.tsx`,
`SettingsPage.tsx`, `lib/orchestration.ts`, `lib/api.ts`, `lib/chatController.ts`, `lib/taskTeams.ts`,
`store/app.ts`, `types.ts`, `styles.css`, `home.css`, and their regression tests.
