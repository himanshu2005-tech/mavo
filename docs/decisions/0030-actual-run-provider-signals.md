# 0030 — Actual run and provider-request signals

Status: accepted

Enabled-state orbiting and access-preview traces do not prove execution. Add a separate
metadata-only feed at authenticated GET /api/v1/runtime/activity?after=cursor. It is protected
by the existing loopback bearer and identity boundary, with no-store responses. Reading it
never wakes a worker, queries a resource provider or invokes a model.

Each ModelRouter has a bounded ActivityFeed. A ContextVar scope binds a real claimed CrewRun
to its Crew and actual route. ModelRouter.chat records the lifetime of the client's model
request attempt, including success, failure and cancellation. Background workers and manual
Crew runs emit start/finish only around actual committed run records. A local device check
emits run events without claiming provider use. Unrelated core requests are not attributed
to a Crew. A changed provider/model/credential reference cannot be attributed to a different
saved route. This observes client request lifetime, not a remote provider's internal thinking,
tokens, tool execution or successful completion of the whole task.

Only sequence, timestamps, Crew/run/route/call IDs, provider/model and status are exposed.
Never include prompts, results, credentials, response/error bodies or tool arguments. Keep
up to 512 events for 120 seconds, plus current active runs/calls. CrewRun remains durable
history; this feed is neither a replacement audit log nor a job queue. Cursor replay catches
requests shorter than a UI refresh. Initial snapshots expose active work without replaying
old completions. Stream IDs, sign-out clearing and generation fencing reject late callbacks
from an old identity/backend. Contexts isolate simultaneous Crews and are cleaned on cancellation.

The existing 2.5-second workspace refresh reads the feed (one bounded local request; 4-second
timeout). Provider progress expires after eight seconds without a fresh snapshot. Recent
outcomes remain visible for 12 seconds without rearming on identical polls. Selection does
not fake provider work. A compact three-entry notice area, the corresponding Crew card,
provider badge and actual provider link show execution without taking focus or moving the map.
Actual fallback routes can appear while they are in use. Existing names, colors, access and
budgets are unchanged. Reduced motion retains static labels and hides travelling pulses.

Tests cover scoped/in-flight/instant/failed/cancelled/concurrent requests, local-only checks,
authentication, content redaction, cursor replay, reset/deduplication and stale-progress
expiry. UI tests use synthetic feed snapshots and never run real user agents or spend credits.
