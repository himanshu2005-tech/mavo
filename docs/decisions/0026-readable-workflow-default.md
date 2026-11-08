# 0026 — Readable workflow board as the default workspace

Status: rejected. The workflow implementation and navigation have been removed. The user
subsequently requested the original Claude-handoff map; see [0027](0027-restore-handoff-map.md).
This document records the rejected direction; it does not describe the current UI.

Fitting the entire radial graph at 48% made 14-agent labels unreadable. Forcing a minimum
100% camera scale instead hid most of the graph. Both failed the product requirement. Tests
that only checked containment did not establish readability.

Home now defaults to WorkflowBoard, a screen-space responsive layout without any camera
transform. Agent names use 14px text, wrap to available width, and retain at least 108px card
height. Providers, resources and actions have separate sections and distinct icon shapes.
At narrow widths the board stacks and scrolls rather than shrinking. Connections are drawn
only for selection, using measured card positions; they preview permissions, not execution.
Stopped agents retain gray dashed cards and connections. Existing detail panels, permissions,
routes, tasks and memory remain the source of truth and are reused unchanged.

Above 40 agents, group by Crew template. Groups show actual member counts and running/input
states, and can be opened to inspect individual agents. Global search selects individual
agents across groups. Nothing is created, deleted, paused or granted access by grouping.

The prior radial rendering remains available through Map overview, with an explicit return
to Workflow. It is no longer the consumer workspace's default rendering.

Verification includes SSR contracts, real browser pixel dimensions, non-overlap, wrapped
names, scrolling, group navigation, selecting/searching the 500th agent, signed-in/out startup,
and read-only inspection of the actual 14-agent native workspace. Screenshots use private
ignored .build output; account data and credentials are never included in diagnostics.
