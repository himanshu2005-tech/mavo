# 0025 — Model routes on the access map and explicit cloud fallback

Status: accepted

The user requested restoration of the original Claude-handoff radial presentation; see
[0027](0027-restore-handoff-map.md). The board, screen-space label packing, subsequent category
restyling and adaptive camera redesign are removed. The original rings, sizing, visible
connections, zoom/detail levels and selection animations are restored.

The Home map shows each agent's selected provider/model route alongside its resources and
actions. Selecting an agent traces assignment, model use, permitted reads and permitted actions
in order. Selecting a route highlights its users. A selected cloud fallback is a dashed link;
local reminder routines and deterministic device monitors have no model link.

The original graph fits the layout canvas, adjusts for an open inspector and offers pan/zoom.
Large maps use the original compact markers and hover detail. Restoring this presentation also
restores its density/readability tradeoffs; prior redesigned-layout test results do not apply.

These animations preview configuration, not tool execution or verified results. Requested but
denied actions remain visibly unavailable and never receive a trace. Reduced-motion mode hides
moving dots and disables camera transitions. Actual provider/model usage remains in CrewRun;
action results remain in the action audit history.

Provider discovery is not proof that an individual cloud route's key or model works. A route
panel has an explicit Test connection button (one short inference, potentially billable). Cloud
routes start untested; Ollama availability also checks that its selected model is installed.

A local-model failure must not silently export local data to the first cloud route in the
workspace. Cloud fallback is off by default. The owner can explicitly select a cloud route per
agent in the map's model panel after reading the data/credit disclosure. The selection is stored
in configuration_plan.fallback_route_id; models cannot grant it through planning output.

Only local model/provider/checkpoint failures before tool dispatch can queue a fallback. It is
revalidated at job claim, uses existing budgets and generation fencing, and is consumed once.
Cloud failures cannot cascade to other routes. Changing the primary route or revoking fallback
invalidates queued work. History distinguishes queued, completed, failed and cancelled retries;
queuing never claims the cloud run succeeded. This is not an exactly-once guarantee for external
tools; normal action idempotency and permissions remain necessary.

No database migration is required for this option: existing configuration JSON is reused, and
existing agents remain local-only unless the owner makes a new selection.
