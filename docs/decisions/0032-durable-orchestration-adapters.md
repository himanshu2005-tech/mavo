# ADR 0032: Durable orchestration adapters, not another execution engine

Status: accepted for the implemented subset; outstanding work is in
[orchestration.md](../orchestration.md).

Keep existing TaskRun/CrewRun, persistent pipelines and bounded BuildTeam lifecycles. Add
transactional message envelopes, per-recipient acknowledgement, correlated conversation events
and versioned configuration around them. Legacy handoff JSON becomes a bounded context
projection, not the only queue. Do not merge temporary build identities into persistent Crew.

Reuse resource ceilings, action policies, provider routing, DPAPI credentials, generation
fencing and loopback authentication. Models propose; authenticated user decisions grant.
Canonical scheduler/configuration fields and safe boundaries take precedence over prompt prose.

Retain completed steps and charge calls before dispatch. A build lead cannot enlarge its own
spending budget. Uncertain external effects require reconciliation instead of blind replay.
Expose meaningful updates in general chat and actual handoffs on the current map, not every
internal token or imagined activity on a selected node.
